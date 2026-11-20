"""Rendered frames -> per-element crop dataset in the fauxel_cv layout (what `python -m fauxel_cv.train` reads).
For every labelled element visible in a frame's id image (>= MIN_PIXELS) it cuts the same tangent-plane crop the API's
GET /training/crop returns (fauxel_cv.crops == fauxel_api.vision.model_slot.crop_for_mask) and files it under
<out>/<trade>/<split>/p<percent>/.  The split is by capture (assign_splits)."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

import cv2  # noqa: E402
import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cv-inference"))
from fauxel_cv import crops  # noqa: E402
from fauxel_cv.dataset import assign_splits, rung_dir  # noqa: E402
from fauxel_cv.ladders import ladder_for  # noqa: E402

MIN_PIXELS = 400


def decode_ids(id_png: np.ndarray) -> np.ndarray:
    return (id_png[..., 2].astype(np.int64) << 16) | (id_png[..., 1].astype(np.int64) << 8) | id_png[..., 0]


def run(render_dir: Path, out: Path, size: int = 384, val_frac: float = 0.2, min_pixels: int = MIN_PIXELS) -> dict:
    caps = {p.parent.name: json.loads(p.read_text()) for p in sorted(render_dir.glob("*/capture.json"))}
    split = assign_splits(list(caps), val_frac)
    rows = defaultdict(list)
    for cid, cap in caps.items():
        for fr in cap["frames"]:
            rgb = cv2.imread(str(render_dir / cid / fr["rgb"]), cv2.IMREAD_COLOR)
            ids = decode_ids(cv2.imread(str(render_dir / cid / fr["id"]), cv2.IMREAD_COLOR))
            dpath = next((render_dir / cid).glob(f"f{fr['index']:04d}_depth_*.png"), None)
            depth = cv2.imread(str(dpath), cv2.IMREAD_UNCHANGED) if dpath else None
            if depth is not None:  # uint16 millimetres, 65535 = no hit
                depth = (depth[..., 0] if depth.ndim == 3 else depth).astype(np.float32) / 1000.0
            for el in cap["elements"].values():
                mask = ids == el["id"]
                n = int(mask.sum())
                if n < min_pixels:
                    continue
                crop = crops.crop_for_mask(rgb, mask, size)
                rel = f"{split[cid]}/{rung_dir(el['percent'])}/{cid}_f{fr['index']:04d}_{el['guid']}.jpg"
                p = out / el["trade"] / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                cv2.imwrite(str(p), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
                rows[el["trade"]].append({
                    "path": rel, "percent": el["percent"], "state": el["state"], "capture_id": cid, "frame_id": f"{cid}/f{fr['index']:04d}",
                    "element_uid": el["guid"], "ifc_class": el["ifc_class"], "source": "synthetic", "pixels": n,
                    "depth_median_m": float(np.median(depth[mask])) if depth is not None and depth.shape == mask.shape else None})
    for trade, rs in rows.items():
        (out / trade).mkdir(parents=True, exist_ok=True)
        (out / trade / "samples.jsonl").write_text("\n".join(json.dumps(r) for r in rs) + "\n")
        (out / trade / "ladder.json").write_text(json.dumps(ladder_for(trade), indent=1))
    stats = {t: {"images": len(r), "by_percent": {str(k): sum(1 for x in r if x["percent"] == k) for k in sorted({x["percent"] for x in r})}}
             for t, r in rows.items()}
    print(json.dumps(stats, indent=1))
    return stats


if __name__ == "__main__":
    run(Path(sys.argv[1]), Path(sys.argv[2]))
