"""Carteira (Posições) read-only viewer.

Renders a per-wallet table showing securities (rows) × dates (column
groups of qty / PU / saldo) for a chosen company, optionally filtered by
groupings and/or wallets and a date range. Below the per-security rows
each wallet's block carries three summary rows:

    1. Contribuição total — sum of `securities[].totalContribution` from
       `processedPosition` for that (walletId, positionDate).
    2. Provisões         — sum of the processed-position envelope's
       `provisions[].balance` for each (walletId, positionDate). The envelope
       already carries exactly the provisions active on that date
       (`initialDate <= date < liquidationDate`), so no separate
       `/beehus/provisions` read is needed.
    3. Caixa             — `cashAccounts.values[].value` from the same
       processed-position envelope, totalled per wallet+date.

The wallet-resolution flow mirrors `_intraday_resolve_wallets` in
`pages/excecoes.py` and the favourites-bar filter endpoints under
`/api/beehus/filters/*` — operators can pick groupings, wallets, both,
or neither (which means "every wallet in the company").

Endpoints:
    /carteira                  GET  — render the filter page (used inside
                                       the /controlpanel tool-view iframe).
    /api/carteira/data         POST — `{companyId, initialDate, finalDate,
                                       groupingIds[], walletIds[]}` →
                                       `{wallets[], dates[]}`.
"""
# ─────────────────────────────────────────────────────────────────────────
# 1. IMPORTS E CONFIGURAÇÃO DO BLUEPRINT
# ─────────────────────────────────────────────────────────────────────────
from __future__ import annotations

from datetime import datetime, timedelta, date

from flask import Blueprint, jsonify, render_template, request

import beehus_catalog
from beehus_api import (
    BeehusAPIError,
    BeehusAuthError,
    upload_unprocessed_security_positions_file,
)
from db import (
    company_visible,
    get_grouping_index,
    get_security_names,
    get_wallet_names,
    resolve_wallet,
)
# Helpers genéricos reaproveitados de utils/ (regra do CLAUDE.md: comum -> utils/,
# reusar em vez de duplicar). A validação de id/data e a geração do .xlsx de
# posições agora vivem lá e são compartilhadas com outras telas.
from utils.validacao import safe_id, safe_date, to_object_id
from utils.planilhas import build_positions_xlsx

bp = Blueprint("carteira", __name__)

# Aliases finos para os utilitários de validação — mantêm os call-sites curtos
# (_safe / _safe_date / _to_oid) sem duplicar a lógica, que vive em utils/.
_safe      = safe_id
_safe_date = safe_date
_to_oid    = to_object_id


# ─────────────────────────────────────────────────────────────────────────
# 2. HELPERS / REGRAS DE NEGÓCIO (funções puras + montagem da matriz)
# ─────────────────────────────────────────────────────────────────────────


def _biz_dates_range(initial_iso, final_iso):
    """Contexto:
    Lista inclusiva de dias úteis (seg–sex) de `initial_iso` a `final_iso`, do
    mais antigo ao mais novo. Os extremos já vêm validados por safe_date. Retorna
    strings ISO — são as colunas de data da matriz de posições.

    Pseudocódigo:
      1. Converte as datas ISO de entrada em objetos date.
      2. Percorre dia a dia do início ao fim, acumulando os dias de semana.
      3. Retorna a lista de datas ISO acumuladas.
    """
    cur = date.fromisoformat(initial_iso)
    end = date.fromisoformat(final_iso)
    out = []
    while cur <= end:
        if cur.weekday() < 5:
            out.append(cur.strftime("%Y-%m-%d"))
        cur += timedelta(days=1)
    return out


def _resolve_wallets(company_id, grouping_ids, wallet_ids):
    """Resolve the operator's filter selections to a list of wallet id
    strings, always scoped by `companyId`. Mirrors the precedence used on
    the Identificar Transações page: explicit `walletIds` win, else union
    of `groupingIds`, else every wallet in the company.

    Defensive `companyId` re-check on the wallets `find` closes the
    cross-company leak a stale grouping cache could otherwise create."""
    if wallet_ids:
        wids = [beehus_catalog.id_str(w) for w in wallet_ids if w]
        if not wids:
            return []
        cmap = beehus_catalog.wallet_company_map(wids)
        return [wid for wid in wids if cmap.get(wid) == company_id]

    if grouping_ids:
        wanted = set()
        gindex = get_grouping_index()
        for gid in grouping_ids:
            g = gindex.get(gid)
            if not g or g.get("trashed"):
                continue
            if g.get("companyId") != company_id:
                continue
            wanted.update(g.get("walletIds", []))
        if not wanted:
            return []
        wids = [beehus_catalog.id_str(w) for w in wanted if w]
        if not wids:
            return []
        cmap = beehus_catalog.wallet_company_map(wids)
        return [wid for wid in wids if cmap.get(wid) == company_id]

    return list(beehus_catalog.wallets_for_company(company_id).keys())


