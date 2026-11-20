"""Direct-to-storage multipart uploads + job status. Large files never pass through Cloudflare's 100 MB body limit."""
from __future__ import annotations

import uuid
from io import BytesIO
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from starlette.datastructures import UploadFile

from .. import uploads as up
from ..db import get_conn
from .deps import get_project

router = APIRouter()


class UploadIn(BaseModel):
    project_id: uuid.UUID
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=100)
    kind: Literal["capture", "model", "schedule", "evidence"]


class PartIn(BaseModel):
    part_number: int = Field(ge=1, le=up.MAX_PARTS)
    etag: str = Field(min_length=1, max_length=200)


class CompleteIn(BaseModel):
    parts: list[PartIn] = Field(min_length=1)


def _get(conn, uid) -> dict:
    row = conn.execute("SELECT * FROM uploads WHERE id = %s", (uid,)).fetchone()
    if not row:
        raise HTTPException(404, "upload not found")
    return row


def _part_url(request: Request, storage, row: dict, n: int) -> str:
    url = storage.presign_part(row["key"], row["storage_upload_id"], n, 3600)
    return url or f"{request.app.state.settings.public_url}/api/v1/uploads/{row['id']}/parts/{n}"


@router.post("/uploads", status_code=201)
def create_upload(body: UploadIn, request: Request, conn=Depends(get_conn, scope="function")):
    s, storage = request.app.state.settings, request.app.state.storage
    get_project(conn, body.project_id)
    up.validate_request(s, body.kind, body.filename, body.size, body.content_type)
    uid = uuid.uuid4()
    key = up.new_key(body.project_id, uid, body.filename)
    ps = up.part_size_for(body.size)
    sid = storage.create_multipart(key, body.content_type)
    row = conn.execute(
        "INSERT INTO uploads (id, project_id, kind, filename, content_type, size, part_size, key, storage_upload_id) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *",
        (uid, body.project_id, body.kind, body.filename, body.content_type, body.size, ps, key, sid)).fetchone()
    return {"upload_id": uid, "key": key, "part_size": ps, "expires_in": 3600, "direct": storage.presigned,
            "parts": [{"part_number": n, "url": _part_url(request, storage, row, n)} for n in range(1, up.n_parts(row) + 1)]}


@router.get("/uploads/{uid}")
def read_upload_state(uid: uuid.UUID, request: Request, conn=Depends(get_conn, scope="function")):
    """Resume support: parts already stored (S3 ListParts) and fresh URLs for the missing ones."""
    storage = request.app.state.storage
    row = _get(conn, uid)
    out = {"upload_id": row["id"], "project_id": row["project_id"], "kind": row["kind"], "filename": row["filename"],
           "size": row["size"], "part_size": row["part_size"], "parts_total": up.n_parts(row), "status": row["status"],
           "key": row["key"] if row["status"] == "complete" else None, "parts": [], "pending": []}
    if row["status"] == "uploading":
        done = storage.list_parts(row["key"], row["storage_upload_id"])
        have = {p["part_number"] for p in done}
        out["parts"] = done
        out["pending"] = [{"part_number": n, "url": _part_url(request, storage, row, n)}
                          for n in range(1, out["parts_total"] + 1) if n not in have]
    return out


@router.put("/uploads/{uid}/parts/{n}")
async def put_part(uid: uuid.UUID, n: int, request: Request, conn=Depends(get_conn, scope="function")):
    """API-side part upload (fs backend / fully local use). Parts are at most part_size (<= 64 MB)."""
    storage = request.app.state.storage
    row = _get(conn, uid)
    if row["status"] != "uploading":
        raise HTTPException(409, f"upload is {row['status']}")
    if not 1 <= n <= up.n_parts(row):
        raise HTTPException(422, f"part_number must be 1..{up.n_parts(row)}")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > row["part_size"]:
            raise HTTPException(413, f"part larger than {row['part_size']} bytes")
    if not buf:
        raise HTTPException(422, "empty part")
    last = n == up.n_parts(row)
    if not last and len(buf) != row["part_size"]:
        raise HTTPException(422, f"part {n} must be exactly {row['part_size']} bytes")
    etag = storage.put_part(row["key"], row["storage_upload_id"], n, bytes(buf))
    return Response(status_code=200, headers={"ETag": f'"{etag}"'}, content=b"", media_type="application/json")


@router.post("/uploads/{uid}/complete")
def complete_upload(uid: uuid.UUID, body: CompleteIn, request: Request, conn=Depends(get_conn, scope="function")):
    storage = request.app.state.storage
    row = _get(conn, uid)
    if row["status"] == "complete":
        return {"upload_id": row["id"], "key": row["key"], "size": row["size"], "status": "complete"}
    if row["status"] != "uploading":
        raise HTTPException(409, f"upload is {row['status']}")
    nums = sorted(p.part_number for p in body.parts)
    if nums != list(range(1, up.n_parts(row) + 1)):
        raise HTTPException(422, f"expected parts 1..{up.n_parts(row)} exactly once each")
    parts = [{"part_number": p.part_number, "etag": p.etag} for p in body.parts]
    try:
        storage.complete_multipart(row["key"], row["storage_upload_id"], parts)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:  # botocore ClientError: bad etag, part too small, ...
        raise HTTPException(422, f"storage refused to complete the upload: {exc}")
    got = storage.size(row["key"])
    if got != row["size"]:
        storage.delete(row["key"])
        conn.execute("UPDATE uploads SET status = 'aborted' WHERE id = %s", (uid,))
        conn.commit()
        raise HTTPException(422, f"stored size {got} does not match declared size {row['size']}; upload discarded")
    conn.execute("UPDATE uploads SET status = 'complete', completed_at = now() WHERE id = %s", (uid,))
    return {"upload_id": row["id"], "key": row["key"], "size": row["size"], "status": "complete"}


@router.delete("/uploads/{uid}", status_code=204)
def abort_upload(uid: uuid.UUID, request: Request, conn=Depends(get_conn, scope="function")):
    row = _get(conn, uid)
    if row["status"] == "complete":
        raise HTTPException(409, "upload already complete")
    if row["status"] == "uploading":
        try:
            request.app.state.storage.abort_multipart(row["key"], row["storage_upload_id"])
        except Exception:
            pass  # already gone on the storage side
        conn.execute("UPDATE uploads SET status = 'aborted' WHERE id = %s", (uid,))
    return Response(status_code=204)


# ---------- jobs ----------

@router.get("/jobs/{jid}")
def read_job(jid: uuid.UUID, conn=Depends(get_conn, scope="function")):
    row = conn.execute("SELECT id, project_id, kind, status, attempts, result, error, created_at, finished_at FROM jobs WHERE id = %s", (jid,)).fetchone()
    if not row:
        raise HTTPException(404, "job not found")
    return row


# ---------- schedule from a stored upload ----------

class UploadKeyIn(BaseModel):
    upload_key: str


@router.post("/projects/{pid}/schedule/from-upload", status_code=201)
async def schedule_from_upload(pid: uuid.UUID, body: UploadKeyIn, request: Request, conn=Depends(get_conn, scope="function")):
    """Same as POST /projects/{pid}/schedule, reading the P6 / MS Project file from storage."""
    from . import progress

    get_project(conn, pid)
    storage = request.app.state.storage
    row = up.claim_key(conn, storage, pid, body.upload_key, "schedule")
    data = storage.get(row["key"])
    return await progress.upload_schedule(pid, request, UploadFile(file=BytesIO(data), filename=row["filename"]), conn)
