"""Speckle connector built on specklepy (Apache-2.0, installs on Python 3.13; verified with specklepy 2026.9.3).

Speckle 2026.9 changed the data model: versions are "bundles" (flat objects + path-keyed properties + typed relations),
loaded with `operations.receive3` and published with `operations.send3` + `BundleBuilder`. The legacy object REST endpoints
(`/objects/...`) 404 on bundle versions, which is why the SDK is used instead of raw HTTP. Docs used:
https://docs.speckle.systems/developers/sdks/python/migrate-to-2026-9 , .../api-reference/operations , /developers/api/rest.

Everything that touches the network is isolated in `open_client`, `receive_elements` and `send_bundle`, which tests replace.
`extract_elements` / `to_bim_export` / `build_progress_bundle` work on duck-typed objects, so they are unit-testable offline.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import numpy as np

UNIT_M = {"m": 1.0, "meters": 1.0, "metres": 1.0, "mm": 0.001, "millimeters": 0.001, "cm": 0.01, "centimeters": 0.01,
          "ft": 0.3048, "feet": 0.3048, "foot": 0.3048, "in": 0.0254, "inches": 0.0254, "inch": 0.0254}
IFC_GUID_RE = re.compile(r"^[0-9A-Za-z_$]{22}$")
REVIT_UID_RE = re.compile(r"^[0-9a-fA-F]{8}(-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}-[0-9a-fA-F]{8}$")
IFC_PROPS = ("ifcGuid", "IfcGUID", "IfcGuid", "GlobalId", "globalId", "Identity Data.IfcGUID", "ifc.GlobalId")
STATE_COLOR = {"installed": "#22c55e", "progress": "#eab308", "deviation": "#ef4444", "none": "#9ca3af"}


class SpeckleError(Exception):
    pass


def trade_for(category: str | None, ifc_class: str | None) -> str:
    c = f"{category or ''} {ifc_class or ''}".lower()
    for trade, keys in (("electrical", ("cable", "light", "electric", "lamp", "outlet", "conduit", "junction")),
                        ("hvac", ("duct", "air terminal", "airterminal", "fan", "damper", "mechanical", "boiler", "chiller", "coil")),
                        ("plumbing_fp", ("pipe", "plumbing", "sprinkler", "sanitary", "firesuppression", "valve", "pump", "tank")),
                        ("structural", ("beam", "column", "slab", "footing", "framing", "structural", "pile", "rebar", "foundation", "floor")),
                        ("arch_interior", ("wall", "door", "window", "ceiling", "covering", "curtain", "roof", "stair", "railing"))):
        if any(k in c for k in keys):
            return trade
    return "other"


# ---------- network edge (replaced in tests) ----------

def open_client(server_url: str, token: str):
    """Authenticated SpeckleClient. `server_url` like https://app.speckle.systems"""
    from specklepy.api.client import SpeckleClient
    u = urlparse(server_url if "://" in server_url else "https://" + server_url)
    if not u.netloc:
        raise SpeckleError(f"bad server url {server_url!r}")
    client = SpeckleClient(host=u.netloc, use_ssl=u.scheme != "http")
    client.authenticate_with_token(token)
    return client


def latest_version_id(client, project_id: str, model_id: str) -> str:
    res = client.version.get_versions(model_id, project_id, limit=1)
    if not res.items:
        raise SpeckleError("model has no versions")
    return res.items[0].id


def receive_elements(server_url: str, token: str, project_id: str, model_id: str, version_id: str | None = None, *,
                     include_geometry: bool = True) -> tuple[str, str, list[dict]]:
    """-> (version_id, model_units, elements). Closes the downloaded bundle before returning."""
    from specklepy.api import operations
    client = open_client(server_url, token)
    version_id = version_id or latest_version_id(client, project_id, model_id)
    try:
        with operations.receive3(client.account, project_id, model_id, version_id, include_geometry=include_geometry,
                                 mark_received=False) as model:
            return version_id, getattr(model, "units", None) or "m", extract_elements(model)
    except Exception as exc:  # specklepy raises SpeckleException for unconverted legacy versions
        raise SpeckleError(f"could not receive version {version_id}: {exc}") from exc


