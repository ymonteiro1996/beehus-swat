"""One-shot migration: re-key reinforcements with the new normalisation.

Why this exists:
    `reinforcement_keys.normalize_reinforcement_key` now applies token
    masking (operation codes → `<OPCODE>`, CDB codes → `CDB<CODE>`) on
    top of the previous normalisation. Entries written before that
    change have raw codes baked into their keys, so live lookups
    against the new normalisation will miss them.

What it does:
    1. Backs up `data/identify_transactions_reinforcements.json` to a
       sibling file with a timestamp.
    2. For each rule:
        a. Re-derives the key from `lastDescriptionRaw` via the new
           `normalize_reinforcement_key`. If `lastDescriptionRaw` is
           empty (legacy entries), falls back to the existing key.
        b. Applies `apply_type_overrides` to the result so historical
           mislabels (FII rendimentos tagged as `coupon`) get fixed.
        c. Merges collisions: sums `hits`, keeps the latest
           `lastSeenAt`, prefers the entry with the higher `hits` for
           non-additive fields (securityId, type, etc.).
    3. Atomically writes the new file.

Idempotent — running twice produces the same result (key remap is a
fixed function).
"""
import argparse
import json
import os
import shutil
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import atomic_write_json  # noqa: E402
from reinforcement_keys import (  # noqa: E402
    apply_type_overrides,
    normalize_reinforcement_key,
    type_override_reason,
)

REINFORCEMENTS_FILE = os.path.join(
    ROOT, "data", "identify_transactions_reinforcements.json",
)


def backup(path):
    """Sibling timestamped backup. Returns the backup path."""
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    backup_path = f"{path}.{stamp}.bak"
    shutil.copyfile(path, backup_path)
    return backup_path