def _cash_unprocessed_ids(wallet_ids, company_id=None, date=None):
    """Return `{walletId: unprocessedId}` from `cashAccounts` for each
    wallet — drives both the Caixa row label and the `Ativo` value of the
    cash line in the xlsx upload. Filters out `trashed=True` docs and
    keeps the first match per wallet (multiple non-trashed cashAccounts
    per wallet are rare; when present we pick deterministically by
    insertion order). Wallets without a cashAccount are absent from the
    return — callers fall back to the literal "Caixa" string the
    upstream parser used historically.
    """
    if not wallet_ids:
        return {}
    return beehus_catalog.cash_unprocessed_ids(wallet_ids, company_id, date)


# ─────────────────────────────────────────────────────────────────────────
# 3. ROTAS (@bp.route) — finas: validam a entrada, chamam helpers e respondem.
# ─────────────────────────────────────────────────────────────────────────


@bp.route("/carteira")
def index():
    """Contexto:
    Renderiza a página de filtros da Carteira (exibida dentro do iframe de
    ferramentas). Sem lógica de negócio — a tela é montada no cliente pelo
    static/js/carteira.js a partir das rotas /api/carteira/*.
    """
    return render_template("carteira.html")


@bp.route("/api/carteira/filters/companies")
def list_companies():
    """Mirror of /api/beehus/filters/companies — same `company_filter`
    settings.json gate so the dropdown matches the favourites-bar one."""
    from db import get_company_filter, get_company_names
    cf = get_company_filter()
    out = []
    for cid, name in get_company_names().items():
        if cf and cid not in cf:
            continue
        out.append({"id": cid, "name": name or cid})
    out.sort(key=lambda c: (c["name"] or "").lower())
    return jsonify(out)


@bp.route("/api/carteira/filters/groupings")
def list_groupings():
    company_id = (request.args.get("companyId") or "").strip()
    if not company_visible(company_id):
        return jsonify([])
    out = []
    for gid, g in get_grouping_index().items():
        if g.get("trashed"):
            continue
        if g.get("companyId") != company_id:
            continue
        out.append({
            "id":        gid,
            "name":      g.get("name") or gid,
            "walletIds": list(g.get("walletIds") or []),
        })
    out.sort(key=lambda x: (x["name"] or "").lower())
    return jsonify(out)


@bp.route("/api/carteira/filters/wallets")
def list_wallets():
    company_id = (request.args.get("companyId") or "").strip()
    if not company_visible(company_id):
        return jsonify([])
    wallet_names = get_wallet_names()
    items = []
    for wid, nm in beehus_catalog.wallets_for_company(company_id).items():
        items.append({"id": wid, "name": nm or wallet_names.get(wid, wid)})
    items.sort(key=lambda x: (x["name"] or "").lower())
    return jsonify(items)


