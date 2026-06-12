# Code Review — tests/ folder (ad-hoc simulation scripts) [v2]

## Summary
- Files reviewed: `tests/detail_t9.py` (258), `tests/simulate_recalculo.py` (211), `tests/test_bayesian_scenarios.py` (385)
- `tests/scenarios/**`: T9/ and T10/ with Mongo-shaped JSON fixtures + pipeline artefacts. Example test data per convention.
- Total findings: 6
- Breakdown: CRITICAL: 0 / HIGH: 0 / MEDIUM: 2 / LOW: 4

## Systematic checks performed
- Index coverage: N/A (pure in-memory sims, no Mongo access in scope)
- File write safety: 0 open-w sites; 0 flagged
- get_company_filter enforcement: 0 routes in scope
- valid_wallet_ids call-sites: 0 in scope
- str(exc) leakage sites: 0
- Unguarded ObjectId() sites: 0 (IDs already strings)
- Unvalidated int()/float() on request args: 0 (no request context)

## Findings

### [MEDIUM] `[0]`-indexed list comprehension IndexError if WITHHOLDING_TAX absent — tests/detail_t9.py:192-193
**Category:** correctness
**Recommendation:** Guard with `if wt_flags:` before `[0]` access.

### [MEDIUM] Unsafe `mc["bestFix"]` dereference when no fix found — tests/detail_t9.py:199-203
**Category:** error-handling
**Recommendation:** `best = mc.get("bestFix")` + None guard, matching pattern at `test_bayesian_scenarios.py:318-319`.

### [LOW] `reduction` formula loses sign when corrected gap flipped — tests/simulate_recalculo.py:199-200
**Category:** financial
**Impact:** Overcorrection prints "Gap fechado: 79.9%" while sign flipped.
**Recommendation:** Report `flipped = (new_gap_pct * gap_pct_orig) < 0` alongside magnitude.

### [LOW] Hardcoded tolerance `0.01` contradicts `cfg['tolerance']` — tests/simulate_recalculo.py:205
**Category:** duplication

### [LOW] Scenario row label slicing `name[:5]` breaks header/label alignment — tests/test_bayesian_scenarios.py:353
**Category:** correctness
**Recommendation:** `tag, _, title = name.partition(": ")`.

### [LOW] `simulate_recalculo.py` has no scenario identifier / inputs not tagged — tests/simulate_recalculo.py:9-21
**Category:** duplication
**Recommendation:** Comment source scenario or load from JSON fixture.

## Patterns observed (cross-file themes)
- **Defensive `bestFix` handling inconsistent:** `test_bayesian_scenarios.py` guards None (line 319); `detail_t9.py` does not (line 199).
- **Tolerance and tier-classification constants redefined inline:** `detail_t9.py:216-218` hand-copies `nav_flags`/`cash_always`/`cash_conditional` sets that live inside `validate_fix` (bayesian.py:928-932); `simulate_recalculo.py:101` redefines `wallet_types` inline. Future production changes leave scripts reporting wrong labels.
