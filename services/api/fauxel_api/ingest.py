"""Capture ingestion shared by the request path (small stills) and the worker (video, raw .insv/.insp)."""
from __future__ import annotations

import os
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from . import media

VIDEO_EXT = (".mp4", ".mov", ".m4v")
RAW_EXT = (".insv", ".insp")
IMAGE_EXT = (".jpg", ".jpeg", ".png")


def store_frame(conn, storage, pid, cid, seq, img, t_sec, taken_at) -> None:
    fid = uuid.uuid4()
    base = f"frames/{pid}/{cid}/{fid}"
    storage.put(f"{base}.jpg", media.encode_jpeg(img, 92), "image/jpeg")
    storage.put(f"{base}_preview.jpg", media.encode_jpeg(media.resize_width(img, media.PREVIEW_W), 88), "image/jpeg")
    h, w = img.shape[:2]
    conn.execute(
        "INSERT INTO panorama_frames (id, capture_id, seq, image_key, preview_key, width, height, blur_score, t_sec, taken_at) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (fid, cid, seq, f"{base}.jpg", f"{base}_preview.jpg", w, h, media.blur_score(img), t_sec, taken_at))




# Indirection points so tests can substitute the stitcher (the real one lives in services/capture-ingestion).
def stitch_file(path: str, out_dir: str, every_s: float = 1.0, width: int = 5760) -> list[str]:
    try:
        from fauxel_capture.insta360 import stitch_file as f
    except ImportError as exc:
        raise ValueError(f"raw Insta360 stitching is not installed (pip install -e services/capture-ingestion): {exc}")
    return [str(p) for p in f(path, out_dir, every_s=every_s, width=width)]


def read_telemetry(path: str):
    try:
        from fauxel_capture.insta360 import read_telemetry as f
    except ImportError:
        return None
    try:
        return f(path)
    except Exception:
        return None


def compact_telemetry(tel, max_list: int = 50):
    """Keep scalars and short lists; large sample arrays are replaced by their length."""
    if not isinstance(tel, dict):
        return None
    out = {}
    for k, v in tel.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (list, tuple)):
            out[k] = list(v) if len(v) <= max_list and all(isinstance(i, (int, float, str)) for i in v) else {"samples": len(v)}
        elif isinstance(v, dict):
            out[k] = compact_telemetry(v, max_list)
        else:
            out[k] = str(v)[:200]
    return out


def ingest_path(conn, storage, settings, pid, cap, name: str, path: str, seq: int, telemetry_out: list | None = None) -> int:
    """Ingest one file from disk; returns the next frame seq. Raises ValueError on unusable input."""
    ext = Path(name).suffix.lower()
    budget = settings.max_frames_per_upload - seq
    if budget <= 0:
        raise ValueError(f"more than {settings.max_frames_per_upload} frames in one capture")
    if ext in RAW_EXT:
        with tempfile.TemporaryDirectory() as out_dir:
            jpgs = stitch_file(path, out_dir, every_s=1.0, width=5760)
            if len(jpgs) > budget:
                raise ValueError(f"{name} yields {len(jpgs)} frames, limit is {settings.max_frames_per_upload}")
            if telemetry_out is not None:
                telemetry_out.append(read_telemetry(path))
            for i, jp in enumerate(sorted(jpgs)):
                img = media.decode_image(Path(jp).read_bytes())
                media.check_equirect(img)
                store_frame(conn, storage, pid, cap["id"], seq, img, float(i), cap["captured_at"] + timedelta(seconds=i))
                seq += 1
    elif ext in VIDEO_EXT:
        for t, frame in media.extract_keyframes_file(path, settings.blur_min, budget):
            store_frame(conn, storage, pid, cap["id"], seq, frame, t, cap["captured_at"] + timedelta(seconds=t))
            seq += 1
    else:
        img = media.decode_image(Path(path).read_bytes())
        media.check_equirect(img)
        store_frame(conn, storage, pid, cap["id"], seq, img, float(seq), cap["captured_at"])
        seq += 1
    return seq


def ingest_bytes(conn, storage, settings, pid, cap, files: list[tuple[str, bytes]]) -> int:
    """Request-path ingest of small in-memory stills / short clips. Returns the frame count."""
    seq = 0
    for name, data in files:
        ext = Path(name).suffix.lower()
        try:
            if ext in VIDEO_EXT:
                for t, frame in media.extract_keyframes(data, settings.blur_min, settings.max_frames_per_upload):
                    store_frame(conn, storage, pid, cap["id"], seq, frame, t, cap["captured_at"] + timedelta(seconds=t))
                    seq += 1
            else:
                img = media.decode_image(data)
                media.check_equirect(img)
                store_frame(conn, storage, pid, cap["id"], seq, img, float(seq), cap["captured_at"])
                seq += 1
        except ValueError as exc:
            raise ValueError(f"{name}: {exc}")
    return seq


def download_tmp(storage, key: str, directory: str, name: str) -> str:
    dest = os.path.join(directory, "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[-100:] or "file")
    storage.download(key, dest)
    return dest
