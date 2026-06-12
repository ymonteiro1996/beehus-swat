"""Build identify_transactions_reinforcements.json entries from transaction history.

Scans `db.transactions` for a given entity (or company), groups by
(normalized description, securityId, beehusTransactionType), and creates a
reinforcement for every combination that:
  1. recurs at least `--min-hits` times in the data, AND
  2. is **unambiguous** for its description — the dominant (securityId, type)
     pair must account for at least `--dominance` (default 0.80) of the
     description's total occurrences.

Existing reinforcement keys are preserved — this script is additive only.
Run with `--apply` to write to data/identify_transactions_reinforcements.json.
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from bson import ObjectId  # noqa: E402

from db import db, atomic_write_json  # noqa: E402
from reinforcement_keys import (  # noqa: E402
    apply_type_overrides,
    normalize_reinforcement_key as normalize,
    type_override_reason,
)

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)


def load_existing():
    if not os.path.exists(REINFORCEMENTS_FILE):
        return {"rules": {}}
    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        data = json.load(f) or {}
    if not isinstance(data.get("rules"), dict):
        data["rules"] = {}
    return data


def aggregate(entity_id, company_id):
    """Return {normalized_desc: {(securityId, beehusTransactionType): {count, rawDesc}}}.

    Filters out trashed rows and rows without a description. Rows with an
    empty `securityId` or `beehusTransactionType` are **kept** — the
    pick step (`pick_candidates`) decides whether to emit a "type-only"
    or "security-only" rule when there's enough signal in the
    classified subset.
    """
    query = {
        "description": {"$exists": True, "$ne": ""},
        "trashed":     {"$ne": True},
    }
    if entity_id:
        query["entityId"] = entity_id
    if company_id:
        query["companyId"] = company_id

    by_desc = defaultdict(lambda: defaultdict(lambda: {"count": 0, "rawDesc": ""}))
    total = 0
    for doc in db.transactions.find(
        query,
        {"description": 1, "securityId": 1, "beehusTransactionType": 1},
    ):
        total += 1
        raw = doc.get("description") or ""
        norm = normalize(raw)
        if not norm:
            continue
        sid = str(doc.get("securityId") or "")
        btt = doc.get("beehusTransactionType") or ""
        bucket = by_desc[norm][(sid, btt)]
        bucket["count"] += 1
        # Keep the first raw description we see as the sample (cosmetic only —
        # used to populate `lastDescriptionRaw` so the operator recognises the
        # rule in the UI).
        if not bucket["rawDesc"]:
            bucket["rawDesc"] = raw
    return by_desc, total


def pick_candidates(by_desc, min_hits, dominance, min_informative_share):
    """Pick the dominant (securityId, type) pair per description.

    A pair `(sid, type)` is **informative** when at least one of the two
    is non-empty — a row that has neither field set in production is a
    "never classified" event and tells us nothing about what the
    description means. We compute dominance over the *informative*
    subset, not the grand total, so descriptions that are mostly
    unclassified but have a clear minority signal (e.g. all the IRRF
    rows labelled `taxes`, no `securityId`) can still produce a rule
    when the signal is strong enough.

    The `min_informative_share` guard prevents this from extrapolating
    a small classified slice onto a large unclassified mass: if fewer
    than that fraction of rows have any classification, we skip the
    description (the operator's classifications, however unanimous,
    aren't a credible sample of what the unknowns are).

    A description can produce three kinds of rule:
      • **full**     — type AND securityId set
      • **type-only**— type set, securityId empty (e.g. IRRF / IOF rules
                        that should always be `taxes` but don't bind to
                        any particular security; the operator can still
                        add a security on a case-by-case basis)
      • **sec-only** — securityId set, type empty (much rarer; happens
                        when the security is consistent but the type
                        was never labelled).
    The `apply_type_overrides` branch is preserved for future overrides
    but currently inactive (`_TYPE_OVERRIDES = []`).
    """
    # Keys shorter than this never qualify as a reinforcement.
    # The Tier-2 substring lookup in `_lookup_reinforcement` already
    # ignores stored keys shorter than `_TIER2_MIN_KEY_LEN=10`, so any
    # rule emitted here with length 4..9 is reachable *only* by Tier-1
    # (exact match on the full description). That's exactly what we
    # want for short, complete descriptions like "IRRF" (just the four
    # letters) that mean "this is a tax" when used standalone but would
    # be horrible substring matchers. We still floor at 4 chars to
    # rule out 1-3 char keys, which are almost never specific enough to
    # carry useful semantics.
    _MIN_KEY_LEN = 4

    candidates = []
    skipped_low_hits = 0
    skipped_ambiguous = 0
    skipped_unclassified = 0
    skipped_short_key = 0
    overridden = 0
    type_only_emitted = 0
    sec_only_emitted = 0
    for norm, pairs in by_desc.items():
        if len(norm) < _MIN_KEY_LEN:
            skipped_short_key += 1
            continue
        grand_total = sum(p["count"] for p in pairs.values())

        informative = {
            (sid, btt): v for (sid, btt), v in pairs.items()
            if sid or btt
        }
        informative_total = sum(p["count"] for p in informative.values())

        if not informative:
            skipped_unclassified += 1
            continue
        if grand_total > 0 and (informative_total / grand_total) < min_informative_share:
            # Most rows are unclassified — the classified slice isn't
            # a representative signal. Skip.
            skipped_unclassified += 1
            continue

        forced_type = apply_type_overrides(norm, None)
        if forced_type:
            # Override branch: collapse by securityId only over informative.
            by_sid_count = {}
            by_sid_raw = {}
            for (sid, _btt), v in informative.items():
                by_sid_count[sid] = by_sid_count.get(sid, 0) + v["count"]
                if sid not in by_sid_raw:
                    by_sid_raw[sid] = v["rawDesc"]
            top_sid, top_count = sorted(by_sid_count.items(), key=lambda kv: -kv[1])[0]
            if top_count < min_hits:
                skipped_low_hits += 1
                continue
            if informative_total > 0 and top_count / informative_total < dominance:
                skipped_ambiguous += 1
                continue
            candidates.append({
                "key":            norm,
                "rawDesc":        by_sid_raw[top_sid],
                "securityId":     top_sid,
                "type":           forced_type,
                "hits":           top_count,
                "total":          grand_total,
                "overrideReason": type_override_reason(norm) or "",
            })
            overridden += 1
            continue

        # Default branch: dominance on the (sid, type) tuple within the
        # informative subset.
        ranked = sorted(informative.items(), key=lambda kv: -kv[1]["count"])
        (sid, btt), top = ranked[0]
        if top["count"] < min_hits:
            skipped_low_hits += 1
            continue
        if informative_total > 0 and top["count"] / informative_total < dominance:
            skipped_ambiguous += 1
            continue
        if not sid and not btt:
            # Defensive — shouldn't reach here because we filtered the
            # informative set, but be explicit.
            skipped_unclassified += 1
            continue
        if not sid:
            type_only_emitted += 1
        elif not btt:
            sec_only_emitted += 1
        candidates.append({
            "key":            norm,
            "rawDesc":        top["rawDesc"],
            "securityId":     sid,
            "type":           btt,
            "hits":           top["count"],
            "total":          grand_total,
            "overrideReason": "",
        })
    candidates.sort(key=lambda c: -c["hits"])
    return (candidates, skipped_low_hits, skipped_ambiguous, overridden,
            skipped_unclassified, skipped_short_key,
            type_only_emitted, sec_only_emitted)


def fetch_security_meta(sec_ids):
    """{securityId(str): {name, main}}. Handles ids that aren't valid
    ObjectIds (rare, but the production data has both shapes)."""
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--entity-id", default="",
                        help="Filter transactions by entityId")
    parser.add_argument("--company-id", default="",
                        help="Filter transactions by companyId (combine with --entity-id if both)")
    parser.add_argument("--min-hits", type=int, default=2,
                        help="Minimum occurrences of a (desc, securityId, type) trio to create a reinforcement")
    parser.add_argument("--dominance", type=float, default=0.80,
                        help="Fraction of the description's INFORMATIVE occurrences the dominant pair must hold")
    parser.add_argument("--min-informative-share", type=float, default=0.30,
                        help="Minimum fraction of rows that must carry some classification "
                             "(type or securityId) for the description to qualify for a rule. "
                             "Guards against extrapolating a small classified slice onto a "
                             "large unclassified mass.")
    parser.add_argument("--apply", action="store_true",
                        help="Write changes to disk. Without this flag the script just reports.")
    parser.add_argument("--print-limit", type=int, default=30,
                        help="How many candidates to print in the summary")
    args = parser.parse_args()

    if not args.entity_id and not args.company_id:
        parser.error("at least one of --entity-id or --company-id is required")

    scope_bits = []
    if args.entity_id:  scope_bits.append(f"entityId={args.entity_id}")
    if args.company_id: scope_bits.append(f"companyId={args.company_id}")
    scope = ", ".join(scope_bits)
    print(f"Aggregating transactions ({scope})…")

    by_desc, total = aggregate(args.entity_id, args.company_id)
    print(f"  scanned {total} transactions; {len(by_desc)} distinct normalised descriptions")

    (candidates, skipped_low, skipped_amb, overridden,
     skipped_unclassified, skipped_short_key,
     type_only_emitted, sec_only_emitted) = pick_candidates(
        by_desc, args.min_hits, args.dominance, args.min_informative_share,
    )
    print(f"  {len(candidates)} candidate reinforcements "
          f"(skipped {skipped_low} low-hits, {skipped_amb} ambiguous, "
          f"{skipped_unclassified} mostly-unclassified, "
          f"{skipped_short_key} short-key, "
          f"{overridden} type-overridden)")
    print(f"  {type_only_emitted} type-only rules emitted (no securityId)")
    print(f"  {sec_only_emitted} sec-only rules emitted (no type)")

    if not candidates:
        return

    sec_meta = fetch_security_meta({c["securityId"] for c in candidates})

    state = load_existing()
    rules = state["rules"]
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    added = 0
    skipped_existing = 0
    new_entries = []
    for c in candidates:
        if c["key"] in rules:
            skipped_existing += 1
            continue
        meta = sec_meta.get(c["securityId"], {})
        rules[c["key"]] = {
            "beehusTransactionType": c["type"],
            "securityId":            c["securityId"],
            "securityName":          meta.get("name", ""),
            "securityMainId":        meta.get("main", ""),
            "lastDescriptionRaw":    c["rawDesc"],
            "addedAt":               now,
            "lastSeenAt":            now,
            "hits":                  c["hits"],
        }
        new_entries.append(c)
        added += 1

    print(f"  {added} new reinforcements; {skipped_existing} already existed (preserved)")
    print()
    print("Top candidates (hits / total / type · security):")
    for c in new_entries[: args.print_limit]:
        meta = sec_meta.get(c["securityId"], {})
        sec_label = meta.get("name") or meta.get("main") or c["securityId"]
        type_label = c["type"] or "(sem tipo)"
        key_preview = c["key"][:80] + ("…" if len(c["key"]) > 80 else "")
        print(f"  [{c['hits']:>4}/{c['total']:<4}] {type_label:<14} · {sec_label[:50]:<50} | {key_preview}")
    if len(new_entries) > args.print_limit:
        print(f"  … and {len(new_entries) - args.print_limit} more")

    if args.apply:
        atomic_write_json(REINFORCEMENTS_FILE, state)
        print()
        print(f"Saved {REINFORCEMENTS_FILE}")
    else:
        print()
        print("Dry run. Re-run with --apply to write the file.")


if __name__ == "__main__":
    main()
