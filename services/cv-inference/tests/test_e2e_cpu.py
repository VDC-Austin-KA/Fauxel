"""Procedural data -> train (CPU, 2 epochs) -> ONNX -> API model slot -> change_detect."""
import json
import time

import numpy as np
import pytest

from fauxel_cv import ordinal
from fauxel_cv.ladders import LADDERS
from fauxel_cv.procedural import make_dataset
from fauxel_cv.train import train


def test_ordinal_math():
    lg = np.array([[5.0, 5.0, 5.0], [-5.0, -5.0, -5.0], [5.0, -5.0, -5.0]])
    p = ordinal.class_probs(lg)
    assert p.argmax(1).tolist() == [3, 0, 1] and np.allclose(p.sum(1), 1)
    y = np.array([3, 0, 1, 1, 2, 2])
    noisy = np.random.default_rng(0).normal(0, 1, (6, 3)) * 8 * (np.arange(6)[:, None] % 2 * 2 - 1) + 1
    assert 0.2 <= ordinal.fit_temperature(noisy, y) <= 8.0


@pytest.mark.slow
def test_train_export_and_slot(tmp_path, monkeypatch):
    ms = pytest.importorskip("fauxel_api.vision.model_slot")
    make_dataset(tmp_path / "data", "hvac", n=200, captures=5)
    rep = train(tmp_path / "data", "hvac", tmp_path / "models", backbone="efficientnet_b0", size=128, epochs=2, batch_size=16,
                pretrained=True, num_workers=0, device="cpu")
    assert rep["n_val"] > 0 and rep["val_split"] == "capture split"
    mdir = tmp_path / "models" / "hvac"
    mf = json.loads((mdir / "manifest.json").read_text())
    assert mf["ladder"] == LADDERS["hvac"] and mf["input_size"] == 128 and 0.2 <= mf["temperature"] <= 8
    assert (mdir / "model.onnx").stat().st_size > 1e6 and (mdir / "metrics.json").exists()

    monkeypatch.setenv("FAUXEL_MODEL_DIR", str(tmp_path / "models"))
    ms.reset_cache()
    assert ms.predict(np.zeros((64, 64, 3), np.uint8), "plumbing_fp") is None  # no model for that trade
    img = np.random.default_rng(1).integers(0, 255, (192, 192, 3), dtype=np.uint8)
    t0 = time.perf_counter()
    r = ms.predict(img, "hvac")
    ms.predict(img, "hvac")
    print("cpu latency ms", (time.perf_counter() - t0) / 2 * 1000)
    assert (r["state"], r["percent"]) in {(x["state"], float(x["percent"])) for x in LADDERS["hvac"]}
    assert 0 < r["confidence"] <= 1

    from fauxel_api.vision.change_detect import propose_state
    pano = np.random.default_rng(2).integers(0, 255, (256, 512, 3), dtype=np.uint8)
    mask = np.zeros((256, 512), bool)
    mask[90:150, 200:300] = True
    p = propose_state(pano, mask, "hvac")
    assert p["percent"] in {0, 30, 80, 100} and "feature" in p
    monkeypatch.delenv("FAUXEL_MODEL_DIR")
    ms.reset_cache()
    assert ms.predict_element(pano, mask, "hvac") is None and propose_state(pano, mask, "hvac")["state"]  # heuristic fallback
