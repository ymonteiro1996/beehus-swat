# Code Review — tests/ folder (ad-hoc simulation scripts)

## Summary
- Files reviewed: `tests/detail_t9.py`, `tests/simulate_recalculo.py`, `tests/test_bayesian_scenarios.py`
- Scenarios folder: `tests/scenarios/` exists with fixture JSON payloads for `T9/` (wallet, securities, positions, nav_packages, transactions, provisions, cash_accounts, diagnose_output, bayesian_output, expected_corrections) and `T10/` (same set plus coverage.json, metadata.json). Content not inspected beyond directory listing.
- Total findings: 11
- Breakdown: CRITICAL: 0 / HIGH: 2 / MEDIUM: 5 / LOW: 4

## Findings

### [HIGH] IndexError crash when WITHHOLDING_TAX flag is absent or impact-zero — tests/detail_t9.py:192-193
**Category:** correctness
**Evidence:**
```python
print(f"  Deterministic WT impact:  R$ {[f['impact'] for f in actionable if f['flag']=='WITHHOLDING_TAX'][0]:,.2f}")
print(f"  MC samples tax rates from: {[f['distribution']['options'] for f in actionable if f['flag']=='WITHHOLDING_TAX'][0]}")
```
**Impact:** The script is pitched as a "detailed walkthrough of T9" but hardcodes the assumption that WITHHOLDING_TAX survives factor extraction as an actionable flag. If `extract_factors` ever changes classification (e.g., tier-1 determinism, or filtering zero-impact sub-flags), these two list comprehensions produce `[]` and the `[0]` subscript raises `IndexError`, aborting the walkthrough before the Validation section prints.
**Recommendation:** Guard the lookup:
```python
wt = next((f for f in actionable if f["flag"] == "WITHHOLDING_TAX"), None)
if wt:
    print(f"  Deterministic WT impact:  R$ {wt['impact']:,.2f}")
    print(f"  MC samples tax rates from: {wt['distribution'].get('options', {})}")
```

### [HIGH] `best = mc["bestFix"]` dereferenced without None-check — tests/detail_t9.py:199-203
**Category:** correctness / error-handling
**Evidence:**
```python
best = mc["bestFix"]
v = validate_fix(diag, best, cfg=cfg)

print(f"  Fix applied: {best['flags']}")
print(f"  totalImpact: R$ {best['totalImpact']:,.2f}")
```
**Impact:** `score_combinations_mc` can legitimately return `bestFix=None` (e.g., if all flags become indicative under a config tweak, or if no combination is admissible). `validate_fix(diag, None, cfg=...)` and `best['flags']` then fail with `TypeError` / `KeyError`, masking the actual pipeline outcome. `full["bestFix"]` at line 247 has the same issue and is also dereferenced without guard.
**Recommendation:** Early-exit with a readable message when `best` is `None`, then fall through to the FULL PIPELINE block — mirror the same guard there.

### [MEDIUM] Hardcoded `sys.path` hack instead of package-aware imports — tests/detail_t9.py:2-3, tests/test_bayesian_scenarios.py:7-8
**Category:** convention
**Evidence:**
```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from pages.bayesian import ...
```
**Impact:** Both scripts mutate `sys.path` at import time. Running `python tests/detail_t9.py` from any other cwd works, but it also means running the files under a test runner (or importing them as modules) permanently pollutes `sys.path` for the interpreter.
**Recommendation:** Add a `tests/conftest.py` (or a `tests/__init__.py` with a project-level `pyproject.toml`/`setup.cfg` listing `pages/` as a source root). Remove the manual `sys.path.insert` from each file.

### [MEDIUM] `_load_config` is a private helper being imported cross-module — tests/detail_t9.py:4, tests/test_bayesian_scenarios.py:10
**Category:** convention / duplication
**Impact:** The leading underscore in `_load_config` signals "module-private". Two test scripts reach across the module boundary to call it, freezing the current internal name. If the function is intentionally part of the test surface, it should be public.
**Recommendation:** Either rename the helper in `pages/bayesian.py` or expose a public `get_bayesian_config()` wrapper.

### [MEDIUM] `reduction` label is misleading when the new gap has the opposite sign — tests/simulate_recalculo.py:199-200
**Category:** financial
**Impact:** `abs(new_gap / orig_gap)` collapses sign information. If corrections overshoot and flip the sign of `gap_pct`, the ratio can be >1, producing a *negative* "Gap fechado" percentage.
**Recommendation:** Report as `reduction = max(0.0, (1 - abs(new_gap_pct/gap_pct_orig)) * 100)` and add a separate "overshoot detected" note when `abs(new_gap_pct) > abs(gap_pct_orig)`.

### [MEDIUM] Tolerance threshold in simulation doesn't match bayesian config — tests/simulate_recalculo.py:205
**Category:** financial / convention
**Impact:** The bayesian module carries a `cfg['tolerance']` value. This sibling script hardcodes `0.01`. If operations tune the tolerance in live config, the simulation reports "RESOLVIDO" under the old threshold.
**Recommendation:** Import `_load_config` and replace `0.01` with `cfg['tolerance']`.

### [MEDIUM] `name[:5]` / `name[4:]` splitting is brittle — tests/test_bayesian_scenarios.py:353
**Category:** correctness
**Impact:** Relies on every scenario name starting with exactly a 4-character `T<n>: ` prefix. `T1:` is 3 chars → split is wrong for T1-T9; correct for T10+.
**Recommendation:** `tid, _, desc = name.partition(":")`

### [MEDIUM] Test oracle only logs failures, never exits non-zero — tests/test_bayesian_scenarios.py:384-385
**Category:** testing
**Impact:** `run_all()` computes `passed`/`failed` counts but the process always exits 0. The file's name (`test_bayesian_scenarios.py`) and PASS/FAIL counting strongly imply a pass/fail contract.
**Recommendation:** `sys.exit(1 if any(r["status"] == "FAIL" for r in results) else 0)`

### [LOW] Duplicate scenario definitions between detail_t9.py and test_bayesian_scenarios.py::T9 — tests/detail_t9.py:8-42, tests/test_bayesian_scenarios.py:258-285
**Category:** duplication
**Recommendation:** Import T9 from the scenarios module.

### [LOW] `old_nav` conflates "NAV without provisions" with `currentNav` — tests/simulate_recalculo.py:126, 135, 141
**Category:** financial / clarity
**Recommendation:** Rename to `nav_before_fix` / `nav_after_fix`.

### [LOW] Hardcoded column widths make tables easy to break — tests/detail_t9.py:155-157, tests/test_bayesian_scenarios.py:311-312
**Category:** style

## Patterns observed (cross-file themes)
- **Graceful handling of pipeline edge cases is absent across both walkthroughs.** Both files would benefit from a shared `summarize_result(r)` helper that distinguishes "no solution found" from "solution with 0% posterior".
- **The scenarios folder exists and appears authoritative (JSON fixtures under `tests/scenarios/T9/` and `tests/scenarios/T10/`), but none of the three Python scripts reviewed reference it.** Consolidating on the JSON fixtures as the single source of truth would eliminate drift.
