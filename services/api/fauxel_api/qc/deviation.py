"""Positional deviation and pre-cover QC rules."""
from __future__ import annotations

import math

TOLERANCES_M = {"anchor": 0.0127, "embed": 0.0127, "structural": 0.0127, "hvac": 0.0381, "plumbing_fp": 0.0381,
                "electrical": 0.0254, "arch_interior": 0.0254, "other": 0.0254}

BOARD_PERCENT = 80  # ladder rung where board is hung
_WALL_DONE = {"INSTALLED", "INSULATED_OR_FINISHED"}
_MEP_OPEN = {"NOT_STARTED", "FRAMED_OR_ROUGHED"}


def positional_deviation(design_xyz, field_xyz, trade: str) -> dict:
    dx, dy, dz = (float(f) - float(d) for d, f in zip(design_xyz, field_xyz))
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)
    tol = TOLERANCES_M.get(trade, TOLERANCES_M["other"])
    ratio = dist / tol
    sev = "low" if ratio <= 1 else "medium" if ratio <= 2 else "high" if ratio <= 4 else "critical"
    return {"dx": dx, "dy": dy, "dz": dz, "dist_m": dist, "tolerance_m": tol, "severity": sev,
            "within_tolerance": ratio <= 1}


def precover_check(wall_state, mep_states) -> bool:
    """True (critical) when the wall is board-hung or beyond while in-wall MEP is not started, or only
    roughed in without verification. wall_state: state str or {state, percent}; mep_states: state strs or
    {state, verified}; a bare FRAMED_OR_ROUGHED string counts as unverified."""
    if isinstance(wall_state, dict):
        pct = wall_state.get("percent")
        closed = pct >= BOARD_PERCENT if pct is not None else wall_state.get("state") in _WALL_DONE
    else:
        closed = wall_state in _WALL_DONE
    if not closed:
        return False
    for m in mep_states or []:
        state, verified = (m.get("state"), bool(m.get("verified"))) if isinstance(m, dict) else (m, False)
        if state == "NOT_STARTED" or (state == "FRAMED_OR_ROUGHED" and not verified):
            return True
    return False
