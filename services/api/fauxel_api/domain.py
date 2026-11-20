"""Shared constants and small pure helpers (states, trades, colors, ladders, IFC class -> trade)."""
from __future__ import annotations

import re

STATES = ("NOT_STARTED", "FRAMED_OR_ROUGHED", "INSTALLED", "INSULATED_OR_FINISHED", "DEVIATION_FLAGGED")
TRADES = ("structural", "arch_interior", "hvac", "electrical", "plumbing_fp", "other")
CONFIDENCE_REVIEW = 0.8

GREEN, YELLOW, RED, GRAY = "#22c55e", "#eab308", "#ef4444", "#9ca3af"


def status_color(state: str, percent: float, deviation: bool = False) -> str:
    if deviation or state == "DEVIATION_FLAGGED":
        return RED
    if state in ("INSTALLED", "INSULATED_OR_FINISHED"):
        return GREEN
    if state == "FRAMED_OR_ROUGHED" or 1 <= percent <= 99:
        return YELLOW
    return GRAY


# Checked in order, first hit wins. Matched against the lowercase IFC class name.
_TRADE_KEYWORDS = (
    ("electrical", ("cable", "light", "electric", "lamp", "outlet", "switchingdevice", "transformer", "junctionbox", "protectivedevice")),
    ("hvac", ("duct", "airterminal", "fan", "damper", "coil", "boiler", "chiller", "unitaryequipment", "heatexchanger", "airtoair", "humidifier")),
    ("plumbing_fp", ("pipe", "sanitary", "firesuppression", "valve", "pump", "tank", "wasteterminal", "stackterminal")),
    ("structural", ("beam", "column", "slab", "footing", "member", "pile", "reinforcing", "tendon")),
    ("arch_interior", ("wall", "door", "window", "covering", "ceiling", "curtainwall")),
)


def trade_for_class(ifc_class: str) -> str:
    c = ifc_class.lower()
    for trade, keys in _TRADE_KEYWORDS:
        if any(k in c for k in keys):
            return trade
    return "other"


def pick_ladder(rules: list[dict], trade: str, ifc_class: str | None) -> list[dict]:
    """rules: progress_rules rows. A matching LIKE pattern beats the trade default."""
    default = None
    for r in rules:
        if r["trade"] != trade:
            continue
        pat = r["ifc_class"]
        if pat is None:
            default = default or r["ladder"]
        elif ifc_class and re.fullmatch(pat.replace("%", ".*"), ifc_class, re.I):
            return r["ladder"]
    if default is None:
        default = next((r["ladder"] for r in rules if r["trade"] == "other" and r["ifc_class"] is None), [])
    return default


def percent_for_state(ladder: list[dict], state: str, current: float = 0.0) -> float:
    if state == "DEVIATION_FLAGGED":
        return current
    for step in ladder:
        if step["state"] == state:
            return float(step["percent"])
    return current


def load_rules(conn, version: int | None = None) -> tuple[int, list[dict]]:
    if version is None:
        version = conn.execute("SELECT max(version) AS v FROM progress_rules").fetchone()["v"]
    rows = conn.execute("SELECT trade, ifc_class, ladder FROM progress_rules WHERE version = %s", (version,)).fetchall()
    return version, rows
