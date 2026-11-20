#!/usr/bin/env python3
"""Fauxel SLAM worker: run stella_vslam (equirectangular) on a stitched ONE X2 video and push keyframe poses to the API.

    run.py --video walk.mp4 --capture-id <uuid> --api-url http://api:8000 [--api-key K]
    run.py --trajectory-file keyframe_trajectory.txt --capture-id <uuid> --api-url ...     # skip SLAM (already ran)
    run.py --video walk.mp4 --out ./slam-out --no-upload                                   # SLAM only, write nodes.json

Output convention (docs/specs/platform-contract.md, `PUT /captures/{cid}/trajectory`): T_slam_from_cam, OpenCV camera
axes (+X right, +Y down, +Z forward), Hamilton quaternion (qw,qx,qy,qz) scalar first. stella_vslam writes TUM lines
`t tx ty tz qx qy qz qw` with the camera-to-world pose, i.e. exactly T_slam_from_cam in the OpenCV camera frame, so only the
quaternion order changes. The SLAM frame is arbitrary (monocular: unknown scale); align it afterwards with
POST /captures/{cid}/align (AprilTags or control points).

Each keyframe is matched by video time to the capture's stored frames (`panorama_frames.t_sec`), which the API sets when it
extracts keyframes from the same video. Frames without a nearby SLAM keyframe simply keep no pose.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

BIN = os.environ.get("FAUXEL_SLAM_BIN", "/usr/local/bin/run_video_slam")
VOCAB = os.environ.get("FAUXEL_SLAM_VOCAB", "/opt/fauxel/orb_vocab.fbow")
CONFIG_DIR = Path(os.environ.get("FAUXEL_SLAM_CONFIG_DIR", "/opt/fauxel/config"))


def parse_tum(text: str) -> list[dict]:
    """TUM keyframe trajectory -> [{t, x, y, z, qw, qx, qy, qz}] (Hamilton, scalar first, unit norm)."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        p = line.split()
        if len(p) != 8:
            raise ValueError(f"line {n}: expected 8 fields (t x y z qx qy qz qw), got {len(p)}")
        t, x, y, z, qx, qy, qz, qw = map(float, p)
        norm = math.sqrt(qw * qw + qx * qx + qy * qy + qz * qz)
        if not all(map(math.isfinite, (t, x, y, z, norm))) or norm < 1e-9:
            raise ValueError(f"line {n}: non finite pose")
        out.append({"t": t, "x": x, "y": y, "z": z, "qw": qw / norm, "qx": qx / norm, "qy": qy / norm, "qz": qz / norm})
    return out


def match_frames(poses: list[dict], frames: list[dict], tol_s: float) -> tuple[list[dict], dict]:
    """Nearest frame (by t_sec) for every keyframe pose, each frame used once (closest pose wins)."""
    timed = [f for f in frames if f.get("t_sec") is not None]
    if not timed:
        raise ValueError("capture frames have no t_sec: SLAM needs video keyframes (stills have no time axis)")
    best: dict[str, tuple[float, dict]] = {}
    unmatched = 0
    for p in poses:
        f = min(timed, key=lambda f: abs(f["t_sec"] - p["t"]))
        d = abs(f["t_sec"] - p["t"])
        if d > tol_s:
            unmatched += 1
            continue
        if f["id"] not in best or d < best[f["id"]][0]:
            best[f["id"]] = (d, p)
    nodes = [{"frame_id": fid, "x": p["x"], "y": p["y"], "z": p["z"], "qw": p["qw"], "qx": p["qx"], "qy": p["qy"], "qz": p["qz"],
              "t_sec": p["t"]} for fid, (_, p) in sorted(best.items(), key=lambda kv: kv[1][1]["t"])]
    return nodes, {"keyframes": len(poses), "frames": len(timed), "matched": len(nodes), "unmatched_keyframes": unmatched}


