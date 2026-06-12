"""Test the reinforcement table against a slice of transaction history.

Runs each transaction in a date window through `_lookup_reinforcement`
(same code path used by the live Identificar Transações flow) and compares
the predicted (beehusTransactionType, securityId) against the values
actually stored on the document. Prints a coverage + accuracy report.

Usage:
    python scripts/test_reinforcements_on_month.py --entity-id <ID> \
        --from 2026-05-01 --to 2026-05-31
"""
import argparse
import os
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import json  # noqa: E402

from db import db  # noqa: E402
from pages.beehus_console import (  # noqa: E402
    _match_against_rules,
    _normalize_reinforcement_key,
)
from reinforcement_keys import strip_negating_prefix  # noqa: E402

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)


def _build_lookup():
    """Read the rules file once, return a closure that does the same
    work as `pages.beehus_console._lookup_reinforcement` but uses the
    in-memory snapshot. Eliminates the OneDrive race where rapid
    repeated reads occasionally collide with the sync agent and
    return a partial/invalid JSON body — when that happens the
    production code silently returns "no match", which would
    undercount coverage and accuracy in this test."""
    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        rules = (json.load(f) or {}).get("rules") or {}

    def _lookup(description):
        if not description:
            return None
        key = _normalize_reinforcement_key(description)
        if not key:
            return None
        inner_key = strip_negating_prefix(key)
        if inner_key:
            inner_rule, inner_match, inner_score = _match_against_rules(inner_key, rules)
            if inner_rule is not None:
                taxed_rule = dict(inner_rule)
                taxed_rule["beehusTransactionType"] = "taxes"
                return {"rule": taxed_rule,
                        "score": round(min(inner_score, 0.99), 4),
                        "exact": False,
                        "matched": inner_match}
        rule, match_key, score = _match_against_rules(key, rules)
        if rule is None:
            return None
        is_exact = (match_key == key) and (score == 1.0)
        return {"rule": dict(rule),
                "score": round(score, 4) if not is_exact else 1.0,
                "exact": is_exact,
                "matched": match_key}

    return _lookup, len(rules)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entity-id", required=True)
    p.add_argument("--from", dest="from_date", required=True,
                   help="liquidationDate >= (YYYY-MM-DD)")
    p.add_argument("--to", dest="to_date", required=True,
                   help="liquidationDate <= (YYYY-MM-DD)")
    p.add_argument("--show-misses", type=int, default=20,
                   help="how many unmatched / wrong rows to sample")
    args = p.parse_args()

    query = {
        "entityId":        args.entity_id,
        "liquidationDate": {"$gte": args.from_date, "$lte": args.to_date},
        "trashed":         {"$ne": True},
    }

    rows = list(db.transactions.find(
        query,
        {"description": 1, "securityId": 1, "beehusTransactionType": 1,
         "liquidationDate": 1, "walletId": 1},
    ))

    total = len(rows)
    print(f"Loaded {total} transactions "
          f"(entityId={args.entity_id}, {args.from_date} → {args.to_date})")
    if not total:
        return

    lookup, n_rules = _build_lookup()
    print(f"Loaded {n_rules} reinforcement rules from disk")

    # ── Bucket counters ─────────────────────────────────────────────────
    matched_exact = 0
    matched_sub   = 0
    unmatched     = 0

    type_correct, type_wrong, type_unknown = 0, 0, 0
    sec_correct,  sec_wrong,  sec_unknown  = 0, 0, 0

    unmatched_samples = []
    type_misses       = []
    sec_misses        = []

    miss_by_desc = Counter()  # for the "top unmatched descriptions" view

    for t in rows:
        desc = t.get("description") or ""
        actual_type = t.get("beehusTransactionType") or ""
        actual_sec  = str(t.get("securityId") or "")

        hit = lookup(desc)
        if hit is None:
            unmatched += 1
            type_unknown += 1
            sec_unknown += 1
            if len(unmatched_samples) < args.show_misses:
                unmatched_samples.append({
                    "desc": desc,
                    "actualType": actual_type,
                    "actualSec":  actual_sec,
                })
            miss_by_desc[desc[:100]] += 1
            continue

        if hit["exact"]:
            matched_exact += 1
        else:
            matched_sub += 1

        rule = hit["rule"]
        pred_type = rule.get("beehusTransactionType") or ""
        pred_sec  = rule.get("securityId") or ""

        # Type accuracy. We treat "actual is empty" as "unknown" — the
        # reinforcement still got applied, but there's no ground truth to
        # compare against (the row was never manually classified yet).
        if not actual_type:
            type_unknown += 1
        elif pred_type == actual_type:
            type_correct += 1
        else:
            type_wrong += 1
            if len(type_misses) < args.show_misses:
                type_misses.append({
                    "desc": desc, "pred": pred_type, "actual": actual_type,
                    "matchedKey": hit["matched"], "score": hit["score"],
                })

        # Security accuracy — same treatment.
        if not actual_sec:
            sec_unknown += 1
        elif pred_sec == actual_sec:
            sec_correct += 1
        else:
            sec_wrong += 1
            if len(sec_misses) < args.show_misses:
                sec_misses.append({
                    "desc": desc, "pred": pred_sec, "actual": actual_sec,
                    "matchedKey": hit["matched"], "score": hit["score"],
                    "predName":   rule.get("securityName") or "",
                })

    matched = matched_exact + matched_sub

    def pct(n, d):
        return f"{(100.0 * n / d) if d else 0:.1f}%"

    print()
    print("─── Cobertura ─────────────────────────────────────────────────")
    print(f"  Match exato        : {matched_exact:>5} ({pct(matched_exact, total)})")
    print(f"  Match substring    : {matched_sub:>5} ({pct(matched_sub, total)})")
    print(f"  Sem match          : {unmatched:>5} ({pct(unmatched, total)})")
    print(f"  TOTAL              : {total:>5}")

    print()
    print("─── Acerto do TIPO (sobre linhas que matched + têm ground truth) ──")
    type_evaluated = type_correct + type_wrong
    print(f"  ✓ Acertos          : {type_correct:>5} ({pct(type_correct, type_evaluated)} sobre avaliáveis)")
    print(f"  ✗ Erros            : {type_wrong:>5} ({pct(type_wrong, type_evaluated)} sobre avaliáveis)")
    print(f"  ? Sem ground truth : {type_unknown:>5}")

    print()
    print("─── Acerto da SECURITY (idem) ─────────────────────────────────")
    sec_evaluated = sec_correct + sec_wrong
    print(f"  ✓ Acertos          : {sec_correct:>5} ({pct(sec_correct, sec_evaluated)} sobre avaliáveis)")
    print(f"  ✗ Erros            : {sec_wrong:>5} ({pct(sec_wrong, sec_evaluated)} sobre avaliáveis)")
    print(f"  ? Sem ground truth : {sec_unknown:>5}")

    if unmatched_samples and args.show_misses:
        print()
        print(f"─── {len(unmatched_samples)} amostras sem match ──────────────────────────────")
        for s in unmatched_samples:
            tail = []
            if s["actualType"]: tail.append(f"type={s['actualType']}")
            if s["actualSec"]:  tail.append(f"sec={s['actualSec']}")
            tail_str = (" · " + ", ".join(tail)) if tail else ""
            print(f"  • {s['desc'][:120]}{tail_str}")

    if type_misses:
        print()
        print(f"─── {len(type_misses)} amostras com TIPO errado ─────────────────────")
        for m in type_misses:
            print(f"  pred={m['pred']:<14} actual={m['actual']:<14} score={m['score']}")
            print(f"     desc: {m['desc'][:110]}")
            if not m["matchedKey"] == m["desc"]:
                print(f"     matched key: {m['matchedKey'][:110]}")

    if sec_misses:
        print()
        print(f"─── {len(sec_misses)} amostras com SECURITY errada ──────────────────")
        for m in sec_misses:
            print(f"  pred={m['predName'][:30]:<30} ({m['pred'][:8]}…)  actual={m['actual'][:8]}…  score={m['score']}")
            print(f"     desc: {m['desc'][:110]}")
            if m["matchedKey"] != m["desc"]:
                print(f"     matched key: {m['matchedKey'][:110]}")

    # Top unmatched descriptions to inform a follow-up training pass.
    if miss_by_desc:
        print()
        print("─── Top 15 descrições sem match (candidatas a regra futura) ──")
        for desc, n in miss_by_desc.most_common(15):
            print(f"  {n:>4}× {desc}")


if __name__ == "__main__":
    main()
