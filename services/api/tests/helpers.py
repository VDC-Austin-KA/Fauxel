"""Test helpers."""
import numpy as np


def equirect(seed: int = 0, w: int = 512) -> bytes:
    """Synthetic textured 2:1 panorama as JPEG bytes."""
    import cv2
    rng = np.random.default_rng(seed)
    img = rng.integers(0, 255, (w // 2, w, 3), dtype=np.uint8)
    img = cv2.GaussianBlur(img, (0, 0), 1.2)
    cv2.rectangle(img, (40, 40), (200, 180), (30, 200, 240), -1)
    ok, buf = cv2.imencode(".jpg", img)
    return buf.tobytes()
