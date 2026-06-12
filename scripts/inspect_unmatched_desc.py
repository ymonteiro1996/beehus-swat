"""Diagnostic — for a description (or descriptions) without a reinforcement,
show the historical (securityId, beehusTransactionType) distribution so we
can see whether the dominance threshold ruled it out, or there's something
else going on.
"""
import argparse
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import db  # noqa: E402
from reinforcement_keys import normalize_reinforcement_key  # noqa: E402
from pages.beehus_console import _lookup_reinforcement  # noqa: E402


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entity-id", required=True)
    p.add_argument("--desc", required=True,
                   help="Substring to match (case-insensitive) against transaction description")
    args = p.parse_args()

    needle = args.desc.lower()
    rows = list(db.transactions.find(
        {"entityId": args.entity_id, "trashed": {"$ne": True}},
        {"description": 1, "securityId": 1, "beehusTransactionType": 1, "liquidationDate": 1},
    ))
    matching = [r for r in rows if needle in (r.get("description") or "").lower()]

    print(f"Entity has {len(rows)} transactions total; "
          f"{len(matching)} contain '{args.desc}' in description.")
    if not matching:
        return

    # Group by NORMALISED description (same key the reinforcement table uses)
    # so we can see whether multiple raw forms collapse to one key.
    by_norm = defaultdict(lambda: {"raws": set(), "pairs": defaultdict(int)})
    for r in matching:
        raw = r.get("description") or ""
        norm = normalize_reinforcement_key(raw)
        sid  = str(r.get("securityId") or "")
        btt  = r.get("beehusTransactionType") or ""
        bucket = by_norm[norm]
        bucket["raws"].add(raw)
        bucket["pairs"][(sid, btt)] += 1

    print(f"  collapsed into {len(by_norm)} normalised key(s).")
    print()
    for norm, info in sorted(by_norm.items(), key=lambda kv: -sum(kv[1]['pairs'].values())):
        total = sum(info["pairs"].values())
        hit = _lookup_reinforcement(next(iter(info["raws"])) if info["raws"] else norm)
        live_status = (
            f"score={hit['score']} exact={hit['exact']}" if hit
            else "NO MATCH IN LIVE LOOKUP"
        )
        print(f"━━━ key (norm): {norm}")
        print(f"    total hits: {total}; live lookup: {live_status}")
        print(f"    distinct raw forms: {len(info['raws'])}")
        for raw in list(info["raws"])[:3]:
            print(f"      • {raw[:120]}")
        if len(info["raws"]) > 3:
            print(f"      … +{len(info['raws']) - 3} more")
        print(f"    (securityId, type) pairs:")
        ranked = sorted(info["pairs"].items(), key=lambda kv: -kv[1])
        for (sid, btt), n in ranked[:6]:
            pct = 100.0 * n / total
            sid_label = sid[:8] + "…" if sid else "(empty)"
            btt_label = btt or "(empty)"
            print(f"      {n:>4}  ({pct:5.1f}%)  type={btt_label:<14}  sec={sid_label}")
        if len(ranked) > 6:
            print(f"      … +{len(ranked) - 6} more pair(s)")
        print()


if __name__ == "__main__":
    main()
