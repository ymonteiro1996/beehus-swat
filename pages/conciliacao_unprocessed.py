"""Conciliação (Não Processado).

A sibling of pages/conciliacao.py that runs the same wallet→date→detail
reconciliation flow, but sources the *position* side from
`unprocessedSecurityPositions` instead of `processedPosition`.

Design rules (per spec):
  • Positions come ONLY from `unprocessedSecurityPositions` — these rows are
    keyed by `unprocessedId`. Quantity and PU are taken verbatim from the
    unprocessed document (never from processedPosition).
  • Each unprocessed asset is resolved to a real security via the company's
    `securityMappings` document ({"from": unprocessedId, "to": securityId}).
    Assets without a mapping are surfaced as "não mapeado" — they cannot be
    linked to transactions/provisions.
  • Transactions and provisions are linked exclusively through the mapped
    `securityId` (the "to" side of securityMappings).
  • Cash is read from `cashAccounts` exactly like the original page
    (db.sum_cash_by_dates).

This page is READ-ONLY: it does not mutate transactions/provisions or push
corrections upstream. See EMR/attention report generated alongside it.
"""
from flask import Blueprint, render_template, jsonify, request
from db import (get_company_filter, company_visible, get_company_names,
                resolve_wallet)
import beehus_catalog
import math

from pages.diagnostic_engine import run_funnel, _prov_dates, _fmt_brl
# Figuras de NAV / gap por carteira vêm de navPackages via API — reusamos os
# helpers extraídos da conciliação original (sem Mongo) para os números baterem.
from pages.conciliacao_shared import (
    _diff_threshold_decimal, _recalc_gap_with_corrections, _find_former_nav,
    _load_thresholds,
)
from pages.correcoes_store import (load_corrections_for_wallet,
                                   load_all_pending_provisions)

import logging
_log = logging.getLogger(__name__)

bp = Blueprint("conciliacao_unprocessed", __name__)