@bp.route("/api/carteira/data", methods=["POST"])
def get_data():
    """Build the per-wallet position matrix. See module docstring for the
    response shape. The securities rows come from the RAW
    `unprocessed-security-positions` snapshot of each date (one batched read) so
    every line carries its own editable `unprocessedId`. The processed-position
    envelope (`carteira_position_bundle`) is still read per date for cash
    (balance + unprocessedId), the active provisions, and to enrich each line's
    `totalContribution` (+ the wallet's contribution total per date).
    """
    body = request.get_json(silent=True) or {}
    company_id = (body.get("companyId") or "").strip()
    initial    = (body.get("initialDate") or "").strip()
    final      = (body.get("finalDate") or initial).strip()
    grp_ids    = list(body.get("groupingIds") or [])
    wal_ids    = list(body.get("walletIds") or [])

    if not company_id or not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe_date(initial); _safe_date(final)
        for g in grp_ids: _safe(str(g))
        for w in wal_ids: _safe(str(w))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if initial > final:
        return jsonify({"error": "initialDate > finalDate"}), 400

    dates = _biz_dates_range(initial, final)
    if not dates:
        return jsonify({"wallets": [], "dates": []})

    wallet_ids = _resolve_wallets(company_id, grp_ids, wal_ids)
    if not wallet_ids:
        return jsonify({"wallets": [], "dates": dates})

    wallet_names = get_wallet_names()
    sec_names    = get_security_names()

    # ── processedPosition envelope (securities + caixa + provisões) ─────────
    # UMA chamada processed-position por data devolve, por carteira, o envelope
    # {position, provisions, cashAccounts}. `carteira_position_bundle` deriva
    # tudo dele de uma vez — eliminando os 3 fetches redundantes ao MESMO
    # endpoint (securities, caixa-saldo, caixa-unprocessedId) e a chamada
    # separada de /beehus/provisions (com piso 2000) que existiam antes.
    #   pos_map[(wid,date)]  = securities array (campos derivados abaixo)
    #   cash_map[(wid,date)] = saldo de caixa na data (ou None)
    #   cash_uid_map[wid]    = cashAccounts.unprocessedId (rótulo/Ativo da linha Caixa)
    #   prov_map[(wid,date)] = soma das provisões ATIVAS na data (do envelope)
    pos_map, cash_map, cash_uid_map, prov_map = (
        beehus_catalog.carteira_position_bundle(company_id, wallet_ids, dates))

    # Securities come from the RAW `unprocessed-security-positions` snapshot of
    # each date: every line carries its own `unprocessedId` ("Ativo" label) and is
    # editable/re-uploadable. We deliberately do NOT list the processed position
    # here — it adds look-through / explosion components created during processing
    # that have NO raw upload label, which previously rendered with a blank
    # `unprocessedId` and couldn't be edited. `pos_map` (processed) is kept only to
    # ENRICH each line's `totalContribution` and to compute the wallet's accurate
    # contribution total per date (the opt-in contribution column + footer).
    raw_docs = beehus_catalog.unprocessed_docs_map(company_id, wallet_ids, dates)

    # ── Per-wallet assembly ─────────────────────────────────────────────────
    wallets_out = []
    for wid in wallet_ids:
        # Processed totalContribution: per-(date, securityId) for enrichment, plus
        # the wallet's full per-date total (footer "Contribuição total") — summed
        # over the WHOLE processed position so the total stays accurate even though
        # the rows shown are the raw lines.
        proc_tc = {}
        contribution_by_date = {dt: 0.0 for dt in dates}
        has_contribution     = {dt: False for dt in dates}
        for dt in dates:
            tcs = {}
            for sec in pos_map.get((wid, dt), []):
                tc = sec.get("totalContribution")
                psid = beehus_catalog.id_str(sec.get("securityId")) if sec.get("securityId") is not None else ""
                if psid and tc is not None:
                    tcs[psid] = tc
                if tc is not None:
                    try:
                        contribution_by_date[dt] += float(tc)
                        has_contribution[dt] = True
                    except (TypeError, ValueError):
                        pass
            proc_tc[dt] = tcs

        # One display row per `unprocessedId` (the raw upload line). securityId /
        # name come from `preProcessingData`; a raw line not yet mapped to a
        # securityId still shows (keyed by its unprocessedId) using its beehusName.
        per_uid = {}
        raw_dates_with_secs = set()   # datas cujo snapshot BRUTO trouxe ≥1 ativo utilizável
        for dt in dates:
            doc = raw_docs.get((wid, dt))
            if not doc:
                continue
            for s in (doc.get("securities") or []):
                if not isinstance(s, dict):
                    continue
                uid = (s.get("unprocessedId") or "").strip()
                if not uid:
                    continue
                ppd = s.get("preProcessingData") or {}
                sid = beehus_catalog.id_str(ppd.get("securityId")) if ppd.get("securityId") is not None else ""
                pu  = s.get("pu")
                qty = s.get("quantity")
                bal = s.get("balance")
                if bal is None and pu is not None and qty is not None:
                    try:
                        bal = round(float(pu) * float(qty), 6)
                    except (TypeError, ValueError):
                        bal = None
                tc = proc_tc.get(dt, {}).get(sid) if sid else None
                entry = per_uid.setdefault(uid, {
                    "securityId":    sid,
                    "securityName":  (sec_names.get(sid) if sid else "") or (ppd.get("beehusName") or "") or sid or uid,
                    "unprocessedId": uid,
                    "byDate":        {},
                })
                # Backfill sid/name once a snapshot reveals the mapping for a line
                # that first appeared unmapped.
                if not entry["securityId"] and sid:
                    entry["securityId"]   = sid
                    entry["securityName"] = sec_names.get(sid) or (ppd.get("beehusName") or "") or sid
                entry["byDate"][dt] = {
                    "quantity":          qty,
                    "pu":                pu,
                    "balance":           bal,
                    "totalContribution": tc,
                }
                raw_dates_with_secs.add(dt)

        # `quantityChanged` flags rows whose quantity varies across the selected
        # range (the front-end colours them — a heads-up that the window covers an
        # actual movement).
        securities_out = []
        for uid, entry in per_uid.items():
            quantities = []
            for dt in dates:
                q = (entry["byDate"].get(dt) or {}).get("quantity")
                if q is not None:
                    try:
                        quantities.append(float(q))
                    except (TypeError, ValueError):
                        pass
            entry["quantityChanged"] = (
                len({round(q, 6) for q in quantities}) > 1
                if len(quantities) >= 2 else False
            )
            securities_out.append(entry)
        securities_out.sort(
            key=lambda s: ((s["securityName"] or "").lower(), s["securityId"])
        )

        # Erro no arquivo BRUTO por data: a carteira TEM posição PROCESSADA na data
        # (logo, deveria ter posição), mas o snapshot `unprocessed-security-positions`
        # daquela data veio SEM ativos (vazio) ou está AUSENTE. Não fazemos fallback —
        # apenas sinalizamos o erro do arquivo e não exibimos ativos (decisão de produto:
        # "informar erro no arquivo unprocessed e não fazer nada"). Caso real: carteira
        # MML PF BTG BRL em 2026-05-29 (bruto 0 ativos, processada com 12).
        unproc_error_by_date = {
            dt: (bool(pos_map.get((wid, dt))) and dt not in raw_dates_with_secs)
            for dt in dates
        }

        # Skip wallets that have nothing across the entire range — keeps
        # the result focused on wallets the operator can actually act on.
        any_data = (
            securities_out
            or any(has_contribution.values())
            or any(prov_map.get((wid, dt)) for dt in dates)
            or any(cash_map.get((wid, dt)) is not None for dt in dates)
            or any(unproc_error_by_date.values())
        )
        if not any_data:
            continue

        wallets_out.append({
            "walletId":   wid,
            "walletName": wallet_names.get(wid, "") or wid,
            "securities": securities_out,
            "totalContributionByDate": {
                dt: (contribution_by_date[dt] if has_contribution[dt] else None)
                for dt in dates
            },
            "provisionsByDate":   {dt: prov_map.get((wid, dt), 0.0) for dt in dates},
            "cashByDate":         {dt: cash_map.get((wid, dt)) for dt in dates},
            "cashUnprocessedId":  cash_uid_map.get(wid, ""),
            "unprocessedErrorByDate": unproc_error_by_date,
        })

    wallets_out.sort(key=lambda w: (w["walletName"] or "").lower())

    # Drop columns (dates) where NO wallet has a real processedPosition
    # entry — provisions/cash on their own don't count. Without this gate
    # weekend-adjacent runs (or wallets that only carry a cash balance)
    # would leave empty position columns next to "Provisões"/"Caixa" rows.
    non_empty = set()
    for w in wallets_out:
        for sec in w["securities"]:
            non_empty.update(sec["byDate"].keys())
        for dt, v in w["totalContributionByDate"].items():
            if v is not None:
                non_empty.add(dt)
        # Uma data com erro de arquivo bruto NÃO tem ativos nem, necessariamente,
        # contribuição — mas precisa sobreviver ao corte de colunas p/ o aviso aparecer.
        for dt, err in w["unprocessedErrorByDate"].items():
            if err:
                non_empty.add(dt)
    dates_kept = [d for d in dates if d in non_empty]

    if len(dates_kept) != len(dates):
        kept_set = set(dates_kept)
        for w in wallets_out:
            w["totalContributionByDate"] = {
                d: w["totalContributionByDate"].get(d) for d in dates_kept
            }
            w["provisionsByDate"] = {
                d: w["provisionsByDate"].get(d, 0.0) for d in dates_kept
            }
            w["cashByDate"] = {
                d: w["cashByDate"].get(d) for d in dates_kept
            }
            w["unprocessedErrorByDate"] = {
                d: w["unprocessedErrorByDate"].get(d, False) for d in dates_kept
            }
            for sec in w["securities"]:
                sec["byDate"] = {
                    d: v for d, v in sec["byDate"].items() if d in kept_set
                }

    return jsonify({
        "wallets": wallets_out,
        "dates":   dates_kept,
    })


