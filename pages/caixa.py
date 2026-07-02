from flask import Blueprint, render_template, jsonify, request
from db import (db, get_biz_dates, get_company_filter,
                company_visible, get_company_names)

bp = Blueprint("caixa", __name__)

_NUM_DATES = 10


def _former_dates_batch(company_id, wallet_ids, date):
    """For each walletId in `wallet_ids`, return the most recent navPackage
    positionDate strictly before `date`. One aggregation instead of N
    `find_one(sort)` calls — uses the (companyId, positionDate) shape.

    Returns: {walletId: formerDate or None}.
    """
    out = {wid: None for wid in wallet_ids}
    if not wallet_ids:
        return out
    for doc in db.navPackages.aggregate([
        {"$match": {"companyId": company_id,
                    "walletId": {"$in": list(wallet_ids)},
                    "positionDate": {"$lt": date},
                    "trashed": {"$ne": True}}},
        {"$sort": {"positionDate": -1}},
        {"$group": {"_id": "$walletId", "formerDate": {"$first": "$positionDate"}}},
    ]):
        wid = str(doc["_id"])
        pd  = doc.get("formerDate")
        out[wid] = str(pd)[:10] if pd else None
    return out


def _cash_by_wallet_dates(wallet_ids, dates):
    """Single sweep of cashAccounts that returns `{walletId: {date: sum_or_None}}`.

    `cashAccounts` has only `(walletId,)` indexed (and lacks even that on
    older Atlas snapshots), so each per-wallet `find` is effectively a full
    collection scan. Folding N scans into one $in scan, then grouping in
    Python, is what makes a 80-wallet `caixa.analyze` cheap.
    """
    wanted_dates = {d[:10] for d in dates if d}
    out = {wid: {d: None for d in dates} for wid in wallet_ids}
    if not wallet_ids or not wanted_dates:
        return out
    sums  = {wid: {k: 0.0 for k in wanted_dates}  for wid in wallet_ids}
    found = {wid: {k: False for k in wanted_dates} for wid in wallet_ids}
    for doc in db.cashAccounts.find(
        {"walletId": {"$in": list(wallet_ids)}},
        {"walletId": 1, "values": 1},
    ):
        wid = str(doc.get("walletId", ""))
        if wid not in sums:
            continue
        for v in doc.get("values", []) or []:
            d_raw = v.get("date")
            if d_raw is None:
                continue
            k = str(d_raw)[:10]
            if k in wanted_dates:
                sums[wid][k]  += float(v.get("value") or 0)
                found[wid][k] = True
    for wid in wallet_ids:
        for d in dates:
            if not d:
                continue
            k = d[:10]
            out[wid][d] = sums[wid][k] if found[wid].get(k) else None
    return out


@bp.route("/caixa")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("caixa.html", companies=companies)


@bp.route("/api/caixa/dates")
def get_dates():
    company_id = request.args.get("companyId", "")
    if not company_visible(company_id):
        return jsonify({"cards": []})
    end_date = request.args.get("endDate") or None

    if not end_date:
        latest = db.navPackages.find_one(
            {"companyId": company_id, "trashed": {"$ne": True}},
            {"positionDate": 1},
            sort=[("positionDate", -1)],
        )
        if latest and latest.get("positionDate"):
            end_date = str(latest["positionDate"])[:10]

    dates = get_biz_dates(_NUM_DATES, end_date)

    totals = {}
    for doc in db.navPackages.aggregate([
        {"$match": {"companyId": company_id,
                    "positionDate": {"$in": dates}, "trashed": {"$ne": True}}},
        {"$group": {"_id": "$positionDate", "n": {"$sum": 1}}},
    ]):
        d = str(doc["_id"])[:10]
        if d:
            totals[d] = doc["n"]

    cards = [{"date": d, "total": totals.get(d, 0)} for d in dates]
    return jsonify({"cards": cards})


@bp.route("/api/caixa/analyze")
def analyze():
    """Analyze cash for all wallets of a company on a given date."""
    company_id = request.args.get("companyId", "")
    date = request.args.get("date", "")
    if not company_visible(company_id):
        return jsonify({"wallets": [], "date": date})

    wallets = list(db.wallets.find({"companyId": company_id}, {"name": 1}))
    wallet_ids = [str(w["_id"]) for w in wallets]
    wallet_names = {str(w["_id"]): w.get("name", str(w["_id"])) for w in wallets}

    # Only include wallets that have a navPackage on this date
    active_ids = set()
    for doc in db.navPackages.find(
        {"companyId": company_id, "walletId": {"$in": wallet_ids},
         "positionDate": date, "trashed": {"$ne": True}},
        {"walletId": 1},
    ):
        active_ids.add(doc["walletId"])

    # Pre-fetch all transaction totals per wallet on this date
    txn_totals = {}
    for doc in db.transactions.aggregate([
        {"$match": {"walletId": {"$in": list(active_ids)},
                    "liquidationDate": date,
                    "balance": {"$ne": None}}},
        {"$group": {"_id": "$walletId", "total": {"$sum": "$balance"}}},
    ]):
        txn_totals[doc["_id"]] = float(doc["total"])

    # Batch the per-wallet lookups: one aggregation for former dates, one
    # cashAccounts scan covering every (wallet, date) we care about.
    former_dates = _former_dates_batch(company_id, active_ids, date)
    cash_dates_per_wallet = {
        wid: [d for d in (former_dates.get(wid), date) if d]
        for wid in active_ids
    }
    all_dates = sorted({d for ds in cash_dates_per_wallet.values() for d in ds})
    cash_lookup = _cash_by_wallet_dates(list(active_ids), all_dates)

    rows = []
    for wid in sorted(active_ids, key=lambda x: wallet_names.get(x, x)):
        former_date  = former_dates.get(wid)
        former_cash  = cash_lookup.get(wid, {}).get(former_date) if former_date else None
        current_cash = cash_lookup.get(wid, {}).get(date)
        total_txns = txn_totals.get(wid, 0)
        projected_cash = (former_cash + total_txns) if former_cash is not None else None
        cash_diff = (
            round(projected_cash - current_cash, 2)
            if projected_cash is not None and current_cash is not None
            else None
        )

        rows.append({
            "walletId":      wid,
            "walletName":    wallet_names.get(wid, wid),
            "formerCash":    former_cash,
            "totalTxns":     total_txns,
            "projectedCash": projected_cash,
            "currentCash":   current_cash,
            "cashDiff":      cash_diff,
            "isAnomaly":     cash_diff is not None and round(cash_diff, 2) != 0,
        })

    return jsonify({"wallets": rows, "date": date})
