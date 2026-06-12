# Code Review — tests/ [v3]

## Summary
- Files reviewed: detail_t9.py, simulate_recalculo.py, test_bayesian_scenarios.py; tests/scenarios/** noted structurally
- Total findings: 8
- Breakdown: CRITICAL: 0 / HIGH: 0 / MEDIUM: 3 / LOW: 5

## Systematic checks performed
- Index coverage: N/A
- File write safety: 0 open-w
- get_company_filter: N/A
- valid_wallet_ids: 0
- str(exc) leakage: 0
- Unguarded ObjectId(): 0
- Unvalidated int()/float(): 0
- Docs ↔ code cross-check: `docs/CONCILIACAO_RECALCULO.md:108` says `returnNavPerShare = navPerShare / formerNavPerShare − 1`; simulate_recalculo.py:141-142 uses compounding shortcut `ratio * (1 + return_nav_ps) - 1` — see MEDIUM
- Security claims verified via Bash: 0 (no security surface)

## Findings

### [MEDIUM] Formula 9 divergence from documented Recálculo spec — tests/simulate_recalculo.py:140-151
**Category:** financial / docs-consistency
**Impact:** Doc defines `navPerShare / formerNavPerShare - 1`; simulation composes delta on previous return. Equal only when share count unchanged and `oldNav = formerNavPerShare × shares` — implicit never-stated assumption.
**Recommendation:** Rewrite using documented ratio (track shares explicitly) or add comment block at line 140 stating compounding identity.

### [MEDIUM] Hand-rolled walkthroughs diverge from library without assertions — tests/detail_t9.py:97-100, 216-232
**Category:** testing / financial
**Impact:** z-score recomputed inline; `nav_flags`/`cash_always`/`cash_conditional` sets hand-copied from bayesian.py:928-932. Stale labels silently printed next to correct library outputs on any rename.
**Recommendation:** Import constants from `pages.bayesian`; assert `extract_factors` z-score matches.

### [MEDIUM] No assertions — "test_" prefix is misleading — tests/test_bayesian_scenarios.py:1-385
**Category:** testing
**Impact:** File named `test_*.py`; runner prints PASS/FAIL but always exits 0. A T10 `expected_valid=False` flip isn't enforced. Also, no top-level `test_*` function → pytest collects nothing, silently "passes".
**Recommendation:** `sys.exit(1 if failed else 0)` OR rename `simulate_bayesian_scenarios.py`.

### [LOW] Unguarded `[0]` indexing on WITHHOLDING_TAX filter — tests/detail_t9.py:192-193
**Category:** correctness
**Recommendation:** Guard `wt_flags = [f for f in actionable if f['flag']=='WITHHOLDING_TAX']; if wt_flags: ...`.

### [LOW] Unguarded `mc["bestFix"]` deref under no-fix-possible — tests/detail_t9.py:199-203
**Category:** correctness
**Recommendation:** `if best is None: print("(no actionable flags — MC fell back)"); sys.exit(0)`.

### [LOW] Separator sync: `len(hdr) - 2` vs hardcoded `88` — tests/detail_t9.py:157 vs :182
**Category:** duplication

### [LOW] T8 "Overlapping" scenario has no `expected_flags` assertion — tests/test_bayesian_scenarios.py:221-235
**Category:** testing
**Recommendation:** Add `"expected_flags_contains": ["MISSING_TRANSACTION"]`.

### [LOW] Stream-of-consciousness comment blocks encode confused sign derivations — tests/test_bayesian_scenarios.py:170-178, 236-257
**Category:** maintainability

## Patterns observed (cross-file themes)
- **Hand-kept duplication of domain sets** — `cash_always`/`nav_flags`/`cash_conditional` re-declared in detail_t9.py:216-218 rather than imported from `pages.bayesian`.
- **Walkthrough scripts named like tests** — `test_bayesian_scenarios.py` claims test semantics (PASS/FAIL, `expected_*`) but always exits 0. Other two files honestly named. Align naming or wire `sys.exit`.
