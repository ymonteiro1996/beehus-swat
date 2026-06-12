# Code Review — Report generation scripts [v2]

## Summary
- Files reviewed: `generate_report_from_mongo.py`, `generate_report_json.py`, `generate_report_sample.py`
- Total findings: 14
- Breakdown: CRITICAL: 1 / HIGH: 3 / MEDIUM: 6 / LOW: 4

## Systematic checks performed
- **Index coverage:** All Mongo reads in scope covered by `db.ensure_indexes()` — wallet `_id`, processedPosition `(walletId, positionDate)`, navPackages `(walletId, positionDate, trashed)`, transactions `(walletId, liquidationDate)`, entities `_id`.
- **File write safety:** 1 open-w site (generate_report_json.py:401); 1 flagged non-atomic.
- **get_company_filter enforcement:** N/A (CLI scripts).
- **valid_wallet_ids call-sites:** N/A (hardcoded WALLET_IDS per convention).
- **str(exc) leakage sites:** 0.
- **Unguarded ObjectId() sites:** 4 found (generate_report_from_mongo.py:81,123; generate_report_json.py:24,95). json:24 wrapped in try/except; others flagged.
- **Unvalidated int()/float() on request args:** N/A.

## Findings

### [CRITICAL] `rentabilidade_mes` passes through daily `returnNavPerShare`; volatility annualised with `sqrt(12)` on daily data — generate_report_from_mongo.py:203,645; generate_report_json.py:165,354
**Category:** financial
**Impact:** Two user-facing numbers materially wrong:
1. `rentabilidade_mes` (column 7 `performance_summary` / JSON field) reports last single-day return, not month.
2. `volatilidade_anualizada` treats daily returns as monthly → annualised with `sqrt(12)` instead of `sqrt(252)` → understates vol by factor ~4.6×.
3. `meses_positivos`/`meses_negativos` (from_mongo:651-652 / json:361-362) count days, not months.
**Recommendation:** Aggregate NAV series to month-end before computing, OR compound daily returns within `REFERENCE_MONTH`: `rent_mes = prod(1 + r_i) - 1`. Use `sqrt(252)` for daily data.

### [HIGH] Non-atomic JSON file write can corrupt output — generate_report_json.py:401-402
**Category:** error-handling
**Recommendation:** Write to `.tmp` + `os.replace()`; wrap in try/except PermissionError.

### [HIGH] N+1 Mongo query loading entity names per wallet — generate_report_from_mongo.py:122-124,468; generate_report_json.py:22-27,129,282
**Category:** performance
**Recommendation:** One `$in` query up front; dict lookup thereafter.

### [HIGH] `load_entity_name` unguarded; invalid `entityId` crashes whole report — generate_report_from_mongo.py:122-124,143,468
**Category:** error-handling
**Impact:** `ObjectId("")` raises `InvalidId`, aborts mid-workbook, no partial output. JSON twin already wraps in try/except.
**Recommendation:** Mirror JSON twin's try/except.

### [MEDIUM] `pu * qty` computed before None check — `None * None` raises `TypeError` — generate_report_from_mongo.py:273,280,300,342,403,517,709; generate_report_json.py:192,222,251,311,377
**Category:** correctness
**Impact:** `.get("quantity", 0)` returns stored value if key exists, even `None`. Single security with `"pu": null` aborts sort and all balance sums.
**Recommendation:** `qty = s.get("quantity") or 0; pu = s.get("pu") or 0`.

### [MEDIUM] `navPerShare == 0` silently coerces `rentabilidade_ano`/`rentabilidade_inicio` to 0 — generate_report_from_mongo.py:194-195; generate_report_json.py:154-167
**Category:** financial
**Impact:** (1) `if x else 0` conflates "denominator 0/bad data" with "genuine 0.00% return". (2) JSON's `.get("navPerShare", 1)` fabricates return as `nps_latest / 1 - 1` when field is missing.
**Recommendation:** Return `None`/blank when denominator missing/zero. `.get("navPerShare")` with no default + explicit `if nps_year` check.

