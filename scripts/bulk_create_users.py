"""Bulk-create partner client users from a CSV with one column: NM_CLIENTE.

Derived fields per row:
    name  : title-cased NM_CLIENTE  ("A 2 G HOLDING LTDA" -> "A 2 G Holding Ltda")
    email : initials of every whitespace-separated token, lowercased, + "@beehus.com"
            ("A 2 G HOLDING LTDA" -> "a2ghl@beehus.com")
    phone : sequential, starting at --phone-start (default +5511900000001)

Usage (PowerShell):
    $env:BEEHUS_PARTNER_TOKEN = "<paste token>"
    python scripts/bulk_create_users.py "C:\path\names_clients_t.csv"           # dry run
    python scripts/bulk_create_users.py "C:\path\names_clients_t.csv" --send    # actually POST
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from pathlib import Path

# Make the project root importable when running from anywhere.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from partner_api import (  # noqa: E402
    PartnerAPIError,
    create_user,
    set_token,
)

DEFAULT_COMPANY_ID = "10000000000000"
DEFAULT_PHONE_START = "+5511900000001"


def title_case_name(raw: str) -> str:
    """Convert "A 2 G HOLDING LTDA" -> "A 2 G Holding Ltda"."""
    parts = raw.split()
    out = []
    for p in parts:
        if len(p) <= 1:
            out.append(p.upper())
        else:
            out.append(p[0].upper() + p[1:].lower())
    return " ".join(out)


def initials_email(raw: str) -> str:
    """First character of every whitespace-separated token, lowercased."""
    initials = "".join(tok[0] for tok in raw.split() if tok)
    return f"{initials.lower()}@beehus.com"


def increment_phone(phone: str) -> str:
    """Increment the trailing digit block of a "+<digits>" phone number."""
    if not phone.startswith("+"):
        raise ValueError(f"phone must start with '+': {phone!r}")
    digits = phone[1:]
    if not digits.isdigit():
        raise ValueError(f"phone must be '+' followed by digits: {phone!r}")
    width = len(digits)
    return "+" + str(int(digits) + 1).zfill(width)


def read_names(csv_path: Path) -> list[str]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if not reader.fieldnames:
            raise ValueError("CSV has no header row")
        # Tolerate surrounding whitespace on the header.
        header_map = {h.strip().upper(): h for h in reader.fieldnames}
        if "NM_CLIENTE" not in header_map:
            raise ValueError(
                f"CSV must have NM_CLIENTE column. Found: {reader.fieldnames}"
            )
        col = header_map["NM_CLIENTE"]
        names = []
        for row in reader:
            v = (row.get(col) or "").strip()
            if v:
                names.append(v)
        return names


def build_payloads(
    names: list[str],
    *,
    company_id: str,
    phone_start: str,
) -> list[dict]:
    payloads = []
    phone = phone_start
    for raw in names:
        payloads.append({
            "admin":       False,
            "companyId":   company_id,
            "email":       initials_email(raw),
            "name":        title_case_name(raw),
            "permissions": [],
            "phone":       phone,
            "type":        "client",
        })
        phone = increment_phone(phone)
    return payloads


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("csv", help="Path to CSV with NM_CLIENTE column")
    p.add_argument("--company-id", default=DEFAULT_COMPANY_ID)
    p.add_argument("--phone-start", default=DEFAULT_PHONE_START)
    p.add_argument("--send", action="store_true",
                   help="Actually POST to the API (otherwise dry-run)")
    p.add_argument("--token",
                   help="Partner bearer token (else env BEEHUS_PARTNER_TOKEN)")
    p.add_argument("--throttle-seconds", type=float, default=0.0,
                   help="Sleep this long between POSTs to stay under server rate limits")
    args = p.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}", file=sys.stderr)
        return 2

    names = read_names(csv_path)
    if not names:
        print("No rows to import.")
        return 0

    payloads = build_payloads(
        names,
        company_id=args.company_id,
        phone_start=args.phone_start,
    )

    print(f"Parsed {len(payloads)} row(s) from {csv_path.name}\n")
    for i, pl in enumerate(payloads, 1):
        print(f"[{i:>3}] {json.dumps(pl, ensure_ascii=False)}")
    print()

    if not args.send:
        print("Dry-run only. Re-run with --send to POST these to the API.")
        return 0

    token = args.token or os.environ.get("BEEHUS_PARTNER_TOKEN", "")
    if not token.strip():
        print("ERROR: no token provided (use --token or $env:BEEHUS_PARTNER_TOKEN).",
              file=sys.stderr)
        return 2
    set_token(token)

    ok = 0
    skipped = 0
    failed: list[tuple[int, dict, str]] = []
    for i, pl in enumerate(payloads, 1):
        if args.throttle_seconds > 0 and i > 1:
            time.sleep(args.throttle_seconds)
        try:
            res = create_user(
                company_id=pl["companyId"],
                name=pl["name"],
                email=pl["email"],
                phone=pl["phone"],
                permissions=pl["permissions"],
                admin=pl["admin"],
                user_type=pl["type"],
            )
            ok += 1
            uid = (res or {}).get("_id") if isinstance(res, dict) else None
            print(f"[{i:>3}] OK   {pl['email']}  id={uid}", flush=True)
        except PartnerAPIError as e:
            body = (e.body or "")[:300]
            msg = f"{e} (status={e.status}) body={body}"
            # Skip rows whose email or phone already exists upstream — these were
            # created in an earlier partial run, so a 400 about a duplicate is benign.
            lo = body.lower()
            already = (
                e.status == 400
                and (
                    "já" in lo or "ja " in lo
                    or "existe" in lo or "exists" in lo
                    or "already" in lo or "duplicad" in lo
                    or "cadastrad" in lo or "registered" in lo
                )
            )
            if already:
                skipped += 1
                print(f"[{i:>3}] SKIP {pl['email']}  already exists: {body[:160]}", flush=True)
                continue
            failed.append((i, pl, msg))
            print(f"[{i:>3}] FAIL {pl['email']}  {msg}", flush=True)

    print(f"\nDone. {ok} succeeded, {skipped} already existed, {len(failed)} failed.")
    if failed:
        print("\nFailed rows:")
        for i, pl, msg in failed:
            print(f"  [{i}] {pl['name']!r} <{pl['email']}> -> {msg}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
