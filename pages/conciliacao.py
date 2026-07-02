from flask import Blueprint, jsonify, request
from db import atomic_write_json, company_visible, resolve_wallet
from datetime import date as _date, timedelta
import json, math, os
import beehus_catalog
from beehus_api import (delete_transaction as _api_delete_transaction,
                        update_transaction as _api_update_transaction,
                        delete_provision as _api_delete_provision,
                        update_provision as _api_update_provision,
                        list_transactions as _api_list_transactions,
                        list_provisions as _api_list_provisions,
                        BeehusAPIError, BeehusAuthError)


def _wallet_company(wallet_id):
    """Resolve the companyId for a given walletId. Returns '' if unknown.
    Used to scope pending-correction lookups from wallet-only endpoints."""
    w = resolve_wallet(wallet_id, {"companyId": 1})
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


bp = Blueprint("conciliacao", __name__)


# ── Routes ─────────────────────────────────────────────────────────────────────
# Nota: a PÁGINA Conciliação (`GET /conciliacao` + template conciliacao.html) foi
# REMOVIDA. Este blueprint segue registrado por causa das rotas `/api/conciliacao/*`
# abaixo — usadas por Conciliação Não Proc. e Repetir Posições — e dos helpers
# deste módulo importados por essas páginas.


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


