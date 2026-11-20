"""Procedural stand-in crops (no Blender): texture per ladder rung, used by tests and for pipeline smoke runs.
Writes the dataset layout of fauxel_cv.dataset. NOT a substitute for real or Blender-rendered data."""
from __future__ import annotations

import json
from pathlib import Path

import cv2
import numpy as np

from .dataset import assign_splits, rung_dir
from .ladders import ladder_for


def _img(rng, i: int, n: int, size: int = 192) -> np.ndarray:
    base = rng.integers(90, 200)
    img = np.full((size, size, 3), base, np.uint8)
    f = i / max(n - 1, 1)
    if f < 0.5:  # bare/studs: strong vertical bars, density grows with progress
        for x in range(0, size, max(8, int(48 - 60 * f))):
            img[:, x:x + 5] = rng.integers(20, 70)
    elif f < 0.85:  # board: flat with seams
        img[:] = (base, base + 5, base + 8)
        img[:, size // 2:size // 2 + 2] = base - 40
    else:  # painted: flat saturated colour
        img[:] = (rng.integers(120, 200), rng.integers(40, 120), rng.integers(40, 120))
    noise = rng.normal(0, 3 + 8 * (1 - f), img.shape)
    return np.clip(img + noise, 0, 255).astype(np.uint8)


def make_dataset(root: Path, trade: str, n: int = 200, captures: int = 5, seed: int = 0, size: int = 192) -> Path:
    rng = np.random.default_rng(seed)
    ladder = ladder_for(trade)
    caps = [f"cap{c}" for c in range(captures)]
    split = assign_splits(caps, 0.2)
    rows = []
    for j in range(n):
        i = j % len(ladder)
        cap = caps[rng.integers(captures)]
        rel = f"{split[cap]}/{rung_dir(ladder[i]['percent'])}/syn{j:05d}.jpg"
        p = root / trade / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(p), _img(rng, i, len(ladder), size))
        rows.append({"path": rel, "percent": ladder[i]["percent"], "state": ladder[i]["state"], "capture_id": cap, "source": "procedural"})
    (root / trade / "samples.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    (root / trade / "ladder.json").write_text(json.dumps(ladder, indent=1))
    return root / trade
