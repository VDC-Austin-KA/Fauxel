"""BIM-prior change detection: transparent per-element state proposals from image statistics.

image_features() summarises the pixels inside an element's projected mask: Canny edge density, Lab mean/std,
gray-histogram entropy (0..1), board_frac (pixels whose 5x5 gray std < 4, i.e. flat uniform surface), specular_frac
(very bright, unsaturated), dark_frac (L < 30, gaps between studs/framing).

propose_state() compares against the empty-scene baseline feature (change score 0..1 from edge density, lightness,
entropy and flatness shifts) and the previous observation, then applies per-trade rules:
  arch_interior  framed if edges >= 0.03 and not flat; 50 (studs) when edges >= 0.05 and dark gaps >= 0.08,
                 else 25 (track); flat + low edges = board hung (80); flat, very low variance and a colour/lightness
                 shift vs the board (or vs prev) = painted (100). 65 (MEP rough + insulation) and 95 (taped) are not
                 visible to this heuristic and come only from human verification.
  hvac/electrical/plumbing_fp  presence vs baseline: change >= 0.15 -> 30 (supports), >= 0.35 -> 80 (installed);
                 100 (insulated/trimmed) when prev was installed and the surface became much flatter.
  structural     change >= 0.2 with edges (rebar/formwork) -> 40; flat concrete-like surface with large change -> 100.
  other          change >= 0.3 -> 100.
Indistinguishable from the baseline (change < 0.1) = not started; unchanged vs prev (change < 0.05) keeps prev. Confidence = 0.45 + 0.4 * rule margin (0..1), x0.75 without a baseline,
x0.6 for tiny masks, capped at 0.95, so it exceeds 0.8 only with strong evidence; the rest goes to human verification.
"""
from __future__ import annotations

import cv2
import numpy as np

from . import model_slot
from .ladders import LADDERS

MIN_PIXELS = 400


def _crop(image, mask):
    ys, xs = np.nonzero(mask)
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return image[y0:y1, x0:x1], mask[y0:y1, x0:x1]


def image_features(image_bgr: np.ndarray, mask: np.ndarray) -> dict:
    mask = np.asarray(mask, bool)
    n = int(mask.sum())
    if n == 0:
        return {"n_pixels": 0, "edge_density": 0.0, "L_mean": 0.0, "L_std": 0.0, "a_mean": 0.0, "b_mean": 0.0,
                "entropy": 0.0, "board_frac": 0.0, "specular_frac": 0.0, "dark_frac": 0.0}
    img, m = _crop(image_bgr, mask)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB).astype(np.float32)
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    edges = cv2.Canny(gray, 50, 150) > 0
    g = gray.astype(np.float32)
    mu = cv2.blur(g, (5, 5))
    local_std = np.sqrt(np.maximum(cv2.blur(g * g, (5, 5)) - mu * mu, 0))
    hist = np.bincount((gray[m] >> 3), minlength=32).astype(float)
    p = hist[hist > 0] / hist.sum()
    L = lab[..., 0][m] * 100 / 255
    return {
        "n_pixels": n,
        "edge_density": float(edges[m].mean()),
        "L_mean": float(L.mean()), "L_std": float(L.std()),
        "a_mean": float(lab[..., 1][m].mean() - 128), "b_mean": float(lab[..., 2][m].mean() - 128),
        "entropy": float(-(p * np.log2(p)).sum() / 5.0),
        "board_frac": float((local_std[m] < 4).mean()),
        "specular_frac": float(((hsv[..., 2][m] > 245) & (hsv[..., 1][m] < 30)).mean()),
        "dark_frac": float((L < 30).mean()),
    }


def change_score(f: dict, ref: dict) -> float:
    """0..1 dissimilarity between two feature dicts."""
    c = (0.4 * min(abs(f["edge_density"] - ref["edge_density"]) / 0.08, 1)
         + 0.3 * min(abs(f["L_mean"] - ref["L_mean"]) / 15, 1)
         + 0.15 * min(abs(f["entropy"] - ref["entropy"]) / 0.3, 1)
         + 0.15 * min(abs(f["board_frac"] - ref["board_frac"]) / 0.5, 1))
    return float(c)


