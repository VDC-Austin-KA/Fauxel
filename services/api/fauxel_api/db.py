"""Connection pool + request-scoped connection dependency."""
from __future__ import annotations

from typing import Iterator

import psycopg
from fastapi import Request
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool


def make_pool(database_url: str, max_size: int = 8) -> ConnectionPool:
    return ConnectionPool(
        database_url, min_size=1, max_size=max_size, open=False,
        kwargs={"row_factory": dict_row}, check=ConnectionPool.check_connection,
    )


def get_conn(request: Request) -> Iterator[psycopg.Connection]:
    """One transaction per request: commits on success, rolls back on error."""
    with request.app.state.pool.connection() as conn:
        yield conn
