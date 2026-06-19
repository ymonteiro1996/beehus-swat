# SWAT – Controle de Cargas: Developer Guide

> Internal tool for monitoring daily data ingestion, position completeness, and report generation across investment funds.

---

## Table of Contents

1. [Tech Stack](#tech-stack)
2. [Project Structure](#project-structure)
3. [Architecture Overview](#architecture-overview)
4. [Running Locally](#running-locally)
5. [Database Setup](#database-setup)
6. [Configuration Files](#configuration-files)
7. [Backend Conventions](#backend-conventions)
8. [Frontend Conventions](#frontend-conventions)
9. [Adding a New Page](#adding-a-new-page)
10. [Adding a New API Endpoint](#adding-a-new-api-endpoint)
11. [MongoDB Collections Reference](#mongodb-collections-reference)
12. [Environment Variables](#environment-variables)
13. [Further Reading](#further-reading)

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3 + Flask |
| Database | MongoDB Atlas (via PyMongo) |
| Frontend | Jinja2 templates + Tailwind CSS (CDN) |
| Excel Export | openpyxl |
| Email | smtplib — Office 365 SMTP |
| Config persistence | JSON files (no SQL, no ORM) |

---

## Project Structure

```
Controle de cargas/
├── app.py                    # Flask app factory — registers all blueprints
├── db.py                     # DB proxy, config loaders, shared utilities
│
├── pages/                    # One blueprint file per page/feature area
│   ├── __init__.py
│   ├── bayesian.py           # Bayesian scoring/optimization for conciliation
│   ├── beehus_console.py     # Beehus console + transaction-type tooling
│   ├── caixa.py              # Cash validation
│   ├── carteira.py           # Wallet position upload pipeline
│   ├── conciliacao.py        # NAV conciliation + diagnostic engine
│   ├── config.py             # Entity selection + app settings
│   ├── controlpanel.py       # Control panel (home/landing page)
│   ├── correcoes.py          # Corrections store API (consumed by conciliacao)
│   ├── excecoes.py           # Exceptions / position stripping
│   ├── nav.py                # NAV package tracking
│   ├── precificacao.py       # Pricing lists / curva PUs
│   ├── repetir_posicoes.py   # Repeat/project positions (daily routine)
│   ├── setup.py              # First-run MongoDB connection registration
│   └── stubs.py              # Placeholder routes for future features
│
├── templates/                # Jinja2 HTML templates (rendered per page)
│   ├── base.html             # Master layout: sidebar + content block
│   ├── shell.html            # SPA shell (iframe host + nav); rendered by "/"
│   ├── beehus_console.html
│   ├── caixa.html
│   ├── carteira.html
│   ├── conciliacao.html
│   ├── config.html
│   ├── controlpanel.html
│   ├── db_unreachable.html   # Friendly page when MongoDB is unreachable
│   ├── excecoes.html
│   ├── precificacao.html
│   ├── repetir_posicoes.html
│   ├── settings.html
│   ├── setup.html
│   ├── stripping.html
│   ├── stub.html             # Shared "em construção" placeholder
│   └── partials/             # Jinja includes (e.g. _repetir_*.html)
│
├── static/
│   └── logo.png
│
├── config.json               # Selected entities + delay/method/responsible per pair
├── settings.json             # Global UI toggles
├── user_connections.json     # Per-Windows-user MongoDB connection strings
├── rentability_thresholds.json # Rentability validation thresholds
└── requirements.txt
```

---

## Architecture Overview

### Request lifecycle

```
Browser request
  └─► app.py before_request()
        ├─ db not ready? → redirect /setup
        └─ db ready? → route handler in pages/*.py
              └─ render_template(template, **ctx)  or  jsonify(data)
```

### DB Proxy pattern

`db.py` exposes a module-level `_DbProxy` singleton called `db`. On startup the proxy is uninitialised (`_ready()` returns `False`). Once the user registers a MongoDB URI via `/setup`, the proxy is initialised and all `db.collection_name` accesses route to the real PyMongo database.

```python
# db.py
db = _DbProxy()           # module-level singleton

# pages/setup.py
db_module.db._init(client[DB_NAME])   # called after successful connection test
```

This allows the application to start and serve the setup page without a valid DB connection, and without restarting.

### Config vs Settings

| File | Purpose | Keyed by |
|---|---|---|
| `config.json` | Which (companyId, entityId) pairs are monitored, plus per-pair delay / method / responsible | Array of objects |
| `settings.json` | Global UI flags and the wizard token blacklist | Single object |
| `user_connections.json` | MongoDB URI per Windows user | `{ "username": "uri" }` |

---

## Running Locally

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start the app
python app.py
# → http://localhost:5000

# 3. First visit → /setup page
#    Enter your MongoDB connection string.
#    It is saved to user_connections.json keyed by your Windows username.
#    Subsequent visits auto-connect using the saved string.
```

**Requirements:**
- Python 3.9+
- Access to the MongoDB Atlas cluster
- Windows (Windows username is used to look up the saved connection string)

---

## Database Setup

The app connects to a MongoDB database named **`Beehus`**. No migrations exist — collections are used as-is. See [MongoDB Collections Reference](#mongodb-collections-reference) for expected schemas.

The connection string is stored **per Windows user** in `user_connections.json`:

```json
{
  "gyamaguti": "mongodb+srv://user:password@cluster.mongodb.net/"
}
```

A new developer must visit `/setup`, enter their own URI, and save it before using the app.

> **Security note:** `user_connections.json` contains MongoDB URIs with credentials in plain text. This file should **never** be committed to version control or shared outside the team. It is listed in `.gitignore`.

---

## Configuration Files

### `config.json`

Stores the entity pairs being monitored and their ingestion metadata.

```json
[
  {
    "companyId":   "10000000000000",
    "entityId":    "64a1b2c3d4e5f6a7b8c9d0e1",
    "delay":       1,
    "method":      "API",
    "responsible": "Beehus"
  }
]
```

| Field | Type | Description |
|---|---|---|
| `companyId` | string | Company identifier (plain string in MongoDB) |
| `entityId` | string | MongoDB ObjectId as string |
| `delay` | int | Business days before data is expected |
| `method` | string | One of: `API`, `scraping`, `XML`, `SFTP`, `pdf`, `excel`, `open finance`, `other` |
| `responsible` | string | `Beehus` or `Parceiro` |

Edited via the **Config** page (`/config`). Never edit by hand while the app is running.

### `settings.json`

```json
{
  "only_daily_position":    true,
  "only_with_consumption":  false,
  "wizard_blacklist":       ["fundo", "fi", "da", ...],
  "company_filter":         ["10000000000000"]
}
```

`company_filter` is an array of `companyId` strings. When non-empty, only those companies are shown on the conciliação and other pages. An empty array means show all companies.

Edited via the **Settings** page (`/settings`).

---

## Backend Conventions

### One blueprint per feature

Every page lives in its own file under `pages/`. Register the blueprint in `app.py`:

```python
# pages/my_feature.py
from flask import Blueprint, render_template, jsonify

bp = Blueprint("my_feature", __name__)

@bp.route("/my-feature")
def index():
    return render_template("my_feature.html")

@bp.route("/api/my-feature/data")
def get_data():
    return jsonify({"items": []})
```

```python
# app.py
from pages.my_feature import bp as my_feature_bp
app.register_blueprint(my_feature_bp)
```

### Route naming conventions

| Pattern | Usage |
|---|---|
| `GET  /page-name` | Render the HTML page |
| `GET  /api/page-name/noun` | Fetch data for that page |
| `POST /api/page-name/verb` | Mutate/action (save, generate, send) |
| `DELETE /api/page-name/noun/<id>` | Delete a specific resource |

### Using the DB

Always import `db` from `db.py`. Never create your own MongoClient.

```python
from db import db

results = db.wallets.find({"companyId": cid}, {"name": 1})
```

### Shared config loaders

`db.py` provides ready-made loader functions. Use them instead of reading JSON files directly:

```python
from db import load_config_full, load_settings

# Preferred: read config.json once (hot-path routes)
selected, delays, methods, responsible = load_config_full()

settings = load_settings()   # dict with UI flags
```

Individual loaders are also available for cases that need only one field:

```python
from db import load_config, load_config_delays, load_config_methods, load_config_responsible

pairs  = load_config()              # set of (companyId, entityId)
delays = load_config_delays()       # {(companyId, entityId): int}
```

### Shared display helpers

`db.py` also exports helpers used by nav and other grids:

```python
from db import biz_days_elapsed, cell_cls, wallet_cls, build_wallet_map

elapsed = biz_days_elapsed("2024-01-15")          # int — biz days since that date
cls     = cell_cls(count, total, expected=True)   # Tailwind class string
cls     = wallet_cls(has_value)                   # Tailwind class string
wallet_to_pair, pair_total = build_wallet_map(load_settings())
```

### Date and company filter helpers

```python
from db import get_biz_dates, get_company_filter

dates = get_biz_dates(10)               # list of last N business date strings (YYYY-MM-DD), most recent last
dates = get_biz_dates(10, "2024-06-30") # same, ending at a specific date

cf = get_company_filter()               # list of companyId strings from settings.json["company_filter"]
                                        # empty list means no filter (show all)
if cf:
    items = [i for i in items if i["companyId"] in cf]
```

### Wallet filter helper

Respect the global settings when querying wallets:

```python
from db import wallet_filter_query, load_settings

query = {"companyId": cid, **wallet_filter_query(load_settings())}
wallets = db.wallets.find(query, {"name": 1})
```

### `trashed` convention

Several collections support soft-deletion via a `trashed` boolean field. Always exclude trashed documents in queries:

```python
db.navPackages.find({"walletId": wid, "trashed": {"$ne": True}})
```

Collections that use `trashed`: `navPackages`. Others do not — do not add the filter unless you know the collection supports it.

### Error responses

Return a JSON object with an `"error"` key and the appropriate HTTP status code:

```python
return jsonify({"error": "navPackage não encontrado"}), 404
return jsonify({"error": "campo obrigatório ausente"}), 400
```

Never raise unhandled exceptions to the browser in production routes.

---

## Frontend Conventions

### Layout

Every page extends `base.html`:

```html
{% extends "base.html" %}
{% set active = "my_feature" %}   {# must match the key in the sidebar nav #}
{% block title %}My Feature{% endblock %}

{% block head %}
{# extra <script> or <link> tags here #}
{% endblock %}

{% block content %}
<header class="bg-white shadow px-6 py-3 flex items-center sticky top-0 z-10">
  <h1 class="font-semibold text-gray-700">Page Title</h1>
</header>

<div class="px-6 py-6 max-w-2xl">
  ...
</div>
{% endblock %}
```

### Sidebar registration

Add a link in `templates/base.html` inside the `<nav>` section:

```html
<a href="/my-feature"
   class="nav-link {% if active == 'my_feature' %}active{% endif %}">
  ...icon svg...
  My Feature
</a>
```

### Tailwind CSS

Using the **CDN** version — all utility classes are available without a build step. No custom CSS files exist. Use utility classes only.

### JavaScript conventions

- No JS framework (no React/Vue). Vanilla JS only.
- API calls use `fetch()`.
- HTML escaping for user content: use the `escHtml(s)` helper already defined in templates that need it.
- JSON data from Flask is embedded via `{{ variable | tojson }}` for initial page state.

```javascript
// Fetching data
const res  = await fetch('/api/my-feature/data');
const data = await res.json();

// Posting JSON
const res = await fetch('/api/my-feature/save', {
  method:  'POST',
  headers: {'Content-Type': 'application/json'},
  body:    JSON.stringify(payload),
});
```

### Colour coding conventions

| Meaning | Tailwind classes |
|---|---|
| Success / complete | `bg-green-100 text-green-700` |
| Partial / warning | `bg-yellow-100 text-yellow-700` |
| Missing / error | `bg-red-100 text-red-600` |
| Not expected / neutral | `bg-gray-50 text-gray-300` |
| Beehus (responsible) | `bg-yellow-100 text-yellow-700` |
| Parceiro (responsible) | `bg-sky-100 text-sky-700` |
| Securities sub-section | `bg-amber-50` borders + `text-amber-600` labels |

---

## Adding a New Page

1. **Create** `pages/my_feature.py` with a Flask Blueprint named `bp`.
2. **Create** `templates/my_feature.html` extending `base.html`.
3. **Register** the blueprint in `app.py`.
4. **Add** a sidebar link in `templates/base.html`.

For a future/placeholder page, just add a route to `pages/stubs.py` and a sidebar link — it will show the generic "em construção" page automatically.

```python
# pages/stubs.py — add to _PAGES list
("/my-route", "my_active_key", "Page Title"),
```

---

## Adding a New API Endpoint

1. Add the route to the relevant blueprint file in `pages/`.
2. Follow the naming pattern: `GET /api/<page>/<noun>` or `POST /api/<page>/<verb>`.
3. Return `jsonify(...)`. Never return raw dicts.
4. For mutations, read the body with `request.get_json(force=True)`.
5. Do not read config/settings JSON files directly — use the loader functions from `db.py`.

---

## MongoDB Collections Reference

All IDs stored as **plain strings** (not ObjectId) in the application-level fields (companyId, entityId, walletId, securityId). The `_id` field is a proper ObjectId in MongoDB.

| Collection | Key fields |
|---|---|
| `companies` | `_id`, `name` |
| `entities` | `_id`, `name` |
| `wallets` | `_id`, `name`, `companyId`, `entityId`, `accountCode`, `hasDailyPosition`, `consumptionIdentifiers` |
| `securities` | `_id`, `beehusName`, `mainId`, `maturityDate`, `yield`, `indexer`, `indexerPercentual`, `securityType`, `type`, `klass`, `redemptionNavDays`, `redemptionSettlementDays`, `subscriptionNavDays`, `subscriptionSettlementDays` |
| `unprocessedSecurityPositions` | `_id`, `walletId`, `companyId`, `entityId`, `positionDate`, `securities[]` → `{unprocessedId, securityId, quantity, pu, amount}` |
| `processedPosition` | `_id`, `positionDate`, `companyId`, `entityId`, `walletId`, `securities[]` |
| `processedPosition.securities[]` | `securityId`, `beehusName`, `quantity`, `pu`, `amount`, `pricingType`, `executionPrice`, `dailyContribution`, `intradayContribution`, `eventContribution`, `totalContribution`, `hierarchicalVariable.variable1`, `hierarchicalVariable.variable2` |
| `publishedPositionSecurities` | `_id`, `positionDate`, `companyId`, `entityId`, `walletId`, `securityId`, `quantity`, `pu`, `amount`, `pricingType`, `variable1`, `variable2`, `dailyContribution`, `totalContribution` |
| `transactions` | `_id`, `operationDate`, `liquidationDate`, `companyId`, `walletId`, `securityId`, `beehusTransactionType`, `quantity`, `price`, `balance`, `description` |
| `provisions` | `_id`, `initialDate`, `liquidationDate`, `companyId`, `walletId`, `securityId`, `provisionType`, `amount`, `description` |
| `cashAccounts` | `_id`, `companyId`, `walletId`, `values[]` → `{date, value}` |
| `navPackages` | `_id`, `walletId`, `companyId`, `entityId`, `positionDate`, `trashed`, `nav`, `navPerShare`, `formerNavPerShare`, `formerNav`, `returnNavPerShare`, `returnContribution`, `inAndOutFlows` |
| `securityMappings` | `_id`, `companyId`, `mappings[]` → `{from: unprocessedId, securityId}` |
| `diagnosticFeedback` | `_id`, `walletId`, `date`, `gapCash`, `scenarioIndex`, `confirmed`, `userNote`, `flagsInScenario[]`, `resolvedAt` |

### Notable schema quirks

**`cashAccounts`** does not have one document per date. It has one document per wallet, with an embedded array of `{date, value}` entries. To get the balance for a specific date you must iterate `values[]`:

```python
for doc in db.cashAccounts.find({"walletId": wid}, {"values": 1}):
    for entry in doc.get("values", []):
        if str(entry.get("date", ""))[:10] == target_date:
            total += float(entry.get("value") or 0)
```

**`provisions`** uses `amount` (not `balance`) for the monetary value. Multiple active provisions can exist for the same `securityId` on the same wallet — always sum them, never assume one per security.

**`transactions.beehusTransactionType`** can be `null` for unclassified transactions. Always handle the null case. The full set of known types (verified against `db.transactions.distinct("beehusTransactionType")` in production):

`amortization`, `brokerageFee`, `buySell`, `bzFundTaxes`, `contributionAdjustment`, `coupon`, `dividend`, `dividendOnboarding`, `gainsExpenses`, `interestOnEquity`, `managementFee`, `maturity`, `other`, `otherFee`, `performanceFee`, `rebate`, `securityContributionAdjustment`, `securityTransfer`, `taxes`, `withdrawalDeposit`, `withdrawalDepositAdjustment`.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `EMAIL_FROM` | No | Sender address for report emails (default: `tecnologia@beehus.com.br`) |
| `EMAIL_PASSWORD` | Yes (for email) | SMTP password for Office 365 account |

Set these in a `.env` file or in your OS environment before starting the app. The email feature silently fails if `EMAIL_PASSWORD` is not set.

---

## Further Reading

| Document | What it covers |
|---|---|
| `CONCILIACAO_DIAGNOSTICO.md` | Full specification of the NAV conciliation diagnostic engine: all flags, their formulas, `signedImpact` semantics, and the scenario scoring algorithm. Read this before touching `pages/conciliacao.py`. |
| `CONCILIACAO_SIMULATION.md` | Synthetic portfolio simulation scenarios for testing and validating the diagnostic engine. |
| `FILE_GENERATION.md` | JSON templates for all upload files: transactions, wallets, positions, provisions, and security mappings. |
| `BEEHUS_CONSOLE.md` | The Funções (`/beehus`) page: bearer-token handling, the catalog of filter and action routes, the per-functionality view list, the daily-routine sequential pipelines, and the session-state preservation pattern between `shell.html` and the iframe. Read this before touching `pages/beehus_console.py` or `templates/beehus_console.html`. |
