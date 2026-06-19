from flask import Blueprint, render_template, jsonify, request
from db import db, get_biz_dates, get_company_filter, company_visible, get_company_names, get_entity_names, resolve_wallet
import math

bp = Blueprint("posicoes", __name__)

_NUM_DATES = 10


def _safe_num(v):
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    return v


def _build_mapping(company_id):
    """Return {unprocessedId: securityId} for the given company."""
    doc = db.securityMappings.find_one({"companyId": company_id}, {"mappings": 1})
    if not doc:
        return {}
    return {m["from"]: m["to"] for m in doc.get("mappings", []) if m.get("from") and m.get("to")}


def _group_unprocessed(securities):
    """Group securities by unprocessedId: sum quantity & balance, weighted-avg PU."""
    grouped = {}  # unprocessedId -> {quantity, balance}
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
    # Compute weighted-average PU = balance / quantity
    for g in grouped.values():
        if g["quantity"] != 0:
            g["pu"] = g["balance"] / g["quantity"]
        else:
            g["pu"] = 0
    return list(grouped.values())


def _security_names(security_ids):
    """Return {securityId_str: beehusName}."""
    from bson import ObjectId
    oids = []
    for s in security_ids:
        try:
            oids.append(ObjectId(s))
        except Exception:
            pass
    if not oids:
        return {}
    return {str(d["_id"]): d.get("beehusName", str(d["_id"]))
            for d in db.securities.find({"_id": {"$in": oids}}, {"beehusName": 1})}


# ── Routes ────────────────────────────────────────────────────────────────────

@bp.route("/posicoes")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]

    entities = sorted(
        [{"id": eid, "name": name or eid} for eid, name in get_entity_names().items()],
        key=lambda e: e["name"],
    )
    return render_template("posicoes.html", companies=companies, entities=entities)


@bp.route("/api/posicoes/dates")
def get_dates():
    """Return date pills with count of flagged wallets per date."""
    company_id = request.args.get("companyId", "")
    entity_id = request.args.get("entityId", "")
    end_date = request.args.get("endDate") or None

    if not company_visible(company_id):
        return jsonify({"cards": []})

    # Resolve wallets for the filter
    wq = {}
    if company_id:
        wq["companyId"] = company_id
    if entity_id:
        wq["entityId"] = entity_id
    if not wq:
        return jsonify({"cards": []})

    wallet_ids = [str(w["_id"]) for w in db.wallets.find(wq, {"_id": 1})]
    if not wallet_ids:
        return jsonify({"cards": []})

    # Find the most recent date if not provided (based on processedPosition)
    if not end_date:
        latest = db.processedPosition.find_one(
            {"walletId": {"$in": wallet_ids}},
            {"positionDate": 1},
            sort=[("positionDate", -1)],
        )
        if latest and latest.get("positionDate"):
            end_date = str(latest["positionDate"])[:10]

    dates = get_biz_dates(_NUM_DATES, end_date)

    # Count wallets that have a processedPosition on each date
    totals = {}
    for doc in db.processedPosition.aggregate([
        {"$match": {"walletId": {"$in": wallet_ids}, "positionDate": {"$in": dates}}},
        {"$group": {"_id": "$positionDate", "n": {"$sum": 1}}},
    ]):
        d = str(doc["_id"])[:10]
        if d:
            totals[d] = doc["n"]

    cards = [{"date": d, "total": totals.get(d, 0)} for d in dates]
    return jsonify({"cards": cards})