def merge_rules(into, src):
    """Merge `src` rule into `into` (mutates `into`). Strategy:

    - `hits` is summed.
    - `addedAt` keeps the earlier of the two (so the rule's lineage
      reflects when it was first observed).
    - `lastSeenAt` keeps the later one.
    - Non-additive fields (securityId, securityName, securityMainId,
      beehusTransactionType, lastDescriptionRaw) are taken from
      whichever rule had MORE hits originally — the assumption being
      that the higher-hit entry is the more reliable signal."""
    def _better(a, b, key):
        # Prefer non-empty values; on tie, prefer the side with more hits.
        av, bv = a.get(key) or "", b.get(key) or ""
        if av and not bv:
            return av
        if bv and not av:
            return bv
        if int(a.get("hits") or 0) >= int(b.get("hits") or 0):
            return av or bv
        return bv or av

    merged = {
        "beehusTransactionType": _better(into, src, "beehusTransactionType"),
        "securityId":            _better(into, src, "securityId"),
        "securityName":          _better(into, src, "securityName"),
        "securityMainId":        _better(into, src, "securityMainId"),
        "lastDescriptionRaw":    _better(into, src, "lastDescriptionRaw"),
        "addedAt":               min(
            (into.get("addedAt") or src.get("addedAt") or ""),
            (src.get("addedAt") or into.get("addedAt") or ""),
        ),
        "lastSeenAt":            max(
            (into.get("lastSeenAt") or ""),
            (src.get("lastSeenAt") or ""),
        ),
        "hits":                  int(into.get("hits") or 0) + int(src.get("hits") or 0),
    }
    into.update(merged)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Write the migrated file. Without this flag the script just reports.")
    args = p.parse_args()

    if not os.path.exists(REINFORCEMENTS_FILE):
        print(f"Nothing to migrate: {REINFORCEMENTS_FILE} does not exist.")
        return

    with open(REINFORCEMENTS_FILE, encoding="utf-8") as f:
        data = json.load(f) or {}
    rules = data.get("rules") or {}
    print(f"Loaded {len(rules)} existing rules.")

    # First pass: group old rules under their new (post-masking) key. We
    # do the grouping BEFORE deciding whether to keep each new key so we
    # can detect ambiguous collisions — two old rules collapsing to the
    # same new key but pointing at *different* securityIds. Those are
    # exactly the cases the masking is supposed to flag, not silently
    # merge.
    grouped = {}  # new_key → list[(old_key, rule)]
    for old_key, rule in rules.items():
        raw = rule.get("lastDescriptionRaw") or old_key
        new_key = normalize_reinforcement_key(raw)
        if not new_key:
            new_key = old_key
        grouped.setdefault(new_key, []).append((old_key, rule))

    new_rules = {}
    remap_unchanged = 0
    remap_changed = 0
    merged_consistent = 0
    dropped_ambiguous = 0
    type_corrected = 0
    type_corrections_sample = []
    key_changes_sample = []
    ambiguous_sample = []

    for new_key, group in grouped.items():
        # Quick check: if every old rule in this group points at the same
        # securityId, the merge is safe — they're the same security
        # whose key happened to collapse under the new normalisation.
        # Different securityIds means the masking was too aggressive for
        # this prefix and we should DROP the rule rather than emit a
        # silently-merged one that picks an arbitrary winner.
        sec_ids = {(r.get("securityId") or "") for _ok, r in group}
        sec_ids.discard("")
        if len(sec_ids) > 1:
            dropped_ambiguous += 1
            if len(ambiguous_sample) < 6:
                ambiguous_sample.append({
                    "key":      new_key,
                    "secCount": len(sec_ids),
                    "sample":   list(sec_ids)[:3],
                    "olds":     [ok for ok, _ in group[:3]],
                })
            continue

        # Build merged rule: start from the highest-hit entry, then fold
        # the rest in via merge_rules (this updates hits / lastSeenAt
        # consistently while preserving the dominant-winner non-additive
        # fields).
        group.sort(key=lambda kv: -int((kv[1].get("hits") or 0)))
        head_old_key, head_rule = group[0]
        merged = dict(head_rule)
        for _old_key, rule in group[1:]:
            merge_rules(merged, rule)
            merged_consistent += 1

        # Apply type override at the new key (currently a no-op since
        # `_TYPE_OVERRIDES` is empty, but kept so future overrides take
        # effect during migration).
        current_type = merged.get("beehusTransactionType") or ""
        forced_type = apply_type_overrides(new_key, current_type)
        if forced_type != current_type:
            merged["beehusTransactionType"] = forced_type
            type_corrected += 1
            if len(type_corrections_sample) < 10:
                type_corrections_sample.append({
                    "key":   new_key,
                    "from":  current_type,
                    "to":    forced_type,
                    "why":   type_override_reason(new_key) or "",
                })

        new_rules[new_key] = merged

        for old_key, _rule in group:
            if old_key != new_key:
                remap_changed += 1
                if len(key_changes_sample) < 8:
                    key_changes_sample.append({"old": old_key, "new": new_key})
            else:
                remap_unchanged += 1

    print()
    print("─── Migration summary ─────────────────────────────────────────")
    print(f"  Rules in source        : {len(rules)}")
    print(f"  Keys unchanged         : {remap_unchanged}")
    print(f"  Keys remapped          : {remap_changed}")
    print(f"  Merged (same security) : {merged_consistent}")
    print(f"  Dropped (ambiguous)    : {dropped_ambiguous}")
    print(f"  Type-corrected rules   : {type_corrected}")
    print(f"  Rules after migration  : {len(new_rules)}")

    if ambiguous_sample:
        print()
        print("Sample ambiguous keys (dropped — masking collapsed distinct securities):")
        for s in ambiguous_sample:
            print(f"  ✗ {s['key'][:90]}")
            print(f"     {s['secCount']} distinct securityIds; sample: {s['sample']}")
            print(f"     came from old keys: {s['olds']}")

    if key_changes_sample:
        print()
        print("Sample key remaps:")
        for c in key_changes_sample:
            print(f"  OLD: {c['old'][:90]}")
            print(f"  NEW: {c['new'][:90]}")
            print()

    if type_corrections_sample:
        print("Sample type corrections:")
        for c in type_corrections_sample:
            print(f"  {c['from'] or '∅':<14} → {c['to']:<10} | {c['why']}")
            print(f"     key: {c['key'][:90]}")

    if args.apply:
        backup_path = backup(REINFORCEMENTS_FILE)
        atomic_write_json(REINFORCEMENTS_FILE, {"rules": new_rules})
        print()
        print(f"Backup: {backup_path}")
        print(f"Saved : {REINFORCEMENTS_FILE}")
    else:
        print()
        print("Dry run. Re-run with --apply to write.")


if __name__ == "__main__":
    main()
