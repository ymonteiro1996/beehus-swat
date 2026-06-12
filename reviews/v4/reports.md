# Code Review — Report generators [v4 anti-fragile]

## Summary
- Files reviewed: generate_report_from_mongo.py, generate_report_json.py, generate_report_sample.py
- **New findings: 9** (1 consolidated)
- Prior confirmed: 13
- Prior refuted: 0
- Breakdown (new only): CRITICAL: 1 / HIGH: 2 / MEDIUM: 4 / LOW: 2

## New findings

### [CRITICAL] `liquidationDate.startswith("2025-12")` crashes if Mongo returns datetime — 2 sites
**Category:** correctness
**Sites:** generate_report_from_mongo.py:567-568, generate_report_json.py:329-330 (same at from_mongo:188, json:150 for positionDate).
**Impact:** Mongo stores `positionDate`/`liquidationDate` as BOTH ISO strings AND BSON Dates (mixed pipeline vintage). `.startswith()` on `datetime` → `AttributeError`, aborts whole script. Silent mis-compare is also possible: `datetime(2025,12,31) != "2025-12-31"`.
**Recommendation:** `_ym(d) = d.strftime("%Y-%m") if hasattr(d, "strftime") else (d or "")[:7]`. Apply everywhere.
**Note:** v2 flagged this as MEDIUM. v3 consolidation regression — dropped it. Escalated to CRITICAL given silent-wrong-number risk.

### [HIGH] `.sort("positionDate", 1)` on mixed-type field yields wrong `nav_list[-1]` — generate_report_from_mongo.py:105, generate_report_json.py:108
**Category:** correctness / financial
**Impact:** Per BSON sort-order spec, `String < Date`. Mixed-type `positionDate` means ascending sort puts all strings before all dates regardless of calendar order. `nav_list[-1]` is the latest Date-typed entry, which may be months behind the latest String-typed entry. Every downstream number (`patrimonio_final`, `rentabilidade_mes`, etc.) silently uses wrong anchor.
**Recommendation:** Preflight type-uniformity check via `$type` aggregation, OR normalize at read time (`$addFields` + `$dateFromString`), OR find max in Python by normalized key.

### [HIGH] `alloc_by_institution` collapses wallets with same entity into duplicate rows — from_mongo.py:472-478, json:280-289
**Category:** correctness / financial
**Impact:** Sheet header says "alloc_by_institution" but no grouping applied. Two wallets sharing an entity (e.g. BTG BRL + BTG USD) produce two rows instead of one consolidated. JSON variant also omits `pct` per-row (sample schema promises it).
**Recommendation:** Group by `entity_name` first: `by_inst.setdefault(name, 0); by_inst[name] += nav * fx`. Emit with `pct = v / total`.

### [MEDIUM] Excel formula/CSV injection via `beehusName` and `description` raw writes — 4 sites (consolidated)
**Category:** security
**Sites:** from_mongo.py:297, 579; json.py:200, 340.
**Impact:** openpyxl Cell.value setter: strings starting with `=`/`+`/`-`/`@` get `data_type='f'` (formula). `beehusName` and `description` come from upstream Mongo ingestion — NOT author-controlled. Injected `=HYPERLINK(...)` or `=IMPORTXML(...)` exfiltrates data when analyst opens the emailed file.
**Recommendation:** `safe_text(s): return ("'" + s) if s[:1] in ("=","+","-","@","\t","\r") else s` at every cell write.

### [MEDIUM] `data/` dir assumed to exist — FileNotFoundError on fresh checkout — from_mongo.py:780-781, json.py:400-402, sample.py:777-778
**Category:** error-handling
**Impact:** Distinct from v3 atomic-write finding. First-run failure: 30+ seconds of Mongo round-trips thrown away because last line crashes.
**Recommendation:** `os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)` + anchor to `__file__`.

### [MEDIUM] `alloc_by_institution` TOTAL row type-drift in JSON — json.py:291-299
**Category:** correctness
**Impact:** Regular rows emit `walletId: str`, `walletName: str`, `saldo_bruto: float`. TOTAL row emits `None`s. Pandas/JS consumers doing `sum(r["saldo_bruto"] for r in rows)` get TypeError on None.
**Recommendation:** Zero-values + `is_total: True` flag, OR separate `_totals` sibling key at root.

### [MEDIUM] `pct_allocation` sums to 1.0 only within a wallet but JSON has no wallet-boundary marker — from_mongo.py:330-371, json.py:212-238
**Category:** correctness / convention
**Impact:** Excel variant emits per-wallet TOTAL row so boundaries are visible. JSON variant has NO TOTAL entry and no group marker. Consumer summing `pct_allocation` naively gets N (wallet count) instead of 1.0.
**Recommendation:** Either add TOTAL row per wallet in JSON (match Excel) or restructure as `"allocation": {walletId: [rows]}`.

### [LOW] `auto_width` undercounts localized BRL / percentage widths — from_mongo.py:54-61, sample.py:60-67
**Category:** template/presentation
**Impact:** `str(cell.value)` ≠ rendered width when `number_format='0.00%'` or BRL_FMT with thousands separator. Negative large BRL (`-62480`) renders as `-62.480,00` (10 chars) but `str` = 6 chars. Excel auto-opens with `#####`.
**Recommendation:** Min-width per column type (BRL ≥ 14, PCT ≥ 8).

### [LOW] `style_body` overwrites category-row styling — from_mongo.py:313+, multiple sheets
**Category:** template
**Impact:** Category header rows in `sheet_asset_detail` picked up body border from `style_body`'s overwrite. Every asset_detail sheet with ≥1 category.
**Recommendation:** Style inline during emit OR pass category-row indices to skip.

## Patterns observed
- **String-typed date assumption pervades** — `startswith`, `[:7]`, `.sort("positionDate", 1)` all assume string. Native Mongo Date breaks them. Single `_ym()`/`_day()` helper would fix 6 sites.
- **Substring-matching classifiers fragile** — region + liquidity buckets use 2-5 char `in` on uppercase broker names. Tokenized match fixes all.
- **No preflight schema check** — scripts assume `data/` exists, dates are strings, strings are injection-safe. 30-line preflight would catch CRITICAL/HIGH/MEDIUM classes.
