"""Earned value, S-curve and slip forecast.

Activity keys used: external_id, trade, zone_name, element_uids, budget_cost, planned_start, planned_finish,
percent_complete, actual_finish. Weights: budget_cost when every activity has one, otherwise element count
(min 1) for all, so mixed units never combine. PV is linear over the inclusive planned duration. EV = weight *
mean element percent (activity percent_complete when it has no elements). Actual cost is not tracked yet, so AC = EV
and CPI = 1.0 (flagged in `note`).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from .schedule import norm_loc

SEQUENCE = [("structural", "plumbing_fp"), ("structural", "hvac"), ("structural", "electrical"),
            ("structural", "arch_interior"), ("plumbing_fp", "arch_interior"), ("hvac", "arch_interior"),
            ("electrical", "arch_interior")]  # upstream must finish before downstream starts, per zone


def _d(x) -> date:
    if isinstance(x, datetime):
        return x.date()
    return x if isinstance(x, date) else date.fromisoformat(str(x)[:10])


def _weights(activities):
    if all((a.get("budget_cost") or 0) > 0 for a in activities):
        return [float(a["budget_cost"]) for a in activities], "budget_cost"
    return [float(max(len(a.get("element_uids") or []), 1)) for a in activities], "element_count"


def _planned_frac(a, on: date) -> float:
    s, f = _d(a["planned_start"]), _d(a["planned_finish"])
    return float(np.clip(((on - s).days + 1) / ((f - s).days + 1), 0, 1))


def _actual_frac(a, pct_of) -> float:
    uids = a.get("element_uids") or []
    if uids:
        return float(np.mean([pct_of(u) for u in uids])) / 100
    return float(a.get("percent_complete") or 0) / 100


def compute_evm(activities, element_percent: dict, as_of: date, scope: str = "project") -> list:
    as_of = _d(as_of)
    w, basis = _weights(activities)
    groups = {}
    for a, wi in zip(activities, w):
        key = {"project": "project", "trade": a.get("trade") or "other", "zone": a.get("zone_name") or "unzoned"}[scope]
        g = groups.setdefault(key, [0.0, 0.0])
        g[0] += wi * _planned_frac(a, as_of)
        g[1] += wi * _actual_frac(a, lambda u: float(element_percent.get(u, 0) or 0))
    out = []
    for key, (pv, ev) in groups.items():
        out.append({"scope": scope, "scope_key": key, "pv": round(pv, 4), "ev": round(ev, 4), "ac": round(ev, 4),
                    "spi": round(ev / pv, 4) if pv > 0 else None, "cpi": 1.0,
                    "weight_basis": basis, "note": "AC unknown: assumed equal to EV (CPI = 1.0)"})
    return out


def _history_index(element_history):
    idx = {}
    for h in element_history:
        idx.setdefault(h["element_uid"], []).append((_d(h["date"]), float(h["percent"])))
    for v in idx.values():
        v.sort()
    return idx


def _pct_at(idx, uid, on: date) -> float:
    best = 0.0
    for d, p in idx.get(uid, ()):
        if d > on:
            break
        best = p
    return best


def _activity_pct(a, idx, on: date) -> float:
    if a.get("element_uids"):
        return 100 * _actual_frac(a, lambda u: _pct_at(idx, u, on))
    af = a.get("actual_finish")
    return 100.0 if af and _d(af) <= on else 0.0


def s_curve(activities, element_history, start: date, end: date) -> list:
    start, end = _d(start), _d(end)
    idx = _history_index(element_history)
    w, _ = _weights(activities)
    total = sum(w) or 1.0
    days = [start + timedelta(days=7 * i) for i in range((end - start).days // 7 + 1)]
    if days[-1] != end:
        days.append(end)
    out = []
    for d in days:
        planned = sum(wi * _planned_frac(a, d) for a, wi in zip(activities, w))
        actual = sum(wi * _activity_pct(a, idx, d) / 100 for a, wi in zip(activities, w))
        out.append({"date": d.isoformat(), "planned_pct": round(100 * planned / total, 2), "actual_pct": round(100 * actual / total, 2)})
    return out


def _velocity(a, idx, as_of: date):
    """Percent per week: least-squares slope of the activity's percent over its history dates (<= as_of)."""
    dates = sorted({d for u in (a.get("element_uids") or []) for d, _ in idx.get(u, ()) if d <= as_of})
    if len(dates) < 2:
        return 0.0
    y = [_activity_pct(a, idx, d) for d in dates]
    slope = np.polyfit([(d - dates[0]).days for d in dates], y, 1)[0]
    return float(max(slope, 0.0) * 7)


def _projected_finish(a, pct, vel, as_of: date):
    s, f = _d(a["planned_start"]), _d(a["planned_finish"])
    if pct >= 99.99:
        return _d(a["actual_finish"]) if a.get("actual_finish") else min(as_of, max(f, s))
    if vel > 0:
        return as_of + timedelta(days=round((100 - pct) / vel * 7))
    if pct <= 0:  # not started: late by however long past its planned start
        return f + timedelta(days=max((as_of - s).days, 0))
    return as_of + timedelta(days=round(((f - s).days + 1) * (1 - pct / 100)))


def forecast(activities, element_history, as_of: date) -> dict:
    as_of = _d(as_of)
    idx = _history_index(element_history)
    rows, proj = [], {}
    for a in activities:
        pct = _activity_pct(a, idx, as_of)
        vel = _velocity(a, idx, as_of)
        pf = _projected_finish(a, pct, vel, as_of)
        proj[a["external_id"]] = (pf, pct)
        rows.append({"external_id": a["external_id"], "slip_days": (pf - _d(a["planned_finish"])).days,
                     "velocity_per_week": round(vel, 3)})
    alerts = []
    by_zone = {}
    for a in activities:
        by_zone.setdefault(norm_loc(a.get("zone_name")) or "unzoned", []).append(a)
    for zone, acts in by_zone.items():
        for up, down in SEQUENCE:
            ups = [a for a in acts if a.get("trade") == up and proj[a["external_id"]][1] < 99.99]
            downs = [a for a in acts if a.get("trade") == down and proj[a["external_id"]][1] < 99.99]
            if not ups or not downs:
                continue
            up_fin = max(proj[a["external_id"]][0] for a in ups)
            d_start = min(_d(a["planned_start"]) for a in downs)
            if d_start < up_fin:
                name = ups[0].get("zone_name") or "unzoned"
                n = (d_start - as_of).days
                alerts.append({"zone": name, "upstream_trade": up, "downstream_trade": down, "days_until_conflict": n,
                               "message": f"{down} starts {d_start.isoformat()} in {name} but {up} is projected to finish "
                                          f"{up_fin.isoformat()} ({(up_fin - d_start).days} d overlap)"})
    return {"projected_finish": max((p[0] for p in proj.values()), default=None), "activities": rows, "stacking_alerts": alerts}
