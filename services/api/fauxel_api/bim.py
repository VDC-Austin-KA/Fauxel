"""BIM ingest: shared element upsert, Revit/Navisworks export import, IFC ingest (IfcOpenShell)."""
from __future__ import annotations

import io
import os
import tempfile
import uuid
from pathlib import Path

import numpy as np

from .domain import trade_for_class


# ---------- shared helpers ----------

def get_or_create_building(conn, pid, name: str = "Building 1"):
    row = conn.execute(
        "INSERT INTO buildings (project_id, name) VALUES (%s, %s) "
        "ON CONFLICT (project_id, name) DO UPDATE SET name = EXCLUDED.name RETURNING id", (pid, name)).fetchone()
    return row["id"]


def upsert_level(conn, pid, building_id, name: str, elevation_m: float):
    return conn.execute(
        "INSERT INTO levels (project_id, building_id, name, elevation_m) VALUES (%s, %s, %s, %s) "
        "ON CONFLICT (project_id, name) DO UPDATE SET elevation_m = EXCLUDED.elevation_m, "
        "building_id = COALESCE(levels.building_id, EXCLUDED.building_id) RETURNING id",
        (pid, building_id, name, elevation_m)).fetchone()["id"]


def get_or_create_zone(conn, pid, level_id, name: str, polygon_wkt: str | None = None):
    row = conn.execute(
        "SELECT id FROM zones WHERE project_id = %s AND level_id IS NOT DISTINCT FROM %s AND name = %s", (pid, level_id, name)).fetchone()
    if row:
        if polygon_wkt:
            conn.execute("UPDATE zones SET geom = ST_GeomFromText(%s, 0) WHERE id = %s", (polygon_wkt, row["id"]))
        return row["id"]
    return conn.execute(
        "INSERT INTO zones (project_id, level_id, name, geom) VALUES (%s, %s, %s, "
        "CASE WHEN %s::text IS NULL THEN NULL ELSE ST_GeomFromText(%s, 0) END) RETURNING id",
        (pid, level_id, name, polygon_wkt, polygon_wkt)).fetchone()["id"]


def box_wkt(mn, mx) -> str:
    z = mn[2]
    ring = [(mn[0], mn[1]), (mx[0], mn[1]), (mx[0], mx[1]), (mn[0], mx[1]), (mn[0], mn[1])]
    return "POLYGON Z((" + ", ".join(f"{x} {y} {z}" for x, y in ring) + "))"


def footprint_polygon(verts: np.ndarray, faces: np.ndarray | None = None, simplify_m: float = 0.01) -> list[tuple[float, float]] | None:
    """XY footprint ring (meters, closed) of a mesh: union of the projected triangles (concave) via shapely when installed,
    else the convex hull. Returns None for degenerate input."""
    pts = np.asarray(verts, dtype=float)[:, :2]
    if len(pts) < 3:
        return None
    ring = None
    try:
        from shapely.geometry import MultiPoint, Polygon
        from shapely.ops import unary_union

        geom = None
        if faces is not None and len(faces):
            tris = [t for t in (Polygon(pts[f]) for f in faces) if t.area > 1e-9]
            if tris:
                geom = unary_union([t.buffer(0) for t in tris])
                if geom.geom_type == "MultiPolygon":
                    geom = max(geom.geoms, key=lambda g: g.area)
                if geom.geom_type != "Polygon" or geom.is_empty:
                    geom = None
                else:
                    geom = Polygon(geom.exterior)  # drop holes: a zone is a single outline
        if geom is None:
            geom = MultiPoint(pts).convex_hull
        if geom.geom_type != "Polygon" or geom.area < 1e-9:
            return None
        if simplify_m:
            simp = geom.simplify(simplify_m)
            geom = simp if simp.geom_type == "Polygon" and not simp.is_empty else geom
        ring = [(float(x), float(y)) for x, y in geom.exterior.coords]
    except ImportError:
        from scipy.spatial import ConvexHull, QhullError

        try:
            hull = ConvexHull(pts)
        except (QhullError, ValueError):
            return None
        ring = [(float(x), float(y)) for x, y in pts[hull.vertices]]
        ring.append(ring[0])
    return ring


