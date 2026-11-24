"""Environment-driven configuration."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from .http import BASE_URL

# Scopes: data:read/data:write/data:create cover Data Management, Issues, Locations, Assets, Photos and the
# AEC Data Model; viewables:read is what the Model Derivative manifest reference lists next to data:read.
DEFAULT_SCOPES_3LO = "data:read data:write data:create viewables:read"
DEFAULT_SCOPES_2LO = "data:read data:write data:create viewables:read"


@dataclass
class ApsConfig:
    client_id: str = ""
    client_secret: str = ""          # empty = public client (PKCE only, no secret)
    callback_url: str = ""
    scopes_3lo: str = DEFAULT_SCOPES_3LO
    scopes_2lo: str = DEFAULT_SCOPES_2LO
    base_url: str = BASE_URL
    region: str | None = None        # x-ads-region / region header: US | EMEA (Issues), US/EMEA/AUS/... (Model Derivative)
    webhook_secret: str = ""         # webhooks secret token (HMAC key); register it with POST /webhooks/v1/tokens
    token_key: str = ""              # Fernet key for token storage at rest
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_env(cls, **overrides) -> "ApsConfig":
        e = lambda k, d=None: os.environ.get(k) or d   # blank (compose `${X:-}`) = unset
        c = cls(
            client_id=e("FAUXEL_APS_CLIENT_ID", ""),
            client_secret=e("FAUXEL_APS_CLIENT_SECRET", ""),
            callback_url=e("FAUXEL_APS_CALLBACK_URL", ""),
            scopes_3lo=e("FAUXEL_APS_SCOPES_3LO", DEFAULT_SCOPES_3LO),
            scopes_2lo=e("FAUXEL_APS_SCOPES_2LO", DEFAULT_SCOPES_2LO),
            base_url=e("FAUXEL_APS_BASE_URL", BASE_URL),
            region=e("FAUXEL_APS_REGION") or None,
            webhook_secret=e("FAUXEL_APS_WEBHOOK_SECRET", ""),
            token_key=e("FAUXEL_TOKEN_KEY", ""),
        )
        for k, v in overrides.items():
            setattr(c, k, v)
        return c

    def require_client(self) -> None:
        from .errors import ApsAuthError
        if not self.client_id:
            raise ApsAuthError("FAUXEL_APS_CLIENT_ID is not set")
