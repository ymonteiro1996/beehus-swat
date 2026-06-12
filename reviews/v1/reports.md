# Code Review — Report generation scripts (generate_report_*.py)

## Summary
- Files reviewed: `generate_report_from_mongo.py`, `generate_report_json.py`, `generate_report_sample.py`
- Total findings: 15
- Breakdown: CRITICAL: 0 / HIGH: 4 / MEDIUM: 7 / LOW: 4

## Findings

### [HIGH] `patrimonio_inicial` uses prior-row NAV, not the month-open NAV — generate_report_from_mongo.py:201 / generate_report_json.py:163
**Category:** financial
**Impact:** `navPackages` sorted ascending by `positionDate` with no date filter, so `nav_list[-2]` is the second-to-last stored NAV — may be any business day (even day before `positionDate`), not end-of-previous-month. For `referenceMonth=2025-12` this silently produces "patrimônio inicial" that is the Dec 30 NAV rather than Nov 30. Monthly P&L becomes wrong by the full monthly return.
**Recommendation:** Select previous deliberately: find last NAV whose `positionDate` is strictly before first day of `REFERENCE_MONTH`.

### [HIGH] `rentabilidade_ano` uses wrong base NAV-per-share when prior year-end is missing — generate_report_from_mongo.py:186-194 / generate_report_json.py:149-166
**Category:** financial
**Impact:** (1) `startswith("2024-12")` hardcoded to single year — wrong every future year. (2) Picks *first* Dec-2024 record (ascending) → may use 2024-12-02 instead of 2024-12-31. (3) Fallback to `first` silently produces "return-since-inception" labelled as "rentabilidade_ano".
**Recommendation:** Derive `year_start = f"{int(REFERENCE_MONTH[:4]) - 1}-12-31"`, pick last NAV with `positionDate <= year_start`, return None if absent.

### [HIGH] `rentabilidade_mes` emits latest single-day return, not compounded monthly return — generate_report_from_mongo.py:203 / generate_report_json.py:165
**Category:** financial
**Impact:** `returnNavPerShare` is per-period (typically daily), not monthly. Column header is `rentabilidade_mes` and format is percent — user sees e.g. "0.12%" and believes it is December return for the entire month.
**Recommendation:** Compute monthly return as `latest["navPerShare"] / prev_month_end_navPerShare - 1`.

### [HIGH] `returnNavPerShare` values aggregated for volatility/positive-month counts are per-period, not monthly — generate_report_from_mongo.py:637-645 / generate_report_json.py:350-354
**Category:** financial
**Impact:** If `navPackages` contains daily entries, `meses_positivos`/`meses_negativos` are actually "positive days"/"negative days", and `sqrt(12)` annualization should be `sqrt(252)`. "Annualized volatility" off by factor of `sqrt(252/12) ≈ 4.58`.
**Recommendation:** Resample NAV history to month-end points before aggregating, or detect series periodicity and use correct annualization factor.

### [MEDIUM] `load_entity_name` runs one Mongo query per wallet inside sheet writers — generate_report_from_mongo.py:122-124, 143, 468 / generate_report_json.py:22-27
**Category:** performance
**Recommendation:** Pre-load distinct entity ids once via `$in`, look up locally.

### [MEDIUM] `"US"` substring match in region inference misclassifies unrelated names — generate_report_from_mongo.py:526 / generate_report_json.py:57
**Category:** correctness
**Impact:** `"US"` matches inside `RUSSELL`, `TRUST`, `PLUS`, `FUSION`, `FIDUCIAR`. Overrides more specific `"HEDGE"`/`"PRIVATE"` → `"Global"` branches.
**Recommendation:** Remove `"US"` token (use word boundary or currency field), reorder so `variable1`-based rules evaluate first.

### [MEDIUM] `main()` produces misleading TOTAL row when all wallets skipped — generate_report_from_mongo.py:213+
**Category:** error-handling
**Recommendation:** Emit placeholder row or log warning per skipped wallet.

### [MEDIUM] `print(f"  {w['name']:40s} …")` crashes when wallet document lacks `name` — generate_report_from_mongo.py:749
**Category:** error-handling
**Impact:** Inconsistent with defensive `w.get("name", "")` used everywhere else.
**Recommendation:** `w.get('name', wid)`.

### [MEDIUM] Output path is relative, not repo-anchored — generate_report_from_mongo.py:780 / generate_report_json.py:400 / generate_report_sample.py:777
**Category:** error-handling
**Recommendation:** `Path(__file__).parent / "data" / "..."`, `mkdir(parents=True, exist_ok=True)`.

### [MEDIUM] `ObjectId(wid)` / `ObjectId(entity_id)` raises on malformed id and aborts run — generate_report_from_mongo.py:81, 123 / generate_report_json.py:24, 95
**Category:** error-handling
**Impact:** `generate_report_json.py` wraps in try/except, `generate_report_from_mongo.py` does not. Empty `entityId` default → `InvalidId`, kills run after hundreds of rows.
**Recommendation:** Guard with try/except or `ObjectId.is_valid(x)`.

### [MEDIUM] `sheet_allocation` applies BRL_FMT regardless of wallet currency — generate_report_from_mongo.py:334-371 / generate_report_json.py:212-238
**Category:** financial
**Impact:** Monetary column labelled "total_balance" with Brazilian number formatting applied to USD magnitudes. Reader interprets USD `$125,000` as `R$ 125.000,00`.
**Recommendation:** Split by currency, add explicit `currency` column, or convert to BRL using `FX_RATE` and rename `total_balance_BRL`.

### [LOW] Dead helper: `apply_fmt` defined but never called — generate_report_from_mongo.py:64
**Category:** duplication

### [LOW] `import statistics` buried inside a function — generate_report_from_mongo.py:644
**Category:** duplication

### [LOW] Hardcoded `fx_rate = 6.19` duplicated 3x — generate_report_from_mongo.py:391, 461, 506
**Category:** duplication
**Recommendation:** Hoist to module-level `FX_RATE = 6.19` matching `generate_report_json.py`.

### [LOW] Unused imports in `generate_report_sample.py` — generate_report_sample.py:10
**Category:** duplication
**Impact:** `numbers` imported unused; `SUBHEADER_FILL`/`SUBHEADER_FONT` defined and never referenced.

## Patterns observed (cross-file themes)
- **Monthly-return semantics confused with per-period `returnNavPerShare`.** Drives three findings across both scripts. Any fix should be shared: resample to month-end once, then feed resampled series into all three consumers.
- **Hardcoded year/month literals instead of derivations from `REFERENCE_MONTH`.** `"2024-12"` YTD anchor, `"2025-12"` transaction filter, `fx_rate = 6.19` Dec-2025 all assume scripts run only once. A `ref_year, ref_month = REFERENCE_MONTH.split("-")` helper would fix all three.
- **Defensive-dict access is inconsistent.** `w.get("name", "")` used dozens of times but `w['name']` at line 749, and `ObjectId(...)` guarded in JSON script but not Excel script. Pick one posture uniformly.
