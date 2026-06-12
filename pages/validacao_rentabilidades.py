from flask import Blueprint, render_template, jsonify, request
from db import (db, get_biz_dates, get_company_filter, atomic_write_json,
                company_visible, get_company_names, resolve_wallet)
import json, os, statistics

bp = Blueprint("validacao_rentabilidades", __name__)

_NUM_DATES = 10
THRESHOLDS_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "rentability_thresholds.json")

# pricingType cujo preço é de mercado (idêntico em todas as carteiras): basta
# calcular/coletar 1x por securityId. Contrasta com C3 (curva), que é por walletId.
_ASSET_LEVEL_PRICING_TYPES = {"B1"}

# pricingType cujo preço é de curva (específico por carteira): o mesmo ativo pode
# ter vários PUs/rentabilidades numa data, um por carteira → analisar dispersão.
_CURVE_PRICING_TYPES = {"C3"}


def _rentab(sec, former):
    """rentabPU e rentabContribution de uma security vs sua posição anterior.

    `former` = {"pu": .., "quantity": ..} (pode ser {} se não houver posição
    anterior). Retorna (ret_pu, ret_c); cada um é None quando não calculável.
    """
    pu      = sec.get("pu")
    qty     = sec.get("quantity")
    event_c = sec.get("eventContribution") or 0
    total_c = sec.get("totalContribution")
    f_pu    = former.get("pu")
    f_qty   = former.get("quantity")
    f_bal   = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None

    try:
        event_per_unit = (event_c / qty) if (qty and qty != 0) else 0
        ret_pu = round((pu + event_per_unit) / f_pu - 1, 8) if (pu and f_pu) else None
    except (TypeError, ZeroDivisionError):
        ret_pu = None
    try:
        ret_c = round(total_c / f_bal, 8) if (total_c is not None and f_bal) else None
    except (TypeError, ZeroDivisionError):
        ret_c = None
    return ret_pu, ret_c


