from __future__ import annotations

import os
import uuid

import numpy as np
import psycopg
import pytest
from fastapi.testclient import TestClient

from fauxel_api.app import create_app
from fauxel_api.config import Settings
from fauxel_api.migrate import apply_migrations

ADMIN_URL = os.environ.get("FAUXEL_TEST_ADMIN_URL", "postgresql://fauxel@localhost:55432/postgres")
DB_NAME = "fauxel_pytest"


def pytest_configure(config):
    config.addinivalue_line("markers", "engines: needs vision/analytics/qc engine modules")


def _engines_available() -> bool:
    try:
        import fauxel_api.analytics.evm, fauxel_api.analytics.schedule  # noqa: F401
        import fauxel_api.qc.bcf, fauxel_api.qc.deviation  # noqa: F401
        import fauxel_api.vision.change_detect, fauxel_api.vision.fusion, fauxel_api.vision.projection  # noqa: F401
        return True
    except ImportError:
        return False


def pytest_collection_modifyitems(config, items):
    if not _engines_available():
        skip = pytest.mark.skip(reason="engine modules not importable")
        for it in items:
            if "engines" in it.keywords:
                it.add_marker(skip)


@pytest.fixture(scope="session")
def db_url():
    try:
        with psycopg.connect(ADMIN_URL, autocommit=True) as c:
            c.execute(f"DROP DATABASE IF EXISTS {DB_NAME} WITH (FORCE)")
            c.execute(f"CREATE DATABASE {DB_NAME}")
    except psycopg.OperationalError as exc:
        pytest.skip(f"no test Postgres at {ADMIN_URL}: {exc}")
    url = ADMIN_URL.rsplit("/", 1)[0] + "/" + DB_NAME
    apply_migrations(url, Settings().migrations_dir)
    return url


def make_settings(db_url, tmp_path, **kw) -> Settings:
    base = dict(database_url=db_url, storage="fs", fs_root=str(tmp_path / "store"), auto_migrate=False, auth="off", key_salt="test-salt")
    return Settings(**{**base, **kw})


@pytest.fixture
def make_client(db_url, tmp_path):
    clients = []

    def _make(**kw):
        c = TestClient(create_app(make_settings(db_url, tmp_path, **kw)))
        c.__enter__()
        clients.append(c)
        return c

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


@pytest.fixture
def client(make_client):
    return make_client()


@pytest.fixture
def pid(client):
    return client.post("/api/v1/projects", json={"name": f"P-{uuid.uuid4().hex[:6]}"}).json()["id"]
