from flask import Blueprint, jsonify, request
from db import (atomic_write_json, company_visible, resolve_wallet)
from datetime import date as _date, timedelta
import json
import math
import os
import re

import beehus_catalog

# Pending corrections stored by the Correções page are injected into the
# diagnostic pipeline so gaps, flags and listings reflect the "post-correction"
# view without touching the database.
from pages.correcoes import (load_corrections_for_wallet, load_all_pending_provisions,
                             load_pending_execution_prices)
from beehus_api import (delete_transaction as _api_delete_transaction,
                        update_transaction as _api_update_transaction,
                        delete_provision as _api_delete_provision,
                        update_provision as _api_update_provision,
                        list_transactions as _api_list_transactions,
                        list_provisions as _api_list_provisions,
                        BeehusAPIError,
                        BeehusAuthError)


def _wallet_company(wallet_id):
    """Resolve the companyId for a given walletId. Returns '' if unknown.
    Used to scope pending-correction lookups from wallet-only endpoints."""
    w = beehus_catalog.wallet_doc(wallet_id)
    return str(w.get("companyId", "")) if w else ""


def _require_visible_wallet(wallet_id):
    """Resolve `wallet_id`'s company and check it's within the user's company filter.

    Returns (company_id, None) on success — caller can use company_id directly.
    Returns ("", error_response_tuple) on failure — caller should return it as-is.

    Closes the cross-company info-leak path on every endpoint that accepts a
    walletId from the request: a user whose company_filter excludes company X
    cannot read X's wallets just by guessing a walletId.
    """
    if not wallet_id:
        return "", (jsonify({"error": "walletId obrigatório"}), 400)
    company_id = _wallet_company(wallet_id)
    if not company_id:
        return "", (jsonify({"error": "wallet não encontrada"}), 404)
    if not company_visible(company_id):
        return "", (jsonify({"error": "acesso negado"}), 403)
    return company_id, None


def _next_biz_day(d):
    """Next business day (Mon–Fri) strictly after `d` (a datetime.date).

    Weekday-only — no holiday calendar, matching the convention used elsewhere
    in this codebase (`get_biz_dates`, `biz_days_between`)."""
    d = d + timedelta(days=1)
    while d.weekday() >= 5:   # 5=Sat, 6=Sun
        d += timedelta(days=1)
    return d


def _prov_dates(date_str, offset):
    """Provision window anchored on the analyzed navPackage date `date_str`,
    spanning |offset| calendar days. The liquidation date is ALWAYS
    `navPackage date + offset` (per spec) — never the wall-clock "today".

      offset > 0 (liquidação futura): initial=date,         liquidation=date+offset
      offset < 0 (nav futuro):        initial=date+offset,   liquidation=date
      offset == 0:                    initial=date,          liquidation=date

    **Minimum liquidation:** a provision must always settle at least 1 business
    day after the navPackage date — `liquidation = max(computed, navDate + 1
    dia útil)`. This guards the offset ≤ 0 paths (offset 0, WRONG_PROVISION_AMOUNT,
    "+ Provisão" manual) that would otherwise produce a same-day or past
    liquidation, which is invalid for a forward-looking provision.

    Returns (initialDate, liquidationDate) as ISO strings. Falls back to
    (date_str, date_str) when `date_str` is not a parseable ISO date.
    """
    try:
        offset = int(offset or 0)
    except (TypeError, ValueError):
        offset = 0
    try:
        base = _date.fromisoformat(date_str)
    except (TypeError, ValueError):
        return date_str, date_str

    if offset > 0:
        init_d, liq_d = base, base + timedelta(days=offset)
    elif offset < 0:
        init_d, liq_d = base + timedelta(days=offset), base
    else:
        init_d, liq_d = base, base

    # Enforce the minimum: liquidation ≥ navDate + 1 business day.
    floor = _next_biz_day(base)
    if liq_d < floor:
        liq_d = floor
    return init_d.isoformat(), liq_d.isoformat()


def _safe_num(v):
    """Replace Infinity / NaN with None so jsonify produces valid JSON."""
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    return v


