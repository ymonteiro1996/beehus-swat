"""Typed errors for Beehus API calls.

Callers can catch BeehusAuthError to prompt the user to re-paste the token,
or BeehusAPIError for any other non-2xx response.
"""


class BeehusAPIError(Exception):
    """Non-2xx response from the Beehus API. Carries status and body."""

    def __init__(self, message: str, status: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status = status
        self.body = body


class BeehusAuthError(BeehusAPIError):
    """Token missing, expired, or rejected (401/403)."""
