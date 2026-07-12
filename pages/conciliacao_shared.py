"""Helpers de conciliação compartilhados (extraídos de `pages/conciliacao.py`).

Apenas o que `conciliacao_unprocessed` consome — sem MongoDB e sem o blueprint
da conciliação original. Dados de NAV vêm da API via `beehus_catalog`; limiares
e config vêm de JSON em disco; correções pendentes vêm de `correcoes_store`.
"""
import json
import os

import beehus_catalog
from pages.correcoes_store import (load_corrections_for_wallet,
                                   load_all_pending_provisions,
                                   load_pending_execution_prices)

_THRESHOLDS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "rentability_thresholds.json")
_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "conciliacao_config.json")
_DEFAULT_DIFF_THRESHOLD_PCT = 0.01  # 0.01% = 1 basis point


def _load_conciliacao_config():
    """Config editável (diffThresholdPct). Cai nos defaults se ausente/corrompido."""
    defaults = {"diffThresholdPct": _DEFAULT_DIFF_THRESHOLD_PCT}
    if not os.path.exists(_CONFIG_FILE):
        return defaults
    try:
        with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return {**defaults, **data}
    except Exception:
        return defaults


def _diff_threshold_decimal(request_obj=None):
    """Limiar |returnNavPerShare - returnContribution| em decimal.
    Prioridade: ?threshold=<pct> → config → default. Unidade da config/UI é
    percentual (0.01 = 0.01%); aqui devolvemos decimal."""
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
    return max(0.0, pct / 100.0)


def _load_thresholds():
    """Limiares de rentabilidade por ativo (data/rentability_thresholds.json)."""
    if not os.path.exists(_THRESHOLDS_FILE):
        return {}
    with open(_THRESHOLDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _mismatch_query(company_id, valid_wallet_ids, date=None, threshold=0.0):
    """Constrói o filtro (estilo Mongo) de navPackages com
    |returnNavPerShare - returnContribution| > threshold. Mantido por
    compatibilidade da assinatura importada — é só montagem de dict (não
    executa nada). `threshold` em decimal."""
    q = {"companyId": company_id, "trashed": {"$ne": True}}
    if valid_wallet_ids is not None:
        q["walletId"] = {"$in": list(valid_wallet_ids)}
    else:
        q["walletId"] = {"$nin": [None, ""]}
    if threshold and threshold > 0:
        q["$expr"] = {"$gt": [
            {"$abs": {"$subtract": [
                {"$ifNull": ["$returnNavPerShare", 0]},
                {"$ifNull": ["$returnContribution", 0]},
            ]}},
            float(threshold),
        ]}
    else:
        q["$expr"] = {"$ne": ["$returnNavPerShare", "$returnContribution"]}
    if date is not None:
        q["positionDate"] = {"$in": date} if isinstance(date, list) else date
    return q


def _find_former_nav(wallet_id, date, company_id=None):
    """navPackage não-trashed imediatamente anterior a `date` (via API).
    Retorna (former_date_str|None, former_nav_float|None). `company_id` evita o
    fan-out global de wallets (resolução da empresa via `_company_of_wallet`)."""
    return beehus_catalog.nav_former_for_entity(wallet_id, date, company_id)


def _exec_price_impact(row):
    """Impacto de caixa (abs) de uma correção de executionPrice aceita:
    amountDiff × (priorPrice − newPrice). 0.0 se algum campo faltar/for inválido."""
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
    """Recalcula o gap do dia considerando correções pendentes (provisões que
    afetam o NAV de hoje/anterior; transações e preços de execução pela regra
    de fechar o gap). Retorna (recalc_gap_cash, recalc_gap_pct, impact_abs,
    corrections_count). Idêntico ao da conciliação original — fontes via API/disco."""
    if not company_id or gap_cash is None:
        recalc_gap_pct = (gap_cash / former_nav) if (gap_cash is not None and former_nav) else None
        return gap_cash, recalc_gap_pct, 0, 0

    txns, _, dels = load_corrections_for_wallet(company_id, date, wallet_id)

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

    uniq_provs = {}
    for p in list(today_provs) + list(former_provs):
        pid = p.get("id") or id(p)
        uniq_provs[pid] = p

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

    delta_nav_today  = sum(float(p.get("balance") or 0) for p in today_provs)
    delta_nav_former = sum(float(p.get("balance") or 0) for p in former_provs)

    recalc_gap_cash = gap_cash
    if delta_nav_today != 0 or delta_nav_former != 0:
        former_pkg = beehus_catalog.nav_doc_for_entity_date(
            wallet_id, former_date, company_id) if former_date else None

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
