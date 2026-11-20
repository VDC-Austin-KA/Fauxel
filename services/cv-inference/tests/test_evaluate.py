"""Eval report + gate on a handcrafted ONNX (brightness -> rung), no training needed."""
import json

import cv2
import numpy as np
import onnx
from onnx import TensorProto, helper

from fauxel_cv.evaluate import evaluate_all, main
from fauxel_cv.ladders import LADDERS
from fauxel_cv.train import MEAN, STD

LADDER = LADDERS["hvac"]  # 4 rungs: 0/30/80/100
GRAYS = [40, 100, 160, 220]


def _norm(g):  # mean of the normalised input for a flat gray image
    return float(np.mean([(g / 255 - m) / s for m, s in zip(MEAN, STD)]))


def _model(path, gain=20.0):
    """logits_k = gain * (mean(image) - t_k), t_k midway between rung brightnesses: P(rung > k) is sharp."""
    t = [(_norm(a) + _norm(b)) / 2 for a, b in zip(GRAYS, GRAYS[1:])]
    nodes = [helper.make_node("ReduceMean", ["image"], ["m"], axes=[1, 2, 3], keepdims=0),
             helper.make_node("Unsqueeze", ["m", "ax"], ["m2"]),
             helper.make_node("Sub", ["m2", "t"], ["d"]),
             helper.make_node("Mul", ["d", "g"], ["logits"])]
    inits = [helper.make_tensor("ax", TensorProto.INT64, [1], [1]), helper.make_tensor("t", TensorProto.FLOAT, [1, 3], t),
             helper.make_tensor("g", TensorProto.FLOAT, [1], [gain])]
    g = helper.make_graph(nodes, "toy", [helper.make_tensor_value_info("image", TensorProto.FLOAT, ["N", 3, 32, 32])],
                          [helper.make_tensor_value_info("logits", TensorProto.FLOAT, ["N", 3])], inits)
    onnx.save(helper.make_model(g, opset_imports=[helper.make_opsetid("", 17)], ir_version=8), str(path))


def _setup(tmp_path, mislabel=0.0, n_per=20):
    mdir = tmp_path / "models" / "hvac"
    mdir.mkdir(parents=True)
    _model(mdir / "model.onnx")
    (mdir / "manifest.json").write_text(json.dumps({"trade": "hvac", "version": "hvac-test", "ladder": LADDER, "input_size": 32,
                                                    "mean": MEAN, "std": STD, "temperature": 1.0, "onnx": "model.onnx"}))
    rng = np.random.default_rng(0)
    for i, r in enumerate(LADDER):
        for j in range(n_per):
            k = (i + 1) % 4 if rng.random() < mislabel else i  # label says rung i, picture shows rung k
            p = tmp_path / "data" / "hvac" / "val" / f"p{r['percent']:03d}" / f"{j}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(p), np.full((48, 48, 3), GRAYS[k], np.uint8))
    return tmp_path / "models", tmp_path / "data"


def test_clean_model_passes_and_gaps_listed(tmp_path):
    models, data = _setup(tmp_path)
    reps = {r["trade"]: r for r in evaluate_all(models, data)}
    h = reps["hvac"]
    assert h["status"] == "pass" and h["metrics"]["n"] == 80 and h["metrics"]["accuracy"] == 1.0
    assert h["auto_accept"]["coverage"] > 0.9 and h["auto_accept"]["precision"] == 1.0 and h["percent_mae"] == 0
    assert {reps[t]["status"] for t in ("structural", "electrical", "plumbing_fp", "arch_interior")} == {"no_model"}
    assert json.loads((models / "hvac" / "eval.json").read_text())["status"] == "pass"
    md = (models / "EVAL_REPORT.md").read_text()
    assert "| hvac | pass | hvac-test | 80 |" in md and "| structural | no_model |" in md


def test_noisy_labels_fail_gate_and_strict_exit(tmp_path):
    models, data = _setup(tmp_path, mislabel=0.3)
    rep = evaluate_all(models, data, ["hvac"])[0]
    assert rep["status"] == "fail" and not rep["gate"]["checks"]["min_auto_precision"]
    assert "hvac: failed" in (models / "EVAL_REPORT.md").read_text()
    assert main(["--models", str(models), "--data", str(data), "--trade", "hvac", "--strict"]) == 1
    assert main(["--models", str(models), "--data", str(data), "--trade", "hvac"]) == 0


def test_no_heldout_data(tmp_path):
    models, data = _setup(tmp_path)
    rep = evaluate_all(models, data, ["hvac"], split="test")[0]
    assert rep["status"] == "no_data"
    assert main(["--models", str(models), "--data", str(data), "--trade", "hvac", "--split", "test", "--strict"]) == 1
