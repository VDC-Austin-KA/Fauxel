"""Schedule ingest: Primavera P6 XER / P6 XML (APIBusinessObjects) / MS Project XML -> activity dicts.

Activity keys: external_id, name, wbs, trade, zone_name, level_name, planned_start, planned_finish, actual_start,
actual_finish (datetime.date|None), percent_complete (0..100), budget_cost (float|None).
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date

TRADE_RULES = [
    ("plumbing_fp", r"plumb|sprinkler|fire (?:protection|suppression|alarm|sprinkler)|\bpip(?:e|ing)\b"),
    ("hvac", r"\bduct|hvac|mech(?:anical)?\b|\bvav\b|\bahu\b|chiller"),
    ("electrical", r"conduit|electric|lighting|wire|wiring|cable|switchgear|transformer"),
    ("arch_interior", r"drywall|gyp|\bstud|partition|paint|ceiling|\btape\b|taping"),
    ("structural", r"structural|concrete|\bsteel\b|rebar|\bslab\b|column|footing|foundation|formwork|\bpour"),
    ("arch_interior", r"framing|finish|door|flooring|\btile\b"),
]
_LEVEL = re.compile(r"\b(?:level|lvl|l)\s*-?\s*(\d+|[a-z])\b(?!\w)", re.I)
_ROOF = re.compile(r"\broof\b", re.I)  # "Roof" is a level name in most Revit/IFC models
_ZONE = re.compile(r"\b(zone|area)\s*-?\s*(\d+[a-z]?|[a-z])\b(?!\w)", re.I)


def infer_trade(name: str, wbs: str = "") -> str:
    for text in (name or "", wbs or ""):
        t = text.lower()
        for trade, pat in TRADE_RULES:
            if re.search(pat, t):
                return trade
    return "other"


def infer_location(name: str, wbs: str = ""):
    """-> (zone_name, level_name); zone falls back to the level when no Zone/Area is named."""
    zone = level = None
    for text in (name or "", wbs or ""):
        z, lv = _ZONE.search(text), _LEVEL.search(text)
        if z and not zone:
            zone = f"{z.group(1).title()} {z.group(2).upper()}"
        if lv and not level:
            level = f"Level {lv.group(1).upper()}"
        if not level and _ROOF.search(text):
            level = "Roof"
    return zone or level, level


def norm_loc(s) -> str | None:
    """Normalise 'L2' / 'Level 2' -> 'level2', 'Zone 3b' -> 'zone3b'."""
    if not s:
        return None
    s = str(s).strip()
    m = _ZONE.fullmatch(s)
    if m:
        return f"{m.group(1).lower()}{m.group(2).lower()}"
    m = _LEVEL.fullmatch(s)
    if m:
        return f"level{m.group(1).lower()}"
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _date(s):
    if not s:
        return None
    try:
        return date.fromisoformat(str(s).strip()[:10])
    except ValueError:
        return None


def _num(s):
    try:
        return float(s) if s not in (None, "") else None
    except ValueError:
        return None


def _make(ext_id, name, wbs, ps, pf, as_, af, pct, cost):
    zone, level = infer_location(name, wbs)
    return {"external_id": str(ext_id), "name": name or "", "wbs": wbs or "", "trade": infer_trade(name, wbs),
            "zone_name": zone, "level_name": level, "planned_start": _date(ps), "planned_finish": _date(pf),
            "actual_start": _date(as_), "actual_finish": _date(af),
            "percent_complete": max(0.0, min(100.0, _num(pct) or 0.0)), "budget_cost": cost}


def _xer_tables(text: str) -> dict:
    tables, cur, fields = {}, None, []
    for line in text.splitlines():
        parts = line.split("\t")
        if parts[0] == "%T":
            cur, fields = parts[1].strip(), []
            tables[cur] = []
        elif parts[0] == "%F":
            fields = [f.strip() for f in parts[1:]]
        elif parts[0] == "%R" and cur:
            row = parts[1:] + [""] * (len(fields) - len(parts) + 1)
            tables[cur].append(dict(zip(fields, row)))
    return tables


def _parse_xer(data: bytes):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252")
    tb = _xer_tables(text)
    wbs = {w["wbs_id"]: w for w in tb.get("PROJWBS", [])}

    def path(wid):
        names = []
        while wid in wbs and len(names) < 20:
            names.append(wbs[wid].get("wbs_name", ""))
            wid = wbs[wid].get("parent_wbs_id", "")
        return " > ".join(reversed(names))
    out = []
    for t in tb.get("TASK", []):
        out.append(_make(t.get("task_code") or t["task_id"], t.get("task_name"), path(t.get("wbs_id", "")),
                         t.get("target_start_date"), t.get("target_end_date"), t.get("act_start_date"),
                         t.get("act_end_date"), t.get("phys_complete_pct"), _num(t.get("target_cost"))))
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def _kids(el):
    return {_local(c.tag): (c.text or "").strip() for c in el if len(c) == 0}


def _parse_p6_xml(root):
    wbs = {}
    for w in root.iter():
        if _local(w.tag) == "WBS":
            k = _kids(w)
            wbs[k.get("ObjectId")] = k
    def path(oid):
        names = []
        while oid in wbs and len(names) < 20:
            names.append(wbs[oid].get("Name", ""))
            oid = wbs[oid].get("ParentObjectId")
        return " > ".join(reversed(names))
    out = []
    for a in root.iter():
        if _local(a.tag) != "Activity":
            continue
        k = _kids(a)
        cost = next((_num(k[n]) for n in ("PlannedTotalCost", "BudgetAtCompletion", "PlannedCost") if k.get(n)), None)
        out.append(_make(k.get("Id") or k.get("ObjectId"), k.get("Name"), path(k.get("WBSObjectId")) or k.get("WBSCode", ""),
                         k.get("PlannedStartDate") or k.get("StartDate"), k.get("PlannedFinishDate") or k.get("FinishDate"),
                         k.get("ActualStartDate"), k.get("ActualFinishDate"),
                         k.get("PhysicalPercentComplete") or k.get("PercentComplete"), cost))
    return out


def _parse_msp(root):
    out, stack = [], {}
    for t in root.iter():
        if _local(t.tag) != "Task":
            continue
        k = _kids(t)
        if k.get("UID") == "0":
            continue
        lvl = int(k.get("OutlineLevel") or 1)
        for d in [d for d in stack if d >= lvl]:
            del stack[d]
        if k.get("Summary") == "1":
            stack[lvl] = k.get("Name", "")
            continue
        wbs = " > ".join(stack[d] for d in sorted(stack))
        out.append(_make(k.get("WBS") or k.get("UID"), k.get("Name"), wbs or k.get("WBS", ""), k.get("Start"), k.get("Finish"),
                         k.get("ActualStart"), k.get("ActualFinish"), k.get("PercentComplete"), _num(k.get("Cost"))))
    return out


def parse_schedule(data: bytes, filename: str) -> list:
    fn = (filename or "").lower()
    if fn.endswith(".xer") or data.lstrip()[:6] == b"ERMHDR":
        return _parse_xer(data)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        raise ValueError(f"unreadable schedule file: {e}") from e
    name = _local(root.tag)
    if name == "Project":
        return _parse_msp(root)
    if name == "APIBusinessObjects":
        return _parse_p6_xml(root)
    raise ValueError(f"unsupported schedule format (root element {name!r})")


def map_activities(activities, elements) -> dict:
    """elements: dicts with element_uid, trade, zone_name?, level_name?. Match trade + zone, falling back to level.
    An activity naming no location maps to every element of its trade."""
    out = {}
    for a in activities:
        same = [e for e in elements if e.get("trade") == a.get("trade")]
        zone, level = norm_loc(a.get("zone_name")), norm_loc(a.get("level_name"))
        hit = []
        if zone:
            # zone names repeat per level ("Zone A" on every floor): a named level must match too
            hit = [e for e in same if zone == norm_loc(e.get("zone_name"))
                   and (not level or level == norm_loc(e.get("level_name")))]
        if not hit and level:
            hit = [e for e in same if level in (norm_loc(e.get("level_name")), norm_loc(e.get("zone_name")))]
        if not zone and not level:
            hit = same
        out[a["external_id"]] = [e["element_uid"] for e in hit]
    return out
