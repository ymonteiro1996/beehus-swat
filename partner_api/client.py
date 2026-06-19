"""Low-level HTTP client + token storage for the Partner API.

Independent token slot from `beehus_api.client` — the partner endpoints use
a different upstream auth flow, so we do not want refreshing one to clobber
the other.
"""
import threading
import time

import requests

from .exceptions import PartnerAPIError, PartnerAuthError

BASE_URL = "https://controladoria.beehus.com.br"
DEFAULT_TIMEOUT = 30  # seconds

_lock = threading.Lock()
_state: dict = {"token": None, "set_at": None}

_session = requests.Session()


def set_token(token: str) -> None:
    """Store the bearer token in memory. Whitespace stripped; empty rejected."""
    t = (token or "").strip()
    if not t:
        raise ValueError("token is empty")
    with _lock:
        _state["token"] = t
        _state["set_at"] = time.time()


def clear_token() -> None:
    with _lock:
        _state["token"] = None
        _state["set_at"] = None


def get_token() -> str | None:
    return _state["token"]


def token_status() -> dict:
    """Used by the UI to show whether a token is loaded and how old it is."""
    t = _state["token"]
    set_at = _state["set_at"]
    return {
        "loaded": bool(t),
        "set_at": set_at,
        "age_seconds": (time.time() - set_at) if set_at else None,
    }


def _headers() -> dict:
    t = get_token()
    if not t:
        raise PartnerAuthError(
            "Bearer token not set. Open /parceiro and paste today's partner token."
        )
    return {
        "Authorization": f"Bearer {t}",
        "Content-Type": "application/json",
    }


def request(method: str, path: str, *, json=None, params=None, timeout: int | None = None):
    """Send a request to the Partner API and return the parsed JSON body.

    Raises PartnerAuthError on 401/403, PartnerAPIError on any other non-2xx.
    """
    url = f"{BASE_URL}{path}"
    try:
        r = _session.request(
            method,
            url,
            headers=_headers(),
            json=json,
            params=params,
            timeout=timeout or DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        raise PartnerAPIError(f"Network error calling {method} {path}: {e}") from e

    if r.status_code in (401, 403):
        raise PartnerAuthError(
            f"Token rejected ({r.status_code}). Re-paste today's partner token on /parceiro.",
            status=r.status_code,
            body=r.text[:500],
        )
    if not r.ok:
        raise PartnerAPIError(
            f"{method} {path} failed: {r.status_code}",
            status=r.status_code,
            body=r.text[:1000],
        )

    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text