### [MEDIUM] `ObjectId(wid)` not guarded — typo in `WALLET_IDS` crashes whole script — generate_report_from_mongo.py:81; generate_report_json.py:95
**Category:** error-handling
**Recommendation:** Try/except `InvalidId` per iteration with `continue`.

### [MEDIUM] `liq_date.startswith("2025-12")` assumes string — drops transactions if Mongo returns datetime — generate_report_from_mongo.py:567-568; generate_report_json.py:329-330
**Category:** correctness
**Impact:** BSON `Date` field → `AttributeError` on `.startswith()`. Mixed storage vintage → silent filter depending on row age.
**Recommendation:** Normalize: `if isinstance(liq_date, datetime): liq_date = liq_date.strftime("%Y-%m-%d")`. Same fix for `n["positionDate"].startswith("2024-12")` (from_mongo:188 / json:150).

### [MEDIUM] Hardcoded year literals `"2024-12"`/`"2025-12"` drift out of sync with `REFERENCE_MONTH` — generate_report_from_mongo.py:188,568; generate_report_json.py:150,330
**Category:** correctness
**Recommendation:** Derive: `ref_year = int(REFERENCE_MONTH.split("-")[0]); year_start_prefix = f"{ref_year-1}-12"`.

### [MEDIUM] Geographic region inference mis-classifies via substring match — generate_report_from_mongo.py:520-531; generate_report_json.py:52-61
**Category:** correctness
**Impact:** `"US" in name` matches `"NATURA & CO … EMERGING MARKETS"` (US substring). Final `else: region = "EUA"` makes "Global" unreachable for non-hedge/non-private.
**Recommendation:** Add `country`/`region` schema field, or match on ISIN prefix. Drop `"US" in name` rule.

### [MEDIUM] Liquidity bucket heuristic: early substring matches win; `.upper()` reapplied — generate_report_from_mongo.py:682,686-692; generate_report_json.py:33-43
**Category:** correctness
**Impact:** `"CDI" in name` classifies `"LCI CDI+0.5% BANCO..."` as "Diaria" before reaching LCA/CDB branch. `"CDB BTG 102.00% CDI"` similarly.
**Recommendation:** Use `\b(CDI|CDB|LCA)\b` regex; order specific → general. Prefer schema `instrumentType` field.

### [LOW] Unused constant `COMPANY_ID` — generate_report_from_mongo.py:23
**Category:** duplication

### [LOW] Unused helper `apply_fmt` — generate_report_from_mongo.py:64-66
**Category:** dead code

### [LOW] Unused imports/constants in `generate_report_sample.py` — generate_report_sample.py:10,17-20,27
**Category:** dead code
**Impact:** `numbers`, `SUBHEADER_FILL`, `SUBHEADER_FONT`, `CATEGORY_FILL`, `CATEGORY_FONT`, `DATE_FMT` — zero references.

### [LOW] `import statistics` inside `sheet_risk_statistics` not at module top — generate_report_from_mongo.py:644
**Category:** convention

## Patterns observed (cross-file themes)
- **`.get(key, 0)` used as null-guard where null is legitimate stored value** — `quantity`, `pu`, `navPerShare`, `returnNavPerShare`. Standardise on `.get(key) or 0`.
- **Duplication between `generate_report_from_mongo.py` and `generate_report_json.py`** — identical classification, NAV logic, transaction filters. JSON twin has quietly diverged (try/except ObjectId, `.get("navPerShare", 1)` default). A shared `report_common.py` would prevent future drift.
- **Hardcoded date/currency/FX literals scattered** — `"2024-12"`, `"2025-12"`, `FX_RATE = 6.19`, `fx_rate = 6.19`. Reproduced 3× inside `generate_report_from_mongo.py` alone (lines 391, 461, 506). Derive from `REFERENCE_MONTH` or load from `data/settings.json`.
