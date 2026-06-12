"""Upload an array of financial transactions to the Beehus API from a JSON file.

Mirrors the per-row submit pattern of upload_intraday_buysell.py, but accepts
a generic payload (e.g. groupingId-based, no entityId/walletId) and POSTs each
transaction as-is via `request()` so the script does not impose a schema.

Input file shape:
    { "companyId": "...", "transactions": [ { ...payload... }, ... ] }
or just a bare JSON array of transaction payloads.

Usage (PowerShell):
    $env:BEEHUS_TOKEN = "<paste JWT here>"
    python scripts/upload_transactions_from_json.py --file C:\path\to\file.json
    python scripts/upload_transactions_from_json.py --file C:\path\to\file.json --send
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

from beehus_api import BeehusAPIError, BeehusAuthError, set_token  # noqa: E402
from beehus_api.client import request  # noqa: E402


def _load_rows(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        return raw
    if isinstance(raw, dict) and isinstance(raw.get("transactions"), list):
        return raw["transactions"]
    raise SystemExit(
        f"{path}: expected a JSON array or an object with a 'transactions' array"
    )


def _summary_line(i: int, total: int, r: dict) -> str:
    date = r.get("operationDate", "????-??-??")
    cur = r.get("currencyId", "???")
    bal = r.get("balance", 0)
    grp = (r.get("groupingId") or r.get("walletId") or "")[-6:]
    desc = (r.get("description") or "")[:48]
    try:
        bal_s = f"{float(bal):>16,.2f}"
    except (TypeError, ValueError):
        bal_s = str(bal)
    return f"[{i:>3}/{total}] {date} {cur} grp={grp} {bal_s}  {desc}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, type=Path,
                   help="Path to the JSON file with transactions to upload.")
    p.add_argument("--send", action="store_true",
                   help="Actually POST. Without this it's a dry run.")
    p.add_argument("--start-at", type=int, default=1,
                   help="1-based index of the first row to send (skip earlier rows). "
                        "Used to resume after a crash.")
    args = p.parse_args()

    rows = _load_rows(args.file)
    if not rows:
        print("No transactions to send.")
        return 0
    if args.start_at < 1 or args.start_at > len(rows):
        raise SystemExit(f"--start-at must be between 1 and {len(rows)}")

    if args.send:
        token = (os.environ.get("BEEHUS_TOKEN") or "").strip()
        if not token:
            print("ERR: set BEEHUS_TOKEN env var to the JWT before running with --send")
            return 2
        set_token(token)

    print(f"{'POSTING' if args.send else 'DRY RUN'} {len(rows)} transaction(s) "
          f"from {args.file}")
    print()

    ok = 0
    errors: list[tuple[int, object, str]] = []
    for i, r in enumerate(rows, start=1):
        label = _summary_line(i, len(rows), r)
        if i < args.start_at:
            print(label, " (skipped)")
            continue
        if not args.send:
            print(label, " (dry run)")
            continue
        try:
            result = request("POST", "/beehus/financial/transactions", json=r)
            created_id = (result or {}).get("_id") or (result or {}).get("id") or "?"
            print(label, f"  [OK] created _id={created_id}")
            ok += 1
        except BeehusAuthError as e:
            print(label, f"  [AUTH] stopping. {e}")
            errors.append((i, "auth_error", str(e)))
            break
        except BeehusAPIError as e:
            body = getattr(e, "body", None)
            body_short = (json.dumps(body, ensure_ascii=False)[:200]
                          if isinstance(body, (dict, list)) else str(body)[:200])
            print(label, f"  [ERR {getattr(e, 'status', '?')}] {body_short}")
            errors.append((i, getattr(e, "status", None), str(e)))
        except Exception as e:  # network / unexpected
            print(label, f"  [EXC] {type(e).__name__}: {e}")
            errors.append((i, "exception", str(e)))

    print()
    if not args.send:
        print("Dry run complete. Re-run with --send to actually POST.")
        return 0
    print(f"Done. {ok} ok, {len(errors)} error(s).")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
