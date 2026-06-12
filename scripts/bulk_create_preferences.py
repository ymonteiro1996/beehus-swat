r"""Bulk-create /partner/client-preferences for a list of userIds.

Input file is a CSV with one userId per line (no header). Lines are stripped
of whitespace and BOM; blank lines are skipped.

Usage (PowerShell):
    $env:BEEHUS_PARTNER_TOKEN = "<paste token>"
    python scripts/bulk_create_preferences.py "C:\path\userid.csv"           # dry run
    python scripts/bulk_create_preferences.py "C:\path\userid.csv" --send    # actually POST
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from partner_api.client import request, set_token  # noqa: E402
from partner_api.exceptions import PartnerAPIError  # noqa: E402

PATH = "/partner/client-preferences"
BASE_PAYLOAD = {
    "authorizedReportMonths": [],
    "currency":                "BRL",
    "language":                "pt",
}


def read_user_ids(csv_path: Path) -> list[str]:
    raw = csv_path.read_text(encoding="utf-8-sig")
    ids = []
    for line in raw.splitlines():
        s = line.strip()
        if s:
            ids.append(s)
    return ids


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", help="Path to file with one userId per line")
    p.add_argument("--send", action="store_true",
                   help="Actually POST (otherwise dry-run)")
    p.add_argument("--token",
                   help="Partner bearer token (else env BEEHUS_PARTNER_TOKEN)")
    p.add_argument("--throttle-seconds", type=float, default=0.0,
                   help="Sleep between POSTs to stay under server rate limits")
    args = p.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"Not found: {csv_path}", file=sys.stderr)
        return 2

    user_ids = read_user_ids(csv_path)
    if not user_ids:
        print("No userIds to process.")
        return 0

    print(f"Parsed {len(user_ids)} userId(s) from {csv_path.name}\n")
    sample = user_ids[:3]
    for uid in sample:
        payload = {**BASE_PAYLOAD, "userId": uid}
        print(f"  example: {json.dumps(payload, ensure_ascii=False)}")
    if len(user_ids) > 3:
        print(f"  ... and {len(user_ids) - 3} more")
    print()

    if not args.send:
        print("Dry-run only. Re-run with --send to POST.")
        return 0

    token = args.token or os.environ.get("BEEHUS_PARTNER_TOKEN", "")
    if not token.strip():
        print("ERROR: no token provided.", file=sys.stderr)
        return 2
    set_token(token)

    ok = 0
    failed: list[tuple[int, str, str]] = []
    for i, uid in enumerate(user_ids, 1):
        if args.throttle_seconds > 0 and i > 1:
            time.sleep(args.throttle_seconds)
        payload = {**BASE_PAYLOAD, "userId": uid}
        try:
            request("POST", PATH, json=payload)
            ok += 1
            print(f"[{i:>3}] OK   {uid}", flush=True)
        except PartnerAPIError as e:
            msg = f"{e} (status={e.status}) body={(e.body or '')[:200]}"
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
