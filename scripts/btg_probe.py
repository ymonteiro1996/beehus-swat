"""Obtain a BTG Pactual Empresas token (Client Credentials) and inspect it.

Usage:
    python scripts/btg_probe.py [--env prod|sandbox] [--scope apps]

Reads credentials from env vars BTG_CLIENT_ID / BTG_CLIENT_SECRET, or
falls back to constants below for one-off local runs.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from pathlib import Path

# Allow running from repo root: `python scripts/btg_probe.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from btg_api import configure, get_access_token, request, token_status  # noqa: E402
from btg_api.client import BTGAPIError, BTGAuthError  # noqa: E402


def _decode_jwt(token: str) -> dict | None:
    """Best-effort decode of a JWT payload (no signature check)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(payload_b64))
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=("prod", "sandbox"), default="prod")
    ap.add_argument("--scope", default="apps",
                    help="OAuth scope. Try 'apps' first; some products require specific scopes.")
    ap.add_argument("--probe", action="store_true",
                    help="After getting the token, try a few read-only endpoints.")
    args = ap.parse_args()

    client_id = os.environ.get("BTG_CLIENT_ID")
    client_secret = os.environ.get("BTG_CLIENT_SECRET")
    if not client_id or not client_secret:
        print("BTG_CLIENT_ID / BTG_CLIENT_SECRET not set in env.", file=sys.stderr)
        print("Set them in your shell (PowerShell):", file=sys.stderr)
        print('  $env:BTG_CLIENT_ID  = "..."', file=sys.stderr)
        print('  $env:BTG_CLIENT_SECRET = "..."', file=sys.stderr)
        return 2

    configure(client_id, client_secret, env=args.env, scope=args.scope)

    try:
        token = get_access_token()
    except BTGAuthError as e:
        print(f"AUTH FAILED: {e}", file=sys.stderr)
        if getattr(e, "body", None):
            print(e.body, file=sys.stderr)
        return 1
    except BTGAPIError as e:
        print(f"NETWORK/API ERROR: {e}", file=sys.stderr)
        return 1

    status = token_status()
    print("=== Token status ===")
    for k, v in status.items():
        print(f"  {k}: {v}")

    claims = _decode_jwt(token)
    if claims:
        print("\n=== JWT claims (unverified) ===")
        # Print compact, only the keys typically useful for debugging access.
        for k in ("iss", "aud", "azp", "client_id", "sub", "scope", "scp",
                  "roles", "permissions", "cnpj", "tenant", "exp", "iat"):
            if k in claims:
                print(f"  {k}: {claims[k]}")
        # Always dump full payload at the end for completeness.
        print("\n=== Full JWT payload ===")
        print(json.dumps(claims, indent=2, ensure_ascii=False))

    if args.probe:
        print("\n=== Probing common endpoints ===")
        # These are *guesses* based on the public API reference; expect some
        # to 403/404 depending on what your client app was registered for.
        probes = [
            ("GET", "/v1/companies/accounts"),
            ("GET", "/v1/companies"),
        ]
        for method, path in probes:
            try:
                resp = request(method, path)
                preview = json.dumps(resp, ensure_ascii=False)[:400] if resp is not None else "<empty>"
                print(f"  OK  {method} {path} -> {preview}")
            except BTGAuthError as e:
                print(f"  AUTH {method} {path} -> {e.status}: {(e.body or '')[:200]}")
            except BTGAPIError as e:
                print(f"  ERR  {method} {path} -> {e.status}: {(e.body or '')[:200]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
