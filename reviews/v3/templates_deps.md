# Code Review — Templates, Dependencies, Data-file Configs [v3]

## Summary
- Files reviewed: 12 templates; `requirements.txt`; `.gitignore`; `.claude/settings*.json`; `data/bayesian_config.json`, `data/conciliacao_config.json`, `data/default_blacklist.json`, `data/settings.json` (nav_settings.json missing)
- Total findings: 14 (2 consolidated)
- Breakdown: CRITICAL: 1 / HIGH: 3 / MEDIUM: 6 / LOW: 4

## Systematic checks performed
- Index/DB checks: N/A
- File write safety: N/A
- get_company_filter: N/A
- valid_wallet_ids: N/A
- str(exc) leakage sites: 0 in templates; `db_unreachable.html:41` renders `{{error}}` — auto-escaped but upstream scrubbing unverified
- Unguarded ObjectId(): N/A
- Unvalidated int()/float(): N/A (client-side `parseInt` noted LOW; server must revalidate)
- **Docs ↔ code cross-check**: `data/conciliacao_config.json` has `diffThresholdPct: 0.001` but template fallback at conciliacao.html:596,599 is `0.01` — 10× mismatch. Flagged.
- **Security claims verified via Bash**: 0 (no bash access; finding below verified by direct reading of unescaped template interpolation)

## Findings

### [CRITICAL] HTML/JS injection via unescaped company & entity names — templates/config.html:34,38,42,47
**Category:** template / security
**Impact:** `config.html` is the ONLY template without `escHtml` discipline. Assembles entire page from `/api/config/entities` response via template literals. A company named `"><img src=x onerror=...>` executes as JS. A compromised `/config` page can exfiltrate `user_connections.json` credentials.
**Recommendation:** Add `escHtml` helper (copied from settings.html:133-135) and wrap every `${co.companyName}`, `${e.entityName}`, `${co.companyId}`, `${e.entityId}` interpolation. Include `"` and `'` escaping for attribute contexts.

### [HIGH] Unbounded polling setInterval with no termination guard — templates/posicoes.html:486-488
**Category:** frontend JS behavior
**Impact:** If `loadDates()` fails, `_selectedDate` never sets, interval polls every 150ms forever. Interval survives iframe-hide in shell.html.
**Recommendation:** Cap attempts (`if (++attempts > 40) clearInterval`).

### [HIGH] Orphan setInterval/event listeners survive page swap; shell keeps iframes alive — shell.html:132-174, posicoes.html:486
**Category:** frontend JS behavior
**Impact:** Hidden iframes keep running timers, fetches, `document.addEventListener("keydown", ...)` handlers (posicoes.html:466-468, validacao_rentabilidades.html:312-314, conciliacao.html:2619-2633, config.html:144). Every Escape keypress fires across all hidden frames.
**Recommendation:** Destroy iframes on navigate-away (`iframe.remove(); delete _iframes[key]`) OR guard handlers on `document.visibilityState`.

### [HIGH] Dynamic Tailwind color classes silently won't render — conciliacao.html:1417, 2088
**Category:** frontend JS behavior / correctness
**Impact:** `bg-${color}-50 text-${color}-700` interpolated at runtime; Tailwind CDN JIT scans DOM/scripts for token literals. `bg-${color}-50` isn't a literal → badges render without color. File already has workaround for `_BAYES_RESOLVED_CLASSES` at 1596-1611.
**Recommendation:** Apply same static-map pattern: `_FLAG_BADGE_CLS = {red: "bg-red-50 text-red-700 border-red-200", ...}`.

### [MEDIUM] Tailwind CDN unpinned + no SRI — 5 occurrences across 4 templates (consolidated)
**Category:** dependency / security
**Sites:** base.html:11-12, shell.html:11-12, setup.html:7, db_unreachable.html:8-9.
**Impact:** Tool stores MongoDB credentials in user_connections.json. CDN compromise or DNS MitM on open network → arbitrary JS on /setup → credential exfil.
**Recommendation:** Download to `static/tailwind.css`; reference via `url_for('static', ...)`. If CDN kept: pin version + add SRI.