def _sanitize(obj):
    """Recursively replace Infinity / NaN with None in dicts / lists."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return _safe_num(obj)

# ── Conciliação config (editable diff threshold) ──────────────────────────────
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "conciliacao_config.json")
_DEFAULT_DIFF_THRESHOLD_PCT = 0.01  # 0.01% = 1 basis point (matches legacy visual cue)


def _load_conciliacao_config():
    """Return dict with user-editable settings. Falls back to defaults when missing/corrupt."""
    defaults = {"diffThresholdPct": _DEFAULT_DIFF_THRESHOLD_PCT}
    if not os.path.exists(_CONFIG_FILE):
        return defaults
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return {**defaults, **data}
    except Exception:
        return defaults


def _save_conciliacao_config(cfg):
    """Persist conciliação config. Returns (ok, error_message)."""
    try:
        atomic_write_json(_CONFIG_FILE, cfg)
        return True, ""
    except Exception as exc:
        # OneDrive-synced paths occasionally throw PermissionError during
        # background sync — surface a friendly message instead of a 500.
        # Never echo `str(exc)` to callers: PermissionError on Windows
        # contains the absolute filesystem path (home dir, OneDrive tenant,
        # username) which would then leak into a JSON response in the browser.
        import logging
        logging.getLogger(__name__).error("failed to save conciliacao config: %s", exc)
        return False, "verifique sincronização do OneDrive e tente novamente"


def _diff_threshold_decimal(request_obj=None):
    """Resolve the |returnNavPerShare - returnContribution| threshold in decimal form.

    Priority: explicit ?threshold=<pct> query param → config file → default.
    """
    pct = None
    if request_obj is not None:
        raw = (request_obj.args.get("threshold") or "").strip()
        if raw:
            try:
                pct = float(raw)
            except ValueError:
                pct = None
    if pct is None:
        pct = float(_load_conciliacao_config().get("diffThresholdPct", _DEFAULT_DIFF_THRESHOLD_PCT))
    # stored/UI unit is percent (e.g. 0.01 = 0.01%). Mongo compares decimals.
    return max(0.0, pct / 100.0)


bp = Blueprint("conciliacao", __name__)


# ── Routes ─────────────────────────────────────────────────────────────────────


@bp.route("/api/conciliacao/config", methods=["GET"])
def get_config():
    """Return current conciliação settings (diffThresholdPct in percent units)."""
    return jsonify(_load_conciliacao_config())


@bp.route("/api/conciliacao/config", methods=["PUT", "POST"])
def update_config():
    """Update conciliação settings. Body: { diffThresholdPct: number }."""
    body = request.get_json(force=True, silent=True) or {}
    cfg  = _load_conciliacao_config()
    if "diffThresholdPct" in body:
        try:
            pct = float(body["diffThresholdPct"])
            # Unit is percent points on the return diff. Values above ~10% are
            # meaningless (returns are typically in -1..+1). Accept 0 (which
            # reverts to strict $ne behavior — documented).
            if pct < 0 or pct > 10:
                return jsonify({"error": "diffThresholdPct fora do intervalo [0, 10]"}), 400
            cfg["diffThresholdPct"] = pct
        except (TypeError, ValueError):
            return jsonify({"error": "diffThresholdPct inválido"}), 400
    ok, err = _save_conciliacao_config(cfg)
    if not ok:
        return jsonify({"error": f"falha ao salvar config: {err}"}), 500
    return jsonify(cfg)


# ── Edit / delete a real DB transaction via the upstream Beehus API ───────────
# Consumed by the wallet-detail screen (inline ✎/🗑 on each linked transaction).
# Pending /correcoes rows are NOT touched here — those are edited via the
# /correcoes endpoints. Only transactions that already exist in db.transactions
# (and belong to the requested wallet) can be patched/deleted.

# Fields the wallet-detail edit form is allowed to change. Subset of
# beehus_api.transactions._PATCHABLE_FIELDS — deliberately excludes
# walletId/entityId/currencyId (wallet migration is the Exceções routine's job,
# not an inline edit) and quantity (not patchable upstream).
_TXN_EDIT_FIELDS = {
    "balance", "beehusTransactionType", "operationDate",
    "liquidationDate", "price", "securityId", "description",
}


def _beehus_error_response(e, auth_status=401, api_status=502):
    """Map a Beehus exception to a JSON error response tuple."""
    status = auth_status if isinstance(e, BeehusAuthError) else api_status
    return jsonify({"error": str(e),
                    "upstream_status": getattr(e, "status", None),
                    "upstream_body": getattr(e, "body", None)}), status


def _txn_belongs_to_wallet(company_id, wallet_id, txn_id, op_date, liq_date):
    """Ownership guard: confirma que `txn_id` é uma transação de `wallet_id`.

    Fecha o IDOR cross-tenant — a visibilidade da CARTEIRA, sozinha, não impede
    apagar/editar a transação de OUTRA empresa por id. Consulta a listagem da
    carteira escopada por (empresa, carteira, data da própria transação) via
    `list_transactions`, que LEVANTA em erro de API → a verificação é FAIL-OPEN:
    qualquer falha de lookup (ou ausência de data) retorna True e deixa a operação
    seguir; só retorna False quando o lookup SUCEDE e o id não está na carteira."""
    checks = []
    if liq_date:
        checks.append(("liquidation", str(liq_date)[:10]))
    if op_date and op_date != liq_date:
        checks.append(("operation", str(op_date)[:10]))
    if not checks:
        return True   # sem data p/ escopar → fail-open
    try:
        for date_type, d in checks:
            txns = _api_list_transactions(
                company_id=company_id, wallet_ids=[wallet_id],
                initial_date=d, final_date=d, date_type=date_type)
            if any(beehus_catalog.id_str(t.get("_id")) == txn_id for t in (txns or [])):
                return True
        return False
    except Exception:
        return True   # falha de lookup nunca bloqueia uma operação legítima


def _prov_belongs_to_wallet(company_id, wallet_id, prov_id):
    """Ownership guard p/ provisões (espelha `_txn_belongs_to_wallet`). Provisões
    por carteira são poucas → uma listagem `walletId`-escopada de janela ampla é
    barata e dispensa receber a data do front. FAIL-OPEN em erro de lookup."""
    try:
        provs = _api_list_provisions(
            company_id=company_id, initial_date="2000-01-01",
            final_date="2999-12-31", wallet_id=wallet_id)
        return any(beehus_catalog.id_str(p.get("_id")) == prov_id for p in (provs or []))
    except Exception:
        return True


@bp.route("/api/conciliacao/transaction/delete", methods=["POST"])
def delete_wallet_transaction():
    """Delete a real DB transaction via `DELETE /beehus/financial/transactions/{id}`.

    Body: `{walletId, txnId, operationDate?, liquidationDate?}`. The wallet must be
    visible AND `txnId` must belong to it (`_txn_belongs_to_wallet`, scoped by the
    transaction's own dates) — closes the cross-tenant delete-by-id path."""
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    txn_id    = str(data.get("txnId", "") or "")

    company_id, err = _require_visible_wallet(wallet_id)
    if err:
        return err
    if not txn_id:
        return jsonify({"error": "txnId obrigatório"}), 400
    if not _txn_belongs_to_wallet(company_id, wallet_id, txn_id,
                                  str(data.get("operationDate") or "")[:10],
                                  str(data.get("liquidationDate") or "")[:10]):
        return jsonify({"error": "transação não pertence à carteira informada"}), 403

    try:
        result = _api_delete_transaction(txn_id)
    except (BeehusAuthError, BeehusAPIError) as e:
        return _beehus_error_response(e)
    return jsonify(_sanitize({"ok": True, "txnId": txn_id, "upstream": result}))


@bp.route("/api/conciliacao/transaction/update", methods=["POST"])
def update_wallet_transaction():
    """Patch a real DB transaction via `PATCH /beehus/financial/transactions/{id}`.

    Body: `{walletId, txnId, patch, operationDate?, liquidationDate?}` where
    `patch` is a partial dict. Only the fields in `_TXN_EDIT_FIELDS` are
    forwarded; `balance`/`price` are coerced to float. The wallet must be visible
    AND `txnId` must belong to it (`_txn_belongs_to_wallet`, scoped by the
    transaction's PRE-edit dates) — closes the cross-tenant patch-by-id path."""
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    txn_id    = str(data.get("txnId", "") or "")
    patch_in  = data.get("patch") or {}

    company_id, err = _require_visible_wallet(wallet_id)
    if err:
        return err
    if not txn_id:
        return jsonify({"error": "txnId obrigatório"}), 400
    if not isinstance(patch_in, dict) or not patch_in:
        return jsonify({"error": "patch (objeto não-vazio) obrigatório"}), 400
    if not _txn_belongs_to_wallet(company_id, wallet_id, txn_id,
                                  str(data.get("operationDate") or "")[:10],
                                  str(data.get("liquidationDate") or "")[:10]):
        return jsonify({"error": "transação não pertence à carteira informada"}), 403

    # Whitelist + coerce. Empty strings on a field mean "leave as-is" → dropped.
    patch = {}
    for k, v in patch_in.items():
        if k not in _TXN_EDIT_FIELDS:
            continue
        if v is None or v == "":
            continue
        if k in ("balance", "price"):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return jsonify({"error": f"{k} inválido (não numérico)"}), 400
        patch[k] = v
    if not patch:
        return jsonify({"error": "nenhum campo editável no patch"}), 400

    try:
        result = _api_update_transaction(txn_id, patch)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except (BeehusAuthError, BeehusAPIError) as e:
        return _beehus_error_response(e)
    return jsonify(_sanitize({"ok": True, "txnId": txn_id, "patch": patch, "upstream": result}))


# Fields the wallet-detail provision edit form is allowed to change. Subset of
# beehus_api.provisions._PATCHABLE_FIELDS — excludes walletId/currencyId/
# provisionSource (not inline-editable here).
_PROV_EDIT_FIELDS = {
    "balance", "provisionType", "initialDate",
    "liquidationDate", "securityId", "description",
}


@bp.route("/api/conciliacao/provision/delete", methods=["POST"])
def delete_wallet_provision():
    """Delete a real DB provision via `DELETE /beehus/provisions/{id}`.

    Body: `{walletId, provisionId}`. The wallet must be visible AND `provisionId`
    must belong to it (`_prov_belongs_to_wallet`) — closes the cross-tenant
    delete-by-id path."""
    data       = request.get_json() or {}
    wallet_id  = data.get("walletId", "")
    prov_id    = str(data.get("provisionId", "") or "")

    company_id, err = _require_visible_wallet(wallet_id)
    if err:
        return err
    if not prov_id:
        return jsonify({"error": "provisionId obrigatório"}), 400
    if not _prov_belongs_to_wallet(company_id, wallet_id, prov_id):
        return jsonify({"error": "provisão não pertence à carteira informada"}), 403

    try:
        result = _api_delete_provision(prov_id)
    except (BeehusAuthError, BeehusAPIError) as e:
        return _beehus_error_response(e)
    return jsonify(_sanitize({"ok": True, "provisionId": prov_id, "upstream": result}))


@bp.route("/api/conciliacao/provision/update", methods=["POST"])
def update_wallet_provision():
    """Patch a real DB provision via `PATCH /beehus/provisions/{id}`.

    Body: `{walletId, provisionId, patch}`. Only `_PROV_EDIT_FIELDS` are
    forwarded; `balance` is coerced to float. The wallet must be visible AND
    `provisionId` must belong to it (`_prov_belongs_to_wallet`) — closes the
    cross-tenant patch-by-id path."""
    data       = request.get_json() or {}
    wallet_id  = data.get("walletId", "")
    prov_id    = str(data.get("provisionId", "") or "")
    patch_in   = data.get("patch") or {}

    company_id, err = _require_visible_wallet(wallet_id)
    if err:
        return err
    if not prov_id:
        return jsonify({"error": "provisionId obrigatório"}), 400
    if not isinstance(patch_in, dict) or not patch_in:
        return jsonify({"error": "patch (objeto não-vazio) obrigatório"}), 400
    if not _prov_belongs_to_wallet(company_id, wallet_id, prov_id):
        return jsonify({"error": "provisão não pertence à carteira informada"}), 403

    patch = {}
    for k, v in patch_in.items():
        if k not in _PROV_EDIT_FIELDS:
            continue
        if v is None or v == "":
            continue
        if k == "balance":
            try:
                v = float(v)
            except (TypeError, ValueError):
                return jsonify({"error": "balance inválido (não numérico)"}), 400
        patch[k] = v
    if not patch:
        return jsonify({"error": "nenhum campo editável no patch"}), 400

    try:
        result = _api_update_provision(prov_id, patch)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except (BeehusAuthError, BeehusAPIError) as e:
        return _beehus_error_response(e)
    return jsonify(_sanitize({"ok": True, "provisionId": prov_id, "patch": patch, "upstream": result}))


# ── Diagnostic engine (V2 — 6-step sequential funnel) ──────────────────────────

_EVENT_TYPES    = {"amortization", "coupon"}
_TOLERANCE_ABS  = 0.01
_TOLERANCE_REL  = 0.05   # 5%
_TOLERANCE_REL_TXN = 0.10  # 10% — Step 3.3 (transaction value vs expected)


def _approx(a, b):
    """Return True if a ≈ b within absolute or relative tolerance."""
    if a is None or b is None:
        return False
    diff = abs(a - b)
    return diff <= _TOLERANCE_ABS or diff <= abs(b) * _TOLERANCE_REL


def _approx_txn(a, b):
    """Return True if a ≈ b within the wider Step 3.3 tolerance (10%)."""
    if a is None or b is None:
        return False
    diff = abs(a - b)
    return diff <= _TOLERANCE_ABS or diff <= abs(b) * _TOLERANCE_REL_TXN


def _find_former_nav(wallet_id, date, company_id=None):
    """Resolve a wallet's immediately-prior untrashed navPackage via API.

    Returns (former_date_str|None, former_nav_float|None).
    """
    return beehus_catalog.nav_former_for_entity(wallet_id, date, company_id)


def _exec_price_impact(row):
    """Absolute cash impact of an accepted executionPrice correction.

    Derivation: replacing the price the system used (`priorExecutionPrice` or
    `pu` fallback) with the user-supplied `executionPrice` shifts the intraday
    contribution by `amountDiff × (priorPrice − newPrice)`. The gap closes by
    that same magnitude with the appropriate sign — same close-the-gap rule
    already used for pending transactions, so callers add `|impact|` to the
    overall `corrections_impact_abs` and the existing `recalc ± impact_abs`
    heuristic does the rest.

    Returns 0.0 if any field is missing or non-numeric so the recalc never
    crashes on malformed rows."""
    try:
        amt   = float(row.get("amountDiff") or 0)
        prior = float(row.get("priorExecutionPrice") or 0) or float(row.get("pu") or 0)
        new   = float(row.get("executionPrice") or 0)
    except (TypeError, ValueError):
        return 0.0
    return abs(amt * (prior - new))


def _recalc_gap_with_corrections(company_id, wallet_id, date, nav_pkg, former_date,
                                 former_nav, return_contrib, gap_cash,
                                 *, pending_provs=None):
    """Full NAV recalc of today's gap considering pending correction provisions
    that affect either today's NAV or former_date's NAV (Option 1 from
    docs/CONCILIACAO_BAYESIAN.md). Transactions/deletions keep the legacy
    |balance| heuristic on top until they get an equally rigorous treatment.

    `pending_provs` is an optional pre-loaded list of provisions for this
    wallet (output of `load_all_pending_provisions(...)` or of a batched
    `load_all_pending_provisions_by_wallet(...)[wallet_id]`). Callers that
    iterate many wallets in one request should batch-load once and pass
    the per-wallet slice to avoid re-scanning the correcoes tree per call.
    When `None`, this helper loads its own.

    Formula chain applied verbatim from docs/CONCILIACAO_RECALCULO.md:
        nav_D             = Σ securities.balance + Σ provisions.amount + Σ cash
        navPerShare_D     = (nav_D − inAndOutFlows_D) / formerAmount_D
        returnNavPS_today = navPerShare_today / navPerShare_former − 1
        gapPct_today      = returnNavPS_today − returnContribution_today
        gapCash_today     = gapPct_today × former_nav

    Pending provisions change nav_D on the dates where they are *active*
    (`initialDate ≤ D < liquidationDate`). Pending txns/deletions adjust
    gap magnitude via the legacy |balance| close rule.

    Returns (recalc_gap_cash, recalc_gap_pct, impact_abs, corrections_count).
    """
    if not company_id or gap_cash is None:
        recalc_gap_pct = (gap_cash / former_nav) if (gap_cash is not None and former_nav) else None
        return gap_cash, recalc_gap_pct, 0, 0

    txns, _, dels = load_corrections_for_wallet(company_id, date, wallet_id)

    # Partition pending provisions into today-active / former-active without
    # re-scanning the tree. A provision can be active on both dates if its
    # window spans them.
    if pending_provs is None:
        pending_provs = load_all_pending_provisions(company_id, wallet_id)

    def _active_on(p, d):
        if not d:
            return False
        init = str(p.get("initialDate") or "")[:10]
        liq  = str(p.get("liquidationDate") or "")[:10]
        dstr = str(d)[:10]
        return bool(init) and bool(liq) and init <= dstr < liq

    today_provs  = [p for p in pending_provs if _active_on(p, date)]
    former_provs = [p for p in pending_provs if _active_on(p, former_date)]

    # Dedupe provisions by id — a provision whose window spans BOTH today and
    # former_date (uncommon but possible) appears in both lists.
    uniq_provs = {}
    for p in list(today_provs) + list(former_provs):
        pid = p.get("id") or id(p)
        uniq_provs[pid] = p

    # Pending executionPrice corrections that target this date and have NOT
    # yet been pushed upstream (`inputed=False`). Inputed rows are baked into
    # the source data already and applying them locally would double-count.
    exec_rows = load_pending_execution_prices(company_id, wallet_id)
    exec_active = [
        r for r in exec_rows
        if (r.get("positionDate") == date) and not r.get("inputed")
    ]
    exec_impact_abs = sum(_exec_price_impact(r) for r in exec_active)

    txn_impact_abs  = sum(abs(float(r.get("balance") or 0)) for r in txns)
    prov_impact_abs = sum(abs(float(p.get("balance") or 0)) for p in uniq_provs.values())
    impact_abs      = txn_impact_abs + prov_impact_abs + exec_impact_abs
    corrections_count = len(txns) + len(uniq_provs) + len(dels) + len(exec_active)

    # Delta NAV per date from active provisions
    delta_nav_today  = sum(float(p.get("balance") or 0) for p in today_provs)
    delta_nav_former = sum(float(p.get("balance") or 0) for p in former_provs)

    # Full NAV recalc when any provision affects either nav
    recalc_gap_cash = gap_cash
    if delta_nav_today != 0 or delta_nav_former != 0:
        former_pkg = (
            beehus_catalog.nav_doc_for_entity_date(wallet_id, former_date, company_id)
            if former_date else None
        )

        former_shares = (former_pkg or {}).get("formerAmount") or 0
        today_shares  = nav_pkg.get("formerAmount") or 0

        if former_pkg and former_shares and today_shares:
            inflow_F = (former_pkg or {}).get("inAndOutFlows") or 0
            inflow_T = nav_pkg.get("inAndOutFlows") or 0
            nav_T    = nav_pkg.get("nav") or 0

            new_nav_former = former_nav + delta_nav_former
            new_nps_former = (new_nav_former - inflow_F) / former_shares

            new_nav_today  = nav_T + delta_nav_today
            new_nps_today  = (new_nav_today - inflow_T) / today_shares

            if new_nps_former:
                new_return_nav_ps = (new_nps_today / new_nps_former) - 1
                new_gap_pct       = new_return_nav_ps - return_contrib
                recalc_gap_cash   = new_gap_pct * new_nav_former

    # Legacy close-the-gap heuristic applied on top of the provision recalc.
    # Both pending transactions AND non-inputed executionPrice corrections
    # follow the same rule: each |impact| pushes `recalc_gap_cash` toward zero
    # by its magnitude. This matches the user's stated semantics ("include the
    # impact previously calculated in the recalculated GAP after Aceitar"),
    # and for the canonical MEP scenario where impact == |gapCash| produces
    # `recalc_gap_cash ≈ 0`.
    close_amount = txn_impact_abs + exec_impact_abs
    if recalc_gap_cash is not None:
        if recalc_gap_cash >= 0:
            recalc_gap_cash = recalc_gap_cash - close_amount
        else:
            recalc_gap_cash = recalc_gap_cash + close_amount

    recalc_gap_cash = round(recalc_gap_cash, 2) if recalc_gap_cash is not None else None
    recalc_gap_pct  = (round(recalc_gap_cash / former_nav, 10)
                       if (recalc_gap_cash is not None and former_nav) else None)
    return recalc_gap_cash, recalc_gap_pct, round(impact_abs, 2), corrections_count


# ── Refinement: offset / settlement-day drift ──────────────────────────────────
# Main diagnose assumes each security's subscriptionSettlementDays /
# subscriptionNavDays / redemptionSettlementDays / redemptionNavDays are
# correctly registered AND that the financial institution settled on the
# expected date. When that assumption is wrong, a legitimate transaction can
# land on a liquidationDate ≠ the reconciliation date, and a legitimate
# position change can appear a few days late/early. This endpoint refines
# MISSING_TRANSACTION / MISSING_PROVISION flags and WRONG_SECURITY /
# unclassified transactions by scanning a ±window-day neighborhood.
_REFINE_WINDOW_DEFAULT = 2
_REFINE_WINDOW_MAX     = 7


# ── Refinement feedback store (local JSON) ────────────────────────────────────
# Stored under data/refinement_feedback/<companyId>/<date>/<walletId>.json.
# Mirrors the correcoes store pattern (same reasons: Mongo user has no
# createCollection rights, and these are app-owned audit records).
_REFINE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "refinement_feedback"))
_REFINE_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_REFINE_SAFE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _refine_safe(s):
    s = str(s or "")
    return s if _REFINE_SAFE_ID_RE.match(s) else ""


