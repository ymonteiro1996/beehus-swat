"""Detect reinforcement rules that disagree with recent ground truth.

A rule encodes "this normalised description → this (type, securityId)".
When the operator's recent classifications systematically pick a
different value, that's drift — either:
  • the rule was wrong from the start (historical mislabel that the
    majority happened to share),
  • the convention changed (e.g. an FII reclassification rolled out),
  • the security got rotated (monthly fund series).

This script walks the rules file, finds each rule's recent occurrences
in `db.transactions`, and reports the cases where the *majority* of
recent classifications disagrees with the rule. Output is grouped by
severity so the operator can fix the worst offenders first.

Usage:
    python scripts/audit_reinforcement_drift.py \
        --entity-id 67cf6a5c71f5e8c88f760505 \
        --from 2026-04-01 --to 2026-05-31
"""
import argparse
import json
import os
import sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import db  # noqa: E402
from reinforcement_keys import normalize_reinforcement_key  # noqa: E402

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)


def load_rules():
    if not os.path.exists(REINFORCEMENTS_FILE):
        sys.exit(f"file not found: {REINFORCEMENTS_FILE}")
    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        d = json.load(f) or {}
    return d.get("rules") or {}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entity-id", default="",
                   help="Restrict to one entity (recommended for focused audits)")
    p.add_argument("--from", dest="from_date", required=True,
                   help="liquidationDate >= (YYYY-MM-DD)")
    p.add_argument("--to", dest="to_date", required=True,
                   help="liquidationDate <= (YYYY-MM-DD)")
    p.add_argument("--min-evidence", type=int, default=3,
                   help="Minimum recent occurrences before we judge a rule")
    p.add_argument("--top", type=int, default=30,
                   help="How many drifting rules to print")
    args = p.parse_args()

    rules = load_rules()
    print(f"Loaded {len(rules)} rules.")

    query = {
        "description":     {"$exists": True, "$ne": ""},
        "liquidationDate": {"$gte": args.from_date, "$lte": args.to_date},
        "trashed":         {"$ne": True},
    }
    if args.entity_id:
        query["entityId"] = args.entity_id

    # For each rule's normalised key, collect the (type, securityId) of
    # rows in the date window. We only judge cases where the row has
    # *some* ground truth — empty rows tell us nothing.
    observed = defaultdict(lambda: defaultdict(int))
    n_rows = 0
    for d in db.transactions.find(
        query,
        {"description": 1, "securityId": 1, "beehusTransactionType": 1},
    ):
        n_rows += 1
        norm = normalize_reinforcement_key(d.get("description") or "")
        if not norm or norm not in rules:
            continue
        sid = str(d.get("securityId") or "")
        btt = d.get("beehusTransactionType") or ""
        if not sid and not btt:
            continue
        observed[norm][(sid, btt)] += 1

    print(f"Scanned {n_rows} transactions in window "
          f"({args.from_date} → {args.to_date}); "
          f"{len(observed)} rule keys have recent ground truth.")
    print()

    drifts = []
    for key, pairs in observed.items():
        total = sum(pairs.values())
        if total < args.min_evidence:
            continue
        ranked = sorted(pairs.items(), key=lambda kv: -kv[1])
        (top_sid, top_btt), top_count = ranked[0]
        rule = rules[key]
        rule_sid = rule.get("securityId") or ""
        rule_btt = rule.get("beehusTransactionType") or ""

        # Two kinds of drift, scored independently.
        type_drift = top_btt and rule_btt and top_btt != rule_btt
        sec_drift  = top_sid and rule_sid and top_sid != rule_sid

        if not (type_drift or sec_drift):
            continue

        majority_share = top_count / total
        drifts.append({
            "key":           key,
            "rule_type":     rule_btt,
            "rule_sec":      rule_sid,
            "rule_sec_name": rule.get("securityName", ""),
            "obs_type":      top_btt,
            "obs_sec":       top_sid,
            "type_drift":    type_drift,
            "sec_drift":     sec_drift,
            "evidence":      total,
            "majority":      top_count,
            "share":         majority_share,
            "all_pairs":     ranked,
        })

    drifts.sort(key=lambda d: -d["evidence"] * d["share"])

    print(f"Detected {len(drifts)} rules with drift "
          f"(min_evidence={args.min_evidence}).")
    print()

    if not drifts:
        return

    # Build a lookup of observed-security ObjectId → beehusName for nice display.
    from bson import ObjectId
    new_sec_ids = {d["obs_sec"] for d in drifts if d["obs_sec"]}
    new_sec_meta = {}
    oids = []
    for sid in new_sec_ids:
        try:
            oids.append(ObjectId(sid))
        except Exception:
            pass
    if oids:
        for s in db.securities.find({"_id": {"$in": oids}}, {"beehusName": 1}):
            new_sec_meta[str(s["_id"])] = s.get("beehusName") or ""

    print("─── Top drifting rules ───────────────────────────────────────────")
    for d in drifts[: args.top]:
        flags = []
        if d["type_drift"]: flags.append(f"TYPE {d['rule_type']} → {d['obs_type']}")
        if d["sec_drift"]:
            old = (d["rule_sec_name"] or d["rule_sec"][:8] + "…")
            new = new_sec_meta.get(d["obs_sec"]) or d["obs_sec"][:8] + "…"
            flags.append(f"SEC {old} → {new}")
        flag_str = "  |  ".join(flags)
        print(f"  [{d['majority']:>3}/{d['evidence']:<3}  {d['share']*100:>3.0f}%] {flag_str}")
        print(f"     key: {d['key'][:90]}")
        # If there are multiple competing observations show the runner-up
        # so the operator sees whether the drift is unanimous or split.
        if len(d["all_pairs"]) > 1:
            (sid2, btt2), n2 = d["all_pairs"][1]
            sid2_label = new_sec_meta.get(sid2, sid2[:8] + "…") if sid2 else "∅"
            print(f"     runner-up: {n2}× type={btt2 or '∅'} sec={sid2_label}")
        print()

    print()
    print("To fix: open Identificar Transações → ⚙ Configurações → "
          "Regras de Reforço and edit the rule (or delete and let the "
          "next manual classification re-seed it).")


if __name__ == "__main__":
    main()
