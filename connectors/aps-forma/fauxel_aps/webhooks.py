"""Webhooks: signature verification, extraction events, and hook registration.

Verified: signature header `x-adsk-signature` = "sha1hash=" + hex(HMAC-SHA1(secret, raw body))
(https://aps.autodesk.com/en/docs/webhooks/v1/tutorials/how-to-verify-payload-signature/); the callback must answer 2xx within
7 s and delivery is at-least-once; secret is set with POST /webhooks/v1/tokens {"token": ...}; hooks are created with
POST /webhooks/v1/systems/{system}/events/{event}/hooks {callbackUrl, scope}; Model Derivative events are system `derivative`,
events `extraction.finished` / `extraction.updated`, scope {"workflow": "<id>"}.
Partly verified: the callback envelope is {version, resourceUrn, hook{system,event,hookId,...}, payload{...}}; filters address
the event data as `payload.Payload.*` (e.g. `Payload.status`). The exact casing of the URN key inside `payload` is not on a
page I could fetch, so `parse_extraction` accepts several spellings.
"""
from __future__ import annotations

import hashlib
import hmac

from .http import ApsSession

SIGNATURE_HEADER = "x-adsk-signature"


def sign(secret: str, body: bytes) -> str:
    return "sha1hash=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()


def verify_signature(secret: str, body: bytes, header_value: str | None) -> bool:
    if not secret or not header_value:
        return False
    return hmac.compare_digest(sign(secret, body), header_value.strip())


def _ci(d: dict, *names):
    low = {k.lower(): v for k, v in (d or {}).items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def parse_extraction(event: dict) -> dict:
    """-> {event, status, urn (url-safe base64 model urn, no padding), resource_urn, workflow, hook_id}."""
    hook = event.get("hook") or {}
    payload = event.get("payload") or {}
    inner = _ci(payload, "Payload") if isinstance(_ci(payload, "Payload"), dict) else {}
    urn = _ci(inner, "URN") or _ci(payload, "URN") or _ci(payload, "Urn")
    status = _ci(inner, "status") or _ci(payload, "status")
    return {"event": hook.get("event"), "status": (status or "").lower() or None, "urn": urn,
            "resource_urn": event.get("resourceUrn"), "workflow": (hook.get("scope") or {}).get("workflow"),
            "hook_id": hook.get("hookId")}


def event_key(event: dict, body: bytes) -> str:
    """Dedupe key for at-least-once delivery."""
    h = event.get("hook") or {}
    return f"{h.get('hookId')}:{hashlib.sha256(body).hexdigest()[:32]}"


class Webhooks:
    def __init__(self, s: ApsSession):
        self.s = s

    def set_secret(self, token: str) -> dict:
        return self.s.json("POST", "/webhooks/v1/tokens", json={"token": token}, ok=(400,))

    def create_extraction_hook(self, callback_url: str, workflow: str) -> dict | None:
        return self.s.json("POST", "/webhooks/v1/systems/derivative/events/extraction.finished/hooks",
                           json={"callbackUrl": callback_url, "scope": {"workflow": workflow}, "autoReactivateHook": True})

    def create_hook(self, system: str, event: str, callback_url: str, scope: dict, **extra) -> dict | None:
        return self.s.json("POST", f"/webhooks/v1/systems/{system}/events/{event}/hooks",
                           json={"callbackUrl": callback_url, "scope": scope, **extra})
