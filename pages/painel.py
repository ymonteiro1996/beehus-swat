from flask import Blueprint, render_template, jsonify, request
from db import db, get_biz_dates, get_company_filter, company_visible, get_company_names

bp = Blueprint("painel", __name__)

_NUM_DATES = 10


@bp.route("/painel")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("painel.html", companies=companies)


@bp.route("/api/painel/dates")
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



@bp.route("/api/painel/wallet-names")
def wallet_names():
    company_id = request.args.get("companyId", "")
    if not company_visible(company_id):
        return jsonify({})
    names = {
        str(w["_id"]): w.get("name", str(w["_id"]))
        for w in db.wallets.find({"companyId": company_id}, {"name": 1})
    }
    return jsonify(names)


