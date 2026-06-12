"""Guard for Tier-2 reinforcement eligibility (`_is_tier2_eligible`).

Multi-word snippets (e.g. "COR JSCP", 8 chars) must match as substrings so
"COR JSCP ITUB4" inherits the type, while bare short single words ("SAQUE")
must stay exact-only to avoid false-positive substring surface. Run:

    python scripts/test_reinforcement_eligibility.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pages.beehus_console as bc  # noqa: E402


def main():
    fails = 0

    def check(label, cond):
        nonlocal fails
        if not cond:
            fails += 1
            print(f"FAIL: {label}")

    e = bc._is_tier2_eligible
    # Multi-word short keys → eligible (specific by adjacency).
    check("multi-word 'COR JSCP' eligible", e("COR JSCP"))
    check("multi-word 'IR IOF' eligible (>=6)", e("IR IOF"))
    # Single short words → NOT eligible (stay exact-only).
    check("'SAQUE' not eligible", not e("SAQUE"))
    check("'CUPOM' not eligible", not e("CUPOM"))
    check("ticker 'PPEI11' not eligible", not e("PPEI11"))
    # Long keys → eligible regardless of spaces.
    check("long single word eligible", e("AMORTIZACAO"))
    # Tiny multi-word below the multiword floor → not eligible.
    check("'A B' (3 chars) not eligible", not e("A B"))

    # End-to-end against _match_against_rules with a synthetic ruleset.
    rules = {"COR JSCP": {"beehusTransactionType": "interestOnEquity",
                          "securityId": ""}}
    elig = [(k, v) for k, v in rules.items() if bc._is_tier2_eligible(k)]
    # Substring hit on the longer description.
    rule, mk, score = bc._match_against_rules("COR JSCP ITUB4", rules, elig)
    check("'COR JSCP ITUB4' matches COR JSCP rule",
          rule is not None and rule["beehusTransactionType"] == "interestOnEquity")
    check("substring score in [0.70, 0.99]", rule is not None and 0.70 <= score <= 0.99)
    # Exact hit still 1.0.
    rule, mk, score = bc._match_against_rules("COR JSCP", rules, elig)
    check("exact 'COR JSCP' scores 1.0", rule is not None and score == 1.0)

    if fails:
        print(f"\n{fails} FAILURE(S)")
        sys.exit(1)
    print("OK — Tier-2 eligibility: multi-word snippets match, short single words protected")


if __name__ == "__main__":
    main()