@bp.route("/api/posicoes/analyze")
def analyze():
    """Compare unprocessed vs processed for all wallets on a given date.

    Returns a list of wallets with at least one security exceeding the tolerance.
    """
    company_id = request.args.get("companyId", "")
    entity_id = request.args.get("entityId", "")
    date = request.args.get("date", "")
    if not company_visible(company_id):
        return jsonify({"wallets": [], "date": date})
    try:
        tol_qty = float(request.args.get("tolQty", "0.0001"))
    except ValueError:
        tol_qty = 0.0001
    try:
        tol_pu = float(request.args.get("tolPu", "0.01"))
    except ValueError:
        tol_pu = 0.01

    if not date:
        return jsonify({"wallets": []})

    # Resolve wallets
    wq = {}
    if company_id:
        wq["companyId"] = company_id
    if entity_id:
        wq["entityId"] = entity_id
    if not wq:
        return jsonify({"wallets": []})

    wallet_map = {str(w["_id"]): w.get("name", str(w["_id"]))
                  for w in db.wallets.find(wq, {"name": 1})}
    wallet_ids = list(wallet_map.keys())
    if not wallet_ids:
        return jsonify({"wallets": []})

    # Get the company for this set of wallets (needed for securityMappings)
    # If company_id is provided use it; otherwise derive from wallets
    if not company_id:
        sample_w = db.wallets.find_one({"_id": {"$in": wallet_ids}}, {"companyId": 1})
        company_id = str(sample_w["companyId"]) if sample_w else ""
    if not company_id:
        return jsonify({"wallets": []})

    mapping = _build_mapping(company_id)

    # Fetch all unprocessed positions for these wallets on this date
    # Group by unprocessedId: sum qty & balance, weighted-avg PU
    unprocessed_by_wallet = {}
    for doc in db.unprocessedSecurityPositions.find(
        {"walletId": {"$in": wallet_ids}, "positionDate": date},
        {"walletId": 1, "securities": 1},
    ):
        wid = str(doc["walletId"])
        unprocessed_by_wallet[wid] = _group_unprocessed(doc.get("securities", []))

    # Fetch all processed positions for these wallets on this date
    processed_by_wallet = {}
    for doc in db.processedPosition.find(
        {"walletId": {"$in": wallet_ids}, "positionDate": date},
        {"walletId": 1, "securities": 1},
    ):
        wid = str(doc["walletId"])
        processed_by_wallet[wid] = doc.get("securities", [])

    # Compare — only wallets that have a processedPosition
    flagged_wallets = []
    for wid in wallet_ids:
        if wid not in processed_by_wallet:
            continue

        unp_secs = unprocessed_by_wallet.get(wid, [])
        proc_secs = processed_by_wallet[wid]

        # Build processed lookup: securityId -> sec data
        proc_map = {}
        for s in proc_secs:
            sid = str(s.get("securityId", ""))
            if sid:
                proc_map[sid] = s

        flags = 0
        total_unp = len(unp_secs)
        total_proc = len(proc_secs)

        for us in unp_secs:
            uid = us.get("unprocessedId", "")
            sec_id = mapping.get(uid)
            if not sec_id:
                flags += 1  # unmapped = flag
                continue
            ps = proc_map.get(sec_id)
            if not ps:
                flags += 1  # missing in processed = flag
                continue

            u_qty = us.get("quantity") or 0
            u_pu = us.get("pu") or 0

            p_qty = ps.get("quantity") or 0
            p_pu = ps.get("pu") or 0

            if (abs(u_qty - p_qty) > tol_qty
                    or abs(u_pu - p_pu) > tol_pu):
                flags += 1

        if flags > 0:
            flagged_wallets.append({
                "walletId": wid,
                "walletName": wallet_map.get(wid, wid),
                "flags": flags,
                "totalUnprocessed": total_unp,
                "totalProcessed": total_proc,
            })

    flagged_wallets.sort(key=lambda x: x["walletName"])
    return jsonify({"wallets": flagged_wallets, "date": date})