_NUM_DATES = 10
_EVENT_TYPES = {"amortization", "coupon"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _safe_num(v):
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    return v


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return _safe_num(obj)


def _mapping_from_position(securities):
    """`{unprocessedId: securityId}` derivado do PRÓPRIO doc da posição não
    processada (`preProcessingData.securityId`) — sem chamar `security-mappings`.

    O pré-processamento já resolve cada `unprocessedId` para o `securityId`
    correspondente e devolve isso embutido em cada security (cobertura ~100% em
    produção). Ativo sem `preProcessingData.securityId` fica 'não mapeado' (linha
    'U:'+unprocessedId) — como antes acontecia com ativo sem entrada em
    securityMappings. `to` normalizado a string p/ casar com os securityIds de
    transações/provisões (mix de ObjectId/string)."""
    out = {}
    for s in (securities or []):
        uid = s.get("unprocessedId")
        sid = (s.get("preProcessingData") or {}).get("securityId")
        if uid and sid:
            out[str(uid)] = str(sid)
    return out


def _group_unprocessed(securities):
    """Group an unprocessedSecurityPositions.securities[] array by unprocessedId.

    Sums quantity & balance and derives a weighted-average PU = balance/qty,
    so a wallet's raw rows collapse into one row per asset."""
    grouped = {}
    for s in securities:
        uid = s.get("unprocessedId", "")
        if not uid:
            continue
        qty = s.get("quantity") or 0
        pu = s.get("pu") or 0
        bal = s.get("balance") if s.get("balance") is not None else pu * qty
        if uid not in grouped:
            grouped[uid] = {"unprocessedId": uid, "quantity": 0, "balance": 0}
        grouped[uid]["quantity"] += qty
        grouped[uid]["balance"] += bal
    for g in grouped.values():
        g["pu"] = (g["balance"] / g["quantity"]) if g["quantity"] else 0
    return grouped  # {unprocessedId: {unprocessedId, quantity, balance, pu}}


def _aggregate_positions(securities):
    """Collapse an unprocessed securities[] array into one row per *resolved
    securityId*, summing quantity & balance and deriving a weighted-average PU.

    O `securityId` de cada ativo vem do `preProcessingData.securityId` do próprio
    doc (via `_mapping_from_position`) — sem chamar `security-mappings`.

    Several distinct unprocessedIds frequently map to the same securityId (e.g.
    two CDB lots). The processing step (processedPosition) merges them into a
    single security row, so we must do the same to (a) match the original
    conciliação's per-security numbers and (b) avoid attaching the same
    securityId-keyed transactions to multiple split rows. Unmapped assets keep
    their own row, keyed by ("U:" + unprocessedId).

    Returns {row_key: {securityId|None, unprocessedIds:[...], quantity,
    balance, pu}} where row_key is the securityId for mapped assets.
    """
    sid_by_uid = _mapping_from_position(securities)
    by_uid = _group_unprocessed(securities)  # {uid: {unprocessedId, quantity, balance, pu}}
    rows = {}
    for uid, d in by_uid.items():
        sid = sid_by_uid.get(uid)
        key = sid if sid else ("U:" + uid)
        r = rows.setdefault(key, {"securityId": sid, "unprocessedIds": [],
                                  "quantity": 0.0, "balance": 0.0})
        r["unprocessedIds"].append(uid)
        r["quantity"] += d["quantity"]
        r["balance"] += d["balance"]
    for r in rows.values():
        r["pu"] = (r["balance"] / r["quantity"]) if r["quantity"] else 0
    return rows


_TOL_ABS = 0.01
_TOL_REL = 0.05  # 5% — matches conciliacao._TOLERANCE_REL


def _approx(a, b):
    if a is None or b is None:
        return False
    diff = abs(a - b)
    return diff <= _TOL_ABS or diff <= abs(b) * _TOL_REL


def _reconcile_security(pu, qty, bal, f_pu, f_qty, f_bal, txn_balance, event_balance):
    """Derive the contribution / return reconciliation for one security from
    *existing* data only — no processedPosition contribution fields, no
    navPackage.

    Validated against processedPosition.totalContribution on ~95% of real
    securities (the misses are futures with notional balances and liquidity
    funds with unrecorded sweeps — exactly the divergences a conciliação
    should surface):

        contribution  = (currentBalance − formerBalance) + Σ(linked txn balance)
        returnContrib = contribution / formerBalance
        returnPU      = currentPU / formerPU − 1
        diffRent      = returnPU − returnContrib

    Returns RAW figures (no event correction) so the gap stays consistent with
    the original navPackage gap — which the diagnostic engine then explains via
    its Step 2/3.2 event handling. `eventCorrected` is an informational flag:
    True when the rentab gap is fully explained by coupon/amortization cash
    (i.e. this asset reconciles and isn't a real problem).

    Returns: returnPU, totalContribution, returnContrib, diffRent, eventCorrected.
    """
    return_pu = None
    if pu is not None and f_pu:
        try:
            return_pu = pu / f_pu - 1
        except ZeroDivisionError:
            return_pu = None

    contribution = return_contrib = diff_rent = None
    event_corrected = False
    if f_bal:
        contribution = (bal or 0) - f_bal + (txn_balance or 0)
        return_contrib = contribution / f_bal
        if return_pu is not None:
            diff_rent = return_pu - return_contrib
            if diff_rent is not None and event_balance and f_bal:
                # Is the rentab gap explained by the coupon/amort cash?
                if _approx(event_balance, -diff_rent * f_bal):
                    event_corrected = True

    return {
        "returnPU": return_pu,
        "totalContribution": contribution,
        "returnContrib": return_contrib,
        "diffRent": diff_rent,
        "eventCorrected": event_corrected,
    }


# Transações que entram no inAndOutFlows (afetam o nº de cotas, não a contribuição
# de ativos). Usadas na estimativa "sem explosão" para recalcular a cota.
_FLOW_TYPES = {"withdrawalDeposit", "taxes", "securityTransfer", "withdrawalDepositAdjustment"}


def _explosao_estimate(*, nav, nav_per_share, amount, former_nav, former_amount,
                       return_contribution, e_all, e_flow, count):
    """Reconciliação NAV estimada desconsiderando os lançamentos de 'explosão'.

    Identidade validada em 100% dos navPackages: ``nav == navPerShare × amount``
    → ``amount`` é o nº de cotas e ``nav`` o PL. Modelo:

        cotas       = amount ;  formerCota = formerNav / formerAmount
        nav_est     = nav − E_all                       (remove explosão do PL)
        cotas_est   = amount − E_flow / navPerShare      (txn de fluxo —
                      withdrawalDeposit/taxes/securityTransfer/
                      withdrawalDepositAdjustment — entram no inAndOutFlows e
                      portanto alteram o nº de cotas, não a cota em si)
        navPerShare_est = nav_est / cotas_est
        retNav_est      = navPerShare_est / formerCota − 1
        retContr_est    = returnContribution − E_all / formerNav
        gap%_est = retNav_est − retContr_est ; gap$_est = gap%_est × formerNav

    Retorna ``None`` quando não há explosão (``count == 0``) ou faltam insumos.
    """
    if not count:
        return None
    if not nav or not nav_per_share or not amount or not former_nav or not former_amount:
        return None
    try:
        nav_est = nav - e_all
        cotas_est = amount - (e_flow / nav_per_share)
        if not cotas_est:
            return None
        nps_est = nav_est / cotas_est
        former_cota = former_nav / former_amount
        if not former_cota:
            return None
        ret_nav_est = nps_est / former_cota - 1
        ret_contr_est = (return_contribution - e_all / former_nav
                         if return_contribution is not None else None)
        gap_pct_est = (ret_nav_est - ret_contr_est) if ret_contr_est is not None else None
        gap_cash_est = (gap_pct_est * former_nav) if gap_pct_est is not None else None
    except (TypeError, ZeroDivisionError):
        return None
    return {
        "nav": round(nav_est, 2),
        "navPerShare": _safe_num(round(nps_est, 8)),
        "returnNavPerShare": _safe_num(round(ret_nav_est, 8)),
        "returnContribution": (_safe_num(round(ret_contr_est, 8))
                               if ret_contr_est is not None else None),
        "gapPct": _safe_num(round(gap_pct_est, 8)) if gap_pct_est is not None else None,
        "gapCash": round(gap_cash_est, 2) if gap_cash_est is not None else None,
        "explosaoTotal": round(e_all, 2),
        "explosaoFlow": round(e_flow, 2),
        "count": count,
    }


def _company_wallets(company_id):
    """Return {wallet_id_str: name} for every wallet of `company_id`."""
    return beehus_catalog.wallets_for_company(company_id)


# ── Nomes de securities SEM catálogo ─────────────────────────────────────────
# A API já devolve o `beehusName` populado nas próprias rotas: posição (em
# `preProcessingData`), transações (`securityId.beehusName`, capturado pela
# camada do catálogo em `securityBeehusName`). Só as PROVISÕES (lidas do
# envelope processed-position, que traz o securityId como string crua) não têm
# nome — essas caem no catálogo, e só quando o ativo nem está na posição/
# transações da carteira+data (resíduo raro).

def _position_name_hints(unproc_doc):
    """`{securityId_str: beehusName}` dos ativos da posição não processada, lido
    de `preProcessingData` (presente em ~100% dos dados). Sem catálogo."""
    out = {}
    for s in (unproc_doc or {}).get("securities", []) or []:
        pp = s.get("preProcessingData") or {}
        sid = str(pp.get("securityId") or "")
        nm = pp.get("beehusName")
        if sid and nm:
            out[sid] = nm
    return out


def _txn_name_hints(txn_docs):
    """`{securityId_str: beehusName}` das transações (`securityBeehusName`
    capturado em `beehus_catalog._normalize_txn`). Sem catálogo."""
    out = {}
    for t in txn_docs or []:
        sid = str(t.get("securityId") or "")
        nm = t.get("securityBeehusName")
        if sid and nm:
            out[sid] = nm
    return out


def _resolve_provision_names(prov_sids, hints):
    """`(names, catalog_warming)` para securityIds de PROVISÕES.

    Usa `hints` (nomes já conhecidos da posição/transações) primeiro — o
    securityId de uma provisão quase sempre está na posição/transações da
    carteira+data. Só o RESÍDUO (provisão de um ativo fora desse conjunto) vai ao
    catálogo (`securities_by_ids`). Se o catálogo estiver FRIO, NÃO bloqueia:
    dispara o warm em background, deixa esses nomes como o próprio id e sinaliza
    `catalog_warming=True` — o front mostra o spinner e refaz quando aquecer."""
    out = {}
    residual = []
    for s in prov_sids:
        s = str(s)
        if not s:
            continue
        if hints.get(s):
            out[s] = hints[s]
        else:
            residual.append(s)
    catalog_warming = False
    if residual:
        if beehus_catalog.securities_index_is_warm():
            for d in beehus_catalog.securities_by_ids(residual).values():
                out[str(d["_id"])] = d.get("beehusName", str(d["_id"]))
        else:
            beehus_catalog.warm_securities_index_async()
            catalog_warming = True
        for s in residual:
            out.setdefault(s, s)   # rótulo provisório = id (preenche ao aquecer)
    return out, catalog_warming


def _format_provisions(prov_docs, names):
    """`(provisions, total)` no shape do painel inline. `names` = {sid: beehusName}
    já resolvido pelo chamador (posição/transações + fallback de catálogo)."""
    out = []
    for p in prov_docs:
        sid = str(p.get("securityId") or "")
        amt = p.get("balance") if p.get("balance") is not None else p.get("amount")
        out.append({
            "provisionId": str(p.get("_id", "") or ""),
            "securityId": sid,
            "securityName": names.get(sid, sid),
            "initialDate": str(p.get("initialDate", ""))[:10],
            "liquidationDate": str(p.get("liquidationDate", ""))[:10],
            "balance": float(amt) if amt is not None else None,
            "provisionType": p.get("provisionType", "") or "",
            "description": p.get("description", "") or "",
        })
    out.sort(key=lambda p: (p["liquidationDate"], p["securityName"]))
    total = round(sum(p["balance"] for p in out if p["balance"] is not None), 2)
    return out, total


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/conciliacao-unprocessed")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("conciliacao_unprocessed.html", companies=companies)


@bp.route("/api/conciliacao-unprocessed/rows")
def get_rows():
    """Divergent-wallet rows for `date`, identical in shape to the original
    conciliação (NAV / Cota / Quantidade / Passivo / Return NAV / Return Contrib
    / Diferença / Novo Gap %), but scoped to wallets that ALSO have an
    unprocessed position that day.

    NAV figures come straight from `navPackages` — they are NOT computed here.
    A wallet with no navPackage on `date` (or no prior untrashed navPackage for
    its former NAV) is omitted entirely, per the fallback rule.
    """
    try:
        company_id = request.args.get("companyId", "")
        date = request.args.get("date", "")
        if not company_id or not company_visible(company_id) or not date:
            return jsonify({"rows": [], "date": date})

        wallets = _company_wallets(company_id)
        wallet_ids = list(wallets.keys())
        if not wallet_ids:
            return jsonify({"rows": [], "date": date})

        # This page's scope: wallets with an unprocessed position on `date`.
        unproc_wids = beehus_catalog.unprocessed_existing_wallets(
            company_id, date, wallet_ids)
        if not unproc_wids:
            return jsonify({"rows": [], "date": date})

        threshold = _diff_threshold_decimal(request)

        # Divergências NA DATA via endpoint consolidado /results (1 chamada, AO
        # VIVO, fallback Mongo). SEM NAV anterior: gap em % (rnps-rc) e em R$
        # (financialValueReturnDifference) já vêm prontos. NAV anterior +
        # recálculo com correções existem só na análise individual da carteira.
        # Escopo desta página mantido: só carteiras com posição não-processada.
        res = beehus_catalog.nav_results(company_id, date)
        rows = []
        for w in res.get("walletsWithNavDetailed", []):
            if not beehus_catalog._nav_results_is_gap(w, threshold):
                continue
            wid = beehus_catalog.id_str(w.get("walletId"))
            if not wid or wid not in unproc_wids:
                continue  # fora do escopo (sem posição não-processada na data)
            rows.append({
                "walletId": wid,
                "walletName": w.get("walletName") or wallets.get(wid, wid),
                "nav": _safe_num(w.get("nav")),
                "navPerShare": _safe_num(w.get("navPerShare")),
                "amount": _safe_num(w.get("amount")),
                "returnNavPerShare": _safe_num(w.get("returnNavPerShare")),
                "returnContribution": _safe_num(w.get("returnContribution")),
                "gapCash": _safe_num(w.get("financialValueReturnDifference")),
            })
        rows.sort(key=lambda x: x["walletName"])
        return jsonify(_sanitize({"rows": rows, "date": date}))
    except Exception:
        import logging, traceback
        traceback.print_exc()
        logging.getLogger(__name__).exception("conciliacao-unprocessed /rows failed")
        return jsonify({"error": "falha ao processar"}), 500


@bp.route("/api/conciliacao-unprocessed/wallet-detail")
def get_wallet_detail():
    """Per-security reconciliation for one wallet on `date`, sourced from
    unprocessed positions and linked to transactions/provisions via the mapped
    securityId."""
    wallet_id = request.args.get("walletId", "")
    date = request.args.get("date", "")
    if not wallet_id or not date:
        return jsonify({"error": "walletId e date obrigatórios"}), 400

    # companyId é HINT de performance (escopa o resolve_wallet a 1 empresa em vez
    # do índice global de ~19); o companyId real vem do doc + company_visible.
    wallet = resolve_wallet(wallet_id, {"companyId": 1, "name": 1},
                            company_id=request.args.get("companyId") or None)
    company_id = str(wallet["companyId"]) if wallet else ""
    if not company_id or not company_visible(company_id):
        return jsonify({"error": "acesso negado"}), 403

    # ── Wallet-level NAV comes from navPackages (NOT computed) ──────────────────
    # Fallback rule: no navPackage on `date` → don't show the wallet's data.
    nav_pkg = beehus_catalog.nav_doc_for_entity_date(wallet_id, date, company_id)
    if not nav_pkg:
        return jsonify({"error": "Carteira sem navPackage nesta data — fora do escopo de análise.",
                        "walletId": wallet_id, "date": date}), 404
    # Former date is the navPackage former date (same source of truth as the
    # original) — it drives both the formerNav and the per-security Δ columns.
    # No prior untrashed navPackage → out of scope (same fallback as /rows and
    # diagnose; otherwise the date-nav arrows could open a half-populated page).
    former_date, former_nav = _find_former_nav(wallet_id, date, company_id)
    if former_date is None or former_nav is None:
        return jsonify({"error": "Carteira sem navPackage anterior — fora do escopo de análise.",
                        "walletId": wallet_id, "date": date}), 404

    # ── Current + former unprocessed positions (qty/PU come ONLY from here) ──────
    # Consolidated by securityId for mapped assets (so two unprocessedId lots of
    # the same security collapse into one row, matching processedPosition), and
    # kept per-unprocessedId for unmapped assets. The former position is read at
    # the navPackage former date so the Δ columns reference the same prior day.
    cur_doc = beehus_catalog.unprocessed_doc(wallet_id, date, company_id)
    if not cur_doc:
        # This page only analyses wallets with an unprocessed position on `date`
        # (the date-nav arrows can land on a date that has a navPackage but no
        # unprocessed snapshot). Surface it instead of a half-populated screen.
        return jsonify({"error": "Carteira sem posição não processada nesta data — fora do escopo de análise.",
                        "walletId": wallet_id, "date": date}), 404
    cur_rows = _aggregate_positions(cur_doc.get("securities", []))

    former_rows = {}
    if former_date:
        f_doc = beehus_catalog.unprocessed_doc(wallet_id, former_date, company_id)
        former_rows = _aggregate_positions((f_doc or {}).get("securities", []))

    # Mapped securityIds present in the current position — transactions and
    # provisions only link onto these.
    pos_sid_set = {r["securityId"] for r in cur_rows.values() if r["securityId"]}
    pos_hints = _position_name_hints(cur_doc)   # sid→beehusName (preProcessingData)

    # "Não pré-processado": o snapshot tem ativos mas NENHUM com securityId
    # resolvido (preProcessingData.securityId vazio em todos) — o
    # pré-processamento upstream ainda não rodou para esta data. Nesse caso o
    # front mostra um aviso em vez de listar os ativos com o unprocessedId como
    # nome (fallback enganoso). NAV/cota/caixa vêm do navPackage e seguem válidos.
    not_pre_processed = bool(cur_rows) and not pos_sid_set

    # ── Transactions (liquidationDate == date), grouped by securityId ───────────
    # Via endpoint G (transactions_on_date); o filtro `balance != None` (Mongo
    # `$ne: None`, exclui null E ausente) é aplicado no cliente.
    txn_docs = [t for t in beehus_catalog.transactions_on_date(company_id, [wallet_id], date)
                if t.get("balance") is not None]
    # Resolve names for txn securityIds.
    txn_sids = {str(t.get("securityId") or "") for t in txn_docs}
    txn_sids.discard("")
    txn_name_by_id = _txn_name_hints(txn_docs)   # da própria transação (sem catálogo)

    txn_total_by_sid = {}
    event_total_by_sid = {}
    txns_by_sid = {}
    total_txns = 0.0
    explosao_txn_total = explosao_flow_total = 0.0
    explosao_count = 0
    for t in txn_docs:
        sid = str(t.get("securityId") or "")
        try:
            bal = float(t.get("balance") or 0)
        except (TypeError, ValueError):
            bal = 0.0
        total_txns += bal
        typ = t.get("beehusTransactionType", "") or ""
        if "explosão" in (t.get("description") or "").lower():
            explosao_txn_total += bal
            explosao_count += 1
            if typ in _FLOW_TYPES:
                explosao_flow_total += bal
        entry = {
            "txnId": str(t.get("_id", "") or ""),
            "operationDate": str(t.get("operationDate", "") or "")[:10],
            "liquidationDate": str(t.get("liquidationDate", "") or "")[:10],
            "securityId": sid,
            "securityName": txn_name_by_id.get(sid, t.get("securityName") or ""),
            "beehusTransactionType": typ,
            "balance": t.get("balance"),
            "quantity": t.get("quantity"),
            "price": t.get("price"),
            "description": t.get("description", "") or "",
            "isEvent": typ in _EVENT_TYPES,
        }
        if sid and sid in pos_sid_set:
            txn_total_by_sid[sid] = txn_total_by_sid.get(sid, 0.0) + bal
            if typ in _EVENT_TYPES:
                event_total_by_sid[sid] = event_total_by_sid.get(sid, 0.0) + bal
            txns_by_sid.setdefault(sid, []).append(entry)

    # ── Provisions active on `date`, grouped by securityId ──────────────────────
    # Caixa + provisões ativas na data de UMA única resposta processed-position
    # (envelope {position, cashAccounts, provisions}): as provisões saem do bloco
    # `provisions` (== provisions_active, mesmo shape; evita o scan desde 2000) e
    # o caixa é reusado na seção de caixa abaixo (sem 2ª chamada ao endpoint).
    # Sem posição processada na data → provisões vazias ("não exibir").
    _cash_by_date, prov_docs = beehus_catalog.wallet_cash_and_provisions(
        wallet_id, [former_date, date], company_id)
    prov_sids = {str(p.get("securityId") or "") for p in prov_docs}
    prov_sids.discard("")
    prov_name_by_id, _catalog_warming = _resolve_provision_names(
        prov_sids, {**pos_hints, **txn_name_by_id})

    prov_total_by_sid = {}
    provs_by_sid = {}
    explosao_prov_total = 0.0
    for p in prov_docs:
        sid = str(p.get("securityId") or "")
        amt = p.get("balance") if p.get("balance") is not None else p.get("amount")
        try:
            amt_f = float(amt) if amt is not None else 0.0
        except (TypeError, ValueError):
            amt_f = 0.0
        # Provisões de explosão entram só no lado da contribuição (E_all), nunca
        # em E_flow: são lançamentos pendentes, não movimentam cotas/inAndOutFlows
        # na data (E_flow é exclusivo de transações de fluxo — ver _explosao_estimate).
        if "explosão" in (p.get("description") or "").lower():
            explosao_prov_total += amt_f
            explosao_count += 1
        entry = {
            "provisionId": str(p.get("_id", "") or ""),
            "securityId": sid,
            "securityName": prov_name_by_id.get(sid, ""),
            "initialDate": str(p.get("initialDate", ""))[:10],
            "liquidationDate": str(p.get("liquidationDate", ""))[:10],
            "balance": _safe_num(amt_f),
            "provisionType": p.get("provisionType", "") or "",
            "description": p.get("description", "") or "",
        }
        if sid and sid in pos_sid_set:
            prov_total_by_sid[sid] = prov_total_by_sid.get(sid, 0.0) + amt_f
            provs_by_sid.setdefault(sid, []).append(entry)

    # ── Resolve names for mapped position securities (preProcessingData) ────────
    sec_names = {sid: pos_hints.get(sid, sid) for sid in pos_sid_set}

    # ── Build per-security reconciliation rows ──────────────────────────────────
    # contribution_wallet = Σ security contributions (computed; returned as an
    # informational total. The headline NAV gap comes from navPackage, below.)
    contribution_wallet = 0.0
    securities = []
    for key, r in cur_rows.items():
        sid = r["securityId"]
        uids = r["unprocessedIds"]
        uid_label = ", ".join(uids)
        pu = r.get("pu")
        qty = r.get("quantity")
        # Use the authoritative summed balance from the aggregate (it preserves
        # the stored balance, which may differ from pu×qty for instruments with
        # embedded adjustments); fall back to pu×qty only if it is absent.
        balance = round(r["balance"], 2) if r.get("balance") is not None else (
            round(pu * qty, 2) if (pu is not None and qty is not None) else None)

        f = former_rows.get(key, {})
        f_pu = f.get("pu")
        f_qty = f.get("quantity")
        f_bal = round(f["balance"], 2) if f.get("balance") is not None else (
            round(f_pu * f_qty, 2) if (f_pu is not None and f_qty is not None) else None)

        amt_diff = round(qty - (f_qty or 0), 6) if qty is not None else None
        bal_diff = round((balance or 0) - (f_bal or 0), 2) if balance is not None else None

        txn_bal = txn_total_by_sid.get(sid) if sid else None
        event_bal = event_total_by_sid.get(sid, 0.0) if sid else 0.0
        rec = _reconcile_security(pu, qty, balance, f_pu, f_qty, f_bal,
                                  txn_bal, event_bal)
        return_pu = rec["returnPU"]
        total_contrib = rec["totalContribution"]
        return_contrib = rec["returnContrib"]
        diff_rent = rec["diffRent"]

        # Accumulate the (informational) computed contribution total.
        if f_bal and total_contrib is not None:
            contribution_wallet += total_contrib

        sec_name = sec_names.get(sid, uid_label) if sid else uid_label
        linked_txns = sorted(txns_by_sid.get(sid, []),
                             key=lambda e: (e.get("operationDate") or "",
                                            e.get("liquidationDate") or "")) if sid else []
        linked_provs = provs_by_sid.get(sid, []) if sid else []

        securities.append({
            "unprocessedId": uid_label,
            "unprocessedIds": uids,
            "securityId": sid,
            "securityName": sec_name,
            "mapped": bool(sid),
            "isNew": former_date is not None and key not in former_rows,
            "pu": _safe_num(pu),
            "quantity": _safe_num(qty),
            "balance": _safe_num(balance),
            "formerPu": _safe_num(f_pu),
            "formerQuantity": _safe_num(f_qty),
            "formerBalance": _safe_num(f_bal),
            "amountDifference": _safe_num(amt_diff),
            "balanceDifference": _safe_num(bal_diff),
            "returnPU": _safe_num(round(return_pu, 8) if return_pu is not None else None),
            "totalContribution": _safe_num(round(total_contrib, 2) if total_contrib is not None else None),
            "returnContrib": _safe_num(round(return_contrib, 8) if return_contrib is not None else None),
            "diffRent": _safe_num(round(diff_rent, 8) if diff_rent is not None else None),
            "eventContribution": _safe_num(round(event_bal, 2)) if (sid and sid in event_total_by_sid) else None,
            "eventCorrected": rec["eventCorrected"],
            "transactionBalance": _safe_num(round(txn_total_by_sid.get(sid, 0.0), 2)) if (sid and sid in txn_total_by_sid) else None,
            "provisionBalance": _safe_num(round(prov_total_by_sid.get(sid, 0.0), 2)) if (sid and sid in prov_total_by_sid) else None,
            "transactions": linked_txns,
            "transactionCount": len(linked_txns),
            "provisions": linked_provs,
        })

    # NOTE: We intentionally list ONLY assets present in the *current*
    # unprocessed position (mirroring the original conciliação, which iterates
    # the current processedPosition and never adds former-only rows). Assets
    # that existed in the former snapshot but exited by `date` are surfaced via
    # the per-asset `isNew` flag / Δ columns rather than as standalone rows.
    securities.sort(key=lambda x: (
        0 if (not x["mapped"]) else (1 if x.get("isNew") else 2),
        x["securityName"] or "",
    ))

    # ── Cash reconciliation ─────────────────────────────────────────────────────
    # Reusa o caixa do mesmo envelope processed-position já buscado acima
    # (wallet_cash_and_provisions) — sem 2ª chamada ao endpoint.
    cash = _cash_by_date
    former_cash = cash.get(former_date) if former_date else None
    current_cash = cash.get(date)
    # total_txns soma TODAS as transações da data, inclusive o fluxo de explosão
    # (withdrawalDeposit/taxes/etc.) — é movimento real de caixa, então pertence
    # à projeção. A estimativa "sem explosão" trata o fluxo à parte (E_flow).
    projected_cash = (former_cash + total_txns) if former_cash is not None else None
    cash_diff = (projected_cash - current_cash) if (
        projected_cash is not None and current_cash is not None) else None

    # ── Wallet-level NAV gap — read from navPackage (NOT computed) ──────────────
    # returnNavPerShare / returnContribution come straight from the stored
    # navPackage; formerNav is the most recent prior untrashed navPackage's nav
    # (resolved above, same source of truth as the original page). The
    # per-security contribution columns above remain computed from the
    # unprocessed position — that is this page's analytical purpose; only the
    # headline NAV figures switch source.
    return_nav_per_share = _safe_num(nav_pkg.get("returnNavPerShare"))
    return_contribution = _safe_num(nav_pkg.get("returnContribution"))
    former_nav = _safe_num(former_nav)
    gap_pct = (return_nav_per_share - return_contribution) if (
        return_nav_per_share is not None and return_contribution is not None) else None
    gap_cash = (gap_pct * former_nav) if (gap_pct is not None and former_nav) else None

    # ── Estimativa desconsiderando os lançamentos de "explosão" ─────────────────
    estimate = _explosao_estimate(
        nav=_safe_num(nav_pkg.get("nav")), nav_per_share=_safe_num(nav_pkg.get("navPerShare")),
        amount=_safe_num(nav_pkg.get("amount")), former_nav=former_nav,
        former_amount=_safe_num(nav_pkg.get("formerAmount")),
        return_contribution=return_contribution,
        e_all=round(explosao_txn_total + explosao_prov_total, 2),
        e_flow=round(explosao_flow_total, 2), count=explosao_count)

    # Provisões já vêm do envelope processed-position (prov_docs, acima) — devolve
    # a lista flat aqui p/ o painel inline ler do próprio wallet-detail, sem a
    # chamada /provisions separada (que refazia o processed-position).
    provisions_list, provisions_total = _format_provisions(prov_docs, prov_name_by_id)

    return jsonify(_sanitize({
        "walletName": wallet.get("name", wallet_id) if wallet else wallet_id,
        "walletId": wallet_id,
        "date": date,
        "formerDate": former_date,          # navPackage former date (drives formerNav + Δ columns)
        "securities": securities,
        "formerCash": former_cash,
        "currentCash": current_cash,
        "totalTransactions": round(total_txns, 2),
        "projectedCash": round(projected_cash, 2) if projected_cash is not None else None,
        "cashDifference": round(cash_diff, 2) if cash_diff is not None else None,
        # Wallet-level NAV reconciliation (from navPackage)
        "nav": _safe_num(nav_pkg.get("nav")),
        "navPerShare": _safe_num(nav_pkg.get("navPerShare")),
        "amount": _safe_num(nav_pkg.get("amount")),
        "inAndOutFlows": _safe_num(nav_pkg.get("inAndOutFlows")),
        "formerNav": round(former_nav, 2) if former_nav is not None else None,
        "totalContribution": round(contribution_wallet, 2),  # computed (informational)
        "returnContribution": _safe_num(round(return_contribution, 8)) if return_contribution is not None else None,
        "returnNavPerShare": _safe_num(round(return_nav_per_share, 8)) if return_nav_per_share is not None else None,
        "gapPct": _safe_num(round(gap_pct, 8)) if gap_pct is not None else None,
        "gapCash": _safe_num(round(gap_cash, 2)) if gap_cash is not None else None,
        # Estimativa sem os lançamentos de "explosão" (None quando não há nenhum)
        "estimate": estimate,
        # Provisões ativas na data (mesmo shape do painel /provisions) — servidas
        # aqui p/ o frontend não disparar /provisions à parte.
        "provisions": provisions_list,
        "provisionsTotal": provisions_total,
        # True quando o catálogo está aquecendo p/ nomear provisões de ativos fora
        # da posição/transações (resíduo raro) — o front mostra o spinner e refaz.
        "catalogWarming": _catalog_warming,
        # True quando a posição bruta da data ainda não foi pré-processada (nenhum
        # ativo com securityId resolvido) — o front exibe um aviso em vez de listar
        # os ativos com o unprocessedId como nome.
        "notPreProcessed": not_pre_processed,
    }))


def _sec_info_for(sids):
    """{sid: securities doc} with settlement/nav days + securityType + name.
    `securities_by_ids` normaliza os ids (str/ObjectId), então passamos strings."""
    q = [s for s in sids if s]
    if not q:
        return {}
    return {str(d["_id"]): d
            for d in beehus_catalog.securities_by_ids(q).values()}


def _resolve_sec_info(sids, hints):
    """`(sec_info, catalog_warming)` para o funil (dias de liquidação/NAV +
    securityType). Usa `hints` (sec_info já vindo das transações, via
    `securitySecInfo` — mesmas chaves do catálogo) primeiro; o RESÍDUO (ativos de
    posição sem transação) vem do catálogo (`_sec_info_for`). Catálogo FRIO → NÃO
    bloqueia e NÃO assume default 0: aquece em background, sinaliza
    `catalog_warming=True` e os dias do resíduo ficam corretos no refetch (o front
    mostra o spinner e refaz). Catálogo quente → resíduo resolvido na hora."""
    out = {}
    residual = []
    for s in sids:
        s = str(s)
        if not s:
            continue
        if s in hints:
            out[s] = hints[s]
        else:
            residual.append(s)
    catalog_warming = False
    if residual:
        if beehus_catalog.securities_index_is_warm():
            out.update(_sec_info_for(residual))
        else:
            beehus_catalog.warm_securities_index_async()
            catalog_warming = True
    return out, catalog_warming


def _build_diagnose_ctx(wallet_id, date, company_id_hint=None):
    """Assemble the diagnostic-engine context.

    The wallet-level NAV gap (returnNavPerShare / returnContribution / gap) is
    read from `navPackages` — NOT computed — exactly like the original
    Diagnosticar. The per-security side (current_secs / former_map) comes from
    `unprocessedSecurityPositions` (consolidated by securityId), so the funnel
    attributes the navPackage gap against the unprocessed positions and their
    linked transactions/provisions. Pending /correcoes rows are injected like
    the original. Returns (ctx, error_tuple)."""
    wallet = resolve_wallet(wallet_id, {"companyId": 1, "name": 1},
                            company_id=company_id_hint)
    company_id = str(wallet["companyId"]) if wallet else ""
    if not company_id or not company_visible(company_id):
        return None, (jsonify({"error": "acesso negado"}), 403)

    # ── Wallet-level gap from navPackage (NOT computed) ─────────────────────────
    nav_pkg = beehus_catalog.nav_doc_for_entity_date(wallet_id, date, company_id)
    if not nav_pkg:
        return None, (jsonify({
            "error": "Carteira sem navPackage nesta data — fora do escopo de análise.",
            "walletId": wallet_id, "date": date}), 404)
    former_date, former_nav = _find_former_nav(wallet_id, date, company_id)
    if former_date is None or former_nav is None:
        return None, (jsonify({
            "error": "Carteira sem navPackage anterior — fora do escopo de análise.",
            "walletId": wallet_id, "date": date}), 404)

    cur_doc = beehus_catalog.unprocessed_doc(wallet_id, date, company_id)
    if not cur_doc:
        return None, (jsonify({
            "error": "Carteira sem posição não processada nesta data — fora do escopo de análise.",
            "walletId": wallet_id, "date": date}), 404)
    cur_rows = _aggregate_positions(cur_doc.get("securities", []))

    # Former unprocessed position is fetched at the navPackage former date (the
    # same alignment the original uses for processedPosition) so per-security
    # deltas reference the same prior day as the navPackage gap.
    f_doc = beehus_catalog.unprocessed_doc(wallet_id, former_date, company_id)
    former_rows = _aggregate_positions((f_doc or {}).get("securities", []))

    pos_sid_set = {r["securityId"] for r in cur_rows.values() if r["securityId"]}

    # ── Pending corrections (same /correcoes store as the original) ─────────────
    try:
        corr_txns, _c2, corr_dels = load_corrections_for_wallet(company_id, date, wallet_id)
        corr_provs = load_all_pending_provisions(company_id, wallet_id)
    except Exception:
        # A genuinely unreadable corrections store must not silently masquerade
        # as "no corrections" (that would make the modal's recalc disagree with
        # the /rows "Novo Gap %"). Log it; proceed best-effort.
        _log.exception("conciliacao-unprocessed: falha ao carregar correções pendentes %s/%s",
                       wallet_id, date)
        corr_txns, corr_dels, corr_provs = [], [], []
    deletion_ids = {str(d.get("originalId")) for d in corr_dels if d.get("originalId")}

    # ── Transactions on `date` → flat list + per-sid balances/events ────────────
    all_txns_flat = []
    txn_total_by_sid = {}
    event_total_by_sid = {}
    txn_secinfo_hints = {}   # sid → sec_info (dias/tipo) capturado da transação
    for d in beehus_catalog.transactions_on_date(company_id, [wallet_id], date):
        txn_id = str(d.get("_id", "") or "")
        if txn_id and txn_id in deletion_ids:
            continue
        sid = str(d.get("securityId", "") or "")
        typ = d.get("beehusTransactionType")
        all_txns_flat.append({"type": typ, "balance": d.get("balance"),
                              "securityId": sid, "txnId": txn_id})
        try:
            bal = float(d.get("balance") or 0)
        except (TypeError, ValueError):
            bal = 0.0
        if sid:
            txn_total_by_sid[sid] = txn_total_by_sid.get(sid, 0.0) + bal
            if (typ or "") in _EVENT_TYPES:
                event_total_by_sid[sid] = event_total_by_sid.get(sid, 0.0) + bal
            if d.get("securitySecInfo") and sid not in txn_secinfo_hints:
                txn_secinfo_hints[sid] = d["securitySecInfo"]
    for ct in corr_txns:
        all_txns_flat.append({"type": ct.get("beehusTransactionType"),
                              "balance": ct.get("balance"),
                              "securityId": str(ct.get("securityId", "") or ""),
                              "pending": True})
        sid = str(ct.get("securityId", "") or "")
        if sid:
            try:
                bal = float(ct.get("balance") or 0)
            except (TypeError, ValueError):
                bal = 0.0
            txn_total_by_sid[sid] = txn_total_by_sid.get(sid, 0.0) + bal
            if (ct.get("beehusTransactionType") or "") in _EVENT_TYPES:
                event_total_by_sid[sid] = event_total_by_sid.get(sid, 0.0) + bal

    # Nomes dos ativos da posição via preProcessingData (sem catálogo).
    _pos_hints = _position_name_hints(cur_doc)
    sec_names = {sid: _pos_hints.get(sid, sid) for sid in pos_sid_set}
    # sec_info (dias de liquidação/NAV) das transações (capturado); resíduo de
    # ativos de posição sem transação → catálogo, aquecendo-o se frio (sem default 0).
    sec_info, _sec_warming = _resolve_sec_info(
        pos_sid_set | {t["securityId"] for t in all_txns_flat if t["securityId"]},
        txn_secinfo_hints)

    # ── Per-security current_secs + former_map (from unprocessed positions) ─────
    # The contribution figures feed the funnel's per-security attribution; the
    # headline gap itself comes from navPackage (below).
    current_secs = []
    former_map = {}
    for key, r in cur_rows.items():
        sid = r["securityId"]
        if not sid:
            continue  # unmapped → can't be diagnosed by securityId (alert instead)
        pu = r.get("pu")
        qty = r.get("quantity")
        bal = round(r["balance"], 2) if r.get("balance") is not None else (
            round(pu * qty, 2) if (pu is not None and qty is not None) else None)
        f = former_rows.get(key, {})
        f_pu, f_qty = f.get("pu"), f.get("quantity")
        f_bal = round(f["balance"], 2) if f.get("balance") is not None else (
            round(f_pu * f_qty, 2) if (f_pu is not None and f_qty is not None) else None)
        rec = _reconcile_security(pu, qty, bal, f_pu, f_qty, f_bal,
                                  txn_total_by_sid.get(sid), event_total_by_sid.get(sid, 0.0))
        current_secs.append({
            "securityId": sid, "pu": pu, "quantity": qty, "executionPrice": None,
            "totalContribution": rec["totalContribution"],
            "eventContribution": event_total_by_sid.get(sid, 0.0),
            "beehusName": sec_names.get(sid, sid),
        })
        former_map[sid] = {"pu": f_pu, "quantity": f_qty}

    # ── Provisions + Cash (de UMA resposta processed-position) ──────────────────
    # Caixa reusado na seção de caixa abaixo; provisões do bloco `provisions` do
    # envelope (== provisions_active). Sem posição processada na data → ambos vazios.
    #
    # Regra de negócio: as provisões "válidas para a data" são EXATAMENTE as do
    # envelope (ativas: initialDate <= data < liquidationDate) — fonte única.
    # `prov_lifecycle_sids` (que decide `cond_c` no motor: um ativo sem provisão
    # ativa na data pode ser eliminado) sai DAQUI também. Não fazemos mais o scan
    # `/beehus/provisions` desde 2000-01-01, que custava ~5-6s por chamada só para
    # detectar eventos de ciclo de vida na data (e não trazia as que liquidam
    # exatamente na data, fora do escopo do envelope).
    _cash_by_date, _prov_docs = beehus_catalog.wallet_cash_and_provisions(
        wallet_id, [former_date, date], company_id)
    prov_map = {}
    prov_lifecycle_sids = set()
    for p in _prov_docs:
        sid_p = str(p.get("securityId") or "")
        if sid_p:
            # `balance` é o campo da provisão (a API não tem `amount` — campo
            # legado). prov_map é usado como conjunto de pertinência
            # (`sid in prov_map`) no detector de órfãs.
            prov_map[sid_p] = prov_map.get(sid_p, 0) + (p.get("balance") or 0)
            prov_lifecycle_sids.add(sid_p)
    for cp in corr_provs:
        sid = str(cp.get("securityId", "") or "")
        bal = cp.get("balance") or 0
        init, liq = cp.get("initialDate"), cp.get("liquidationDate")
        # Mesma regra (ativa na data) para as correções pendentes.
        if sid and (init or "") <= date and (liq or "") > date:
            prov_map[sid] = prov_map.get(sid, 0) + float(bal)
            prov_lifecycle_sids.add(sid)

    # ── Cash + wallet-level NAV gap (from navPackage, NOT computed) ─────────────
    # Caixa reusado do mesmo envelope buscado na seção de provisões (sem 2ª chamada).
    cash = _cash_by_date
    former_cash = cash.get(former_date)
    current_cash = cash.get(date)
    return_nav_ps = nav_pkg.get("returnNavPerShare", 0) or 0
    return_contribution = nav_pkg.get("returnContribution", 0) or 0
    gap_pct = return_nav_ps - return_contribution
    gap_cash_r = round(gap_pct * former_nav, 2) if former_nav else 0.0

    # Recalculated gap after pending /correcoes (same helper as the original).
    # Reaproveita as provisões pendentes já carregadas acima (honra o try/except
    # daquele load e evita um segundo carregamento não-guardado dentro do helper;
    # mesma fonte que o /rows usa, mantendo o "Novo Gap %" consistente).
    recalc_gap_cash, recalc_gap_pct, corrections_impact_abs, corrections_count = \
        _recalc_gap_with_corrections(company_id, wallet_id, date, nav_pkg,
                                     former_date, former_nav, return_contribution,
                                     gap_cash_r, pending_provs=corr_provs)

    try:
        thresholds = _load_thresholds()
    except Exception:
        _log.exception("conciliacao-unprocessed: falha ao carregar rentability_thresholds")
        thresholds = {}

    ctx = {
        "wallet_id": wallet_id, "date": date, "former_date": former_date,
        "gap_pct": gap_pct, "gap_cash": gap_cash_r, "former_nav": former_nav,
        "return_nav_ps": return_nav_ps, "return_contrib": return_contribution,
        "corrections_count": corrections_count,
        "corrections_impact": round(corrections_impact_abs, 2),
        "recalc_gap_cash": recalc_gap_cash, "recalc_gap_pct": recalc_gap_pct,
        "current_secs": current_secs, "former_map": former_map,
        "all_txns_flat": all_txns_flat, "sec_info": sec_info,
        "prov_map": prov_map, "prov_lifecycle_sids": prov_lifecycle_sids,
        "former_cash": former_cash, "current_cash": current_cash,
        "history_returns": [],  # Step 6.1 (3-sigma) needs a NAV history we don't synthesise
        "thresholds": thresholds,
        # Extra (not consumed by the engine) — surfaced for the UI / recálculo:
        # company_id e nav_pkg ficam no ctx p/ o /diagnose recomputar NAV/gap a
        # partir das sugestões (item: novo NAV/cota/GAP) sem refazer chamadas.
        "company_id": company_id, "nav_pkg": nav_pkg,
        "_unmappedCount": sum(1 for r in cur_rows.values() if not r["securityId"]),
        # Posição da data ainda não pré-processada (nenhum ativo com securityId):
        # o diagnóstico não tem ativos p/ atribuir o gap; o front avisa em vez de
        # apresentar um veredito falsamente "limpo".
        "_notPreProcessed": bool(cur_rows) and not pos_sid_set,
        # True quando o catálogo está aquecendo p/ resolver sec_info de ativos de
        # posição sem transação — o front mostra spinner e refaz ao aquecer.
        "_catalogWarming": _sec_warming,
    }
    return ctx, None


def _approx_qty(a, b):
    """Quantity tolerance: 1e-6 absolute or 0.1% relative."""
    if a is None or b is None:
        return False
    return abs(a - b) <= max(1e-6, abs(b) * 0.001)


def _orphan_transactions(ctx):
    """Transactions whose securityId IS in the position but have NO matching
    amountDifference (considering the settlement/nav offset) and no active
    provision — i.e. cash moved with no corresponding position movement. The
    suggested correction is a provision spanning the offset window.

    A buySell of `balance` at `price` implies a quantity change of
    `-balance/price`. If the security's actual Δqty doesn't match that (and no
    provision already bridges it), the transaction is "órfã"."""
    date = ctx["date"]
    sec_by_id = {str(s.get("securityId", "")): s for s in ctx.get("current_secs", [])}
    former_map = ctx.get("former_map", {})
    sec_info = ctx.get("sec_info", {})
    prov_map = ctx.get("prov_map", {})

    txns_by_sid = {}
    for t in ctx.get("all_txns_flat", []):
        sid = str(t.get("securityId") or "")
        if sid and t.get("type") == "buySell":
            txns_by_sid.setdefault(sid, []).append(t)

    out = []
    for sid, txns in txns_by_sid.items():
        sec = sec_by_id.get(sid)
        if not sec:
            continue  # security NOT in position → WRONG_SECURITY, not an "órfã"
        if sid in prov_map:
            continue  # a provision already accounts for it
        qty = sec.get("quantity")
        f_qty = (former_map.get(sid) or {}).get("quantity")
        amt_diff = round((qty or 0) - (f_qty or 0), 6)
        txn_bal = round(sum(float(t.get("balance") or 0) for t in txns), 2)
        if abs(txn_bal) < 0.01:
            continue
        pu = sec.get("pu")
        price = sec.get("executionPrice") or pu or 0
        implied_qty = round(-txn_bal / price, 6) if price else None
        # Matched (not órfã) when the actual Δqty equals the txn-implied Δqty.
        if implied_qty is not None and _approx_qty(amt_diff, implied_qty):
            continue
        info = sec_info.get(sid, {})
        if txn_bal < 0:  # buy → subscription window
            settle = info.get("subscriptionSettlementDays") or 0
            nav = info.get("subscriptionNAVDays") or 0
        else:            # sell → redemption window
            settle = info.get("redemptionSettlementDays") or 0
            nav = info.get("redemptionNAVDays") or 0
        offset = settle - nav
        init_d, liq_d = _prov_dates(date, offset)
        out.append({
            "key": f"{sid}:ORPHAN_TXN",
            "securityId": sid,
            "securityName": sec.get("beehusName", sid),
            "flag": "MISSING_PROVISION",          # generated as a provision
            "txnBalance": txn_bal,
            "amountDiff": amt_diff,
            "impliedQty": implied_qty,
            "offset": offset,
            "impact": round(abs(txn_bal), 2),
            "provisionData": {"initialDate": init_d, "liquidationDate": liq_d,
                              "provisionType": "buySell", "balance": txn_bal,
                              "offset": offset},
            "detail": (f"Transação buySell de {_fmt_brl(txn_bal)} sem variação de "
                       f"quantidade correspondente (Δqtd={amt_diff}, implícito="
                       f"{implied_qty}). Sugerir provisão (offset={offset})."),
        })
    return out


_QTY_NO_TXN_DESC = "Mudança de quantidade sem transação"


def _build_suggestions(ctx, funnel):
    """Reduce the funnel output to the five actionable categories this page
    focuses on. Returns {provisionsQtyDiff, orphanTransactions, executionPrices,
    withholdingTax, cashMismatch}."""
    step3 = funnel.get("step3", {}) or {}
    step5 = funnel.get("step5", {}) or {}
    date = ctx.get("date")

    prov_qty, exec_prices, withholding = [], [], []
    for sec in step3.get("securities", []):
        sid = sec["securityId"]
        name = sec["name"]
        s1 = sec.get("step3_1") or {}
        if s1.get("status") == "flag" and s1.get("flag") == "MISSING_PROVISION":
            prov_qty.append({
                "key": f"{sid}:MISSING_PROVISION",
                "securityId": sid, "securityName": name, "flag": "MISSING_PROVISION",
                "amountDiff": sec.get("amountDiff"), "impact": s1.get("impact"),
                "offset": s1.get("offset"), "provisionData": s1.get("provisionData"),
                "detail": s1.get("detail"),
            })
        elif s1.get("status") == "flag" and s1.get("flag") == "MISSING_TRANSACTION":
            # offset 0 (liquidação imediata) sem buySell: por opção do usuário,
            # lançamos uma PROVISÃO buySell liquidando em D+1 útil em vez de uma
            # transação. _prov_dates(date, 0) já fixa a liquidação no próximo dia
            # útil (initialDate = data). O sinal segue a convenção das demais
            # provisões (compra → saída de caixa negativa; venda → entrada).
            amt = sec.get("amountDiff")
            impact = s1.get("impact") or 0
            # Exige sinal explícito de amt: sem ele não dá para decidir a direção
            # da provisão (compra → saída/negativo; venda → entrada/positivo).
            if amt and abs(impact) >= 0.01:
                init_d, liq_d = _prov_dates(date, 0)
                prov_balance = -impact if amt > 0 else impact
                prov_qty.append({
                    "key": f"{sid}:QTY_NO_TXN",
                    "securityId": sid, "securityName": name, "flag": "MISSING_PROVISION",
                    "amountDiff": amt, "impact": impact, "offset": 0,
                    "origemLabel": "Δ qtd (sem transação)",
                    "provisionData": {"initialDate": init_d, "liquidationDate": liq_d,
                                      "provisionType": "buySell", "balance": prov_balance,
                                      "offset": 0, "description": _QTY_NO_TXN_DESC},
                    "detail": f"{_QTY_NO_TXN_DESC} (liquidação imediata, offset 0).",
                })
        s3 = sec.get("step3_3") or {}
        if s3.get("status") == "flag" and s3.get("flag") == "MISSING_EXECUTION_PRICE":
            exec_prices.append({
                "key": f"{sid}:MISSING_EXECUTION_PRICE",
                "securityId": sid, "securityName": name, "flag": "MISSING_EXECUTION_PRICE",
                "pu": s3.get("pu"), "executionPrice": s3.get("executionPrice"),
                "expectedExecPrice": s3.get("expectedExecPrice"),
                "expectedValue": s3.get("expectedValue"), "actualBalance": s3.get("actualBalance"),
                "amountDiff": sec.get("amountDiff"), "impact": s3.get("impact"),
                "detail": s3.get("detail"),
            })
        elif s3.get("status") == "flag" and s3.get("flag") == "WITHHOLDING_TAX":
            withholding.append({
                "key": f"{sid}:WITHHOLDING_TAX",
                "securityId": sid, "securityName": name, "flag": "WITHHOLDING_TAX",
                "expectedValue": s3.get("expectedValue"), "actualBalance": s3.get("actualBalance"),
                "impact": s3.get("impact"), "detail": s3.get("detail"),
            })

    cash = {
        "status": step5.get("status"), "diagnosis": step5.get("diagnosis"),
        "cashDiff": step5.get("cashDiff"), "formerCash": step5.get("formerCash"),
        "currentCash": step5.get("currentCash"), "projectedCash": step5.get("projectedCash"),
        "totalTransactions": step5.get("totalTransactions"),
        "suspectTxns": step5.get("suspectTxns", []),
    }
    return {
        "provisionsQtyDiff": prov_qty,
        "orphanTransactions": _orphan_transactions(ctx),
        "executionPrices": exec_prices,
        "withholdingTax": withholding,
        "cashMismatch": cash,
    }


def _count_suggestions(suggestions):
    """Contagem por categoria acionável (4 colunas da grade).

    Provisões = Δquantidade + transações órfãs (ambas viram provisão no fluxo).
    Ajuste de caixa = 1 quando |cashDiff| ≥ 0.01 (uma provisão de ajuste), senão 0.
    """
    cash = suggestions.get("cashMismatch") or {}
    cash_diff = cash.get("cashDiff")
    cash_count = 1 if (cash_diff is not None and abs(cash_diff) >= 0.01) else 0
    return {
        "provisions":       len(suggestions.get("provisionsQtyDiff") or [])
                            + len(suggestions.get("orphanTransactions") or []),
        "executionPrices":  len(suggestions.get("executionPrices") or []),
        "withholdingTax":   len(suggestions.get("withholdingTax") or []),
        "cashAdjustments":  cash_count,
    }


def _recompute_with_suggestions(company_id, wallet_id, date, nav_pkg, former_date,
                                former_nav, return_contrib, gap_cash, suggestions):
    """Recalcula NAV / navPerShare / GAP aplicando as SUGESTÕES do diagnóstico —
    cálculo feito NESTE projeto (não no sistema origem).

    Mesma cadeia de fórmulas validada em `_recalc_gap_with_corrections`:
      • PROVISÕES (Δqtd + órfãs + ajuste de caixa) mudam o NAV do dia (e/ou do dia
        anterior, conforme a janela de atividade), recompondo navPerShare e o gap:
            nav_D'    = nav_D + Σ(provisões ativas em D)
            nps_D'    = (nav_D' − inAndOutFlows_D) / formerAmount_D
            retNav'   = nps_D' / nps_former' − 1
            gap%'     = retNav' − returnContribution ; gap$' = gap%' × formerNav'
      • IR RETIDO (taxes) + PREÇO DE EXECUÇÃO fecham o gap residual pela magnitude
        do impacto (mesma regra do helper compartilhado).

    Retorna {newNav, newNavPerShare, newGapCash, newGapPct}.
    """
    nav_pkg = nav_pkg or {}
    nav_T = nav_pkg.get("nav") or 0
    nps_T = _safe_num(nav_pkg.get("navPerShare"))

    # Provisões sugeridas (Δqtd + órfãs) + provisão de ajuste de caixa.
    provs = []
    for it in (suggestions.get("provisionsQtyDiff") or []):
        pd = it.get("provisionData") or {}
        provs.append({"balance": pd.get("balance") if pd.get("balance") is not None else (it.get("impact") or 0),
                      "initialDate": pd.get("initialDate"), "liquidationDate": pd.get("liquidationDate")})
    for it in (suggestions.get("orphanTransactions") or []):
        pd = it.get("provisionData") or {}
        provs.append({"balance": pd.get("balance") or 0,
                      "initialDate": pd.get("initialDate"), "liquidationDate": pd.get("liquidationDate")})
    cash = suggestions.get("cashMismatch") or {}
    cash_diff = cash.get("cashDiff")
    if cash_diff is not None and abs(cash_diff) >= 0.01:
        init_d, liq_d = _prov_dates(date, 0)
        provs.append({"balance": -cash_diff, "initialDate": init_d, "liquidationDate": liq_d})

    def _active_on(p, d):
        if not d:
            return False
        init = str(p.get("initialDate") or "")[:10]
        liq  = str(p.get("liquidationDate") or "")[:10]
        ds   = str(d)[:10]
        return bool(init) and bool(liq) and init <= ds < liq

    delta_nav_today  = sum(float(p["balance"] or 0) for p in provs if _active_on(p, date))
    delta_nav_former = sum(float(p["balance"] or 0) for p in provs if _active_on(p, former_date))

    # IR retido (taxes) + preço de execução: fecham o gap pela magnitude do impacto.
    irrf_impact = sum(abs(float(it.get("impact") or 0)) for it in (suggestions.get("withholdingTax") or []))
    exec_impact = sum(abs(float(it.get("impact") or 0)) for it in (suggestions.get("executionPrices") or []))

    new_nav_today = nav_T + delta_nav_today
    new_nps_today = nps_T
    recalc_gap_cash = gap_cash

    if delta_nav_today != 0 or delta_nav_former != 0:
        former_pkg = (beehus_catalog.nav_doc_for_entity_date(wallet_id, former_date, company_id)
                      if (former_date and company_id) else None)
        former_shares = (former_pkg or {}).get("formerAmount") or 0
        today_shares  = nav_pkg.get("formerAmount") or 0
        if former_pkg and former_shares and today_shares:
            inflow_F = (former_pkg or {}).get("inAndOutFlows") or 0
            inflow_T = nav_pkg.get("inAndOutFlows") or 0
            new_nav_former = (former_nav or 0) + delta_nav_former
            new_nps_former = (new_nav_former - inflow_F) / former_shares
            new_nps_today  = (new_nav_today - inflow_T) / today_shares
            if new_nps_former:
                new_return_nav_ps = (new_nps_today / new_nps_former) - 1
                new_gap_pct       = new_return_nav_ps - (return_contrib or 0)
                recalc_gap_cash   = new_gap_pct * new_nav_former

    close_amount = irrf_impact + exec_impact
    if recalc_gap_cash is not None:
        recalc_gap_cash = (recalc_gap_cash - close_amount) if recalc_gap_cash >= 0 else (recalc_gap_cash + close_amount)

    recalc_gap_cash = round(recalc_gap_cash, 2) if recalc_gap_cash is not None else None
    recalc_gap_pct  = (round(recalc_gap_cash / former_nav, 10)
                       if (recalc_gap_cash is not None and former_nav) else None)
    return {
        "newNav":          round(new_nav_today, 2),
        "newNavPerShare":  _safe_num(round(new_nps_today, 8)) if new_nps_today is not None else None,
        "newGapCash":      _safe_num(recalc_gap_cash),
        "newGapPct":       _safe_num(recalc_gap_pct),
    }


@bp.route("/api/conciliacao-unprocessed/diagnose")
def diagnose():
    """Focused diagnosis for the Não Processado page. Reuses the shared funnel
    to detect flags, then reduces them to the five actionable categories the
    modal presents: provisões por diferença de quantidade, provisões por
    transação órfã, preço de execução, IR retido na fonte e cash mismatch."""
    wallet_id = request.args.get("walletId", "")
    date = request.args.get("date", "")
    if not wallet_id or not date:
        return jsonify({"error": "walletId e date obrigatórios"}), 400
    ctx, err = _build_diagnose_ctx(wallet_id, date, request.args.get("companyId") or None)
    if err:
        return err
    funnel = run_funnel(ctx)
    step1 = funnel.get("step1", {}) or {}
    step7 = funnel.get("step7", {}) or {}
    suggestions = _build_suggestions(ctx, funnel)
    nav_pkg = ctx.get("nav_pkg") or {}

    # Contagem por categoria (4 colunas) + recálculo NAV/cota/GAP a partir das
    # sugestões — feito AQUI, neste projeto (não no sistema origem).
    counts = _count_suggestions(suggestions)
    recalc = _recompute_with_suggestions(
        ctx.get("company_id"), wallet_id, date, nav_pkg,
        ctx.get("former_date"), ctx.get("former_nav"), ctx.get("return_contrib"),
        ctx.get("gap_cash"), suggestions)

    out = {
        "walletId": wallet_id, "date": date,
        "gap": {
            "status": step1.get("status"),
            "gapPct": step1.get("gapPct"), "gapCash": step1.get("gapCash"),
            "formerNav": step1.get("formerNav"),
            "returnNavPerShare": step1.get("returnNavPerShare"),
            "returnContribution": step1.get("returnContribution"),
            # NAV/cota atuais (base do recálculo) — p/ a grade comparar com o "novo".
            "nav": _safe_num(nav_pkg.get("nav")),
            "navPerShare": _safe_num(nav_pkg.get("navPerShare")),
        },
        "verdict": step7.get("verdict"), "verdictDetail": step7.get("detail"),
        "suggestions": suggestions,
        "counts": counts,
        "recalc": recalc,
        "unmappedCount": ctx.get("_unmappedCount", 0),
        "notPreProcessed": ctx.get("_notPreProcessed", False),
        "catalogWarming": ctx.get("_catalogWarming", False),
        "note": ("Gap NAV via navPackage. Sugestões focadas em: provisões (Δqtd / "
                 "transação órfã), preço de execução, IR retido na fonte e caixa. "
                 "Novo NAV/cota/GAP recalculados neste projeto a partir das sugestões."),
    }
    return jsonify(_sanitize(out))


@bp.route("/api/conciliacao-unprocessed/transactions")
def get_transactions():
    """Flat list of all transactions liquidating on `date` for the wallet
    (display-only; the wallet-detail endpoint already embeds linked txns)."""
    wallet_id = request.args.get("walletId", "")
    date = request.args.get("date", "")
    if not wallet_id or not date:
        return jsonify({"transactions": []})
    wallet = resolve_wallet(wallet_id, {"companyId": 1},
                            company_id=request.args.get("companyId") or None)
    company_id = str(wallet["companyId"]) if wallet else ""
    if not company_id or not company_visible(company_id):
        return jsonify({"error": "acesso negado"}), 403

    docs = sorted(beehus_catalog.transactions_on_date(company_id, [wallet_id], date),
                  key=lambda t: str(t.get("operationDate") or ""))
    names = _txn_name_hints(docs)   # nome vem da própria transação (sem catálogo)
    txns = []
    for t in docs:
        sid = str(t.get("securityId") or "")
        txns.append({
            "txnId": str(t.get("_id", "") or ""),
            "operationDate": str(t.get("operationDate", "") or "")[:10],
            "liquidationDate": str(t.get("liquidationDate", "") or "")[:10],
            "securityId": sid,
            "securityName": names.get(sid, ""),
            "beehusTransactionType": t.get("beehusTransactionType", "") or "",
            "quantity": t.get("quantity"),
            "price": t.get("price"),
            "balance": t.get("balance"),
            "description": t.get("description", "") or "",
        })
    return jsonify(_sanitize({"transactions": txns, "date": date}))


@bp.route("/api/conciliacao-unprocessed/catalog-status")
def catalog_status():
    """`{warm: bool}` — se o catálogo de securities já está aquecido (uma leitura
    de nome não bloqueia ~20s). O front usa para mostrar/ocultar o spinner de
    aquecimento e refazer a chamada quando aquecer."""
    return jsonify({"warm": beehus_catalog.securities_index_is_warm()})
