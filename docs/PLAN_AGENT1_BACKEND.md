# AGENT 1 — Backend Implementation Plan

> Formalize the existing Flask backend into a tested, contract-validated structure.
> This agent works on `pages/` Python files and `tests/`.
> Reference: `api_contract.json` for all endpoint schemas.

---

## Prerequisites

- Python 3.10+, Flask, pymongo, certifi, pytest
- MongoDB Atlas connection configured via `/setup`
- Working directory: project root

---

## Phase 1: Test Infrastructure

1. Create `tests/` directory
2. Create `tests/conftest.py` with Flask test client fixture:
   - Import `app` from `app.py`
   - Create `client` fixture with `app.test_client()`
   - Mock MongoDB connection for unit tests
3. Create `tests/test_smoke.py` — verify app starts and all page routes return 200

## Phase 2: Contract Validation Tests

For each module in `api_contract.json`, create a test file:

| Test File | Module | Key Validations |
|-----------|--------|-----------------|
| `tests/test_setup.py` | setup | POST test-connection returns `{ok}` |
| `tests/test_config.py` | config | GET entities returns company+entities shape |
| `tests/test_conciliacao.py` | conciliacao | GET dates, rows, wallet-detail, diagnose (6 steps) |
| `tests/test_validacao.py` | validacao_rentabilidades | GET dates, securities, detail |
| `tests/test_nav.py` | nav | GET rows, detail, settings |
| `tests/test_precos.py` | precos | GET search, filters, history |
| `tests/test_impostos.py` | impostos | GET companies, wallets, securities |

Each test validates:
- HTTP status code (200 for success, 404 for not found)
- Response JSON keys match contract
- Response types match contract (arrays are arrays, numbers are numbers)

## Phase 3: Endpoint Hardening

For each module, review and fix:

1. **Missing error handling** — all endpoints should return proper 400/404/500
2. **Input validation** — required params checked, types validated
3. **Response consistency** — ensure all responses match `api_contract.json` exactly
4. **Edge cases** — empty results, null values, missing MongoDB documents

## Phase 4: Shared Utilities

1. Extract common patterns into `utils.py`:
   - `_valid_wallet_ids()` — duplicated across 3+ files
   - `_sum_cash()` — shared between conciliacao routes
   - Company/wallet fetching boilerplate
2. Create `api_helpers.py`:
   - `validate_required_params(request, ["param1", "param2"])` → returns 400 if missing
   - `json_response(data, status=200)` → consistent response wrapper

## Phase 5: Run & Validate

```bash
pytest tests/ -v --tb=short
```

All tests must pass. Any endpoint that doesn't match the contract gets fixed.

---

## Files Created/Modified

| Action | File |
|--------|------|
| Create | `tests/conftest.py` |
| Create | `tests/test_smoke.py` |
| Create | `tests/test_*.py` (12 files) |
| Create | `utils.py` (optional extraction) |
| Modify | `pages/*.py` (error handling, validation) |

## Integration Point

Frontend (Agent 2) calls all endpoints listed in `api_contract.json`. As long as response shapes match the contract, integration is seamless. No coordination needed during development.
