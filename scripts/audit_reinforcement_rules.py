"""Audit reinforcement rules against recent ground truth.

Picks a recent date window from `db.transactions`, runs each row
through `_lookup_reinforcement`, and tallies per-rule:

  - hits         — number of recent rows the rule matched
  - typeOK       — rows where the rule's beehusTransactionType matched
                   the row's stored type
  - typeMiss     — rows where it disagreed
  - secOK        — same, for securityId
  - secMiss      — same, for securityId
  - majorityType — what type the majority of recent rows actually has
  - majoritySec  — same, for security
  - suggestion   — `update_type`, `update_security`, `disable`, or
                   `keep` based on simple thresholds

The output is sorted by impact (rules with most misses first), so the
operator can clean up the highest-leverage entries via the
Configurações > Regras de Reforço UI (or pass `--apply` to let this
script update the file directly using the majority rule's values, with
a guard against low-sample suggestions).
"""
import argparse
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import db, atomic_write_json  # noqa: E402
from pages.beehus_console import (  # noqa: E402
    _lookup_reinforcement,
    _match_against_rules,
    _normalize_reinforcement_key,
)
from reinforcement_keys import strip_negating_prefix  # noqa: E402


def _lookup_with_cached_rules(description, rules_snapshot):
    """Same logic as `pages.beehus_console._lookup_reinforcement`, but
    operates on an in-memory snapshot of the rules dict instead of
    re-reading the JSON file on every call.

    The on-disk file lives on a OneDrive-synced path; concurrent
    reads while the sync agent is mid-write can occasionally return a
    half-written body, which `_load_reinforcements` then logs as
    "reinforcements unreadable" and silently falls back to an empty
    dict. That silently dropped hits from this audit, so we read once
    up front and reuse the snapshot for every lookup.
    """
    if not description:
        return None
    key = _normalize_reinforcement_key(description)
    if not key:
        return None
    inner_key = strip_negating_prefix(key)
    if inner_key:
        inner_rule, inner_match, inner_score = _match_against_rules(inner_key, rules_snapshot)
        if inner_rule is not None:
            taxed_rule = dict(inner_rule)
            taxed_rule["beehusTransactionType"] = "taxes"
            return {"rule": taxed_rule,
                    "score": round(min(inner_score, 0.99), 4),
                    "exact": False,
                    "matched": inner_match}
    rule, match_key, score = _match_against_rules(key, rules_snapshot)
    if rule is None:
        return None
    is_exact = (match_key == key) and (score == 1.0)
    return {"rule": dict(rule),
            "score": round(score, 4) if not is_exact else 1.0,
            "exact": is_exact,
            "matched": match_key}

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--entity-id", default="",
                   help="Audit a single entity (recommended; rules with the same key can have different right answers across entities).")
    p.add_argument("--from", dest="from_date", required=True,
                   help="liquidationDate >= (YYYY-MM-DD)")
    p.add_argument("--to", dest="to_date", required=True,
                   help="liquidationDate <= (YYYY-MM-DD)")
    p.add_argument("--min-hits", type=int, default=5,
                   help="Only audit rules hit by at least N rows in the window")
    p.add_argument("--miss-threshold", type=float, default=0.30,
                   help="Flag a rule when its miss rate (on hits with ground truth) is >= this")
    p.add_argument("--top", type=int, default=40,
                   help="How many flagged rules to print in detail")
    p.add_argument("--apply", action="store_true",
                   help="Auto-update flagged rules (majority type/security) and disable rules whose majority class is 'no signal'.")
    args = p.parse_args()

    query = {
        "liquidationDate": {"$gte": args.from_date, "$lte": args.to_date},
        "trashed":         {"$ne": True},
    }
    if args.entity_id:
        query["entityId"] = args.entity_id

    rows = list(db.transactions.find(
        query,
        {"description": 1, "securityId": 1, "beehusTransactionType": 1},
    ))
    print(f"Loaded {len(rows)} transactions ({args.from_date} → {args.to_date}"
          + (f", entity {args.entity_id}" if args.entity_id else ", all entities") + ")")
    if not rows:
        return

    # Snapshot the rules dict ONCE up front so every lookup hits the
    # same view. The on-disk file lives on OneDrive and concurrent
    # reads can occasionally race the sync agent's writes — when that
    # happens `_load_reinforcements` falls back to an empty dict and
    # we silently undercount hits for the rules whose lookups were
    # unlucky enough to land on a half-written read.
    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        rules_snapshot = (json.load(f) or {}).get("rules") or {}
    print(f"Loaded {len(rules_snapshot)} reinforcement rules from disk")

    # by_rule[matched_key] = {
    #   "rule":      <stored rule snapshot>,
    #   "hits":      total rows that matched it,
    #   "typeOK":    ...,
    #   "typeMiss":  ...,
    #   "secOK":     ...,
    #   "secMiss":   ...,
    #   "actualType": Counter(...),  # what the matched rows actually had
    #   "actualSec":  Counter(...),
    # }
    by_rule = defaultdict(lambda: {
        "rule": None, "hits": 0,
        "typeOK": 0, "typeMiss": 0, "typeUnknown": 0,
        "secOK":  0, "secMiss":  0, "secUnknown":  0,
        "actualType": Counter(), "actualSec": Counter(),
    })

    for t in rows:
        hit = _lookup_with_cached_rules(t.get("description") or "", rules_snapshot)
        if hit is None:
            continue
        bucket = by_rule[hit["matched"]]
        bucket["hits"] += 1
        if bucket["rule"] is None:
            bucket["rule"] = hit["rule"]
        pred_type = (hit["rule"] or {}).get("beehusTransactionType") or ""
        pred_sec  = (hit["rule"] or {}).get("securityId") or ""
        actual_type = t.get("beehusTransactionType") or ""
        actual_sec  = str(t.get("securityId") or "")

        if actual_type:
            bucket["actualType"][actual_type] += 1
            if pred_type and pred_type == actual_type:
                bucket["typeOK"] += 1
            elif pred_type and pred_type != actual_type:
                bucket["typeMiss"] += 1
            # If pred_type is empty (sec-only rule), there's no claim to verify.
        else:
            bucket["typeUnknown"] += 1
        if actual_sec:
            bucket["actualSec"][actual_sec] += 1
            if pred_sec and pred_sec == actual_sec:
                bucket["secOK"] += 1
            elif pred_sec and pred_sec != actual_sec:
                bucket["secMiss"] += 1
        else:
            bucket["secUnknown"] += 1

    flagged = []
    for key, b in by_rule.items():
        if b["hits"] < args.min_hits:
            continue
        type_evaluated = b["typeOK"] + b["typeMiss"]
        sec_evaluated = b["secOK"] + b["secMiss"]
        type_miss_rate = (b["typeMiss"] / type_evaluated) if type_evaluated else 0.0
        sec_miss_rate  = (b["secMiss"]  / sec_evaluated)  if sec_evaluated  else 0.0
        if max(type_miss_rate, sec_miss_rate) < args.miss_threshold:
            continue

        # Majority answers — used both for the suggestion column and the
        # `--apply` auto-update path.
        maj_type, maj_type_n = ("", 0)
        if b["actualType"]:
            maj_type, maj_type_n = b["actualType"].most_common(1)[0]
        maj_sec, maj_sec_n = ("", 0)
        if b["actualSec"]:
            maj_sec, maj_sec_n = b["actualSec"].most_common(1)[0]

        # Suggestion logic:
        # - If type miss rate is high but majority has a clear winner →
        #   update_type to the majority.
        # - If security miss rate is high with a clear majority → update_security.
        # - If no evaluable rows (everything unknown) → flag as low signal,
        #   but don't auto-disable (might just be a new period with
        #   classification still pending).
        suggestions = []
        if type_miss_rate >= args.miss_threshold and maj_type:
            maj_share = maj_type_n / type_evaluated if type_evaluated else 0
            if maj_share >= 0.70:
                suggestions.append(("update_type", maj_type))
        if sec_miss_rate >= args.miss_threshold and maj_sec:
            maj_share = maj_sec_n / sec_evaluated if sec_evaluated else 0
            if maj_share >= 0.70:
                suggestions.append(("update_security", maj_sec))
        if not suggestions:
            suggestions.append(("review_manually", ""))

        flagged.append({
            "key": key,
            "rule": b["rule"],
            "hits": b["hits"],
            "typeOK": b["typeOK"],
            "typeMiss": b["typeMiss"],
            "typeRate": type_miss_rate,
            "secOK": b["secOK"],
            "secMiss": b["secMiss"],
            "secRate": sec_miss_rate,
            "majorityType": maj_type,
            "majorityTypeN": maj_type_n,
            "majoritySec": maj_sec,
            "majoritySecN": maj_sec_n,
            "suggestions": suggestions,
        })

    flagged.sort(key=lambda r: -(r["typeMiss"] + r["secMiss"]))
    print(f"  {len(flagged)} rules flagged "
          f"(min-hits={args.min_hits}, miss-threshold={args.miss_threshold:.0%})")
    print()

    if not flagged:
        print("Nothing to clean up — every audited rule is performing OK.")
        return

    # Load security names for the report.
    from bson import ObjectId
    sec_ids = set()
    for r in flagged:
        if r["rule"]:
            sec_ids.add(str(r["rule"].get("securityId") or ""))
        if r["majoritySec"]:
            sec_ids.add(r["majoritySec"])
    sec_ids.discard("")
    sec_names = {}
    oids = []
    for sid in sec_ids:
        try:
            oids.append(ObjectId(sid))
        except Exception:
            pass
    if oids:
        for s in db.securities.find({"_id": {"$in": oids}}, {"beehusName": 1}):
            sec_names[str(s["_id"])] = s.get("beehusName") or ""

    def _sec_label(sid):
        return sec_names.get(sid, sid[:10] + "…" if sid else "(none)")

    print(f"Top {min(args.top, len(flagged))} flagged rules (by absolute misses):")
    for r in flagged[: args.top]:
        cur_type = (r["rule"] or {}).get("beehusTransactionType") or "∅"
        cur_sec  = _sec_label((r["rule"] or {}).get("securityId") or "")
        print()
        print(f"  KEY: {r['key'][:90]}")
        print(f"    hits={r['hits']}  type={cur_type} → maj {r['majorityType'] or '∅'} ({r['majorityTypeN']})  "
              f"sec={cur_sec[:30]} → maj {_sec_label(r['majoritySec'])[:30]} ({r['majoritySecN']})")
        if r["typeOK"] + r["typeMiss"] > 0:
            print(f"    TIPO    {r['typeOK']:>4} OK / {r['typeMiss']:>4} miss  ({r['typeRate']:.0%})")
        if r["secOK"] + r["secMiss"] > 0:
            print(f"    SECURITY {r['secOK']:>4} OK / {r['secMiss']:>4} miss  ({r['secRate']:.0%})")
        for action, value in r["suggestions"]:
            if action == "update_type":
                print(f"    → suggestion: update type to '{value}'")
            elif action == "update_security":
                print(f"    → suggestion: update security to '{_sec_label(value)}'")
            else:
                print(f"    → suggestion: review manually (no clear winner)")

    if not args.apply:
        print()
        print("Dry run. Re-run with --apply to update flagged rules to their "
              "majority class.")
        return

    # ── Apply: update rules with clear-majority suggestions ──────────
    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        state = json.load(f)
    rules = state["rules"]
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    updated_type = 0
    updated_sec = 0
    new_sec_meta_to_fetch = set()
    pending = []
    for r in flagged:
        key = r["key"]
        rule = rules.get(key)
        if not rule:
            continue
        new_type = rule.get("beehusTransactionType") or ""
        new_sec  = rule.get("securityId") or ""
        for action, value in r["suggestions"]:
            if action == "update_type":
                new_type = value
                updated_type += 1
            elif action == "update_security":
                new_sec = value
                new_sec_meta_to_fetch.add(value)
                updated_sec += 1
        pending.append((key, rule, new_type, new_sec))

    # Resolve names for any newly-attached securities.
    if new_sec_meta_to_fetch:
        oids = []
        for sid in new_sec_meta_to_fetch:
            try:
                oids.append(ObjectId(sid))
            except Exception:
                pass
        for s in db.securities.find({"_id": {"$in": oids}}, {"beehusName": 1, "mainId": 1}):
            sec_names[str(s["_id"])] = s.get("beehusName") or ""

    for key, rule, new_type, new_sec in pending:
        rule["beehusTransactionType"] = new_type
        rule["securityId"]            = new_sec
        rule["securityName"]          = sec_names.get(new_sec, "") if new_sec else ""
        rule["lastSeenAt"]            = now

    atomic_write_json(REINFORCEMENTS_FILE, state)
    print()
    print(f"Applied: {updated_type} type updates, {updated_sec} security updates.")
    print(f"Saved {REINFORCEMENTS_FILE}")


if __name__ == "__main__":
    main()