def polygon_wkt(ring: list[tuple[float, float]], z: float) -> str:
    return "POLYGON Z((" + ", ".join(f"{x} {y} {z}" for x, y in ring) + "))"


def transform_aabb(mn, mx, m16) -> tuple[list[float], list[float]]:
    """Apply a row-major 4x4 to the 8 box corners and re-box."""
    M = np.asarray(m16, dtype=float).reshape(4, 4)
    corners = np.array([[x, y, z, 1.0] for x in (mn[0], mx[0]) for y in (mn[1], mx[1]) for z in (mn[2], mx[2])])
    out = (corners @ M.T)[:, :3]
    return out.min(axis=0).tolist(), out.max(axis=0).tolist()


ID_COLS = ("revit_unique_id", "ifc_global_id", "navis_item_guid")


def resolve_uids(conn, pid, rows: list[dict]) -> None:
    """Sets row['element_uid'] (existing match on any of the three ids, else new) and row['_exists']."""
    existing: dict[tuple[str, str], uuid.UUID] = {}
    for col in ID_COLS:
        ids = [r[col] for r in rows if r.get(col)]
        if ids:
            for e in conn.execute(
                f"SELECT element_uid, {col} AS k FROM bim_elements WHERE project_id = %s AND {col} = ANY(%s)", (pid, ids)):
                existing[(col, e["k"])] = e["element_uid"]
    for r in rows:
        uid = next((existing[(c, r[c])] for c in ID_COLS if r.get(c) and (c, r[c]) in existing), None)
        r["_exists"] = uid is not None
        r["element_uid"] = uid or uuid.uuid4()


def write_elements(conn, pid, model_id, rows: list[dict]) -> None:
    cols = ("revit_unique_id", "ifc_global_id", "navis_item_guid")
    with conn.cursor() as cur:
        for r in rows:
            mn, mx = r["aabb_min"], r["aabb_max"]
            c = [(mn[i] + mx[i]) / 2 for i in range(3)]
            args = dict(uid=r["element_uid"], pid=pid, mid=model_id, name=r.get("name"), cat=r.get("category"),
                        cls=r.get("ifc_class"), fam=r.get("family_type"), trade=r["trade"], lvl=r.get("level_id"),
                        zone=r.get("zone_id"), mn=list(mn), mx=list(mx), cx=c[0], cy=c[1], cz=c[2],
                        mesh=r.get("mesh_key"), mat=r.get("material"), qty=r.get("quantity"), unit=r.get("quantity_unit"),
                        **{k: r.get(k) for k in cols})
            if r["_exists"]:
                cur.execute(
                    "UPDATE bim_elements SET model_id=%(mid)s, name=%(name)s, category=%(cat)s, "
                    "ifc_class=COALESCE(%(cls)s, ifc_class), family_type=%(fam)s, trade=%(trade)s, level_id=%(lvl)s, "
                    "zone_id=%(zone)s, aabb_min=%(mn)s::float8[], aabb_max=%(mx)s::float8[], "
                    "centroid=ST_SetSRID(ST_MakePoint(%(cx)s,%(cy)s,%(cz)s),0), mesh_key=COALESCE(%(mesh)s, mesh_key), "
                    "material=%(mat)s, expected_qty=%(qty)s, qty_unit=%(unit)s, "
                    "revit_unique_id=COALESCE(%(revit_unique_id)s, revit_unique_id), "
                    "ifc_global_id=COALESCE(%(ifc_global_id)s, ifc_global_id), "
                    "navis_item_guid=COALESCE(%(navis_item_guid)s, navis_item_guid) WHERE element_uid=%(uid)s", args)
            else:
                cur.execute(
                    "INSERT INTO bim_elements (element_uid, project_id, model_id, revit_unique_id, ifc_global_id, "
                    "navis_item_guid, name, category, ifc_class, family_type, trade, level_id, zone_id, aabb_min, aabb_max, "
                    "centroid, mesh_key, material, expected_qty, qty_unit) VALUES (%(uid)s, %(pid)s, %(mid)s, "
                    "%(revit_unique_id)s, %(ifc_global_id)s, %(navis_item_guid)s, %(name)s, %(cat)s, %(cls)s, %(fam)s, "
                    "%(trade)s, %(lvl)s, %(zone)s, %(mn)s::float8[], %(mx)s::float8[], "
                    "ST_SetSRID(ST_MakePoint(%(cx)s,%(cy)s,%(cz)s),0), %(mesh)s, %(mat)s, %(qty)s, %(unit)s)", args)


