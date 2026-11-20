"""Migrations, projects, auth, events/relay."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

import psycopg
import pytest
from jsonschema import Draft202012Validator
from psycopg.rows import dict_row

from fauxel_api.config import Settings
from fauxel_api.migrate import apply_migrations
from fauxel_api.relay import publish_pending

API = "/api/v1"
CONTRACTS = Path(Settings().contracts_dir)


def test_migrations(db_url):
    assert apply_migrations(db_url, Settings().migrations_dir) == []  # already applied by the fixture, idempotent
    with psycopg.connect(db_url, row_factory=dict_row) as c:
        tables = {r["tablename"] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")}
        for t in ("projects buildings levels zones bim_models bim_elements element_status element_observations progress_rules "
                  "capture_sessions trajectory_nodes panorama_frames detections schedule_activities activity_elements "
                  "evm_snapshots deviations api_keys outbox_events schema_migrations").split():
            assert t in tables, t
        assert c.execute("SELECT count(*) AS n FROM progress_rules WHERE version = 1").fetchone()["n"] == 7
        assert c.execute("SELECT count(*) AS n FROM pg_extension WHERE extname IN ('postgis','vector')").fetchone()["n"] == 2
        assert c.execute("SELECT filename FROM schema_migrations").fetchone()["filename"] == "001_init.sql"


def test_health_and_projects(client):
    h = client.get(f"{API}/health").json()
    assert h["status"] == "ok" and h["db"] and h["storage"] and isinstance(h["gpu"], bool)
    p = client.post(f"{API}/projects", json={"name": "Tower", "description": "d"})
    assert p.status_code == 201
    pid = p.json()["id"]
    assert client.get(f"{API}/projects/{pid}").json()["name"] == "Tower"
    assert any(x["id"] == pid for x in client.get(f"{API}/projects").json())
    assert client.get(f"{API}/projects/{uuid.uuid4()}").status_code == 404
    assert client.get(f"{API}/projects/not-a-uuid").status_code == 422
    assert client.post(f"{API}/projects", json={"name": ""}).status_code == 422
    assert client.get(f"{API}/projects/{pid}/lbs").json() == {"buildings": []}


def test_auth_on_off(make_client):
    off = make_client(auth="off")
    assert off.get(f"{API}/projects").status_code == 200

    on = make_client(auth="on")
    assert on.get(f"{API}/health").status_code == 200          # health is open
    assert on.get(f"{API}/projects").status_code == 401
    assert on.get(f"{API}/projects", headers={"X-Fauxel-Key": "bogus"}).status_code == 401
    # key creation: refused when it arrives through a proxy, allowed for a direct local caller
    assert on.post(f"{API}/api-keys", json={"name": "x"}, headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    r = on.post(f"{API}/api-keys", json={"name": "revit plugin"})
    assert r.status_code == 201
    key = r.json()["key"]
    assert on.get(f"{API}/projects", headers={"X-Fauxel-Key": key}).status_code == 200
    assert on.get(f"{API}/projects", headers={"X-Fauxel-Key": key[:-2] + "zz"}).status_code == 401
    # only salt+hash stored, never the secret
    with psycopg.connect(off.app.state.settings.database_url, row_factory=dict_row) as c:
        h = c.execute("SELECT key_hash FROM api_keys WHERE id = %s", (r.json()["id"],)).fetchone()["key_hash"]
    assert key.split(".", 1)[1] not in h and "$" in h and len(h.split("$")[1]) == 64


def test_auth_access_header_and_admin_token(make_client):
    c = make_client(auth="on", trust_access_header=True, admin_token="s3cret")
    assert c.get(f"{API}/projects", headers={"Cf-Access-Authenticated-User-Email": "ka@example.com"}).status_code == 200
    assert c.post(f"{API}/api-keys", json={"name": "k"}, headers={"X-Forwarded-For": "9.9.9.9", "X-Fauxel-Admin": "s3cret"}).status_code == 201
    no_trust = make_client(auth="on")
    assert no_trust.get(f"{API}/projects", headers={"Cf-Access-Authenticated-User-Email": "ka@example.com"}).status_code == 401



def test_public_read_window(make_client):
    from datetime import datetime, timedelta, timezone
    soon = datetime.now(timezone.utc) + timedelta(hours=2)
    c = make_client(auth="on", trust_access_header=True, public_read_until=soon)
    assert c.get(f"{API}/health").json()["public_read_until"] == soon.isoformat()
    assert c.get(f"{API}/projects").status_code == 200                          # keyless read works
    r = c.post(f"{API}/projects", json={"name": "x"})
    assert r.status_code == 401 and "read-only" in r.json()["detail"]           # writes still need a key
    assert c.get(f"{API}/integrations/aps/status").status_code == 401           # deny list
    assert c.get(f"{API}/uploads/{uuid.uuid4()}").status_code == 401
    # a forged Access identity buys nothing while the window is open
    assert c.post(f"{API}/projects", json={"name": "x"}, headers={"Cf-Access-Authenticated-User-Email": "a@b.c"}).status_code == 401
    past = make_client(auth="on", public_read_until=datetime.now(timezone.utc) - timedelta(seconds=1))
    assert past.get(f"{API}/projects").status_code == 401 and past.get(f"{API}/health").json()["public_read_until"] is None


def test_public_read_until_parsing():
    from fauxel_api.config import _aware
    assert _aware("2026-11-20T23:59:59-06:00").utcoffset().total_seconds() == -21600
    assert _aware("2026-11-20T23:59:59") is None and _aware("nope") is None and _aware("") is None

def test_event_schemas_and_relay(client, pid, db_url):
    # produce one event of each kind that does not need heavy setup
    client.post(f"{API}/projects/{pid}/writeback/ack", json={"host": "revit", "applied": 12, "model": "A.rvt"})
    assert client.post(f"{API}/projects/{pid}/deviations", json={"kind": "missing", "severity": "high", "note": "n"}).status_code == 201
    with psycopg.connect(db_url, row_factory=dict_row, autocommit=True) as c:
        rows = c.execute("SELECT topic, payload FROM outbox_events WHERE payload->>'project_id' = %s", (pid,)).fetchall()
        assert {r["topic"] for r in rows} == {"progress.synced", "deviation.detected"}
        for r in rows:
            schema = json.loads((CONTRACTS / "events" / f"{r['topic']}.schema.json").read_text())
            errs = list(Draft202012Validator(schema).iter_errors(r["payload"]))
            assert not errs, errs

        class FakeProducer:
            def __init__(self):
                self.sent = []

            def produce(self, topic, key, value, on_delivery):
                self.sent.append((topic, key, json.loads(value)))
                on_delivery(None, None)

            def flush(self, t):
                pass

        p = FakeProducer()
        assert publish_pending(c, p, batch=1000) >= 2
        assert {s[0] for s in p.sent} >= {"progress.synced", "deviation.detected"}
        assert c.execute("SELECT count(*) AS n FROM outbox_events WHERE published_at IS NULL AND payload->>'project_id' = %s", (pid,)).fetchone()["n"] == 0
        assert publish_pending(c, FakeProducer()) == 0  # nothing left to publish for this project (other tests' rows may exist)


def test_relay_without_kafka_leaves_rows(monkeypatch):
    from fauxel_api import relay
    monkeypatch.delenv("FAUXEL_KAFKA_BOOTSTRAP", raising=False)
    assert relay.main([]) == 1
