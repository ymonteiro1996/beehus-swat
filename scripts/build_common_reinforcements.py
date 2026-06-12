"""Build reinforcements only for the MOST COMMON securities.

Ranks securities by how many (classified, non-trashed) transactions reference
them, keeps the top-N, then emits a reinforcement for every recurring
description that maps unambiguously to one of those securities. Only **full**
rules (beehusTransactionType AND securityId both set) are produced — the goal
is to seed the empty reinforcement file with the high-frequency cases.

Reuses the aggregation/dominance logic from
`build_reinforcements_from_history` so the conflict handling stays identical.

Preview (default): prints the table, writes nothing.
`--apply`: merges the candidates into data/identify_transactions_reinforcements.json
(additive — existing keys are preserved).
"""
import argparse
import json
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from bson import ObjectId  # noqa: E402
from db import db, atomic_write_json  # noqa: E402
import build_reinforcements_from_history as base  # noqa: E402

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)


def top_securities(limit):
    """Return (ordered list of securityId str, {sid: count}) for the most
    frequent securities among classified, non-trashed transactions."""
    pipeline = [
        {"$match": {"securityId": {"$nin": [None, ""]},
                    "trashed": {"$ne": True},
                    "description": {"$exists": True, "$ne": ""}}},
        {"$group": {"_id": "$securityId", "n": {"$sum": 1}}},
        {"$sort": {"n": -1}},
        {"$limit": limit},
    ]
    ordered, counts = [], {}
    for r in db.transactions.aggregate(pipeline):
        sid = str(r["_id"])
        ordered.append(sid)
        counts[sid] = r["n"]
    return ordered, counts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--top", type=int, default=50, help="How many top securities to cover")
    ap.add_argument("--min-hits", type=int, default=10, help="Min occurrences of a description to emit a rule")
    ap.add_argument("--dominance", type=float, default=0.80, help="Dominant (sid,type) share of the informative subset")
    ap.add_argument("--min-informative-share", type=float, default=0.30)
    ap.add_argument("--exclude-name", default="",
                    help="Comma-separated security-name substrings to drop (case-insensitive), "
                         "e.g. placeholders like 'Sem posição'")
    ap.add_argument("--apply", action="store_true", help="Write the rules to disk (additive)")
    args = ap.parse_args()

    print(f"Ranking top {args.top} securities by transaction frequency…")
    top_ids, top_counts = top_securities(args.top)
    top_set = set(top_ids)
    rank = {sid: i + 1 for i, sid in enumerate(top_ids)}

    print("Aggregating ALL transactions by normalised description…")
    by_desc, total = base.aggregate("", "")
    print(f"  scanned {total} transactions; {len(by_desc)} distinct normalised descriptions")

    cands = base.pick_candidates(by_desc, args.min_hits, args.dominance,
                                 args.min_informative_share)[0]
    # Keep only FULL rules (type + security) whose security is in the top-N.
    cands = [c for c in cands if c["securityId"] and c["type"] and c["securityId"] in top_set]

    meta = base.fetch_security_meta({c["securityId"] for c in cands})

    # Drop placeholder/unwanted securities by name substring.
    excl = [s.strip().lower() for s in args.exclude_name.split(",") if s.strip()]
    if excl:
        def _excluded(c):
            nm = (meta.get(c["securityId"], {}).get("name") or "").lower()
            return any(e in nm for e in excl)
        before = len(cands)
        cands = [c for c in cands if not _excluded(c)]
        print(f"  excluded {before - len(cands)} rules by name filter {excl}")

    # Sort by security frequency (rank), then by hits desc within a security.
    cands.sort(key=lambda c: (rank.get(c["securityId"], 9999), -c["hits"]))

    covered = sorted({c["securityId"] for c in cands}, key=lambda s: rank.get(s, 9999))
    print(f"\n{len(cands)} candidate rules covering {len(covered)}/{args.top} top securities\n")

    hdr = f'{"#":>3} {"hits":>5}  {"tipo":<16} {"mainId":<18} {"security":<34} | descrição (chave normalizada)'
    print(hdr); print("-" * len(hdr))
    for c in cands:
        m = meta.get(c["securityId"], {})
        sec = (m.get("name") or m.get("main") or c["securityId"])
        key = c["key"]
        key = key[:60] + ("…" if len(key) > 60 else "")
        print(f'{rank.get(c["securityId"],"?"):>3} {c["hits"]:>5}  {(c["type"] or "")[:16]:<16} '
              f'{(m.get("main") or "")[:18]:<18} {sec[:34]:<34} | {key}')

    # Per-security summary
    print("\nResumo por security (rank · qtd txns · nº de regras):")
    by_sec = {}
    for c in cands:
        by_sec.setdefault(c["securityId"], 0)
        by_sec[c["securityId"]] += 1
    for sid in covered:
        m = meta.get(sid, {})
        sec = (m.get("name") or m.get("main") or sid)
        print(f'  #{rank.get(sid,"?"):>2}  {top_counts.get(sid,0):>6} txns  {by_sec[sid]:>3} regras  · {sec[:46]}')
    missing = [sid for sid in top_ids if sid not in by_sec]
    if missing:
        print(f"\n  {len(missing)} das top {args.top} securities não geraram regra "
              f"(descrições abaixo de min-hits={args.min_hits} ou ambíguas):")
        for sid in missing:
            m = meta.get(sid, {})
            print(f'    #{rank.get(sid,"?"):>2}  {top_counts.get(sid,0):>6} txns  · {(m.get("name") or m.get("main") or sid)[:46]}')

    if not args.apply:
        print(f"\n[PREVIEW] {len(cands)} regras. Rode com --apply para gravar em {REINFORCEMENTS_FILE}")
        return

    # Apply: merge additively.
    if os.path.exists(REINFORCEMENTS_FILE):
        with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
            state = json.load(f) or {}
    else:
        state = {}
    if not isinstance(state.get("rules"), dict):
        state["rules"] = {}
    rules = state["rules"]
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    added = skipped = 0
    for c in cands:
        if c["key"] in rules:
            skipped += 1
            continue
        m = meta.get(c["securityId"], {})
        rules[c["key"]] = {
            "beehusTransactionType": c["type"],
            "securityId":            c["securityId"],
            "securityName":          m.get("name", ""),
            "securityMainId":        m.get("main", ""),
            "lastDescriptionRaw":    c["rawDesc"],
            "addedAt":               now,
            "lastSeenAt":            now,
            "hits":                  c["hits"],
        }
        added += 1
    atomic_write_json(REINFORCEMENTS_FILE, state)
    print(f"\n[APPLIED] {added} novas regras gravadas; {skipped} já existiam. "
          f"Total no arquivo: {len(rules)}")


if __name__ == "__main__":
    main()
