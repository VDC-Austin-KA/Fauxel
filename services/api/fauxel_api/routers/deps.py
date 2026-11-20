"""Shared router helpers."""
from __future__ import annotations

from fastapi import HTTPException, Request


def get_project(conn, pid) -> dict:
    row = conn.execute("SELECT * FROM projects WHERE id = %s", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "project not found")
    return row


def get_capture(conn, cid) -> dict:
    row = conn.execute("SELECT * FROM capture_sessions WHERE id = %s", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "capture not found")
    return row


async def read_upload(upload, limit_mb: int) -> bytes:
    limit = limit_mb * 1024 * 1024
    chunks, size = [], 0
    while chunk := await upload.read(1024 * 1024):
        size += len(chunk)
        if size > limit:
            raise HTTPException(413, f"upload larger than {limit_mb} MB")
        chunks.append(chunk)
    return b"".join(chunks)


def storage(request: Request):
    return request.app.state.storage
