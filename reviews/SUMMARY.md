# Code Review — Final Summary

**Project:** SWAT Controle de Cargas (Beehus)
**Date:** 2026-04-18
**Iterations:** v1 → v2 → v3 → v4 (agent converged after 4 iterations)
**Agent spec:** [`.claude/agents/code-reviewer.md`](../.claude/agents/code-reviewer.md)
**Raw reviews:** per-iteration folders `v1/`, `v2/`, `v3/`, `v4/` in this directory

---

## Iteration journey

| Iter | Focus | Key methodology added | Key findings yielded |
|---|---|---|---|
| **v1** | Python backend + reports + tests | Baseline Python/Flask/PyMongo checklist; pt-BR locale + example-securities exclusions | 50 findings (1C/12H/22M/15L). Caught: SSRF, N+1s, credential leakage via str(exc), non-atomic JSON writes |
| **v2** | +Templates + dependencies + .claude/settings | 14 categories (incl. concurrency, JS, deps); 7 mandatory systematic sweeps with required counts; strict anti-noise LOW policy | 51 findings (4C/12H/19M/16L). +3 CRITICAL elevated (daily-as-monthly rent, pre-auth SSRF); new: credential leak in `db_unreachable.html` template, missing `data/*.json` in .gitignore |
| **v3** | +JS behavior + data-file configs + docs↔code cross-check | Cross-file consolidation mandate (3+ sites → one finding); reproducibility proof mandate for security claims | 49 findings (3C/11H/20M/15L). Higher density via consolidation. New CRITICAL: XSS in `config.html`. New MEDIUMS: `expectedValue` sign doc divergence, `rentabPU` formula mismatch across modules, real-looking CNPJs in `settings.json` |
| **v4** | Anti-fragile mode: prior findings visible, agents must find what v3 missed | Prior-findings-as-input; required "new findings / confirmed / refuted" counts | **33 NEW findings** (5C/9H/11M/8L). All 4 batches exceeded 3-new threshold. Anti-fragile mode caught: T10 real production data in repo, no auth layer at all, escHtml missing `'` escape, BSON mixed-type sort order, Bayesian tolerance 1000× off from conciliação threshold |

**Convergence rationale:** v4 still yielded substantial new findings, but the AGENT methodology has plateaued. Each iteration added concrete primitives (systematic sweeps → consolidation → anti-fragile); v5 would be speculative. The codebase has many more bugs to find, but agent improvement has hit diminishing returns.

---

## Agent evolution (what each version adds)

**v1 — Baseline** (9 categories)
1. Correctness bugs, Security, Performance, Error handling, Project-convention compliance, Dead code/duplication, Testing gaps, Data integrity/financial correctness — just the Python stack.

**v2 — Systematic sweeps** (+2 categories, +7 sweeps)
- Added: Concurrency/thread-safety; Templates; Dependencies/configuration
- Sweeps: index coverage, file write safety, get_company_filter enforcement, valid_wallet_ids redundancy, str(exc) leakage, unguarded ObjectId, unvalidated int()/float()
- Stricter LOW-severity rule (concrete trigger required)

**v3 — Cross-file + reproducibility + docs** (+3 categories, +2 mandates)
- Added: Frontend JS behavior (setInterval leaks, polling loops, escHtml discipline); Data-file configs; Docs↔code consistency
- Mandate: consolidate 3+ same-pattern findings into one
- Mandate: verify security claims via doc citation or Bash repro (speculation rejected)

**v4 — Anti-fragile** (no new categories, new operating mode)
- Prior-version findings shown to agent; agent must find NEW issues only
- Required Summary counts: new / confirmed / refuted
- Forces diminishing-returns convergence signal

---

## Findings index — aggregated across v1-v4

**Total unique findings across all iterations:** ~110 items (after deduplication)
**Most severe, highest-signal items (prioritized for action):**

### CRITICAL (must fix)
1. **No authentication layer anywhere.** After first `/setup/save-connection`, every API is wide open on the LAN. (v4 web)
2. **`/api/setup/save-connection` rewrites stored Mongo URI with no CSRF or origin check** — single-request DB takeover. (v4 templates, v3/v1 web)
3. **`tests/scenarios/T10/` contains REAL production data** — wallet `SZES - 2831028`, real companyId, real user UUID, real security names. Directly violates user's `feedback_no_example_securities` memory. Needs purge + `git filter-repo`. (v4 tests)
4. **XSS in `templates/config.html`** — only template without `escHtml`. Company/entity names from Mongo rendered raw into template literals. (v3 templates)
5. **`escHtml` does not escape `'`** — every OTHER template's escape also insufficient for single-quoted `onclick` attributes. (v4 templates)
6. **`rentabilidade_mes` passes daily `returnNavPerShare` as monthly** — user-facing financial number off by ~20×. (v3/v2 reports)
7. **`config_save` truncates `data/config.json` with no write-safety** — malformed payload + OneDrive PermissionError mid-write corrupts the allow-list that drives every page. (v1 web)
8. **`liquidationDate.startswith(...)` crashes when Mongo returns `datetime`** — mixed-pipeline vintage means every subsequent report run may crash after 30s of queries. (v4 reports; regression from v2)
9. **Pre-auth unvalidated URI + SSRF + credential echo on `/api/setup/*`** — PyMongo exceptions embed URI; `MongoClient` probes private/link-local IPs. (v3/v2/v1 web)
10. **`requirements.txt` has zero version pins** — supply-chain risk. (v4/v3/v2 templates)

