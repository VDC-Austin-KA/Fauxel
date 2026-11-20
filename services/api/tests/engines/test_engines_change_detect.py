import numpy as np

from fauxel_api.vision.change_detect import change_score, image_features, propose_state

RNG = np.random.default_rng(1)
MASK = np.ones((200, 200), bool)


def noisy(img, s):
    return (img + RNG.normal(0, s, img.shape)).clip(0, 255).astype(np.uint8)


def empty():
    return noisy(np.full((200, 200, 3), 150, np.uint8), 2)


def studs():
    img = np.full((200, 200, 3), 60, np.uint8)
    for x in range(10, 200, 25):
        img[:, x:x + 6] = 190
    return noisy(img, 3)


def board():
    return noisy(np.full((200, 200, 3), (185, 190, 192), np.uint8), 1)


def paint():
    return noisy(np.full((200, 200, 3), (120, 160, 200), np.uint8), 1)


BASE = image_features(empty(), MASK)


def test_features_separate_textures():
    fs, fb = image_features(studs(), MASK), image_features(board(), MASK)
    assert fs["edge_density"] > 0.05 > fb["edge_density"]
    assert fb["board_frac"] > 0.9 > fs["board_frac"]
    assert fs["dark_frac"] > 0.08
    assert image_features(empty(), np.zeros((200, 200), bool))["n_pixels"] == 0
    spec = np.zeros((50, 50, 3), np.uint8)
    spec[:25] = 255
    assert abs(image_features(spec, np.ones((50, 50), bool))["specular_frac"] - 0.5) < 0.01


def test_wall_ladder():
    r0 = propose_state(empty(), MASK, "arch_interior", None, BASE)
    rs = propose_state(studs(), MASK, "arch_interior", None, BASE)
    rb = propose_state(board(), MASK, "arch_interior", None, BASE)
    assert (r0["percent"], r0["state"]) == (0, "NOT_STARTED")
    assert (rs["percent"], rs["state"]) == (50, "FRAMED_OR_ROUGHED")
    assert (rb["percent"], rb["state"]) == (80, "INSTALLED")
    rp = propose_state(paint(), MASK, "arch_interior", {"state": "INSTALLED", "percent": 80, "feature": rb["feature"]}, BASE)
    assert (rp["percent"], rp["state"]) == (100, "INSULATED_OR_FINISHED")


def test_confidence_is_conservative():
    r = propose_state(studs(), MASK, "arch_interior", None, None)  # no baseline
    assert r["confidence"] < 0.8
    assert propose_state(board(), MASK[:10, :10], "arch_interior", None, BASE)["confidence"] < 0.8


def test_unchanged_vs_prev_keeps_state():
    r1 = propose_state(studs(), MASK, "arch_interior", None, BASE)
    r2 = propose_state(studs(), MASK, "arch_interior", {"state": r1["state"], "percent": r1["percent"], "feature": r1["feature"]}, BASE)
    assert r2["percent"] == r1["percent"]


def test_mep_presence_vs_baseline():
    assert propose_state(empty(), MASK, "hvac", None, BASE)["percent"] == 0
    r = propose_state(studs(), MASK, "hvac", None, BASE)  # large change, textured
    assert r["percent"] == 80 and r["state"] == "INSTALLED"
    assert change_score(image_features(studs(), MASK), BASE) > 0.35