def send_bundle(server_url: str, token: str, project_id: str, model_id: str, builder) -> str:
    from specklepy.api import operations
    client = open_client(server_url, token)
    return operations.send3(client.account, project_id, model_id, builder).version_id


# ---------- bundle model -> elements ----------

def _unit_factor(units: str | None, default: str = "m") -> float:
    return UNIT_M.get((units or default).lower(), 1.0)


def _bbox(obj, model_units: str) -> tuple[list[float], list[float]] | None:
    pts = []
    for g in getattr(obj, "geometries", None) or []:
        try:
            mesh = g.decode_mesh()
        except Exception:
            continue
        v = np.asarray(mesh.vertices, dtype=float).reshape(-1, 3)
        if len(v):
            pts.append(v * _unit_factor(getattr(mesh, "units", None) or model_units))
    if not pts:
        return None
    allv = np.vstack(pts)
    return allv.min(axis=0).tolist(), allv.max(axis=0).tolist()


def _first(obj, getter: str, paths: Iterable[str]):
    fn = getattr(obj, getter, None)
    if fn is None:
        return None
    for p in paths:
        try:
            v = fn(p)
        except Exception:
            v = None
        if v:
            return v
    return None


def extract_elements(model, *, with_geometry: bool = True) -> list[dict]:
    """Flatten a specklepy `Model` (or any object with `.objects` / `.units`) into plain dicts."""
    units = getattr(model, "units", None) or "m"
    out = []
    for o in model.objects:
        lvl = getattr(o, "level", None)
        d = {"application_id": getattr(o, "application_id", None), "name": getattr(o, "name", None),
             "category": _first(o, "get_string", ("category", "Category", "speckle_type")),
             "ifc_class": _first(o, "get_string", ("ifcType", "ifcClass", "IfcType", "type")),
             "ifc_guid": _first(o, "get_string", IFC_PROPS),
             "level": getattr(lvl, "name", None) if lvl is not None else _first(o, "get_string", ("level", "Level", "Constraints.Level")),
             "type_name": _first(o, "get_string", ("family", "type", "Type Name", "Family and Type")),
             "bbox": _bbox(o, units) if with_geometry else None}
        out.append(d)
    return out


def classify_id(app_id: str | None, ifc_guid: str | None) -> dict:
    ids = {"revit_unique_id": None, "ifc_global_id": None, "navis_item_guid": None}
    if ifc_guid and IFC_GUID_RE.match(ifc_guid):
        ids["ifc_global_id"] = ifc_guid
    if app_id:
        if REVIT_UID_RE.match(app_id):
            ids["revit_unique_id"] = app_id
        elif IFC_GUID_RE.match(app_id) and not ids["ifc_global_id"]:
            ids["ifc_global_id"] = app_id
    return ids


def to_bim_export(model_name: str, elements: list[dict], *, host_version: str = "speckle") -> tuple[dict, dict]:
    """-> (payload for contracts/bim-export.schema.json, report). Needs a bbox and a Revit/IFC identity per element."""
    rep = {"objects": len(elements), "elements": 0, "skipped_no_id": 0, "skipped_no_geometry": 0}
    levels: dict[str, float] = {}
    els = []
    for e in elements:
        ids = classify_id(e.get("application_id"), e.get("ifc_guid"))
        if not any(ids.values()):
            rep["skipped_no_id"] += 1
            continue
        if not e.get("bbox"):
            rep["skipped_no_geometry"] += 1
            continue
        mn, mx = e["bbox"]
        cat = e.get("category") or (e.get("ifc_class") or "Element")
        if e.get("level"):
            levels.setdefault(e["level"], 0.0)
        els.append({**ids, "name": e.get("name") or cat, "category": cat, "family_type": e.get("type_name"),
                    "level": e.get("level"), "room_or_zone": None, "trade": trade_for(cat, e.get("ifc_class")),
                    "aabb_min": [float(x) for x in mn], "aabb_max": [float(x) for x in mx],
                    "quantity": 1.0, "quantity_unit": "ea"})
        rep["elements"] += 1
    # Speckle levels have no elevation here: use the lowest element z per level
    for e in els:
        if e["level"]:
            levels[e["level"]] = min(levels.get(e["level"], 1e18), e["aabb_min"][2]) if levels[e["level"]] != 0.0 else e["aabb_min"][2]
    payload = {"source": "revit", "model_name": model_name, "host_version": host_version, "units": "m",
               "shared_transform": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
               "levels": [{"name": n, "elevation_m": round(z, 4)} for n, z in sorted(levels.items(), key=lambda kv: kv[1])],
               "elements": els}
    return payload, rep