def create_model(conn, pid, name: str, source: str, **extra) -> dict:
    version = conn.execute(
        "SELECT COALESCE(max(version), 0) + 1 AS v FROM bim_models WHERE project_id = %s AND name = %s AND source = %s",
        (pid, name, source)).fetchone()["v"]
    return conn.execute(
        "INSERT INTO bim_models (project_id, name, source, version, file_key, host_version, shared_transform) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s::float8[]) RETURNING id",
        (pid, name, source, version, extra.get("file_key"), extra.get("host_version"), extra.get("shared_transform"))).fetchone()


def finish_model(conn, model_id, count: int) -> dict:
    return conn.execute("UPDATE bim_models SET element_count = %s WHERE id = %s RETURNING *", (count, model_id)).fetchone()


# ---------- Revit / Navisworks export import ----------

def import_export(conn, pid, payload: dict) -> dict:
    building = get_or_create_building(conn, pid)
    levels = {lv["name"]: upsert_level(conn, pid, building, lv["name"], lv["elevation_m"]) for lv in payload["levels"]}
    model = create_model(conn, pid, payload["model_name"], payload["source"], host_version=payload["host_version"],
                         shared_transform=[float(v) for v in payload["shared_transform"]])
    zones: dict[tuple, uuid.UUID] = {}
    rows = []
    for e in payload["elements"]:
        lvl_name = e.get("level")
        if lvl_name and lvl_name not in levels:
            levels[lvl_name] = upsert_level(conn, pid, building, lvl_name, 0.0)
        level_id = levels.get(lvl_name)
        zone_id = None
        if e.get("room_or_zone"):
            key = (level_id, e["room_or_zone"])
            if key not in zones:
                zones[key] = get_or_create_zone(conn, pid, level_id, e["room_or_zone"])
            zone_id = zones[key]
        mn, mx = transform_aabb(e["aabb_min"], e["aabb_max"], payload["shared_transform"])
        rows.append({**e, "aabb_min": mn, "aabb_max": mx, "level_id": level_id, "zone_id": zone_id,
                     "quantity": e.get("quantity"), "quantity_unit": e.get("quantity_unit"),
                     "ifc_class": None})
    rows, dups = dedupe_rows(rows)
    resolve_uids(conn, pid, rows)
    write_elements(conn, pid, model["id"], rows)
    return {**finish_model(conn, model["id"], len(rows)), "duplicates": dups}


ID_COLS = ("revit_unique_id", "ifc_global_id", "navis_item_guid")


