# Code Review — Templates+Deps+JS+Configs [v4 anti-fragile]

## Summary
- Files reviewed: 12 templates, requirements.txt, .gitignore, .claude/settings*.json, small data/*.json configs
- **New findings: 9**
- Prior confirmed: 14
- Prior refuted: 0
- Breakdown (new only): CRITICAL: 2 / HIGH: 3 / MEDIUM: 3 / LOW: 1

## New findings

### [CRITICAL] `/api/setup/save-connection` rewrites stored Mongo URI with no auth/CSRF/origin — pages/setup.py:32-54, setup.html:145
**Category:** security
**Impact:** Pre-auth POST reachable via `before_request` whitelist. `get_windows_user()` reads SERVER-process USERNAME (not requester), so any caller overwrites the same user's URI. Flask no CSRFProtect. Browser will happily POST `application/json` cross-origin via fetch. Attacker page can (a) persist malicious URI in user_connections.json, (b) trigger `db._init` to point running Flask at attacker's Mongo. Subsequent requests read attacker-controlled data; NAV/Conciliação dashboards render bogus numbers; write paths get poisoned. Single-request full DB takeover.
**Recommendation:** Initialize `flask_wtf.CSRFProtect(app)`; bind to `127.0.0.1` only; validate URI scheme + reject private IPs.

### [CRITICAL] `escHtml` does not escape `'` — JS-injection in every single-quoted `onclick` embedding user data — consolidated, 3+ live sites
**Category:** template / security
**Sites:** validacao_rentabilidades.html:224 (beehusName from Mongo), posicoes.html:353, conciliacao.html:2296,2302,2307,2311, caixa.html:124, painel.html:341.
**Evidence:** Every `escHtml` copy replaces `&`, `<`, `>`, `"` — but NOT `'`. Security named `fund'); alert(1); //` terminates JS string literal in `onclick="openDetail('${escHtml(s.beehusName)}')"`.
**Impact:** v3 flagged config.html's total lack of `escHtml` — but every OTHER template's `escHtml` is also insufficient for single-quoted attribute contexts. Mongo-controlled security names are a real attack vector.
**Recommendation:** Add `.replace(/'/g, "&#39;")` to every `escHtml`. Extract to `static/common.js` — fix once.

### [HIGH] `settings.html` wipes `company_filter` if Save clicked before companies load — settings.html:137-164
**Category:** correctness / frontend JS behavior
**Evidence:** `_companies` starts `[]`. `[].every(...)` returns true → `allVisible=true` → `companyFilter=[]` POSTed. Silent wipe of whatever was there.
**Impact:** `data/settings.json` we reviewed has 6 CNPJs in `company_filter`. One fast click on laggy connection nukes them. Server accepts `[]` as semantically valid ("show all") — no guard.
**Recommendation:** Disable Save until `_companiesLoaded=true`, OR server rejects `company_filter: []` without `confirmClearFilter: true`.

### [HIGH] `postMessage` lacks origin check; target origin is `'*'` — shell.html:208-213, painel.html:313
**Category:** security / frontend JS behavior
**Impact:** Painel sends with `'*'` target; shell listens with no `event.origin === location.origin` check. Today limited to same-origin iframes, but latent: any future embedded help/chat/CDN iframe can leak `companyId`/`date` context AND force shell to navigate any registered page with attacker-controlled query string.
**Recommendation:** Replace `'*'` with `location.origin` in postMessage; add `if (e.origin !== location.origin) return;` first line of handler.

### [HIGH] Tailwind Play CDN explicitly "not for production" — base.html:11, shell.html:11, setup.html:7, db_unreachable.html:8
**Category:** dependency / correctness
**Evidence:** `cdn.tailwindcss.com` is the Play CDN documented as "Do not use this in production". Recompiles stylesheet in browser on every page load via 300KB+ JIT compiler.
**Impact:** Extends v3's CDN unpinned finding. JIT runtime scan is root cause of v3's dynamic-class bugs (conciliacao.html:1417, 2088) and `_BAYES_RESOLVED_CLASSES` workaround — a proper build-time Tailwind would've errored at build, not silently dropped classes in prod.
**Recommendation:** Pre-build `static/tailwind.css` via `npx tailwindcss -i in.css -o static/tailwind.css --minify --content 'templates/**/*.html'`; delete CDN script. Also resolves dynamic-class finding.
**Extends prior finding:** v3 Tailwind CDN unpinned.

### [MEDIUM] `pages/setup.py` SSRF + credential-leak specifics — pages/setup.py:24,29,40,43
**Category:** security
**Evidence:** No scheme allow-list (`mongodb://`/`mongodb+srv://` only), no private-IP rejection (127.0.0.1, 169.254.0.0/16 AWS IMDS, 10/8, 192.168/16). Raw URI fed to `MongoClient`; `str(e)` on `ConfigurationError` re-emits URI.
**Impact:** Pre-auth SSRF + credential disclosure. Sharpens v3's general SSRF finding with concrete PyMongo exception behavior.
**Recommendation:** Validate scheme + reject private IPs BEFORE `MongoClient`; return generic `"Conexão falhou"` + error code, never `str(e)`.

### [MEDIUM] Static assets referenced without cache-busting — base.html:62, shell.html:59,66
**Category:** frontend JS behavior / dependency
**Impact:** `/static/logo.png` no version suffix. Browsers cache far-future. Sets bad precedent for future `static/tailwind.css`.
**Recommendation:** `{{ url_for('static', filename='...') }}` + `ASSET_VERSION` query param.

### [MEDIUM] `data/bayesian_config.json`: monte_carlo_samples=200 too low; gaussian_sigma 10× tighter than tolerance — data/bayesian_config.json:4-6
**Category:** data-file config / financial
**Evidence:** `tolerance: 0.01, gaussian_sigma: 0.001, monte_carlo_samples: 200`. 200 MC samples → SE on probability ≈ ±3.5% → flip `confidence_overrides` rankings near ties. `gaussian_sigma` 10× tighter than tolerance → near-delta likelihood → over-confident posterior mode.
**Impact:** Bayesian ranking driving user-visible "Implementar" buttons is statistically noisy and may recommend different top fix per run. Non-deterministic financial-correction advice.
**Recommendation:** Raise to `monte_carlo_samples >= 1000`; document sigma/tolerance invariant via existing `_comment` key; cross-reference docs/CONCILIACAO_BAYESIAN.md.

### [LOW] `.claude/settings.json` grants cross-project Read(Relatorios/) — .claude/settings.json:10-12
**Category:** convention
**Impact:** Three entries grant Read on sibling `SWAT/Relatorios/` project. Inadvertent `Read(../Relatorios/...)` succeeds silently.
**Recommendation:** Remove the three entries.

## Patterns observed
- **No CSRF / no auth / no origin checks anywhere** — individually each is "internal tool accepted risk"; together they compound to full DB takeover via auth-less URI rewrite.
- **`escHtml` copy-paste 7× all missing `'` escape** — settings.html weaker still. config.html has none (v3 CRITICAL). Single shared `static/common.js` fixes all eight.
