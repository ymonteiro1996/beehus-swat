# Code Review — Report generation scripts [v3]

## Summary
- Files reviewed: generate_report_from_mongo.py, generate_report_json.py, generate_report_sample.py
- Cross-checked: docs/FILE_GENERATION.md, db.py
- Total findings: 13 (3 consolidated)
- Breakdown: CRITICAL: 1 / HIGH: 3 / MEDIUM: 6 / LOW: 3

## Systematic checks performed
- Index coverage: all hot queries covered by `ensure_indexes()`. Inefficiency is N+1 loop pattern, not missing indexes.
- File write safety: 2 open-w + 2 `wb.save` sites; none atomic. Consolidated finding.
- get_company_filter: N/A (CLI scripts). `COMPANY_ID` at from_mongo:23 defined but unused.
- valid_wallet_ids: 0.
- str(exc) leakage: 0.
- Unguarded ObjectId(): 3 consolidated — from_mongo:81,123; json:95. json:24 is guarded.
- Unvalidated int()/float() on request args: N/A.
- **Docs ↔ code cross-check**: `docs/FILE_GENERATION.md` covers upload/replication templates only. Report output schema (sheet/field names) entirely undocumented. Violation of CLAUDE.md instruction "Spec changes go to .md files first, alongside code". Flagged as MEDIUM finding.
- **Security claims verified via Bash**: 0 (no bash; claims backed by library-doc citations — InvalidId per `bson.objectid.ObjectId.__init__` docs).

## Findings

### [CRITICAL] `rentabilidade_mes` silently mis-labels a daily return as monthly — generate_report_from_mongo.py:203, generate_report_json.py:165
**Category:** financial
**Impact:** User-facing monthly return is off by ~20×; column label promises monthly, delivers daily.
**Recommendation:** Compound daily returns over REFERENCE_MONTH: `rent_mes = prod(1+r_i) - 1` over `positionDate` in REFERENCE_MONTH.

### [HIGH] `patrimonio_inicial` = previous daily NAV, not month-start NAV — generate_report_from_mongo.py:182,201; generate_report_json.py:146,163
**Category:** financial
**Impact:** "Initial patrimony" off by up to 29 days of market movement. Derived `final - inicial` is wrong.
**Recommendation:** `previous = next((n for n in reversed(nav_list) if not n["positionDate"].startswith(REFERENCE_MONTH)), first)`.

### [HIGH] `volatilidade_anualizada` uses `sqrt(12)` on daily returns — generate_report_from_mongo.py:645, generate_report_json.py:354
**Category:** financial
**Impact:** Volatility ~4.6× too low. `meses_positivos`/`meses_negativos` count days, not months.
**Recommendation:** Either resample to monthly + keep `sqrt(12)`, or use `sqrt(252)` for daily. Rename `meses_*` to match.

### [HIGH] N+1 Mongo round-trips — 9 occurrences across 2 files (consolidated)
**Category:** performance
**Sites:** generate_report_from_mongo.py:80-84 (wallets), :89-96 (positions), :101-107 (navs), :112-119 (txns), :143 (entity), :468 (entity); generate_report_json.py:94-116, :129, :282.
**Recommendation:** Replace each per-wallet loop with `$in`-filtered query; dict-cache entity names.

### [MEDIUM] Unguarded `ObjectId()` conversion — 3 sites (consolidated)
**Category:** error-handling
**Sites:** generate_report_from_mongo.py:81, :123; generate_report_json.py:95.
**Evidence:** `bson.errors.InvalidId` raised per bson.objectid docs for any non-24-char-hex input. A single malformed `entityId` aborts the script mid-run, discarding in-memory workbook state.
**Recommendation:** Wrap in try/except `(InvalidId, TypeError)`.

### [MEDIUM] Hardcoded FX rate duplicated across 3 sites — generate_report_from_mongo.py:391, 461, 506; generate_report_json.py:19
**Category:** financial / duplication
**Recommendation:** Single module-level constant; reference canonical source (PTAX) or load from `data/settings.json`.

### [MEDIUM] Non-atomic file write on OneDrive path — 3 sites (consolidated)
**Category:** error-handling
**Sites:** generate_report_json.py:401, generate_report_from_mongo.py:781 (`wb.save`), generate_report_sample.py:778.
**Impact:** OneDrive PermissionError mid-write leaves 0-byte file, overwriting last valid output.
**Recommendation:** `wb.save(tmp); os.replace(tmp, output_path)`.

### [MEDIUM] `year_start_nav`/`rent_ano` hardcoded to 2024-12 — generate_report_from_mongo.py:187-195; generate_report_json.py:149-167
**Category:** correctness / financial
**Impact:** (1) After 2026-01-01 YTD wrong for every wallet. (2) Fallback to `first` conflates inception return with YTD.
**Recommendation:** `prev_year_prefix = f"{int(REFERENCE_MONTH[:4])-1}-12"`; skip column if no match rather than using inception.

### [MEDIUM] `direction` heuristic mis-labels non-deposit transactions — generate_report_from_mongo.py:571, generate_report_json.py:337
**Category:** financial
**Impact:** Every non-negative balance labelled "entrada" regardless of `beehusTransactionType`. buySell cash-in mixed with genuine deposits.
**Recommendation:** Derive from `beehusTransactionType` + sign, or drop column and document heuristic.

### [MEDIUM] Report output schema entirely undocumented — docs/FILE_GENERATION.md
**Category:** convention
**Impact:** Sheet/field names emitted by both scripts are undocumented. Periodicity (daily/monthly), units (percent/fraction), sign conventions, currency — all missing. User's CLAUDE.md memory instruction requires this.
**Recommendation:** Add `docs/RELATORIO_PATRIMONIAL.md` with each sheet/key + unit + periodicity + sign + currency.

### [LOW] Dead constant `COMPANY_ID` — generate_report_from_mongo.py:23

### [LOW] Region inference "US" substring match — generate_report_from_mongo.py:526, generate_report_json.py:57
**Impact:** Matches `USINAS`, `RUSSO`, `AUSTRIA`, `AUSTRALIA`.
**Recommendation:** Word-boundary regex or explicit prefix check.

### [LOW] `random.seed(42)` produces non-stable fixtures across file edits — generate_report_sample.py:752

## Patterns observed (cross-file themes)
- **Daily/monthly periodicity confusion** — `rentabilidade_mes`, `patrimonio_inicial`, `volatilidade_anualizada` all treat daily data as monthly. Single `monthly_series(nav_list, ref_month)` helper would fix all three.
- **Heuristic string-matching on `beehusName`** — `classify_liquidity`, `infer_region` duplicated and already out of sync (mongo script uses `.upper()` twice on line 682, JSON variant only once on :33).
- **No companyId scoping** — `COMPANY_ID` defined-but-unused. Multi-tenant adaptation = silent cross-company leakage.
