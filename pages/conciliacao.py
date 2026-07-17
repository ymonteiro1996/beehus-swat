from flask import Blueprint, render_template, jsonify, request
from db import (get_biz_dates, get_company_filter, valid_wallet_ids, atomic_write_json,
                atomic_write_text, company_visible, get_company_names, get_wallet_names,
                resolve_wallet)
from datetime import date as _date, timedelta, datetime as _dt, timezone
import json, math, os, re, statistics, subprocess, shutil, sys
import logging

import beehus_catalog

# Pending corrections stored by the Correções page are injected into the
# diagnostic pipeline so gaps, flags and listings reflect the "post-correction"
# view without touching the database.
from pages.correcoes import (load_corrections_for_wallet, append_rows_for_wallet,
                             load_active_pending_provisions,
                             load_all_pending_provisions,
                             load_all_pending_provisions_by_wallet,
                             load_pending_execution_prices,
                             _iter_wallet_files)
from beehus_api import (delete_transaction as _api_delete_transaction,
                        update_transaction as _api_update_transaction,
                        delete_provision as _api_delete_provision,
                        update_provision as _api_update_provision,
                        calculate_nav_wallets as _api_calculate_nav_wallets,
                        list_transactions as _api_list_transactions,
                        list_provisions as _api_list_provisions,
                        BeehusAPIError, BeehusAuthError)


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
from pages.bayesian import (extract_factors, compute_summary,
                            optimize_with_validation, _load_config as _load_bayesian_config)

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

_NUM_DATES = 10


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/conciliacao")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("conciliacao.html", companies=companies)


@bp.route("/api/conciliacao/dates")
def get_dates():
    company_id = request.args.get("companyId", "")
    end_date   = request.args.get("endDate") or None
    if not company_visible(company_id):
        return jsonify({"cards": []})
    threshold = _diff_threshold_decimal(request)

    pkgs = beehus_catalog.nav_packages(company_id)
    if not end_date:
        dates_in_pkgs = [str(p.get("positionDate") or "")[:10]
                         for p in pkgs if p.get("positionDate")]
        if dates_in_pkgs:
            end_date = max(dates_in_pkgs)

    dates = get_biz_dates(_NUM_DATES, end_date)
    dates_set = set(dates)

    totals = {}
    for pkg in pkgs:
        pd = str(pkg.get("positionDate") or "")[:10]
        if pd not in dates_set:
            continue
        wid = str(pkg.get("walletId") or "")
        if not wid:
            continue
        rnps = pkg.get("returnNavPerShare")
        rc   = pkg.get("returnContribution")
        is_gap = (
            (abs((rnps or 0) - (rc or 0)) > threshold)
            if threshold > 0
            else (rnps != rc)
        )
        if is_gap:
            totals[pd] = totals.get(pd, 0) + 1

    cards = [{"date": d, "total": totals.get(d, 0)} for d in dates]
    return jsonify({"cards": cards})


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


@bp.route("/api/conciliacao/rows")
def get_rows():
    try:
        company_id = request.args.get("companyId", "")
        date       = request.args.get("date", "")
        if not company_visible(company_id):
            return jsonify({"rows": [], "dates": []})
        threshold = _diff_threshold_decimal(request)

        # All navPackages for the company (cached by beehus_catalog)
        all_pkgs = beehus_catalog.nav_packages(company_id)

        # Build former_map: most recent navPackage strictly before `date` per wallet
        # Also build current_pkgs_map: current day navPackage per wallet
        former_map   = {}  # {walletId: {"nav": float, "date": str, "pkg": dict}}
        current_pkgs = {}  # {walletId: pkg}
        for pkg in all_pkgs:
            pos_date = str(pkg.get("positionDate") or "")[:10]
            wid = str(pkg.get("walletId") or "")
            if not pos_date or not wid:
                continue
            if pos_date == date:
                current_pkgs[wid] = pkg
            elif pos_date < date:
                if wid not in former_map or pos_date > former_map[wid]["date"]:
                    former_map[wid] = {
                        "nav":  pkg.get("nav"),
                        "date": pos_date,
                        "pkg":  pkg,
                    }

        # Pre-load all pending provisions for this company in a single tree
        # walk, keyed by wallet — avoids re-scanning the correcoes store
        # inside the per-row loop below.
        _all_pending_provs = load_all_pending_provisions_by_wallet(company_id)
        wallet_names_map = dict(get_wallet_names())

        rows = []
        for wid, pkg in current_pkgs.items():
            rnps = _safe_num(pkg.get("returnNavPerShare"))
            rc   = _safe_num(pkg.get("returnContribution"))
            is_gap = (
                (abs((rnps or 0) - (rc or 0)) > threshold)
                if threshold > 0
                else (rnps != rc)
            )
            if not is_gap:
                continue
            former = former_map.get(wid)
            if not former or former.get("nav") is None:
                continue

            former_nav  = _safe_num(former["nav"])
            gap_pct     = (
                (rnps - rc)
                if (rnps is not None and rc is not None)
                else None
            )
            gap_cash    = (
                (gap_pct * former_nav)
                if (gap_pct is not None and former_nav)
                else None
            )

            new_gap_cash, new_gap_pct, _impact_abs, corrections_count = (
                _recalc_gap_with_corrections(
                    company_id, wid, date, pkg,
                    former["date"], former_nav, rc, gap_cash,
                    pending_provs=_all_pending_provs.get(wid, []),
                )
                if gap_cash is not None
                else (gap_cash, gap_pct, 0, 0)
            )

            rows.append({
                "walletId":           wid,
                "walletName":         wallet_names_map.get(wid, wid),
                "nav":                _safe_num(pkg.get("nav")),
                "navPerShare":        _safe_num(pkg.get("navPerShare")),
                "amount":             _safe_num(pkg.get("amount")),
                "inAndOutFlows":      _safe_num(pkg.get("inAndOutFlows")),
                "returnNavPerShare":  rnps,
                "returnContribution": rc,
                "formerNav":          former_nav,
                "formerDate":         former["date"],
                "newGapPct":          new_gap_pct,
                "newGapCash":         new_gap_cash,
                "correctionsCount":   corrections_count,
            })

        rows.sort(key=lambda x: x["walletName"])
        return jsonify({"rows": rows, "date": date})
    except Exception:
        import traceback
        traceback.print_exc()
        logging.getLogger(__name__).exception("conciliacao /rows failed")
        return jsonify({"error": "falha ao processar"}), 500


