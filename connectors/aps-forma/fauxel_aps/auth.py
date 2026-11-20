"""APS Authentication v2: client credentials (2-legged) and authorization code + PKCE (3-legged), refresh.

Docs (verified): https://aps.autodesk.com/en/docs/oauth/v2/reference/http/authorize-GET/  and  .../gettoken-POST/
"""
from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import urlencode

import httpx

from .config import ApsConfig
from .errors import ApsAuthError

AUTHORIZE_PATH = "/authentication/v2/authorize"
TOKEN_PATH = "/authentication/v2/token"


def new_pkce() -> tuple[str, str]:
    """(code_verifier, S256 code_challenge). Verifier is 43..128 unreserved chars."""
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def new_state() -> str:
    return secrets.token_urlsafe(24)


def authorize_url(cfg: ApsConfig, state: str, code_challenge: str | None = None, scopes: str | None = None,
                  prompt: str | None = None) -> str:
    cfg.require_client()
    if not cfg.callback_url:
        raise ApsAuthError("FAUXEL_APS_CALLBACK_URL is not set")
    q = {"response_type": "code", "client_id": cfg.client_id, "redirect_uri": cfg.callback_url,
         "scope": scopes or cfg.scopes_3lo, "state": state}
    if code_challenge:
        q.update(code_challenge=code_challenge, code_challenge_method="S256")
    if prompt:
        q["prompt"] = prompt
    return f"{cfg.base_url}{AUTHORIZE_PATH}?{urlencode(q)}"


def _basic(cfg: ApsConfig) -> str:
    return "Basic " + base64.b64encode(f"{cfg.client_id}:{cfg.client_secret}".encode()).decode()


def _post_token(cfg: ApsConfig, form: dict, client: httpx.Client | None) -> dict:
    """Private client: credentials in the Basic header only (sending both header and body returns 400).
    Public client (no secret): client_id in the body and no Authorization header."""
    headers = {"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"}
    if cfg.client_secret:
        headers["Authorization"] = _basic(cfg)
    else:
        form = {**form, "client_id": cfg.client_id}
    own = client is None
    client = client or httpx.Client(timeout=30)
    try:
        r = client.post(f"{cfg.base_url}{TOKEN_PATH}", headers=headers, data=form)
    finally:
        if own:
            client.close()
    if r.status_code != 200:
        raise ApsAuthError(f"token endpoint -> {r.status_code}: {r.text[:300]}", r.status_code)
    return r.json()


def client_credentials(cfg: ApsConfig, scopes: str | None = None, client: httpx.Client | None = None) -> dict:
    """2-legged. Needs a client secret. Returns {access_token, token_type, expires_in}."""
    cfg.require_client()
    if not cfg.client_secret:
        raise ApsAuthError("two-legged auth needs FAUXEL_APS_CLIENT_SECRET")
    return _post_token(cfg, {"grant_type": "client_credentials", "scope": scopes or cfg.scopes_2lo}, client)


def exchange_code(cfg: ApsConfig, code: str, code_verifier: str | None, client: httpx.Client | None = None) -> dict:
    """3-legged code exchange. Returns {access_token, refresh_token, expires_in, ...}."""
    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": cfg.callback_url}
    if code_verifier:
        form["code_verifier"] = code_verifier
    return _post_token(cfg, form, client)


def refresh(cfg: ApsConfig, refresh_token: str, scopes: str | None = None, client: httpx.Client | None = None) -> dict:
    """Returns a new access token and a NEW refresh token (the old one stops working)."""
    form = {"grant_type": "refresh_token", "refresh_token": refresh_token}
    if scopes:
        form["scope"] = scopes
    return _post_token(cfg, form, client)