def _refine_store_path(company_id, date, wallet_id):
    c = _refine_safe(company_id)
    w = _refine_safe(wallet_id)
    d = date if _REFINE_SAFE_DATE_RE.match(str(date or "")) else ""
    if not (c and w and d):
        return None
    return os.path.join(_REFINE_ROOT, c, d, f"{w}.json")


# ── Dismissed-flag store (local JSON) ─────────────────────────────────────────
# Stored under data/dismissed_flags/<companyId>/<date>/<walletId>.json.
# A "dismiss" is the user acknowledging a detected flag as non-actionable
# (noise / investigated / not fixable right now). UI hides dismissed flags
# from the active issue list but they're visible under "Descartados".
_DISMISS_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "dismissed_flags"))


def _dismiss_store_path(company_id, date, wallet_id):
    c = _refine_safe(company_id)
    w = _refine_safe(wallet_id)
    d = date if _REFINE_SAFE_DATE_RE.match(str(date or "")) else ""
    if not (c and w and d):
        return None
    return os.path.join(_DISMISS_ROOT, c, d, f"{w}.json")


# ── Transaction type mapping per flag ──────────────────────────────────────────
_FLAG_TXN_TYPE = {
    "MISSING_TRANSACTION":      "buySell",
    "MISSING_PROVISION":        None,           # provision, not transaction
    "WRONG_EVENT_BALANCE":      None,           # existing txn has wrong value
    "WRONG_PROVISION_AMOUNT":   None,           # provision issue
    "MISSING_EVENT":            "coupon",       # amortization/coupon event
    "WITHHOLDING_TAX":          "taxes",        # IR retido na fonte → saída de caixa (balance ×-1)
    "MISSING_EXECUTION_PRICE":  None,           # handled via executionPrices bucket
    "WRONG_TRANSACTION_VALUE":  "buySell",
    "WRONG_SECURITY":           None,
    "UNCLASSIFIED_TRANSACTION": None,           # existing txn needs reclassification
    "CASH_MISMATCH":            "gainsExpenses",
}

