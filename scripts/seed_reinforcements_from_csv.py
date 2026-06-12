"""Seed multiple reinforcement rules at once from a CSV file.

Solves the "first manual classification per description" bootstrap
problem: rows whose description has never been classified can't produce
a reinforcement automatically. With this script the operator prepares
a CSV (or .tsv) of one mapping per line and applies them in a single
batch — much faster than the per-row dance in the UI.

CSV columns (header required, case-insensitive, any column order):
    description    raw description fragment (will be normalised before storing)
    type           beehusTransactionType (e.g. "buySell", "taxes")
    securityId     optional ObjectId string (empty = type-only rule)

Each row becomes a reinforcement keyed by the **normalised**
description (same pipeline `_lookup_reinforcement` uses). Existing
keys are preserved unless `--overwrite` is passed.

Example CSV:
    description,type,securityId
    "Oby Ágil FIRF RL",buySell,68f4198b...
    "Mapfre Confianza FIF RF Referenciado DI CP RL",buySell,67ffbc1b...
    "INVESTBACK",buySell,
"""
import argparse
import csv
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bson import ObjectId  # noqa: E402

from db import db, atomic_write_json  # noqa: E402
from reinforcement_keys import normalize_reinforcement_key  # noqa: E402

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)
_MIN_KEY_LEN = 10  # mirror build_reinforcements_from_history


def load_existing():
    if not os.path.exists(REINFORCEMENTS_FILE):
        return {"rules": {}}
    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        data = json.load(f) or {}
    if not isinstance(data.get("rules"), dict):
        data["rules"] = {}
    return data


def fetch_security_meta(sec_ids):
    """{securityId(str): {name, main}} for the ids we'll actually attach."""
    oids = []
    for sid in sec_ids:
        try:
            oids.append(ObjectId(sid))
        except Exception:
            pass
    meta = {}
    if oids:
        for s in db.securities.find(
            {"_id": {"$in": oids}},
            {"beehusName": 1, "mainId": 1},
        ):
            meta[str(s["_id"])] = {
                "name": s.get("beehusName") or "",
                "main": s.get("mainId") or "",
            }
    return meta


def detect_dialect(path):
    """Sniff comma-vs-tab — the operator's CSV may have come out of
    Excel as TSV. Fall back to comma if Sniffer can't decide."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        sample = f.read(8192)
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error:
        return csv.excel  # default to comma


def main():
    p = argparse.ArgumentParser()
    p.add_argument("path", help="Path to the CSV/TSV file")
    p.add_argument("--overwrite", action="store_true",
                   help="Replace existing rules with the same normalised key")
    p.add_argument("--apply", action="store_true",
                   help="Write the file. Without this flag the script just reports.")
    args = p.parse_args()

    if not os.path.exists(args.path):
        sys.exit(f"file not found: {args.path}")

    dialect = detect_dialect(args.path)
    rows_in = []
    with open(args.path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, dialect=dialect)
        # Normalise header keys to lowercase for case-insensitive access.
        fieldnames = {k.lower(): k for k in (reader.fieldnames or [])}
        required = {"description", "type"}
        missing = required - set(fieldnames)
        if missing:
            sys.exit(f"CSV missing required column(s): {sorted(missing)}; "
                     f"got {list(fieldnames)}")
        for raw_row in reader:
            row = {k.lower(): (v or "").strip() for k, v in raw_row.items()}
            rows_in.append(row)
    print(f"Read {len(rows_in)} rows from {args.path}")

    state = load_existing()
    rules = state["rules"]
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    to_add = []
    skipped_short_key = 0
    skipped_existing = 0
    skipped_empty_desc = 0
    overwritten = 0
    for row in rows_in:
        desc = row.get("description") or ""
        btt  = row.get("type") or ""
        sid  = row.get("securityid") or ""
        if not desc:
            skipped_empty_desc += 1
            continue
        norm = normalize_reinforcement_key(desc)
        if not norm or len(norm) < _MIN_KEY_LEN:
            skipped_short_key += 1
            continue
        if not btt and not sid:
            # No info to store. Skip rather than create a useless rule.
            continue
        if norm in rules and not args.overwrite:
            skipped_existing += 1
            continue
        if norm in rules and args.overwrite:
            overwritten += 1
        to_add.append({"key": norm, "rawDesc": desc, "type": btt, "securityId": sid})

    sec_meta = fetch_security_meta({a["securityId"] for a in to_add if a["securityId"]})

    for a in to_add:
        meta = sec_meta.get(a["securityId"], {})
        rules[a["key"]] = {
            "beehusTransactionType": a["type"],
            "securityId":            a["securityId"],
            "securityName":          meta.get("name", ""),
            "securityMainId":        meta.get("main", ""),
            "lastDescriptionRaw":    a["rawDesc"],
            "addedAt":               now,
            "lastSeenAt":            now,
            "hits":                  1,
        }

    print(f"  {len(to_add)} rules to {'overwrite/add' if args.overwrite else 'add'}")
    print(f"  ({skipped_existing} already existed, "
          f"{skipped_short_key} skipped — short key, "
          f"{skipped_empty_desc} skipped — empty description, "
          f"{overwritten} overwritten)")

    if to_add:
        print()
        print("Sample additions:")
        for a in to_add[:15]:
            meta = sec_meta.get(a["securityId"], {})
            sec_label = meta.get("name") or a["securityId"] or "(none)"
            print(f"  [{a['type']:<12}] {sec_label[:35]:<35}  ← {a['key'][:60]}")
        if len(to_add) > 15:
            print(f"  … and {len(to_add) - 15} more")

    if args.apply:
        atomic_write_json(REINFORCEMENTS_FILE, state)
        print()
        print(f"Saved {REINFORCEMENTS_FILE}")
    else:
        print()
        print("Dry run. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