@bp.route("/api/conciliacao/wallet-detail")
def get_wallet_detail():
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")
    company_id, err = _require_visible_wallet(wallet_id)
    if err: return err

    # Current position via processed envelope (position + provisions + cash)
    cur_env = beehus_catalog.processed_envelope(wallet_id, date, company_id)
    cur_pos = (cur_env or {}).get("position") or {}
    current_pos_secs = cur_pos.get("securities") or []

    # Former date comes from the most recent untrashed navPackage for this wallet.
    former_date, _ = beehus_catalog.nav_former_for_entity(
        wallet_id, date, company_id)

    # Fetch processed envelope at former_date for former_map
    former_map = {}
    if former_date:
        fmr_env = beehus_catalog.processed_envelope(
            wallet_id, former_date, company_id)
        fmr_secs = ((fmr_env or {}).get("position") or {}).get("securities") or []
        for sec in fmr_secs:
            sid = str(sec.get("securityId", ""))
            former_map[sid] = {
                "pu":       sec.get("pu"),
                "quantity": sec.get("quantity"),
            }

    # ── Pending deletion markers (MISCLASSIFIED corrections) ────────────────────
    _company_for_corr = company_id
    _c_txns, _c_provs, _c_dels = load_corrections_for_wallet(
        _company_for_corr, date, wallet_id)
    _del_id_strs = [
        str(d.get("originalId")) for d in _c_dels if d.get("originalId")]
    _del_id_set = set(_del_id_strs)

    # ── Build position-side lookup indices for transaction linkage ─────────────
    _pos_sid_set  = set()
    _pos_by_name  = {}      # {beehusName.lower(): str(securityId)}
    for sec in current_pos_secs:
        _sid = str(sec.get("securityId", "") or "")
        if _sid:
            _pos_sid_set.add(_sid)
        _bn = (sec.get("beehusName") or "").strip().lower()
        if _bn and _sid and _bn not in _pos_by_name:
            _pos_by_name[_bn] = _sid

    # Fetch transactions via beehus_catalog; filter out deleted ones
    _all_txn_docs = beehus_catalog.transactions_on_date(
        company_id, [wallet_id], date)
    _txn_docs = [
        t for t in _all_txn_docs
        if t.get("balance") is not None
        and str(t.get("_id", "") or "") not in _del_id_set
    ]

    # Build name lookup from transactions (securityBeehusName field)
    _sec_name_by_id = {}
    for t in _txn_docs:
        sid_str = str(t.get("securityId") or "")
        nm = t.get("securityBeehusName") or t.get("securityName") or ""
        if sid_str and nm and sid_str not in _sec_name_by_id:
            _sec_name_by_id[sid_str] = nm

    def _resolve_txn_to_pos_sid(raw_sid_str, raw_name):
        """Map a transaction's (securityId, securityName) onto a position's
        securityId. Returns ('', '') when no linkage is possible."""
        if raw_sid_str and raw_sid_str in _pos_sid_set:
            return raw_sid_str, _sec_name_by_id.get(raw_sid_str, raw_name or "")
        # Fallback: name match against current-position securities. Used when
        # txns carry a different ObjectId than the position (e.g. duplicate
        # security docs, stale ingestion id) but the name aligns.
        nm = (raw_name or _sec_name_by_id.get(raw_sid_str, "") or "").strip().lower()
        if nm and nm in _pos_by_name:
            pos_sid = _pos_by_name[nm]
            return pos_sid, _sec_name_by_id.get(raw_sid_str, raw_name or "")
        return "", _sec_name_by_id.get(raw_sid_str, raw_name or "")

    # ── Transaction aggregation per security (liquidationDate == date) ─────────
    # In addition to the running totals, we materialize a per-security list of
    # linked transactions so the frontend can render them inline in the asset
    # listing (the user-facing "linkar a transação ao ativo" requirement).
    txn_by_security       = {}   # {securityId: total_balance}
    event_txn_by_security = {}   # {securityId: total event balance (amortization/coupon)}
    txns_by_security      = {}   # {securityId: [ {txnId, type, balance, ...}, ... ]}
    unmatched_txns        = []   # txns whose security can't be resolved to a position row

    for t in _txn_docs:
        raw_sid_str = str(t.get("securityId") or "")
        bal_raw = t.get("balance")
        try:
            bal = float(bal_raw) if bal_raw is not None else 0.0
        except (TypeError, ValueError):
            bal = 0.0
        typ = t.get("beehusTransactionType", "") or ""
        resolved_sid, sec_nm = _resolve_txn_to_pos_sid(raw_sid_str, t.get("securityName"))
        key_sid = resolved_sid or raw_sid_str
        txn_by_security[key_sid] = txn_by_security.get(key_sid, 0) + bal
        if typ in _EVENT_TYPES:
            event_txn_by_security[key_sid] = event_txn_by_security.get(key_sid, 0) + bal

        entry = {
            "txnId":                 str(t.get("_id", "") or ""),
            "operationDate":         str(t.get("operationDate", "")   or "")[:10],
            "liquidationDate":       str(t.get("liquidationDate", "") or "")[:10],
            "securityId":            raw_sid_str,
            "securityName":          sec_nm,
            "beehusTransactionType": typ,
            "balance":               bal_raw,
            "quantity":              t.get("quantity"),
            "price":                 t.get("price"),
            "description":           t.get("description", "") or "",
            "isPending":             False,
            "isEvent":               typ in _EVENT_TYPES,
            "linkedBy":              ("securityId" if resolved_sid and raw_sid_str in _pos_sid_set
                                       else "name" if resolved_sid
                                       else "unmatched"),
        }
        if resolved_sid:
            txns_by_security.setdefault(resolved_sid, []).append(entry)
        else:
            unmatched_txns.append(entry)

    # Include pending correction transactions (stored in /correcoes, not yet
    # ingested) in the per-security listing so the asset row reflects what the
    # user has already accepted via the Aceitar/+Transação/+Provisão buttons.
    for ct in _c_txns:
        raw_sid_str = str(ct.get("securityId") or "")
        bal_raw = ct.get("balance")
        typ = ct.get("beehusTransactionType", "") or ""
        resolved_sid, sec_nm = _resolve_txn_to_pos_sid(raw_sid_str, ct.get("securityName"))
        entry = {
            "txnId":                 "",
            "correctionId":          ct.get("id", ""),
            "operationDate":         str(ct.get("operationDate", "")   or "")[:10],
            "liquidationDate":       str(ct.get("liquidationDate", "") or "")[:10],
            "securityId":            raw_sid_str,
            "securityName":          sec_nm or ct.get("securityName", ""),
            "beehusTransactionType": typ,
            "balance":               bal_raw,
            "quantity":              None,
            "price":                 None,
            "description":           ct.get("description", "") or "",
            "isPending":             True,
            "isEvent":               typ in _EVENT_TYPES,
            "linkedBy":              ("securityId" if resolved_sid and raw_sid_str in _pos_sid_set
                                       else "name" if resolved_sid
                                       else "unmatched"),
        }
        if resolved_sid:
            txns_by_security.setdefault(resolved_sid, []).append(entry)
        else:
            unmatched_txns.append(entry)

    # Order each security's transactions by operationDate (asc) so the UI lists
    # them chronologically.
    for _lst in txns_by_security.values():
        _lst.sort(key=lambda e: (e.get("operationDate") or "", e.get("liquidationDate") or ""))

    securities = []
    for sec in current_pos_secs:
        sid = str(sec.get("securityId", ""))
        pu  = sec.get("pu")
        qty = sec.get("quantity")
        balance = round(pu * qty, 6) if (pu is not None and qty is not None) else None

        total_contrib = sec.get("totalContribution")
        event_contrib = sec.get("eventContribution") or 0

        f      = former_map.get(sid, {})
        f_pu   = f.get("pu")
        f_qty  = f.get("quantity")
        f_bal  = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None
        amt_diff = round(qty - (f_qty or 0), 6) if qty is not None else None

        try:
            return_pu = round(pu / f_pu - 1, 8) if (pu is not None and f_pu and f_pu != 0) else None
        except (TypeError, ZeroDivisionError):
            return_pu = None

        try:
            return_contrib = round(total_contrib / f_bal, 8) if (total_contrib is not None and f_bal and f_bal != 0) else None
        except (TypeError, ZeroDivisionError):
            return_contrib = None

        diff_rent = round(return_pu - return_contrib, 8) if (return_pu is not None and return_contrib is not None) else None

        # Correct returnPU when event transactions (coupon/amortization) explain the diff
        ev_corrected = False
        if diff_rent and f_bal:
            ev_total = event_txn_by_security.get(sid)
            if ev_total is not None:
                expected_event_cash = round(-diff_rent * f_bal, 2)
                if _approx(round(ev_total, 2), expected_event_cash):
                    return_pu = round((pu + ev_total / f_qty) / f_pu - 1, 8)
                    diff_rent = round(return_pu - return_contrib, 8)
                    ev_corrected = True

        txn_bal = txn_by_security.get(sid)   # None if no transactions for this security
        if ev_corrected and txn_bal is not None:
            txn_bal = round(txn_bal - event_txn_by_security[sid], 2) or None

        # Linked transactions for this asset (DB + pending corrections), already
        # resolved upstream via _resolve_txn_to_pos_sid (securityId first, name
        # fallback). The list is chronological and includes both ingested and
        # pending rows so the asset listing can show the user-facing "linkage".
        linked_txns = list(txns_by_security.get(sid, []))
        txn_count = len(linked_txns)
        linked_by_name_count = sum(1 for e in linked_txns if e.get("linkedBy") == "name")

        securities.append({
            "securityId":        sid,
            "beehusName":        sec.get("beehusName", ""),
            "pricingType":       sec.get("pricingType", ""),
            "pu":                pu,
            "executionPrice":    sec.get("executionPrice"),
            "quantity":          qty,
            "balance":           balance,
            "totalContribution": total_contrib,
            "formerPu":          f_pu,
            "formerQuantity":    f_qty,
            "formerBalance":     f_bal,
            "amountDifference":  amt_diff,
            "returnPU":          return_pu,
            "returnContrib":     return_contrib,
            "diffRent":          diff_rent,
            "transactionBalance": txn_bal,
            "transactions":         linked_txns,
            "transactionCount":     txn_count,
            "transactionsByNameCount": linked_by_name_count,
            "dailyContribution":    sec.get("dailyContribution"),
            "intradayContribution": sec.get("intradayContribution"),
            "eventContribution":    event_contrib,
        })

    securities.sort(key=lambda s: s["beehusName"])

    current_sids = {s["securityId"] for s in securities}
    unmatched_txn_total = sum(
        bal for sid, bal in txn_by_security.items() if sid not in current_sids
    ) or None
    matched_txn_total = sum(
        bal for sid, bal in txn_by_security.items() if sid in current_sids
    ) or None
    # Sort unmatched txns so the UI can display them under a single
    # "Transações sem ativo" group at the bottom of the listing.
    unmatched_txns.sort(key=lambda e: (e.get("operationDate") or "", e.get("liquidationDate") or ""))

    # ── Cash accounts ──────────────────────────────────────────────────────────
    # Read cash from the processed envelope (already fetched above) plus
    # the former date's envelope — one call covers both.
    _cash_by_date, _ = beehus_catalog.wallet_cash_and_provisions(
        wallet_id, [former_date, date], company_id)
    former_cash  = _cash_by_date.get(former_date)
    current_cash = _cash_by_date.get(date)

    # Total transactions = sum of all per-security transaction balances
    total_txns = sum(txn_by_security.values())

    projected_cash = former_cash + total_txns if former_cash is not None else None
    cash_diff = (
        projected_cash - current_cash
        if projected_cash is not None and current_cash is not None
        else None
    )

    # ── Alerts ─────────────────────────────────────────────────────────────────
    alerts = []

    # Alert 1: transactions with unidentified type
    if any(
        t.get("beehusTransactionType") is None
        for t in _txn_docs
    ):
        alerts.append({"id": "unidentified_txns", "message": "Existem transações não identificadas"})

    # Alert 2: projected cash ≠ current cash
    if projected_cash is not None and current_cash is not None:
        if round(projected_cash - current_cash, 2) != 0:
            alerts.append({"id": "cash_mismatch", "message": "Há uma divergência no caixa"})

    # Pending corrections (for the recalculated-gap pill in the wallet view).
    # `_c_txns`, `_c_provs`, `_c_dels` were loaded at the top of this function.
    # ExecutionPrice corrections are pulled here too — they're stored in a
    # cross-folder bucket and only count when (a) targeting this date and
    # (b) not yet pushed upstream (`inputed=False`). Otherwise the gap-recalc
    # pills on the wallet-detail screen would silently ignore MEP corrections,
    # showing the original gap as if Aceitar had no effect.
    _c_exec_active = [
        r for r in load_pending_execution_prices(_company_for_corr, wallet_id)
        if (r.get("positionDate") == date) and not r.get("inputed")
    ]
    exec_impact_abs = sum(_exec_price_impact(r) for r in _c_exec_active)
    corrections_impact_abs = sum(
        abs(float(r.get("balance") or 0))
        for r in list(_c_txns) + list(_c_provs)
    ) + exec_impact_abs
    corrections_count = (len(_c_txns) + len(_c_provs) + len(_c_dels)
                         + len(_c_exec_active))

    return jsonify(_sanitize({
        "securities":                securities,
        "formerDate":                former_date,
        "date":                      date,
        "formerCash":                former_cash,
        "totalTransactions":         total_txns,
        "projectedCash":             projected_cash,
        "currentCash":               current_cash,
        "cashDifference":            cash_diff,
        "unmatchedTransactions":     unmatched_txn_total,
        "matchedTransactions":       matched_txn_total,
        # Full list of transactions that couldn't be linked to any position row
        # (no matching securityId AND no matching beehusName). Surfaced so the
        # UI can show them as an "órfãs" group at the bottom of the listing.
        "unmatchedTransactionsList": unmatched_txns,
        "alerts":                    alerts,
        "correctionsCount":          corrections_count,
        "correctionsImpact":         round(corrections_impact_abs, 2),
    }))


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


def _find_wallet_txn(wallet_id, txn_id):
    """Return a minimal dict for `txn_id` IF it belongs to `wallet_id`, using
    the Beehus API. Returns None when the txn doesn't exist or belongs to
    another wallet — closing the door on editing/deleting by guessing an id."""
    if not txn_id or not wallet_id:
        return None
    company_id = _wallet_company(wallet_id)
    if not company_id:
        return None
    try:
        # Search a wide window; we only need to confirm ownership.
        txns = _api_list_transactions(
            company_id=company_id,
            initial_date="2000-01-01",
            final_date="2099-12-31",
            wallet_ids=[wallet_id],
        )
    except Exception:
        return None
    for t in txns:
        tid = str(t.get("_id") or "")
        wid = str(
            (t.get("walletId") or {}).get("_id", "")
            if isinstance(t.get("walletId"), dict)
            else t.get("walletId", "")
        )
        if tid == txn_id and wid == wallet_id:
            return {"_id": tid, "walletId": wid}
    return None


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


def _find_wallet_provision(wallet_id, prov_id):
    """Return a minimal dict for `prov_id` IF it belongs to `wallet_id`, using
    the Beehus API. Returns None when the provision doesn't exist or belongs to
    another wallet."""
    if not prov_id or not wallet_id:
        return None
    company_id = _wallet_company(wallet_id)
    if not company_id:
        return None
    try:
        provs = _api_list_provisions(
            company_id=company_id,
            initial_date="2000-01-01",
            final_date="2099-12-31",
            wallet_id=wallet_id,
        )
    except Exception:
        return None
    for prov in provs:
        pid = str(prov.get("_id") or "")
        wid = str(
            (prov.get("walletId") or {}).get("_id", "")
            if isinstance(prov.get("walletId"), dict)
            else prov.get("walletId", "")
        )
        if pid == prov_id and wid == wallet_id:
            return {"_id": pid, "walletId": wid}
    return None


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


@bp.route("/api/conciliacao/calculate-nav", methods=["POST"])
def calculate_wallet_nav():
    """Trigger upstream NAV-contribution recalculation for one wallet+date via
    `POST /beehus/consolidation/nav-contribution-calculation/wallets`
    (`beehus_api.calculate_nav_wallets`).

    Body: `{walletId, date}`. The company is resolved from the wallet
    (`_require_visible_wallet`), so the caller can't recalc a wallet outside
    its visible company. The upstream call can take a while (wide timeout)."""
    data      = request.get_json() or {}
    wallet_id = data.get("walletId", "")
    date      = data.get("date", "")

    company_id, err = _require_visible_wallet(wallet_id)
    if err:
        return err
    if not date:
        return jsonify({"error": "date obrigatório"}), 400

    try:
        result = _api_calculate_nav_wallets(
            company_id=company_id, position_date=date, wallets=[wallet_id],
        )
    except (BeehusAuthError, BeehusAPIError) as e:
        return _beehus_error_response(e)
    return jsonify(_sanitize({"ok": True, "walletId": wallet_id, "date": date, "upstream": result}))


