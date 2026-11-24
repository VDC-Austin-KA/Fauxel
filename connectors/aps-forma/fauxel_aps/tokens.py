"""Encrypted token storage (Fernet) and a token manager that refreshes 3-legged tokens.

`conn` is a psycopg connection with dict_row (the API's get_conn dependency).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx
from cryptography.fernet import Fernet, InvalidToken

from . import auth
from .config import ApsConfig
from .errors import ApsAuthError

PROVIDER = "aps"
SKEW = 90  # refresh this many seconds before expiry


def generate_key() -> str:
    return Fernet.generate_key().decode()


class Vault:
    def __init__(self, key: str):
        if not key:
            raise ApsAuthError("FAUXEL_TOKEN_KEY (a Fernet key) is not set; generate one with fauxel_aps.tokens.generate_key()")
        try:
            self._f = Fernet(key.encode() if isinstance(key, str) else key)
        except Exception as exc:
            raise ApsAuthError(f"FAUXEL_TOKEN_KEY is not a valid Fernet key: {exc}") from exc

    def enc(self, s: str | None) -> str | None:
        return None if s is None else self._f.encrypt(s.encode()).decode()

    def dec(self, s: str | None) -> str | None:
        if s is None:
            return None
        try:
            return self._f.decrypt(s.encode()).decode()
        except InvalidToken as exc:
            raise ApsAuthError("stored token cannot be decrypted (FAUXEL_TOKEN_KEY changed?)") from exc


def _expiry(expires_in) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(expires_in or 3600))


def save_three_legged(conn, vault: Vault, token: dict, project_id=None, subject: str | None = None, scopes: str | None = None) -> None:
    conn.execute(
        "INSERT INTO integration_tokens (provider, kind, project_id, subject, scopes, access_token_enc, refresh_token_enc, expires_at) "
        "VALUES (%s,'three_legged',%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT (provider, kind, COALESCE(project_id, '00000000-0000-0000-0000-000000000000'::uuid), COALESCE(subject, '')) "
        "DO UPDATE SET scopes = EXCLUDED.scopes, access_token_enc = EXCLUDED.access_token_enc, "
        "refresh_token_enc = COALESCE(EXCLUDED.refresh_token_enc, integration_tokens.refresh_token_enc), "
        "expires_at = EXCLUDED.expires_at, updated_at = now()",
        (PROVIDER, project_id, subject, scopes, vault.enc(token["access_token"]), vault.enc(token.get("refresh_token")),
         _expiry(token.get("expires_in"))))


def find_three_legged(conn, project_id=None):
    """Account-wide sign-in first (decision 2026-11-24); a legacy project-scoped row is only a fallback."""
    return conn.execute(
        "SELECT * FROM integration_tokens WHERE provider = %s AND kind = 'three_legged' AND (project_id = %s OR project_id IS NULL) "
        "ORDER BY (project_id IS NOT NULL), updated_at DESC LIMIT 1", (PROVIDER, project_id)).fetchone()


class TokenManager:
    """Provides bearer tokens. Two-legged tokens are cached in memory; three-legged ones live encrypted in the DB."""

    def __init__(self, cfg: ApsConfig, *, vault: Vault | None = None, client: httpx.Client | None = None,
                 clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self._vault = vault
        self.client = client
        self.clock = clock
        self._two: tuple[str, float] | None = None
        self._lock = threading.Lock()

    @property
    def vault(self) -> Vault:
        if self._vault is None:
            self._vault = Vault(self.cfg.token_key)
        return self._vault

    def two_legged(self) -> str:
        with self._lock:
            if self._two and self._two[1] - SKEW > self.clock():
                return self._two[0]
            t = auth.client_credentials(self.cfg, client=self.client)
            self._two = (t["access_token"], self.clock() + int(t.get("expires_in", 3600)))
            return self._two[0]

    def three_legged(self, conn, project_id=None) -> str:
        row = find_three_legged(conn, project_id)
        if not row:
            raise ApsAuthError("no 3-legged APS login stored; open /api/v1/integrations/aps/login first", 401)
        exp = row["expires_at"]
        if exp is None or exp - timedelta(seconds=SKEW) > datetime.now(timezone.utc):
            return self.vault.dec(row["access_token_enc"])
        rt = self.vault.dec(row["refresh_token_enc"])
        if not rt:
            raise ApsAuthError("APS access token expired and no refresh token is stored; log in again", 401)
        t = auth.refresh(self.cfg, rt, client=self.client)
        conn.execute(
            "UPDATE integration_tokens SET access_token_enc = %s, refresh_token_enc = COALESCE(%s, refresh_token_enc), "
            "expires_at = %s, updated_at = now() WHERE id = %s",
            (self.vault.enc(t["access_token"]), self.vault.enc(t.get("refresh_token")), _expiry(t.get("expires_in")), row["id"]))
        return t["access_token"]

    def provider3(self, conn, project_id=None) -> Callable[[], str]:
        return lambda: self.three_legged(conn, project_id)
