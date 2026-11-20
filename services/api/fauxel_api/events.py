"""Transactional outbox: events are inserted in the caller's transaction; relay.py publishes them."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

TOPICS = ("capture.uploaded", "trajectory.aligned", "inference.completed", "deviation.detected", "progress.synced")


def emit(conn, topic: str, project_id, **payload) -> dict:
    if topic not in TOPICS:
        raise ValueError(f"unknown topic {topic}")
    ev = {
        "event_id": str(uuid.uuid4()),
        "occurred_at": datetime.now(timezone.utc).isoformat(),
        "project_id": str(project_id),
        **payload,
    }
    conn.execute(
        "INSERT INTO outbox_events (id, topic, payload) VALUES (%s, %s, %s)",
        (ev["event_id"], topic, Jsonb(ev)),
    )
    return ev
