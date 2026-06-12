"""Drastically thin the reinforcement table to a minimal set per
``(securityId, beehusTransactionType)`` pair.

Rationale: the operator re-adds reinforcements through normal day-to-day
use, so an aggressive cull is cheap — a dropped textual variant simply
gets re-learned the next time it appears. What we keep is the dominant,
most-reliable form for each (security, type), which is the one the
operator sees most often anyway.

Policy: within each ``(securityId, beehusTransactionType)`` group keep the
top ``--max-per-pair`` rules ranked by ``hits`` (desc), breaking ties by
shorter key first (broader Tier-2 substring reach) then lexically for
determinism. The dropped rules' ``hits`` are folded into the #1 survivor
of their group, and ``lastSeenAt`` keeps the latest seen.

Groups keyed on an empty securityId (type-only fallback rules) or empty
type are still grouped on whatever they have, so single-member groups —
which all the broad fallbacks are — pass through untouched.

Dry-run by default; pass ``--apply`` to write (atomic, timestamped
``.bak`` like the other migration scripts)."""
import argparse
import json
import os
import shutil
import sys
from collections import defaultdict
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from db import atomic_write_json  # noqa: E402

FILE = os.path.join(ROOT, "data", "identify_transactions_reinforcements.json")


def rank_key(item):
    """Pick the best single representative for a (security, type) group.

    Priority:
      1. Most structured shape first — measured by the count of ``" - "``
         separators. The system-built ``TIPO - CLASSE - id - nome`` form
         has ~3; one-off broker lines (`TED TER BCO ...`, `BOUGHT|...`,
         `RESGATE DE COTAS NO FUNDO ...`, `PGTO JUROS 760199 |...`) have
         0-1. The separator count is reliable where the ``securityMainId``
         field is not (it is often formatted differently than it appears
         in the key).
      2. Tie-break: key embeds the security's own ``securityMainId``
         (CNPJ / ISIN / CETIP / títulos code).
      3. Then most ``hits`` (dominant observed form).
      4. Then shorter key (broader Tier-2 substring reach), then lexical.
    """
    key, rule = item
    seps = key.count(" - ")
    main_id = (rule.get("securityMainId") or "").strip()
    has_main = bool(main_id) and main_id in key
    return (-seps,
            0 if has_main else 1,
            -int(rule.get("hits") or 0), len(key), key)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--max-per-pair", type=int, default=1,
                   help="How many rules to keep per (securityId, type). Default 1.")
    p.add_argument("--apply", action="store_true",
                   help="Write the reduced file. Without it, dry-run report only.")
    args = p.parse_args()

    with open(FILE, encoding="utf-8") as f:
        doc = json.load(f)
    rules = doc["rules"]

    groups = defaultdict(list)
    for k, v in rules.items():
        groups[(v.get("securityId") or "", v.get("beehusTransactionType") or "")].append((k, v))

    new_rules = {}
    dropped = 0
    drop_samples = []
    for (sid, typ), items in groups.items():
        items.sort(key=rank_key)
        keep = items[:max(1, args.max_per_pair)]
        drop = items[max(1, args.max_per_pair):]

        head_key, head_rule = keep[0]
        head_rule = dict(head_rule)
        for dk, dv in drop:
            head_rule["hits"] = int(head_rule.get("hits") or 0) + int(dv.get("hits") or 0)
            ls = max((head_rule.get("lastSeenAt") or ""), (dv.get("lastSeenAt") or ""))
            if ls:
                head_rule["lastSeenAt"] = ls
            dropped += 1
            if len(drop_samples) < 30:
                drop_samples.append((dk, head_key))
        new_rules[head_key] = head_rule
        for k, v in keep[1:]:
            new_rules[k] = v

    print(f"Rules: {len(rules)}  ->  {len(new_rules)}   (dropped {dropped})")
    print(f"(securityId, type) pairs: {len(groups)}   max-per-pair: {args.max_per_pair}")
    if drop_samples:
        print("\nSample drops (folded into survivor):")
        for dk, hk in drop_samples:
            print(f"  - {dk[:78]!r}")
            print(f"      -> {hk[:78]!r}")

    if not args.apply:
        print("\nDry run. Re-run with --apply to write.")
        return

    doc["rules"] = new_rules
    stamp = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    shutil.copyfile(FILE, f"{FILE}.{stamp}.bak")
    atomic_write_json(FILE, doc)
    print(f"\nSaved. Backup .{stamp}.bak")


if __name__ == "__main__":
    main()
