"""Orchestrator: IFC -> prestep (ifcopenshell) -> Blender render -> crop dataset.
    python3 run.py --ifc model.ifc --out /out [--captures 6 --frames 8 --width 1024 --height 512 --samples 16 --seed 0]
Output: <out>/render/<capture>/ (rgb, id, depth EXR, capture.json) and <out>/dataset/<trade>/<split>/p###/*.jpg (+ samples.jsonl)."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ifc", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--captures", type=int, default=6)
    ap.add_argument("--frames", type=int, default=8)
    ap.add_argument("--width", type=int, default=1024)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--samples", type=int, default=16)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--crop-size", type=int, default=384)
    ap.add_argument("--blender", default=os.environ.get("BLENDER", "blender"))
    a = ap.parse_args(argv)
    work, render = a.out / "work", a.out / "render"
    subprocess.run([sys.executable, str(HERE / "prestep.py"), a.ifc, str(work)], check=True)
    subprocess.run([a.blender, "-b", "--factory-startup", "-P", str(HERE / "render_blender.py"), "--", "--work", str(work), "--out", str(render),
                    "--captures", str(a.captures), "--frames", str(a.frames), "--width", str(a.width), "--height", str(a.height),
                    "--samples", str(a.samples), "--seed", str(a.seed)], check=True, stdout=subprocess.DEVNULL if os.environ.get("QUIET") else None)
    import postprocess
    postprocess.run(render, a.out / "dataset", a.crop_size)


if __name__ == "__main__":
    main()
