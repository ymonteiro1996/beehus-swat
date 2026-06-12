"""Carteira (Posições) read-only viewer.

Renders a per-wallet table showing securities (rows) × dates (column
groups of qty / PU / saldo) for a chosen company, optionally filtered by
groupings and/or wallets and a date range. Below the per-security rows
each wallet's block carries three summary rows:

    1. Contribuição total — sum of `securities[].totalContribution` from
       `processedPosition` for that (walletId, positionDate).
    2. Provisões         — sum of `db.provisions.balance` whose
       `[initialDate, liquidationDate)` interval covers each business day.
    3. Caixa             — `cashAccounts.values[].value` totalled per
       wallet+date via the same helper used by `/caixa` and
       `/conciliacao`.

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
from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, jsonify, render_template, request
from openpyxl import Workbook

from beehus_api import (
    BeehusAPIError,
    BeehusAuthError,
    upload_unprocessed_security_positions_file,
)
from db import (
    company_visible,
    db,
    get_grouping_index,
    get_security_names,
    get_wallet_names,
    sum_cash_by_dates,
)

bp = Blueprint("carteira", __name__)

_SAFE_ID_RE   = re.compile(r"^[A-Za-z0-9_.\-]+$")
_SAFE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _safe(s):
    if not isinstance(s, str) or not _SAFE_ID_RE.match(s):
        raise ValueError(f"invalid id: {s!r}")
    return s


def _safe_date(d):
    if not isinstance(d, str) or not _SAFE_DATE_RE.match(d):
        raise ValueError(f"invalid date: {d!r}")
    return d


def _to_oid(v):
    try:
        return ObjectId(str(v))
    except (InvalidId, TypeError):
        return None


def _to_oids(values):
    out = []
    for v in values or []:
        oid = _to_oid(v)
        if oid is not None:
            out.append(oid)
    return out


def _biz_dates_range(initial_iso, final_iso):
    """Inclusive list of business days (Mon-Fri) from `initial_iso` to
    `final_iso`, oldest → newest. Both endpoints are validated by
    `_safe_date` before calling this. Returns ISO-formatted strings."""
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
        oids = _to_oids(wallet_ids)
        if not oids:
            return []
        return [
            str(w["_id"])
            for w in db.wallets.find(
                {"_id": {"$in": oids}, "companyId": company_id}, {"_id": 1}
            )
        ]

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
        oids = _to_oids(wanted)
        return [
            str(w["_id"])
            for w in db.wallets.find(
                {"_id": {"$in": oids}, "companyId": company_id}, {"_id": 1}
            )
        ]

    return [
        str(w["_id"])
        for w in db.wallets.find({"companyId": company_id}, {"_id": 1})
    ]


def _unprocessed_id_maps(company_id, wallet_ids, end_date):
    """Return `{walletId: {securityId: unprocessedId}}` for each wallet's
    most-recent `unprocessedSecurityPositions` snapshot ≤ `end_date`.

    Mirrors the lookup used by `pages/repetir_posicoes._unprocessed_id_map`
    so the carteira edit flow shares the same `securityId → unprocessedId`
    contract as the Repetir flow. One `find_one` per wallet (sorted desc
    by `positionDate`) — fine for the modest wallet counts on this page,
    and the alternative ($group with $max) would still need a second pass
    to pull the snapshot's `securities[]`.

    Resolution per security:
      - If the mapping's `from` is in the wallet's latest snapshot, use it.
        The snapshot disambiguates a `securityId` that carries more than
        one historical `from` label (upstream renames, yield revisions,
        dash-format drift — ~6% of mappings in practice).
      - Otherwise, if the `securityId` has exactly one `from` across the
        company mappings, fall back to it. This keeps a security editable
        when it aged out of the latest snapshot — most commonly when it
        matured on `end_date`: still present in that day's
        `processedPosition`, but already dropped from
        `unprocessedSecurityPositions` (the upstream stops emitting a
        matured asset on its maturity date). Without this fallback the row
        rendered "—" and the edit flow rejected it as "sem unprocessedId".
      - A `securityId` with several `from` labels and none in the snapshot
        stays unresolved (renders "—") — we never guess which `Ativo` to
        ship upstream.
    """
    if not company_id or not wallet_ids:
        return {}

    # securityMappings is per-company, so one find_one feeds every wallet
    # in the batch.
    mapping_doc = db.securityMappings.find_one(
        {"companyId": company_id}, {"mappings": 1}
    ) or {}

    # Company-level `securityId -> [from-uid, ...]`. Most securityIds map to a
    # single `from`; a handful carry several (the collisions the snapshot
    # below disambiguates). Built once, reused for every wallet in the batch.
    sid_to_uids = {}
    for m in (mapping_doc.get("mappings") or []):
        uid = m.get("from")
        sid = m.get("to")
        if uid and sid:
            sid_to_uids.setdefault(str(sid), []).append(uid)

    out = {}
    for wid in wallet_ids:
        query = {"walletId": wid}
        if end_date:
            query["positionDate"] = {"$lte": end_date}
        doc = db.unprocessedSecurityPositions.find_one(
            query,
            {"securities.unprocessedId": 1},
            sort=[("positionDate", -1)],
        )
        uids_in_doc = {
            s.get("unprocessedId")
            for s in ((doc or {}).get("securities") or [])
            if s.get("unprocessedId")
        }
        sid_to_uid = {}
        for sid, uids in sid_to_uids.items():
            in_snap = [u for u in uids if u in uids_in_doc]
            if in_snap:
                # Snapshot pins the uid actually held by this wallet — the
                # right disambiguator when a sid has multiple `from` labels.
                sid_to_uid[sid] = in_snap[-1]
            elif len(uids) == 1:
                # Unambiguous mapping, but the security aged out of the
                # latest snapshot (e.g. matured on `end_date`). Fall back to
                # the lone canonical uid so the row stays editable.
                sid_to_uid[sid] = uids[0]
            # else: multiple candidates AND none in the snapshot → genuinely
            # ambiguous; leave unresolved so we never ship a wrong `Ativo`.
        out[wid] = sid_to_uid
    return out


def _cash_unprocessed_ids(wallet_ids):
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
    out = {}
    for doc in db.cashAccounts.find(
        {"walletId": {"$in": list(wallet_ids)}, "trashed": {"$ne": True}},
        {"walletId": 1, "unprocessedId": 1},
    ):
        wid = str(doc.get("walletId") or "")
        uid = (doc.get("unprocessedId") or "").strip()
        if wid and uid and wid not in out:
            out[wid] = uid
    return out


def _provisions_by_wallet_date(company_id, wallet_ids, dates):
    """Sum `db.provisions.balance` per `(walletId, date)` for every date
    where the provision's `[initialDate, liquidationDate)` window covers
    that date. One scan over the overlap window, grouped in Python — the
    collection has no per-(wallet,date) index for the overlap query."""
    out = {}
    if not wallet_ids or not dates:
        return out
    ini = dates[0]
    fin = dates[-1]
    cursor = db.provisions.find(
        {
            "companyId":       company_id,
            "walletId":        {"$in": wallet_ids},
            "initialDate":     {"$lte": fin},
            "liquidationDate": {"$gte": ini},
            "trashed":         {"$ne": True},
        },
        {"walletId": 1, "balance": 1,
         "initialDate": 1, "liquidationDate": 1},
    )
    for d in cursor:
        wid = str(d.get("walletId") or "")
        if not wid:
            continue
        bal = d.get("balance")
        if bal is None:
            continue
        try:
            bal = float(bal)
        except (TypeError, ValueError):
            continue
        prov_ini = str(d.get("initialDate") or "")[:10]
        prov_liq = str(d.get("liquidationDate") or "")[:10]
        if not (prov_ini and prov_liq):
            continue
        for dt in dates:
            if prov_ini <= dt < prov_liq:
                out[(wid, dt)] = out.get((wid, dt), 0.0) + bal
    return out


@bp.route("/carteira")
def index():
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
    for w in db.wallets.find({"companyId": company_id}, {"name": 1}):
        wid = str(w["_id"])
        items.append({"id": wid, "name": w.get("name") or wallet_names.get(wid, wid)})
    items.sort(key=lambda x: (x["name"] or "").lower())
    return jsonify(items)


@bp.route("/api/carteira/data", methods=["POST"])
def get_data():
    """Build the per-wallet position matrix. See module docstring for the
    response shape. Performs three reads:
      - processedPosition for {walletId ∈ targets, positionDate ∈ range}
      - provisions overlapping the range, summed per (wallet, date)
      - cashAccounts via `sum_cash_by_dates` (one scan per wallet)
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

    # ── processedPosition (per-security qty / pu / contribution) ────────────
    # Keyed by (walletId, positionDate). `pos_map` holds the raw securities
    # array; per-date derived fields are computed below.
    pos_map = {}
    for doc in db.processedPosition.find(
        {"walletId": {"$in": wallet_ids}, "positionDate": {"$in": dates}},
        {"walletId": 1, "positionDate": 1, "securities": 1},
    ):
        wid = str(doc.get("walletId") or "")
        d   = str(doc.get("positionDate") or "")[:10]
        if wid and d:
            pos_map[(wid, d)] = doc.get("securities") or []

    prov_map = _provisions_by_wallet_date(company_id, wallet_ids, dates)
    cash_map = {}
    for wid in wallet_ids:
        # sum_cash_by_dates returns {date: total_or_None}
        for dt, val in sum_cash_by_dates(wid, dates).items():
            cash_map[(wid, dt)] = val

    # `securityId → unprocessedId` per wallet, anchored on the wallet's
    # most-recent `unprocessedSecurityPositions` snapshot ≤ `final`. Drives
    # the new "unprocessedSecurityId" column and the edit/apply flow.
    uid_maps = _unprocessed_id_maps(company_id, wallet_ids, final)

    # `walletId → cashAccount.unprocessedId` drives the Caixa row label and
    # the Ativo column of the cash line in the apply-flow xlsx.
    cash_uid_map = _cash_unprocessed_ids(wallet_ids)

    # ── Per-wallet assembly ─────────────────────────────────────────────────
    wallets_out = []
    for wid in wallet_ids:
        per_sec = {}  # sid → {date → {quantity, pu, balance, totalContribution}}
        contribution_by_date = {dt: 0.0 for dt in dates}
        has_contribution     = {dt: False for dt in dates}
        for dt in dates:
            for sec in pos_map.get((wid, dt), []):
                sid = str(sec.get("securityId") or "")
                if not sid:
                    continue
                pu  = sec.get("pu")
                qty = sec.get("quantity")
                bal = sec.get("balance")
                if bal is None and pu is not None and qty is not None:
                    try:
                        bal = round(float(pu) * float(qty), 6)
                    except (TypeError, ValueError):
                        bal = None
                tc = sec.get("totalContribution")
                cell = {
                    "quantity":          qty,
                    "pu":                pu,
                    "balance":           bal,
                    "totalContribution": tc,
                }
                per_sec.setdefault(sid, {})[dt] = cell
                if tc is not None:
                    try:
                        contribution_by_date[dt] += float(tc)
                        has_contribution[dt] = True
                    except (TypeError, ValueError):
                        pass

        # Securities list — sorted by display name, with a `quantityChanged`
        # flag that the front-end uses to colour the row when the quantity
        # of a security varies across the selected range (a heads-up that
        # the operator picked a window covering an actual movement).
        wallet_uid_map = uid_maps.get(wid) or {}
        securities_out = []
        for sid, by_date in per_sec.items():
            quantities = []
            for dt in dates:
                q = (by_date.get(dt) or {}).get("quantity")
                if q is not None:
                    try:
                        quantities.append(float(q))
                    except (TypeError, ValueError):
                        pass
            quantity_changed = (
                len({round(q, 6) for q in quantities}) > 1
                if len(quantities) >= 2 else False
            )
            securities_out.append({
                "securityId":      sid,
                "securityName":    sec_names.get(sid, "") or sid,
                "unprocessedId":   wallet_uid_map.get(sid, "") or "",
                "byDate":          by_date,
                "quantityChanged": quantity_changed,
            })
        securities_out.sort(
            key=lambda s: ((s["securityName"] or "").lower(), s["securityId"])
        )

        # Skip wallets that have nothing across the entire range — keeps
        # the result focused on wallets the operator can actually act on.
        any_data = (
            securities_out
            or any(has_contribution.values())
            or any(prov_map.get((wid, dt)) for dt in dates)
            or any(cash_map.get((wid, dt)) is not None for dt in dates)
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

    mapping_doc = db.securityMappings.find_one(
        {"companyId": company_id}, {"mappings": 1}
    ) or {}

    matched_uid = ""
    for m in (mapping_doc.get("mappings") or []):
        if str(m.get("to") or "") == security_id and m.get("from"):
            matched_uid = m["from"]
            break

    beehus_name = ""
    s_oid = _to_oid(security_id)
    if s_oid is not None:
        s = db.securities.find_one({"_id": s_oid}, {"beehusName": 1})
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

def _build_carteira_xlsx(*, target_date, wallet_id, rows, cash, currency_id,
                         cash_unprocessed_id="Caixa"):
    """Build the upstream-shaped workbook for a single (wallet, date).

    Schema (shared with `pages.excecoes._build_xlsx` and `pages.repetir_posicoes._build_combined_xlsx`):
        Data, Carteira, Ativo, Quant, PU, SaldoBruto, Caixa, Moeda

    The cash row's `Ativo` is the wallet's `cashAccounts.unprocessedId`
    (e.g. "Caixa" for BRL wallets, "Cash" for USD). Defaults to "Caixa"
    when the caller omits it, preserving the historical behaviour for
    wallets without a cashAccount on file.

    A cash row is appended only when the operator supplied a value — an
    empty cash field means "don't touch caixa", not "set caixa to zero"."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(["Data", "Carteira", "Ativo", "Quant", "PU",
               "SaldoBruto", "Caixa", "Moeda"])
    for r in rows:
        ws.append([
            target_date,
            wallet_id,
            r.get("ativo") or "",
            r.get("quantity") or 0,
            r.get("pu") or 0,
            r.get("balance") or 0,
            "Não",
            currency_id or "",
        ])
    if cash is not None:
        ws.append([
            target_date,
            wallet_id,
            (cash_unprocessed_id or "Caixa"),
            0,
            0,
            cash,
            "Sim",
            currency_id or "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _round(v, digits):
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
    w_doc = db.wallets.find_one(
        {"_id": w_oid}, {"companyId": 1, "currencyId": 1, "name": 1}
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

    xlsx = _build_carteira_xlsx(
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