def diff_elements(previous: dict[str, dict], current: Iterable[dict], tol: float = 1e-3) -> dict:
    """previous: id -> {name, level, aabb_min, aabb_max}; current: bim-export elements. id = ifc guid else revit id."""
    def key(e):
        return e.get("ifc_global_id") or e.get("revit_unique_id")

    cur = {key(e): e for e in current if key(e)}
    added = sorted(k for k in cur if k not in previous)
    removed = sorted(k for k in previous if k not in cur)
    changed = []
    for k, e in cur.items():
        p = previous.get(k)
        if not p:
            continue
        moved = any(abs(a - b) > tol for a, b in zip(list(p["aabb_min"]) + list(p["aabb_max"]), e["aabb_min"] + e["aabb_max"]))
        if moved or (p.get("name") or None) != (e.get("name") or None) or (p.get("level") or None) != (e.get("level") or None):
            changed.append(k)
    return {"added": added, "removed": removed, "changed": sorted(changed),
            "unchanged": len(cur) - len(added) - len(changed)}


# ---------- progress -> bundle ----------

def hex_to_argb(h: str) -> int:
    return 0xFF000000 | int(h.lstrip("#"), 16)


def box_mesh(mn, mx):
    """Axis-aligned box as an 8-vertex, 6-quad Speckle Mesh (meters)."""
    from specklepy.objects.geometry import Mesh
    (x0, y0, z0), (x1, y1, z1) = mn, mx
    v = [x0, y0, z0, x1, y0, z0, x1, y1, z0, x0, y1, z0, x0, y0, z1, x1, y0, z1, x1, y1, z1, x0, y1, z1]
    f = [4, 0, 3, 2, 1, 4, 4, 5, 6, 7, 4, 0, 1, 5, 4, 4, 1, 2, 6, 5, 4, 2, 3, 7, 6, 4, 3, 0, 4, 7]
    return Mesh(vertices=v, faces=f, units="m")


def build_progress_bundle(rows: list[dict], *, producer_version: str = "0.1.0", output_dir: str | None = None):
    """rows: {application_id, name, state, percent, color, confidence, last_verified, trade, level, level_elevation, aabb_min, aabb_max}.
    Each object carries `fauxel_status`, `fauxel_percent_complete`, `fauxel_color` (hex), `fauxel_confidence`,
    `fauxel_last_verified`, a Speckle color (ARGB) and a box proxy mesh of its bounding box, grouped by trade."""
    from specklepy.bundle import BundleBuilder, Level, Producer
    builder = BundleBuilder(Producer("fauxel", producer_version), "m", output_dir=output_dir)
    levels: dict[str, Any] = {}
    containers: dict[str, Any] = {}
    for r in rows:
        if not r.get("application_id"):
            continue
        props = {"fauxel_status": r["state"], "fauxel_percent_complete": float(r.get("percent") or 0.0), "fauxel_color": r["color"]}
        if r.get("confidence") is not None:
            props["fauxel_confidence"] = float(r["confidence"])
        if r.get("last_verified"):
            lv = r["last_verified"]
            props["fauxel_last_verified"] = lv.isoformat() if isinstance(lv, datetime) else str(lv)
        obj = builder.get_or_add_object(r["application_id"]).set_properties(props, name=r.get("name"))
        obj.color = builder.get_or_add_color(hex_to_argb(r["color"]))
        if r.get("trade"):
            c = containers.get(r["trade"]) or containers.setdefault(r["trade"], builder.get_or_add_container_path([r["trade"]]))
            obj.collection = c
        if r.get("level"):
            lvl = levels.get(r["level"]) or levels.setdefault(r["level"], builder.get_or_add_level(r["level"], Level(float(r.get("level_elevation") or 0.0), r["level"])))
            obj.level = lvl
        if r.get("aabb_min") and r.get("aabb_max"):
            obj.add_geometry(box_mesh(r["aabb_min"], r["aabb_max"]))
    return builder
