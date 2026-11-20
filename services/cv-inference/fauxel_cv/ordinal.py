"""Ordinal (cumulative-link, Frank & Hall) maths. Output k of the network is the logit of P(rung > k), k = 0..K-2.
Mirrors model_slot.class_probs in the API."""
from __future__ import annotations

import numpy as np
import torch
import torch.nn.functional as F


def targets(y: torch.Tensor, k: int) -> torch.Tensor:
    """(N,) rung index -> (N, K-1) float cumulative targets [y > j]."""
    return (y[:, None] > torch.arange(k - 1, device=y.device)[None]).float()


def loss(logits, y, k, class_weight=None):
    per = F.binary_cross_entropy_with_logits(logits, targets(y, k), reduction="none").mean(1)
    if class_weight is not None:
        per = per * class_weight[y]
    return per.mean()


def class_probs(logits: np.ndarray, temperature: float = 1.0) -> np.ndarray:
    """(N, K-1) logits -> (N, K) class probabilities."""
    c = 1 / (1 + np.exp(-np.asarray(logits, np.float64) / max(temperature, 1e-3)))
    c = np.minimum.accumulate(np.clip(c, 0, 1), axis=1)
    n = len(c)
    full = np.concatenate([np.ones((n, 1)), c, np.zeros((n, 1))], 1)
    p = np.clip(-np.diff(full, axis=1), 0, None)
    return p / np.maximum(p.sum(1, keepdims=True), 1e-12)


def nll(probs, y):
    return float(-np.log(np.clip(probs[np.arange(len(y)), y], 1e-9, 1)).mean())


def fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    """Temperature minimising class NLL on held-out logits (1-D log-grid search; robust, no optimiser state)."""
    grid = np.exp(np.linspace(np.log(0.2), np.log(8.0), 160))
    return float(grid[int(np.argmin([nll(class_probs(logits, t), y) for t in grid]))])


def ece(probs, y, bins: int = 10) -> float:
    conf, pred = probs.max(1), probs.argmax(1)
    edges, tot = np.linspace(0, 1, bins + 1), 0.0
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            tot += m.mean() * abs((pred[m] == y[m]).mean() - conf[m].mean())
    return float(tot)


def confusion(pred, y, k):
    cm = np.zeros((k, k), int)
    np.add.at(cm, (y, pred), 1)
    return cm


def metrics(probs, y, ladder) -> dict:
    k = len(ladder)
    pred = probs.argmax(1)
    cm = confusion(pred, y, k)
    tp = np.diag(cm).astype(float)
    prec = tp / np.maximum(cm.sum(0), 1)
    rec = tp / np.maximum(cm.sum(1), 1)
    f1 = 2 * prec * rec / np.maximum(prec + rec, 1e-12)
    support = cm.sum(1)
    present = support > 0
    return {"n": int(len(y)), "accuracy": float((pred == y).mean()) if len(y) else 0.0,
            "within_one_rung": float((np.abs(pred - y) <= 1).mean()) if len(y) else 0.0,
            "macro_f1": float(f1[present].mean()) if present.any() else 0.0,
            "per_class": [{"percent": r["percent"], "label": r["label"], "support": int(support[i]), "precision": float(prec[i]),
                           "recall": float(rec[i]), "f1": float(f1[i])} for i, r in enumerate(ladder)],
            "confusion_matrix": cm.tolist(), "nll": nll(probs, y) if len(y) else 0.0, "ece": ece(probs, y) if len(y) else 0.0}
