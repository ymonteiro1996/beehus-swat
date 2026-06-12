"""Add or update a single reinforcement entry from the CLI.

Targets the same `data/identify_transactions_reinforcements.json` file
the UI writes through, so the result is indistinguishable from what
"Salvar como reforço" produces in Identificar Transações.

Two ways to identify the description being taught:
  --desc-raw  "Pgto Juros 26C4374746 | CRI X"  (we normalise it)
  --desc-key  "PGTO JUROS <OPCODE> | CRI X"    (already normalised)

Either way the canonical key is recomputed via
`reinforcement_keys.normalize_reinforcement_key` before storing — same
contract used by the runtime lookup.

Security metadata (`securityName`, `securityMainId`) is auto-resolved
by looking up `securityId` in `db.securities`; you can override either
with the corresponding flag.

Updating an existing key:
  * `hits` is incremented by `--hits` (default 1).
  * Non-empty new fields overwrite the existing ones; empty new fields
    keep the previous value.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bson import ObjectId  # noqa: E402

from db import db, atomic_write_json  # noqa: E402
from reinforcement_keys import normalize_reinforcement_key  # noqa: E402

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--desc-raw", help="Raw description (will be normalised)")
    g.add_argument("--desc-key", help="Pre-normalised key (still re-normalised for safety)")

    p.add_argument("--type", default="",
                   help="beehusTransactionType (e.g. buySell, coupon, taxes). "
                        "Leave empty to teach security-only.")
    p.add_argument("--security-id", default="",
                   help="securityId to attach to this description")
    p.add_argument("--security-name", default="",
                   help="Override the looked-up name (rarely needed)")
    p.add_argument("--security-main-id", default="",
                   help="Override the looked-up mainId (rarely needed)")
    p.add_argument("--hits", type=int, default=1,
                   help="How many hits to add (default 1 — same as a manual save)")
    p.add_argument("--apply", action="store_true",
                   help="Write the file. Without this flag the script just reports.")
    args = p.parse_args()

    if not args.type and not args.security_id:
        p.error("at least one of --type or --security-id must be provided")

    raw = args.desc_raw or args.desc_key or ""
    key = normalize_reinforcement_key(raw)
    if not key:
        print("Empty key after normalisation — nothing to add.")
        return

    # Resolve security metadata if id provided and the operator didn't
    # override name/main explicitly. Tolerate ids that aren't valid
    # ObjectIds (legacy string ids exist in production) — they just
    # won't enrich, which is fine.
    name = args.security_name
    main = args.security_main_id
    if args.security_id and (not name or not main):
        try:
            doc = db.securities.find_one(
                {"_id": ObjectId(args.security_id)},
                {"beehusName": 1, "mainId": 1},
            )
        except Exception:
            doc = None
        if doc:
            name = name or doc.get("beehusName") or ""
            main = main or doc.get("mainId") or ""

    # Load existing.
    state = {"rules": {}}
    if os.path.exists(REINFORCEMENTS_FILE):
        with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
            state = json.load(f) or {"rules": {}}
        if not isinstance(state.get("rules"), dict):
            state["rules"] = {}

    existing = state["rules"].get(key) or {}
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    new_rule = {
        "beehusTransactionType": args.type or existing.get("beehusTransactionType") or "",
        "securityId":            args.security_id or existing.get("securityId") or "",
        "securityName":          name or existing.get("securityName") or "",
        "securityMainId":        main or existing.get("securityMainId") or "",
        "lastDescriptionRaw":    args.desc_raw or existing.get("lastDescriptionRaw") or key,
        "addedAt":               existing.get("addedAt") or now,
        "lastSeenAt":            now,
        "hits":                  int(existing.get("hits") or 0) + max(0, args.hits),
    }

    print("Key:", key)
    print("Existing:", json.dumps(existing, ensure_ascii=False, indent=2) if existing else "(new)")
    print("New     :", json.dumps(new_rule, ensure_ascii=False, indent=2))

    if args.apply:
        state["rules"][key] = new_rule
        atomic_write_json(REINFORCEMENTS_FILE, state)
        print()
        print(f"Saved {REINFORCEMENTS_FILE}")
    else:
        print()
        print("Dry run. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
