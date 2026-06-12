"""Low-level HTTP client + token storage for the Beehus API.

Token lives in module-level state guarded by a lock. Single-process Flask
(use_reloader=False) means this is shared across all blueprints and requests.

The token is also **persisted to disk** (`~/.swat/beehus.token`) and reloaded
at import time, so a server restart comes back up with the previously-entered
token instead of forcing the operator to re-paste it. We deliberately store it
in `~/.swat/` (same local, current-user-only directory as the session token in
auth.py) and NOT under the OneDrive-synced project `data/` dir — a bearer
credential must not sync to the cloud. The token may still expire upstream
(it's a short-lived token); a stale reload just yields a 401 on the next call
and the operator re-pastes, exactly as before — persistence only saves the
re-paste when the token is still valid (e.g. a mid-day restart).
"""
import json
import threading
import time
from pathlib import Path

import requests

from .exceptions import BeehusAPIError, BeehusAuthError

BASE_URL = "https://controladoria.beehus.com.br"
DEFAULT_TIMEOUT = 30  # seconds

_lock = threading.Lock()
_state: dict = {"token": None, "set_at": None}

_session = requests.Session()


def _token_path() -> Path:
    d = Path.home() / ".swat"
    d.mkdir(parents=True, exist_ok=True)
    return d / "beehus.token"


def _persist_token(token, set_at) -> None:
    """Best-effort write of the token to disk. Failure never breaks the live
    in-memory token — persistence is a convenience, not a hard requirement."""
    try:
        _token_path().write_text(
            json.dumps({"token": token, "set_at": set_at}), encoding="utf-8",
        )
    except Exception:
        pass


def _load_persisted_token() -> None:
    """Populate `_state` from `~/.swat/beehus.token` at import time, if present."""
    try:
        p = _token_path()
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8") or "{}")
        t = (data.get("token") or "").strip()
        if t:
            _state["token"] = t
            _state["set_at"] = data.get("set_at")
    except Exception:
        pass


def set_token(token: str) -> None:
    """Store the bearer token in memory AND persist it to disk so it survives a
    server restart. Whitespace stripped; empty rejected."""
    t = (token or "").strip()
    if not t:
        raise ValueError("token is empty")
    with _lock:
        _state["token"] = t
        _state["set_at"] = time.time()
        _persist_token(_state["token"], _state["set_at"])


def clear_token() -> None:
    with _lock:
        _state["token"] = None
        _state["set_at"] = None
        try:
            _token_path().unlink(missing_ok=True)
        except Exception:
            pass


# Reload any previously-saved token on startup so a restart comes back
# authenticated without a manual re-paste.
_load_persisted_token()


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


def _headers(*, json_body: bool = True) -> dict:
    t = get_token()
    if not t:
        raise BeehusAuthError(
            "Bearer token not set. Open /beehus and paste today's token."
        )
    h = {"Authorization": f"Bearer {t}"}
    if json_body:
        h["Content-Type"] = "application/json"
    # For multipart uploads, let `requests` set Content-Type with the boundary.
    return h


def request(method: str, path: str, *, json=None, params=None, timeout: int | None = None):
    """Send a request to the Beehus API and return the parsed JSON body.

    Raises BeehusAuthError on 401/403, BeehusAPIError on any other non-2xx.
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
        raise BeehusAPIError(f"Network error calling {method} {path}: {e}") from e

    if r.status_code in (401, 403):
        raise BeehusAuthError(
            f"Token rejected ({r.status_code}). Re-paste today's token on /beehus.",
            status=r.status_code,
            body=r.text[:500],
        )
    if not r.ok:
        raise BeehusAPIError(
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


def request_multipart(method: str, path: str, *, files, data=None,
                      params=None, timeout: int | None = None):
    """Send a multipart/form-data request and return the parsed JSON body.

    `files` is a dict accepted by `requests` (e.g. `{"file": (name, bytes,
    mimetype)}`); `data` carries non-file form fields. We omit the JSON
    Content-Type header so `requests` can fill in the multipart boundary.
    """
    url = f"{BASE_URL}{path}"
    try:
        r = _session.request(
            method,
            url,
            headers=_headers(json_body=False),
            files=files,
            data=data or {},
            params=params,
            timeout=timeout or DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        raise BeehusAPIError(f"Network error calling {method} {path}: {e}") from e

    if r.status_code in (401, 403):
        raise BeehusAuthError(
            f"Token rejected ({r.status_code}). Re-paste today's token on /beehus.",
            status=r.status_code,
            body=r.text[:500],
        )
    if not r.ok:
        raise BeehusAPIError(
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
