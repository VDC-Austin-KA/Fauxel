"""Fine-tune a per-trade ordinal state classifier, calibrate it, export ONNX + manifest.

    python -m fauxel_cv.train --data data/ --trade arch_interior --out models/ [--backbone efficientnet_b0|mobilenet_v3_large]

Writes <out>/<trade>/{model.pt, model.onnx, manifest.json, metrics.json}; point FAUXEL_MODEL_DIR at <out>.
Defaults (efficientnet_b0, 224 px, batch 32, mixed precision on CUDA) use roughly 3-4 GB VRAM; lower --batch-size for less.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import torch
import torch.nn as nn
from torchvision import models
from torchvision.transforms import v2

from . import ordinal
from .ladders import ladder_for

MEAN, STD = [0.485, 0.456, 0.406], [0.229, 0.224, 0.225]  # ImageNet, what the pretrained weights expect
BACKBONES = {"efficientnet_b0": (models.efficientnet_b0, "IMAGENET1K_V1"),
             "mobilenet_v3_large": (models.mobilenet_v3_large, "IMAGENET1K_V2")}  # torchvision, BSD-3


# ---------------------------------------------------------------- data

class CropDataset(torch.utils.data.Dataset):
    """Reads <root>/<split>/p###/*.jpg. Preprocessing equals model_slot._preprocess: cv2 INTER_AREA resize, RGB, /255, mean/std."""

    def __init__(self, root: Path, split: str, ladder: list, size: int, augment: bool):
        idx = {int(round(r["percent"])): i for i, r in enumerate(ladder)}
        self.items, skipped = [], 0
        for d in sorted((root / split).glob("p*")) if (root / split).is_dir() else []:
            try:
                k = idx[int(d.name[1:])]
            except (ValueError, KeyError):
                skipped += len(list(d.glob("*.jpg")))
                continue
            self.items += [(p, k) for p in sorted(d.glob("*.jpg"))]
        if skipped:
            print(f"warning: {skipped} images in {split}/ have a percent that is not a rung of this ladder; ignored")
        self.size = size
        self.aug = v2.Compose([v2.RandomHorizontalFlip(), v2.RandomAffine(5, translate=(0.05, 0.05), scale=(0.9, 1.1)),
                               v2.ColorJitter(0.3, 0.3, 0.3, 0.03)]) if augment else None
        self.norm = v2.Normalize(MEAN, STD)

    def __len__(self):
        return len(self.items)

    @property
    def labels(self):
        return np.array([k for _, k in self.items], int)

    def __getitem__(self, i):
        p, k = self.items[i]
        img = cv2.imread(str(p), cv2.IMREAD_COLOR)
        if img is None:
            raise IOError(f"cannot read {p}")
        img = cv2.resize(img, (self.size, self.size), interpolation=cv2.INTER_AREA)
        x = torch.from_numpy(cv2.cvtColor(img, cv2.COLOR_BGR2RGB)).permute(2, 0, 1).float() / 255
        if self.aug:
            x = self.aug(x).clamp(0, 1)
        return self.norm(x), k


# ---------------------------------------------------------------- model

def build_model(backbone: str, n_out: int, pretrained: bool) -> nn.Module:
    ctor, w = BACKBONES[backbone]
    m = ctor(weights=w if pretrained else None)
    head = [i for i, l in enumerate(m.classifier) if isinstance(l, nn.Linear)][-1]
    m.classifier[head] = nn.Linear(m.classifier[head].in_features, n_out)
    return m


def predict_logits(model, loader, device) -> np.ndarray:
    model.eval()
    out = []
    with torch.no_grad():
        for x, _ in loader:
            out.append(model(x.to(device)).float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 0))


def class_weights(y: np.ndarray, k: int) -> torch.Tensor:
    cnt = np.bincount(y, minlength=k).astype(float)
    w = np.where(cnt > 0, len(y) / (k * np.maximum(cnt, 1)), 0.0)
    return torch.tensor(w / max(w[cnt > 0].mean(), 1e-9), dtype=torch.float32)


# ---------------------------------------------------------------- export

def export_onnx(model: nn.Module, path: Path, size: int) -> None:
    model = copy.deepcopy(model).cpu().eval()
    dummy = torch.randn(1, 3, size, size)
    torch.onnx.export(model, dummy, str(path), input_names=["image"], output_names=["logits"], opset_version=17,
                      dynamic_axes={"image": {0: "batch"}, "logits": {0: "batch"}}, dynamo=False)
    import onnxruntime as ort
    sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    ref = model(dummy).detach().numpy()
    got = sess.run(None, {"image": dummy.numpy()})[0]
    if not np.allclose(ref, got, atol=1e-3):
        raise RuntimeError(f"ONNX output differs from torch (max abs {np.abs(ref - got).max():.2e})")


# ---------------------------------------------------------------- train

