"""Lightweight request profiler: logs slow Flask routes with their Mongo
op counts and the top-N slowest ops.

Purpose — turn "some pages feel slow" into "route X ran Y queries totalling
Z ms, here are the worst three". Runs in-process with negligible overhead
(a dict update per pymongo op, a single log line per slow request).

Thread-safety: pymongo blocking I/O runs on the calling thread, and Flask's
default WSGI worker is threaded, so per-request state stored in a
`threading.local` is visible to the CommandListener callbacks for the same
request. Not safe under gevent/asyncio — revisit if the server model changes.

Config via env vars (no code change needed to tune):
    DB_PROFILE_SLOW_REQUEST_MS   default 500  — below this, nothing is logged
    DB_PROFILE_TOP_N_OPS         default 3    — how many slow ops per line
"""
import logging
import os
import threading
import time

from flask import request
from pymongo import monitoring

_log = logging.getLogger("db_profile")

_SLOW_REQUEST_MS = int(os.environ.get("DB_PROFILE_SLOW_REQUEST_MS", "500"))
_TOP_N_OPS       = int(os.environ.get("DB_PROFILE_TOP_N_OPS", "3"))

# Skip hot paths that don't matter (static assets, health).
_SKIP_PREFIXES = ("/static", "/favicon")

_local = threading.local()


def _stats_for_request():
    """Return this thread's per-request stats dict, or None if we are outside
    a request (import-time index creation, background tasks, etc.)."""
    return getattr(_local, "stats", None)


def _collection_for(event):
    """Pymongo's CommandSucceededEvent exposes the command document; for
    find/aggregate/count/etc the collection is the value keyed by the
    command name (`event.command["find"]`, `event.command["aggregate"]` ...).
    Returns '' when the shape doesn't match (isMaster, ping, ...)."""
    try:
        return str(event.command.get(event.command_name, ""))
    except Exception:
        return ""


class _QueryMonitor(monitoring.CommandListener):
    def started(self, event):
        # Nothing to do here — duration is on succeeded/failed.
        pass

    def succeeded(self, event):
        stats = _stats_for_request()
        if stats is None:
            return
        ms = event.duration_micros / 1000.0
        stats["count"]    += 1
        stats["total_ms"] += ms
        _record_op(stats, ms, event.command_name, _collection_for(event))

    def failed(self, event):
        stats = _stats_for_request()
        if stats is None:
            return
        ms = event.duration_micros / 1000.0
        stats["count"]    += 1
        stats["total_ms"] += ms
        _record_op(stats, ms, event.command_name + "!FAIL", _collection_for(event))


def _record_op(stats, ms, name, coll):
    """Keep a running top-N of slowest ops without growing the list unbounded."""
    slow = stats["slow"]
    entry = (ms, name, coll)
    if len(slow) < _TOP_N_OPS:
        slow.append(entry)
        slow.sort(reverse=True)
        return
    if ms > slow[-1][0]:
        slow[-1] = entry
        slow.sort(reverse=True)


def install(app):
    """Register the pymongo listener and the Flask request hooks.
    Idempotent-ish — `monitoring.register` doesn't dedupe, so call once."""
    monitoring.register(_QueryMonitor())

    @app.before_request
    def _profile_start():
        if any(request.path.startswith(p) for p in _SKIP_PREFIXES):
            return
        _local.stats = {
            "t0":       time.monotonic(),
            "count":    0,
            "total_ms": 0.0,
            "slow":     [],
        }

    @app.after_request
    def _profile_end(response):
        stats = _stats_for_request()
        if stats is None:
            return response
        elapsed_ms = (time.monotonic() - stats["t0"]) * 1000.0
        # Drop stats before any further logging so background work on this
        # thread after the response doesn't get mis-attributed to the request.
        _local.stats = None
        if elapsed_ms >= _SLOW_REQUEST_MS:
            top = "; ".join(
                f"{ms:.0f}ms {name}({coll})" for ms, name, coll in stats["slow"]
            ) or "(no mongo ops)"
            _log.warning(
                "SLOW %s %s -> %.0fms total | %d mongo ops in %.0fms | top: %s",
                request.method,
                request.path,
                elapsed_ms,
                stats["count"],
                stats["total_ms"],
                top,
            )
        return response
