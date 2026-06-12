r"""Bulk-PATCH /partner/auth/users/{userId} with 3 groupings each.

Reads a semicolon-separated CSV with header:
    userId;groupings._id;groupings.description;groupings._id1;groupings.description;groupings._id2;groupings.description

For each row, sends a minimal PATCH containing only `admin`, `groupings`,
`type` — no name/email/phone. The upstream is expected to keep those fields
unchanged when they are omitted from the body.

Usage (PowerShell):
    $env:BEEHUS_PARTNER_TOKEN = "<paste token>"
    python scripts/bulk_assign_groupings.py "C:\path\lista_blue.csv"           # dry run
    python scripts/bulk_assign_groupings.py "C:\path\lista_blue.csv" --send    # actually PATCH
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from partner_api.client import request, set_token  # noqa: E402
from partner_api.exceptions import PartnerAPIError  # noqa: E402


def parse_rows(csv_path: Path) -> list[dict]:
    """Parse the CSV, returning [{userId, groupings: [{_id, description, isOwn}, ...]}]."""
    rows = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f, delimiter=";")
        header = next(reader, None)
        if not header:
            return rows
        for raw in reader:
            if not raw:
                continue
            uid = (raw[0] or "").strip() if len(raw) > 0 else ""
            if not uid:
                continue
            groupings = []
            for i in (1, 3, 5):
                gid  = (raw[i]   or "").strip() if len(raw) > i   else ""
                desc = (raw[i+1] or "").strip() if len(raw) > i+1 else ""
                if not gid:
                    continue
                groupings.append({
                    "_id":         gid,
                    "description": desc,
                    "isOwn":       True,
                })
            rows.append({"userId": uid, "groupings": groupings})
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", help="Path to lista_blue-style semicolon CSV")
    p.add_argument("--send", action="store_true",
                   help="Actually PATCH (otherwise dry-run)")
    p.add_argument("--token",
                   help="Partner bearer token (else env BEEHUS_PARTNER_TOKEN)")
    p.add_argument("--throttle-seconds", type=float, default=0.0,
                   help="Sleep between PATCHes to stay under server rate limits")
    args = p.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Not found: {csv_path}", file=sys.stderr)
        return 2

    rows = parse_rows(csv_path)
    if not rows:
        print("No rows to process.")
        return 0

    print(f"Parsed {len(rows)} row(s) from {csv_path.name}\n")
    for r in rows[:3]:
        print(f"  example userId={r['userId']}  {len(r['groupings'])} groupings:")
        for g in r["groupings"]:
            print(f"    - {g['_id']}  {g['description']!r}  isOwn={g['isOwn']}")
    if len(rows) > 3:
        print(f"  ... and {len(rows) - 3} more rows")
    print()

    if not args.send:
        print("Dry-run only. Re-run with --send to PATCH.")
        return 0

    token = args.token or os.environ.get("BEEHUS_PARTNER_TOKEN", "")
    if not token.strip():
        print("ERROR: no token provided.", file=sys.stderr)
        return 2
    set_token(token)

    ok = 0
    failed: list[tuple[int, str, str]] = []
    for i, r in enumerate(rows, 1):
        if args.throttle_seconds > 0 and i > 1:
            time.sleep(args.throttle_seconds)
        uid = r["userId"]
        payload = {
            "admin":     False,
            "groupings": r["groupings"],
            "type":      "client",
        }
        try:
            request("PATCH", f"/partner/auth/users/{uid}", json=payload)
            ok += 1
            print(f"[{i:>3}] OK   {uid}  ({len(r['groupings'])} groupings)", flush=True)
        except PartnerAPIError as e:
            msg = f"{e} (status={getattr(e, 'status', None)}) body={(getattr(e, 'body', '') or '')[:200]}"
            failed.append((i, uid, msg))
            print(f"[{i:>3}] FAIL {uid}  {msg}", flush=True)

    print(f"\nDone. {ok} succeeded, {len(failed)} failed.")
    if failed:
        print("\nFailed rows:")
        for i, uid, msg in failed:
            print(f"  [{i}] {uid} -> {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