_FLAG_DESCRIPTIONS = {
    "MISSING_TRANSACTION":      "Correção: transação buySell faltante",
    "MISSING_EVENT":            "Correção: transação de evento faltante",
    "WITHHOLDING_TAX":          "Correção: ajuste IR retido na fonte",
    "MISSING_EXECUTION_PRICE":  "Correção: ajuste preço de execução",
    "WRONG_TRANSACTION_VALUE":  "Correção: valor de transação divergente",
    "CASH_MISMATCH":            "Correção: transação de caixa faltante",
}


@bp.route("/api/conciliacao/generate-transactions", methods=["POST"])
def generate_transactions():
    """Build a transaction file (and deletion markers) from accepted items.

    Output:
        {"companyId": str, "transactions": [...], "deletions": [...]}

    Deletions are emitted only for MISCLASSIFIED accepts: when the user
    confirms that a DB transaction is in the wrong security, we append a
    new transaction under the target security AND a deletion row that
    tells the reconciliation pipeline to disregard the original. See
    docs/CONCILIACAO_BAYESIAN.md for the full contract.
    """
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    date      = data.get("date", "")
    items     = data.get("items", [])

    if not wallet_id or not date or not items:
        return jsonify({"error": "walletId, date e items são obrigatórios"}), 400
    _, err = _require_visible_wallet(wallet_id)
    if err: return err

    # Fetch wallet info for entityId, currencyId, companyId
    wallet = resolve_wallet(wallet_id, {"entityId": 1, "currencyId": 1, "companyId": 1})
    if not wallet:
        return jsonify({"error": "Wallet não encontrada"}), 404

    company_id  = str(wallet.get("companyId", ""))
    entity_id   = str(wallet.get("entityId", ""))
    currency_id = str(wallet.get("currencyId", "BRL"))

    transactions = []
    deletions    = []
    for item in items:
        flag = item.get("flag", "")

        if flag == "MISCLASSIFIED":
            # MISCLASSIFIED: reclassify the original txn under the target
            # security. The `matchFlag` selected by the user determines the
            # transaction type (e.g. MISSING_TRANSACTION → buySell,
            # MISSING_EVENT → coupon). Fall back to buySell if absent.
            match_flag = item.get("matchFlag", "") or ""
            txn_type   = _FLAG_TXN_TYPE.get(match_flag) or "buySell"
            balance    = float(item.get("impact") or item.get("originalBalance") or 0)
            target_sid = item.get("securityId") or item.get("targetSecurityId") or ""
            sec_name   = item.get("securityName") or item.get("targetSecurityName") or ""
            original_id = item.get("originalId") or ""

            if not original_id:
                # Without the original _id we can't mark it for deletion —
                # skip the whole item to avoid orphaned txns duplicating cash.
                continue

            desc = f"Reclassificação MISCLASSIFIED — {sec_name}" if sec_name else "Reclassificação MISCLASSIFIED"
            txn_entry = {
                "companyId":              company_id,
                "entityId":               entity_id,
                "walletId":               wallet_id,
                "currencyId":             currency_id,
                "operationDate":          date,
                "liquidationDate":        date,
                "balance":                balance,
                "description":            desc,
                "inputType":              "sheets",
                "beehusTransactionType":  txn_type,
                "hide":                   True,
                "comment":                "",
            }
            if target_sid:
                txn_entry["securityId"] = target_sid
            if item.get("sourceAnomalyKey"):
                txn_entry["sourceAnomalyKey"] = item["sourceAnomalyKey"]
            transactions.append(txn_entry)

            del_entry = {
                "companyId":              company_id,
                "walletId":               wallet_id,
                "originalId":             str(original_id),
                "securityId":             item.get("originalSecurityId") or "",
                "balance":                float(item.get("originalBalance") or balance or 0),
                "operationDate":          date,
                "liquidationDate":        date,
                "beehusTransactionType":  item.get("originalType") or "",
                "description":            f"Desconsiderar original — {sec_name}" if sec_name else "Desconsiderar original (MISCLASSIFIED)",
                "reason":                 "MISCLASSIFIED",
            }
            if item.get("sourceAnomalyKey"):
                del_entry["sourceAnomalyKey"] = item["sourceAnomalyKey"]
            deletions.append(del_entry)
            continue

        txn_type = _FLAG_TXN_TYPE.get(flag)
        # Skip flags that don't produce transactions
        if txn_type is None:
            continue

        sec_name = item.get("securityName", "")
        base_desc = _FLAG_DESCRIPTIONS.get(flag, f"Correção: {flag}")
        description = f"{base_desc} — {sec_name}" if sec_name and sec_name not in ("(carteira)", "(caixa)") else base_desc

        balance = item.get("impact") or 0
        # IR retido na fonte: imposto é uma SAÍDA de caixa. O `impact` vem
        # sempre como magnitude positiva (abs no step 3.3), então invertemos
        # o sinal (×-1) para registrar a transação `taxes` com valor negativo.
        if flag == "WITHHOLDING_TAX":
            try:
                balance = -abs(float(balance))
            except (TypeError, ValueError):
                balance = 0

        txn_entry = {
            "companyId":              company_id,
            "entityId":              entity_id,
            "walletId":              wallet_id,
            "currencyId":            currency_id,
            "operationDate":         date,
            "liquidationDate":       date,
            "balance":               balance,
            "description":           description,
            "inputType":             "sheets",
            "beehusTransactionType": txn_type,
            "hide":                  True,
            "comment":               "",
        }
        if item.get("securityId"):
            txn_entry["securityId"] = item["securityId"]
        if item.get("sourceAnomalyKey"):
            txn_entry["sourceAnomalyKey"] = item["sourceAnomalyKey"]
        transactions.append(txn_entry)

    return jsonify(_sanitize({
        "companyId":    company_id,
        "transactions": transactions,
        "deletions":    deletions,
    }))


