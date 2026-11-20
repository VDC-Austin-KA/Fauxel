"""Model slot: per-trade ONNX state classifiers (trained by services/cv-inference) replace the heuristic when present.

Layout of FAUXEL_MODEL_DIR (one folder per trade, written by `python -m fauxel_cv.train`):
    <dir>/<trade>/manifest.json   {trade, ladder, input_size, mean, std, temperature, version, onnx?: "model.onnx", ...}
    <dir>/<trade>/model.onnx      input "image" (N,3,S,S) float32 RGB, normalised with mean/std; output (N, K-1) cumulative logits

The network is an ordinal classifier over the trade ladder rungs: output k is the logit of P(rung > k).  Class
probabilities are p_0 = 1-c_0, p_k = c_{k-1}-c_k, p_{K-1} = c_{K-2}, with c = sigmoid(logit / temperature) made monotone.
Confidence = max class probability after temperature scaling, so it is comparable to the 0.8 verification threshold.

crop_for_mask() is the single definition of the tangent-plane crop used both by training (routers/training.py) and by
inference (change_detect), so the network sees the same view geometry in both places.  fauxel_cv.crops carries a
numpy copy for the synthetic worker; a test in services/cv-inference asserts the two stay identical.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

try:
    from fauxel_capture.alignment import EquirectangularProjector
except ImportError:  # running from the repo without `pip install -e services/capture-ingestion`
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "capture-ingestion"))
    from fauxel_capture.alignment import EquirectangularProjector

log = logging.getLogger("fauxel.model_slot")

ENV_DIR = "FAUXEL_MODEL_DIR"
CROP_SIZE = 384            # side of the crop the trainer fetches (GET /training/crop default)
FOV_MIN, FOV_MAX = 40.0, 110.0
FOV_MARGIN = 1.25          # context around the element, as a multiple of its angular radius
_TRADE_RE = re.compile(r"^[a-z0-9_]{1,40}$")


# ---------------------------------------------------------------- tangent-plane crop

def _unit_dirs(us: np.ndarray, vs: np.ndarray, W: int, H: int) -> np.ndarray:
    """Pixel centres (u, v) -> unit rays in the OpenCV camera frame (inverse of the projection in projection.py)."""
    theta = ((us + 0.5) / W - 0.5) * 2 * np.pi
    phi = ((vs + 0.5) / H - 0.5) * np.pi
    return np.stack([np.cos(phi) * np.sin(theta), np.sin(phi), np.cos(phi) * np.cos(theta)], 1)


def _dirs_from_mask(mask: np.ndarray, max_pts: int = 4000) -> np.ndarray:
    H, W = mask.shape
    vs, us = np.nonzero(mask)
    if len(us) > max_pts:
        sel = np.random.default_rng(0).choice(len(us), max_pts, replace=False)
        us, vs = us[sel], vs[sel]
    return _unit_dirs(us.astype(float), vs.astype(float), W, H)


def _dirs_from_bbox(bbox, W: int, H: int, n: int = 24) -> np.ndarray:
    """bbox (x0,y0,x1,y1); x0 > x1 means it wraps across the azimuth seam (see projection.Visible.pixel_bbox)."""
    x0, y0, x1, y1 = [float(b) for b in bbox]
    if x0 > x1:
        x1 += W
    us = np.linspace(x0, x1, n) % W
    vs = np.linspace(y0, y1, n)
    uu, vv = np.meshgrid(us, vs)
    return _unit_dirs(uu.ravel(), vv.ravel(), W, H)


def view_for_dirs(dirs: np.ndarray) -> tuple[np.ndarray, float]:
    """Unit rays covering an element -> (centre ray, field of view in degrees of the square tangent-plane crop)."""
    c = dirs.mean(0)
    n = np.linalg.norm(c)
    c = c / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
    r = float(np.arccos(np.clip(dirs @ c, -1, 1)).max())
    fov = np.degrees(2 * np.arctan(FOV_MARGIN * np.tan(min(r, np.radians(80)))))
    return c, float(np.clip(fov, FOV_MIN, FOV_MAX))


def tangent_crop(image_bgr: np.ndarray, center: np.ndarray, fov_deg: float, size: int = CROP_SIZE) -> np.ndarray:
    """Level (no roll) rectilinear view of an equirect panorama centred on ray `center` (OpenCV camera frame).

    Uses EquirectangularProjector for the virtual camera intrinsics/ray grid.  Its extract_rectilinear_view() composes
    yaw/pitch extrinsically (adds roll off the horizon), so the rotation is built here as R = Ry(yaw) @ Rx(pitch).
    """
    H, W = image_bgr.shape[:2]
    P = EquirectangularProjector(2 * H, H, virt_size=size, fov_deg=fov_deg)
    x, y, z = [float(v) for v in center]
    a, b = np.arctan2(x, z), -np.arcsin(np.clip(y, -1, 1))
    Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    rays = (Ry @ Rx) @ P._base_rays
    theta = np.arctan2(rays[0], rays[2])
    phi = np.arcsin(np.clip(rays[1], -1, 1))
    map_u = (W * (theta / (2 * np.pi) + 0.5) - 0.5).reshape(size, size).astype(np.float32)
    map_v = (H * (phi / np.pi + 0.5) - 0.5).reshape(size, size).astype(np.float32)
    return cv2.remap(image_bgr, map_u, map_v, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def crop_for_mask(image_bgr: np.ndarray, mask: Optional[np.ndarray] = None, bbox=None, size: int = CROP_SIZE) -> Optional[np.ndarray]:
    """Tangent-plane crop of a full equirect panorama around an element's projected mask (or pixel bbox).

    Returns None when the mask is empty and no bbox is given.
    """
    H, W = image_bgr.shape[:2]
    if mask is not None and np.any(mask):
        dirs = _dirs_from_mask(np.asarray(mask, bool))
    elif bbox is not None:
        dirs = _dirs_from_bbox(bbox, W, H)
    else:
        return None
    c, fov = view_for_dirs(dirs)
    return tangent_crop(image_bgr, c, fov, size)


# ---------------------------------------------------------------- ONNX models

@dataclass
class _Model:
    manifest: dict
    session: object
    input_name: str
    mean: np.ndarray
    std: np.ndarray
    temperature: float
    ladder: list
    size: int


_cache: dict = {}
_lock = threading.Lock()


def model_dir() -> Optional[Path]:
    d = os.environ.get(ENV_DIR)
    return Path(d) if d else None


def reset_cache() -> None:
    with _lock:
        _cache.clear()


def _providers() -> list:
    import onnxruntime as ort
    avail = ort.get_available_providers()
    return ["CUDAExecutionProvider", "CPUExecutionProvider"] if "CUDAExecutionProvider" in avail else ["CPUExecutionProvider"]


def _load(trade: str) -> Optional[_Model]:
    d = model_dir()
    if d is None or not _TRADE_RE.match(trade or ""):
        return None
    mf_path = d / trade / "manifest.json"
    try:
        stamp = mf_path.stat().st_mtime_ns
    except OSError:
        return None
    key = (str(d.resolve()), trade, stamp)
    with _lock:
        if key in _cache:
            return _cache[key]
    model = None
    try:
        import onnxruntime as ort
        mf = json.loads(mf_path.read_text())
        onnx_path = (mf_path.parent / mf.get("onnx", "model.onnx")).resolve()
        if mf_path.parent.resolve() not in onnx_path.parents:
            raise ValueError("onnx path escapes the model folder")
        ladder = mf["ladder"]
        if mf.get("trade") != trade or len(ladder) < 2:
            raise ValueError("manifest trade/ladder mismatch")
        sess = ort.InferenceSession(str(onnx_path), providers=_providers())
        out_dim = sess.get_outputs()[0].shape[-1]
        if isinstance(out_dim, int) and out_dim != len(ladder) - 1:
            raise ValueError(f"model emits {out_dim} cumulative logits, ladder needs {len(ladder) - 1}")
        model = _Model(mf, sess, sess.get_inputs()[0].name, np.asarray(mf["mean"], np.float32), np.asarray(mf["std"], np.float32),
                       float(mf.get("temperature", 1.0)), ladder, int(mf["input_size"]))
        log.info("model slot: loaded %s v%s (%s)", trade, mf.get("version"), sess.get_providers()[0])
    except Exception as exc:  # a broken model must not take processing down: fall back to the heuristic
        log.warning("model slot: cannot load %s model from %s: %s", trade, d, exc)
    with _lock:
        for k in [k for k in _cache if k[:2] == key[:2]]:
            del _cache[k]  # drop stale versions of the same model
        _cache[key] = model
    return model


def available(trade: str) -> bool:
    return _load(trade) is not None


def class_probs(logits: np.ndarray, temperature: float) -> np.ndarray:
    """Cumulative logits (K-1,) -> class probabilities (K,) with temperature scaling."""
    c = 1.0 / (1.0 + np.exp(-np.asarray(logits, np.float64) / max(temperature, 1e-3)))
    c = np.minimum.accumulate(np.clip(c, 0, 1))
    p = np.diff(np.concatenate([[1.0], c, [0.0]])) * -1
    p = np.clip(p, 0, None)
    return p / max(p.sum(), 1e-12)


def _preprocess(m: _Model, crop_bgr: np.ndarray) -> np.ndarray:
    img = cv2.resize(crop_bgr, (m.size, m.size), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return ((rgb - m.mean) / m.std).transpose(2, 0, 1)[None].astype(np.float32)


def predict(image_bgr_crop: np.ndarray, trade: str) -> Optional[dict]:
    """Tangent-plane crop (BGR uint8) -> {state, percent, confidence} or None when no model exists for the trade."""
    m = _load(trade)
    if m is None or image_bgr_crop is None or image_bgr_crop.size == 0:
        return None
    logits = m.session.run(None, {m.input_name: _preprocess(m, image_bgr_crop)})[0][0]
    p = class_probs(logits, m.temperature)
    k = int(p.argmax())
    rung = m.ladder[k]
    return {"state": rung["state"], "percent": float(rung["percent"]), "confidence": float(p[k]),
            "model_version": m.manifest.get("version")}


def predict_element(image_bgr: np.ndarray, mask: np.ndarray, trade: str) -> Optional[dict]:
    """Full panorama + element mask -> model prediction, or None when there is no model / no pixels."""
    if not available(trade):
        return None
    crop = crop_for_mask(image_bgr, mask)
    return None if crop is None else predict(crop, trade)
