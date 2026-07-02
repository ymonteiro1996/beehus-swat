from flask import Blueprint, jsonify, request, redirect
from db import (db, get_biz_dates, load_config_full, load_nav_settings, wallet_filter_query,
                NAV_SETTINGS_FILE, biz_days_elapsed as _biz_days_elapsed,
                cell_cls as _cell_cls, wallet_cls as _wallet_cls,
                build_wallet_map as _build_wallet_map, atomic_write_json,
                get_company_names, get_entity_names, company_visible)

bp = Blueprint("nav", __name__)


def _safe_limit(raw, default=10, lo=1, hi=30):
    """Parse ?limit= safely: reject non-ints and clamp to [lo, hi].
    Unbounded / non-numeric values were a DoS lever — they drove huge
    get_biz_dates() loops and $in arrays on navPackages."""
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _count_navs(dates, wallet_to_pair, pairs):
    relevant_wids = [wid for wid, pair in wallet_to_pair.items() if pair in pairs]
    if not relevant_wids:
        return {}
    pair_wids = {}  # (pair, date) -> set of walletIds
    for doc in db.navPackages.aggregate([
        {"$match": {"positionDate": {"$in": dates}, "walletId": {"$in": relevant_wids}, "trashed": {"$ne": True}}},
        {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
    ]):
        wid  = str(doc["_id"]["w"])
        d    = str(doc["_id"]["d"])[:10]
        pair = wallet_to_pair.get(wid)
        if pair and pair in pairs:
            key = (pair, d)
            pair_wids.setdefault(key, set()).add(wid)
    return {k: len(v) for k, v in pair_wids.items()}


# ── Main page ──────────────────────────────────────────────────────────────────

@bp.route("/nav")
def index():
    return redirect("/")


@bp.route("/api/nav/rows")
def get_rows():
    limit         = _safe_limit(request.args.get("limit"))
    dates         = get_biz_dates(limit)
    company_names = get_company_names()
    entity_names  = get_entity_names()

    wallet_to_pair, pair_total = _build_wallet_map(load_nav_settings())

    pairs = set(wallet_to_pair.values())
    selected, delays, _, __ = load_config_full()
    if selected:
        pairs = pairs & selected
    # Enforce company visibility filter: drop pairs whose company is hidden
    # for this user. Without this, /api/nav/rows returns every company pair
    # regardless of the Painel "company_filter" setting.
    pairs = {(cid, eid) for (cid, eid) in pairs if company_visible(cid)}

    elapsed = {d: _biz_days_elapsed(d) for d in dates}
    counts  = _count_navs(dates, wallet_to_pair, pairs)

    rows = []
    for cid, eid in sorted(pairs, key=lambda p: (company_names.get(p[0], p[0]), entity_names.get(p[1], p[1]))):
        total = pair_total.get((cid, eid), 0)
        delay = delays.get((cid, eid), 0)
        cells = []
        for d in dates:
            count    = counts.get(((cid, eid), d), 0)
            expected = elapsed[d] >= delay
            cells.append({
                "label": f"{count}/{total}",
                "cls":   _cell_cls(count, total, expected),
            })
        rows.append({
            "companyId": cid,
            "entityId":  eid,
            "company":   company_names.get(cid, cid),
            "entity":    entity_names.get(eid, eid),
            "delay":     delay,
            "cells":     cells,
        })

    return jsonify({"rows": rows, "dates": dates})


@bp.route("/api/nav/detail")
def get_detail():
    cid = request.args.get("companyId")
    eid = request.args.get("entityId")
    d   = request.args.get("date")
    if not company_visible(cid):
        return jsonify({"detail": [], "date": d})

    wq = {"companyId": cid, "entityId": eid, **wallet_filter_query(load_nav_settings())}
    wallets = {
        str(w["_id"]): {"name": w.get("name", str(w["_id"])), "accountCode": w.get("accountCode", "")}
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1})
    }

    wids_with_nav = {
        str(doc["walletId"])
        for doc in db.navPackages.find(
            {"walletId": {"$in": list(wallets)}, "positionDate": d, "trashed": {"$ne": True}},
            {"walletId": 1}
        )
    }

    detail = sorted([
        {
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "count":       1 if wid in wids_with_nav else 0,
            "cls":         _wallet_cls(wid in wids_with_nav),
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"detail": detail, "date": d})


@bp.route("/api/nav/detail-grid")
def get_nav_detail_grid():
    cid   = request.args.get("companyId")
    eid   = request.args.get("entityId")
    limit = _safe_limit(request.args.get("limit"))
    dates = get_biz_dates(limit)
    if not company_visible(cid):
        return jsonify({"rows": [], "dates": dates})

    wq = {"companyId": cid, "entityId": eid, **wallet_filter_query(load_nav_settings())}
    wallets = {
        str(w["_id"]): {"name": w.get("name", str(w["_id"])), "accountCode": w.get("accountCode", "")}
        for w in db.wallets.find(wq, {"name": 1, "accountCode": 1})
    }

    wids_by_date = {d: set() for d in dates}
    for doc in db.navPackages.aggregate([
        {"$match": {"walletId": {"$in": list(wallets)}, "positionDate": {"$in": dates}, "trashed": {"$ne": True}}},
        {"$group": {"_id": {"w": "$walletId", "d": "$positionDate"}}},
    ]):
        d = str(doc["_id"]["d"])[:10]
        if d in wids_by_date:
            wids_by_date[d].add(str(doc["_id"]["w"]))

    rows = sorted([
        {
            "name":        wallets[wid]["name"],
            "accountCode": wallets[wid]["accountCode"],
            "cells": [
                {"label": "✓" if wid in wids_by_date[d] else "—",
                 "cls":   _wallet_cls(wid in wids_by_date[d])}
                for d in dates
            ],
        }
        for wid in wallets
    ], key=lambda x: x["name"])

    return jsonify({"rows": rows, "dates": dates})


# ── Settings ───────────────────────────────────────────────────────────────────

@bp.route("/api/nav/settings/load")
def nav_settings_load():
    return jsonify(load_nav_settings())


@bp.route("/api/nav/settings/save", methods=["POST"])
def nav_settings_save():
    current = load_nav_settings()
    data    = request.get_json() or {}
    if "only_daily_position"   in data: current["only_daily_position"]   = bool(data["only_daily_position"])
    if "only_with_consumption" in data: current["only_with_consumption"] = bool(data["only_with_consumption"])
    atomic_write_json(NAV_SETTINGS_FILE, current)
    return jsonify({"ok": True})
