"""004: a project-scoped Autodesk sign-in moves to account-wide, once, keeping the newest row."""
from pathlib import Path

import psycopg

from fauxel_api.config import Settings

SQL = (Path(Settings().migrations_dir) / "004_aps_signin_account_wide.sql").read_text(encoding="utf-8")


def _tok(c, pid, days_ago=0):
    return c.execute(
        "INSERT INTO integration_tokens (provider, kind, project_id, access_token_enc, updated_at) "
        "VALUES ('aps','three_legged',%s,'x', now() - make_interval(days => %s)) RETURNING id", (pid, days_ago)).fetchone()[0]


def test_aps_signin_moves_account_wide(db_url):
    with psycopg.connect(db_url) as c:
        c.execute("DELETE FROM integration_tokens")
        p1 = c.execute("INSERT INTO projects (name) VALUES ('m1') RETURNING id").fetchone()[0]
        p2 = c.execute("INSERT INTO projects (name) VALUES ('m2') RETURNING id").fetchone()[0]
        old, new = _tok(c, p1, 3), _tok(c, p2, 1)
        c.execute(SQL)
        scope = dict(c.execute("SELECT id, project_id FROM integration_tokens").fetchall())
        assert scope[new] is None and scope[old] == p1          # newest moved, the other untouched
        c.execute(SQL)                                         # idempotent: an account-wide row exists
        assert dict(c.execute("SELECT id, project_id FROM integration_tokens").fetchall()) == scope
        c.rollback()
