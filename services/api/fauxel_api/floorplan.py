"""Level floor plan SVG: zones by percent complete, element footprints by status, walk path of the latest capture."""
from __future__ import annotations

from html import escape

from .process import bim_from_cam, transformer_for

STATE_COLOR = {"NOT_STARTED": "#9aa0a6", "FRAMED_OR_ROUGHED": "#f4b400", "INSTALLED": "#34a853",
               "INSULATED_OR_FINISHED": "#1e8e3e", "DEVIATION_FLAGGED": "#d93025"}
SKIP_CLASSES = ("IfcSlab", "IfcRoof", "IfcCovering", "IfcPlate", "IfcSpace")
MAX_ELEMENTS = 6000


def pct_color(p: float) -> str:
    """gray -> yellow -> green ramp for 0..100 %."""
    p = max(0.0, min(100.0, p))
    a, b, t = ((154, 160, 166), (244, 180, 0), p / 50) if p < 50 else ((244, 180, 0), (52, 168, 83), (p - 50) / 50)
    return "#%02x%02x%02x" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def zone_polygons(conn, pid, level_id=None) -> list[dict]:
    """[{id, level_id, name, polygon:[[x,y]..]|None, bbox:[minx,miny,maxx,maxy]|None}] with bbox falling back to the zone's elements."""
    rows = conn.execute(
        "SELECT z.id, z.level_id, z.name, ST_AsGeoJSON(z.geom)::jsonb AS gj, ST_XMin(z.geom) AS x0, ST_YMin(z.geom) AS y0, "
        "ST_XMax(z.geom) AS x1, ST_YMax(z.geom) AS y1, "
        "(SELECT ARRAY[min(aabb_min[1]), min(aabb_min[2]), max(aabb_max[1]), max(aabb_max[2])] FROM bim_elements e WHERE e.zone_id = z.id) AS eb "
        "FROM zones z WHERE z.project_id = %s AND (%s::uuid IS NULL OR z.level_id = %s) ORDER BY z.name", (pid, level_id, level_id)).fetchall()
    out = []
    for r in rows:
        poly = None
        if r["gj"] and r["gj"].get("type") == "Polygon" and r["gj"]["coordinates"]:
            poly = [[c[0], c[1]] for c in r["gj"]["coordinates"][0]]
        bbox = [r["x0"], r["y0"], r["x1"], r["y1"]] if r["x0"] is not None else (
            [float(v) for v in r["eb"]] if r["eb"] and all(v is not None for v in r["eb"]) else None)
        out.append({"id": r["id"], "level_id": r["level_id"], "name": r["name"], "polygon": poly, "bbox": bbox})
    return out


def level_bbox(conn, pid, level_id, zones: list[dict] | None = None):
    r = conn.execute("SELECT min(aabb_min[1]) AS x0, min(aabb_min[2]) AS y0, max(aabb_max[1]) AS x1, max(aabb_max[2]) AS y1 "
                     "FROM bim_elements WHERE project_id = %s AND level_id = %s", (pid, level_id)).fetchone()
    boxes = [[r["x0"], r["y0"], r["x1"], r["y1"]]] if r["x0"] is not None else []
    boxes += [z["bbox"] for z in (zones or []) if z["bbox"]]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def walk_path(conn, pid, level_id) -> list[tuple[float, float]]:
    cap = conn.execute(
        "SELECT * FROM capture_sessions c WHERE c.project_id = %s AND EXISTS (SELECT 1 FROM trajectory_nodes n WHERE n.capture_id = c.id) "
        "ORDER BY (c.level_id = %s) DESC NULLS LAST, c.captured_at DESC LIMIT 1", (pid, level_id)).fetchone()
    if not cap or (cap["level_id"] not in (None, level_id)):
        return []
    tf = transformer_for(cap)
    nodes = conn.execute("SELECT x, y, z, qw, qx, qy, qz FROM trajectory_nodes WHERE capture_id = %s ORDER BY seq", (cap["id"],)).fetchall()
    return [(float(T[0, 3]), float(T[1, 3])) for T in (bim_from_cam(n, tf) for n in nodes)]