### HIGH (strongly recommended)
- **`get_company_filter()` enforced only on 5 HTML routes; 8+ `/api/...` endpoints accept `companyId` without check** — client-side-only filter (v3 web, consolidated)
- **Non-atomic JSON writes on OneDrive — 7 sites** — multi-threaded WSGI racers see empty/partial files (v3 web, consolidated)
- **`str(exc)` + `traceback.print_exc()` leaks on 5 sites** — PyMongo exceptions embed failed command documents with credentials (v3 web, consolidated)
- **`db_unreachable.html` renders raw `{{error}}` post-auth** — credential leak on any DB error, distinct from /setup pre-auth (v4 web)
- **Unbounded `limit` on `/api/nav/rows|detail-grid`** — DoS via 10M-iteration Python loop (v3 web)
- **N+1 in `/api/validacao-rentabilidades/securities`, `/calculate-thresholds`** — O(wallets) queries on hot path (v3 web)
- **MC sampler for `MISSING_EXECUTION_PRICE` reverse-engineers `PU` incorrectly** — silent financial bug (v4 web)
- **Scenario-capture race condition** — concurrent POSTs overwrite each other (v4 web)
- **BSON mixed-type sort order `nav_list[-1]` wrong** — strings < dates in Mongo sort, silent wrong-number (v4 reports)
- **`patrimonio_inicial` uses previous daily NAV not month-start** — off by up to 29 days of market movement (v3/v2 reports)
- **`volatilidade_anualizada` uses sqrt(12) on daily returns** — off by factor ~4.6× (v3/v2 reports)
- **N+1 Mongo round-trips in report scripts** — 9 sites (v3 reports, consolidated)
- **Iframe event listeners leak** — shell.html hides but never destroys iframes; setInterval/keydown handlers accumulate (v3 templates)
- **`postMessage` no origin check; target `'*'`** — XSS primitive if any 3rd-party iframe ever added (v4 templates)
- **Tailwind Play CDN explicitly "not for production"** — root cause of dynamic-class bugs + supply-chain risk (v4 templates)
- **`settings.html` wipes `company_filter` if Save clicked before companies load** — data loss (v4 templates)
- **Dynamic Tailwind `bg-${color}-50` classes silently won't render** — conciliação flag badges render colorless on first paint (v3 templates)
- **`.gitignore` missing every `data/*.json` cache** — real security data leaks on `git add data/` (v3 templates)
- **Real-looking CNPJs in `data/settings.json:45-52`** — violates `feedback_no_example_securities` memory (v3 templates)

### MEDIUM (worth addressing)
- Doc↔code divergence: `expectedValue` sign convention (DIAGNOSTICO doc says `+`, code uses `−`, code is correct) (v3 web)
- Doc↔code divergence: `rentabPU` formula differs between docs/validacao_rentabilidades.py (event-corrected) / conciliacao.py (raw) — same security same date produces different numbers (v3 web)
- Bayesian `tolerance=0.01` is 1000× looser than Conciliação `diffThresholdPct=0.001` — contradictory user-facing semantics (v4 web)
- `date.today()` local time vs Mongo UTC — 1-day shift for late-evening BRT queries (v4 web)
- `_sum_cash` duplicated byte-for-byte in caixa.py + conciliacao.py (v4/v3 web, consolidated)
- `wallet_filter_query` produces invalid `$not:{$size:0}` Mongo filter — latent crash button (v3 web)
- `find_one({"_id": wallet_id})` convention violation — 5 sites try string _id first (v3 web, consolidated)
- Excel formula injection via `beehusName`/`description` raw writes (v4 reports)
- `data/` dir assumed to exist — FileNotFoundError on fresh checkout (v4 reports)
- `alloc_by_institution` collapses wallets with same entity into duplicate rows (v4 reports)
- `bayesian_config.json: monte_carlo_samples=200` too low for stable posteriors (v4 templates)
- SSRF specifics: no scheme allow-list, no private-IP rejection (v4 templates)
- Config mismatch `diffThresholdPct: 0.001` (disk) vs `0.01` (template fallback) — 10× disagreement when /config fetch fails (v3 templates)
- Report output schema entirely undocumented (v3 reports)
- Formula 9 divergence: `simulate_recalculo.py` uses compounding shortcut vs documented NAV ratio (v3 tests)
- Threshold unit confusion: `diffThresholdPct` "percent of percent" (v2 web)
- `numDays` from user JSON passed to `.limit()` without validation (v4 web)
- `_check_data_quality` decimal-shift heuristic sign-blind + float-imprecise (v2 web)
- `diagnosticFeedback.insert_one` accepts arbitrary untrusted fields (v2 web)
- Missing CSRF protection on POST endpoints (v3/v2 templates)
- Overly-broad `.claude/settings.json Read(//c/Users/**)` grant (v3/v2 templates)
- Hardcoded year literals `"2024-12"`/`"2025-12"` don't derive from `REFERENCE_MONTH` (v3/v2 reports)
- Hardcoded `fx_rate = 6.19` duplicated 3× (v3/v2 reports)
- Non-atomic file writes in report scripts (v3 reports, consolidated)
- Region inference `"US" in name` matches URSSO, AUSTRIA, etc. (v3/v2 reports)
- Liquidity bucket heuristic: early-substring-wins ordering bug (v3/v2 reports)
- Test oracle always exits 0 — CI regressions silent (v3/v2 tests)

