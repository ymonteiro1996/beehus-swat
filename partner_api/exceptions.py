"""Typed errors for Partner API calls."""


class PartnerAPIError(Exception):
    """Non-2xx response from the Partner API. Carries status and body."""

    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class PartnerAuthError(PartnerAPIError):
    """Token missing, expired, or rejected (401/403)."""