_PROVISION_FLAGS = {"MISSING_PROVISION", "WRONG_PROVISION_AMOUNT"}


@bp.route("/api/conciliacao/generate-provisions", methods=["POST"])
def generate_provisions():
    """Build provision rows (for clipboard) from accepted diagnostic items."""
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    date      = data.get("date", "")
    items     = data.get("items", [])

    if not wallet_id or not date or not items:
        return jsonify({"error": "walletId, date e items são obrigatórios"}), 400
    _, err = _require_visible_wallet(wallet_id)
    if err: return err

    # Fetch wallet info
    wallet = resolve_wallet(wallet_id, {"currencyId": 1})
    currency_id = str((wallet or {}).get("currencyId", "BRL"))

    provisions = []
    for item in items:
        flag = item.get("flag", "")
        if flag not in _PROVISION_FLAGS:
            continue

        prov_data = item.get("provisionData") or {}
        sec_name  = item.get("securityName", "")
        sec_id    = item.get("securityId", "")

        desc = f"Provisão gerada por conciliação — {sec_name}" if sec_name else "Provisão gerada por conciliação"

        # Liquidation date is ALWAYS navPackage date + offset (spec). Computed
        # here for EVERY path — MISSING_PROVISION (which already carried the
        # offset via provisionData), WRONG_PROVISION_AMOUNT, and the manual
        # "+ Provisão" override (which previously hardcoded both dates to the
        # navPackage date, dropping the offset). The offset travels on the item
        # (set by step 3.1); provisionData.offset is a secondary fallback.
        offset = item.get("offset")
        if offset is None:
            offset = prov_data.get("offset", 0)
        init_date, liq_date = _prov_dates(date, offset)

        prov_entry = {
            "walletId":        wallet_id,
            "initialDate":     init_date,
            "liquidationDate": liq_date,
            "provisionType":   prov_data.get("provisionType", "buySell"),
            "securityId":      sec_id,
            "balance":         prov_data.get("balance") or item.get("impact") or 0,
            "description":     desc,
            "provisionSource": "adjustments",
            "currencyId":      currency_id,
        }
        if item.get("sourceAnomalyKey"):
            prov_entry["sourceAnomalyKey"] = item["sourceAnomalyKey"]
        provisions.append(prov_entry)

    return jsonify(_sanitize({"provisions": provisions}))


