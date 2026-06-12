# Code Review — Web/Backend Layer [v2]

## Summary
- Files reviewed: `app.py`, `db.py`, `pages/__init__.py`, `pages/painel.py`, `pages/nav.py`, `pages/config.py`, `pages/setup.py`, `pages/stubs.py`, `pages/caixa.py`, `pages/posicoes.py`, `pages/validacao_rentabilidades.py`, `pages/conciliacao.py`, `pages/bayesian.py`
- Total findings: 24
- Breakdown: CRITICAL: 2 / HIGH: 7 / MEDIUM: 9 / LOW: 6

## Systematic checks performed
- **Index coverage**: Hot-path queries mostly covered. Two gaps: `db.securityMappings.find_one({"companyId"})` at posicoes.py:18 (no index); `db.securities.find({})` at conciliacao.py:439 is unbounded volume concern.
- **File write safety**: 6 open-w sites (db.py:42, nav.py:162, config.py:69, config.py:92, validacao_rentabilidades.py:20, conciliacao.py:49, + loop writer at conciliacao.py:2367). None atomic. All flagged under one MEDIUM.
- **get_company_filter enforcement**: 14 handlers read `companyId`. Only 6 index routes apply filter. **8 API handlers** unguarded.
- **valid_wallet_ids call-sites**: 9 calls. 4 wasteful (companyId already scopes). 5 in conciliacao.py do dual duty for filtering + wallet-name map.
- **str(exc) leakage sites**: 5 matches (conciliacao.py:264, 1921, 2130, 2407; setup.py:29). All flagged.
- **Unguarded ObjectId() sites**: 11 total; all wrapped in try/except.
- **Unvalidated int()/float() on request args**: 2 `int()` unguarded (nav.py:38, nav.py:114); 4 `float()` guarded but unbounded (posicoes.py:139, 143, 254, 258).

## Findings

### [CRITICAL] Mongo URI credentials echoed to browser in setup error paths — pages/setup.py:29,43
**Category:** security
**Impact:** PyMongo `OperationFailure`, `ConfigurationError`, DNS errors embed full URI incl. `username:password@host` inside `str(e)`. Unsuccessful setup call echoes credential back to frontend, DevTools, proxy logs, screenshots.
**Recommendation:** `pymongo.uri_parser.parse_uri`, return only classification ("timeout", "auth failed", "TLS error") + generic message. Never echo `str(e)` from `server_info()`.

### [CRITICAL] Pre-auth SSRF + uncapped timeout on arbitrary Mongo URI — pages/setup.py:18-29
**Category:** security
**Impact:** `/api/setup` bypasses `before_request` guard → pre-auth reachable. `MongoClient` speaks to `127.0.0.1:27017`, `192.168.x.x`, `169.254.169.254` (IMDS). No scheme whitelist, no private-IP block, no `connectTimeoutMS`/`socketTimeoutMS`, no rate-limit. Combined with credential leak → LAN attacker scans internal services through the app.
**Recommendation:** (1) Scheme whitelist `{mongodb, mongodb+srv}`. (2) Parse hosts, reject RFC1918/loopback/link-local. (3) Add `connectTimeoutMS=5000, socketTimeoutMS=5000`. (4) Per-user attempt counter with lockout.

### [HIGH] Config file corruption on any malformed item — pages/config.py:59-70
**Category:** error-handling
**Impact:** Comprehension can raise KeyError/ValueError after `open("w")` is called → truncated file. Non-atomic write + OneDrive PermissionError produces same corruption. Pattern repeats across 6 writers.
**Recommendation:** Validate payload first (400 on bad fields), then atomic write via `.tmp` + `os.replace`.

### [HIGH] `/api/conciliacao/bayesian` bypasses HTTP error handling by calling `diagnose()` directly — pages/conciliacao.py:1595-1621
**Category:** correctness
**Impact:** When `diag` has no `"error"` key yet `"step1"` missing, `extract_factors` proceeds silently with `gap_cash=None`. Frontend shows "no fix needed" on broken diagnostic. Also double-reads `request.args`.
**Recommendation:** Refactor to `_run_diagnose(wallet_id, date) -> (payload, status_code)` helper.