class Api:
    def __init__(self, base: str, key: str | None, cf_id: str | None, cf_secret: str | None):
        b = base.rstrip("/")
        self.base = b if b.endswith("/api/v1") else b + "/api/v1"
        self.h = {}
        if key:
            self.h["X-Fauxel-Key"] = key
        if cf_id and cf_secret:
            self.h["CF-Access-Client-Id"], self.h["CF-Access-Client-Secret"] = cf_id, cf_secret

    def _check(self, r: requests.Response) -> None:
        if "cloudflareaccess.com" in r.url or any("cloudflareaccess.com" in h.url for h in r.history) or (
                "text/html" in r.headers.get("content-type", "") and "Cloudflare Access" in r.text[:4000]):
            raise RuntimeError("Cloudflare Access login page returned: set FAUXEL_CF_ACCESS_CLIENT_ID / FAUXEL_CF_ACCESS_CLIENT_SECRET "
                               "(Access service token for API clients, see infra/cloudflare/access-policy.md)")

    def frames(self, cid: str) -> list[dict]:
        r = requests.get(f"{self.base}/captures/{cid}/frames", headers=self.h, timeout=60)
        self._check(r)
        r.raise_for_status()
        return r.json()

    def put_trajectory(self, cid: str, nodes: list[dict]) -> dict:
        r = requests.put(f"{self.base}/captures/{cid}/trajectory", json={"nodes": nodes}, headers=self.h, timeout=300)
        self._check(r)
        r.raise_for_status()
        return r.json() if r.content else {}


def run_slam(video: str, workdir: Path, width: int, vocab: str, binary: str) -> Path:
    cfg = CONFIG_DIR / ("equirect_onex2_5760x2880.yaml" if width == 5760 else "equirect_onex2_1920x960.yaml")
    src = video
    if width not in (0, 5760):
        src = str(workdir / "slam_input.mp4")  # stella_vslam extracts ORB at full size: 1920x960 is plenty and ~9x cheaper
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", video, "-vf", f"scale={width}:{width // 2}", "-an",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", src], check=True)
    out = workdir / "eval"
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run([binary, "-v", vocab, "-m", src, "-c", str(cfg), "--viewer", "none", "--no-sleep", "--auto-term",
                    "--start-timestamp", "0", "--eval-log-dir", str(out)], check=True)
    kf = out / "keyframe_trajectory.txt"
    if not kf.exists():
        raise RuntimeError("stella_vslam produced no keyframe_trajectory.txt (tracking never initialised: check overlap, motion blur, texture)")
    return kf


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video")
    ap.add_argument("--trajectory-file", help="existing keyframe_trajectory.txt (TUM); skips SLAM")
    ap.add_argument("--width", type=int, default=1920, choices=(0, 1920, 5760), help="SLAM input width (0/5760 = native); default 1920")
    ap.add_argument("--capture-id")
    ap.add_argument("--api-url", default=os.environ.get("FAUXEL_API_URL", "http://api:8000"))
    ap.add_argument("--api-key", default=os.environ.get("FAUXEL_API_KEY"))
    ap.add_argument("--cf-client-id", default=os.environ.get("FAUXEL_CF_ACCESS_CLIENT_ID") or os.environ.get("CF_ACCESS_CLIENT_ID"))
    ap.add_argument("--cf-client-secret", default=os.environ.get("FAUXEL_CF_ACCESS_CLIENT_SECRET") or os.environ.get("CF_ACCESS_CLIENT_SECRET"))
    ap.add_argument("--tolerance", type=float, default=0.75, help="max |keyframe time - frame t_sec| in seconds")
    ap.add_argument("--out", help="directory for nodes.json and SLAM logs (default: temp)")
    ap.add_argument("--no-upload", action="store_true")
    a = ap.parse_args(argv)
    if not a.video and not a.trajectory_file:
        ap.error("--video or --trajectory-file required")
    if not a.no_upload and not a.capture_id:
        ap.error("--capture-id required unless --no-upload")

    work = Path(a.out) if a.out else Path(tempfile.mkdtemp(prefix="fauxel-slam-"))
    work.mkdir(parents=True, exist_ok=True)
    try:
        kf = Path(a.trajectory_file) if a.trajectory_file else run_slam(a.video, work, a.width, VOCAB, BIN)
        poses = parse_tum(kf.read_text())
        if a.no_upload:
            (work / "nodes.json").write_text(json.dumps(poses))
            print(f"{len(poses)} keyframe poses -> {work / 'nodes.json'}")
            return 0
        api = Api(a.api_url, a.api_key, a.cf_client_id, a.cf_client_secret)
        nodes, stats = match_frames(poses, api.frames(a.capture_id), a.tolerance)
        if not nodes:
            print(f"no keyframe matched a stored frame within {a.tolerance}s: {stats}", file=sys.stderr)
            return 2
        (work / "nodes.json").write_text(json.dumps(nodes))
        api.put_trajectory(a.capture_id, nodes)
        print(json.dumps({"capture_id": a.capture_id, **stats}))
        return 0
    finally:
        if not a.out:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