@bp.route("/api/conciliacao/generate-execution-prices", methods=["POST"])
def generate_execution_prices():
    """Build executionPrice rows from accepted MISSING_EXECUTION_PRICE items.

    Output:
        {"companyId": str, "executionPrices": [...]}

    Each row mirrors the payload required by `POST /beehus/financial/
    execution-prices` (companyId, walletId, securityId, positionDate,
    executionPrice) plus a few diagnostic snapshots (`pu`,
    `priorExecutionPrice`, `expectedValue`, `actualBalance`, `amountDiff`)
    so the Correções page can render the row without re-running the
    diagnose. Rows are persisted via /api/correcoes/bulk under the
    `executionPrices` bucket and are pushed to the upstream API later via
    the per-row "Enviar via API" button (`inputed=false` until submitted).
    """
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    date      = data.get("date", "")
    items     = data.get("items", [])

    if not wallet_id or not date or not items:
        return jsonify({"error": "walletId, date e items são obrigatórios"}), 400
    _, err = _require_visible_wallet(wallet_id)
    if err: return err

    wallet = resolve_wallet(wallet_id, {"companyId": 1})
    if not wallet:
        return jsonify({"error": "Wallet não encontrada"}), 404
    company_id = str(wallet.get("companyId", ""))

    rows = []
    for item in items:
        if item.get("flag") != "MISSING_EXECUTION_PRICE":
            continue
        sec_id = item.get("securityId") or ""
        if not sec_id:
            continue
        # Prefer the exact implied price computed during diagnose. Fall back to
        # `actualBalance / -amountDiff` if the front-end didn't relay it.
        exec_price = item.get("expectedExecPrice")
        if exec_price in (None, "") and item.get("actualBalance") is not None and item.get("amountDiff"):
            try:
                exec_price = round(-float(item["actualBalance"]) / float(item["amountDiff"]), 6)
            except (TypeError, ValueError, ZeroDivisionError):
                exec_price = None
        if exec_price in (None, ""):
            continue
        sec_name = item.get("securityName") or ""
        desc = (f"Preço de execução sugerido pela conciliação — {sec_name}"
                if sec_name else "Preço de execução sugerido pela conciliação")
        row = {
            "companyId":           company_id,
            "walletId":            wallet_id,
            "securityId":          sec_id,
            "securityName":        sec_name,
            "positionDate":        date,
            "executionPrice":      float(exec_price),
            "expectedExecPrice":   float(exec_price),
            "pu":                  item.get("pu"),
            "priorExecutionPrice": item.get("executionPrice"),
            "amountDiff":          item.get("amountDiff"),
            "actualBalance":       item.get("actualBalance"),
            "expectedValue":       item.get("expectedValue"),
            "description":         desc,
            "inputed":             False,
            "inputedAt":           None,
            "beehusId":            None,
        }
        if item.get("sourceAnomalyKey"):
            row["sourceAnomalyKey"] = item["sourceAnomalyKey"]
        rows.append(row)

    return jsonify(_sanitize({
        "companyId":       company_id,
        "executionPrices": rows,
    }))


# ── Global Analysis ────────────────────────────────────────────────────────────

_TIER1_FLAGS = {"MISSING_TRANSACTION", "MISSING_PROVISION", "MISSING_EVENT",
                "WRONG_EVENT_BALANCE", "WRONG_PROVISION_AMOUNT"}


# ── Global Execution-Price Check ──────────────────────────────────────────────


# ── Transaction Check ──────────────────────────────────────────────────────────


# ── Scenario Capture ─────────────────────────────────────────────────────────

_SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "scenarios")


