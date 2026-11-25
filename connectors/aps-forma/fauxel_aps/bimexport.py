"""Model Derivative properties -> contracts/bim-export.schema.json payload.

Honest scope: Model Derivative properties carry identity, category, level, type and dimensions, but NOT geometry.
`bboxes` (external id -> (min, max) in meters, shared frame) must come from elsewhere (an IFC derivative run through the
IFC ingest, the AEC Data Model, or the Viewer). Elements without a box are reported in `report["skipped_no_geometry"]`
and left out, unless `placeholder_box=True` (a 0.2 m cube at the level elevation, flagged in the report).
"""
from __future__ import annotations

import re
from typing import Iterable

FT = 0.3048
_LEN = {"m": 1.0, "mm": 0.001, "cm": 0.01, "ft": FT, "in": 0.0254, "'": FT, '"': 0.0254}
_QTY_RE = re.compile(r"^\s*(-?\d+(?:[.,]\d+)?)\s*([a-zA-Z'\"\^²³0-9]*)(?:-and-fractional-[a-z]+)?\s*$")  # Revit "12.5 ft-and-fractional-in"

NON_PHYSICAL = {"levels", "grids", "cameras", "sheets", "views", "project information", "materials", "rooms", "areas",
                "scope boxes", "reference planes", "lines", "model groups", "detail groups", "revision clouds", "text notes",
                "dimensions", "sun path", "elevations", "sections", "callouts", "legends", "schedules"}

_TRADE = (
    ("electrical", ("electrical", "lighting", "conduit", "cable tray", "data devices", "communication", "fire alarm", "security devices", "telephone")),
    ("hvac", ("duct", "air terminal", "mechanical", "hvac")),
    ("plumbing_fp", ("pipe", "plumbing", "sprinkler", "fire protection")),
    ("structural", ("structural", "floors", "foundation", "framing", "column", "beam", "rebar", "slab")),
    ("arch_interior", ("wall", "door", "window", "ceiling", "curtain", "roof", "stairs", "railing", "casework", "furniture", "generic models", "parking", "ramp")),
)


def trade_for_category(category: str) -> str:
    c = (category or "").lower()
    # "Structural Framing" etc. are structural even though 'framing' appears; order above handles electrical/hvac/plumbing first.
    for trade, keys in _TRADE:
        if any(k in c for k in keys):
            return trade
    return "other"


def parse_length_m(value, default_unit: str = "m") -> float | None:
    if isinstance(value, (int, float)):
        return float(value) * _LEN.get(default_unit, 1.0)
    if isinstance(value, str):
        m = _QTY_RE.match(value)
        if m:
            unit = m.group(2) or default_unit
            if unit in _LEN:
                return float(m.group(1).replace(",", ".")) * _LEN[unit]
    return None


def _num_unit(value, kind: str, default_unit: str):
    """Return (number in m / m2 / m3, ok)."""
    if isinstance(value, (int, float)):
        f = _LEN.get(default_unit, 1.0)
        return float(value) * f ** {"m": 1, "m2": 2, "m3": 3}[kind], True
    if isinstance(value, str):
        m = _QTY_RE.match(value)
        if m:
            unit = m.group(2).replace("^", "").replace("²", "2").replace("³", "3")
            base = re.sub(r"[23]$", "", unit) or default_unit
            if base in _LEN:
                return float(m.group(1).replace(",", ".")) * _LEN[base] ** {"m": 1, "m2": 2, "m3": 3}[kind], True
    return 0.0, False


def _find(props: dict, *names: str):
    """Case-insensitive search for a property key anywhere in the (two-level) groups."""
    wanted = {n.lower() for n in names}
    for k, v in (props or {}).items():
        if k.lower() in wanted and not isinstance(v, dict):
            return v
    for g in (props or {}).values():
        if isinstance(g, dict):
            for k, v in g.items():
                if k.lower() in wanted and not isinstance(v, dict):
                    return v
    return None


def with_tree_categories(collection: list[dict], tree: list[dict]) -> list[dict]:
    """Current Model Derivative property collections for Revit carry no `__category__` group; the category is the
    second node of the object tree (model > category > family > type > instance). Fill it in where it is missing."""
    from .modelderivative import walk_tree
    cats = {o.get("objectid"): path[1] for o, path in walk_tree(tree) if len(path) >= 2}
    for o in collection:
        if category_of(o) is None and o.get("objectid") in cats:
            o["properties"] = {**(o.get("properties") or {}), "__category__": {"Category": cats[o["objectid"]]}}
    return collection


