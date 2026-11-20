"""Default progress ladders (platform-contract v1). Each rung: {percent, state, label}."""
NS, FR, IN, FIN, DEV = "NOT_STARTED", "FRAMED_OR_ROUGHED", "INSTALLED", "INSULATED_OR_FINISHED", "DEVIATION_FLAGGED"


def _l(*rungs):
    return [{"percent": p, "state": s, "label": lab} for p, s, lab in rungs]


_MEP = _l((0, NS, "not started"), (30, FR, "hangers/supports"), (80, IN, "installed"), (100, FIN, "insulated/trimmed"))
LADDERS = {
    "arch_interior": _l((0, NS, "not started"), (25, FR, "track"), (50, FR, "studs"),
                        (65, FR, "MEP rough verified + insulation"), (80, IN, "board hung"),
                        (95, IN, "taped"), (100, FIN, "painted")),
    "hvac": _MEP, "plumbing_fp": _MEP, "electrical": _MEP,
    "structural": _l((0, NS, "not started"), (40, FR, "formwork/rebar"), (100, IN, "poured/erected")),
    "other": _l((0, NS, "not started"), (100, IN, "installed")),
}


def ladder_for(trade: str):
    return LADDERS.get(trade, LADDERS["other"])
