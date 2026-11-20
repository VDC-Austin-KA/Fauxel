"""Panorama handling: decode/validate equirect images, MP4 keyframe extraction, previews."""
from __future__ import annotations

import os
import tempfile

import cv2
import numpy as np

PREVIEW_W = 2048
MAX_PIXELS = 120_000_000


def decode_image(data: bytes) -> np.ndarray:
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("not a decodable JPG/PNG image")
    return img


def check_equirect(img: np.ndarray) -> None:
    h, w = img.shape[:2]
    if h * w > MAX_PIXELS:
        raise ValueError("image too large")
    if h < 16 or abs(w - 2 * h) > max(2, 0.01 * w):
        raise ValueError(f"panorama must be equirectangular 2:1, got {w}x{h}")


def encode_jpeg(img: np.ndarray, quality: int = 90) -> bytes:
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("jpeg encode failed")
    return buf.tobytes()


def resize_width(img: np.ndarray, w: int) -> np.ndarray:
    h0, w0 = img.shape[:2]
    if w >= w0:
        return img
    return cv2.resize(img, (w, max(1, round(h0 * w / w0))), interpolation=cv2.INTER_AREA)


def blur_score(img: np.ndarray) -> float:
    """Variance of the Laplacian on a 1024 px wide grayscale copy (low = blurry)."""
    g = cv2.cvtColor(resize_width(img, 1024), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


def extract_keyframes(data: bytes, blur_min: float, max_frames: int, min_interval: float = 1.0,
                      change_thresh: float = 0.25, probe_interval: float = 0.25):
    """In-memory variant of extract_keyframes_file (small clips)."""
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(data)
        path = tmp.name
    try:
        yield from extract_keyframes_file(path, blur_min, max_frames, min_interval, change_thresh, probe_interval)
    finally:
        os.unlink(path)


def extract_keyframes_file(path: str, blur_min: float, max_frames: int, min_interval: float = 1.0,
                           change_thresh: float = 0.25, probe_interval: float = 0.25):
    """Yield (t_sec, bgr) keyframes: one per ~min_interval s, or earlier on a large visual change; blurry frames dropped."""
    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            raise ValueError("not a decodable video")
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, int(round(fps * probe_interval)))
        n_kept = 0
        last_t, last_small, idx = -1e9, None, 0
        while True:
            if not cap.grab():
                break
            if idx % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    break
                t = idx / fps
                small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 32)).astype(np.float32)
                change = 1.0 if last_small is None else float(np.abs(small - last_small).mean() / 255.0)
                if t - last_t >= min_interval or (change > change_thresh and t - last_t >= probe_interval):
                    if blur_score(frame) >= blur_min:
                        check_equirect(frame)
                        n_kept += 1
                        if n_kept > max_frames:
                            raise ValueError(f"video yields more than {max_frames} keyframes")
                        last_t, last_small = t, small
                        yield t, frame
            idx += 1
    finally:
        cap.release()
