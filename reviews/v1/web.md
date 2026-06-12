# Code Review — web/backend layer (app.py, db.py, pages/*)

## Summary
- Files reviewed: `app.py`, `db.py`, `pages/__init__.py`, `pages/painel.py`, `pages/nav.py`, `pages/config.py`, `pages/setup.py`, `pages/stubs.py`, `pages/caixa.py`, `pages/posicoes.py`, `pages/validacao_rentabilidades.py`, `pages/conciliacao.py`, `pages/bayesian.py`
- Total findings: 24
- Breakdown: CRITICAL: 1 / HIGH: 6 / MEDIUM: 10 / LOW: 7

## Findings

### [CRITICAL] `config_save` truncates `data/config.json` with no write-safety or validation — pages/config.py:55
**Category:** correctness
**Impact:** No try/except wraps the comprehension — malformed item raises `KeyError` *after* `open(..., "w")` is called, leaving `config.json` truncated. Non-atomic write — PermissionError mid-write during OneDrive sync produces same corruption. Drives `selected` pairs on `/nav` — corrupt file silently hides all pairs from NAV dashboard.
**Recommendation:** Validate first, then atomic write via `.tmp` + `os.replace()`. Apply to all JSON save routes: `settings_save` (pages/config.py:92), `nav_settings_save` (pages/nav.py:162), `_save_thresholds` (pages/validacao_rentabilidades.py:20), `_save_conciliacao_config` (pages/conciliacao.py:49).

### [HIGH] `_save_thresholds` silently truncates rentability_thresholds.json on any failure — pages/validacao_rentabilidades.py:19
**Category:** error-handling
**Impact:** Called after expensive 60-day recomputation. Failure mid-write → thresholds missing, user must rerun. No directory creation guard.
**Recommendation:** Atomic write + `OSError`/`PermissionError` → `jsonify({"error":"falha ao salvar thresholds"}), 500`.

### [HIGH] `db.py` crashes the entire app on startup if MongoDB URI is malformed — db.py:79
**Category:** error-handling
**Impact:** If `_conns[_user]` malformed (manual edit), `MongoClient` raises `InvalidURI` synchronously at import time → Flask process fails to start, user can't reach `/setup`.
**Recommendation:** Wrap conditional connect in try/except with `serverSelectionTimeoutMS=5000`, log warning, leave `db` uninitialized so `before_request` redirects to `/setup`.

### [HIGH] Mongo URI with credentials echoed verbatim in setup error response — pages/setup.py:29,43
**Category:** security
**Impact:** PyMongo exception messages routinely embed full URI including credentials. Lands verbatim in JSON response, rendered in browser, cached in devtools/proxy logs. `/api/setup/*` is pre-registration = most exposed surface.
**Recommendation:** Sanitize via `str(msg).replace(uri, "<uri>")` or return generic "Conexão falhou — verifique URI" and log detail server-side.

### [HIGH] `/api/setup/test-connection` has no SSRF guard or full timeout — pages/setup.py:18
**Category:** security
**Impact:** `serverSelectionTimeoutMS=5000` covers Mongo handshake, but crafted URIs like `mongodb://169.254.169.254/` can probe internal services. Endpoint reachable pre-auth via `before_request` bypass.
**Recommendation:** Reject non-mongodb schemes, reject IP-literal hosts in private/link-local ranges, add `connectTimeoutMS=5000, socketTimeoutMS=5000`.

### [HIGH] `load_user_connections` has no fallback on corrupt JSON — db.py:34
**Category:** error-handling
**Impact:** Corrupt file (partial OneDrive sync, manual edit) → `JSONDecodeError` at app import → Flask process fails to start, user can't reach `/setup`.
**Recommendation:** try/except `(OSError, JSONDecodeError)` → `{}`. Make `save_user_connections` atomic too.

### [HIGH] `valid_wallet_ids()` full-collection scan on every hot API call — db.py:112
**Category:** performance
**Impact:** Called on every `/api/painel/dates`, `/api/caixa/dates`, `/api/conciliacao/dates`, `/api/validacao-rentabilidades/*`. Full scan returns every `_id`, embeds huge `$in` array into downstream queries. Worst on `get_rows` / `transaction_check`.
**Recommendation:** Most callers have `companyId` in scope — filter wallets by company first. If guard needed, cache per-request on `flask.g`.

### [MEDIUM] `/api/conciliacao/bayesian` re-invokes `diagnose()` without clean parameter passing — pages/conciliacao.py:1602
**Category:** correctness
**Impact:** Calls view function as plain function, relying on shared `request` object. Runs every DB query twice. Fragile `isinstance(..., tuple)` check for errors.
**Recommendation:** Extract diagnose body into `_run_diagnose(wallet_id, date)` helper returning payload dict.

### [MEDIUM] `_sum_cash` unbounded `$unwind` on cashAccounts.values — pages/conciliacao.py:497, pages/caixa.py:9
**Category:** performance
**Impact:** Matches wallet then unwinds every historical `values[]` in Python. No index on `cashAccounts.values.date`. For wallet with years of history pulls entire array on every `/wallet-detail` and `/caixa/analyze`.
**Recommendation:** Push date filter into `$match` or index `cashAccounts.values.date`.