@bp.route("/api/conciliacao/transactions")
def get_transactions():
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")
    company_id, err = _require_visible_wallet(wallet_id)
    if err: return err

    corr_txns, _, corr_dels = load_corrections_for_wallet(
        company_id, date, wallet_id)
    _del_by_id = {
        str(d.get("originalId")): d
        for d in corr_dels if d.get("originalId")
    }

    # Fetch transactions via catalog; securityBeehusName already populated.
    raw_docs = beehus_catalog.transactions_on_date(
        company_id, [wallet_id], date)
    txn_docs = sorted(raw_docs, key=lambda t: str(t.get("operationDate") or ""))

    # Build security name map from the transaction docs themselves.
    security_names = {}
    for txn in txn_docs:
        sid = str(txn.get("securityId") or "")
        nm  = txn.get("securityBeehusName") or txn.get("securityName") or ""
        if sid and nm and sid not in security_names:
            security_names[sid] = nm
    for ct in corr_txns:
        sid = str(ct.get("securityId") or "")
        nm  = ct.get("securityName") or ""
        if sid and nm and sid not in security_names:
            security_names[sid] = nm

    txns = []
    for txn in txn_docs:
        sid    = str(txn.get("securityId", "") or "")
        txn_id = str(txn.get("_id", "") or "")
        del_entry = _del_by_id.get(txn_id)
        txns.append({
            "txnId":                 txn_id,
            "operationDate":         str(txn.get("operationDate",   "") or "")[:10],
            "liquidationDate":       str(txn.get("liquidationDate", "") or "")[:10],
            "securityId":            sid,
            "securityName":          security_names.get(sid, ""),
            "beehusTransactionType": txn.get("beehusTransactionType", ""),
            "quantity":              txn.get("quantity"),
            "price":                 txn.get("price"),
            "balance":               txn.get("balance"),
            "description":           txn.get("description", ""),
            "isPending":             False,
            "pendingDeletion":       del_entry is not None,
            "deletionReason":        (del_entry or {}).get("reason", ""),
        })

    for ct in corr_txns:
        sid = str(ct.get("securityId", "") or "")
        txns.append({
            "operationDate":         str(ct.get("operationDate",   "") or "")[:10],
            "liquidationDate":       str(ct.get("liquidationDate", "") or "")[:10],
            "securityId":            sid,
            "securityName":          security_names.get(sid, ""),
            "beehusTransactionType": ct.get("beehusTransactionType", ""),
            "quantity":              None,
            "price":                 None,
            "balance":               ct.get("balance"),
            "description":           ct.get("description", ""),
            "isPending":             True,
            "correctionId":          ct.get("id"),
        })

    return jsonify(_sanitize({"transactions": txns, "date": date}))


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