@bp.route("/api/posicoes/wallet-detail")
def wallet_detail():
    """Return security-level comparison for a single wallet on a date."""
    wallet_id = request.args.get("walletId", "")
    date = request.args.get("date", "")
    try:
        tol_qty = float(request.args.get("tolQty", "0.0001"))
    except ValueError:
        tol_qty = 0.0001
    try:
        tol_pu = float(request.args.get("tolPu", "0.01"))
    except ValueError:
        tol_pu = 0.01

    if not wallet_id or not date:
        return jsonify({"securities": []})

    # Get company from wallet
    wallet = resolve_wallet(wallet_id, {"companyId": 1, "name": 1})
    company_id = str(wallet["companyId"]) if wallet else ""

    # Enforce company_filter: a user whose settings exclude this wallet's
    # company must not be able to read security-level positions by guessing
    # walletIds. Mirrors _require_visible_wallet in pages/conciliacao.py.
    if not company_id or not company_visible(company_id):
        return jsonify({"error": "acesso negado"}), 403

    mapping = _build_mapping(company_id) if company_id else {}

    # Unprocessed — grouped by unprocessedId
    unp_doc = db.unprocessedSecurityPositions.find_one(
        {"walletId": wallet_id, "positionDate": date},
        {"securities": 1},
    )
    unp_secs = _group_unprocessed((unp_doc or {}).get("securities", []))

    # Processed
    proc_doc = db.processedPosition.find_one(
        {"walletId": wallet_id, "positionDate": date},
        {"securities": 1},
    )
    proc_secs = (proc_doc or {}).get("securities", [])

    # Build processed lookup
    proc_map = {}
    for s in proc_secs:
        sid = str(s.get("securityId", ""))
        if sid:
            proc_map[sid] = s

    # Collect all security IDs for name resolution
    all_sec_ids = set(proc_map.keys())
    for us in unp_secs:
        sid = mapping.get(us.get("unprocessedId", ""))
        if sid:
            all_sec_ids.add(sid)
    sec_names = _security_names(all_sec_ids)

    rows = []
    matched_proc_ids = set()

    for us in unp_secs:
        uid = us.get("unprocessedId", "")
        sec_id = mapping.get(uid)
        u_qty = us.get("quantity") or 0
        u_pu = us.get("pu") or 0
        u_bal = us.get("balance") if us.get("balance") is not None else u_pu * u_qty

        p_qty = p_pu = p_bal = None
        pricing_type = None
        sec_name = uid  # fallback to unprocessedId

        if sec_id:
            matched_proc_ids.add(sec_id)
            sec_name = sec_names.get(sec_id, sec_id)
            ps = proc_map.get(sec_id)
            if ps:
                p_qty = ps.get("quantity") or 0
                p_pu = ps.get("pu") or 0
                p_bal = p_pu * p_qty
                pricing_type = ps.get("pricingType")

        diff_qty = round(u_qty - (p_qty or 0), 6) if p_qty is not None else None
        diff_pu = round(u_pu - (p_pu or 0), 8) if p_pu is not None else None

        flag_qty = abs(diff_qty) > tol_qty if diff_qty is not None else False
        flag_pu = abs(diff_pu) > tol_pu if diff_pu is not None else False

        has_flag = flag_qty or flag_pu or sec_id is None or (sec_id and sec_id not in proc_map)

        rows.append({
            "unprocessedId": uid,
            "securityId": sec_id,
            "securityName": sec_name,
            "pricingType": pricing_type,
            "unp_qty": _safe_num(u_qty),
            "unp_pu": _safe_num(u_pu),
            "unp_bal": _safe_num(round(u_bal, 2)),
            "proc_qty": _safe_num(p_qty),
            "proc_pu": _safe_num(p_pu),
            "proc_bal": _safe_num(round(p_bal, 2)) if p_bal is not None else None,
            "diff_qty": _safe_num(diff_qty),
            "diff_pu": _safe_num(diff_pu),
            "flag_qty": flag_qty,
            "flag_pu": flag_pu,
            "status": "unmapped" if sec_id is None else ("missing_processed" if sec_id not in proc_map else ("flagged" if has_flag else "ok")),
        })

    # Securities present in processed but NOT matched from unprocessed
    for sid, ps in proc_map.items():
        if sid in matched_proc_ids:
            continue
        p_qty = ps.get("quantity") or 0
        p_pu = ps.get("pu") or 0
        p_bal = p_pu * p_qty
        rows.append({
            "unprocessedId": None,
            "securityId": sid,
            "securityName": sec_names.get(sid, sid),
            "pricingType": ps.get("pricingType"),
            "unp_qty": None,
            "unp_pu": None,
            "unp_bal": None,
            "proc_qty": _safe_num(p_qty),
            "proc_pu": _safe_num(p_pu),
            "proc_bal": _safe_num(round(p_bal, 2)),
            "diff_qty": None,
            "diff_pu": None,
            "flag_qty": False,
            "flag_pu": False,
            "status": "only_processed",
        })

    rows.sort(key=lambda r: (0 if r["status"] in ("flagged", "unmapped", "missing_processed") else 1, r["securityName"]))
    return jsonify({"securities": rows, "walletName": wallet.get("name", wallet_id) if wallet else wallet_id})