def _m(x, thr):  # margin above a threshold, 0..1
    return float(np.clip((x - thr) / max(thr, 1e-6), 0, 1))


def _rule(trade, f, change, prev_f):
    """-> (percent, margin 0..1)."""
    ed, flat = f["edge_density"], f["board_frac"]
    if trade == "arch_interior":
        if flat >= 0.6 and ed < 0.03:  # board or paint
            chroma = (f["a_mean"] ** 2 + f["b_mean"] ** 2) ** 0.5
            painted = flat > 0.9 and f["L_std"] < 4 and (chroma > 12 or f["L_mean"] > 85 or
                      (prev_f is not None and change_score(f, prev_f) > 0.15 and prev_f["board_frac"] > 0.6))
            return (100, min(_m(flat, 0.9) + 0.5, 1)) if painted else (80, _m(flat, 0.6) + 0.3 if flat < 0.9 else 1.0)
        if ed >= 0.03 and flat < 0.85:
            if ed >= 0.05 and f["dark_frac"] >= 0.08:
                return 50, min(_m(ed, 0.05) + _m(f["dark_frac"], 0.08) + 0.3, 1)
            return 25, _m(ed, 0.03)
        return 0, 0.5 if change < 0.15 else 0.2
    if trade in ("hvac", "electrical", "plumbing_fp"):
        if change >= 0.35:
            if prev_f is not None and flat - prev_f["board_frac"] > 0.25 and prev_f["board_frac"] < 0.5:
                return 100, min(_m(flat - prev_f["board_frac"], 0.25) + 0.3, 1)
            return 80, _m(change, 0.35)
        if change >= 0.15:
            return 30, _m(change, 0.15)
        return 0, 1 - change / 0.15
    if trade == "structural":
        if change >= 0.2 and flat >= 0.6 and ed < 0.03:
            return 100, _m(change, 0.2)
        if change >= 0.2 and ed >= 0.03:
            return 40, _m(change, 0.2)
        return 0, 1 - change / 0.2
    return (100, _m(change, 0.3)) if change >= 0.3 else (0, 1 - change / 0.3)


def propose_state(image_bgr, mask, trade, prev=None, baseline_feature=None) -> dict:
    """prev: {state, percent, feature?} of the previous observation or None. -> {state, percent, confidence, feature}."""
    f = image_features(image_bgr, mask)
    ladder = LADDERS.get(trade, LADDERS["other"])
    prev_f = (prev or {}).get("feature")
    if f["n_pixels"] == 0:
        return {"state": ladder[0]["state"], "percent": 0, "confidence": 0.0, "feature": f}
    if model_slot.available(trade):  # trained trade model (FAUXEL_MODEL_DIR) replaces the heuristic; heuristic stays the fallback
        try:
            mp = model_slot.predict_element(image_bgr, mask, trade)
        except Exception:
            mp = None
        if mp:
            conf = mp["confidence"] * (0.6 if f["n_pixels"] < MIN_PIXELS else 1.0)
            return {"state": mp["state"], "percent": mp["percent"], "confidence": round(min(conf, 0.99), 3), "feature": f}
    change = change_score(f, baseline_feature) if baseline_feature else 0.5
    if prev and prev_f and change_score(f, prev_f) < 0.05:  # nothing moved since last time
        conf = min(0.9, 0.7 + 0.2 * (1 - change_score(f, prev_f) / 0.05)) if prev.get("percent", 0) > 0 else 0.6
        return {"state": prev["state"], "percent": prev["percent"], "confidence": round(conf, 3), "feature": f}
    if baseline_feature and change < 0.1:  # indistinguishable from the empty scene
        return {"state": ladder[0]["state"], "percent": 0, "confidence": round(0.45 + 0.4 * (1 - change / 0.1), 3), "feature": f}
    pct, margin = _rule(trade, f, change, prev_f)
    pct = min(ladder, key=lambda r: abs(r["percent"] - pct))
    conf = 0.45 + 0.4 * float(np.clip(margin, 0, 1))
    if not baseline_feature:
        conf *= 0.75
    if f["n_pixels"] < MIN_PIXELS:
        conf *= 0.6
    return {"state": pct["state"], "percent": pct["percent"], "confidence": round(min(conf, 0.95), 3), "feature": f}