@bp.route("/api/conciliacao/diagnose")
def diagnose():
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")
    _, err = _require_visible_wallet(wallet_id)
    if err: return err

    # ── Data loading ────────────────────────────────────────────────────────────
    company_id = _wallet_company(wallet_id)

    # NAV package via catalog
    nav_pkg = beehus_catalog.nav_doc_for_entity_date(wallet_id, date, company_id)
    if not nav_pkg:
        return jsonify({"error": "navPackage não encontrado"}), 404

    return_nav_ps  = nav_pkg.get("returnNavPerShare", 0) or 0
    return_contrib = nav_pkg.get("returnContribution", 0) or 0

    former_date, former_nav = _find_former_nav(wallet_id, date, company_id)

    if former_date is None or former_nav is None:
        return jsonify({
            "error": "Carteira sem navPackage anterior — fora do escopo de análise.",
            "walletId": wallet_id, "date": date,
        }), 404

    gap_pct  = return_nav_ps - return_contrib
    gap_cash = round(gap_pct * former_nav, 2) if former_nav is not None else None

    # ── Recalculated gap after applying pending corrections ─────────────────────
    recalc_gap_cash, recalc_gap_pct, corrections_impact_abs, corrections_count = \
        _recalc_gap_with_corrections(company_id, wallet_id, date, nav_pkg,
                                     former_date, former_nav, return_contrib, gap_cash)

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 1 — Detect
    # ═══════════════════════════════════════════════════════════════════════════
    step1 = {
        "status":             "gap" if abs(gap_pct) > 1e-10 else "ok",
        "returnNavPerShare":  return_nav_ps,
        "returnContribution": return_contrib,
        "gapPct":             gap_pct,
        "gapCash":            gap_cash,
        "formerNav":          former_nav,
        "correctionsCount":   corrections_count,
        "correctionsImpact":  round(corrections_impact_abs, 2),
        "recalculatedGapCash": recalc_gap_cash,
        "recalculatedGapPct":  recalc_gap_pct,
    }

    _skipped = {"status": "skipped"}
    if step1["status"] == "ok":
        step7_no_gap = {
            "status":    "ok",
            "verdict":   "NO_GAP",
            "detail":    "Sem divergência detectada.",
            "formerNav": former_nav,
            "formerDate": None,
            "gapCash":   gap_cash,
            "gapPct":    gap_pct,
            "signals": {
                "step3HasFlags":                  False,
                "step4HasIssues":                 False,
                "step5Consistent":                True,
                "step6WalletAnomaly":             False,
                "allSuspectsMissingContribution": False,
            },
        }
        return jsonify(_sanitize({
            "walletId": wallet_id, "date": date,
            "step1": step1, "step2": _skipped, "step3": _skipped,
            "step4": _skipped, "step5": _skipped, "step6": _skipped,
            "step7": step7_no_gap,
        }))

    # ── Processed positions via catalog ─────────────────────────────────────────
    cur_env = beehus_catalog.processed_envelope(wallet_id, date, company_id)
    cur_secs = ((cur_env or {}).get("position") or {}).get("securities") or []

    fmr_env = beehus_catalog.processed_envelope(
        wallet_id, former_date, company_id) if former_date else None
    fmr_secs = ((fmr_env or {}).get("position") or {}).get("securities") or []
    former_map = {
        str(s.get("securityId", "")): {
            "pu": s.get("pu"), "quantity": s.get("quantity")}
        for s in fmr_secs
    }

    # ── Pending corrections ──────────────────────────────────────────────────────
    _corr_txns, _, _corr_dels = load_corrections_for_wallet(
        company_id, date, wallet_id)
    _corr_provs = load_all_pending_provisions(company_id, wallet_id)
    _deletion_ids = {
        str(d.get("originalId")) for d in _corr_dels if d.get("originalId")}

    # ── Transactions grouped by securityId ──────────────────────────────────────
    txns_by_security = {}
    wallet_txns      = []
    all_txns_flat    = []
    for doc in beehus_catalog.transactions_on_date(company_id, [wallet_id], date):
        txn_id = str(doc.get("_id", "") or "")
        if txn_id and txn_id in _deletion_ids:
            continue
        entry = {
            "type":       doc.get("beehusTransactionType"),
            "balance":    doc.get("balance"),
            "securityId": str(doc.get("securityId", "") or ""),
            "txnId":      txn_id,
        }
        all_txns_flat.append(entry)
        sid = entry["securityId"]
        if sid:
            txns_by_security.setdefault(sid, []).append(entry)
        else:
            wallet_txns.append(entry)

    for corr_txn in _corr_txns:
        entry = {
            "type":       corr_txn.get("beehusTransactionType"),
            "balance":    corr_txn.get("balance"),
            "securityId": str(corr_txn.get("securityId", "") or ""),
            "pending":    True,
        }
        all_txns_flat.append(entry)
        sid = entry["securityId"]
        if sid:
            txns_by_security.setdefault(sid, []).append(entry)
        else:
            wallet_txns.append(entry)

    # ── Security info (settlement days + securityType) via catalog ───────────────
    current_secs    = cur_secs
    current_sec_ids = {str(s.get("securityId", "")) for s in current_secs}

    all_sec_ids_raw = (
        {str(s.get("securityId", "")) for s in current_secs if s.get("securityId")}
        | set(txns_by_security.keys())
    )
    all_sec_ids_raw.discard("")

    if all_sec_ids_raw and beehus_catalog.securities_index_is_warm():
        sec_info = {
            str(d["_id"]): d
            for d in beehus_catalog.securities_by_ids(
                list(all_sec_ids_raw)).values()
        }
    else:
        if all_sec_ids_raw:
            beehus_catalog.warm_securities_index_async()
        sec_info = {}

    # ── Active provisions + lifecycle sids via processed envelope ────────────────
    # The processed-position envelope already contains exactly the provisions
    # active on `date` (initialDate <= date < liquidationDate).
    _prov_docs = (cur_env or {}).get("provisions") or []
    prov_map = {}
    prov_lifecycle_sids = set()
    for pdoc in _prov_docs:
        sid_p = str(pdoc.get("securityId") or "")
        if not sid_p:
            continue
        bal = pdoc.get("balance") or 0
        prov_map[sid_p] = prov_map.get(sid_p, 0) + float(bal)
        prov_lifecycle_sids.add(sid_p)

    # Inject pending correction provisions.
    for cp in _corr_provs:
        sid = str(cp.get("securityId", "") or "")
        bal = cp.get("balance") or 0
        init = cp.get("initialDate")
        liq  = cp.get("liquidationDate")
        # Active provision spans (init <= date < liq) — add to prov_map.
        if sid and (init or "") <= date and (liq or "") > date:
            prov_map[sid] = prov_map.get(sid, 0) + float(bal)
        # Lifecycle event on this date (init == date or liq == date).
        if sid and (init == date or liq == date):
            prov_lifecycle_sids.add(sid)

    # ── Event transactions by security (for Step 3.2) ───────────────────────────
    event_txns_by_sec = {}   # {securityId: [{"type", "balance"}]}
    for t in all_txns_flat:
        if t["type"] in _EVENT_TYPES and t["securityId"]:
            event_txns_by_sec.setdefault(t["securityId"], []).append(t)

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 2 — Eliminate
    # ═══════════════════════════════════════════════════════════════════════════
    eliminated = []
    suspects   = []

    for sec in current_secs:
        sid  = str(sec.get("securityId", ""))
        pu   = sec.get("pu")
        qty  = sec.get("quantity")
        f    = former_map.get(sid, {})
        f_pu = f.get("pu")
        f_qty = f.get("quantity")
        f_bal = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None
        amt_diff = round(qty - (f_qty or 0), 6) if qty is not None else None

        exec_price = sec.get("executionPrice")
        event_c    = sec.get("eventContribution") or 0
        total_c    = sec.get("totalContribution")

        # Compute rentab PU and rentab Contribution
        try:
            ret_pu = round(pu / f_pu - 1, 8) if (pu and f_pu) else None
        except (TypeError, ZeroDivisionError):
            ret_pu = None
        try:
            ret_c = round(total_c / f_bal, 8) if (total_c is not None and f_bal) else None
        except (TypeError, ZeroDivisionError):
            ret_c = None
        diff_rent = round(ret_pu - ret_c, 8) if (ret_pu is not None and ret_c is not None) else None

        sec_txns = txns_by_security.get(sid, [])

        # Elimination conditions (ALL must be true)
        cond_a = amt_diff is not None and amt_diff == 0     # no quantity change
        cond_b = not sec_txns                               # no transactions
        cond_c = sid not in prov_lifecycle_sids              # no provision lifecycle event
        cond_d = diff_rent is not None and diff_rent == 0   # rentab equal

        # If rentab differs, check if event txns explain it (coupon/amortization)
        if not cond_d and diff_rent and f_bal:
            ev_txns = event_txns_by_sec.get(sid, [])
            if ev_txns:
                ev_total = round(sum(float(t.get("balance", 0) or 0) for t in ev_txns), 2)
                expected_event_cash = round(-diff_rent * f_bal, 2)
                if _approx(ev_total, expected_event_cash):
                    cond_d = True   # explained by event transactions
                    # Exclude event txns from cond_b check — they are accounted for
                    non_event_txns = [t for t in sec_txns if t["type"] not in _EVENT_TYPES]
                    cond_b = not non_event_txns

        sec_entry = {
            "securityId":     sid,
            "name":           sec.get("beehusName", ""),
            "amountDiff":     amt_diff,
            "diffRent":       diff_rent,
            "formerBalance":  f_bal,
            "pu":             pu,
            "formerPu":       f_pu,
            "executionPrice": exec_price,
            "quantity":       qty,
            "formerQuantity": f_qty,
            "eventContribution": event_c,
            "totalContribution": total_c,
            "securityType":   sec_info.get(sid, {}).get("securityType", ""),
            "failedConditions": [],
        }

        if cond_a and cond_b and cond_c and cond_d:
            eliminated.append(sec_entry)
        else:
            if not cond_a:
                sec_entry["failedConditions"].append("amountDifference")
            if not cond_b:
                sec_entry["failedConditions"].append("hasTransactions")
            if not cond_c:
                sec_entry["failedConditions"].append("provisionLifecycle")
            if not cond_d:
                sec_entry["failedConditions"].append("rentabilityDifference")
            suspects.append(sec_entry)

    step2 = {
        "status":          "done",
        "eliminatedCount": len(eliminated),
        "suspectCount":    len(suspects),
        "suspects":        suspects,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 3 — Diagnose Securities
    # ═══════════════════════════════════════════════════════════════════════════
    step3_securities = []

    for sec_entry in suspects:
        sid        = sec_entry["securityId"]
        amt_diff   = sec_entry["amountDiff"]
        diff_rent  = sec_entry["diffRent"]
        f_bal      = sec_entry["formerBalance"]
        pu         = sec_entry["pu"]
        exec_price = sec_entry["executionPrice"]
        price      = exec_price or pu or 0
        sec_type   = sec_entry["securityType"]
        sec_txns   = txns_by_security.get(sid, [])

        diag = {
            "securityId": sid,
            "name":       sec_entry["name"],
            "amountDiff": amt_diff,
            "diffRent":   diff_rent,
            "eliminated": False,
            "step3_1":    None,
            "step3_2":    None,
            "step3_3":    None,
            "step3_4":    None,
        }

        # ── 3.1 Amount Difference ──────────────────────────────────────────────
        if amt_diff:
            info   = sec_info.get(sid, {})
            settle = info.get("redemptionSettlementDays" if amt_diff < 0 else "subscriptionSettlementDays") or 0
            nav_d  = info.get("redemptionNavDays"        if amt_diff < 0 else "subscriptionNavDays")        or 0
            offset = settle - nav_d

            if offset == 0:
                # Expect transaction
                has_buysell = any(t.get("type") == "buySell" for t in sec_txns)
                if has_buysell:
                    diag["step3_1"] = {"status": "ok", "offset": offset,
                                       "detail": "Transação buySell encontrada"}
                else:
                    impact = round(abs(amt_diff) * price, 2)
                    diag["step3_1"] = {"status": "flag", "flag": "MISSING_TRANSACTION",
                                       "offset": offset, "impact": impact,
                                       "detail": "Liquidação imediata mas transação buySell não encontrada"}
            else:
                # Expect provision
                if sid in prov_map:
                    diag["step3_1"] = {"status": "ok", "offset": offset,
                                       "detail": "Provisão ativa encontrada",
                                       "provisionAmount": round(float(prov_map[sid]), 2)}
                else:
                    impact = round(abs(amt_diff) * price, 2)
                    flag_detail = "Liquidação futura" if offset > 0 else "Nav futuro"
                    # Provision window: liquidation = navPackage date + offset.
                    prov_initial, prov_liquidation = _prov_dates(date, offset)
                    prov_type = "buySell"
                    # Provision sign rule is SCENARIO-DEPENDENT (see
                    # refine/accept for the full derivation):
                    #   offset > 0 (liquidação futura, active [date, date+offset)):
                    #     qty already on position, cash not yet moved.
                    #     subscription → NEGATIVE ; redemption → POSITIVE
                    #   offset < 0 (nav futuro — txn already settled in past,
                    #     active [date+offset, date)): cash already moved,
                    #     qty not yet on position.
                    #     subscription → POSITIVE ; redemption → NEGATIVE
                    if offset > 0:
                        prov_balance = -impact if amt_diff > 0 else impact
                    else:
                        prov_balance = impact if amt_diff > 0 else -impact
                    diag["step3_1"] = {"status": "flag", "flag": "MISSING_PROVISION",
                                       "offset": offset, "impact": impact,
                                       "detail": f"{flag_detail} (offset={offset}) mas provisão não encontrada",
                                       "provisionData": {
                                           "initialDate":    prov_initial,
                                           "liquidationDate": prov_liquidation,
                                           "provisionType":  prov_type,
                                           "balance":        prov_balance,
                                           "offset":         offset,
                                       }}

        # ── 3.2 Rentability Difference ─────────────────────────────────────────
        if diff_rent and f_bal:
            expected_event_cash = round(-diff_rent * f_bal, 2)
            ev_txns = event_txns_by_sec.get(sid, [])
            ev_total = round(sum(float(t.get("balance", 0) or 0) for t in ev_txns), 2)

            if ev_txns and _approx(ev_total, expected_event_cash):
                diag["step3_2"] = {"status": "eliminated",
                                   "detail": "Diferença explicada por transação de evento",
                                   "expectedEventCash": expected_event_cash,
                                   "eventTransactionTotal": ev_total}
                diag["eliminated"] = True
            elif ev_txns:
                diag["step3_2"] = {"status": "flag", "flag": "WRONG_EVENT_BALANCE",
                                   "detail": "Transação de evento existe mas valor diverge",
                                   "expectedEventCash": expected_event_cash,
                                   "eventTransactionTotal": ev_total,
                                   "impact": round(abs(ev_total - expected_event_cash), 2)}
            elif sid in prov_map and _approx(float(prov_map[sid]), expected_event_cash):
                diag["step3_2"] = {"status": "eliminated",
                                   "detail": "Diferença explicada por provisão (provável evento anunciado)",
                                   "expectedEventCash": expected_event_cash,
                                   "provisionAmount": round(float(prov_map[sid]), 2)}
                diag["eliminated"] = True
            elif sid in prov_map:
                diag["step3_2"] = {"status": "flag", "flag": "WRONG_PROVISION_AMOUNT",
                                   "detail": "Provisão existe mas valor diverge do evento esperado",
                                   "expectedEventCash": expected_event_cash,
                                   "provisionAmount": round(float(prov_map[sid]), 2),
                                   "impact": round(abs(float(prov_map[sid]) - expected_event_cash), 2)}
            else:
                diag["step3_2"] = {"status": "flag", "flag": "MISSING_EVENT",
                                   "detail": "Sem transação de evento e sem provisão",
                                   "expectedEventCash": expected_event_cash,
                                   "impact": abs(expected_event_cash)}

        # ── 3.3 Withholding Tax / Execution Price ──────────────────────────────
        if amt_diff and not diag["eliminated"]:
            buysell_txns = [t for t in sec_txns if t.get("type") == "buySell"]
            if buysell_txns:
                actual_bal    = round(sum(float(t.get("balance", 0) or 0) for t in buysell_txns), 2)
                expected_val  = round(-amt_diff * price, 2)
                if _approx_txn(expected_val, actual_bal):
                    # Total value roughly matches (within 10%). There are three
                    # sub-cases to consider:
                    #   (a) brazilianFund redemption where actual < expected
                    #       → IR retido na fonte (WITHHOLDING_TAX). Priority
                    #         over MISSING_EXECUTION_PRICE because the implied
                    #         price naturally differs from the used price by
                    #         tax/quantity, which would otherwise be mis-flagged.
                    #   (b) implied price (= -actual / Δqty) differs from the
                    #       price actually used (`price = executionPrice or
                    #       PU`) by more than 0.5% → MISSING_EXECUTION_PRICE.
                    #   (c) otherwise → OK (value matches within tolerance).
                    diff_val = round(abs(expected_val - actual_bal), 2)
                    implied_exec = round(-actual_bal / amt_diff, 6) if amt_diff else None

                    if (sec_type == "brazilianFund" and amt_diff < 0
                            and actual_bal < expected_val and diff_val > _TOLERANCE_ABS):
                        diag["step3_3"] = {"status": "flag", "flag": "WITHHOLDING_TAX",
                                           "detail": "Provável IR retido na fonte (brazilianFunds)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "impact": diff_val}
                    elif (price and implied_exec is not None
                            and abs(implied_exec - price) > abs(price) * 0.005):
                        diag["step3_3"] = {"status": "flag", "flag": "MISSING_EXECUTION_PRICE",
                                           "detail": "Preço de execução divergente do preço usado (provável fallback de PU)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "pu": pu, "executionPrice": exec_price,
                                           "expectedExecPrice": implied_exec,
                                           "impact": diff_val}
                    else:
                        diag["step3_3"] = {"status": "ok",
                                           "detail": "Valor da transação confere com esperado",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2)}
                else:
                    diff_val = round(abs(expected_val - actual_bal), 2)
                    if sec_type == "brazilianFund" and amt_diff < 0:
                        diag["step3_3"] = {"status": "flag", "flag": "WITHHOLDING_TAX",
                                           "detail": "Provável IR retido na fonte (brazilianFunds)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "impact": diff_val}
                    elif exec_price is None or (pu is not None and exec_price == pu):
                        # expectedExecPrice = actualBalance / amountDiff
                        expected_exec_price = round(-actual_bal / amt_diff, 6) if amt_diff else None
                        diag["step3_3"] = {"status": "flag", "flag": "MISSING_EXECUTION_PRICE",
                                           "detail": "Preço de execução ausente (sistema usou PU como fallback)",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "pu": pu, "executionPrice": exec_price,
                                           "expectedExecPrice": expected_exec_price,
                                           "impact": diff_val}
                    else:
                        diag["step3_3"] = {"status": "flag", "flag": "WRONG_TRANSACTION_VALUE",
                                           "detail": "Valor da transação diverge do esperado",
                                           "expectedValue": expected_val,
                                           "actualBalance": round(actual_bal, 2),
                                           "impact": diff_val}

        # ── 3.4 Transaction exists but no quantity/rentab signal ───────────────
        # Fires when a suspect has transactions on the security, but neither
        # quantity nor rentability moved (amountDiff == 0 AND diffRent ∈ {0, None}).
        # If the sum of those transactions matches the gap, it's almost certainly
        # a misclassified beehusTransactionType — the transaction is moving cash
        # but its type (e.g. dividend, rebate, otherFee) is not counted in
        # eventContribution. Purely diagnostic: no Aceitar / no Bayesian / no
        # file generation for this flag.
        no_amt_change  = (amt_diff is not None and amt_diff == 0)
        no_rent_signal = (diff_rent is None or diff_rent == 0)
        if (no_amt_change and no_rent_signal and sec_txns
                and gap_cash is not None and not diag["step3_3"]):
            txn_sum = round(sum(float(t.get("balance", 0) or 0) for t in sec_txns), 2)
            if abs(txn_sum) > 0.01 and _approx(abs(txn_sum), abs(gap_cash)):
                types_found = sorted({(t.get("type") or "—") for t in sec_txns})
                diag["step3_4"] = {
                    "status":            "flag",
                    "flag":              "MISCLASSIFIED_EVENT_TYPE",
                    "impact":            abs(txn_sum),
                    "transactionTotal":  txn_sum,
                    "transactionTypes":  types_found,
                    "transactionCount":  len(sec_txns),
                    "detail": (
                        f"Transações neste security somam R$ {txn_sum:.2f}, valor idêntico ao gap. "
                        f"Tipo(s) presente(s): {', '.join(types_found)}. "
                        "Provável erro de classificação — o tipo atual não é reconhecido como evento "
                        "(coupon/amortization) e por isso não impacta eventContribution."
                    ),
                }

        if diag["step3_1"] or diag["step3_2"] or diag["step3_3"] or diag["step3_4"]:
            step3_securities.append(diag)

    step3 = {
        "status":     "done",
        "securities": step3_securities,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 4 — Diagnose Transactions
    # ═══════════════════════════════════════════════════════════════════════════

    # 4.1 Unclassified transactions
    unclassified = [
        {"securityId": t["securityId"], "balance": t.get("balance")}
        for t in all_txns_flat if not t.get("type")
    ]

    # 4.2 Wrong security identification
    wrong_security = []
    for sid, txns in txns_by_security.items():
        if sid in current_sec_ids:
            continue
        # This securityId is in transactions but not in position
        info = sec_info.get(sid, {})
        has_provision = sid in prov_map

        # Check: new purchase with subscriptionNavDays > 0
        sub_nav = info.get("subscriptionNavDays") or 0
        if sub_nav > 0 and has_provision:
            verdict = "LEGITIMATE_NEW_PURCHASE"
            reason  = f"Compra nova com subscriptionNavDays={sub_nav} e provisão existente"
        else:
            # Check: sold security with offset > 0
            red_settle = info.get("redemptionSettlementDays") or 0
            red_nav    = info.get("redemptionNavDays") or 0
            offset     = red_settle - red_nav
            if offset > 0 and has_provision:
                verdict = "LEGITIMATE_POST_SALE"
                reason  = f"Venda com offset={offset} e provisão existente"
            elif has_provision:
                verdict = "LEGITIMATE_WITH_PROVISION"
                reason  = "Security não está na posição mas provisão existe"
            else:
                verdict = "WRONG_SECURITY"
                reason  = "Security não encontrado na posição e sem provisão correspondente"

        total_bal = round(sum(float(t.get("balance", 0) or 0) for t in txns), 2)
        wrong_security.append({
            "securityId":   sid,
            "securityName": info.get("beehusName", ""),
            "balance":      total_bal,
            "txnCount":     len(txns),
            "verdict":      verdict,
            "reason":       reason,
        })

    # 4.3 Probable misclassified transactions
    # Collect missing values from Step 3 flags, then for each transaction check if
    # its balance matches a missing value from a DIFFERENT security.
    misclassified = []
    step3_missing = {}  # {securityId: [(name, impact, flag)]}
    for diag in step3_securities:
        sid = diag["securityId"]
        name = diag["name"]
        for key in ("step3_1", "step3_2", "step3_3"):
            s = diag.get(key)
            if s and s.get("status") == "flag" and s.get("impact"):
                step3_missing.setdefault(sid, []).append(
                    (name, round(float(s["impact"]), 2), s["flag"]))

    if step3_missing:
        for t in all_txns_flat:
            t_bal = round(abs(float(t.get("balance", 0) or 0)), 2)
            if not t_bal:
                continue
            t_sid = t.get("securityId", "")
            t_type = t.get("type")
            matches = []
            for miss_sid, entries in step3_missing.items():
                if miss_sid == t_sid:
                    continue
                for miss_name, miss_impact, miss_flag in entries:
                    if _approx(t_bal, miss_impact):
                        matches.append({
                            "securityId":   miss_sid,
                            "securityName": miss_name,
                            "flag":         miss_flag,
                            "expectedValue": miss_impact,
                        })
            if matches:
                misclassified.append({
                    "txnId":         t.get("txnId") or None,
                    "txnBalance":    round(float(t.get("balance", 0) or 0), 2),
                    "txnSecurityId": t_sid or None,
                    "txnType":       t_type,
                    "matches":       matches,
                })

    step4 = {
        "status":        "done",
        "unclassified":  unclassified,
        "wrongSecurity": wrong_security,
        "misclassified": misclassified,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 5 — Cash Validation
    # ═══════════════════════════════════════════════════════════════════════════
    # Batch both dates into one `cashAccounts` scan (no index on walletId).
    _cash = beehus_catalog.cash_sums_by_dates(wallet_id, [former_date, date], company_id)
    former_cash  = _cash[former_date]
    current_cash = _cash[date]
    total_txn_balance = round(sum(float(t.get("balance", 0) or 0) for t in all_txns_flat), 2)
    projected_cash = round(former_cash + total_txn_balance, 2) if former_cash is not None else None
    cash_diff = round(projected_cash - current_cash, 2) if (projected_cash is not None and current_cash is not None) else None

    # Step 5.1 — suspect transactions: any txn whose balance matches cashDiff
    # closely enough that removing it would zero out the divergence.
    # See docs/CONCILIACAO_DIAGNOSTICO.md § 5.1.
    #
    # Tighter tolerance than the generic _approx: use max(R$ 0,01; 0,1% of
    # |cashDiff|). The 5% relative band of _approx is too loose here — on a
    # cashDiff of R$ 500k it would accept any txn within ±R$ 25k, drowning
    # the genuine exact-match in noise. Results are sorted by closeness
    # ascending so the most plausible candidate renders first in the UI.
    suspect_txns = []
    if cash_diff is not None and abs(cash_diff) > _TOLERANCE_ABS:
        tol = max(_TOLERANCE_ABS, abs(cash_diff) * 0.001)
        candidates = []
        for t in all_txns_flat:
            bal = float(t.get("balance") or 0)
            delta = abs(bal - cash_diff)
            if delta <= tol:
                sid = t.get("securityId") or None
                sec_name = None
                if sid:
                    info = sec_info.get(str(sid))
                    if info:
                        sec_name = info.get("beehusName")
                candidates.append((delta, {
                    "txnId":        t.get("txnId") or None,
                    "balance":      round(bal, 2),
                    "type":         t.get("type"),
                    "securityId":   sid,
                    "securityName": sec_name,
                    "pending":      bool(t.get("pending")),
                }))
        candidates.sort(key=lambda x: x[0])
        suspect_txns = [c[1] for c in candidates]

    if cash_diff is not None and round(cash_diff, 2) == 0:
        cash_diagnosis = "consistent"
        cash_status    = "ok"
    elif unclassified:
        cash_diagnosis = "unclassified_txns"
        cash_status    = "warning"
    elif not all_txns_flat:
        cash_diagnosis = "missing_cash_txn"
        cash_status    = "warning"
    elif suspect_txns:
        cash_diagnosis = "likely_wrong_txn"
        cash_status    = "warning"
    elif cash_diff is not None:
        cash_diagnosis = "value_error"
        cash_status    = "warning"
    else:
        cash_diagnosis = "no_data"
        cash_status    = "ok"

    step5 = {
        "status":            cash_status,
        "formerCash":        former_cash,
        "currentCash":       current_cash,
        "totalTransactions": total_txn_balance,
        "projectedCash":     projected_cash,
        "cashDiff":          cash_diff,
        "diagnosis":         cash_diagnosis,
        "suspectTxns":       suspect_txns,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 6 — Rentability Anomalies
    # ═══════════════════════════════════════════════════════════════════════════

    # 6.1 Wallet-level 3-sigma check
    wallet_anomaly = None
    _nav_series = beehus_catalog.nav_series_for_entity(wallet_id, company_id)
    history = [
        pkg for pkg in _nav_series
        if str(pkg.get("positionDate") or "")[:10] < date
    ]
    history = sorted(history,
                     key=lambda h: str(h.get("positionDate") or ""),
                     reverse=True)[:60]
    returns = [
        h["returnNavPerShare"]
        for h in history if h.get("returnNavPerShare") is not None
    ]

    if len(returns) >= 3:
        mean   = statistics.mean(returns)
        stddev = statistics.stdev(returns)
        lower  = mean - 3 * stddev
        upper  = mean + 3 * stddev
        is_anomaly = return_nav_ps < lower or return_nav_ps > upper
        wallet_anomaly = {
            "isAnomaly":     is_anomaly,
            "currentReturn": return_nav_ps,
            "mean":          round(mean, 8),
            "stdDev":        round(stddev, 8),
            "lowerBound":    round(lower, 8),
            "upperBound":    round(upper, 8),
            "sampleSize":    len(returns),
        }

    # 6.2 Per-security anomalies — DISABLED. The sole producer of
    # rentability_thresholds.json (the removed "Validação Rentabilidades"
    # page) no longer exists, so there are no thresholds to compare against;
    # this stays an empty list. Reintroduce a thresholds producer
    # (batch/pre-compute) to re-enable per-security anomaly detection.
    security_anomalies = []

    step6_status = "ok"
    if (wallet_anomaly and wallet_anomaly["isAnomaly"]) or any(a["isAnomaly"] for a in security_anomalies):
        step6_status = "warning"

    step6 = {
        "status":             step6_status,
        "walletAnomaly":      wallet_anomaly,
        "securityAnomalies":  security_anomalies,
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # STEP 7 — Causa Provável
    # ═══════════════════════════════════════════════════════════════════════════
    step3_has_flags = any(
        (s.get("step3_1") or {}).get("status") == "flag" or
        (s.get("step3_2") or {}).get("status") == "flag" or
        (s.get("step3_3") or {}).get("status") == "flag" or
        (s.get("step3_4") or {}).get("status") == "flag"
        for s in step3_securities
    )
    step4_has_issues = (
        bool(unclassified)
        or any(t.get("verdict") == "WRONG_SECURITY" for t in wrong_security)
        or bool(misclassified)
    )
    step5_consistent = step5["diagnosis"] == "consistent"
    all_suspects_missing_contrib = (
        len(suspects) > 0 and all(s.get("diffRent") is None for s in suspects)
    )
    step6_wallet_anomaly = bool(wallet_anomaly and wallet_anomaly.get("isAnomaly"))

    def _fmt_brl(v):
        if v is None:
            return "—"
        try:
            return "R$ " + f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        except Exception:
            return str(v)

    # Matrix order: SECURITY_ISSUES → TRANSACTION_ISSUES → CASH_ISSUES →
    # LIKELY_WRONG_FORMER_NAV. First match wins. (NO_GAP handled in early return.)
    if step3_has_flags:
        verdict      = "SECURITY_ISSUES"
        step7_status = "warning"
        step7_detail = "Há flags acionáveis em securities. Ver Step 3."
    elif step4_has_issues:
        verdict      = "TRANSACTION_ISSUES"
        step7_status = "warning"
        step7_detail = "Há transações não identificadas, com security divergente ou mal classificadas. Ver Step 4."
    elif not step5_consistent:
        verdict      = "CASH_ISSUES"
        step7_status = "warning"
        step7_detail = "Caixa projetado diverge do caixa real. Ver Step 5."
    else:
        verdict      = "LIKELY_WRONG_FORMER_NAV"
        step7_status = "warning"
        step7_detail = (
            "Securities, transações e caixa estão consistentes, mas o gap persiste. "
            f"Causa mais provável: o NAV anterior ({_fmt_brl(former_nav)} em "
            f"{former_date or '(data desconhecida)'}) está incorreto. "
            "Verifique a posição e o navPackage do dia anterior."
        )

    step7 = {
        "status":     step7_status,
        "verdict":    verdict,
        "detail":     step7_detail,
        "formerNav":  former_nav,
        "formerDate": former_date,
        "gapCash":    gap_cash,
        "gapPct":     gap_pct,
        "signals": {
            "step3HasFlags":                  step3_has_flags,
            "step4HasIssues":                 step4_has_issues,
            "step5Consistent":                step5_consistent,
            "step6WalletAnomaly":             step6_wallet_anomaly,
            "allSuspectsMissingContribution": all_suspects_missing_contrib,
        },
    }

    # ═══════════════════════════════════════════════════════════════════════════
    # Response
    # ═══════════════════════════════════════════════════════════════════════════
    return jsonify(_sanitize({
        "walletId": wallet_id,
        "date":     date,
        "step1":    step1,
        "step2":    step2,
        "step3":    step3,
        "step4":    step4,
        "step5":    step5,
        "step6":    step6,
        "step7":    step7,
    }))


@bp.route("/api/conciliacao/provisions")
def get_provisions():
    wallet_id  = request.args.get("walletId", "")
    date       = request.args.get("date", "")
    company_id, err = _require_visible_wallet(wallet_id)
    if err: return err

    # Active provisions from processed-position envelope (same source as
    # wallet-detail and diagnose — avoids the slow full-scan from 2000-01-01).
    raw = beehus_catalog.provisions_for_processed_date(wallet_id, date, company_id)

    # Build name map from catalog (warm) or from provision docs themselves.
    name_map = {}
    for prov in raw:
        sid = str(prov.get("securityId") or "")
        nm  = prov.get("securityBeehusName") or prov.get("securityName") or ""
        if sid and nm:
            name_map[sid] = nm

    # Warm the securities index for any remaining unknowns (non-blocking).
    unknown_sids = [
        str(p.get("securityId") or "")
        for p in raw
        if str(p.get("securityId") or "") and
           str(p.get("securityId") or "") not in name_map
    ]
    if unknown_sids:
        if beehus_catalog.securities_index_is_warm():
            for sid_u, doc in beehus_catalog.securities_by_ids(
                    unknown_sids).items():
                name_map[str(sid_u)] = doc.get("beehusName", "")
        else:
            beehus_catalog.warm_securities_index_async()

    provisions = []
    for prov in raw:
        sid = str(prov.get("securityId", "") or "")
        amt = prov.get("balance") if prov.get("balance") is not None else prov.get("amount")
        provisions.append({
            "provisionId":    str(prov.get("_id", "") or ""),
            "securityId":     sid,
            "securityName":   name_map.get(sid, ""),
            "initialDate":    str(prov.get("initialDate", ""))[:10],
            "liquidationDate": str(prov.get("liquidationDate", ""))[:10],
            "balance":        float(amt) if amt is not None else None,
            "provisionType":  prov.get("provisionType", ""),
            "description":    prov.get("description", ""),
            "isPending":      False,
        })

    # Append pending correction provisions active on `date`.
    corr_provs = load_active_pending_provisions(company_id, wallet_id, date)
    for corr_p in corr_provs:
        sid = str(corr_p.get("securityId", "") or "")
        bal = corr_p.get("balance")
        provisions.append({
            "securityId":      sid,
            "securityName":    name_map.get(sid, ""),
            "initialDate":     str(corr_p.get("initialDate", ""))[:10],
            "liquidationDate": str(corr_p.get("liquidationDate", ""))[:10],
            "balance":         float(bal) if bal is not None else None,
            "provisionType":   corr_p.get("provisionType", ""),
            "description":     corr_p.get("description", ""),
            "isPending":       True,
            "correctionId":    corr_p.get("id"),
        })

    provisions.sort(key=lambda pv: (pv["liquidationDate"], pv["securityName"]))
    total = round(
        sum(pv["balance"] for pv in provisions if pv["balance"] is not None), 2)
    return jsonify(_sanitize({"provisions": provisions, "total": total}))


@bp.route("/api/conciliacao/diagnose/feedback", methods=["POST"])
def diagnose_feedback():
    return jsonify({
        "error": "diagnose/feedback não disponível — armazenamento MongoDB removido.",
        "code":  "MONGO_FREE",
    }), 503


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


def _processed_position_window(wallet_id, center_iso, window_days):
    """Return sorted list of YYYY-MM-DD strings for processedPosition dates that
    bracket `center_iso` — up to `window_days` dates strictly before and
    strictly after. Excludes `center_iso` itself. Uses catalog nav series as
    a proxy for processed-position dates (same business-day cadence).
    """
    if not wallet_id or not center_iso:
        return []
    try:
        _date.fromisoformat(center_iso[:10])
    except Exception:
        return []
    center_str = center_iso[:10]
    company_id = _wallet_company(wallet_id)
    nav_series = beehus_catalog.nav_series_for_entity(wallet_id, company_id)
    raw_dates = {
        str(pkg.get("positionDate") or "")[:10]
        for pkg in nav_series
        if str(pkg.get("positionDate") or "")[:10] not in ("", center_str)
    }
    before = sorted(
        [d for d in raw_dates if d < center_str], reverse=True)[:window_days]
    after  = sorted(
        [d for d in raw_dates if d > center_str])[:window_days]
    return sorted(before + after)


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


def _refine_load(company_id, date, wallet_id):
    path = _refine_store_path(company_id, date, wallet_id)
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("accepted", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _refine_save(company_id, date, wallet_id, accepted):
    path = _refine_store_path(company_id, date, wallet_id)
    if not path:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, {
        "walletId":  wallet_id,
        "date":      date,
        "companyId": company_id,
        "accepted":  accepted,
    })
    return True


@bp.route("/api/conciliacao/diagnose/refine/accept", methods=["POST"])
def diagnose_refine_accept():
    """Record that the user confirmed a refinement match as the real cause.

    Does NOT emit a correction file — this is an audit/confirmation record
    that downstream flows will consume later. Idempotent: records keyed by
    (securityId, matchTxnId) are replaced in-place so repeated clicks don't
    duplicate.
    """
    data = request.get_json() or {}
    wallet_id  = str(data.get("walletId") or "")[:64]
    _, err = _require_visible_wallet(wallet_id)
    if err: return err
    date       = str(data.get("date") or "")[:10]
    sid        = str(data.get("securityId") or "")[:64]
    match      = data.get("match") or {}
    txn_id     = str(match.get("txnId") or "")[:64]
    hypothesis = str(data.get("hypothesis") or "")[:64]
    user_note  = str(data.get("userNote") or "")[:2048]
    security_name = str(data.get("securityName") or "")[:256]

    _VALID_HYPOS = {"WRONG_SECURITY", "OFFSET_OR_SETTLEMENT_DRIFT"}
    if not (wallet_id and date and sid and txn_id and hypothesis in _VALID_HYPOS):
        return jsonify({"error": "walletId, date, securityId, match.txnId e hypothesis (WRONG_SECURITY|OFFSET_OR_SETTLEMENT_DRIFT) são obrigatórios"}), 400

    company_id = _wallet_company(wallet_id)
    if not company_id:
        return jsonify({"error": "companyId não resolvido para a wallet"}), 400

    now = _dt.now(timezone.utc).isoformat()
    existing = _refine_load(company_id, date, wallet_id)

    # Upsert by (securityId, matchTxnId, hypothesis) — each hypothesis on the
    # same match is a distinct confirmation the user may give independently.
    key_tuple = (sid, txn_id, hypothesis)
    updated = False
    for rec in existing:
        if (str(rec.get("securityId")), str(rec.get("matchTxnId")), str(rec.get("hypothesis"))) == key_tuple:
            rec.update({
                "securityName":   security_name,
                "amountDiff":     data.get("amountDiff"),
                "expectedImpact": data.get("expectedImpact"),
                "match":          match,
                "userNote":       user_note,
                "updatedAt":      now,
            })
            updated = True
            break

    if not updated:
        existing.append({
            "walletId":         wallet_id,
            "date":             date,
            "securityId":       sid,
            "matchTxnId":       txn_id,
            "hypothesis":       hypothesis,
            "securityName":     security_name,
            "amountDiff":       data.get("amountDiff"),
            "expectedImpact":   data.get("expectedImpact"),
            "match":            match,
            "userNote":         user_note,
            "firstAcceptedAt":  now,
            "updatedAt":        now,
        })

    if not _refine_save(company_id, date, wallet_id, existing):
        return jsonify({"error": "falha ao persistir feedback"}), 500

    # ── Side effect: emit a pending provision for OFFSET_OR_SETTLEMENT_DRIFT ──
    # The provision bridges the position change (on `date`) and the matched
    # transaction on a different date, closing the NAV gap that results from
    # the cadastro offset being wrong or the institution settling off-date.
    # WRONG_SECURITY intentionally does NOT emit here — that fix belongs to
    # the MISCLASSIFIED flow (deletion + replacement txn), out of scope for
    # this endpoint.
    provision_written = None
    if hypothesis == "OFFSET_OR_SETTLEMENT_DRIFT":
        match_liq = str(match.get("liquidationDate") or "")[:10]
        match_bal = match.get("balance")
        amt_diff  = data.get("amountDiff")
        expected_impact = data.get("expectedImpact")

        # Pick dates AND sign simultaneously. The provision is ACTIVE during the
        # window `[initialDate, liquidationDate)` and must counterbalance whatever
        # has already happened in that window so NAV stays correct.
        #
        # Case B (match in FUTURE): active window is [date, match.liq).
        #   On these days, qty change is already on the position but cash hasn't
        #   moved yet. Provision offsets the asset change:
        #     subscription (amt_diff>0) → NEGATIVE  (pending payable)
        #     redemption   (amt_diff<0) → POSITIVE  (pending receivable)
        #   Equivalent to: provision ≈ match.balance (which already carries the
        #   correct "future cash flow" sign).
        #
        # Case A (match in PAST): active window is [match.liq, date).
        #   On these days, cash already moved but qty change isn't on position
        #   yet. Provision offsets the cash move, raising the depressed NAV:
        #     subscription (amt_diff>0) → POSITIVE  (asset receivable pending)
        #     redemption   (amt_diff<0) → NEGATIVE  (asset payable pending)
        #   Equivalent to: provision ≈ −match.balance.
        prov_balance = None
        match_in_past = bool(match_liq and match_liq < date)
        match_in_future = bool(match_liq and match_liq > date)

        if match_in_future:
            prov_initial, prov_liquidation = date, match_liq
            if isinstance(match_bal, (int, float)) and match_bal != 0:
                prov_balance = float(match_bal)
            elif isinstance(amt_diff, (int, float)) and isinstance(expected_impact, (int, float)):
                prov_balance = -abs(expected_impact) if amt_diff > 0 else abs(expected_impact)
        elif match_in_past:
            prov_initial, prov_liquidation = match_liq, date
            if isinstance(match_bal, (int, float)) and match_bal != 0:
                prov_balance = -float(match_bal)    # flip sign vs future case
            elif isinstance(amt_diff, (int, float)) and isinstance(expected_impact, (int, float)):
                prov_balance = abs(expected_impact) if amt_diff > 0 else -abs(expected_impact)
        else:
            prov_initial = prov_liquidation = None

        # Minimum-1-business-day rule: a provision must settle at least 1
        # business day after the nav date (same floor as `_prov_dates`). Guards
        # the match_in_past branch, whose raw liquidation is the nav date itself.
        if prov_liquidation:
            try:
                _floor = _next_biz_day(_date.fromisoformat(date)).isoformat()
                if prov_liquidation < _floor:
                    prov_liquidation = _floor
            except (TypeError, ValueError):
                pass

        if prov_initial and prov_liquidation and prov_balance is not None:
            source_key = f"OFFSET_DRIFT|{sid}|{txn_id}"
            sec_name = data.get("securityName", "") or sid
            desc = (
                f"Provisão gerada por refinamento (offset/settlement drift) — "
                f"{sec_name}: transação em {match_liq}, posição em {date}"
            )
            added, skipped = append_rows_for_wallet(
                company_id, date, wallet_id,
                provisions=[{
                    "walletId":        wallet_id,
                    "initialDate":     prov_initial,
                    "liquidationDate": prov_liquidation,
                    "provisionType":   "buySell",
                    "securityId":      sid,
                    "balance":         round(prov_balance, 2),
                    "description":     desc,
                    "sourceAnomalyKey": source_key,
                }],
            )
            provision_written = {
                "initialDate":     prov_initial,
                "liquidationDate": prov_liquidation,
                "balance":         round(prov_balance, 2),
                "sourceAnomalyKey": source_key,
                "added":           added.get("provisions", 0),
                "skipped":         skipped.get("provisions", 0),
            }

    return jsonify({"ok": True, "securityId": sid, "matchTxnId": txn_id,
                    "hypothesis": hypothesis, "acceptedAt": now,
                    "provision": provision_written})


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


def _dismiss_load(company_id, date, wallet_id):
    path = _dismiss_store_path(company_id, date, wallet_id)
    if not path or not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("dismissed", []) if isinstance(data, dict) else []
    except Exception:
        return []


def _dismiss_save(company_id, date, wallet_id, dismissed):
    path = _dismiss_store_path(company_id, date, wallet_id)
    if not path:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, {
        "walletId":  wallet_id,
        "date":      date,
        "companyId": company_id,
        "dismissed": dismissed,
    })
    return True


@bp.route("/api/conciliacao/diagnose/dismiss", methods=["POST"])
def diagnose_dismiss():
    """Mark a detected flag as non-actionable (dismissed) so the UI hides it.
    Keyed by `anomalyKey` (the same key used by /correcoes). Idempotent."""
    data = request.get_json() or {}
    # Cap user-controlled string fields to prevent denial-of-storage on the
    # OneDrive-synced dismiss/refinement files.
    wallet_id    = str(data.get("walletId") or "")[:64]
    date         = str(data.get("date") or "")[:10]
    anomaly_key  = str(data.get("anomalyKey") or "")[:256]
    reason       = str(data.get("reason") or "")[:2048]
    flag         = str(data.get("flag") or "")[:64]
    security_id  = str(data.get("securityId") or "")[:64]
    security_name = str(data.get("securityName") or "")[:256]

    if not (wallet_id and date and anomaly_key):
        return jsonify({"error": "walletId, date e anomalyKey são obrigatórios"}), 400
    _, err = _require_visible_wallet(wallet_id)
    if err: return err

    company_id = _wallet_company(wallet_id)
    if not company_id:
        return jsonify({"error": "companyId não resolvido para a wallet"}), 400

    now = _dt.now(timezone.utc).isoformat()
    existing = _dismiss_load(company_id, date, wallet_id)

    updated = False
    for rec in existing:
        if str(rec.get("anomalyKey")) == anomaly_key:
            rec.update({
                "reason":    reason,
                "flag":      flag or rec.get("flag", ""),
                "updatedAt": now,
            })
            updated = True
            break
    if not updated:
        existing.append({
            "walletId":        wallet_id,
            "date":            date,
            "anomalyKey":      anomaly_key,
            "flag":            flag,
            "securityId":      security_id,
            "securityName":    security_name,
            "reason":          reason,
            "firstDismissedAt": now,
            "updatedAt":       now,
        })

    if not _dismiss_save(company_id, date, wallet_id, existing):
        return jsonify({"error": "falha ao persistir descarte"}), 500
    return jsonify({"ok": True, "anomalyKey": anomaly_key, "dismissedAt": now})


@bp.route("/api/conciliacao/diagnose/dismiss/undo", methods=["POST"])
def diagnose_dismiss_undo():
    """Remove a dismiss record so the flag reappears in the active list."""
    data = request.get_json() or {}
    wallet_id   = str(data.get("walletId") or "")
    date        = str(data.get("date") or "")
    anomaly_key = str(data.get("anomalyKey") or "")

    if not (wallet_id and date and anomaly_key):
        return jsonify({"error": "walletId, date e anomalyKey são obrigatórios"}), 400

    company_id, err = _require_visible_wallet(wallet_id)
    if err: return err

    existing = _dismiss_load(company_id, date, wallet_id)
    remaining = [r for r in existing if str(r.get("anomalyKey")) != anomaly_key]
    if len(remaining) == len(existing):
        return jsonify({"ok": True, "removed": False})
    if not _dismiss_save(company_id, date, wallet_id, remaining):
        return jsonify({"error": "falha ao persistir"}), 500
    return jsonify({"ok": True, "removed": True})


@bp.route("/api/conciliacao/diagnose/dismissed")
def diagnose_dismissed():
    """Return dismissed anomalyKeys (+ metadata) for a wallet+date."""
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")
    if not wallet_id or not date:
        return jsonify({"dismissed": []})
    company_id, err = _require_visible_wallet(wallet_id)
    if err: return err
    records = _dismiss_load(company_id, date, wallet_id)
    return jsonify({"dismissed": records})


@bp.route("/api/conciliacao/diagnose/refine/accepted")
def diagnose_refine_accepted():
    """Return the set of (securityId, matchTxnId, hypothesis) triples already
    confirmed for a wallet+date. UI uses this to pre-mark each hypothesis's
    "Aceitar" button independently."""
    wallet_id = request.args.get("walletId", "")
    date      = request.args.get("date", "")
    if not wallet_id or not date:
        return jsonify({"accepted": []})
    company_id, err = _require_visible_wallet(wallet_id)
    if err: return err
    records = _refine_load(company_id, date, wallet_id)
    accepted = [
        {"securityId":       r.get("securityId"),
         "matchTxnId":       r.get("matchTxnId"),
         "hypothesis":       r.get("hypothesis"),
         "firstAcceptedAt":  r.get("firstAcceptedAt")}
        for r in records
    ]
    return jsonify({"accepted": accepted})


@bp.route("/api/conciliacao/diagnose/refine")
def diagnose_refine():
    """Refinement analysis — temporarily unavailable (MongoDB removed)."""
    return jsonify({
        "error": "diagnose/refine não disponível — migração MongoDB em curso.",
        "code":  "MONGO_FREE",
    }), 503


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


@bp.route("/api/conciliacao/bayesian")
def bayesian_optimize():
    """Run Bayesian factor extraction + optimization on a diagnose result.

    Query params: walletId, date (same as /diagnose).
    Returns factors, best fix with validation, alternatives, and summary.
    """
    diag_resp = diagnose()

    if isinstance(diag_resp, tuple):
        return diag_resp
    diag = diag_resp.get_json(force=True)
    if not diag or "error" in diag:
        return diag_resp

    cfg     = _load_bayesian_config()
    factors = extract_factors(diag, cfg=cfg)
    result  = optimize_with_validation(diag, cfg=cfg)
    summary = compute_summary(diag)

    return jsonify(_sanitize({
        "walletId": request.args.get("walletId", ""),
        "date":     request.args.get("date", ""),
        "factors":  factors,
        "optimization": result,
        "summary":  summary,
    }))


@bp.route("/api/conciliacao/bayesian/from-payload", methods=["POST"])
def bayesian_from_payload():
    """Run Bayesian optimization on an already-computed diagnose payload.

    POST body: the raw JSON output of /api/conciliacao/diagnose.
    No DB access required. Includes Monte Carlo + validation.
    """
    diag = request.get_json(force=True)
    if not diag or "step1" not in diag:
        return jsonify({"error": "Payload inválido — esperado output de /diagnose"}), 400

    cfg     = _load_bayesian_config()
    factors = extract_factors(diag, cfg=cfg)
    result  = optimize_with_validation(diag, cfg=cfg)
    summary = compute_summary(diag)

    return jsonify(_sanitize({
        "walletId":     diag.get("walletId", ""),
        "date":         diag.get("date", ""),
        "factors":      factors,
        "optimization": result,
        "summary":      summary,
    }))


# ── Global Analysis ────────────────────────────────────────────────────────────

_TIER1_FLAGS = {"MISSING_TRANSACTION", "MISSING_PROVISION", "MISSING_EVENT",
                "WRONG_EVENT_BALANCE", "WRONG_PROVISION_AMOUNT"}


@bp.route("/api/conciliacao/global-analysis")
def global_analysis():
    """Cross-wallet analysis: group deterministic (Tier 1) flags by security.

    Runs a lightweight Steps 1-3 across all wallets with mismatches for the
    given company + date, then aggregates flags by (securityId, flagType).
    """
    return jsonify({
        "error": "rota não disponível — migração MongoDB em curso.",
        "code":  "MONGO_FREE",
    }), 503


def _agg_flag(agg, sid, sec_name, sec_type, flag, impact, wid, wallet_name, gap_cash,
              amt_diff, pu, exec_price, offset=0):
    """Aggregate a flag occurrence into the global analysis dict."""
    key = (sid, flag)
    if key not in agg:
        agg[key] = {
            "securityName": sec_name,
            "securityType": sec_type,
            "pu":           pu,
            "executionPrice": exec_price,
            "amountDiff":   amt_diff,
            "offset":       offset,
            "wallets":      [],
        }
    agg[key]["wallets"].append({
        "walletId":   wid,
        "walletName": wallet_name,
        "impact":     impact,
        "gapCash":    gap_cash,
    })


# ── Global Execution-Price Check ──────────────────────────────────────────────

@bp.route("/api/conciliacao/global-execution-prices")
def global_execution_prices():
    return jsonify({
        "error": "rota não disponível — migração MongoDB em curso.",
        "code":  "MONGO_FREE",
    }), 503


def _is_oid(s):
    try:
        _OID(str(s))
        return True
    except Exception:
        return False


# ── Transaction Check ──────────────────────────────────────────────────────────

@bp.route("/api/conciliacao/transaction-check")
def transaction_check():
    return jsonify({
        "error": "rota não disponível — migração MongoDB em curso.",
        "code":  "MONGO_FREE",
    }), 503


# ── Scenario Capture ─────────────────────────────────────────────────────────

_SCENARIOS_DIR = os.path.join(os.path.dirname(__file__), "..", "tests", "scenarios")


def _next_scenario_id():
    """Find the next available scenario number (T10, T11, ...)."""
    os.makedirs(_SCENARIOS_DIR, exist_ok=True)
    existing = []
    for name in os.listdir(_SCENARIOS_DIR):
        m = re.match(r"^T(\d+)$", name)
        if m:
            existing.append(int(m.group(1)))
    return max(existing, default=0) + 1


def _bson_to_json(obj):
    """Recursively convert BSON types (ObjectId, datetime) to JSON-safe values."""
    if isinstance(obj, _OID):
        return str(obj)
    if isinstance(obj, _dt):
        return obj.isoformat()
    if isinstance(obj, _date):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _bson_to_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_bson_to_json(v) for v in obj]
    if isinstance(obj, float) and (math.isinf(obj) or math.isnan(obj)):
        return None
    return obj


def _capture_scenario_data(wallet_id, date_str):
    """Snapshot all MongoDB state needed to replay a diagnostic scenario.

    Returns a dict with the same structure as tests/scenarios/T9/.
    """
    # ── 1. Wallet ─────────────────────────────────────────────────────────────
    wallet_doc = db.wallets.find_one({"_id": wallet_id})
    if not wallet_doc:
        try:
            wallet_doc = db.wallets.find_one({"_id": _OID(wallet_id)})
        except Exception:
            pass
    wallet_json = {
        "_id":                     str(wallet_doc["_id"]) if wallet_doc else wallet_id,
        "name":                    (wallet_doc or {}).get("name", ""),
        "companyId":               str((wallet_doc or {}).get("companyId", "")),
        "entityId":                str((wallet_doc or {}).get("entityId", "")),
        "currencyId":              str((wallet_doc or {}).get("currencyId", "BRL")),
        "hasDailyPosition":        (wallet_doc or {}).get("hasDailyPosition", True),
        "startDateConsolidation":  str((wallet_doc or {}).get("startDateConsolidation", "")),
        "startDateReturn":         str((wallet_doc or {}).get("startDateReturn", "")),
    }

    # ── 2. Former date (navPackage source of truth) ───────────────────────────
    former_date, former_nav = _find_former_nav(wallet_id, date_str)

    # ── 3. Nav packages (former + current) ────────────────────────────────────
    current_nav = db.navPackages.find_one(
        {"walletId": wallet_id, "positionDate": date_str, "trashed": {"$ne": True}},
    )
    former_nav_doc = None
    if former_date:
        former_nav_doc = db.navPackages.find_one(
            {"walletId": wallet_id, "positionDate": former_date, "trashed": {"$ne": True}},
        )
    nav_packages_json = {
        "former":  _bson_to_json({k: v for k, v in (former_nav_doc or {}).items() if k != "_id"}),
        "current": _bson_to_json({k: v for k, v in (current_nav or {}).items() if k != "_id"}),
    }

    # ── 4. Positions (former + current) ───────────────────────────────────────
    current_pos = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": date_str}, {"securities": 1}
    )
    former_pos = None
    if former_date:
        former_pos = db.processedPosition.find_one(
            {"walletId": wallet_id, "positionDate": former_date}, {"securities": 1}
        )
    positions_json = {
        "former":  _bson_to_json({k: v for k, v in (former_pos or {}).items() if k != "_id"}),
        "current": _bson_to_json({k: v for k, v in (current_pos or {}).items() if k != "_id"}),
    }

    # ── 5. Transactions ──────────────────────────────────────────────────────
    txns = list(db.transactions.find(
        {"walletId": wallet_id, "liquidationDate": date_str},
        {"_id": 0}
    ))
    transactions_json = {
        "transactions": _bson_to_json(txns),
    }

    # ── 6. Provisions (active on date) ────────────────────────────────────────
    active_provs = list(db.provisions.find(
        {"walletId": wallet_id, "initialDate": {"$lte": date_str},
         "liquidationDate": {"$gt": date_str}},
        {"_id": 0}
    ))
    provisions_json = {
        "activeProvisions": _bson_to_json(active_provs),
    }

    # ── 7. Cash accounts ─────────────────────────────────────────────────────
    cash_docs = list(db.cashAccounts.find(
        {"walletId": wallet_id}, {"_id": 0}
    ))
    cash_accounts_json = {
        "cashAccounts": _bson_to_json(cash_docs),
    }

    # ── 8. Securities metadata (only those in position or transactions) ──────
    sec_ids_raw = set()
    for s in (current_pos or {}).get("securities", []):
        if s.get("securityId"):
            sec_ids_raw.add(s["securityId"])
    for t in txns:
        if t.get("securityId"):
            sec_ids_raw.add(t["securityId"])

    sec_ids_query = []
    for sid in sec_ids_raw:
        sec_ids_query.append(sid)
        try:
            sec_ids_query.append(_OID(str(sid)))
        except Exception:
            pass

    securities = []
    for s in db.securities.find(
        {"_id": {"$in": sec_ids_query}},
        {"beehusName": 1, "securityType": 1,
         "redemptionNavDays": 1, "redemptionSettlementDays": 1,
         "subscriptionNavDays": 1, "subscriptionSettlementDays": 1}
    ):
        securities.append({
            "_id":                       str(s["_id"]),
            "beehusName":                s.get("beehusName", ""),
            "securityType":              s.get("securityType", ""),
            "redemptionNavDays":         s.get("redemptionNavDays", 0),
            "redemptionSettlementDays":  s.get("redemptionSettlementDays", 0),
            "subscriptionNavDays":       s.get("subscriptionNavDays", 0),
            "subscriptionSettlementDays": s.get("subscriptionSettlementDays", 0),
        })

    return {
        "wallet":         wallet_json,
        "nav_packages":   nav_packages_json,
        "positions":      positions_json,
        "transactions":   transactions_json,
        "provisions":     provisions_json,
        "cash_accounts":  cash_accounts_json,
        "securities":     securities,
    }


def _compute_coverage(diagnose_output, bayesian_output):
    """Compute how much of the gap is explained by diagnosed flags.

    Returns dict with coveragePct, explainedImpact, residualGap, flags.
    """
    gap_cash = abs((diagnose_output.get("step1") or {}).get("gapCash") or 0)
    if gap_cash == 0:
        return {"coveragePct": 100.0, "explainedImpact": 0, "residualGap": 0, "flags": []}

    # Extract flag impacts from step3 securities. step3_4
    # (MISCLASSIFIED_EVENT_TYPE) is purely diagnostic — see step3 builder
    # comment, "no Aceitar / no Bayesian" — so the Bayesian engine ignores
    # it. Drop it from coverage too, otherwise a security whose only signal
    # is step3_4 inflates explained-impact without ever appearing in the
    # power-set scoring downstream.
    flags = []
    for sec in (diagnose_output.get("step3") or {}).get("securities", []):
        for key in ("step3_1", "step3_2", "step3_3"):
            sub = sec.get(key)
            if sub and sub.get("status") == "flag":
                flags.append({
                    "flag":       sub.get("flag", ""),
                    "securityId": sec.get("securityId", ""),
                    "impact":     abs(sub.get("impact") or 0),
                })

    explained = sum(f["impact"] for f in flags)
    residual = gap_cash - explained

    # If bayesian found a best fix, use its residual instead (more accurate)
    best_fix = (bayesian_output or {}).get("bestFix")
    if best_fix and best_fix.get("residualGap") is not None:
        residual = abs(best_fix["residualGap"])
        explained = gap_cash - residual

    coverage_pct = round((explained / gap_cash) * 100, 2) if gap_cash else 100.0
    coverage_pct = min(coverage_pct, 100.0)

    return {
        "coveragePct":    coverage_pct,
        "explainedImpact": round(explained, 2),
        "residualGap":    round(residual, 2),
        "flags":          flags,
    }


@bp.route("/api/conciliacao/capture-scenario", methods=["POST"])
def capture_scenario():
    return jsonify({
        "error": "rota não disponível — migração MongoDB em curso.",
        "code":  "MONGO_FREE",
    }), 503

@bp.route("/api/conciliacao/scenario-analysis")
def scenario_analysis():
    """Return the (possibly streaming) analysis.md content for a scenario.

    Response shape:
        { status: "pending"|"done"|"error"|"absent", content: "..." }

    `pending`  → subprocess still writing
    `done`     → subprocess finished (status sentinel removed or not present
                 and the file has stabilized)
    `error`    → subprocess failed to start
    `absent`   → no analysis was ever triggered for this scenario
    """
    scenario_id = request.args.get("scenarioId", "").strip()
    if not scenario_id or not re.match(r"^T\d+$", scenario_id):
        return jsonify({"error": "scenarioId inválido"}), 400
    scenario_dir  = os.path.join(_SCENARIOS_DIR, scenario_id)
    status_path   = os.path.join(scenario_dir, ".analysis_status")
    analysis_path = os.path.join(scenario_dir, "analysis.md")

    if not os.path.exists(scenario_dir):
        return jsonify({"status": "absent", "content": ""}), 404

    content = ""
    if os.path.exists(analysis_path):
        try:
            with open(analysis_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            content = ""

    if os.path.exists(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                status = f.read().strip() or "pending"
        except Exception:
            status = "pending"
        # Heuristic completion: if the status is still "pending" and the
        # stdout file hasn't been touched for ≥ 30 seconds, assume Claude
        # finished and the subprocess exited without flipping the sentinel.
        # The 30s threshold accommodates tool-call pauses (MCP latency,
        # rate-limit backoff) that commonly exceed 5s without meaning
        # the subprocess is done.
        if status == "pending":
            try:
                mtime = os.path.getmtime(analysis_path) if os.path.exists(analysis_path) else 0
                if mtime and (_dt.now(timezone.utc).timestamp() - mtime) > 30 and content.strip():
                    status = "done"
                    atomic_write_text(status_path, "done")
            except Exception:
                pass
        return jsonify({"status": status, "content": content})

    if content:
        return jsonify({"status": "done", "content": content})
    return jsonify({"status": "absent", "content": ""})


def _trigger_implementation(scenario_id, user_choice=""):
    """Spawn `claude -p /implement-scenario-suggestion <id>` in background.

    Mirrors `_maybe_trigger_analysis` but runs the implementation slash command
    against a scenario whose analysis.md already exists. If `user_choice` is
    provided, it is persisted to `<scenario_dir>/user_choice.txt` and the
    slash command will prefer it over any A/B ambiguity in analysis.md.

    Returns (ok: bool, error: str|None). Never raises.
    """
    scenario_dir = os.path.join(_SCENARIOS_DIR, scenario_id)
    if not os.path.isdir(scenario_dir):
        return False, "scenario directory not found"
    analysis_path = os.path.join(scenario_dir, "analysis.md")
    if not os.path.exists(analysis_path):
        return False, "analysis.md missing — run /analyze-scenario first"
    choice_path = os.path.join(scenario_dir, "user_choice.txt")
    # Persist (or clear) the user directive BEFORE spawning. The slash command
    # reads this file and treats it as authoritative over any A/B ambiguity.
    try:
        trimmed = (user_choice or "").strip()
        if trimmed:
            with open(choice_path, "w", encoding="utf-8") as f:
                f.write(trimmed)
        elif os.path.exists(choice_path):
            # Clear stale choice from previous runs.
            os.remove(choice_path)
    except Exception:
        pass
    return _spawn_claude_bg(
        scenario_dir,
        slash_cmd_args=[f"/implement-scenario-suggestion {scenario_id}",
                        "--permission-mode", "acceptEdits"],
        out_filename="implementation.md",
        status_filename=".implementation_status",
    )


@bp.route("/api/conciliacao/implement-suggestion", methods=["POST"])
def implement_suggestion():
    """Trigger background `/implement-scenario-suggestion` for a scenario.

    Precondition: `analysis.md` must exist in the scenario directory (user
    already ran /analyze-scenario via the capture flow).
    """
    data = request.get_json() or {}
    scenario_id = (data.get("scenarioId") or "").strip()
    user_choice = (data.get("userChoice") or "").strip()
    if not scenario_id or not re.match(r"^T\d+$", scenario_id):
        return jsonify({"ok": False, "error": "scenarioId inválido"}), 400
    ok, err = _trigger_implementation(scenario_id, user_choice=user_choice)
    if not ok:
        return jsonify({"ok": False, "error": err or "falha ao disparar"}), 400
    return jsonify({"ok": True, "scenarioId": scenario_id, "userChoice": user_choice})


@bp.route("/api/conciliacao/scenario-implementation")
def scenario_implementation():
    """Return the (possibly streaming) implementation.md content for a scenario.

    Same response shape and heuristics as `/scenario-analysis`, but reads
    `implementation.md` and `.implementation_status` instead.
    """
    scenario_id = request.args.get("scenarioId", "").strip()
    if not scenario_id or not re.match(r"^T\d+$", scenario_id):
        return jsonify({"error": "scenarioId inválido"}), 400
    scenario_dir = os.path.join(_SCENARIOS_DIR, scenario_id)
    status_path  = os.path.join(scenario_dir, ".implementation_status")
    out_path     = os.path.join(scenario_dir, "implementation.md")

    if not os.path.exists(scenario_dir):
        return jsonify({"status": "absent", "content": ""}), 404

    content = ""
    if os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except Exception:
            content = ""

    if os.path.exists(status_path):
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                status = f.read().strip() or "pending"
        except Exception:
            status = "pending"
        # Same 30s heuristic as /scenario-analysis — see that endpoint for
        # rationale on why 5s/10s were too tight.
        if status == "pending":
            try:
                mtime = os.path.getmtime(out_path) if os.path.exists(out_path) else 0
                if mtime and (_dt.now(timezone.utc).timestamp() - mtime) > 30 and content.strip():
                    status = "done"
                    atomic_write_text(status_path, "done")
            except Exception:
                pass
        return jsonify({"status": status, "content": content})

    if content:
        return jsonify({"status": "done", "content": content})
    return jsonify({"status": "absent", "content": ""})


@bp.route("/api/conciliacao/scenarios")
def list_scenarios():
    """List all captured scenarios with their metadata."""
    if not os.path.exists(_SCENARIOS_DIR):
        return jsonify({"scenarios": []})
    scenarios = []
    for name in sorted(os.listdir(_SCENARIOS_DIR)):
        meta_path = os.path.join(_SCENARIOS_DIR, name, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path, "r", encoding="utf-8") as f:
                scenarios.append(json.load(f))
    return jsonify({"scenarios": scenarios})
