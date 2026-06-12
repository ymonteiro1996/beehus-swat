"""Prune reinforcement rules that a *more generic* rule already covers.

A rule ``K`` is redundant when some other stored rule ``J`` exists such
that:

  * ``J`` is a substring of ``K`` (so a description that hits ``K`` also
    hits ``J`` via the Tier-2 substring path in ``_match_against_rules``),
  * ``J`` is Tier-2-eligible (long/multi-word enough to match as a
    substring — same gate the runtime uses), and
  * ``J`` carries the **same** ``securityId`` *and* ``beehusTransactionType``
    as ``K``.

In that case ``K`` adds nothing: removing it leaves the generic ``J`` to
answer the same descriptions with the same result. This is the "keep only
the text that represents generically" cleanup — it drops noise like the
trailing ``(EXPLOSAO CARTEIRA ...)`` annotations, ``ADIANTAMENTO RESGATE
...`` vs ``RESGATE ...``, and broker line-length truncations.

Safety: before dropping anything the script *simulates the real lookup*
(exact → longest-substring Tier-2) against the survivor set and refuses to
drop any key whose post-prune resolution would land on a different
``securityId``/type. The dropped rule's ``hits`` are folded into the
survivor the lookup actually resolves to (following chains like
``... (E1) (E2)`` → ``... (E1)`` → base), and ``lastSeenAt`` keeps the
later of the two.

Dry-run by default; pass ``--apply`` to write (atomic, with a timestamped
``.bak`` like the other migration scripts).
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
from reinforcement_keys import normalize_reinforcement_key  # noqa: E402

FILE = os.path.join(ROOT, "data", "identify_transactions_reinforcements.json")

# Mirror of the runtime Tier-2 eligibility gate in pages/beehus_console.py.
_MIN_LEN = 10
_MIN_MULTIWORD = 6


def tier2_eligible(k):
    if not k:
        return False
    if len(k) >= _MIN_LEN:
        return True
    return (" " in k) and len(k) >= _MIN_MULTIWORD


def find_redundant(rules):
    """Return {redundant_key: generic_substring_key} for the immediate
    (longest shorter) generic match. Only same securityId + type."""
    keys = list(rules)
    out = {}
    for K in keys:
        rk = rules[K]
        best = None
        for J in keys:
            if J == K or len(J) >= len(K):
                continue
            if J in K and tier2_eligible(J):
                rj = rules[J]
                if (rj.get("securityId") == rk.get("securityId")
                        and rj.get("beehusTransactionType") == rk.get("beehusTransactionType")):
                    if best is None or len(J) > len(best):
                        best = J
        if best is not None:
            out[K] = best
    return out


def resolve(incoming_key, survivors, eligible):
    """Replicate _match_against_rules: exact, else longest-substring Tier-2.
    Returns (rule, matched_key) or (None, None)."""
    if incoming_key in survivors:
        return survivors[incoming_key], incoming_key
    best_key = best_rule = None
    for rk, rv in eligible:
        if rk in incoming_key and (best_key is None or len(rk) > len(best_key)):
            best_key, best_rule = rk, rv
    return (best_rule, best_key) if best_rule else (None, None)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true",
                   help="Write the pruned file. Without it, dry-run report only.")
    args = p.parse_args()

    with open(FILE, encoding="utf-8") as f:
        doc = json.load(f)
    rules = doc["rules"]

    redundant = find_redundant(rules)
    survivors = {k: v for k, v in rules.items() if k not in redundant}
    eligible = [(k, v) for k, v in survivors.items() if tier2_eligible(k)]

    # Safety gate + resolve the actual fold-target (may chain past the
    # immediate generic if that one is itself being dropped).
    unsafe = []
    fold = {}  # redundant_key -> survivor_key it resolves to
    for K in redundant:
        rk = rules[K]
        rule, mk = resolve(normalize_reinforcement_key(K), survivors, eligible)
        same = (rule is not None
                and rule.get("securityId") == rk.get("securityId")
                and rule.get("beehusTransactionType") == rk.get("beehusTransactionType"))
        if not same:
            unsafe.append((K, mk, rule.get("securityId") if rule else None))
        else:
            fold[K] = mk

    print(f"Rules: {len(rules)}")
    print(f"Redundant (generic already covers): {len(redundant)}")
    print(f"Unsafe (resolution differs — NOT dropped): {len(unsafe)}")
    for K, mk, sid in unsafe:
        print(f"  ! KEEP {K!r}\n      would resolve to {mk!r} (sec={sid})")

    print("\nDrops:")
    for K, mk in sorted(fold.items(), key=lambda kv: -len(kv[0])):
        print(f"  - {rules[K].get('hits', 0):>4}h  {K!r}")
        print(f"         -> {mk!r}")

    if not args.apply:
        print(f"\nDry run. {len(fold)} would be dropped "
              f"({len(rules)} -> {len(rules) - len(fold)}). Re-run with --apply.")
        return

    new_rules = dict(survivors)
    for K, mk in fold.items():
        if mk in new_rules:
            tgt = new_rules[mk]
            tgt["hits"] = int(tgt.get("hits") or 0) + int(rules[K].get("hits") or 0)
            ls = max((tgt.get("lastSeenAt") or ""), (rules[K].get("lastSeenAt") or ""))
            if ls:
                tgt["lastSeenAt"] = ls
    doc["rules"] = new_rules

    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    shutil.copyfile(FILE, f"{FILE}.{stamp}.bak")
    atomic_write_json(FILE, doc)
    print(f"\nSaved: {len(rules)} -> {len(new_rules)}  (backup .{stamp}.bak)")


if __name__ == "__main__":
    main()
