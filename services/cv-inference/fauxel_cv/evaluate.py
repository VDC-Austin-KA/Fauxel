"""Per-trade eval report and release gate (M4.3 definition of done).

    python -m fauxel_cv.evaluate --models models/ --data data/ [--trade hvac ...] [--split val] [--strict]

Scores each trade's exported model.onnx the way the API model slot runs it (same preprocessing, same temperature) on a
capture-held-out split, writes <models>/<trade>/eval.json and one <models>/EVAL_REPORT.md table for all trades.
The gate looks hardest at predictions at or above the 0.8 auto-accept threshold, because those write verified status
without a human check. Trades with no model or no held-out data are listed so the gaps stay visible.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from . import ordinal
from .ladders import LADDERS
from .train import CropDataset

AUTO_ACCEPT = 0.8  # API: CV proposals below this go to the human queue and never write verified status
GATE = {"min_n": 50, "min_auto_precision": 0.95, "max_ece": 0.10, "min_within_one_rung": 0.90}
TRADES = [t for t in LADDERS if t != "other"]


def _preprocess(path: Path, size: int, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    """Mirror of model_slot._preprocess (cv2 INTER_AREA, RGB, /255, mean/std, NCHW)."""
    img = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if img is None:
        raise IOError(f"cannot read {path}")
    img = cv2.resize(img, (size, size), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return ((rgb - mean) / std).transpose(2, 0, 1).astype(np.float32)


def evaluate_trade(models: Path, data: Path, trade: str, split: str = "val", gate: dict = GATE, batch: int = 32) -> dict:
    tdir = models / trade
    base = {"trade": trade, "split": split, "evaluated_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    mf_path = tdir / "manifest.json"
    if not mf_path.exists():
        return {**base, "status": "no_model"}
    mf = json.loads(mf_path.read_text())
    onnx_path = (tdir / mf.get("onnx", "model.onnx")).resolve()
    if tdir.resolve() not in onnx_path.parents or mf.get("trade") != trade:
        raise ValueError(f"{trade}: manifest trade or onnx path invalid")
    ladder, size = mf["ladder"], int(mf["input_size"])
    items = CropDataset(data / trade, split, ladder, size, False).items
    if not items:
        return {**base, "status": "no_data", "model_version": mf.get("version")}

    import onnxruntime as ort
    sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    name = sess.get_inputs()[0].name
    mean, std = np.asarray(mf["mean"], np.float32), np.asarray(mf["std"], np.float32)
    logits = np.concatenate([sess.run(None, {name: np.stack([_preprocess(p, size, mean, std) for p, _ in items[i:i + batch]])})[0]
                             for i in range(0, len(items), batch)])
    y = np.array([k for _, k in items], int)
    probs = ordinal.class_probs(logits, float(mf.get("temperature", 1.0)))
    m = ordinal.metrics(probs, y, ladder)
    pred, conf = probs.argmax(1), probs.max(1)
    pct = np.array([float(r["percent"]) for r in ladder])
    st = np.array([r["state"] for r in ladder])
    hi = conf >= AUTO_ACCEPT
    auto = {"threshold": AUTO_ACCEPT, "coverage": float(hi.mean()), "n": int(hi.sum()),
            "precision": float((pred[hi] == y[hi]).mean()) if hi.any() else None,
            "state_precision": float((st[pred[hi]] == st[y[hi]]).mean()) if hi.any() else None}
    checks = {"min_n": m["n"] >= gate["min_n"],
              "min_auto_precision": auto["precision"] is not None and auto["precision"] >= gate["min_auto_precision"],
              "max_ece": m["ece"] <= gate["max_ece"],
              "min_within_one_rung": m["within_one_rung"] >= gate["min_within_one_rung"]}
    rep = {**base, "status": "pass" if all(checks.values()) else "fail", "model_version": mf.get("version"),
           "metrics": m, "auto_accept": auto,
           "state_accuracy": float((st[pred] == st[y]).mean()), "percent_mae": float(np.abs(pct[pred] - pct[y]).mean()),
           "gate": {"thresholds": gate, "checks": checks}}
    (tdir / "eval.json").write_text(json.dumps(rep, indent=1))
    return rep


def _f(v, pct=True):
    return "n/a" if v is None else (f"{v * 100:.1f}%" if pct else f"{v:.3f}")


def render_md(reports: list[dict]) -> str:
    rows = ["| Trade | Status | Model | n val | Acc | ±1 rung | Macro F1 | ECE | Auto-accept cov. | Auto-accept prec. | % MAE |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in reports:
        if "metrics" not in r:
            rows.append(f"| {r['trade']} | {r['status']} | {r.get('model_version') or '—'} | 0 | | | | | | | |")
            continue
        m, a = r["metrics"], r["auto_accept"]
        rows.append(f"| {r['trade']} | {r['status']} | {r['model_version']} | {m['n']} | {_f(m['accuracy'])} | {_f(m['within_one_rung'])} | "
                    f"{_f(m['macro_f1'], False)} | {_f(m['ece'], False)} | {_f(a['coverage'])} | {_f(a['precision'])} | {r['percent_mae']:.1f} |")
    fails = [f"- {r['trade']}: failed {', '.join(k for k, ok in r['gate']['checks'].items() if not ok)}" for r in reports if r["status"] == "fail"]
    g = GATE
    head = ["# Fauxel trade model eval report", "", f"Generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Split: capture-held-out.",
            f"Gate: n ≥ {g['min_n']}, auto-accept (conf ≥ {AUTO_ACCEPT}) precision ≥ {g['min_auto_precision']:.0%}, "
            f"ECE ≤ {g['max_ece']}, within one rung ≥ {g['min_within_one_rung']:.0%}.", ""]
    return "\n".join(head + rows + ([""] + fails if fails else [])) + "\n"


def evaluate_all(models: Path, data: Path, trades=None, split: str = "val", gate: dict = GATE) -> list[dict]:
    reports = [evaluate_trade(models, data, t, split, gate) for t in (trades or TRADES)]
    models.mkdir(parents=True, exist_ok=True)
    (models / "EVAL_REPORT.md").write_text(render_md(reports))
    return reports


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--models", required=True, type=Path)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--trade", action="append", help="repeatable; default: every trade ladder")
    ap.add_argument("--split", default="val")
    ap.add_argument("--min-n", type=int, default=GATE["min_n"])
    ap.add_argument("--strict", action="store_true", help="exit 1 unless every evaluated model passes the gate")
    a = ap.parse_args(argv)
    reps = evaluate_all(a.models, a.data, a.trade, a.split, {**GATE, "min_n": a.min_n})
    print((a.models / "EVAL_REPORT.md").read_text())
    return 1 if a.strict and any(r["status"] in ("fail", "no_data") for r in reps) else 0


if __name__ == "__main__":
    raise SystemExit(main())