def dedupe_rows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Federated exports repeat elements (e.g. a linked model loaded twice). First row wins per id; boxes merge."""
    seen: dict[tuple, dict] = {}
    out, dups = [], []
    for r in rows:
        keys = [(c, r[c]) for c in ID_COLS if r.get(c)]
        first = next((seen[k] for k in keys if k in seen), None)
        if first is None:
            out.append(r)
            for k in keys:
                seen[k] = r
            continue
        first["aabb_min"] = [min(a, b) for a, b in zip(first["aabb_min"], r["aabb_min"])]
        first["aabb_max"] = [max(a, b) for a, b in zip(first["aabb_max"], r["aabb_max"])]
        dups.append({k: r.get(k) for k in (*ID_COLS, "name")})
        for k in keys:
            seen.setdefault(k, first)
    return out, dups


# ---------- IFC ----------

AREA_KEYS = ("NetSideArea", "GrossSideArea", "NetArea", "GrossArea")
VOLUME_KEYS = ("NetVolume", "GrossVolume")
LINEAR = ("duct", "pipe", "cable", "beam", "column", "member", "conduit")
AREA_CLASSES = ("wall", "covering", "plate", "curtainwall")
VOLUME_CLASSES = ("slab", "footing", "pile")


def _quantity(el, ifc_class: str, scale: float):
    import ifcopenshell.util.element as ue

    q: dict = {}
    for qto in ue.get_psets(el, qtos_only=True).values():
        q.update({k: v for k, v in qto.items() if isinstance(v, (int, float))})
    c = ifc_class.lower()
    wants = []
    if any(k in c for k in LINEAR):
        wants = [(("Length",), scale, "m")]
    if any(k in c for k in AREA_CLASSES):
        wants.append((AREA_KEYS, scale ** 2, "m2"))
    if any(k in c for k in VOLUME_CLASSES):
        wants.append((VOLUME_KEYS, scale ** 3, "m3"))
    for keys, factor, unit in wants:
        for k in keys:
            if q.get(k):
                return float(q[k]) * factor, unit
    return 1.0, "ea"


def _storey_of(el):
    """Walk containment/aggregation up to a building storey; also report a directly containing IfcSpace."""
    import ifcopenshell.util.element as ue

    space = None
    cur = el
    for _ in range(8):
        parent = ue.get_container(cur) or ue.get_aggregate(cur)
        if parent is None:
            return None, space
        if parent.is_a("IfcSpace") and space is None:
            space = parent
        if parent.is_a("IfcBuildingStorey"):
            return parent, space
        cur = parent
    return None, space


def _shape_arrays(shape, scale: float):
    """Triangles in meters (IfcOpenShell returns file length units)."""
    g = shape.geometry
    verts = np.array(g.verts, dtype=np.float64).reshape(-1, 3) * scale
    faces = np.array(g.faces, dtype=np.int64).reshape(-1, 3)
    return verts, faces


def ingest_ifc(conn, storage, pid, data: bytes, filename: str) -> dict:
    import ifcopenshell
    import ifcopenshell.geom
    import ifcopenshell.util.element as ue
    import ifcopenshell.util.placement as up
    import ifcopenshell.util.unit as uu

    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        try:
            model = ifcopenshell.open(path)
        except Exception as exc:  # untrusted upload
            raise ValueError(f"not a readable IFC file: {exc}") from exc
        scale = uu.calculate_unit_scale(model)

        settings = ifcopenshell.geom.settings()
        settings.set("use-world-coords", True)

        def geometry_of(entities) -> dict[int, tuple]:
            out: dict[int, tuple] = {}
            if not entities:
                return out
            it = ifcopenshell.geom.iterator(settings, model, max(1, (os.cpu_count() or 2) // 2), include=entities)
            if it.initialize():
                while True:
                    shape = it.get()
                    try:
                        out[shape.id] = _shape_arrays(shape, scale)
                    except Exception:
                        pass
                    if not it.next():
                        break
            return out

        elements = [e for e in model.by_type("IfcElement")
                    if not e.is_a("IfcFeatureElement") and not e.is_a("IfcVirtualElement")]
        spaces = model.by_type("IfcSpace")
        geoms = geometry_of(elements)
        space_geoms = geometry_of(spaces)

        mdl = create_model(conn, pid, Path(filename).stem or "model", "ifc", file_key="pending")
        key = f"models/{pid}/{mdl['id']}.ifc"
        storage.put(key, data, "application/x-step")
        conn.execute("UPDATE bim_models SET file_key = %s WHERE id = %s", (key, mdl["id"]))

        # LBS: building, storeys -> levels
        buildings = model.by_type("IfcBuilding")
        building_id = get_or_create_building(conn, pid, (buildings[0].Name if buildings and buildings[0].Name else "Building 1"))
        level_ids: dict[int, uuid.UUID] = {}
        for st in model.by_type("IfcBuildingStorey"):
            elev = float(st.Elevation) * scale if st.Elevation is not None else 0.0
            level_ids[st.id()] = upsert_level(conn, pid, building_id, st.Name or f"Level {st.id()}", elev)

        # zones: IfcSpace boxes, IfcZone = union of member spaces
        regions: list[dict] = []  # {id, level_id, mn, mx, area}
        space_zone: dict[int, uuid.UUID] = {}

        def box_of(verts):
            return verts.min(axis=0), verts.max(axis=0)

        for sp in spaces:
            storey, _ = _storey_of(sp)
            lvl = level_ids.get(storey.id()) if storey else None
            if sp.id() in space_geoms:
                mn, mx = box_of(space_geoms[sp.id()][0])
            else:
                continue
            ring = footprint_polygon(*space_geoms[sp.id()])
            wkt = polygon_wkt(ring, float(mn[2])) if ring else box_wkt(mn, mx)
            zid = get_or_create_zone(conn, pid, lvl, sp.LongName or sp.Name or f"Space {sp.id()}", wkt)
            space_zone[sp.id()] = zid
            regions.append({"id": zid, "level_id": lvl, "mn": mn, "mx": mx})
        for zn in model.by_type("IfcZone"):
            members = [m for rel in (zn.IsGroupedBy or []) for m in rel.RelatedObjects if m.is_a("IfcSpace") and m.id() in space_geoms]
            if not members:
                continue
            boxes = [box_of(space_geoms[m.id()][0]) for m in members]
            mn = np.min([b[0] for b in boxes], axis=0)
            mx = np.max([b[1] for b in boxes], axis=0)
            storey, _ = _storey_of(members[0])
            lvl = level_ids.get(storey.id()) if storey else None
            allv = np.vstack([space_geoms[m.id()][0] for m in members])
            ring = footprint_polygon(allv)  # outline of all member spaces (convex hull of their vertices)
            wkt = polygon_wkt(ring, float(mn[2])) if ring else box_wkt(mn, mx)
            zid = get_or_create_zone(conn, pid, lvl, zn.Name or f"Zone {zn.id()}", wkt)
            regions.append({"id": zid, "level_id": lvl, "mn": mn, "mx": mx})
        for r in regions:  # smallest region wins when containing a centroid
            r["area"] = float((r["mx"][0] - r["mn"][0]) * (r["mx"][1] - r["mn"][1]))
        regions.sort(key=lambda r: r["area"])

        rows = []
        meshes: list[tuple[dict, np.ndarray]] = []
        for el in elements:
            ifc_class = el.is_a()
            if el.id() in geoms and len(geoms[el.id()][0]):
                verts, faces = geoms[el.id()]
                mn, mx = box_of(verts)
                tri = verts[faces] if len(faces) else None
            elif getattr(el, "ObjectPlacement", None) is not None:
                loc = up.get_local_placement(el.ObjectPlacement)[:3, 3] * scale
                mn = mx = loc
                tri = None
            else:
                continue
            storey, space = _storey_of(el)
            level_id = level_ids.get(storey.id()) if storey else None
            c = (mn + mx) / 2
            zone_id = space_zone.get(space.id()) if space else None
            if zone_id is None:
                zone_id = next((r["id"] for r in regions
                                if (r["level_id"] == level_id or level_id is None)
                                and r["mn"][0] <= c[0] <= r["mx"][0] and r["mn"][1] <= c[1] <= r["mx"][1]
                                and r["mn"][2] - 0.01 <= c[2] <= r["mx"][2] + 0.01), None)
            typ = ue.get_type(el)
            mat = ue.get_material(el)
            qty, unit = _quantity(el, ifc_class, scale)
            row = {"ifc_global_id": el.GlobalId, "name": el.Name or f"{ifc_class} {el.id()}",
                   "category": ifc_class[3:], "ifc_class": ifc_class,
                   "family_type": getattr(typ, "Name", None) if typ else None,
                   "trade": trade_for_class(ifc_class), "level_id": level_id, "zone_id": zone_id,
                   "aabb_min": [float(v) for v in mn], "aabb_max": [float(v) for v in mx],
                   "material": (getattr(mat, "Name", None) or getattr(mat, "LayerSetName", None)) if mat else None,
                   "quantity": qty, "quantity_unit": unit}
            rows.append(row)
            if tri is not None and len(tri):
                meshes.append((row, tri))
        resolve_uids(conn, pid, rows)
        for row, tri in meshes:
            buf = io.BytesIO()
            np.savez_compressed(buf, tri=tri.astype(np.float32))
            row["mesh_key"] = f"meshes/{pid}/{row['element_uid']}.npz"
            storage.put(row["mesh_key"], buf.getvalue(), "application/octet-stream")
        write_elements(conn, pid, mdl["id"], rows)
        return finish_model(conn, mdl["id"], len(rows))
    finally:
        os.unlink(path)