### [HIGH] `int(request.args.get("limit"))` unguarded and unbounded — pages/nav.py:38, pages/nav.py:114
**Category:** correctness + security
**Impact:** `?limit=foo` → ValueError 500. `?limit=1000000` → 1.4M iterations in get_biz_dates + 1M-element `$in` rejected by Mongo as `BSONObjectTooLarge`. DoS vector.
**Recommendation:** try/except + `max(1, min(limit, 60))`.

### [HIGH] `/api/nav/detail` / `detail-grid` trust client `companyId`+`entityId` with no visibility check — pages/nav.py:77-145
**Category:** security
**Impact:** Detail endpoints never consult `get_company_filter()`. Users discover wallet names / NAV coverage of companies they shouldn't see by guessing `companyId`.
**Recommendation:** Early-return empty if `cid not in get_company_filter()`.

### [HIGH] Cross-company info leakage on 8 API endpoints accepting `companyId` — multiple files
**Category:** security
**Call sites:** painel.py:24, painel.py:57, caixa.py:56, caixa.py:89, validacao_rentabilidades.py:44, validacao_rentabilidades.py:77, conciliacao.py:146, conciliacao.py:204, conciliacao.py:1663, conciliacao.py:1959.
**Impact:** When `company_filter` configured in `data/settings.json`, only index page filters dropdown. Every data-backing API trusts client-supplied `companyId`.
**Recommendation:** `assert_company_visible(cid)` helper in db.py; guard every API handler.

### [HIGH] `/api/conciliacao/transactions` full-collection scan of `securities` every call — pages/conciliacao.py:437-440
**Category:** performance
**Impact:** Rebuilds name map from thousands of rows when handler only needs ~20. ~99% waste per modal open.
**Recommendation:** Inline: collect distinct securityIds from transaction result, then `db.securities.find({"_id": {"$in": ...}}, {"beehusName": 1})`.

### [HIGH] `replicate_scenario` picks former-position reference from `processedPosition` directly, bypassing `_find_former_nav` — pages/conciliacao.py:1508-1523
**Category:** correctness
**Impact:** Exactly the "phantom multi-day return" pattern the file explicitly avoids (see block comment at conciliacao.py:604-607). `processedPosition` can exist for date with no navPackage; replicated scenario carries former position from trashed/missing-NAV day, so replayed diagnostic produces different gap than source.
**Recommendation:** Use `_find_former_nav(source_wallet, source_date)` to resolve former_source_date, mirror `get_wallet_detail:281-289`.

### [MEDIUM] `str(exc)` + `traceback.print_exc()` returned to client on 5 endpoints — pages/conciliacao.py:261-264,1918-1921,2127-2130,2404-2407; pages/setup.py:29
**Category:** security + error-handling
**Recommendation:** Keep server-side logging; return fixed opaque message.

### [MEDIUM] Non-atomic JSON writes across 6 writers — db.py:42, pages/nav.py:162, pages/config.py:69, pages/config.py:92, pages/validacao_rentabilidades.py:20, pages/conciliacao.py:49
**Category:** concurrency + error-handling
**Impact:** Multi-threaded WSGI: concurrent POSTs race. Reader between `open("w")` and `json.dump` sees empty file.
**Recommendation:** `_atomic_write_json(path, obj)` helper in db.py.

### [MEDIUM] `_DbProxy._init` swap not thread-safe during concurrent setup — pages/setup.py:51-52
**Category:** concurrency
**Impact:** Multi-threaded WSGI: reader calling `db.companies.find(...)` during `save-connection` window sees old `db._db` while `db_module.client` already points to new host. Lost-update vector.
**Recommendation:** Wrap `client=...; db._init(...)` in `threading.Lock` or single `db.swap(new_client, DB_NAME)` helper.