# ── Mapping lookup (used by the inline-edit security search modal) ────────────

@bp.route("/api/carteira/lookup-mapping")
def lookup_mapping():
    """Resolve a `securityId → unprocessedId` for the inline-edit modal.

    When the operator picks a security in the search modal we need the
    corresponding upstream identifier. The lookup walks
    `securityMappings.mappings[]` for the given company, matching by
    `to == securityId` and returning `from`. When no mapping exists we
    surface the security's `beehusName` so the operator at least gets a
    human-readable label that the upstream parser accepts as a label-style
    `Ativo`.

    Query params:
      companyId   required
      securityId  required

    Returns: `{unprocessedId, beehusName, source}` where `source` is one
    of `"mapping"` (canonical match), `"beehusName"` (fallback), or `""`
    (security cadastro missing — caller should treat as a hard error)."""
    company_id  = (request.args.get("companyId") or "").strip()
    security_id = (request.args.get("securityId") or "").strip()
    if not company_id or not security_id:
        return jsonify({"error": "companyId and securityId required"}), 400
    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403

    mapping_doc = beehus_catalog.security_mappings_doc(company_id) or {}

    matched_uid = ""
    for m in (mapping_doc.get("mappings") or []):
        if str(m.get("to") or "") == security_id and m.get("from"):
            matched_uid = m["from"]
            break

    beehus_name = ""
    s_oid = _to_oid(security_id)
    if s_oid is not None:
        s = beehus_catalog.security_doc(s_oid)
        if s:
            beehus_name = s.get("beehusName") or ""

    if matched_uid:
        return jsonify({
            "unprocessedId": matched_uid,
            "beehusName":    beehus_name,
            "source":        "mapping",
        })
    if beehus_name:
        return jsonify({
            "unprocessedId": beehus_name,
            "beehusName":    beehus_name,
            "source":        "beehusName",
        })
    # No mapping AND no security cadastro → caller will surface a friendly
    # error; we don't ship an empty `Ativo` upstream.
    return jsonify({
        "unprocessedId": "",
        "beehusName":    "",
        "source":        "",
    })