### [MEDIUM] `requirements.txt` has no version pins — requirements.txt:1-6
**Category:** dependency
**Recommendation:** Pin to `flask==3.0.3`, `pymongo==4.8.0`, etc.

### [MEDIUM] `.gitignore` missing every `data/*.json` cache — .gitignore:1-12
**Category:** dependency / security
**Impact:** Only `user_connections.json` listed. `data/config.json`, `securities_cache.json`, `unprocessed_security_types.json`, `saved_reports.json`, `rentability_thresholds.json` unprotected. `settings.json:45-52` already contains what look like real CNPJs (see next finding).
**Recommendation:** Add `data/config.json`, `data/securities_cache.json`, `data/unprocessed_security_types.json`, `data/rentability_thresholds.json`, `data/saved_reports.json`.

### [MEDIUM] Real-looking CNPJs in `data/settings.json:45-52` — possible examples-leaked-to-prod
**Category:** data-file config
**Evidence:** `company_filter` mixes placeholders (`12312312312312`, `10000000000000`, `00000000000000`) with `23313334000110` and `54577402000182` — plausible real CNPJs with `0001` branch marker structure. Conflicts with `feedback_no_example_securities` memory entry.
**Recommendation:** Confirm real or fake. If real, strip before commit and leave `company_filter: []`. If fake, use clearly-synthetic values like `99999999000199`.

### [MEDIUM] `db_unreachable.html` renders raw `{{ error }}` — verify upstream scrubs URI — templates/db_unreachable.html:41
**Category:** template / security
**Impact:** Jinja auto-escapes (safe HTML), but PyMongo exception messages contain full URI including credentials. Page reachable pre-auth (DB auth failed).
**Recommendation:** Python caller scrubs via `re.sub(r'mongodb(\+srv)?://[^@]*@', 'mongodb\\1://***@', str(exc))`, OR drop `{{error}}` from template entirely.

### [MEDIUM] Config mismatch: `diffThresholdPct` is 0.001 on disk, 0.01 template fallback — data/conciliacao_config.json:2, conciliacao.html:596,599
**Category:** docs ↔ config consistency
**Impact:** When `/api/conciliacao/config` fails, template uses 0.01 fallback while disk says 0.001 — 10× off. User unknowingly uses wrong filter.
**Recommendation:** Match template default to shipped config value (`let _thresholdPct = 0.001`), or block UI behind successful config fetch.

### [LOW] `shell.html` splash-screen setTimeout not cleared if user navigates before 2.2s — shell.html:227
**Category:** frontend JS behavior

### [LOW] `painel.html:693` `implementDiagnostic` 2.5s timer with no cleanup — painel.html:679-699

### [LOW] Stale backup `templates/conciliacao-OSAON168.html` — duplication
**Category:** dead code
**Recommendation:** Delete or move to `.archive/`.

### [LOW] `.claude/settings.json` allowlist has path typo — .claude/settings.json:25
**Evidence:** `OneDScene` should be `OneDrive`. Dead entry.

## Patterns observed (cross-file themes)
- **Fetch chains without `.catch()`** — validacao_rentabilidades.html:156-165 (`loadDates`), :193-202 (`selectDate`), caixa.html:110-119 (`loadDates`). Users see "Carregando..." forever on API error.
- **Sidebar hash-anchor links broken outside `shell.html`** — base.html:65-107. When a page opened directly (not inside shell), clicking sidebar `/#painel` is no-op.
- **Every per-page JS reinvents `escHtml`/`fmtMoney`/`fmtNum`/`fmtPct`** — 6 near-identical copies (painel, caixa, posicoes, validacao_rentabilidades, conciliacao, settings). settings.html:133 version is weaker (drops `"` escape). config.html has none. Extract to `static/common.js`, include via base.html — single source of truth, config.html gets escaping for free.
