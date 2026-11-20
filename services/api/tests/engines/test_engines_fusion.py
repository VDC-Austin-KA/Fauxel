from fauxel_api.vision.fusion import fuse
from fauxel_api.vision.ladders import LADDERS

L = LADDERS["arch_interior"]


def ob(pct, conf=0.9, vq=1.0, occ=0.0, state="INSTALLED"):
    return {"state": state, "percent": pct, "confidence": conf, "view_quality": vq, "occlusion_ratio": occ}


def test_agreeing_observations_raise_confidence():
    one = fuse([ob(80, 0.6)], {"state": "NOT_STARTED", "percent": 0}, L)
    two = fuse([ob(80, 0.6), ob(80, 0.6)], {"state": "NOT_STARTED", "percent": 0}, L)
    assert one["percent"] == two["percent"] == 80 and one["state"] == "INSTALLED"
    assert two["confidence"] > one["confidence"]


def test_weighting_prefers_good_views_and_snaps():
    r = fuse([ob(52, 0.9, 1.0), ob(100, 0.9, 0.2, 0.5)], {"percent": 0, "state": "NOT_STARTED"}, L)
    assert r["percent"] == 50 and r["state"] == "FRAMED_OR_ROUGHED"


def test_monotonic_and_rework():
    cur = {"state": "INSTALLED", "percent": 80, "confidence": 0.9}
    r = fuse([ob(25, 0.9, state="FRAMED_OR_ROUGHED")], cur, L)
    assert r["percent"] == 80 and r["state"] == "INSTALLED"
    r = fuse([ob(25, 0.9, state="FRAMED_OR_ROUGHED")], cur, L, rework=True)
    assert r["percent"] == 25 and r["state"] == "FRAMED_OR_ROUGHED"


def test_occluded_never_lowers_or_changes():
    cur = {"state": "INSTALLED", "percent": 80, "confidence": 0.95}
    r = fuse([ob(0, 0.99, 1.0, 0.9, "NOT_STARTED")], cur, L)
    assert r == {"state": "INSTALLED", "percent": 80.0, "confidence": 0.95}


def test_deviation_passes_through():
    cur = {"state": "FRAMED_OR_ROUGHED", "percent": 50}
    r = fuse([ob(80, 0.9), {"state": "DEVIATION_FLAGGED", "confidence": 0.7, "view_quality": 1, "occlusion_ratio": 0.1}], cur, L)
    assert r["state"] == "DEVIATION_FLAGGED" and r["percent"] == 50


def test_state_only_observation():
    r = fuse([{"state": "INSTALLED", "confidence": 0.9, "view_quality": 1, "occlusion_ratio": 0}], {"percent": 0, "state": "NOT_STARTED"}, L)
    assert r["percent"] == 80
