"""Job worker: capture ingestion (stills, video keyframes, raw .insv/.insp stitching), capture processing, IFC ingest.

    python -m fauxel_api.worker            # run forever
    python -m fauxel_api.worker --once     # drain the queue and exit
"""
from __future__ import annotations

import argparse
import logging
import os
import tempfile
import time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from . import bim, ingest, jobs
from .config import Settings
from .events import emit
from .storage import make_storage

log = logging.getLogger("fauxel.worker")


def _set_capture(conn, cid, status: str, error: str | None = None) -> None:
    conn.execute("UPDATE capture_sessions SET status = %s, error = %s WHERE id = %s", (status, error, cid))


def handle_capture_ingest(conn, storage, settings, job) -> dict:
    p = job["payload"]
    cid = p["capture_id"]
    cap = conn.execute("SELECT * FROM capture_sessions WHERE id = %s", (cid,)).fetchone()
    if not cap:
        raise ValueError("capture no longer exists")
    pid = cap["project_id"]
    _set_capture(conn, cid, "processing")
    conn.execute("DELETE FROM panorama_frames WHERE capture_id = %s", (cid,))  # idempotent on requeue
    conn.commit()
    seq, tel = 0, []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for item in p["items"]:
                path = ingest.download_tmp(storage, item["key"], tmp, item["name"])
                try:
                    seq = ingest.ingest_path(conn, storage, settings, pid, cap, item["name"], path, seq, tel)
                except ValueError as exc:
                    raise ValueError(f"{item['name']}: {exc}")
                finally:
                    os.unlink(path)
                conn.commit()  # frames of finished files become visible while the next file runs
        if seq == 0:
            raise ValueError("no usable frames (all frames blurry or no files)")
    except Exception as exc:
        conn.rollback()
        conn.execute("DELETE FROM panorama_frames WHERE capture_id = %s", (cid,))
        _set_capture(conn, cid, "failed", str(exc)[:1000])
        conn.commit()
        raise
    telemetry = next((ingest.compact_telemetry(t) for t in tel if t), None)
    conn.execute("UPDATE capture_sessions SET status = 'ready', error = NULL, telemetry = %s WHERE id = %s",
                 (Jsonb(telemetry) if telemetry else None, cid))
    emit(conn, "capture.uploaded", pid, capture_id=str(cid), level_id=str(cap["level_id"]) if cap["level_id"] else None,
         source=cap["source"], frame_count=seq)
    return {"capture_id": str(cid), "frame_count": seq}


def handle_capture_process(conn, storage, settings, job) -> dict:
    from .process import process_capture

    cid = job["payload"]["capture_id"]
    try:
        return process_capture(conn, storage, cid)
    except ImportError as exc:
        raise ValueError(f"vision engine not available: {exc}")


def handle_model_ingest(conn, storage, settings, job) -> dict:
    p = job["payload"]
    model = bim.ingest_ifc(conn, storage, job["project_id"], storage.get(p["key"]), p.get("filename") or "model.ifc")
    return {"model_id": str(model["id"]), "element_count": model["element_count"]}


HANDLERS = {"capture.ingest": handle_capture_ingest, "capture.process": handle_capture_process, "model.ingest": handle_model_ingest}


def run_one(conn, storage, settings) -> bool:
    """Claim and run a single job. Returns False when the queue is empty."""
    job = jobs.claim(conn)
    conn.commit()
    if not job:
        return False
    handler = HANDLERS.get(job["kind"])
    try:
        if not handler:
            raise ValueError(f"unknown job kind {job['kind']}")
        result = handler(conn, storage, settings, job)
        jobs.finish(conn, job["id"], result=result)
        conn.commit()
    except Exception as exc:  # job failures are recorded, never crash the worker
        conn.rollback()
        log.warning("job %s (%s) failed: %s", job["id"], job["kind"], exc)
        jobs.finish(conn, job["id"], error=str(exc)[:1000])
        conn.commit()
    return True


def run_once(settings: Settings | None = None, storage=None) -> int:
    """Drain the queue; returns the number of jobs run (used by tests)."""
    settings = settings or Settings.from_env()
    storage = storage or make_storage(settings)
    n = 0
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        while run_one(conn, storage, settings):
            n += 1
    return n


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--once", action="store_true", help="drain the queue, then exit")
    ap.add_argument("--poll", type=float, default=2.0, help="idle poll interval in seconds")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env()
    storage = make_storage(s)
    if a.once:
        print("jobs run:", run_once(s, storage))
        return 0
    while True:
        try:
            with psycopg.connect(s.database_url, row_factory=dict_row) as conn:
                while True:
                    if not run_one(conn, storage, s):
                        time.sleep(a.poll)
        except psycopg.OperationalError as exc:
            log.warning("database unavailable: %s", exc)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
