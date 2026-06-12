"""Partner API client package.

Reusable HTTP client for https://controladoria.beehus.com.br/partner endpoints.

Mirrors the beehus_api package but uses an INDEPENDENT bearer token. The
two tokens come from different upstream auth flows and are stored in
separate process-memory slots so refreshing one does not clobber the other.
"""
from .client import set_token, get_token, clear_token, token_status
from .exceptions import PartnerAPIError, PartnerAuthError
from .users import create_user, update_user

__all__ = [
    "set_token",
    "get_token",
    "clear_token",
    "token_status",
    "PartnerAPIError",
    "PartnerAuthError",
    "create_user",
    "update_user",
]
