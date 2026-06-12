"""Audit existing reinforcement rules against the production data.

For every rule in `data/identify_transactions_reinforcements.json`, look
up the transactions whose normalised description matches the rule key
and compare the rule's `(beehusTransactionType, securityId)` against
the most frequent classification in the data. Flag rules where the
rule disagrees with the data majority, and rules with worryingly low
support.

Use cases:
  * Find FII / coupon vs dividend inconsistencies before they propagate.
  * Spot rules that were correct once but the operator has since been
    re-classifying differently.
  * Detect rules whose backing transactions all disappeared (zero-hit
    rules — pure dead weight).

This is read-only — it prints a report but never writes. Decisions go
through the UI (Configurações → Regras de Reforço) so the operator
retains intent.
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
    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        data = json.load(f) or {}
    return data.get("rules") or {}


def aggregate_by_key(entity_id=None):
    """Return {normalised_key: {(securityId, type): count}} from the
    entire transactions collection (or restricted to one entity)."""
    query = {
        "description": {"$exists": True, "$ne": ""},
        "trashed":     {"$ne": True},
    }
    if entity_id:
        query["entityId"] = entity_id

    by_key = defaultdict(lambda: defaultdict(int))
    for d in db.transactions.find(
        query,
        {"description": 1, "securityId": 1, "beehusTransactionType": 1},
    ):
        norm = normalize_reinforcement_key(d.get("description") or "")
        if not norm:
            continue
        sid = str(d.get("securityId") or "")
        btt = d.get("beehusTransactionType") or ""
        by_key[norm][(sid, btt)] += 1
    return by_key


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entity-id", default="",
                   help="Restrict audit to one entity (default: all)")
    p.add_argument("--min-disagreement", type=float, default=0.50,
                   help="Flag rules where the data majority disagrees with the rule "
                        "and the disagreeing fraction is >= this threshold")
    p.add_argument("--min-support", type=int, default=10,
                   help="Skip rules with fewer than N matching transactions "
                        "(low support means the disagreement is noisy)")
    p.add_argument("--show", type=int, default=40, help="Max rows to print per category")
    args = p.parse_args()

    rules = load_rules()
    print(f"Loaded {len(rules)} rules.")

    print(f"Aggregating transactions"
          + (f" for entityId={args.entity_id}" if args.entity_id else " (all entities)")
          + "…")
    by_key = aggregate_by_key(args.entity_id)
    print(f"  {len(by_key)} distinct normalised keys appear in transactions.")

    disagree_type = []
    disagree_sec  = []
    zero_hit      = []
    no_data_signal = []

    for key, rule in rules.items():
        rule_type = rule.get("beehusTransactionType") or ""
        rule_sec  = rule.get("securityId") or ""
        pairs = by_key.get(key)
        if not pairs:
            zero_hit.append({"key": key, "rule": rule})
            continue
        total = sum(pairs.values())
        if total < args.min_support:
            continue

        # Informative subset only (drop rows with neither type nor sec).
        informative = {(sid, btt): c for (sid, btt), c in pairs.items() if sid or btt}
        info_total = sum(informative.values())
        if not informative or info_total < args.min_support:
            no_data_signal.append({"key": key, "rule": rule,
                                   "total": total, "info": info_total})
            continue

        # Type disagreement — compare rule.type to majority of informative types.
        if rule_type:
            type_counts = defaultdict(int)
            for (_sid, btt), c in informative.items():
                if btt:
                    type_counts[btt] += c
            type_total = sum(type_counts.values())
            if type_total >= args.min_support:
                top_type, top_n = sorted(type_counts.items(), key=lambda kv: -kv[1])[0]
                if top_type != rule_type and (top_n / type_total) >= args.min_disagreement:
                    disagree_type.append({
                        "key":         key,
                        "ruleType":    rule_type,
                        "dataTop":     top_type,
                        "dataTopHits": top_n,
                        "dataTopShare": top_n / type_total,
                        "typeTotal":   type_total,
                        "ruleHits":    rule.get("hits", 0),
                    })

        # Security disagreement — same shape.
        if rule_sec:
            sec_counts = defaultdict(int)
            for (sid, _btt), c in informative.items():
                if sid:
                    sec_counts[sid] += c
            sec_total = sum(sec_counts.values())
            if sec_total >= args.min_support:
                top_sec, top_n = sorted(sec_counts.items(), key=lambda kv: -kv[1])[0]
                if top_sec != rule_sec and (top_n / sec_total) >= args.min_disagreement:
                    disagree_sec.append({
                        "key":         key,
                        "ruleSec":     rule_sec,
                        "ruleSecName": rule.get("securityName") or "",
                        "dataTop":     top_sec,
                        "dataTopHits": top_n,
                        "dataTopShare": top_n / sec_total,
                        "secTotal":    sec_total,
                        "ruleHits":    rule.get("hits", 0),
                    })

    print()
    print(f"─── TYPE disagreements ({len(disagree_type)}) ─────────────────────────")
    disagree_type.sort(key=lambda d: -d["dataTopHits"])
    for d in disagree_type[: args.show]:
        share = 100 * d["dataTopShare"]
        print(f"  rule={d['ruleType']:<14} data={d['dataTop']:<14} "
              f"({d['dataTopHits']}/{d['typeTotal']} = {share:.0f}% disagree)")
        print(f"    key: {d['key'][:110]}")
    if len(disagree_type) > args.show:
        print(f"  … and {len(disagree_type) - args.show} more")

    print()
    print(f"─── SECURITY disagreements ({len(disagree_sec)}) ─────────────────────")
    disagree_sec.sort(key=lambda d: -d["dataTopHits"])
    for d in disagree_sec[: args.show]:
        share = 100 * d["dataTopShare"]
        print(f"  rule={d['ruleSecName'][:35]:<35} ({d['ruleSec'][:8]}…)")
        print(f"  data top hits={d['dataTopHits']}/{d['secTotal']} = {share:.0f}% disagree → sec={d['dataTop'][:8]}…")
        print(f"    key: {d['key'][:110]}")
    if len(disagree_sec) > args.show:
        print(f"  … and {len(disagree_sec) - args.show} more")

    print()
    print(f"─── ZERO-HIT rules ({len(zero_hit)}) — no matching txns at all ───")
    for z in zero_hit[: min(args.show, 10)]:
        print(f"  {z['key'][:110]}")
    if len(zero_hit) > 10:
        print(f"  … and {len(zero_hit) - 10} more")

    print()
    print(f"─── NO-SIGNAL keys ({len(no_data_signal)}) — keys whose txns are "
          f"mostly unclassified ───")
    for n in no_data_signal[: min(args.show, 5)]:
        print(f"  {n['key'][:90]}  total={n['total']} informative={n['info']}")
    if len(no_data_signal) > 5:
        print(f"  … and {len(no_data_signal) - 5} more")


if __name__ == "__main__":
    main()
