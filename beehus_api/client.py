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
import logging
import os
import threading
import time
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter

from .exceptions import BeehusAPIError, BeehusAuthError

BASE_URL = "https://controladoria.beehus.com.br"
DEFAULT_TIMEOUT = 30  # seconds

_lock = threading.Lock()
# `rejected`: set True whenever the upstream answers 401/403 (token missing on
# the server side, expired, or wrong), cleared on the next 2xx and whenever a
# new token is pasted/cleared. Lets the UI distinguish "server rejected this
# token" from a locally-valid-looking JWT — the local `exp` decode can't.
_state: dict = {"token": None, "set_at": None, "rejected": False}

_session = requests.Session()
# Connection pool sized for the per-company fan-outs (wallets/groupings index,
# nav warm) that fire up to `beehus_catalog._NAV_WARM_WORKERS` (10) parallel GETs
# to this single host — and which may now run in the BACKGROUND
# (stale-while-revalidate) while a foreground request is also in flight. The
# urllib3 default `pool_maxsize=10` discards the overflow ("Connection pool is
# full"), forcing a fresh TCP+TLS handshake each time. 20 keeps a full fan-out
# plus a concurrent foreground request pooled for keep-alive reuse.
_adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20)
_session.mount("https://", _adapter)
_session.mount("http://", _adapter)

_log = logging.getLogger(__name__)

# ── Per-call timing instrumentation (diagnostics only) ───────────────────────
# Off by default (zero overhead beyond a boolean check). Turn on by setting the
# env var BEEHUS_HTTP_TIMING=1 at startup, or call `enable_timing()` at runtime.
# When on, every upstream call is logged (method, path, compact param summary,
# status, elapsed ms) and appended to `_timing_log` so a harness can tally how
# many upstream round-trips a single dashboard request actually fires.
_timing_lock = threading.Lock()
_timing_on = os.environ.get("BEEHUS_HTTP_TIMING", "").strip() in ("1", "true", "True")
_timing_log: list = []


def enable_timing(on: bool = True) -> None:
    global _timing_on
    _timing_on = bool(on)


def _param_summary(params, path):
    """Compact one-line summary of the query params that matter for cost —
    chiefly `date`/`initialDate`/`finalDate` and the SIZE of `walletIds`."""
    if not params:
        return ""
    bits = []
    for k in ("date", "initialDate", "finalDate", "positionDate"):
        if params.get(k):
            bits.append(f"{k}={params[k]}")
    wi = params.get("walletIds")
    if wi is not None:
        n = len([x for x in str(wi).split(",") if x]) if wi else 0
        bits.append(f"walletIds[{n}]")
    for k in ("securityIds", "pricingType"):
        if params.get(k):
            bits.append(k)
    return " ".join(bits)


def _record_timing(method, path, params, status, elapsed_ms):
    summary = _param_summary(params, path)
    with _timing_lock:
        _timing_log.append({
            "method": method, "path": path, "summary": summary,
            "status": status, "ms": round(elapsed_ms, 1),
        })
    _log.info("[beehus] %s %s %s -> %s in %.0fms",
              method, path, summary, status, elapsed_ms)


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
        # New token: forget any prior rejection until the next call proves it.
        _state["rejected"] = False
        _persist_token(_state["token"], _state["set_at"])


def clear_token() -> None:
    with _lock:
        _state["token"] = None
        _state["set_at"] = None
        _state["rejected"] = False
        try:
            _token_path().unlink(missing_ok=True)
        except Exception:
            pass


# Reload any previously-saved token on startup so a restart comes back
# authenticated without a manual re-paste.
_load_persisted_token()


def get_token() -> str | None:
    return _state["token"]


def _decode_jwt_exp(token):
    """Best-effort read of a JWT's `exp` (epoch seconds) WITHOUT verifying the
    signature — just enough to tell the UI the in-process token has expired.
    Returns an int, or None if the token isn't a decodable JWT."""
    try:
        import base64, json as _json
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload = parts[1]
        payload += "=" * (-len(payload) % 4)  # restore base64 padding
        claims = _json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = claims.get("exp")
        return int(exp) if exp is not None else None
    except Exception:  # noqa: BLE001 — malformed token must never raise here
        return None


def token_status() -> dict:
    """Whether a token is loaded, how old it is, and whether it has expired.

    `expired` decodes the **in-process** token's `exp` claim, so a running
    instance whose token aged out (the file may already hold a fresh one a
    different process pasted) is detectable — the global banner uses this to
    prompt a re-paste instead of letting API-backed pages fail silently."""
    t = _state["token"]
    set_at = _state["set_at"]
    exp = _decode_jwt_exp(t) if t else None
    expired = bool(t) and exp is not None and exp <= time.time()
    return {
        "loaded": bool(t),
        "set_at": set_at,
        "age_seconds": (time.time() - set_at) if set_at else None,
        "exp": exp,
        "expired": expired,
        # True when the upstream last answered 401/403 — catches a token the
        # server rejects even though its local `exp` still looks valid.
        "rejected": bool(_state.get("rejected")),
    }


def verify_token() -> None:
    """Probe the API with the current token via a cheap authenticated GET.

    Returns None on success; raises BeehusAuthError if the token is missing or
    rejected (401/403), or BeehusAPIError on any other upstream failure. Used by
    the token-save route to validate a pasted token immediately instead of
    letting later page reads fail silently."""
    request("GET", "/beehus/partners/companies")


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
    # Retry on 429 (rate limit) with backoff — the bulk warm of the navPackages
    # cache fires hundreds of calls and can trip the upstream rate limiter.
    # Honour `Retry-After` when present; otherwise exponential backoff capped.
    attempt = 0
    while True:
        attempt += 1
        _t0 = time.monotonic() if _timing_on else None
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
            if _timing_on:
                _record_timing(method, path, params, "ERR",
                               (time.monotonic() - _t0) * 1000.0)
            raise BeehusAPIError(f"Network error calling {method} {path}: {e}") from e
        if _timing_on:
            _record_timing(method, path, params, r.status_code,
                           (time.monotonic() - _t0) * 1000.0)
        if r.status_code == 429 and attempt <= 5:
            ra = r.headers.get("Retry-After")
            try:
                delay = float(ra) if ra else min(0.5 * (2 ** attempt), 8.0)
            except (TypeError, ValueError):
                delay = min(0.5 * (2 ** attempt), 8.0)
            time.sleep(delay)
            continue
        break

    if r.status_code in (401, 403):
        _state["rejected"] = True  # atomic dict set under the GIL
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

    _state["rejected"] = False  # a 2xx clears any prior rejection
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
    _t0 = time.monotonic() if _timing_on else None
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
        if _timing_on:
            _record_timing(method, path, params, "ERR",
                           (time.monotonic() - _t0) * 1000.0)
        raise BeehusAPIError(f"Network error calling {method} {path}: {e}") from e
    if _timing_on:
        _record_timing(method, path, params, r.status_code,
                       (time.monotonic() - _t0) * 1000.0)

    if r.status_code in (401, 403):
        _state["rejected"] = True  # atomic dict set under the GIL
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

    _state["rejected"] = False  # a 2xx clears any prior rejection
    if not r.content:
        return None
    try:
        return r.json()
    except ValueError:
        return r.text
