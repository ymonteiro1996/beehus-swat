# Code Review — Web/Backend Layer [v4 anti-fragile]

## Summary
- Files reviewed: app.py, db.py, pages/__init__.py, pages/painel.py, pages/nav.py, pages/config.py, pages/setup.py, pages/stubs.py, pages/caixa.py, pages/posicoes.py, pages/validacao_rentabilidades.py, pages/conciliacao.py, pages/bayesian.py; docs/CONCILIACAO_BAYESIAN.md, docs/CONCILIACAO_SIMULATION.md, docs/CONCILIACAO_RECALCULO.md, docs/MANUAL_OTIMIZACAO_BAYESIANA.md, docs/PAINEL_DIAGNOSTICO.md
- **New findings: 10** (1 consolidated)
- Prior confirmed: 15
- Prior refuted: 0
- Breakdown (new only): CRITICAL: 1 / HIGH: 3 / MEDIUM: 4 / LOW: 2

## New findings

### [CRITICAL] No authentication layer — every API endpoint writable by anyone on the network once DB is initialized — app.py:45-53
**Category:** security
**Impact:** Only gatekeeper is `before_request` `_ready()` check. After first successful `/api/setup/save-connection`, every subsequent request bypasses. No login, session, CSRF, IP allowlist, HTTP Basic. Destructive routes exposed to anyone on LAN:
- `POST /api/config/save` (overwrites allow-list)
- `POST /api/settings/save`, `POST /api/nav/settings/save`
- `POST /api/validacao-rentabilidades/calculate-thresholds`
- `PUT/POST /api/conciliacao/config`
- `POST /api/conciliacao/capture-scenario` (writes 10 JSON files)
- `POST /api/conciliacao/diagnose/feedback` (arbitrary docs into MongoDB with no validation)
- `POST /api/setup/save-connection` (repoint prod Mongo — from V3 #1)

V3 flagged specific data leaks assuming authenticated callers. "Internal Flask dashboard" ≠ "authenticated". CLAUDE.md doesn't mandate deployment binding to loopback only.
**Recommendation:** Add shared-secret HTTP Basic or Flask-Login against Windows-user whitelist in `before_request`. If out of scope, document deployment requirement: bind 127.0.0.1 only. Flag `/save-connection` as post-ready endpoint requiring URI re-prompt.

### [HIGH] `PyMongoError` handler renders raw `str(err)` — post-auth credential leak on any DB error — app.py:31-34
**Category:** security
**Evidence:** `render_template("db_unreachable.html", error=str(err))` → template prints `{{error}}` in `<pre>`. PyMongo exceptions embed URI with credentials on ConfigurationError/InvalidURI/some ServerSelectionTimeoutError variants. This is a POST-AUTH route — distinct from V3 #1's `/api/setup/*` pre-auth vector.
**Impact:** Credential disclosure on any DB error (network glitch, Atlas rolling restart, expired cert).
**Recommendation:** Scrub `mongodb(\+srv)?://[^/]*@` before render. `logging.getLogger(__name__).error("pymongo error", exc_info=err)` for full server-side log. Apply same to app.py:41.

### [HIGH] MC sampler for MISSING_EXECUTION_PRICE reverse-engineers `PU` incorrectly — pages/bayesian.py:655-674
**Category:** financial
**Evidence:** `_classify_tier` (line 417-419) built `high = max(pu, former) * (1 + margin)`. Sampler reconstructs `pu = high / (1 + margin)` → this equals `max(pu, formerPu)` NOT `pu`. When `formerPu > pu` (price drop, common), sampler substitutes formerPu for pu. Also string-parses `amt_diff` from `impactFormula` — fragile, silent fallback to 0.
**Impact:** Silent financial-correctness bug on user-facing "best fix" recommendation. Posterior averages over 200 MC samples are biased.
**Recommendation:** Store `pu` and `amountDiff` as first-class fields on distribution dict. Stop parsing human-readable formula strings at runtime.

### [HIGH] `_next_scenario_id` race condition — concurrent capture-scenario POSTs collide on same `T<n>/` directory — pages/conciliacao.py:2138-2146, 2354-2378
**Category:** concurrency
**Evidence:** `listdir → max+1` read-then-write without lock. Two concurrent POSTs compute same `T<n>`; `os.makedirs(..., exist_ok=True)` doesn't detect collision; second request's `_write()` silently overwrites first's files.
**Impact:** Captured regression-test scenario destroyed before anyone knows. Forbidden per CLAUDE.md.
**Recommendation:** `os.mkdir` without `exist_ok`, retry with bumped number on `FileExistsError`. OR `tempfile.mkdtemp(prefix="T_", dir=_SCENARIOS_DIR)` → rename. Also make `_write()` atomic.

### [MEDIUM] Bayesian `tolerance = 0.01` is 100× looser than conciliação's own diffThreshold — data/bayesian_config.json:4, data/conciliacao_config.json:2, pages/bayesian.py:918
**Category:** financial / doc divergence
**Evidence:**
- bayesian_config.json: `tolerance: 0.01` (decimal = 1% return-gap = "resolved")
- MANUAL_OTIMIZACAO_BAYESIANA.md:229 labels "0.01 (1%)"
- conciliacao_config.json: `diffThresholdPct: 0.001` (percent → 0.00001 decimal = 0.001%)

Disagree by **1000×**. A navPackage with 0.5% mismatch is flagged red on Conciliação dashboard AND simultaneously "Gap resolvido" by Bayesian validator.
**Impact:** User-facing contradiction. Best-fix validation claims `valid=True` on navPackages shown as un-reconciled.
**Recommendation:** Decide decimal or percent. Rename to `tolerance_decimal` or `tolerance_pct`. Update docs. Minimum: set default to `0.0001`.

### [MEDIUM] `date.today()` uses local time but Mongo stores UTC — 1-day shift for late-evening/early-morning BRT queries — db.py:117-125, 210-216
**Category:** correctness / financial
**Impact:** Between 21:00-23:59 BRT (00:00-02:59 UTC), `date.today()` returns BRT day while Mongo `positionDate` has rolled to UTC next day. Evening workers see Nav cells red for positions that WERE ingested under UTC date. `biz_days_elapsed` off by 1.
**Recommendation:** `from datetime import datetime, timezone, timedelta; BR_TZ = timezone(timedelta(hours=-3)); today = datetime.now(BR_TZ).date()`. OR `datetime.now(timezone.utc).date()` if Mongo stores UTC. Document in CLAUDE.md.

### [MEDIUM] `numDays` from user JSON passed to PyMongo `.limit()` without validation — pages/validacao_rentabilidades.py:281, 302
**Category:** error-handling
**Impact:** `{"numDays": "60"}` (string) → `TypeError`. `{"numDays": -1}` → `OperationFailure`. `{"numDays": 1e9}` → list() 1B docs. No try/except, no isinstance, no upper bound.
**Recommendation:** `int()` with try/except ValueError → 400; clamp to `[1, 365]`.

### [MEDIUM] `_sum_cash` duplicated byte-for-byte — pages/caixa.py:9-23, pages/conciliacao.py:492-503
**Category:** duplication
**Recommendation:** Move to db.py as shared `sum_cash`.

### [LOW] Bayesian MC uses module-level `random` — non-deterministic posteriors across requests — pages/bayesian.py:661, 683, 700
**Category:** correctness / testing
**Impact:** `random.uniform`/`choices`/`gauss` from shared Mersenne Twister. Concurrent requests interleave. Same request re-run yields different posteriors by a few percent.
**Recommendation:** Accept optional `seed` in body. OR deterministic hash of `(walletId, date, gap_cash)`. Document or gate behind seed knob.

### [LOW] `requirements.txt` has zero version pins — requirements.txt:1-6
**Category:** dependency
**Recommendation:** Pin `flask~=3.0`, `pymongo~=4.6`, etc. Commit lock file. Add `.python-version`.

## Patterns observed
- **No authentication boundary** — F1 is architectural. All V3's cross-company-leak, credential-leak, JSON-overwrite findings compound because there is no identity layer.
- **Shared-helper drift** — `_sum_cash` duplication (F8); `db.py` missing helpers that multiple pages reinvent.
- **Doc ↔ code tolerance semantics** — F5 (tolerance 1% vs 0.001%) + V3 #10/#11 show Bayesian and Conciliação modules have never been reconciled on percent-vs-decimal. A "units convention" addendum to docs/CONCILIACAO_RECALCULO.md would prevent next drift.
