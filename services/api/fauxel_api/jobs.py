"""Postgres-backed job queue (SELECT ... FOR UPDATE SKIP LOCKED). Workers: `python -m fauxel_api.worker`."""
from __future__ import annotations

import uuid

from psycopg.types.json import Jsonb

STALE_AFTER = "2 hours"
MAX_ATTEMPTS = 3


def enqueue(conn, kind: str, payload: dict, project_id=None) -> uuid.UUID:
    return conn.execute("INSERT INTO jobs (project_id, kind, payload) VALUES (%s, %s, %s) RETURNING id",
                        (project_id, kind, Jsonb(payload))).fetchone()["id"]


def claim(conn) -> dict | None:
    """Atomically take the oldest queued job (requeues jobs whose worker died). Caller commits."""
    conn.execute("UPDATE jobs SET status = 'queued' WHERE status = 'running' AND attempts < %s "
                 f"AND locked_at < now() - interval '{STALE_AFTER}'", (MAX_ATTEMPTS,))
    conn.execute("UPDATE jobs SET status = 'failed', error = 'worker died', finished_at = now() WHERE status = 'running' "
                 f"AND attempts >= %s AND locked_at < now() - interval '{STALE_AFTER}'", (MAX_ATTEMPTS,))
    return conn.execute(
        "UPDATE jobs SET status = 'running', attempts = attempts + 1, locked_at = now() WHERE id = ("
        "SELECT id FROM jobs WHERE status = 'queued' ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *").fetchone()


def finish(conn, job_id, result: dict | None = None, error: str | None = None) -> None:
    conn.execute("UPDATE jobs SET status = %s, result = %s, error = %s, finished_at = now() WHERE id = %s",
                 ("failed" if error else "done", Jsonb(result) if result is not None else None, error, job_id))
