"""numpy/cv2 copy of the tangent-plane crop in fauxel_api.vision.model_slot (same maths, no API/fauxel_capture import).
Used by the synthetic worker post-step so synthetic crops have the same geometry as API crops; tests/test_crops_parity.py
asserts the two give identical pixels."""
from __future__ import annotations

import cv2
import numpy as np

FOV_MIN, FOV_MAX, FOV_MARGIN = 40.0, 110.0, 1.25


def unit_dirs(us, vs, W, H):
    theta = ((us + 0.5) / W - 0.5) * 2 * np.pi
    phi = ((vs + 0.5) / H - 0.5) * np.pi
    return np.stack([np.cos(phi) * np.sin(theta), np.sin(phi), np.cos(phi) * np.cos(theta)], 1)


def dirs_from_mask(mask, max_pts=4000):
    H, W = mask.shape
    vs, us = np.nonzero(mask)
    if len(us) > max_pts:
        sel = np.random.default_rng(0).choice(len(us), max_pts, replace=False)
        us, vs = us[sel], vs[sel]
    return unit_dirs(us.astype(float), vs.astype(float), W, H)


def view_for_dirs(dirs):
    c = dirs.mean(0)
    n = np.linalg.norm(c)
    c = c / n if n > 1e-9 else np.array([0.0, 0.0, 1.0])
    r = float(np.arccos(np.clip(dirs @ c, -1, 1)).max())
    fov = np.degrees(2 * np.arctan(FOV_MARGIN * np.tan(min(r, np.radians(80)))))
    return c, float(np.clip(fov, FOV_MIN, FOV_MAX))


def tangent_crop(image_bgr, center, fov_deg, size=384):
    H, W = image_bgr.shape[:2]
    f = (size / 2.0) / np.tan(np.deg2rad(fov_deg) / 2.0)
    K = np.array([[f, 0, size / 2.0], [0, f, size / 2.0], [0, 0, 1.0]])
    u, v = np.meshgrid(np.arange(size, dtype=np.float64), np.arange(size, dtype=np.float64))
    rays = np.linalg.inv(K) @ np.stack([u.ravel(), v.ravel(), np.ones(size * size)])
    rays /= np.linalg.norm(rays, axis=0, keepdims=True)
    x, y, z = [float(t) for t in center]
    a, b = np.arctan2(x, z), -np.arcsin(np.clip(y, -1, 1))
    Ry = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]])
    Rx = np.array([[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]])
    rays = (Ry @ Rx) @ rays
    theta, phi = np.arctan2(rays[0], rays[2]), np.arcsin(np.clip(rays[1], -1, 1))
    mu = (W * (theta / (2 * np.pi) + 0.5) - 0.5).reshape(size, size).astype(np.float32)
    mv = (H * (phi / np.pi + 0.5) - 0.5).reshape(size, size).astype(np.float32)
    return cv2.remap(image_bgr, mu, mv, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_WRAP)


def crop_for_mask(image_bgr, mask, size=384):
    if mask is None or not np.any(mask):
        return None
    c, fov = view_for_dirs(dirs_from_mask(np.asarray(mask, bool)))
    return tangent_crop(image_bgr, c, fov, size)