def _load_thresholds():
    if not os.path.exists(THRESHOLDS_FILE):
        return {}
    with open(THRESHOLDS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_thresholds(data):
    atomic_write_json(THRESHOLDS_FILE, data)


# ── Routes ─────────────────────────────────────────────────────────────────────

@bp.route("/validacao-rentabilidades")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("validacao_rentabilidades.html", companies=companies)


@bp.route("/api/validacao-rentabilidades/dates")
def get_dates():
    company_id = request.args.get("companyId", "")
    end_date   = request.args.get("endDate") or None
    if not company_visible(company_id):
        return jsonify({"cards": []})

    # processedPosition has no companyId field; we must restrict by walletId.
    # The previous `_valid_wallet_ids()` global-wallet-IN was both bloated
    # AND under-scoped — it counted positions across every company. Pull the
    # current company's wallet list once and reuse it below.
    company_wallets = [str(w["_id"]) for w in db.wallets.find({"companyId": company_id}, {"_id": 1})]

    # Auto-detect most recent date from navPackages
    if not end_date:
        latest = db.navPackages.find_one(
            {"companyId": company_id, "trashed": {"$ne": True}},
            {"positionDate": 1},
            sort=[("positionDate", -1)]
        )
        if latest and latest.get("positionDate"):
            end_date = str(latest["positionDate"])[:10]

    dates = get_biz_dates(_NUM_DATES, end_date)

    # Count wallets with processedPosition per date
    totals = {}
    for doc in db.processedPosition.aggregate([
        {"$match": {"walletId": {"$in": company_wallets}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": "$positionDate", "n": {"$sum": 1}}},
    ]):
        d = str(doc["_id"])[:10]
        if d:
            totals[d] = doc["n"]

    cards = [{"date": d, "total": totals.get(d, 0)} for d in dates]
    return jsonify({"cards": cards})


@bp.route("/api/validacao-rentabilidades/securities")
def get_securities():
    """Return rentability data for all securities across all wallets for a company+date."""
    company_id = request.args.get("companyId", "")
    date       = request.args.get("date", "")
    if not company_visible(company_id):
        return jsonify({"securities": [], "thresholdsAvailable": False})

    # Get all navPackages for this company+date to know which wallets have data
    nav_wallets = set()
    for doc in db.navPackages.find(
        {"companyId": company_id,
         "positionDate": date, "trashed": {"$ne": True}},
        {"walletId": 1}
    ):
        nav_wallets.add(str(doc["walletId"]))

    if not nav_wallets:
        return jsonify({"securities": [], "thresholdsAvailable": False})

    # Load thresholds
    thresholds = _load_thresholds()

    # Fetch all processedPositions for these wallets on this date
    results = []
    # B1 (market price) is identical across wallets — emit one row per securityId
    # using the first wallet that holds it; subsequent wallets are skipped.
    seen_asset_secs = set()
    for pos_doc in db.processedPosition.find(
        {"walletId": {"$in": list(nav_wallets)}, "positionDate": date},
        {"walletId": 1, "securities": 1}
    ):
        wallet_id = str(pos_doc.get("walletId", ""))

        # Get former position
        former_doc = db.processedPosition.find_one(
            {"walletId": wallet_id, "positionDate": {"$lt": date}},
            {"securities": 1, "positionDate": 1},
            sort=[("positionDate", -1)]
        )
        former_date = str(former_doc.get("positionDate", ""))[:10] if former_doc else None
        former_map = {}
        for s in (former_doc or {}).get("securities", []):
            sid = str(s.get("securityId", ""))
            former_map[sid] = {"pu": s.get("pu"), "quantity": s.get("quantity")}

        for sec in pos_doc.get("securities", []):
            sid          = str(sec.get("securityId", ""))
            pricing_type = sec.get("pricingType", "")

            # Market-priced (B1) securities are wallet-independent: keep only the
            # first wallet's record and skip the redundant duplicates.
            if pricing_type in _ASSET_LEVEL_PRICING_TYPES:
                if sid in seen_asset_secs:
                    continue
                seen_asset_secs.add(sid)

            event_c = sec.get("eventContribution") or 0
            ret_pu, ret_c = _rentab(sec, former_map.get(sid, {}))

            # Threshold
            sec_threshold = thresholds.get(sid)
            is_anomaly = False
            if sec_threshold and ret_pu is not None:
                lb = sec_threshold.get("lowerBound")
                ub = sec_threshold.get("upperBound")
                if lb is not None and ub is not None:
                    is_anomaly = ret_pu < lb or ret_pu > ub

            results.append({
                "walletId":           wallet_id,
                "securityId":         sid,
                "mainId":             sec.get("mainId", ""),
                "beehusName":         sec.get("beehusName", ""),
                "pricingType":        pricing_type,
                "formerDate":         former_date,
                "eventContribution":  event_c,
                "rentabPU":           ret_pu,
                "rentabContribution": ret_c,
                "threshold":          sec_threshold,
                "isAnomaly":          is_anomaly,
            })

    # Sort: anomalies first, then by name
    results.sort(key=lambda r: (not r["isAnomaly"], r["beehusName"]))

    return jsonify({
        "securities":          results,
        "thresholdsAvailable": bool(thresholds),
    })


@bp.route("/api/validacao-rentabilidades/c3-dispersion")
def c3_dispersion():
    """Cross-sectional dispersion of C3 (curve-priced) securities for one date.

    A C3 price is per (security, wallet), so the same security can have several
    PUs / rentabilities on a date. For each securityId we compute the mean and
    stddev of rentabPU and rentabContribution *across wallets* and flag wallets
    whose value deviates by >= `sigma` standard deviations. No history, no
    persisted thresholds — it is a within-date comparison.
    """
    company_id = request.args.get("companyId", "")
    date       = request.args.get("date", "")
    try:
        sigma = float(request.args.get("sigma", 3.0))
    except (TypeError, ValueError):
        sigma = 3.0
    sigma = max(0.5, min(sigma, 10.0))
    if not company_visible(company_id):
        return jsonify({"securities": [], "sigma": sigma})

    nav_wallets = set()
    for doc in db.navPackages.find(
        {"companyId": company_id, "positionDate": date, "trashed": {"$ne": True}},
        {"walletId": 1}
    ):
        nav_wallets.add(str(doc["walletId"]))

    if not nav_wallets:
        return jsonify({"securities": [], "sigma": sigma})

    # Group C3 securities by securityId, collecting one entry per wallet.
    groups = {}
    for pos_doc in db.processedPosition.find(
        {"walletId": {"$in": list(nav_wallets)}, "positionDate": date},
        {"walletId": 1, "securities": 1}
    ):
        wallet_id = str(pos_doc.get("walletId", ""))

        former_doc = db.processedPosition.find_one(
            {"walletId": wallet_id, "positionDate": {"$lt": date}},
            {"securities": 1, "positionDate": 1},
            sort=[("positionDate", -1)]
        )
        former_date = str(former_doc.get("positionDate", ""))[:10] if former_doc else None
        former_map = {
            str(s.get("securityId", "")): {"pu": s.get("pu"), "quantity": s.get("quantity")}
            for s in (former_doc or {}).get("securities", [])
        }

        for sec in pos_doc.get("securities", []):
            if sec.get("pricingType", "") not in _CURVE_PRICING_TYPES:
                continue
            sid = str(sec.get("securityId", ""))
            ret_pu, ret_c = _rentab(sec, former_map.get(sid, {}))
            g = groups.setdefault(sid, {
                "securityId": sid,
                "beehusName": sec.get("beehusName", ""),
                "mainId":     sec.get("mainId", ""),
                "wallets":    [],
            })
            g["wallets"].append({
                "walletId":           wallet_id,
                "formerDate":         former_date,
                "rentabPU":           ret_pu,
                "rentabContribution": ret_c,
            })

    # Per-group statistics + per-wallet z-scores.
    securities = []
    for g in groups.values():
        wallets = g["wallets"]
        pu_vals = [w["rentabPU"] for w in wallets if w["rentabPU"] is not None]
        c_vals  = [w["rentabContribution"] for w in wallets if w["rentabContribution"] is not None]

        mean_pu = statistics.mean(pu_vals) if pu_vals else None
        std_pu  = statistics.stdev(pu_vals) if len(pu_vals) >= 2 else 0.0
        mean_c  = statistics.mean(c_vals) if c_vals else None
        std_c   = statistics.stdev(c_vals) if len(c_vals) >= 2 else 0.0

        has_outlier = False
        for w in wallets:
            z_pu = (w["rentabPU"] - mean_pu) / std_pu if (w["rentabPU"] is not None and std_pu > 0) else 0.0
            z_c  = (w["rentabContribution"] - mean_c) / std_c if (w["rentabContribution"] is not None and std_c > 0) else 0.0
            out_pu = abs(z_pu) >= sigma
            out_c  = abs(z_c) >= sigma
            w["zPU"]              = round(z_pu, 4)
            w["zContrib"]         = round(z_c, 4)
            w["isOutlierPU"]      = out_pu
            w["isOutlierContrib"] = out_c
            w["isOutlier"]        = out_pu or out_c
            has_outlier = has_outlier or w["isOutlier"]

        # Worst (largest |z|) wallet first within the security.
        wallets.sort(key=lambda w: max(abs(w["zPU"]), abs(w["zContrib"])), reverse=True)

        securities.append({
            "securityId":  g["securityId"],
            "beehusName":  g["beehusName"],
            "mainId":      g["mainId"],
            "count":       len(pu_vals),
            "meanPU":      round(mean_pu, 8) if mean_pu is not None else None,
            "stdPU":       round(std_pu, 8),
            "meanContrib": round(mean_c, 8) if mean_c is not None else None,
            "stdContrib":  round(std_c, 8),
            "hasOutlier":  has_outlier,
            "wallets":     wallets,
        })

    # Securities with outliers first, then by name.
    securities.sort(key=lambda s: (not s["hasOutlier"], s["beehusName"]))

    return jsonify({"securities": securities, "sigma": sigma})


@bp.route("/api/validacao-rentabilidades/security-detail")
def security_detail():
    """Return detailed position info for a security on current and former dates."""
    wallet_id   = request.args.get("walletId", "")
    security_id = request.args.get("securityId", "")
    date        = request.args.get("date", "")

    # Enforce company_filter at the wallet boundary so a user without
    # visibility into this wallet's company cannot read its PU history by
    # guessing walletIds. Mirrors _require_visible_wallet in conciliacao.
    wallet = resolve_wallet(wallet_id, {"companyId": 1}) if wallet_id else None
    company_id = str(wallet["companyId"]) if wallet else ""
    if not company_id or not company_visible(company_id):
        return jsonify({"error": "acesso negado"}), 403

    # Current position
    pos_doc = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": date},
        {"securities": 1}
    )
    current_sec = None
    for s in (pos_doc or {}).get("securities", []):
        if str(s.get("securityId", "")) == security_id:
            current_sec = s
            break

    # Former position
    former_doc = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": {"$lt": date}},
        {"securities": 1, "positionDate": 1},
        sort=[("positionDate", -1)]
    )
    former_date = str(former_doc.get("positionDate", ""))[:10] if former_doc else None
    former_sec  = None
    for s in (former_doc or {}).get("securities", []):
        if str(s.get("securityId", "")) == security_id:
            former_sec = s
            break

    # Last 10 PUs: look at the last 11 processedPositions (need n+1 to compute rentab for n)
    raw_history = []
    for hist_doc in db.processedPosition.find(
        {"walletId": wallet_id, "positionDate": {"$lte": date}},
        {"securities": 1, "positionDate": 1}
    ).sort("positionDate", -1).limit(11):
        hist_date = str(hist_doc.get("positionDate", ""))[:10]
        for s in hist_doc.get("securities", []):
            if str(s.get("securityId", "")) == security_id:
                raw_history.append({
                    "date":              hist_date,
                    "pu":                s.get("pu"),
                    "quantity":          s.get("quantity"),
                    "eventContribution": s.get("eventContribution") or 0,
                    "totalContribution": s.get("totalContribution"),
                })
                break

    # Compute rentabilities (need former row for each)
    pu_history = []
    for i in range(len(raw_history) - 1):
        cur = raw_history[i]
        fmr = raw_history[i + 1]
        pu      = cur["pu"]
        f_pu    = fmr["pu"]
        qty     = cur["quantity"]
        f_qty   = fmr["quantity"]
        event_c = cur["eventContribution"]
        total_c = cur["totalContribution"]
        f_bal   = round(f_pu * f_qty, 6) if (f_pu is not None and f_qty is not None) else None

        try:
            event_per_unit = (event_c / qty) if (qty and qty != 0) else 0
            ret_pu = round((pu + event_per_unit) / f_pu - 1, 8) if (pu and f_pu) else None
        except (TypeError, ZeroDivisionError):
            ret_pu = None
        try:
            ret_c = round(total_c / f_bal, 8) if (total_c is not None and f_bal) else None
        except (TypeError, ZeroDivisionError):
            ret_c = None

        pu_history.append({
            "date":              cur["date"],
            "pu":                pu,
            "quantity":          qty,
            "rentabPU":          ret_pu,
            "rentabContribution": ret_c,
        })

    def _sec_to_dict(sec, label):
        if not sec:
            return None
        return {
            "label":              label,
            "pu":                 sec.get("pu"),
            "quantity":           sec.get("quantity"),
            "executionPrice":     sec.get("executionPrice"),
            "totalContribution":  sec.get("totalContribution"),
            "dailyContribution":  sec.get("dailyContribution"),
            "intradayContribution": sec.get("intradayContribution"),
            "eventContribution":  sec.get("eventContribution"),
            "pricingType":        sec.get("pricingType"),
            "mainId":             sec.get("mainId"),
        }

    return jsonify({
        "current":    _sec_to_dict(current_sec, date),
        "former":     _sec_to_dict(former_sec, former_date),
        "puHistory":  pu_history,
    })


@bp.route("/api/validacao-rentabilidades/calculate-thresholds", methods=["POST"])
def calculate_thresholds():
    """Calculate 3-sigma thresholds for all securities based on historical rentabilities."""
    data       = request.get_json() or {}
    company_id = data.get("companyId", "")
    # numDays goes straight into a Mongo $slice; without bounds, a request like
    # {"numDays": 1_000_000_000} produces an OOM-sized array and a non-int
    # raises mid-pipeline. Clamp to a sensible window.
    try:
        num_days = int(data.get("numDays", 60))
    except (TypeError, ValueError):
        num_days = 60
    num_days = max(1, min(num_days, 365))
    if not company_visible(company_id):
        return jsonify({"error": "Empresa não visível neste filtro", "count": 0}), 403

    # Get wallets for this company
    nav_wallets = set()
    for doc in db.navPackages.find(
        {"companyId": company_id, "trashed": {"$ne": True}},
        {"walletId": 1}
    ):
        nav_wallets.add(str(doc["walletId"]))

    if not nav_wallets:
        return jsonify({"error": "Nenhuma carteira encontrada", "count": 0}), 404

    # Get the last num_days positions for each wallet, collect rentabilities per securityId.
    # Replaces a per-wallet find().sort().limit() loop (N+1 against processedPosition)
    # with a single aggregation that sort-groups-slices by wallet. Uses the existing
    # (walletId, positionDate) compound index for the initial sort.
    rentab_by_sec = {}  # {securityId: [rentabPU values]}
    # B1 returns are identical across wallets on a given date — count each
    # (securityId, date) once so the sample isn't inflated N× (which would
    # shrink the stddev and produce artificially tight bounds).
    seen_asset_returns = set()

    pipeline = [
        {"$match": {"walletId": {"$in": list(nav_wallets)}}},
        {"$sort":  {"walletId": 1, "positionDate": -1}},
        {"$group": {
            "_id":       "$walletId",
            "positions": {"$push": {"securities": "$securities", "positionDate": "$positionDate"}},
        }},
        {"$project": {"positions": {"$slice": ["$positions", num_days + 1]}}},
    ]

    for wallet_doc in db.processedPosition.aggregate(pipeline, allowDiskUse=True):
        positions = wallet_doc.get("positions") or []

        # Build a timeline of {securityId: {date: pu}} to compute returns
        for i in range(len(positions) - 1):
            current = positions[i]
            former  = positions[i + 1]
            current_date = str(current.get("positionDate", ""))[:10]

            former_map = {
                str(s.get("securityId", "")): {"pu": s.get("pu"), "quantity": s.get("quantity")}
                for s in former.get("securities", [])
            }

            for sec in current.get("securities", []):
                sid          = str(sec.get("securityId", ""))
                pricing_type = sec.get("pricingType", "")
                pu           = sec.get("pu")
                qty          = sec.get("quantity")
                event_c      = sec.get("eventContribution") or 0

                f    = former_map.get(sid, {})
                f_pu = f.get("pu")

                if not pu or not f_pu:
                    continue

                # Market-priced (B1): one return per (securityId, date) across all
                # wallets. Skip if already recorded for this date.
                is_asset_level = pricing_type in _ASSET_LEVEL_PRICING_TYPES
                if is_asset_level and (sid, current_date) in seen_asset_returns:
                    continue

                try:
                    event_per_unit = (event_c / qty) if (qty and qty != 0) else 0
                    ret_pu = (pu + event_per_unit) / f_pu - 1
                except (TypeError, ZeroDivisionError):
                    continue

                # Mark only after a successful computation so a failed calc doesn't
                # consume the slot and discard a valid return from another wallet.
                if is_asset_level:
                    seen_asset_returns.add((sid, current_date))

                rentab_by_sec.setdefault(sid, []).append(ret_pu)

    # Calculate thresholds
    thresholds = {}
    for sid, returns in rentab_by_sec.items():
        if len(returns) < 3:
            continue
        mean   = statistics.mean(returns)
        stddev = statistics.stdev(returns)
        thresholds[sid] = {
            "mean":       round(mean, 10),
            "stdDev":     round(stddev, 10),
            "lowerBound": round(mean - 3 * stddev, 10),
            "upperBound": round(mean + 3 * stddev, 10),
            "sampleSize": len(returns),
        }

    _save_thresholds(thresholds)

    return jsonify({"ok": True, "count": len(thresholds)})
