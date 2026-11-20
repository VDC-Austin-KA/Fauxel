"""API keys."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from .. import auth
from ..db import get_conn

router = APIRouter()


class KeyIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@router.post("/api-keys", status_code=201, dependencies=[Depends(auth.require_admin)])
def create_api_key(body: KeyIn, request: Request, conn=Depends(get_conn)):
    """The plaintext key is returned once; only salt + sha256 is stored."""
    key_id, key = auth.create_key(conn, body.name, request.app.state.settings.key_salt)
    return {"id": key_id, "name": body.name, "key": key}