def render_svg(conn, pid, level_id, level_name: str, width: int = 1200, capture: bool = True) -> str:
    zones = zone_polygons(conn, pid, level_id)
    els = conn.execute(
        "SELECT e.name, e.ifc_class, e.zone_id, e.aabb_min[1] AS x0, e.aabb_min[2] AS y0, e.aabb_max[1] AS x1, e.aabb_max[2] AS y1, "
        "COALESCE(s.state, 'NOT_STARTED') AS state, COALESCE(s.percent_complete, 0) AS pct, "
        "EXISTS (SELECT 1 FROM deviations d WHERE d.element_uid = e.element_uid AND d.status = 'open') AS dev "
        "FROM bim_elements e LEFT JOIN element_status s USING (element_uid) WHERE e.project_id = %s AND e.level_id = %s LIMIT %s",
        (pid, level_id, MAX_ELEMENTS)).fetchall()
    path = walk_path(conn, pid, level_id) if capture else []
    bb = level_bbox(conn, pid, level_id, zones)
    if bb and path:
        bb = [min(bb[0], min(p[0] for p in path)), min(bb[1], min(p[1] for p in path)),
              max(bb[2], max(p[0] for p in path)), max(bb[3], max(p[1] for p in path))]
    if not bb:
        bb = [0.0, 0.0, 10.0, 10.0]
    pad = max(1.0, 0.03 * max(bb[2] - bb[0], bb[3] - bb[1]))
    x0, y0, x1, y1 = bb[0] - pad, bb[1] - pad, bb[2] + pad, bb[3] + pad
    w_m, h_m = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    sc = width / w_m
    height = max(1, round(h_m * sc))

    def px(x, y):  # project +X East / +Y North to SVG (y down)
        return round((x - x0) * sc, 2), round((y1 - y) * sc, 2)

    zone_pct: dict = {}
    for e in els:
        if e["zone_id"]:
            zone_pct.setdefault(e["zone_id"], []).append(e["pct"])
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
           f'font-family="sans-serif" data-level="{escape(level_name)}" data-scale-px-per-m="{sc:.4f}" '
           f'data-bbox="{x0:.3f},{y0:.3f},{x1:.3f},{y1:.3f}">',
           f'<rect width="{width}" height="{height}" fill="#fff"/>', '<g id="zones">']
    for z in zones:
        vals = zone_pct.get(z["id"])
        pct = sum(vals) / len(vals) if vals else 0.0
        poly = z["polygon"] or ([[z["bbox"][0], z["bbox"][1]], [z["bbox"][2], z["bbox"][1]], [z["bbox"][2], z["bbox"][3]],
                                 [z["bbox"][0], z["bbox"][3]]] if z["bbox"] else None)
        if not poly:
            continue
        pts = " ".join("%s,%s" % px(*p) for p in poly)
        cx, cy = px(sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly))
        out.append(f'<polygon points="{pts}" fill="{pct_color(pct)}" fill-opacity="0.45" stroke="#5f6368" stroke-width="1" '
                   f'data-zone-id="{z["id"]}" data-percent="{pct:.1f}"><title>{escape(z["name"])}: {pct:.0f}%</title></polygon>')
        out.append(f'<text x="{cx}" y="{cy}" font-size="14" text-anchor="middle" fill="#202124">{escape(z["name"])} {pct:.0f}%</text>')
    out.append('</g><g id="elements">')
    for e in sorted(els, key=lambda r: -((r["x1"] - r["x0"]) * (r["y1"] - r["y0"]))):
        if e["ifc_class"] and e["ifc_class"].startswith(SKIP_CLASSES):
            continue
        color = STATE_COLOR["DEVIATION_FLAGGED"] if e["dev"] else STATE_COLOR.get(e["state"], STATE_COLOR["NOT_STARTED"])
        ax, ay = px(e["x0"], e["y1"])
        bx, by = px(e["x1"], e["y0"])
        out.append(f'<rect x="{ax}" y="{ay}" width="{max(bx - ax, 1.5):.2f}" height="{max(by - ay, 1.5):.2f}" fill="{color}" '
                   f'fill-opacity="0.8" stroke="{color}" stroke-width="0.5" data-state="{e["state"]}"><title>{escape(e["name"] or "")}: {e["pct"]:.0f}%</title></rect>')
    out.append('</g><g id="walk-path" fill="#1a73e8" stroke="#fff" stroke-width="1">')
    for x, y in path:
        cx, cy = px(x, y)
        out.append(f'<circle cx="{cx}" cy="{cy}" r="4"/>')
    out.append("</g></svg>")
    return "\n".join(out)
