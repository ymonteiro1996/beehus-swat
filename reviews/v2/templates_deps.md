# Code Review — Templates & Dependencies/Config [v2]

## Summary
- Files reviewed: all 12 `templates/*.html`, `requirements.txt`, `.gitignore`, `.claude/settings.json`, `.claude/settings.local.json`
- Total findings: 7
- Breakdown: CRITICAL: 1 / HIGH: 2 / MEDIUM: 2 / LOW: 2

## Systematic checks performed
- Index coverage: N/A
- File write safety: N/A (0 open-w in scope)
- get_company_filter enforcement: N/A (no routes)
- valid_wallet_ids call-sites: N/A
- str(exc) leakage sites: 1 (db_unreachable.html via app.py — see HIGH finding)
- Unguarded ObjectId() sites: N/A
- Unvalidated int()/float() on request args: N/A
- Auto-escape bypasses (`| safe`, `{% autoescape off %}`, `Markup(...)`): 0 found
- `<script>` blocks embedding Flask vars: 0 found
- `onclick=...` handlers embedding raw Flask data: 0 found
- Forms missing CSRF: no `<form>` element in project; all state changes via `fetch(..., POST)`; no CSRFProtect registered
- Hardcoded URLs vs `url_for()`: `url_for` never used — project-style choice, not flagged

## Findings

### [CRITICAL] `requirements.txt` has zero version pins — supply-chain & reproducibility risk — requirements.txt:1-6
**Category:** dependency
**Impact:** Any `pip install -r requirements.txt` pulls latest of Flask/pymongo/openpyxl. Compromised release ships directly. Two installs on different days produce different environments.
**Recommendation:** Pin every dep (`pip freeze` → trim to direct deps with `==`). Consider `requirements.lock` / pip-compile capturing transitive pins for Flask, Jinja2, Werkzeug, MarkupSafe, itsdangerous.

### [HIGH] `db_unreachable.html` leaks full PyMongo error string — may contain URI/credentials — templates/db_unreachable.html:41
**Category:** template / security
**Evidence:** `<pre>{{ error }}</pre>` fed by `app.py:34,41` via `str(err)`. PyMongo exceptions (`ConfigurationError`, `InvalidURI`, some `ConnectionFailure` variants) embed URI verbatim incl. credentials.
**Impact:** User who typoed password into `/setup` can see their own password rendered back in HTML. In screenshare/multi-user contexts, any PyMongo error embedding URI is etched into a viewable page. Jinja auto-escapes (no XSS), but value itself is the problem.
**Recommendation:** Scrub upstream in app.py with regex like `re.compile(r'(mongodb(?:\+srv)?://)[^@\s]+@', re.I)` → `\1***:***@`, OR replace the `<pre>` with generic "consulte o administrador" and log server-side.

### [HIGH] `.gitignore` does not cover `data/*.json` containing real security data — .gitignore:1-12
**Category:** dependency
**Impact:** `data/securities_cache.json` (4.9 MB), `data/unprocessed_security_types.json` (3.9 MB) contain real Beehus security identifiers cached from Mongo (not example data). `data/saved_reports.json` may contain real wallet snapshots. None excluded. `user_connections.json` alone is covered.
**Recommendation:**
```
# Local state & runtime caches
data/
!data/default_blacklist.json
# Excel report outputs
*.xlsx
```

### [MEDIUM] POST endpoints have no CSRF protection and app-level CSRF not enabled — project-wide
**Category:** template / security
**Call sites:** config.html:137-141, settings.html:151-163, validacao_rentabilidades.html:271-275, conciliacao.html:613-616, conciliacao.html:2524-2534, setup.html:118-121, setup.html:145-148.
**Impact:** `/setup` accepts arbitrary Mongo URI via POST. Same-origin fetch from malicious page + dashboard running on localhost:5000 = vulnerability when auth is eventually added.
**Recommendation:** Initialize `flask-wtf`'s `CSRFProtect(app)`; embed `<meta name="csrf-token" content="{{ csrf_token() }}">` in base.html/shell.html.

### [MEDIUM] `.claude/settings.json` grants overly-broad `Read(//c/Users/**)` — .claude/settings.json:26
**Category:** dependency
**Impact:** Allows agent to read anything in `C:\Users\` — other Windows profiles, `~/.ssh`, browser profiles, saved-password files. Line 25 also typo'd: `OneDScene` → dead entry.
**Recommendation:** Remove bare `Read(//c/Users/**)` entry. Replace with minimal scoped paths. Remove typo'd `OneDScene` entry.

### [LOW] `base.html` sidebar uses hash-fragment URLs broken outside shell frame — templates/base.html:65-107
**Category:** template
**Impact:** When child page rendered standalone (direct URL visit without `?_frame=1`), duplicate sidebar renders and its links reload everything via `/#painel`.
**Recommendation:** Add `target="_top"` to sidebar links OR drop base.html sidebar entirely and rely on shell.html as single chrome owner.

### [LOW] `posicoes.html` auto-init setInterval polling never self-cleans on error — templates/posicoes.html:485-489
**Category:** template
**Impact:** If `loadDates()` fails silently, polling at 150ms runs forever. ~24k fires/hour. Interval retains closure over whole module scope.
**Recommendation:** Bounded retry: `if (++_attempts > 40) clearInterval(_wait)`.

## Patterns observed (cross-file themes)
- **Client-side escape discipline is consistent** — every template renders fetched JSON via local `escHtml(s)` helper. Jinja auto-escape never bypassed. The 141 KB `conciliacao.html` vanilla-JS surface is not currently an XSS vector. One outlier: `config.html:34-72` inserts `co.companyName`, `e.entityName`, `e.method` into template literals without `escHtml` — low risk because Mongo-controlled source.
- **Zero use of Flask `url_for`** — every URL is literal string. Defensible in small blueprint-per-page app, but route rename = find-replace across 12 templates.
- **Dependency hygiene is the weakest area** — unpinned `requirements.txt`, no `.python-version`, `.gitignore` missing `data/*.json`, CDN-loaded Tailwind (`https://cdn.tailwindcss.com` in base.html:11 / shell.html:11, officially "prototyping only" per Tailwind docs). Reproducibility risk + supply-chain drift.