### [MEDIUM] `generate-transactions` treats `item.get("impact") or 0` as valid — pages/conciliacao.py:1400
**Category:** financial
**Recommendation:** Validate: skip/400 when `raw_impact is None`.

### [MEDIUM] `diagnoseFeedback.insert_one` accepts arbitrary untrusted fields — pages/conciliacao.py:1313-1326
**Category:** security + convention
**Impact:** No type validation. Client can store arbitrary structures, nested dicts, enormous arrays. Missing `get_company_filter()` check.
**Recommendation:** Whitelist + coerce types; `len(flagsInScenario) <= 50`.

### [MEDIUM] `get_biz_dates` infinite-loops on malformed inputs — db.py:118-125
**Category:** correctness
**Impact:** `?endDate=2024-13-40` → ValueError 500. `limit=-1` loops forever.
**Recommendation:** Clamp `limit = max(0, min(limit, 365))`; wrap `date.fromisoformat` with fallback.

### [MEDIUM] Threshold unit confusion: "percent of percent" in `/api/conciliacao/config` — pages/conciliacao.py:29, 76
**Category:** financial
**Impact:** Bounds `0<=pct<=100` allows 100 which means "100% of 100 = 10000 in decimal" — nonsensical. User reads `diffThresholdPct: 0.01` as 1% but it's actually 0.01%.
**Recommendation:** Rename to `diffThresholdBps` or tighten bounds `0<=pct<=1.0`.

### [MEDIUM] `_check_data_quality` decimal-shift heuristic is sign-blind and float-imprecise — pages/bayesian.py:113-130
**Category:** correctness
**Impact:** `DATA_QUALITY_ERROR` has confidence 0.99, so false positives override Tier-1 flags in posterior ranking. Float tail like `1.2345678912` produces spurious digit match. Negative values strip sign asymmetrically.
**Recommendation:** `f"{abs(fq):.0f}"` for integer magnitude; fixed precision or `math.log10(ratio)`.

### [MEDIUM] `statistics.stdev` on ≥3 samples drives 3-sigma thresholds — pages/conciliacao.py:1126-1140, pages/validacao_rentabilidades.py:337-347
**Category:** financial + error-handling
**Impact:** 3-observation stdev extremely noisy. Identical values → `stddev = 0` → `z_score = x / stddev` → ZeroDivisionError masked by `stddev or 1`.
**Recommendation:** Require `len(returns) >= 20`; skip when `stddev < 1e-10`.

### [LOW] Wasted `valid_wallet_ids()` calls where `companyId` already scopes — painel.py:26, caixa.py:58, validacao_rentabilidades.py:46, 79
**Category:** performance

### [LOW] `sum(...) or None` loses legitimate zero for `unmatched_txn_total` / `matched_txn_total` — pages/conciliacao.py:380-386
**Category:** correctness

### [LOW] `float(request.args.get("tol*"))` has no upper bound — pages/posicoes.py:139, 143, 254, 258
**Category:** correctness

### [LOW] `requirements.txt` has zero version pins — requirements.txt:1-6
**Category:** dependency

### [LOW] Dead import `_OID` / unused alias `_valid_wallet_ids` — pages/validacao_rentabilidades.py:3, 24
**Category:** duplication

### [LOW] `pages/__init__.py` is empty — pages/__init__.py
**Category:** duplication (documentational only — no change needed)

## Patterns observed (cross-file themes)
- **`get_company_filter()` applied only on index routes, never backing APIs.** 8 `/api/...` handlers trust client `companyId`. Filter is UI hint not access control.
- **Every JSON config write is non-atomic.** 6 writers on OneDrive-synced paths. Centralize on `_atomic_write_json`.
- **Error handlers leak implementation details.** `str(exc)` reaches browser on 5 endpoints plus setup (pre-auth → credential-exfil vector).
