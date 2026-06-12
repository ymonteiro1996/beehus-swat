# Code Review — Web/Backend Layer [v3]

## Summary
- Files reviewed: app.py, db.py, pages/__init__.py, pages/painel.py, pages/nav.py, pages/config.py, pages/setup.py, pages/stubs.py, pages/caixa.py, pages/posicoes.py, pages/validacao_rentabilidades.py, pages/conciliacao.py, pages/bayesian.py; docs/CONCILIACAO_DIAGNOSTICO.md, docs/CONCILIACAO_RECALCULO.md
- Total findings: 14 (3 consolidated)
- Breakdown: CRITICAL: 1 / HIGH: 5 / MEDIUM: 5 / LOW: 3

## Systematic checks performed
- Index coverage: hot queries scope by (walletId, positionDate, trashed) / (walletId, positionDate) / (walletId, liquidationDate) — all covered. `companyId` not indexed but walletId prefix dominates. `$expr`/`$abs` clause in `_mismatch_query` (conciliacao.py:112) can't use index — other prefix fields filter first.
- File write safety: 7 open-w sites — all flagged non-atomic (consolidated).
- get_company_filter enforcement: 13 routes read `companyId`; only 5 HTML index routes enforce. **12 API routes unguarded** (consolidated).
- valid_wallet_ids call-sites: 9 calls; 7 redundant alongside companyId scope. Only flagged where list crosses thousands (conciliacao.py:208, 1667, 1962).
- str(exc) leakage sites: 5 (consolidated) — setup.py:29 + conciliacao.py:264, 1921, 2130, 2407.
- Unguarded ObjectId() sites: 0 uncaught — all 11 guarded.
- Unvalidated int()/float() on request args: nav.py:38/114 unguarded + unbounded (flagged); config.py:63 no try/except around comprehension (flagged); validacao_rentabilidades.py:281 no int coercion of numDays (noted).
- **Docs ↔ code cross-check**: Verified:
  - Gap formula (DIAGNOSTICO §Step 1 / RECALCULO §GAP): `gapPct = returnNavPerShare − returnContribution`; `gapCash = gapPct × formerNav`. conciliacao.py:561-562 ✓
  - expectedEventCash (DIAGNOSTICO §3.2): math matches at conciliacao.py:734/858 ✓
  - rentabContribution: `totalContribution / formerBalance`. conciliacao.py:716 / validacao_rentabilidades.py:137 ✓
  - **expectedValue sign convention (DIAGNOSTICO §3.3)**: doc says `amountDifference × executionPrice`; code at conciliacao.py:897 computes `-amt_diff * price`. Doc omits sign. → DOC DIVERGENCE finding.
  - **rentabPU formula (RECALCULO formula 11)**: doc says `PU / formerPU − 1`; validacao_rentabilidades.py:131 computes event-corrected `(pu + event_c/qty) / f_pu − 1`; conciliacao.py:712 uses literal raw version; conciliacao.py:1154 also uses raw. → two endpoints report DIFFERENT numbers for same security same date when coupons fire.
- **Security claims verified via Bash**: 0 (Python exec blocked in sandbox; claims backed by PyMongo source/docs citations).

## Findings

### [CRITICAL] Pre-auth unvalidated URI with credential leak on `/api/setup/test-connection` and `/save-connection` — pages/setup.py:18-43
**Category:** security
**Impact:** Three compounded issues:
1. **SSRF.** No scheme validation; MongoClient probes arbitrary hosts incl. `169.254.169.254` metadata.
2. **Credential echo.** `ServerSelectionTimeoutError.__str__` contains TopologyDescription host list; `ConfigurationError`/`InvalidURI` contain raw URI per PyMongo docs → previously-working credentials surface in response body captured by proxy logs.
3. **Pre-auth DoS.** 5-sec timeout × worker threads, no rate-limit.
Most exposed surface — runs BEFORE db is initialized.
**Recommendation:** Reject non-mongodb schemes; reject private/loopback/link-local/metadata IPs via `ipaddress` module; add `connectTimeoutMS=3000, socketTimeoutMS=3000`; return fixed scrubbed error; per-IP attempt counter.

### [HIGH] N+1 queries in `/api/validacao-rentabilidades/securities` and `/calculate-thresholds` — pages/validacao_rentabilidades.py:98-114, 298-302
**Category:** performance
**Impact:** One `find_one` per wallet for former position; `/calculate-thresholds` issues one cursor per wallet with `num_days+1` limit = O(num_wallets) round-trips × 60 days of full `securities[]` arrays.
**Recommendation:** Aggregation with `$group`/`$first` pattern already used at conciliacao.py:1716.

### [HIGH] Cross-company info leak — 8 API endpoints accept `companyId` without `get_company_filter()` enforcement (consolidated)
**Category:** security / convention
**Sites:** painel.py:24, painel.py:57, caixa.py:56, caixa.py:89, posicoes.py:86, posicoes.py:135, validacao_rentabilidades.py:44, validacao_rentabilidades.py:77, conciliacao.py:146, conciliacao.py:204, conciliacao.py:1663, conciliacao.py:1959.
**Impact:** User with `company_filter: ["A"]` sees only A in dropdown; URL edit `?companyId=B` returns B's data on all 12 endpoints. Feature is client-side only.
**Recommendation:** `enforce_company_scope(company_id)` helper in db.py calling `abort(403)` or returning empty result.