# ── Security search (proxy onto the controlpanel cache) ───────────────────────

@bp.route("/api/carteira/search-securities")
def search_securities():
    """Free-text search over the in-memory securities cache.

    Thin proxy onto the controlpanel's `_get_matcher()` cache so the
    carteira edit modal doesn't duplicate cache-loading logic. The
    arguments and response shape mirror /api/controlpanel/search-securities
    so the same JS modal helper can drive either page."""
    from pages.controlpanel import search_securities as cp_search
    return cp_search()


# ── Apply: upload edited positions as a new unprocessedSecurityPositions ──────
# O workbook de posições é gerado por utils.planilhas.build_positions_xlsx
# (schema Data/Carteira/Ativo/Quant/PU/SaldoBruto/Caixa/Moeda), compartilhado
# com as telas Exceções e Repetir Posições.

def _round(v, digits):
    """Contexto:
    Arredonda `v` para `digits` casas, tolerando entrada inválida (retorna 0 em
    erro). Usado ao normalizar qtd/pu/saldo antes de montar o .xlsx de envio.

    Pseudocódigo:
      1. Tenta round(float(v), digits).
      2. Em TypeError/ValueError, retorna 0.
    """
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return 0


@bp.route("/api/carteira/apply", methods=["POST"])
def apply_edits():
    """Upload the operator-edited positions as a fresh
    `unprocessedSecurityPositions` snapshot for `(walletId, targetDate)`.

    Body:
        {
          "companyId":         str,
          "walletId":          str,
          "targetDate":        "YYYY-MM-DD",
          "currencyId":        str (optional; falls back to wallets.currencyId or "BRL"),
          "securities":       [{"unprocessedId", "quantity", "pu"}, ...],
          "cash":              number | null,
          "cashUnprocessedId": str (optional; falls back to wallets.cashAccounts.unprocessedId or "Caixa")
        }

    Each security row's `Ativo` field is the operator-resolved
    `unprocessedId` from the lookup-mapping endpoint (canonical from
    `securityMappings.mappings[].from` or, when no mapping exists, the
    security's `beehusName`). The cash row's `Ativo` is
    `cashUnprocessedId` (sourced from `cashAccounts.unprocessedId` on
    the page, editable by the operator). The upstream multipart
    endpoint splits cash from security rows via the `Caixa` column.
    """
    body = request.get_json(silent=True) or {}
    company_id  = (body.get("companyId") or "").strip()
    wallet_id   = (body.get("walletId") or "").strip()
    target_date = (body.get("targetDate") or "").strip()
    currency_id = (body.get("currencyId") or "").strip()
    securities  = body.get("securities") or []
    cash_raw    = body.get("cash")
    cash_uid    = (body.get("cashUnprocessedId") or "").strip()

    if not company_id or not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe(wallet_id); _safe_date(target_date)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Wallet ↔ company guard: prevents an operator with cross-company
    # visibility from re-targeting a wallet to a sibling company.
    w_oid = _to_oid(wallet_id)
    if w_oid is None:
        return jsonify({"error": "invalid walletId"}), 400
    w_doc = resolve_wallet(
        wallet_id, {"companyId": 1, "currencyId": 1, "name": 1},
        company_id=company_id,
    )
    if not w_doc:
        return jsonify({"error": "wallet not found"}), 404
    if str(w_doc.get("companyId") or "") != company_id:
        return jsonify({"error": "wallet does not belong to companyId"}), 400
    if not currency_id:
        currency_id = str(w_doc.get("currencyId") or "BRL")

    rows_xlsx = []
    seen_ativos = set()
    for sec in securities:
        if not isinstance(sec, dict):
            return jsonify({"error": "invalid security row"}), 400
        ativo = (sec.get("unprocessedId") or "").strip()
        if not ativo:
            return jsonify({"error": "unprocessedId/ativo é obrigatório em cada linha"}), 400
        # The upstream parser keys positions by `Ativo` within a (Data,
        # Carteira) — duplicates would silently collapse one row.
        if ativo in seen_ativos:
            return jsonify({"error": f"Ativo duplicado: {ativo}"}), 400
        seen_ativos.add(ativo)
        try:
            qty = float(sec.get("quantity") or 0)
            pu  = float(sec.get("pu") or 0)
        except (TypeError, ValueError):
            return jsonify({"error": f"qtd/pu inválidos para {ativo}"}), 400
        balance = _round(qty * pu, 2)
        rows_xlsx.append({
            "ativo":    ativo,
            "quantity": _round(qty, 8),
            "pu":       _round(pu, 8),
            "balance":  balance,
        })

    cash_value = None
    if cash_raw is not None and cash_raw != "":
        try:
            cash_value = _round(float(cash_raw), 2)
        except (TypeError, ValueError):
            return jsonify({"error": "valor de caixa inválido"}), 400

    if not rows_xlsx and cash_value is None:
        return jsonify({"error": "nada a enviar — inclua securities ou um valor de caixa"}), 400

    # When the operator omits/clears `cashUnprocessedId`, fall back to the
    # wallet's existing cashAccount label (so a stray reset doesn't silently
    # rewrite "Cash" to "Caixa" upstream). If no cashAccount exists either,
    # `_build_carteira_xlsx` defaults to the literal "Caixa".
    if cash_value is not None and not cash_uid:
        cash_uid = _cash_unprocessed_ids([wallet_id]).get(wallet_id, "")

    xlsx = build_positions_xlsx(
        target_date=target_date,
        wallet_id=wallet_id,
        rows=rows_xlsx,
        cash=cash_value,
        currency_id=currency_id,
        cash_unprocessed_id=cash_uid or "Caixa",
    )
    filename = (
        f"carteira_{company_id}_{wallet_id}_{target_date}_"
        f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.xlsx"
    )
    try:
        upstream = upload_unprocessed_security_positions_file(
            company_id=company_id,
            file_bytes=xlsx,
            filename=filename,
        )
    except BeehusAuthError as e:
        return jsonify({
            "error":           str(e),
            "upstream_status": getattr(e, "status", None),
            "upstream_body":   getattr(e, "body", None),
        }), 401
    except BeehusAPIError as e:
        return jsonify({
            "error":           str(e),
            "upstream_status": getattr(e, "status", None),
            "upstream_body":   getattr(e, "body", None),
        }), 502

    return jsonify({
        "ok":         True,
        "filename":   filename,
        "rows":       len(rows_xlsx),
        "cashSent":   cash_value is not None,
        "upstream":   upstream if upstream is not None else {},
    })
