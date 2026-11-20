"""Exceptions raised by the APS adapter."""
from __future__ import annotations


class ApsError(Exception):
    """Any failure talking to APS."""

    def __init__(self, message: str, status: int | None = None, body: object = None):
        super().__init__(message)
        self.status = status
        self.body = body


class ApsAuthError(ApsError):
    """Missing configuration, no token, or an OAuth failure."""


class ApsRateLimited(ApsError):
    """429 that survived all retries."""


class ApsPending(ApsError):
    """The service answered 202 for too long (translation or property extraction not finished)."""
