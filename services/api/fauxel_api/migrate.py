"""Tiny migration runner: applies db/migrations/*.sql in filename order, once each.

    python -m fauxel_api.migrate
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from .config import Settings

LOCK_ID = 7_261_001


def apply_migrations(database_url: str, migrations_dir: str) -> list[str]:
    files = sorted(Path(migrations_dir).glob("*.sql"))
    if not files:
        raise FileNotFoundError(f"no migrations in {migrations_dir}")
    applied: list[str] = []
    with psycopg.connect(database_url, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations "
                "(filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
            )
            done = {r[0] for r in conn.execute("SELECT filename FROM schema_migrations")}
            for f in files:
                if f.name in done:
                    continue
                with conn.transaction():
                    conn.execute(f.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migrations(filename) VALUES (%s)", (f.name,))
                applied.append(f.name)
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied


def main() -> int:
    s = Settings.from_env()
    applied = apply_migrations(s.database_url, s.migrations_dir)
    print("applied:", ", ".join(applied) if applied else "nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main())
