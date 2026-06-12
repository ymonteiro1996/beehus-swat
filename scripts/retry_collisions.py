"""One-shot retry for the 4 rows whose initials-based emails collided.

Uses firstname.lastname@beehus.com instead of bare initials, with phones
continuing from where the previous bulk run left off.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from partner_api import PartnerAPIError, create_user, set_token  # noqa: E402

COMPANY_ID = "10000000000000"

ROWS = [
    # (full name, email, phone)
    ("Leia Susskind",            "leia.susskind@beehus.com",     "+5511999999944"),
    ("Mary Ribeiro Nicoliello",  "mary.nicoliello@beehus.com",   "+5511999999945"),
    ("Richard Riviere",          "richard.riviere@beehus.com",   "+5511999999946"),
    ("Robson Rosa",              "robson.rosa@beehus.com",       "+5511999999947"),
]


def main() -> int:
    token = os.environ.get("BEEHUS_PARTNER_TOKEN", "").strip()
    if not token:
        print("ERROR: BEEHUS_PARTNER_TOKEN not set", file=sys.stderr)
        return 2
    set_token(token)

    ok = 0
    failed = []
    for name, email, phone in ROWS:
        try:
            res = create_user(
                company_id=COMPANY_ID,
                name=name,
                email=email,
                phone=phone,
            )
            uid = (res or {}).get("_id") if isinstance(res, dict) else None
            print(f"OK   {email:<35} ({name})  id={uid}")
            ok += 1
        except PartnerAPIError as e:
            msg = f"{e} body={(e.body or '')[:200]}"
            failed.append((name, email, msg))
            print(f"FAIL {email:<35} ({name})  {msg}")

    print(f"\n{ok} succeeded, {len(failed)} failed.")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
