"""Auth per contract: X-Fauxel-Key (hashed in api_keys), optional trusted Cloudflare Access header, FAUXEL_AUTH=off for dev."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid

from fastapi import HTTPException, Request

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
PROXY_HEADERS = ("cf-connecting-ip", "x-forwarded-for", "cf-ray", "cf-access-authenticated-user-email", "forwarded")


def _digest(salt: str, secret: str, pepper: str) -> str:
    return hashlib.sha256(f"{pepper}{salt}{secret}".encode()).hexdigest()


def create_key(conn, name: str, pepper: str) -> tuple[str, str]:
    """Returns (key_id, plaintext key). The plaintext is shown once; only salt+sha256 is stored."""
    key_id = str(uuid.uuid4())
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO api_keys (id, name, key_hash) VALUES (%s, %s, %s)",
        (key_id, name, f"{salt}${_digest(salt, secret, pepper)}"),
    )
    return key_id, f"{key_id}.{secret}"


def verify_key(conn, presented: str, pepper: str) -> bool:
    try:
        key_id, secret = presented.split(".", 1)
        uuid.UUID(key_id)
    except ValueError:
        return False
    row = conn.execute("SELECT key_hash FROM api_keys WHERE id = %s", (key_id,)).fetchone()
    if not row:
        return False
    salt, digest = row["key_hash"].split("$", 1)
    return hmac.compare_digest(digest, _digest(salt, secret, pepper))


def require_auth(request: Request) -> None:
    s = request.app.state.settings
    if s.auth == "off":
        return
    if s.trust_access_header and request.headers.get("cf-access-authenticated-user-email"):
        return
    key = request.headers.get("x-fauxel-key")
    if key:
        with request.app.state.pool.connection() as conn:
            if verify_key(conn, key, s.key_salt):
                return
    raise HTTPException(401, "missing or invalid credentials (X-Fauxel-Key)")


def require_admin(request: Request) -> None:
    """Key creation: dev mode, the admin token, or a direct loopback caller (never via the tunnel/proxy)."""
    s = request.app.state.settings
    if s.auth == "off":
        return
    token = request.headers.get("x-fauxel-admin")
    if s.admin_token and token and hmac.compare_digest(token, s.admin_token):
        return
    direct = request.client and request.client.host in LOOPBACK and not any(h in request.headers for h in PROXY_HEADERS)
    if direct:
        return
    raise HTTPException(403, "api key creation is local-only")