### [MEDIUM] `posicoes.wallet_detail` tries string `_id` lookup first — convention violation — pages/posicoes.py:266
**Category:** convention
**Impact:** Per project convention, `_id` is always ObjectId. First query always wasted. Same pattern repeats at conciliacao.py:1366, 1432, 1481, 2172.
**Recommendation:** `_wallet_by_id(wid)` helper in db.py, ObjectId first.

### [MEDIUM] `posicoes.analyze` silently changes company_id to arbitrary matched wallet's company — pages/posicoes.py:167
**Category:** correctness
**Impact:** When UI passes only entityId, wallet_ids can span multiple companies; `_build_mapping` uses single `companyId` via arbitrary Mongo ordering. Same security flagged/unflagged depending on which wallet Mongo returns first.
**Recommendation:** Require companyId or iterate mappings per-company group.

### [MEDIUM] `validacao_rentabilidades` N+1 former-position queries — pages/validacao_rentabilidades.py:104, 192, 206
**Category:** performance
**Impact:** One `find_one` per wallet on hot `/securities` endpoint. Pattern solved elsewhere via `$group $first` aggregation.
**Recommendation:** Single aggregation with `$sort` + `$group` + `$first`.

### [MEDIUM] `calculate_thresholds` numDays param trusted verbatim — pages/validacao_rentabilidades.py:281
**Category:** security/performance
**Impact:** No type coercion, no upper cap. Client sending `{"numDays": 100000}` pulls massive result sets per wallet. `"60"` string → TypeError in `limit()` → 500.
**Recommendation:** `max(3, min(int(data.get("numDays", 60)), 365))`.

### [MEDIUM] `/api/caixa/analyze` N+1 `_sum_cash` calls — pages/caixa.py:117
**Category:** performance
**Impact:** 3 round-trips per wallet × 50 wallets = 150 sequential aggregations, each unwinding full `values[]`.
**Recommendation:** Batch single aggregation with `$in` and date match.

### [MEDIUM] `diagnose` returns raw exception text on 500 — pages/conciliacao.py:264, 1921, 2130, 2407
**Category:** security
**Impact:** `str(exc)` on PyMongo errors often contains connection string with credentials; on serialization errors contains real data values.
**Recommendation:** `logger.exception(...); return jsonify({"error": "erro interno"}), 500`.

### [MEDIUM] `get_rows` embeds full tenant wallet set in `$in` — pages/conciliacao.py:208, 227
**Category:** performance
**Impact:** Aggregation pipeline ships with every wallet as `$in` array. `wallet_names` iterates every wallet then filters in Python instead of one-pass query.
**Recommendation:** Scope wallets to companyId first.

### [MEDIUM] `bayesian._sample_tier2_impact` parses amountDiff out of formatted string — pages/bayesian.py:667
**Category:** correctness
**Impact:** Fragile string parse of `amountDiff({amt_diff})`. On scientific notation or edge cases, try/except falls back to 0 and Monte Carlo sample is lost. PU estimated from `high / (1 + margin)` rather than passed through.
**Recommendation:** Store `amountDiff` and `pu` as numeric fields on distribution dict; read as typed floats.

### [MEDIUM] `/api/…/dates` routes miss `get_company_filter()` enforcement — pages/painel.py:22, caixa.py:54, conciliacao.py:144, validacao_rentabilidades.py:42
**Category:** convention
**Impact:** User restricted to company A can hit `?companyId=B` and receive counts for B. Silent information-leak across the filter the settings page advertises.
**Recommendation:** Top of each handler: `if cf and company_id and company_id not in cf: return jsonify({"cards": []})`.

### [LOW] `stubs.py` view `__name__` collision risk — pages/stubs.py:13
**Category:** convention
**Recommendation:** Assert uniqueness or single handler with whitelist dict.

### [LOW] `item.get("impact") or 0` loses legitimate 0 — pages/conciliacao.py:1400
**Category:** financial
**Recommendation:** `0 if item.get("impact") is None else item["impact"]`.

### [LOW] `_group_unprocessed` mixes `balance`-authoritative and `pu*qty`-derived rows — pages/posicoes.py:33
**Category:** financial
**Recommendation:** Always compute `bal = pu * qty` for consistency.

### [LOW] Duplicate `_sum_cash` implementation — pages/caixa.py:9, pages/conciliacao.py:492
**Category:** duplication
**Recommendation:** Move to `db.py` as `sum_cash(wallet_id, pos_date)`.

### [LOW] Duplicate "latest navPackages date" lookup in 4 files — pages/painel.py:28, caixa.py:60, conciliacao.py:152, validacao_rentabilidades.py:49
**Category:** duplication
**Recommendation:** `db.py` helper `latest_nav_date(company_id, wallet_ids)`.

### [LOW] Unused import `_OID` — pages/validacao_rentabilidades.py:3
**Category:** dead code

## Patterns observed (cross-file themes)
- **Unsafe JSON file writes everywhere.** Every persistence path opens in `"w"` directly. No atomic-replace, most have no try/except. Introduce single `_atomic_write_json(path, obj)` helper in `db.py`.
- **Full-collection scans on hot API paths.** `valid_wallet_ids()` + `{"$in": list(wallet_ids)}` is most-called anti-pattern. Most callers have `companyId` and could scope wallets accordingly.
- **Credential / exception leakage to client.** Setup routes echo `str(e)` with Mongo URIs; four `/api/conciliacao/*` routes return `str(exc)` on 500. Centralize error formatting, scrub URIs, log server-side.
