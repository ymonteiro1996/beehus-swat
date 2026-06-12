"""Low-level HTTP client for the BTG Pactual Empresas API.

Auth flow: OAuth2 Client Credentials against BTG Id.
  POST https://id.btgpactual.com/oauth2/token
  Basic <base64(client_id:client_secret)>
  grant_type=client_credentials&scope=<scope>

Token is cached in memory and refreshed automatically when within
`_REFRESH_BUFFER_S` of expiry. Banking APIs require Authorization Code,
not Client Credentials — those endpoints will 401/403 here.
"""
from __future__ import annotations

import base64
import threading
import time

import requests


class BTGAPIError(RuntimeError):
    def __init__(self, msg, *, status=None, body=None):
        super().__init__(msg)
        self.status = status
        self.body = body


class BTGAuthError(BTGAPIError):
    pass


TOKEN_URL_PROD = "https://id.btgpactual.com/oauth2/token"
TOKEN_URL_SANDBOX = "https://id.sandbox.btgpactual.com/oauth2/token"
BASE_URL_PROD = "https://api.empresas.btgpactual.com"
BASE_URL_SANDBOX = "https://api.sandbox.empresas.btgpactual.com"

DEFAULT_TIMEOUT = 30
_REFRESH_BUFFER_S = 60  # refresh if less than 60s left on the token

_lock = threading.Lock()
_state: dict = {
    "client_id": None,
    "client_secret": None,
    "scope": "apps",
    "token_url": TOKEN_URL_PROD,
    "base_url": BASE_URL_PROD,
    "access_token": None,
    "expires_at": 0.0,
    "obtained_scope": None,
}

_session = requests.Session()


def configure(client_id: str, client_secret: str, *,
              env: str = "prod", scope: str = "apps") -> None:
    """Store credentials and pick the environment. Call once at startup."""
    if not client_id or not client_secret:
        raise ValueError("client_id and client_secret are required")
    if env not in ("prod", "sandbox"):
        raise ValueError("env must be 'prod' or 'sandbox'")
    with _lock:
        _state["client_id"] = client_id.strip()
        _state["client_secret"] = client_secret.strip()
        _state["scope"] = scope
        if env == "sandbox":
            _state["token_url"] = TOKEN_URL_SANDBOX
            _state["base_url"] = BASE_URL_SANDBOX
        else:
            _state["token_url"] = TOKEN_URL_PROD
            _state["base_url"] = BASE_URL_PROD
        _state["access_token"] = None
        _state["expires_at"] = 0.0
        _state["obtained_scope"] = None


def _fetch_token() -> None:
    cid = _state["client_id"]
    secret = _state["client_secret"]
    if not cid or not secret:
        raise BTGAuthError("Credentials not configured. Call btg_api.configure(...) first.")

    basic = base64.b64encode(f"{cid}:{secret}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {"grant_type": "client_credentials", "scope": _state["scope"]}

    try:
        r = _session.post(
            _state["token_url"], headers=headers, data=data, timeout=DEFAULT_TIMEOUT
        )
    except requests.RequestException as e:
        raise BTGAPIError(f"Network error obtaining token: {e}") from e

    if r.status_code in (400, 401, 403):
        raise BTGAuthError(
            f"Token request rejected ({r.status_code})",
            status=r.status_code,
            body=r.text[:1000],
        )
    if not r.ok:
        raise BTGAPIError(
            f"Token request failed: {r.status_code}",
            status=r.status_code,
            body=r.text[:1000],
        )

    payload = r.json()
    token = payload.get("access_token")
    if not token:
        raise BTGAuthError("Token response missing access_token", body=r.text[:1000])
    expires_in = int(payload.get("expires_in") or 0)
    _state["access_token"] = token
    _state["expires_at"] = time.time() + max(0, expires_in - _REFRESH_BUFFER_S)
    _state["obtained_scope"] = payload.get("scope")


def get_access_token(force_refresh: bool = False) -> str:
    with _lock:
        if force_refresh or not _state["access_token"] or time.time() >= _state["expires_at"]:
            _fetch_token()
        return _state["access_token"]


def token_status() -> dict:
    """Snapshot for diagnostics / UI."""
    now = time.time()
    return {
        "configured": bool(_state["client_id"]),
        "env": "sandbox" if _state["base_url"] == BASE_URL_SANDBOX else "prod",
        "base_url": _state["base_url"],
        "token_url": _state["token_url"],
        "scope_requested": _state["scope"],
        "scope_granted": _state["obtained_scope"],
        "has_token": bool(_state["access_token"]),
        "expires_in": max(0, _state["expires_at"] - now) if _state["expires_at"] else 0,
    }


def request(method: str, path: str, *, json=None, params=None,
            data=None, headers=None, timeout: int | None = None):
    """Send an authenticated request. Returns parsed JSON, raw text, or None."""
    token = get_access_token()
    base = _state["base_url"]
    url = path if path.startswith("http") else f"{base}{path}"
    h = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if json is not None:
        h["Content-Type"] = "application/json"
    if headers:
        h.update(headers)

    try:
        r = _session.request(
            method, url, headers=h, json=json, params=params, data=data,
            timeout=timeout or DEFAULT_TIMEOUT,
        )
    except requests.RequestException as e:
        raise BTGAPIError(f"Network error calling {method} {path}: {e}") from e

    if r.status_code == 401:
        # Token may have been revoked early — refresh once and retry.
        token = get_access_token(force_refresh=True)
        h["Authorization"] = f"Bearer {token}"
        try:
            r = _session.request(
                method, url, headers=h, json=json, params=params, data=data,
                timeout=timeout or DEFAULT_TIMEOUT,
            )
        except requests.RequestException as e:
            raise BTGAPIError(f"Network error on retry {method} {path}: {e}") from e

    if r.status_code in (401, 403):
        raise BTGAuthError(
            f"BTG API rejected token on {method} {path} ({r.status_code})",
            status=r.status_code,
            body=r.text[:1000],
        )
    if not r.ok:
        raise BTGAPIError(
            f"{method} {path} failed: {r.status_code}",
            status=r.status_code,
            body=r.text[:1500],
        )

    if not r.content:
        return None
    ctype = r.headers.get("Content-Type", "")
    if "application/json" in ctype:
        try:
            return r.json()
        except ValueError:
            return r.text
    return r.text
