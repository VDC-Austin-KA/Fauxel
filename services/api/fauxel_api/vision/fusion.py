"""Fuse several observations of one element into a single monotonic state.

Each observation: {state, percent?, confidence, view_quality, occlusion_ratio}. Weight w = confidence * view_quality *
(1 - occlusion_ratio). Observations with occlusion_ratio > 0.8 are ignored (they never lower, and with nothing else
the current state is simply kept). Percents snap to the nearest ladder rung; the rung with the largest total weight
wins; confidence is a noisy-OR of the winning weights times the winner's weight share. A DEVIATION_FLAGGED
observation passes straight through. Progress is monotonic unless rework=True.
"""
from __future__ import annotations

OCCLUDED = 0.8


def _snap(ladder, percent):
    return min(ladder, key=lambda r: (abs(r["percent"] - percent), r["percent"]))


def _rung_for_state(ladder, state):
    return next((r for r in ladder if r["state"] == state), ladder[0])


def fuse(observations, current, ladder, rework: bool = False) -> dict:
    cur_pct = float(current.get("percent", 0) or 0)
    held = {"state": current.get("state", ladder[0]["state"]), "percent": cur_pct,
            "confidence": float(current.get("confidence", 0) or 0)}
    usable = [o for o in observations if float(o.get("occlusion_ratio", 0) or 0) <= OCCLUDED]
    dev = [o for o in usable if o.get("state") == "DEVIATION_FLAGGED"]
    if dev:
        return {"state": "DEVIATION_FLAGGED", "percent": cur_pct,
                "confidence": max(float(o.get("confidence", 0)) for o in dev)}
    weights = {}  # rung percent -> [weights]
    for o in usable:
        w = float(o.get("confidence", 0)) * float(o.get("view_quality", 1)) * (1 - float(o.get("occlusion_ratio", 0) or 0))
        if w <= 0:
            continue
        rung = _snap(ladder, o["percent"]) if o.get("percent") is not None else _rung_for_state(ladder, o["state"])
        weights.setdefault(rung["percent"], []).append(w)
    if not weights:
        return held
    total = sum(sum(v) for v in weights.values())
    pct = max(weights, key=lambda p: (sum(weights[p]), -p))
    noisy_or = 1.0
    for w in weights[pct]:
        noisy_or *= 1 - min(w, 1.0)
    conf = (1 - noisy_or) * sum(weights[pct]) / total
    if not rework and pct < cur_pct:
        floor = max((r for r in ladder if r["percent"] <= cur_pct), key=lambda r: r["percent"])
        return {"state": floor["state"], "percent": cur_pct, "confidence": held["confidence"]}
    rung = next(r for r in ladder if r["percent"] == pct)
    return {"state": rung["state"], "percent": float(pct), "confidence": round(conf, 4)}
