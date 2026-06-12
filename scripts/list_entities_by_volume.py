"""List entities by transaction volume and current reinforcement coverage.

Quick triage tool: shows which entities have the most transactions, what
fraction already has at least one of (type, securityId) populated, and how
many distinct descriptions repeat enough to be worth building rules
against. Run this before pointing `build_reinforcements_from_history` at
a new entity to decide whether the volume justifies a pass.
"""
import argparse
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import db  # noqa: E402
from reinforcement_keys import normalize_reinforcement_key  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--top", type=int, default=20,
                   help="How many entities to print")
    p.add_argument("--min-volume", type=int, default=100,
                   help="Skip entities with fewer than N transactions")
    args = p.parse_args()

    # Per-entity counters. We do a single pass over the collection so the
    # script stays under a few seconds even on large mirrors.
    by_entity = defaultdict(lambda: {
        "total":          0,
        "classified":     0,    # row has type OR securityId
        "fully_classified": 0,  # row has BOTH type AND securityId
        "desc_counts":    defaultdict(lambda: {"total": 0, "classified": 0}),
    })

    cursor = db.transactions.find(
        {"description": {"$exists": True, "$ne": ""},
         "trashed":     {"$ne": True}},
        {"entityId": 1, "description": 1, "securityId": 1, "beehusTransactionType": 1},
    )
    for d in cursor:
        eid = str(d.get("entityId") or "")
        if not eid:
            continue
        bucket = by_entity[eid]
        bucket["total"] += 1
        sid = str(d.get("securityId") or "")
        btt = d.get("beehusTransactionType") or ""
        if sid or btt:
            bucket["classified"] += 1
        if sid and btt:
            bucket["fully_classified"] += 1
        norm = normalize_reinforcement_key(d.get("description") or "")
        if norm:
            db_bucket = bucket["desc_counts"][norm]
            db_bucket["total"] += 1
            if sid or btt:
                db_bucket["classified"] += 1

    # Resolve entity names. The collection's _id key is `entityId` (string
    # in transactions).
    name_by_id = {}
    eids = list(by_entity.keys())
    if eids:
        for e in db.entities.find({"_id": {"$in": eids}}, {"name": 1}):
            name_by_id[str(e["_id"])] = e.get("name") or ""

    rows = []
    for eid, b in by_entity.items():
        if b["total"] < args.min_volume:
            continue
        # Count distinct "recurring" descriptions — i.e. with >= 5
        # occurrences and >= 30% classified rate. That's the universe the
        # build script would actually emit rules from.
        recurring = 0
        for desc, dc in b["desc_counts"].items():
            if dc["total"] < 5:
                continue
            if dc["total"] > 0 and dc["classified"] / dc["total"] >= 0.30:
                recurring += 1
        rows.append({
            "entityId":          eid,
            "name":              name_by_id.get(eid, "")[:40],
            "total":             b["total"],
            "classified":        b["classified"],
            "fully":             b["fully_classified"],
            "distinctDescs":     len(b["desc_counts"]),
            "ruleCandidates":    recurring,
        })

    rows.sort(key=lambda r: -r["total"])
    print(f"{'entityId':<26} {'name':<40} {'total':>7} {'classif':>7} "
          f"{'full':>7} {'descs':>6} {'ruleCand':>8}")
    print("-" * 108)
    for r in rows[: args.top]:
        cls_pct = 100.0 * r["classified"] / r["total"]
        print(f"{r['entityId']:<26} {r['name']:<40} "
              f"{r['total']:>7} {r['classified']:>7} ({cls_pct:>4.0f}%) "
              f"{r['fully']:>7} {r['distinctDescs']:>6} {r['ruleCandidates']:>8}")


if __name__ == "__main__":
    main()
