"""Direct-to-storage multipart uploads: validation rules and key-ownership checks."""
from __future__ import annotations

import math
import re
import uuid
from pathlib import PurePosixPath

from fastapi import HTTPException

MB = 1024 * 1024
MAX_PARTS = 10_000

KIND_EXT = {
    "capture": {".jpg", ".jpeg", ".png", ".mp4", ".mov", ".m4v", ".insv", ".insp"},
    "model": {".ifc"},
    "schedule": {".xer", ".xml"},
    "evidence": {".jpg", ".jpeg", ".png", ".webp"},
}
_IMG = {"image/jpeg", "image/png"}
KIND_TYPES = {
    "capture": _IMG | {"video/mp4", "video/quicktime", "video/x-m4v", "application/octet-stream", "application/x-insv", "image/x-insp"},
    "model": {"application/x-step", "application/octet-stream", "model/ifc", "text/plain"},
    "schedule": {"application/xml", "text/xml", "application/octet-stream", "text/plain", "application/x-xer"},
    "evidence": _IMG | {"image/webp"},
}
KIND_LIMIT_MB = {"capture": None, "model": 4096, "schedule": 512, "evidence": 50}  # None = FAUXEL_MAX_DIRECT_UPLOAD_MB


def safe_name(name: str) -> str:
    base = PurePosixPath(name.replace("\\", "/")).name
    return re.sub(r"[^A-Za-z0-9._-]", "_", base)[-120:].lstrip(".") or "file"


def limit_bytes(settings, kind: str) -> int:
    cap = settings.max_direct_upload_mb
    k = KIND_LIMIT_MB[kind]
    return (min(cap, k) if k else cap) * MB


def part_size_for(size: int) -> int:
    ps = 16 * MB if size <= 2048 * MB else 32 * MB if size <= 8192 * MB else 64 * MB
    return ps


def validate_request(settings, kind: str, filename: str, size: int, content_type: str) -> None:
    ext = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    if ext not in KIND_EXT[kind]:
        raise HTTPException(422, f"{kind} uploads accept {', '.join(sorted(KIND_EXT[kind]))}, got '{ext or filename}'")
    if content_type.split(";")[0].strip().lower() not in KIND_TYPES[kind]:
        raise HTTPException(422, f"content_type {content_type} not allowed for {kind} uploads")
    if size > limit_bytes(settings, kind):
        raise HTTPException(413, f"{kind} upload larger than {limit_bytes(settings, kind) // MB} MB")
    if math.ceil(size / part_size_for(size)) > MAX_PARTS:
        raise HTTPException(413, "too many parts")


def n_parts(row: dict) -> int:
    return math.ceil(row["size"] / row["part_size"])


def claim_key(conn, storage, pid, key: str, kind: str) -> dict:
    """The key must belong to a completed upload of the same project and kind, and the object must exist."""
    row = conn.execute("SELECT * FROM uploads WHERE key = %s AND project_id = %s AND kind = %s AND status = 'complete'",
                       (key, pid, kind)).fetchone()
    if not row:
        raise HTTPException(422, f"upload_key {key!r} is not a completed {kind} upload of this project")
    if storage.size(key) is None:
        raise HTTPException(422, f"upload_key {key!r} is missing from storage")
    return row


def new_key(pid, upload_id: uuid.UUID, filename: str) -> str:
    return f"uploads/{pid}/{upload_id}/{safe_name(filename)}"
