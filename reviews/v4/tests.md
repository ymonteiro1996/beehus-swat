# Code Review — tests/ [v4 anti-fragile]

## Summary
- Files reviewed: detail_t9.py, simulate_recalculo.py, test_bayesian_scenarios.py, tests/scenarios/T9/**, tests/scenarios/T10/**
- **New findings: 5**
- Prior confirmed: 8
- Prior refuted: 0
- Breakdown (new only): CRITICAL: 1 / HIGH: 1 / LOW: 3

## New findings

### [CRITICAL] Real production data committed to repo as "test fixture" — tests/scenarios/T10/**
**Category:** security
**Evidence:**
- `tests/scenarios/T10/wallet.json:2-4` — `"_id": "69b99c5584ea99045a762d1d"`, `"name": "SZES - 2831028"`, `"companyId": "10000000000000"`, `"entityId": "67cf6a5c71f5e8c88f760505"`
- `tests/scenarios/T10/securities.json:3-50` — real Mongo ObjectIds + real security names ("NTN-B Ago/2050", "CRA JBS Pós-fixado 15/Set/2037", "Newave Energia I Advisory A Multiestratégia FIP", "XP Crédito AGRO")
- `tests/scenarios/T10/transactions.json:7-27` — real `"userId": "692ee961a6d7a25df19d6a82"`, real bucket UUID `75d0ae06-aa3d-4edb-9a52-012865eb8611`, real descriptions ("FEE FIXO - COBRANÇA REF. MAR26", "TRANSFERÊNCIA ENVIADA...")
- `tests/scenarios/T10/cash_accounts.json` — full month-long daily cash trail
- `tests/scenarios/T10/metadata.json:6` — `"capturedAt": "2026-04-17T15:06:13.253964+00:00"` with note confirming snapshot
- `.gitignore` does NOT cover `tests/scenarios/`
- User memory `feedback_no_example_securities.md`: "Do not reference specific securities from test/example payloads"

**Impact:** The v3 spec assumption "Hardcoded WALLET_IDS and sample payloads in `tests/scenarios/*` are example test data" is VIOLATED for T10. T10 is a REAL captured production fund snapshot (wallet `SZES - 2831028`, companyId `10000000000000`, real user UUID, real Mongo ObjectIds, real captured timestamps). T9 IS synthetic (`"T9-all-tiers"`, `s9`, `s10`); T10 is the outlier. If repo is pushed public/mirrored/used for training, customer data leaks. CompanyId repeats across every file → re-identifying the fund is trivial.

**Recommendation:**
1. Delete `tests/scenarios/T10/` from working tree (or regenerate synthetic).
2. If regression value needed, rewrite with `walletId: "T10-cash-only"`, synthetic securities, companyId `0`.
3. Add `tests/scenarios/T*-real/` or broader `tests/scenarios/T10/` to `.gitignore`.
4. If ever committed historically, `git filter-repo` to purge.
5. Add capture-guard to the script that produced T10.

### [HIGH] Non-deterministic PASS/FAIL via unseeded `random` in MC pipeline — tests/test_bayesian_scenarios.py:1-385
**Category:** testing
**Evidence:** `pages/bayesian.py:661, 683, 700` call `random.uniform`/`random.choices`/`random.gauss` inside `_sample_tier2_impact`. `optimize_with_validation` (line 317 of test) fans through `score_combinations_mc` iterating `n_samples=200` with fresh PRNG draws. Test never calls `random.seed()`. T4/T5/T6/T9 all contain Tier-2 flags → `bestFix.flags` and `posterior` fluctuate run-to-run.
**Impact:** Comparator at 332 uses `sorted(flags) != sorted(spec["expected_flags"])` (strict set equality). PASS ↔ FAIL flip without code change. Developer validating refactor gets intermittent failures with no root cause. T6's `expected_flags: ["WRONG_TRANSACTION_VALUE"]` especially at risk.
**Recommendation:** Add `random.seed(42)` at module top. OR thread `seed` kwarg through `optimize_with_validation` → `score_combinations_mc` → `_sample_tier2_impact`. (`generate_report_sample.py:752` confirms seeding IS project's pattern — tests forgot.)
**Extends v3-3:** v3 noted "runner always exits 0". This is sharper — even with correct exit code, the fail count is non-reproducible.

### [LOW] Name-slicing produces garbled column header for T1–T9 vs T10 — tests/test_bayesian_scenarios.py:353
**Category:** correctness
**Evidence:** `name[:5]` on `"T1: MISSING_TRANSACTION only"` = `"T1: M"` (duplicates first payload letter); `"T10: …"[:5]` = `"T10: "` (clean). Two columns overlap for T1–T9, misaligned for T10.
**Recommendation:** `label, _, title = name.partition(":")`.

### [LOW] Duplicate scenario identifier "T10" — tests/test_bayesian_scenarios.py:288 vs tests/scenarios/T10/metadata.json:2-8
**Category:** convention
**Evidence:** Inline T10 = "Insufficient coverage (gap >> flags)", `gap_cash=-500000, former_nav=2000000`. Folder T10 = "cash mismatch = gap e nenhum FLAG", `gapCash=-900`. Two different scenarios same name.
**Impact:** "T10 failed" in any report is ambiguous; can't promote folder fixture into inline parametrized case without rename.
**Recommendation:** Rename fixture folder to `tests/scenarios/R1-cash-only/` (or inline to "T11").

### [LOW] Fixture `impact: 500` in T4 silently ignored by library — tests/test_bayesian_scenarios.py:142-144
**Category:** duplication
**Evidence:** Test supplies `"impact": 500` but `pages/bayesian.py:364-372` (`_compute_signed_impact`) recomputes `amt_diff * (pu - true_exec) = -2000 * 0.25 = -500`. Explicit `impact` field is documentation-only. Same pattern recurs in T5, T6, T9.
**Recommendation:** Drop `"impact"` field from `step3_3` stubs OR add note "recomputed by library".

## Patterns observed
- **Fixture vs library impedance mismatch** — fixtures carry precomputed `impact`, `noFixPosterior`, `posterior: 1.0` that library either recomputes or produces non-deterministically. Fixtures are trophies from one lucky run, not invariants. Combined with v3 "no assertions" finding: JSON fixtures are decorative not contractual.
- **Test infrastructure ignores project-established seeding pattern** — generate_report_sample.py seeds; tests don't.

**Convergence note:** 5 new findings, above the "<3 new = converged" bar. CRITICAL T10 real-data leak is the anti-fragile win — required opening a fixture JSON v3 took at face value per the spec's "sample payloads are OK" rule.