def category_of(obj: dict) -> str | None:
    c = _find(obj.get("properties") or {}, "Category")
    if isinstance(c, str):
        return re.sub(r"^Revit\s+", "", c).strip()
    return None


def strip_id(name: str | None) -> str:
    return re.sub(r"\s*\[\d+\]\s*$", "", name or "").strip()


def levels_from(collection: Iterable[dict], default_unit: str = "m") -> list[dict]:
    out = {}
    for o in collection:
        if (category_of(o) or "").lower() == "levels":
            elev = parse_length_m(_find(o.get("properties") or {}, "Elevation"), default_unit)
            nm = strip_id(o.get("name"))
            if nm and elev is not None:
                out[nm] = {"name": nm, "elevation_m": round(elev, 4)}
    return sorted(out.values(), key=lambda x: x["elevation_m"])


def quantity_of(obj: dict, category: str, default_unit: str):
    p = obj.get("properties") or {}
    c = category.lower()
    linear = any(k in c for k in ("duct", "pipe", "conduit", "cable tray", "framing", "column"))
    areal = any(k in c for k in ("wall", "floor", "ceiling", "roof"))
    volumetric = any(k in c for k in ("foundation", "slab"))
    for flag, key, kind, unit in ((linear, "Length", "m", "m"), (areal, "Area", "m2", "m2"), (volumetric, "Volume", "m3", "m3")):
        if flag:
            q, ok = _num_unit(_find(p, key), kind, default_unit)
            if ok and q > 0:
                return q, unit
    return 1.0, "ea"


def build_bim_export(model_name: str, collection: list[dict], *, file_type: str = "rvt", host_version: str = "aps-cloud",
                     bboxes: dict | None = None, placeholder_box: bool = False, default_unit: str = "m",
                     shared_transform: list[float] | None = None) -> tuple[dict, dict]:
    """Returns (payload, report). `payload` validates against contracts/bim-export.schema.json (source is 'revit' or
    'navisworks' there; the router relabels the stored model as 'aps')."""
    bboxes = bboxes or {}
    levels = levels_from(collection, default_unit)
    level_elev = {l["name"]: l["elevation_m"] for l in levels}
    elements, report = [], {"objects": len(collection), "elements": 0, "skipped_no_id": 0, "skipped_no_geometry": 0,
                            "skipped_non_physical": 0, "placeholder_boxes": 0}
    ft = file_type.lower()
    for o in collection:
        cat = category_of(o)
        if not cat or cat.lower() in NON_PHYSICAL:
            report["skipped_non_physical"] += 1
            continue
        ext = o.get("externalId")
        props = o.get("properties") or {}
        ifc_guid = _find(props, "IfcGUID", "IFC GUID", "GlobalId")
        ids = {"revit_unique_id": None, "ifc_global_id": None, "navis_item_guid": None}
        if ft == "ifc" and ext:
            ids["ifc_global_id"] = ext
        elif ft == "rvt" and ext:
            ids["revit_unique_id"] = ext
        if isinstance(ifc_guid, str) and ifc_guid:
            ids["ifc_global_id"] = ids["ifc_global_id"] or ifc_guid
        if ft in ("nwd", "nwc") and ext:
            ids["navis_item_guid"] = ext
        if not any(ids.values()):
            report["skipped_no_id"] += 1
            continue
        level = _find(props, "Level", "Base Constraint", "Reference Level", "Schedule Level")
        level = strip_id(level) if isinstance(level, str) and level else None
        box = bboxes.get(ext) or bboxes.get(ids["ifc_global_id"])
        if box is None:
            if not placeholder_box:
                report["skipped_no_geometry"] += 1
                continue
            z = level_elev.get(level or "", 0.0)
            box = ((0.0, 0.0, z), (0.2, 0.2, z + 0.2))
            report["placeholder_boxes"] += 1
        ftype = _find(props, "Type Name", "Type")
        quantity, unit = quantity_of(o, cat, default_unit)
        elements.append({
            **ids, "name": strip_id(o.get("name")) or cat, "category": cat,
            "family_type": ftype if isinstance(ftype, str) else None, "level": level,
            "room_or_zone": None, "trade": trade_for_category(cat),
            "aabb_min": [float(v) for v in box[0]], "aabb_max": [float(v) for v in box[1]],
            "quantity": quantity, "quantity_unit": unit})
        report["elements"] += 1
    ident = [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
    payload = {"source": "navisworks" if ft in ("nwd", "nwc") else "revit", "model_name": model_name,
               "host_version": host_version, "units": "m", "shared_transform": shared_transform or ident,
               "levels": levels, "elements": elements}
    return payload, report
