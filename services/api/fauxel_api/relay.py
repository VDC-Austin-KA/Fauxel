"""Outbox relay: publishes unpublished outbox_events to Kafka/Redpanda.

    FAUXEL_KAFKA_BOOTSTRAP=localhost:19092 python -m fauxel_api.relay [--once]

Without FAUXEL_KAFKA_BOOTSTRAP rows simply stay in outbox_events.
"""
from __future__ import annotations

import json
import sys
import time

import psycopg
from psycopg.rows import dict_row

from .config import Settings


def make_producer(bootstrap: str):
    from confluent_kafka import Producer

    return Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True})


def publish_pending(conn: psycopg.Connection, producer, batch: int = 200) -> int:
    """Publish up to `batch` rows; rows are marked published only after the broker acks."""
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, topic, payload FROM outbox_events WHERE published_at IS NULL "
            "ORDER BY created_at LIMIT %s FOR UPDATE SKIP LOCKED", (batch,),
        ).fetchall()
        acked: list = []
        errors: list = []
        for r in rows:
            def cb(err, _msg, rid=r["id"]):
                (errors if err else acked).append(err or rid)
            producer.produce(r["topic"], key=str(r["payload"].get("project_id", "")),
                             value=json.dumps(r["payload"]).encode(), on_delivery=cb)
        producer.flush(10)
        if acked:
            conn.execute("UPDATE outbox_events SET published_at = now() WHERE id = ANY(%s)", (acked,))
        if errors:
            print(f"relay: {len(errors)} delivery errors, will retry", file=sys.stderr)
    return len(acked)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    s = Settings.from_env()
    if not s.kafka_bootstrap:
        print("FAUXEL_KAFKA_BOOTSTRAP not set: events stay in outbox_events", file=sys.stderr)
        return 1
    producer = make_producer(s.kafka_bootstrap)
    with psycopg.connect(s.database_url, row_factory=dict_row, autocommit=True) as conn:
        while True:
            n = publish_pending(conn, producer)
            if "--once" in argv:
                print(f"published {n}")
                return 0
            if n == 0:
                time.sleep(1.0)


if __name__ == "__main__":
    sys.exit(main())