def train(data: Path, trade: str, out: Path, backbone: str = "efficientnet_b0", size: int = 224, epochs: int = 20,
          batch_size: int = 32, lr: float = 1e-3, patience: int = 4, pretrained: bool = True, num_workers: int | None = None,
          device: str | None = None, version: str | None = None, seed: int = 0) -> dict:
    torch.manual_seed(seed)
    np.random.seed(seed)
    droot = data / trade
    lp = droot / "ladder.json"
    ladder = json.loads(lp.read_text()) if lp.exists() else ladder_for(trade)
    k = len(ladder)
    dev = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    nw = min(4, os.cpu_count() or 1) if num_workers is None else num_workers
    tr, va = CropDataset(droot, "train", ladder, size, True), CropDataset(droot, "val", ladder, size, False)
    if len(tr) == 0:
        raise SystemExit(f"no training images under {droot}/train")
    val_note = "capture split"
    if len(va) == 0:  # a single capture: nothing independent to validate on; hold out a random 15% and say so
        print("warning: no val split (need >= 2 captures). Holding out a random 15% of train; metrics are optimistic (frame leakage).")
        g = np.random.default_rng(seed)
        order = g.permutation(len(tr.items))
        n_val = max(1, int(0.15 * len(order)))
        va = CropDataset(droot, "train", ladder, size, False)
        va.items = [tr.items[i] for i in order[:n_val]]
        tr.items = [tr.items[i] for i in order[n_val:]]
        val_note = "random holdout (leaky)"
    y_tr = tr.labels
    cw = class_weights(y_tr, k).to(dev)
    mk = lambda ds, sh: torch.utils.data.DataLoader(ds, batch_size=batch_size, shuffle=sh, num_workers=nw, drop_last=False,
                                                    pin_memory=dev.type == "cuda")
    ltr, lva = mk(tr, True), mk(va, False)

    model = build_model(backbone, k - 1, pretrained).to(dev)
    opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    sched = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(epochs, 1))
    amp = dev.type == "cuda"
    scaler = torch.amp.GradScaler(enabled=amp)
    best, best_state, bad, hist = float("inf"), None, 0, []
    for ep in range(epochs):
        model.train()
        t0, tot, n = time.time(), 0.0, 0
        for x, y in ltr:
            x, y = x.to(dev, non_blocking=True), y.to(dev)
            with torch.autocast(device_type=dev.type, enabled=amp):
                lg = model(x)
            l = ordinal.loss(lg.float(), y, k, cw)
            opt.zero_grad(set_to_none=True)
            scaler.scale(l).backward()
            scaler.step(opt)
            scaler.update()
            tot, n = tot + l.item() * len(y), n + len(y)
        sched.step()
        lg = predict_logits(model, lva, dev)
        yv = va.labels
        vloss = float(ordinal.loss(torch.tensor(lg), torch.tensor(yv), k, class_weights(yv, k)).item())
        m = ordinal.metrics(ordinal.class_probs(lg), yv, ladder)
        hist.append({"epoch": ep + 1, "train_loss": tot / n, "val_loss": vloss, "val_acc": m["accuracy"], "val_macro_f1": m["macro_f1"],
                     "sec": round(time.time() - t0, 1)})
        print(f"epoch {ep + 1}/{epochs} train {tot / n:.4f} val {vloss:.4f} acc {m['accuracy']:.3f} f1 {m['macro_f1']:.3f} ({hist[-1]['sec']}s)")
        if vloss < best - 1e-4:
            best, bad, best_state = vloss, 0, copy.deepcopy(model.state_dict())
        else:
            bad += 1
            if bad >= patience:
                print("early stopping")
                break
    model.load_state_dict(best_state or model.state_dict())

    # calibration on the held-out split: temperature scaling of the cumulative logits
    lg, yv = predict_logits(model, lva, dev), va.labels
    T = ordinal.fit_temperature(lg, yv)
    report = {"trade": trade, "backbone": backbone, "input_size": size, "val_split": val_note, "n_train": len(tr), "n_val": len(va),
              "train_class_counts": np.bincount(y_tr, minlength=k).tolist(), "temperature": T, "history": hist,
              "val_uncalibrated": ordinal.metrics(ordinal.class_probs(lg, 1.0), yv, ladder),
              "val": ordinal.metrics(ordinal.class_probs(lg, T), yv, ladder)}
    tdir = out / trade
    tdir.mkdir(parents=True, exist_ok=True)
    torch.save(model.state_dict(), tdir / "model.pt")
    export_onnx(model, tdir / "model.onnx", size)
    ver = version or f"{trade}-{datetime.now(timezone.utc):%Y%m%d%H%M}"
    manifest = {"trade": trade, "version": ver, "ladder": ladder, "input_size": size, "mean": MEAN, "std": STD, "temperature": T,
                "onnx": "model.onnx", "backbone": backbone, "head": "ordinal_cumulative_logits", "input_layout": "NCHW_RGB_float32",
                "crop": {"source_size": 384, "fov_margin": 1.25, "fov_range_deg": [40, 110]},
                "metrics": {"accuracy": report["val"]["accuracy"], "macro_f1": report["val"]["macro_f1"], "ece": report["val"]["ece"],
                            "n_val": len(va)},
                "trained_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "license_note": "torchvision weights: BSD-3"}
    (tdir / "manifest.json").write_text(json.dumps(manifest, indent=1))
    (tdir / "metrics.json").write_text(json.dumps(report, indent=1))
    v = report["val"]
    print(f"{trade}: acc {v['accuracy']:.3f} macro-F1 {v['macro_f1']:.3f} ECE {v['ece']:.3f} (uncal {report['val_uncalibrated']['ece']:.3f}) "
          f"T={T:.2f}; onnx {(tdir / 'model.onnx').stat().st_size / 1e6:.1f} MB -> {tdir}")
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", required=True, type=Path)
    ap.add_argument("--trade", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--backbone", default="efficientnet_b0", choices=list(BACKBONES))
    ap.add_argument("--size", type=int, default=224)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=32)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--patience", type=int, default=4)
    ap.add_argument("--no-pretrained", action="store_true")
    ap.add_argument("--num-workers", type=int)
    ap.add_argument("--device")
    ap.add_argument("--version")
    a = ap.parse_args(argv)
    train(a.data, a.trade, a.out, a.backbone, a.size, a.epochs, a.batch_size, a.lr, a.patience, not a.no_pretrained,
          a.num_workers, a.device, a.version)


if __name__ == "__main__":
    main()
