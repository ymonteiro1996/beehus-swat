"""Iterate the per-entity reinforcement build across every entity with
enough transaction volume to be worth training on.

Wraps `build_reinforcements_from_history.main` per-entity, sorted by
volume desc so high-impact entities go first. Existing reinforcements
are preserved by the underlying script (first writer for a given key
wins), so this run is safe to repeat — already-covered keys are
skipped, new keys are added.

Usage:
    python scripts/build_reinforcements_all_entities.py            # dry run
    python scripts/build_reinforcements_all_entities.py --apply    # actually write

Tuneable knobs (forwarded to the underlying script):
    --min-entity-volume 1000   Skip entities with fewer transactions
    --min-hits 2               Reinforcement floor (same as the per-entity script)
    --dominance 0.80           Same
    --min-informative-share 0.30  Same
"""
import argparse
import os
import subprocess
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import db  # noqa: E402


def list_entities(min_volume):
    """Return [(entityId, name, volume)] sorted by volume desc.

    Done via $group on the server so we don't stream every transaction
    through Python — the collection is 500k+ rows, a full Python scan
    would take a minute or so before the per-entity builds even start.
    """
    pipeline = [
        {"$match": {"description": {"$exists": True, "$ne": ""},
                    "trashed":     {"$ne": True}}},
        {"$group": {"_id": "$entityId", "n": {"$sum": 1}}},
        {"$match": {"n": {"$gte": min_volume}}},
        {"$sort":  {"n": -1}},
    ]
    eligible = []
    for doc in db.transactions.aggregate(pipeline, allowDiskUse=True):
        eid = str(doc.get("_id") or "")
        if eid:
            eligible.append((eid, int(doc["n"])))

    # Look up names in one shot for the report.
    name_by_id = {}
    if eligible:
        eids = [e for e, _ in eligible]
        for e in db.entities.find({"_id": {"$in": eids}}, {"name": 1}):
            name_by_id[str(e["_id"])] = e.get("name") or ""

    return [(eid, name_by_id.get(eid, ""), n) for eid, n in eligible]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--min-entity-volume", type=int, default=1000,
                   help="Skip entities below this transaction count")
    p.add_argument("--min-hits", type=int, default=2)
    p.add_argument("--dominance", type=float, default=0.80)
    p.add_argument("--min-informative-share", type=float, default=0.30)
    p.add_argument("--apply", action="store_true",
                   help="Forward --apply to each per-entity build (default: dry run)")
    p.add_argument("--skip", action="append", default=[],
                   help="Entity id to skip (repeatable). Useful for re-running after a partial failure.")
    args = p.parse_args()

    print(f"Listing entities with >= {args.min_entity_volume} transactions…")
    entities = list_entities(args.min_entity_volume)
    print(f"  {len(entities)} eligible entities")
    print()

    skip_set = set(args.skip)
    script = os.path.join(ROOT, "scripts", "build_reinforcements_from_history.py")

    total_new = 0
    failed = []
    for i, (eid, name, vol) in enumerate(entities, start=1):
        if eid in skip_set:
            print(f"[{i}/{len(entities)}] {eid} ({vol:>6} txns) — SKIPPED (--skip)")
            continue
        label = f"{eid} {name[:32]} ({vol:>6} txns)"
        print(f"[{i}/{len(entities)}] {label}")
        cmd = [
            sys.executable, script,
            "--entity-id", eid,
            "--min-hits", str(args.min_hits),
            "--dominance", str(args.dominance),
            "--min-informative-share", str(args.min_informative_share),
            "--print-limit", "0",
        ]
        if args.apply:
            cmd.append("--apply")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                check=False,
            )
        except Exception as exc:
            print(f"    ✗ launch error: {exc}")
            failed.append(eid)
            continue
        # Extract the "N new reinforcements" line from the per-entity output
        # so we can give a one-line summary instead of N pages of logs.
        summary = ""
        for line in (result.stdout or "").splitlines():
            line = line.strip()
            if line.startswith(("scanned ", "candidate ", "new reinforcements"))\
               or "new reinforcements" in line:
                summary += "\n    " + line
        if result.returncode != 0:
            print(f"    ✗ failed (exit {result.returncode}){summary}")
            stderr_tail = (result.stderr or "").splitlines()[-3:]
            for s in stderr_tail:
                print(f"      {s}")
            failed.append(eid)
            continue
        # Count the "N new" number for the running total.
        for line in result.stdout.splitlines():
            line = line.strip()
            if "new reinforcements" in line and "already existed" in line:
                # format: "  X new reinforcements; Y already existed (preserved)"
                try:
                    n = int(line.split()[0])
                    total_new += n
                except (ValueError, IndexError):
                    pass
                break
        print(summary or "    (no detail)")

    print()
    print(f"Done. Total new reinforcements: {total_new}")
    if failed:
        print(f"Failed entities ({len(failed)}): {failed}")
    if not args.apply:
        print("Dry run. Re-run with --apply to actually write the rules.")


if __name__ == "__main__":
    main()