### [HIGH] `str(exc)` / `traceback.print_exc()` leak internals in error responses — 5 sites across 2 files (consolidated)
**Category:** security / error-handling
**Sites:** setup.py:29; conciliacao.py:264, 1921, 2130, 2407.
**Impact:** PyMongo exception strings embed failed command document verbatim (per `pymongo.errors.OperationFailure.details`). Collection names, field paths, original filter shape leaked.
**Recommendation:** `logging.exception(...)` server-side; return fixed message.

### [HIGH] Non-atomic JSON writes on OneDrive — reader threads see empty/partial files — 7 sites across 6 files (consolidated)
**Category:** concurrency / error-handling
**Sites:** db.py:42, config.py:69, config.py:92, nav.py:162, validacao_rentabilidades.py:20, conciliacao.py:49, conciliacao.py:2367.
**Impact:** Multi-threaded WSGI + OneDrive sync. Between truncate and rewrite, concurrent reader gets 0 bytes → JSONDecodeError → 500 or silent defaults. `settings.json` is read on EVERY request via `get_company_filter()`.
**Recommendation:** Single `atomic_write_json(path, payload)` helper in db.py using `tempfile.mkstemp` + `os.replace` (atomic on NTFS, handles OneDrive lock contention).

### [HIGH] Unbounded `limit` on `/api/nav/rows` and `/api/nav/detail-grid` — DoS — pages/nav.py:38, 114
**Category:** security / performance
**Impact:** `int(request.args.get("limit", 10))` unguarded; `get_biz_dates` loops `while len(result) < limit` with no ceiling. `limit=10_000_000` → 14M Python date subtractions before Mongo aggregation runs.
**Recommendation:** `limit = max(1, min(int(...), 90))` + try/except ValueError → 400.

### [MEDIUM] `date.fromisoformat(end_date)` uncaught on every `/api/*/dates` — 5 sites — db.py:120
**Category:** error-handling
**Sites:** painel.py:38, caixa.py:70, posicoes.py:113, validacao_rentabilidades.py:58, conciliacao.py:161.
**Recommendation:** Wrap in `get_biz_dates` with try/except → fallback to `date.today()`.

### [MEDIUM] `int(s.get("delay", 0))` in `/api/config/save` crashes endpoint on malformed payload — pages/config.py:63
**Category:** correctness / error-handling
**Recommendation:** Validate each entry, return 400 with list of bad rows.

### [MEDIUM] `wallet_filter_query` generates INVALID Mongo filter — `$not: {$size: 0}` — db.py:204
**Category:** correctness
**Impact:** `$not` cannot take operator document directly. Flipping "only_with_consumption" toggle in Settings → `OperationFailure: $not needs a regex or a document`. Latent crash button; probably never exercised.
**Recommendation:** `q["consumptionIdentifiers"] = {"$exists": True, "$ne": []}`.

### [MEDIUM] Doc divergence: `expectedValue` sign convention — docs/CONCILIACAO_DIAGNOSTICO.md:147 vs pages/conciliacao.py:897
**Category:** documentation
**Impact:** Doc says `expectedValue = amountDifference × executionPrice`; code correctly uses `-amt_diff * price` (because transaction `balance` is signed opposite `amountDifference`). Future contributor matching doc breaks step 3.3.
**Recommendation:** Update doc to `expectedValue = −amountDifference × executionPrice` + sign convention note.

### [MEDIUM] Doc divergence: `rentabPU` formula per-security — docs/CONCILIACAO_RECALCULO.md:123-125 vs pages/validacao_rentabilidades.py:131 vs pages/conciliacao.py:716
**Category:** documentation / financial
**Impact:** Two endpoints report DIFFERENT numbers for same security same date when coupons fire. validacao_rentabilidades uses event-corrected `(PU + eventC/qty) / formerPU − 1`; conciliacao.py:1154 (Step 6.2) uses raw `PU/formerPU − 1`. 3-sigma thresholds at validacao_rentabilidades.py:340 trained on event-corrected series but checked against raw series → false positives/negatives in Step 6.2.
**Recommendation:** Pick one convention and document it. Either align conciliacao.py:1154 to event-corrected OR rename the columns (`rentabPU_raw` vs `rentabPU_eventAdjusted`).

### [MEDIUM] `find_one({"_id": wallet_id})` assumes walletId is stored `_id` but convention says opposite — 5 sites
**Category:** convention / correctness
**Sites:** posicoes.py:266, conciliacao.py:1366, 1432, 1481, 2172.
**Impact:** Two find_one round-trips every diagnose/replicate/generate-transactions request. Fallback pattern contradicts project rule.
**Recommendation:** Confirm collection schema, pick one branch.

### [LOW] `@app.errorhandler(RuntimeError)` re-raises — loses stack — app.py:37-42

### [LOW] `_DbProxy._init` + `client = new_client` non-atomic — pages/setup.py:51-52
**Category:** concurrency (noted, microsecond window, cosmetic)

### [LOW] `count_documents > 0` → replace with `find_one({...}, {"_id": 1})` — pages/conciliacao.py:406

## Patterns observed (cross-file themes)
- **`get_company_filter()` is a facade** — enforced on 5 HTML routes, never on 13 API endpoints reading `companyId`. One helper, 13 call sites.
- **JSON persistence pattern uniformly unsafe** — every config/settings save is open-w truncate-rewrite on OneDrive. `atomic_write_json` helper removes 7 latent-crash/data-loss paths.
- **Error-reporting idiom leaks internals** — `str(exc)` in response is wrong reflex; `logging.exception` + fixed user message is right one.
