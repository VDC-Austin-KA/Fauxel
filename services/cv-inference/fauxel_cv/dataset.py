"""Dataset layout + builder.

Layout (ImageFolder-style, one tree per trade):
    <root>/<trade>/<split>/p<percent:03d>/<id>.jpg      split in {train,val}; folder = ladder rung percent
    <root>/<trade>/samples.jsonl                        one row per image: path, percent, state, capture_id, element_uid, source ...
The val split is by capture (never by frame or element), so views of one walk never straddle train and val.

Build from a running API:
    python -m fauxel_cv.dataset --api http://localhost:8000 --key $FAUXEL_KEY --project <pid> --out data/
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

from .ladders import ladder_for


def rung_dir(percent: float) -> str:
    return f"p{int(round(percent)):03d}"


def assign_splits(capture_ids, val_frac: float = 0.2) -> dict:
    """capture_id -> 'train'|'val'. Deterministic (hash order); with >= 2 captures at least one goes to val."""
    ids = sorted({str(c) for c in capture_ids}, key=lambda c: hashlib.sha1(c.encode()).hexdigest())
    n_val = 0 if len(ids) < 2 else max(1, math.floor(len(ids) * val_frac + 0.5))
    return {c: ("val" if i < n_val else "train") for i, c in enumerate(ids)}


def snap_rung(trade: str, percent: float):
    """Nearest ladder rung (human percents are free-form; the classifier needs a rung)."""
    return min(ladder_for(trade), key=lambda r: abs(r["percent"] - percent))


class Api:
    def __init__(self, base: str, key: str | None):
        self.base = base.rstrip("/") + "/api/v1"
        self.s = requests.Session()
        if key:
            self.s.headers["X-Fauxel-Key"] = key

    def samples(self, pid: str, since: str | None, source: str = "human", limit: int = 20000) -> list[dict]:
        r = self.s.get(f"{self.base}/projects/{pid}/training-samples", params={"since": since, "source": source, "limit": limit}, timeout=60)
        r.raise_for_status()
        return r.json()

    def crop(self, frame_id: str, element_uid: str, size: int) -> bytes | None:
        r = self.s.get(f"{self.base}/training/crop", params={"frame_id": frame_id, "element_uid": element_uid, "size": size}, timeout=120)
        if r.status_code in (404, 409, 422):  # element not visible / no pose: skip the sample
            return None
        r.raise_for_status()
        return r.content


def build(api: Api, project: str, out: Path, since: str | None = None, size: int = 384, val_frac: float = 0.2,
          source: str = "human", workers: int = 8) -> dict:
    rows = api.samples(project, since, source)
    by_trade = defaultdict(list)
    for r in rows:
        by_trade[r["trade"]].append(r)
    stats = {}
    for trade, rs in by_trade.items():
        split = assign_splits([r["capture_id"] for r in rs], val_frac)
        tdir = out / trade
        jobs = []
        for r in rs:
            rung = snap_rung(trade, float(r["percent"]))
            rel = f"{split[str(r['capture_id'])]}/{rung_dir(rung['percent'])}/{r['observation_id']}.jpg"
            jobs.append((r, rung, rel))

        def fetch(job):
            r, rung, rel = job
            p = tdir / rel
            if not p.exists():
                data = api.crop(str(r["frame_id"]), str(r["element_uid"]), size)
                if data is None:
                    return None
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(data)
            return {"path": rel, "percent": rung["percent"], "state": rung["state"], "capture_id": str(r["capture_id"]),
                    "frame_id": str(r["frame_id"]), "element_uid": str(r["element_uid"]), "source": source,
                    "observation_id": str(r["observation_id"])}

        with ThreadPoolExecutor(workers) as ex:
            got = [g for g in ex.map(fetch, jobs) if g]
        tdir.mkdir(parents=True, exist_ok=True)
        with open(tdir / "samples.jsonl", "w") as fh:
            for g in got:
                fh.write(json.dumps(g) + "\n")
        (tdir / "ladder.json").write_text(json.dumps(ladder_for(trade), indent=1))
        stats[trade] = {"requested": len(rs), "written": len(got), "skipped": len(rs) - len(got),
                        "captures": len(split), "val": sum(1 for g in got if split[g["capture_id"]] == "val")}
    return stats


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", default=os.environ.get("FAUXEL_API", "http://localhost:8000"))
    ap.add_argument("--key", default=os.environ.get("FAUXEL_KEY"))
    ap.add_argument("--project", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--since")
    ap.add_argument("--source", default="human", choices=["human", "cv", "import"])
    ap.add_argument("--size", type=int, default=384)
    ap.add_argument("--val-frac", type=float, default=0.2)
    a = ap.parse_args(argv)
    stats = build(Api(a.api, a.key), a.project, a.out, a.since, a.size, a.val_frac, a.source)
    json.dump(stats, sys.stdout, indent=1)
    print()


if __name__ == "__main__":
    main()