### LOW (cleanup)
~20 items across backup-file stale, unused imports, empty `__init__.py`, stylistic, documentation polish, etc. — see individual review files.

---

## Deliverables (in this folder)

| File | Contents |
|---|---|
| [`SUMMARY.md`](SUMMARY.md) | This file |
| [`v1/web.md`](v1/web.md), [`v1/reports.md`](v1/reports.md), [`v1/tests.md`](v1/tests.md) | v1 baseline reviews |
| [`v2/web.md`](v2/web.md), [`v2/reports.md`](v2/reports.md), [`v2/tests.md`](v2/tests.md), [`v2/templates_deps.md`](v2/templates_deps.md) | v2 with systematic sweeps |
| [`v3/web.md`](v3/web.md), [`v3/reports.md`](v3/reports.md), [`v3/tests.md`](v3/tests.md), [`v3/templates_deps.md`](v3/templates_deps.md) | v3 with consolidation + docs cross-check |
| [`v4/web.md`](v4/web.md), [`v4/reports.md`](v4/reports.md), [`v4/tests.md`](v4/tests.md), [`v4/templates_deps.md`](v4/templates_deps.md) | v4 anti-fragile mode (new findings only) |
| [`../.claude/agents/code-reviewer.md`](../.claude/agents/code-reviewer.md) | Final agent spec (v4) — reusable for future reviews |

---

## How to use the agent going forward

- Invoke via `Agent` tool with `subagent_type: general-purpose` and a prompt that says "Read `.claude/agents/code-reviewer.md` first."
- For fresh reviews, omit the prior-findings block → agent starts from v1-equivalent scan.
- For delta reviews after code changes, pass prior findings in the prompt → anti-fragile mode kicks in automatically (v4 feature).
- The spec captures this project's specific idioms (pt-BR, OneDrive path, `_DbProxy`, `get_company_filter` enforcement convention, etc.). Copy to a new project only with adaptations.

---

## Immediate-action recommendations (operator focus)

1. **Delete `tests/scenarios/T10/` and purge from git history** (real customer data leak — CRITICAL, most urgent)
2. **Pin `requirements.txt` versions** (1-line fix, blocks supply-chain drift)
3. **Scrub PyMongo URIs from `app.py:34,41` error handler** (2-line fix, blocks credential leak)
4. **Add `data/config.json, data/securities_cache.json, data/unprocessed_security_types.json, data/rentability_thresholds.json, data/saved_reports.json` to `.gitignore`** (5-line fix)
5. **Centralize `atomic_write_json(path, obj)` in `db.py`** and route all 7 JSON save paths through it (30-line fix, closes entire class of data-loss bugs)
6. **Bind Flask to `127.0.0.1` by default** in `app.py:59` (`host="127.0.0.1"`) — closes LAN exposure until auth is added (1-line fix)
7. **Add `'` escape to every `escHtml`** in templates (single regex) — closes JS-injection in every single-quoted `onclick`
8. **Fix `rentabilidade_mes` in both report scripts** to compound daily returns within `REFERENCE_MONTH` (critical financial correctness)
9. **Gate every `/api/...` endpoint that reads `companyId` through `get_company_filter()` helper** — closes cross-company info leak
10. **Delete `templates/conciliacao-OSAON168.html` stale backup** (hygiene, and makes future grep noise disappear)
