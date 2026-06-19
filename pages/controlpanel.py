import io
import json
import os
import threading
from datetime import date

from bson import ObjectId
from flask import Blueprint, render_template, jsonify, make_response, request
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from beehus_api import (
    BeehusAPIError,
    BeehusAuthError,
    update_security_mappings,
)
from db import (db, get_biz_dates, load_config_delays, get_company_filter,
                company_visible, get_company_names, get_wallet_names,
                get_grouping_index, atomic_write_json, today_in_brt, _cached_ttl)
from security_type_classifier import SecurityTypeClassifier, JSON_PATH
from security_matcher import (
    SecurityMatcher, get_cache, _score_breakdown, _confidence_label,
)

bp = Blueprint("controlpanel", __name__)

# Number of business-day cards rendered in the date strip at the top of /controlpanel.
# Kept in sync with the inline grid (`grid-template-columns: repeat(N, ...)`)
# in templates/controlpanel.html — change both together. Matches the value used by
# sibling pages (painel, posicoes, caixa, conciliacao, etc.).
_NUM_DATES = 10

# Guards lazy init of _clf and _matcher under multi-threaded WSGI: without
# this, concurrent first-loads each retrain a fresh sklearn model and
# refresh_cache/rebuild reassignments race with in-flight match requests.
# MUST be reentrant (RLock): _get_matcher() holds this lock while building the
# SecurityMatcher, which calls _get_classifier() — and that re-acquires the
# same lock. A plain Lock here self-deadlocks the FIRST /api/controlpanel/match
# whenever _clf isn't already cached (e.g. data/unprocessed_security_types.json
# absent, so _get_classifier never caches), hanging the request forever.
_init_lock = threading.RLock()
# Serialises read-modify-write on classifier_overrides.json. Dropdown JS
# fires fire-and-forget POSTs per row change, so several land on different
# WSGI threads and clobber each other's saved overrides.
_overrides_lock = threading.Lock()

# ── Lazy classifier singleton ─────────────────────────────────────────────────
_clf = None

def _get_classifier():
    global _clf
    if _clf is not None:
        return _clf
    with _init_lock:
        if _clf is None and os.path.exists(JSON_PATH):
            c = SecurityTypeClassifier()
            c.train()
            _clf = c          # only cache after successful training
        return _clf

def reset_classifier():
    """Force retrain on next call (used after rebuild_mapping)."""
    global _clf
    with _init_lock:
        _clf = None

# ── Lazy matcher singleton ───────────────────────────────────────────────────
_matcher = None

def _get_matcher():
    global _matcher
    if _matcher is not None:
        return _matcher
    with _init_lock:
        if _matcher is None and db._ready():
            _matcher = SecurityMatcher(db, classifier=_get_classifier())
        return _matcher

def _reset_matcher():
    """Drop the cached matcher so the next call rebuilds it under the lock."""
    global _matcher
    with _init_lock:
        _matcher = None

# Each entry: (type key in MongoDB, column label shown in the table)
ISSUE_TYPES = [
    ("missing_wallet",                       "Carteira"),
    ("missing_unprocessed_position",         "Posição"),
    ("security_unmapped",                    "Mapeamento"),
    ("security_missing_classification",      "Classificação"),
    ("security_missing_price",               "Registro de Preço"),
    ("security_missing_history_price",       "Preço para o dia"),
    ("missing_fund_position_for_explosion",  "Posição para explosão"),
    ("explosion_error",                      "Erro em explosão"),
]

# Columns appended to the right of ISSUE_TYPES, separated by a visual divider
# in the UI. They surface day-by-day pipeline state per company, not pending
# issues — so the count/total fraction is the natural display format.
EXTRA_COLS = [
    ("processed",     "Posições Processadas"),
    ("nav_wallet",    "NAV Wallet"),
    ("gap",           "GAP"),
    ("nav_grouping",  "NAV Grouping"),
    ("published",     "Published"),
]

# ── Threshold helpers (mirrors pages.conciliacao) ────────────────────────────
# Painel de Controle's GAP column shares the same |returnNavPerShare - returnContribution|
# threshold as the conciliação page, persisted in the same JSON file so both
# UIs stay in sync when an operator tweaks the value. We re-implement the
# tiny read/write helpers here instead of importing from pages.conciliacao to
# avoid coupling page modules; the file format (single key `diffThresholdPct`,
# unit = percent) is the contract.
_CONCILIACAO_CONFIG_FILE = os.path.join(os.path.dirname(__file__), "..", "data",
                                        "conciliacao_config.json")
_DEFAULT_DIFF_THRESHOLD_PCT = 0.01  # 0.01% = 1 basis point

# Per-securityType field configuration for the "Cadastrar ativos" modal
# (templates/controlpanel.html → #registration-modal). Defines which editable
# fields the bottom line of each asset shows, keyed by securityType. The
# frontend renders the inputs and builds the registration JSON straight from
# this file, so adding fields for a new type is a JSON edit — no code change.
# See docs/CONTROLPANEL.md → "Cadastrar ativos".
_SECURITY_TYPE_FIELDS_FILE = os.path.join(os.path.dirname(__file__), "..", "data",
                                          "security_type_fields.json")


def _load_threshold_config():
    defaults = {"diffThresholdPct": _DEFAULT_DIFF_THRESHOLD_PCT}
    if not os.path.exists(_CONCILIACAO_CONFIG_FILE):
        return defaults
    try:
        with open(_CONCILIACAO_CONFIG_FILE, "r", encoding="utf-8") as f:
            return {**defaults, **(json.load(f) or {})}
    except Exception:
        return defaults


def _save_threshold_config(cfg):
    """Persist via atomic_write_json. Returns (ok, friendly_error_message)."""
    try:
        atomic_write_json(_CONCILIACAO_CONFIG_FILE, cfg)
        return True, ""
    except Exception as exc:
        import logging
        logging.getLogger(__name__).error("save threshold cfg failed: %s", exc)
        # Don't leak the full filesystem path in the error.
        return False, "verifique sincronização do OneDrive e tente novamente"


def _diff_threshold_decimal(req=None):
    """Resolve threshold in DECIMAL form (UI/storage unit is percent).

    Priority: explicit ?threshold=<pct> query param → config file → default.
    """
    pct = None
    if req is not None:
        raw = (req.args.get("threshold") or "").strip()
        if raw:
            try:
                pct = float(raw)
            except ValueError:
                pct = None
    if pct is None:
        pct = float(_load_threshold_config().get("diffThresholdPct",
                                                _DEFAULT_DIFF_THRESHOLD_PCT))
    return max(0.0, pct / 100.0)


# ── Per-company / per-date counters for the extra columns ────────────────────

def _wallets_by_company():
    """Returns ({companyId: total_wallets}, {walletIdStr: companyIdStr}).

    processedPosition has no companyId field — we resolve company through
    walletId. Doing the join in Python (one full wallets scan) avoids a
    MongoDB $lookup on the hot path. Cached 5 min via _cached_ttl: the
    wallets collection rarely changes (new wallet registrations) and this
    function runs on every /api/controlpanel/rows call. Callers must NOT mutate.
    """
    def _load():
        by_company = {}
        wallet_to_company = {}
        for w in db.wallets.find({}, {"_id": 1, "companyId": 1}):
            cid = str(w.get("companyId") or "")
            wid = str(w["_id"])
            if cid:
                by_company[cid] = by_company.get(cid, 0) + 1
                wallet_to_company[wid] = cid
        return (by_company, wallet_to_company)
    return _cached_ttl("controlpanel.wallets_by_company", _load)


def _groupings_by_company():
    """{companyId: total_untrashed_groupings}. Cached 5 min via _cached_ttl —
    callers must NOT mutate the returned dict."""
    def _load():
        out = {}
        for doc in db.groupings.aggregate([
            {"$match": {"trashed": {"$ne": True}}},
            {"$group": {"_id": "$companyId", "n": {"$sum": 1}}},
        ]):
            cid = str(doc["_id"] or "")
            if cid:
                out[cid] = doc["n"]
        return out
    return _cached_ttl("controlpanel.groupings_by_company", _load)


def _processed_count_by_company(date, wallet_to_company):
    """{companyId: processedPosition docs on date}, joined via wallet_to_company."""
    out = {}
    for doc in db.processedPosition.find({"positionDate": date}, {"walletId": 1}):
        cid = wallet_to_company.get(str(doc.get("walletId") or ""), "")
        if cid:
            out[cid] = out.get(cid, 0) + 1
    return out


def _navpackage_counts_by_company(date, threshold):
    """One pass over navPackages on this date, returning four parallel dicts:

      - nav_wallet:   docs with walletId    (NAV calculados por carteira)
      - gap:          wallet docs where |returnNavPerShare - returnContribution| > threshold
      - nav_grouping: docs with groupingId  (NAV calculados por agrupamento)
      - published:    docs with groupingId AND published == true

    All counts respect trashed != true. Single $match + $facet halves the
    round-trips compared to four separate aggregations.
    """
    nav_wallet, gap, nav_grouping, published = {}, {}, {}, {}
    if threshold and threshold > 0:
        gap_expr = {"$gt": [
            {"$abs": {"$subtract": [
                {"$ifNull": ["$returnNavPerShare", 0]},
                {"$ifNull": ["$returnContribution", 0]},
            ]}},
            float(threshold),
        ]}
    else:
        gap_expr = {"$ne": ["$returnNavPerShare", "$returnContribution"]}

    pipeline = [
        {"$match": {"positionDate": date, "trashed": {"$ne": True}}},
        {"$facet": {
            "nav_wallet": [
                {"$match": {"walletId": {"$exists": True, "$ne": None}}},
                {"$group": {"_id": "$companyId", "n": {"$sum": 1}}},
            ],
            "gap": [
                {"$match": {"walletId": {"$exists": True, "$ne": None}, "$expr": gap_expr}},
                {"$group": {"_id": "$companyId", "n": {"$sum": 1}}},
            ],
            "nav_grouping": [
                {"$match": {"groupingId": {"$exists": True, "$ne": None}}},
                {"$group": {"_id": "$companyId", "n": {"$sum": 1}}},
            ],
            "published": [
                {"$match": {"groupingId": {"$exists": True, "$ne": None}, "published": True}},
                {"$group": {"_id": "$companyId", "n": {"$sum": 1}}},
            ],
        }},
    ]

    cur = list(db.navPackages.aggregate(pipeline))
    if not cur:
        return nav_wallet, gap, nav_grouping, published
    facets = cur[0]
    for key, target in (("nav_wallet", nav_wallet), ("gap", gap),
                        ("nav_grouping", nav_grouping), ("published", published)):
        for doc in facets.get(key, []):
            cid = str(doc["_id"] or "")
            if cid:
                target[cid] = doc["n"]
    return nav_wallet, gap, nav_grouping, published


def _extra_cell(count, total, *, mode="ratio"):
    """Format an extras cell. mode: 'ratio' shows X/Y with colour by completeness;
    'count' shows just X with red if > 0 (for GAP)."""
    if mode == "count":
        if count > 0:
            cls = "bg-red-100 text-red-700 font-medium"
        else:
            cls = "text-gray-400"
        return {"count": count, "total": None, "label": str(count), "cls": cls}

    # ratio mode
    if total <= 0:
        cls = "text-gray-300"
        label = "—"
    elif count >= total:
        cls = "bg-green-100 text-green-700 font-medium"
        label = f"{count}/{total}"
    elif count == 0:
        cls = "bg-red-50 text-red-600"
        label = f"{count}/{total}"
    else:
        cls = "bg-amber-50 text-amber-700"
        label = f"{count}/{total}"
    return {"count": count, "total": total, "label": label, "cls": cls}

# ── Helpers ────────────────────────────────────────────────────────────────────

def _cell_cls(count):
    return "bg-green-100 text-green-700 font-medium" if count > 0 else "text-gray-300"


def _count_pending_for_date(date):
    """Returns {(companyId, type): count} for the given date."""
    counts = {}
    for doc in db.issues.aggregate([
        {"$match": {"status": "pending", "date": date}},
        {"$group": {"_id": {"c": "$companyId", "t": "$type"}, "n": {"$sum": 1}}},
    ]):
        cid = str(doc["_id"].get("c", ""))
        typ = doc["_id"].get("t", "")
        if cid and typ:
            counts[(cid, typ)] = doc["n"]
    return counts


def _format_issue(issue):
    return {
        "type":                  issue.get("type", ""),
        "description":           issue.get("description", ""),
        "walletId":              str(issue.get("walletId", "") or ""),
        "externalId":            str(issue.get("externalId", "") or ""),
        "externalOrigin":        str(issue.get("externalOrigin", "") or ""),
        "securityId":            str(issue.get("securityId", "") or ""),
        "unprocessedSecurityId": str(issue.get("unprocessedSecurityId", "") or ""),
        "createdAt":             issue["createdAt"].strftime("%Y-%m-%d %H:%M") if issue.get("createdAt") else "",
    }


# ── Routes ─────────────────────────────────────────────────────────────────────

def _date_cards(dates):
    """Returns list of {date, total} for the date picker cards."""
    totals = {}
    for doc in db.issues.aggregate([
        {"$match": {"status": "pending", "date": {"$in": dates}}},
        {"$group": {"_id": "$date", "n": {"$sum": 1}}},
    ]):
        d = str(doc["_id"])[:10]
        if d:
            totals[d] = doc["n"]
    return [{"date": d, "total": totals.get(d, 0)} for d in dates]


@bp.route("/controlpanel")
def index():
    dates   = get_biz_dates(_NUM_DATES)
    cards   = _date_cards(dates)
    delays  = load_config_delays()
    default_delay = min(delays.values(), default=1) if delays else 1
    threshold_pct = float(_load_threshold_config().get("diffThresholdPct",
                                                      _DEFAULT_DIFF_THRESHOLD_PCT))
    return render_template(
        "controlpanel.html",
        cards=cards,
        types=ISSUE_TYPES,
        extra_cols=EXTRA_COLS,
        default_delay=default_delay,
        threshold_pct=threshold_pct,
    )


@bp.route("/api/controlpanel/date-cards")
def date_cards():
    """Return `_NUM_DATES` business-day cards ending on the given date.

    endDate must be ISO YYYY-MM-DD; anything else falls back to today so a
    malformed query param never reaches get_biz_dates / datetime parsing.
    """
    import re
    end_date = (request.args.get("endDate") or "").strip() or None
    if end_date and not re.match(r"^\d{4}-\d{2}-\d{2}$", end_date):
        end_date = None
    try:
        dates = get_biz_dates(_NUM_DATES, end_date=end_date)
    except Exception:
        dates = get_biz_dates(_NUM_DATES)
    return jsonify({"cards": _date_cards(dates), "dates": dates})


def _unidentified_txn_count_by_company(date, wallet_to_company):
    """Count transactions still missing a `beehusTransactionType` per company
    for `date`.

    Mirrors the Identificar Transações default search (company wallets,
    `identified=false`, single day): transactions are scoped to a company
    through `walletId → companyId` (they carry no companyId field), the date
    axis is `liquidationDate`, and "não identificada" means
    `beehusTransactionType` is null or empty. `trashed` rows are excluded.
    Returns {companyId: count}.
    """
    counts = {}
    try:
        for doc in db.transactions.aggregate([
            {"$match": {
                "liquidationDate":       {"$gte": date, "$lte": date},
                "trashed":               {"$ne": True},
                "beehusTransactionType": {"$in": [None, ""]},
            }},
            {"$group": {"_id": "$walletId", "n": {"$sum": 1}}},
        ]):
            wid = str(doc.get("_id") or "")
            cid = wallet_to_company.get(wid)
            if cid:
                counts[cid] = counts.get(cid, 0) + int(doc["n"])
    except Exception:
        import logging, traceback
        logging.error("unidentified txn count failed: %s", traceback.format_exc())
    return counts


@bp.route("/api/controlpanel/rows")
def get_rows():
    date          = request.args.get("date", get_biz_dates(1)[0])
    company_names = get_company_names()
    threshold     = _diff_threshold_decimal(request)

    counts = _count_pending_for_date(date)
    # Include companies that have pipeline activity even when no pending issues
    # exist — without this, the new processed/NAV/published columns would never
    # render once the issues backlog is cleared.
    wallets_total, wallet_to_company = _wallets_by_company()
    groupings_total = _groupings_by_company()
    processed   = _processed_count_by_company(date, wallet_to_company)
    nav_wallet, gap, nav_grouping, published = _navpackage_counts_by_company(date, threshold)
    txn_unident = _unidentified_txn_count_by_company(date, wallet_to_company)

    company_ids = (
        {cid for (cid, _) in counts}
        | set(processed.keys())
        | set(nav_wallet.keys())
        | set(nav_grouping.keys())
        | set(published.keys())
        | set(txn_unident.keys())
    )
    cf = get_company_filter()
    if cf:
        company_ids = company_ids & cf

    rows = []
    for cid in sorted(company_ids, key=lambda c: company_names.get(c, c)):
        cells = []
        for key, _label in ISSUE_TYPES:
            count = counts.get((cid, key), 0)
            cells.append({
                "type":  key,
                "count": count,
                "label": str(count) if count > 0 else "—",
                "cls":   _cell_cls(count),
            })

        wt = wallets_total.get(cid, 0)
        gt = groupings_total.get(cid, 0)
        extras = [
            {"key": "processed",
             **_extra_cell(processed.get(cid, 0),    wt)},
            {"key": "nav_wallet",
             **_extra_cell(nav_wallet.get(cid, 0),   wt)},
            {"key": "gap",
             **_extra_cell(gap.get(cid, 0),          wt, mode="count")},
            {"key": "nav_grouping",
             **_extra_cell(nav_grouping.get(cid, 0), gt)},
            {"key": "published",
             **_extra_cell(published.get(cid, 0),    gt)},
        ]

        tn = txn_unident.get(cid, 0)
        rows.append({
            "companyId": cid,
            "company":   company_names.get(cid, cid),
            "cells":     cells,
            "extras":    extras,
            # Unidentified-transaction counter (column "TXN"), rendered between
            # the issue-type cells and the pipeline-progress extras.
            "txn": {
                "count": tn,
                "label": str(tn) if tn > 0 else "—",
                "cls":   _cell_cls(tn),
            },
        })

    return jsonify({"rows": rows, "date": date})


# ── Per-company issue summary (used by Fluxo apontamentos) ────────────────────

@bp.route("/api/controlpanel/issues-summary")
def issues_summary():
    """Pending-issue counts for a single (company, date), narrowed to the
    types the caller cares about.

    Query params:
        companyId  required
        date       required, YYYY-MM-DD
        types      optional, comma-separated. Defaults to all ISSUE_TYPES keys.

    The Fluxo apontamento for each step asks for a focused subset (e.g. just
    the four "post-process" issue types). Returning one type at a time would
    multiply round-trips for no benefit, so the endpoint accepts a list and
    aggregates once. Labels come from ISSUE_TYPES so the UI doesn't need to
    keep its own copy in sync.
    """
    cid  = (request.args.get("companyId") or "").strip()
    date = (request.args.get("date") or "").strip()
    if not cid or not date:
        return jsonify({"error": "companyId and date are required"}), 400
    if not company_visible(cid):
        return jsonify({"error": "company not visible"}), 403

    valid_types = {k for k, _ in ISSUE_TYPES}
    label_by_type = dict(ISSUE_TYPES)
    raw_types = (request.args.get("types") or "").strip()
    if raw_types:
        requested = [t.strip() for t in raw_types.split(",") if t.strip()]
        types = [t for t in requested if t in valid_types]
    else:
        types = [k for k, _ in ISSUE_TYPES]
    if not types:
        return jsonify({"companyId": cid, "date": date, "types": [], "total": 0})

    counts = {}
    for doc in db.issues.aggregate([
        {"$match": {
            "status":    "pending",
            "date":      date,
            "companyId": cid,
            "type":      {"$in": types},
        }},
        {"$group": {"_id": "$type", "n": {"$sum": 1}}},
    ]):
        counts[doc["_id"]] = doc["n"]

    # Preserve the order requested by the caller so the UI can render the
    # apontamento list deterministically — order in ISSUE_TYPES matches the
    # left-to-right column order of the Painel de Controle table.
    items = [
        {"type": t, "label": label_by_type[t], "count": counts.get(t, 0)}
        for t in types
    ]
    total = sum(it["count"] for it in items)
    return jsonify({"companyId": cid, "date": date, "types": items, "total": total})


# ── Threshold endpoints (shared with conciliação) ─────────────────────────────

@bp.route("/api/controlpanel/threshold", methods=["GET"])
def get_threshold():
    """Return the current diffThresholdPct (percent units, e.g. 0.01)."""
    cfg = _load_threshold_config()
    return jsonify({"diffThresholdPct": cfg.get("diffThresholdPct",
                                               _DEFAULT_DIFF_THRESHOLD_PCT)})


@bp.route("/api/controlpanel/threshold", methods=["PUT", "POST"])
def set_threshold():
    """Update the diffThresholdPct. Writes to the same config file as
    conciliação so both pages stay in sync."""
    body = request.get_json(force=True, silent=True) or {}
    try:
        pct = float(body.get("diffThresholdPct"))
    except (TypeError, ValueError):
        return jsonify({"error": "diffThresholdPct inválido"}), 400
    if pct < 0 or pct > 10:
        return jsonify({"error": "diffThresholdPct fora do intervalo [0, 10]"}), 400
    cfg = _load_threshold_config()
    cfg["diffThresholdPct"] = pct
    ok, err = _save_threshold_config(cfg)
    if not ok:
        return jsonify({"error": f"falha ao salvar: {err}"}), 500
    return jsonify(cfg)


@bp.route("/api/controlpanel/detail")
def get_detail():
    cid  = request.args.get("companyId")
    date = request.args.get("date")
    typ  = request.args.get("type")

    if cid and not company_visible(cid):
        return jsonify({"issues": [], "date": date, "type": typ}), 403

    wallet_names = get_wallet_names()

    issues = sorted([
        {**_format_issue(issue),
         "walletName": wallet_names.get(str(issue.get("walletId", "") or ""), "")}
        for issue in db.issues.find(
            {"companyId": cid, "status": "pending", "date": date, "type": typ},
            {"_id": 0, "type": 1, "description": 1, "walletId": 1,
             "externalId": 1, "externalOrigin": 1, "securityId": 1,
             "unprocessedSecurityId": 1, "createdAt": 1}
        )
    ], key=lambda x: x["createdAt"])

    # Enrich with beehusName from securities collection
    def _to_oid(val):
        try:
            return ObjectId(val)
        except (TypeError, ValueError):
            return None

    sec_ids = [_to_oid(i["securityId"]) for i in issues if i.get("securityId")]
    sec_ids = [s for s in sec_ids if s]
    sec_map = {}
    if sec_ids:
        sec_map = {
            str(s["_id"]): s
            for s in db.securities.find(
                {"_id": {"$in": sec_ids}}, {"beehusName": 1, "mainId": 1}
            )
        }
    for issue in issues:
        sec = sec_map.get(issue.get("securityId", "")) or {}
        issue["beehusName"] = sec.get("beehusName", "") or ""
        issue["mainId"]     = sec.get("mainId", "") or ""

    return jsonify({"issues": issues, "date": date, "type": typ})


# ── Cell drill-down (Posições Processadas / NAV Wallet / NAV Grouping / Published)
#
# The 4 right-most columns in /controlpanel show pipeline progress per
# (company, date). Operators want to click the count and see which wallets
# (or groupings) are actually done vs pending. This endpoint backs the
# `#cell-detail-modal` on the Home view — one row per wallet (for the
# wallet-level columns) or per grouping (for the grouping-level columns),
# each with a boolean `done` flag the UI renders as a status badge.
#
# Column → meaning of `done`:
#   processed     — wallet has a `processedPosition` for (positionDate)
#   nav_wallet    — wallet has a NAV `navPackages` doc (walletId set)
#   nav_grouping  — grouping has a NAV `navPackages` doc (groupingId set)
#   published     — grouping has a NAV `navPackages` doc with published=true
#
# All navPackages queries respect `trashed != true` and filter by companyId
# to match the count rendered in the table.

_WALLET_COLUMNS    = {"processed", "nav_wallet"}
_GROUPING_COLUMNS  = {"nav_grouping", "published"}
_CELL_DETAIL_COLS  = _WALLET_COLUMNS | _GROUPING_COLUMNS

# Issue types that can block a wallet from being processed. Surfaced in
# the "Posições Processadas" drill-down so the operator can tell at a
# glance why an unprocessed wallet hasn't reached `processedPosition`
# yet. Post-processing types (`missing_fund_position_for_explosion`,
# `explosion_error`) are deliberately excluded — they're symptoms of a
# *later* stage and would only add noise to a "why isn't this wallet
# processed?" view. Order mirrors the canonical ISSUE_TYPES list so the
# chips render in pipeline order.
_PROCESSING_BLOCKING_ISSUE_TYPES = (
    "missing_wallet",
    "missing_unprocessed_position",
    "security_unmapped",
    "security_missing_classification",
    "security_missing_price",
    "security_missing_history_price",
)


def _wallets_for_company(company_id):
    """List of {id, name} for the given company, sorted by name."""
    out = []
    for w in db.wallets.find({"companyId": company_id}, {"_id": 1, "name": 1}):
        out.append({"id": str(w["_id"]), "name": (w.get("name") or "")})
    out.sort(key=lambda w: (w["name"] or w["id"]).lower())
    return out


def _untrashed_groupings_for_company(company_id):
    """List of {id, name, walletIds} from `get_grouping_index()`. Filtered
    to untrashed and matching company. We copy the wallet-id list rather
    than aliasing the cache entry so callers can mutate freely."""
    gindex = get_grouping_index()
    out = []
    for gid, info in gindex.items():
        if info.get("trashed"):
            continue
        if info.get("companyId") != company_id:
            continue
        out.append({
            "id":        gid,
            "name":      info.get("name") or gid,
            "walletIds": list(info.get("walletIds") or []),
        })
    out.sort(key=lambda g: (g["name"] or g["id"]).lower())
    return out


def _processed_done_wallets(company_id, date, wallet_ids):
    """Set of walletIds (str) with a processedPosition for the date. The
    collection has no companyId field; we narrow by walletId to keep the
    scan tight."""
    if not wallet_ids:
        return set()
    done = set()
    for doc in db.processedPosition.find(
        {"positionDate": date, "walletId": {"$in": list(wallet_ids)}},
        {"walletId": 1},
    ):
        wid = str(doc.get("walletId") or "")
        if wid:
            done.add(wid)
    return done


def _unprocessed_existing_wallets(date, wallet_ids):
    """Set of walletIds (str) that have at least one
    `unprocessedSecurityPositions` doc for the date. Surfaces in the
    "Posições Processadas" drill-down so operators can tell whether a
    pending wallet is waiting on raw positions arriving from upstream
    (no unprocessed doc yet) vs waiting on the processing step itself
    (unprocessed doc exists but no processedPosition yet)."""
    if not wallet_ids:
        return set()
    done = set()
    for doc in db.unprocessedSecurityPositions.find(
        {"positionDate": date, "walletId": {"$in": list(wallet_ids)}},
        {"walletId": 1},
    ):
        wid = str(doc.get("walletId") or "")
        if wid:
            done.add(wid)
    return done


def _blocking_issues_by_wallet(company_id, date, wallet_ids):
    """{walletId: [{type, label, count}, ...]} for pending issues whose
    type is in `_PROCESSING_BLOCKING_ISSUE_TYPES`. One aggregation
    covers every wallet in the company so the per-wallet rendering on
    the frontend is just a dict lookup. Issues without a walletId
    (company-level rows) are skipped — they don't belong next to a
    specific wallet in the UI."""
    if not wallet_ids:
        return {}
    label_by_type = dict(ISSUE_TYPES)
    by_wallet = {}
    cursor = db.issues.aggregate([
        {"$match": {
            "companyId": company_id,
            "status":    "pending",
            "date":      date,
            "type":      {"$in": list(_PROCESSING_BLOCKING_ISSUE_TYPES)},
            "walletId":  {"$in": list(wallet_ids)},
        }},
        {"$group": {
            "_id": {"w": "$walletId", "t": "$type"},
            "n":   {"$sum": 1},
        }},
    ])
    for doc in cursor:
        wid = str(doc["_id"].get("w", "") or "")
        typ = doc["_id"].get("t", "")
        n   = doc.get("n", 0)
        if not wid or not typ:
            continue
        by_wallet.setdefault(wid, {})[typ] = n

    # Sort each wallet's issue list using the canonical pipeline order so
    # the chips read left-to-right as "earliest blocker first".
    result = {}
    for wid, type_counts in by_wallet.items():
        items = []
        for typ in _PROCESSING_BLOCKING_ISSUE_TYPES:
            if typ in type_counts:
                items.append({
                    "type":  typ,
                    "label": label_by_type.get(typ, typ),
                    "count": int(type_counts[typ]),
                })
        if items:
            result[wid] = items
    return result


def _nav_done_wallets(company_id, date):
    """Set of walletIds (str) that have an untrashed NAV navPackages doc
    for (companyId, positionDate). Only docs with `walletId` set count —
    grouping-level docs go through `_nav_done_groupings`."""
    done = set()
    for doc in db.navPackages.find(
        {"companyId": company_id, "positionDate": date,
         "trashed": {"$ne": True},
         "walletId": {"$exists": True, "$ne": None}},
        {"walletId": 1},
    ):
        wid = str(doc.get("walletId") or "")
        if wid:
            done.add(wid)
    return done


def _nav_done_groupings(company_id, date, *, only_published=False):
    """Set of groupingIds (str) that have an untrashed NAV navPackages
    doc for (companyId, positionDate). When `only_published=True` we also
    require `published == True`, matching the "Published" column."""
    match = {
        "companyId":    company_id,
        "positionDate": date,
        "trashed":      {"$ne": True},
        "groupingId":   {"$exists": True, "$ne": None},
    }
    if only_published:
        match["published"] = True
    return {
        str(gid) for gid in db.navPackages.distinct("groupingId", match)
        if gid
    }


@bp.route("/api/controlpanel/cell-detail")
def cell_detail():
    """Drill-down detail for the 4 progress columns on the Home view.

    Query params:
        companyId  required
        date       required, YYYY-MM-DD
        column     required, one of {processed, nav_wallet, nav_grouping, published}

    For wallet-level columns the response groups wallets under their
    grouping (with an `orphanWallets` bucket for wallets that aren't part
    of any grouping for this company). For grouping-level columns the
    response is a flat sorted list of groupings.
    """
    company_id = (request.args.get("companyId") or "").strip()
    date       = (request.args.get("date") or "").strip()
    column     = (request.args.get("column") or "").strip()

    if not company_id or not date or not column:
        return jsonify({"error": "companyId, date and column are required"}), 400
    if column not in _CELL_DETAIL_COLS:
        return jsonify({"error": f"invalid column: {column}"}), 400
    if not company_visible(company_id):
        return jsonify({"error": "company not visible"}), 403

    company_names = get_company_names()
    company_name  = company_names.get(company_id) or company_id

    groupings = _untrashed_groupings_for_company(company_id)

    # ── Grouping-level columns ────────────────────────────────────────────
    if column in _GROUPING_COLUMNS:
        done = _nav_done_groupings(company_id, date,
                                   only_published=(column == "published"))
        items = [{
            "groupingId":   g["id"],
            "groupingName": g["name"],
            "done":         g["id"] in done,
        } for g in groupings]
        return jsonify({
            "column":          column,
            "level":           "grouping",
            "companyId":       company_id,
            "companyName":     company_name,
            "date":            date,
            "totalGroupings":  len(groupings),
            "doneGroupings":   sum(1 for it in items if it["done"]),
            "groupings":       items,
        })

    # ── Wallet-level columns (processed / nav_wallet) ─────────────────────
    wallets = _wallets_for_company(company_id)
    wallet_ids = {w["id"] for w in wallets}
    wallet_names = {w["id"]: (w["name"] or w["id"]) for w in wallets}

    if column == "processed":
        done = _processed_done_wallets(company_id, date, wallet_ids)
        # Side-channel flag: tells the operator whether the wallet is
        # pending because raw positions haven't arrived (no unprocessed
        # doc) or because processing simply hasn't run yet (unprocessed
        # doc exists). Only relevant for the "Posições Processadas"
        # drill-down, so we skip the query for `nav_wallet`.
        unprocessed_set = _unprocessed_existing_wallets(date, wallet_ids)
        # Blocking issues per wallet — surfaced only for the "Posições
        # Processadas" view so the operator can tell *why* an
        # unprocessed wallet hasn't reached `processedPosition`.
        issues_by_wallet = _blocking_issues_by_wallet(
            company_id, date, wallet_ids
        )
    else:  # nav_wallet
        done = _nav_done_wallets(company_id, date)
        unprocessed_set    = None
        issues_by_wallet   = None

    # Build per-grouping wallet lists; track which company wallets get
    # captured so we can surface the leftovers as `orphanWallets`.
    seen = set()
    groupings_out = []
    for g in groupings:
        # Restrict to wallets that actually belong to this company — the
        # cached grouping index may carry stale ids if a wallet was moved.
        wallets_out = []
        for wid in g["walletIds"]:
            if wid not in wallet_ids:
                continue
            entry = {
                "walletId":   wid,
                "walletName": wallet_names.get(wid) or wid,
                "done":       wid in done,
            }
            if unprocessed_set is not None:
                entry["hasUnprocessed"] = wid in unprocessed_set
            if issues_by_wallet is not None:
                entry["issues"] = issues_by_wallet.get(wid, [])
            wallets_out.append(entry)
            seen.add(wid)
        wallets_out.sort(key=lambda w: w["walletName"].lower())
        if not wallets_out:
            # Skip empty groupings — they would render an empty section with
            # zero wallets and only add noise to the modal.
            continue
        groupings_out.append({
            "groupingId":   g["id"],
            "groupingName": g["name"],
            "wallets":      wallets_out,
            "doneCount":    sum(1 for w in wallets_out if w["done"]),
            "totalCount":   len(wallets_out),
        })

    orphan_wallets = []
    for w in wallets:
        if w["id"] in seen:
            continue
        entry = {
            "walletId":   w["id"],
            "walletName": w["name"] or w["id"],
            "done":       w["id"] in done,
        }
        if unprocessed_set is not None:
            entry["hasUnprocessed"] = w["id"] in unprocessed_set
        if issues_by_wallet is not None:
            entry["issues"] = issues_by_wallet.get(w["id"], [])
        orphan_wallets.append(entry)

    payload = {
        "column":         column,
        "level":          "wallet",
        "companyId":      company_id,
        "companyName":    company_name,
        "date":           date,
        "totalWallets":   len(wallets),
        "doneWallets":    sum(1 for w in wallets if w["id"] in done),
        "groupings":      groupings_out,
        "orphanWallets":  orphan_wallets,
    }
    if unprocessed_set is not None:
        payload["totalUnprocessed"] = sum(
            1 for w in wallets if w["id"] in unprocessed_set
        )
    return jsonify(payload)


# ── Classifier endpoints ──────────────────────────────────────────────────────

@bp.route("/api/controlpanel/classify/override", methods=["POST"])
def classify_override():
    """Save a user correction: {unprocessedId, securityType}."""
    body = request.get_json(force=True, silent=True) or {}
    uid  = body.get("unprocessedId", "").strip()
    stype = body.get("securityType", "").strip()
    if not uid or not stype:
        return jsonify({"ok": False, "error": "missing fields"}), 400

    overrides_path = os.path.join(os.path.dirname(JSON_PATH), "classifier_overrides.json")
    with _overrides_lock:
        overrides = {}
        if os.path.exists(overrides_path):
            try:
                with open(overrides_path, "r", encoding="utf-8") as f:
                    overrides = json.load(f)
            except json.JSONDecodeError:
                overrides = {}
        overrides[uid] = stype
        atomic_write_json(overrides_path, overrides)
    return jsonify({"ok": True})


@bp.route("/api/controlpanel/security-types")
def security_types():
    """Return the list of known securityType values for the dropdown."""
    types = set()
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, encoding="utf-8") as f:
            for row in json.load(f):
                if row.get("securityType"):
                    types.add(row["securityType"])
    return jsonify({"types": sorted(types)})


@bp.route("/api/controlpanel/security-type-fields")
def security_type_fields():
    """Per-securityType field config for the "Cadastrar ativos" modal.

    Returns the contents of data/security_type_fields.json:
      { "types": { "<securityType>": { "label": str, "fields": [ {key,...} ] } } }

    The frontend uses `.types` to render the editable (bottom) line of each
    asset and to build the registration JSON. Degrades gracefully to an empty
    map when the file is missing/corrupt — the modal then shows only the
    structural fields (beehusName + securityType)."""
    try:
        if os.path.exists(_SECURITY_TYPE_FIELDS_FILE):
            with open(_SECURITY_TYPE_FIELDS_FILE, encoding="utf-8") as f:
                data = json.load(f)
            return jsonify({"types": data.get("types", {})})
    except Exception:
        import traceback, logging
        logging.error("security_type_fields error: %s", traceback.format_exc())
    return jsonify({"types": {}})


def _batch_pu(company_id, date, unprocessed_ids):
    """Return {unprocessedId: pu} from the most recent unprocessedSecurityPositions.

    Strategy: fetch the most recent position docs (one per wallet) and scan
    their securities arrays in Python. Much faster than $unwind aggregation
    on a large collection.
    """
    if not company_id or not unprocessed_ids:
        return {}
    query = {"companyId": company_id}
    if date:
        query["positionDate"] = {"$lte": date}

    pu_map = {}
    remaining = set(unprocessed_ids)

    # Fetch recent position docs, most recent first. Each doc is one wallet+date.
    for doc in db.unprocessedSecurityPositions.find(
        query, {"securities.unprocessedId": 1, "securities.pu": 1}
    ).sort("positionDate", -1).limit(200):
        for sec in doc.get("securities", []):
            uid = sec.get("unprocessedId")
            if uid and uid in remaining:
                pu_map[uid] = sec.get("pu")
                remaining.discard(uid)
        if not remaining:
            break
    return pu_map


def _batch_last_price(security_ids, target_date=None):
    """Return {securityId: {value, date}} from securityPrices.historyPrice.

    If target_date (YYYY-MM-DD) is given, return the entry on that date; if
    no exact match exists, fall back to the nearest available by absolute
    day distance. Without target_date, returns the last entry (legacy).
    """
    price_map = {}
    if not security_ids:
        return price_map

    if not target_date:
        for doc in db.securityPrices.find(
            {"securityId": {"$in": list(security_ids)}},
            {"securityId": 1, "historyPrice": {"$slice": -1}},
        ):
            sid = doc.get("securityId")
            hp = doc.get("historyPrice", [])
            if sid and hp:
                price_map[sid] = {"value": hp[0].get("value"), "date": str(hp[0].get("date", ""))[:10]}
        return price_map

    from datetime import datetime
    try:
        target_dt = datetime.strptime(target_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        # Malformed target → fall back to last-entry behaviour rather than error.
        return _batch_last_price(security_ids)

    for doc in db.securityPrices.find(
        {"securityId": {"$in": list(security_ids)}},
        {"securityId": 1, "historyPrice": 1},
    ):
        sid = doc.get("securityId")
        hp = doc.get("historyPrice") or []
        if not sid or not hp:
            continue

        exact = None
        best = None
        best_diff = None
        for entry in hp:
            d_str = str(entry.get("date", ""))[:10]
            if not d_str:
                continue
            if d_str == target_date[:10]:
                exact = entry
                break
            try:
                entry_dt = datetime.strptime(d_str, "%Y-%m-%d")
            except ValueError:
                continue
            diff = abs((entry_dt - target_dt).days)
            if best_diff is None or diff < best_diff:
                best = entry
                best_diff = diff

        chosen = exact or best
        if chosen:
            price_map[sid] = {
                "value": chosen.get("value"),
                "date":  str(chosen.get("date", ""))[:10],
            }
    return price_map


def _price_agreement(pu, last_price):
    """Score contribution from comparing the unprocessed position's ``pu``
    against the matched security's ``lastPrice`` (securityPrices.historyPrice).

    Extra evidence available ONLY in the mapping flow (the identification flow
    has no PU to compare). A tight agreement confirms the match; a large
    divergence is evidence against it. Returns ``(points, label)`` — points are
    0 when either side is missing/non-positive or the gap is in the neutral
    band, so a missing price never penalises a candidate."""
    try:
        pu_f = float(pu)
        lp_f = float(last_price)
    except (TypeError, ValueError):
        return 0, None
    if pu_f <= 0 or lp_f <= 0:
        return 0, None
    rel = abs(pu_f - lp_f) / max(abs(pu_f), abs(lp_f))
    if rel <= 0.001:
        return 30, "Preço idêntico ao PU (Δ ≤ 0,1%)"
    if rel <= 0.01:
        return 20, "Preço bate com o PU (Δ ≤ 1%)"
    if rel <= 0.05:
        return 8,  "Preço próximo do PU (Δ ≤ 5%)"
    if rel >= 0.50:
        return -25, "Preço diverge muito do PU (Δ > 50%)"
    if rel >= 0.20:
        return -10, "Preço diverge do PU (Δ > 20%)"
    return 0, None


@bp.route("/api/controlpanel/match", methods=["POST"])
def match_securities():
    """
    Match unprocessedIds against the securities collection.

    Body: { "items": [...], "companyId": str (optional), "date": str (optional) }
    Returns: { "results": [ { unprocessedId, predicted_type, type_confidence,
                               candidate, pu, lastPrice }, ... ] }
    """
    try:
        body  = request.get_json(force=True, silent=True) or {}
        items = body.get("items", [])
        company_id = body.get("companyId", "")
        date       = body.get("date", "")
        if not items:
            return jsonify({"results": []})
        if company_id and not company_visible(company_id):
            return jsonify({"results": [], "error": "company not visible"}), 403

        matcher = _get_matcher()
        if matcher is None:
            return jsonify({"results": [], "error": "matcher not available"}), 500

        # Collect uids, resolving item format
        uids = []
        item_types = {}  # uid → user-supplied securityType (if any)
        for item in items:
            uid   = item.get("unprocessedId", "") if isinstance(item, dict) else str(item)
            stype = item.get("securityType") if isinstance(item, dict) else None
            if uid:
                uids.append(uid)
                if stype:
                    item_types[uid] = stype

        if not uids:
            return jsonify({"results": []})

        # Step 1: batch-classify types for all uids that need it
        uids_needing_clf = [u for u in uids if u not in item_types]
        clf = _get_classifier()
        type_map = {}  # uid → {type, confidence}
        if clf and uids_needing_clf:
            preds = clf.predict_batch(uids_needing_clf)
            for p in preds:
                type_map[p["unprocessedId"]] = {
                    "type": p["type"], "confidence": p["confidence"]
                }
        for uid, stype in item_types.items():
            type_map[uid] = {"type": stype, "confidence": None}

        # Step 2: match each uid (classifier step already done)
        results = []
        sec_id_set = set()
        for uid in uids:
            tm = type_map.get(uid, {})
            stype = tm.get("type")
            sconf = tm.get("confidence")

            match = matcher.match(uid, security_type=stype, type_confidence=sconf, limit=3)
            top   = match["candidates"][0] if match["candidates"] else None
            if top:
                sec_id_set.add(top["securityId"])
            # Structured identifiers parsed from the unprocessedId text, surfaced
            # for the asset-info (top) line of the "Cadastrar ativos" modal.
            # taxId is the bare CNPJ; `type` is the detected instrument when the
            # parser found one (bonds), blank otherwise.
            feats = match.get("extracted") or {}
            results.append({
                "unprocessedId":  uid,
                "predicted_type": match["predicted_type"],
                "type_confidence": match["type_confidence"],
                "extracted": {
                    "isin":   feats.get("isin", ""),
                    "ticker": feats.get("ticker", ""),
                    "taxId":  feats.get("cnpj", ""),
                    "type":   feats.get("bond_type") or feats.get("instrument", ""),
                },
                "candidate": {
                    "securityId": top["securityId"],
                    "mainId":     top["mainId"],
                    "beehusName": top["beehusName"],
                    "score":      top["score"],
                    "confidence": top["confidence"],
                    "matched_on": top["matched_on"],
                } if top else None,
            })

        # Step 3: batch enrich with PU and lastPrice (2 queries total)
        uid_set   = set(uids)
        pu_map    = _batch_pu(company_id, date, uid_set)
        price_map = _batch_last_price(sec_id_set, target_date=date)

        for r in results:
            r["pu"] = pu_map.get(r["unprocessedId"])
            cand = r.get("candidate")
            r["lastPrice"] = price_map.get(cand["securityId"]) if cand else None
            if not cand:
                continue
            # Score decomposition for the "como o score foi calculado" tooltip
            # (same logic as Identificar Transações; here it's L3-only — full
            # cadastro sweep — since there's no wallet position to scope to).
            base_score = cand.get("score") or 0
            breakdown = _score_breakdown(cand.get("matched_on") or [], base_score)
            # Price-vs-PU verification: only confirms/penalises a candidate that
            # already has a real match signal (base_score > 0), so a price
            # coincidence can never lift an otherwise-unmatched security.
            if base_score > 0:
                lp = r["lastPrice"]
                pts, label = _price_agreement(r["pu"], lp.get("value") if lp else None)
                if pts:
                    # Clamp so a penalty can't push the score below 0; keep the
                    # breakdown honest by recording the *applied* delta.
                    applied = pts if base_score + pts >= 0 else -base_score
                    if applied:
                        breakdown.append({"code": "price", "points": applied, "label": label})
                        new_score = base_score + applied
                        cand["score"] = new_score
                        cand["confidence"] = _confidence_label(new_score)
            cand["breakdown"] = breakdown

        return jsonify({"results": results})
    except Exception:
        import traceback, logging
        logging.error("match_securities error: %s", traceback.format_exc())
        return jsonify({"results": [], "error": "internal error"}), 500


@bp.route("/api/controlpanel/security-mapping-id")
def security_mapping_id():
    """Return the securityMappings _id for a given companyId."""
    company_id = request.args.get("companyId", "").strip()
    if not company_id:
        return jsonify({"securityMappingId": None, "error": "missing companyId"}), 400
    if not company_visible(company_id):
        return jsonify({"securityMappingId": None, "error": "company not visible"}), 403
    doc = db.securityMappings.find_one({"companyId": company_id}, {"_id": 1})
    return jsonify({"securityMappingId": str(doc["_id"]) if doc else None})


@bp.route("/api/controlpanel/apply-mapping", methods=["POST"])
def apply_mapping():
    """Apply selected security mappings directly into Beehus via upstream PATCH.

    Body: { "companyId": str, "mappingsToInclude": [{from, to}, ...] }

    Looks up the `securityMappings._id` for the company server-side (so the
    client cannot tamper with it) and forwards the include list upstream.
    Only `mappingsToInclude` is accepted here by design — exclusions are out
    of scope for the Painel de Controle page.
    """
    body = request.get_json(silent=True) or {}
    company_id = (body.get("companyId") or "").strip()
    includes   = body.get("mappingsToInclude") or []

    if not company_id:
        return jsonify({"error": "companyId is required"}), 400
    if not company_visible(company_id):
        return jsonify({"error": "company is not visible to this user"}), 403
    if not isinstance(includes, list) or not includes:
        return jsonify({"error": "mappingsToInclude must be a non-empty list"}), 400

    cleaned = []
    for m in includes:
        if not isinstance(m, dict):
            return jsonify({"error": "each mapping must be an object {from, to}"}), 400
        frm = (m.get("from") or "").strip()
        to  = (m.get("to") or "").strip()
        if not frm or not to:
            return jsonify({"error": "each mapping requires non-empty 'from' and 'to'"}), 400
        cleaned.append({"from": frm, "to": to})

    doc = db.securityMappings.find_one({"companyId": company_id}, {"_id": 1})
    if not doc:
        return jsonify({"error": "securityMappingId not found for this company"}), 404
    mapping_id = str(doc["_id"])

    try:
        result = update_security_mappings(
            mapping_id,
            mappings_to_include=cleaned,
            mappings_to_exclude=[],
        )
    except BeehusAuthError as e:
        return jsonify({
            "error": str(e),
            "upstream_status": e.status,
            "upstream_body": e.body,
        }), 401
    except BeehusAPIError as e:
        return jsonify({
            "error": str(e),
            "upstream_status": e.status,
            "upstream_body": e.body,
        }), 502

    return jsonify({
        "ok": True,
        "securityMappingId": mapping_id,
        "applied": len(cleaned),
        "response": result if result is not None else {},
    })


@bp.route("/api/controlpanel/search-securities")
def search_securities():
    """
    Free-text search over the in-memory securities cache.

    Query params:
      - q:    search term (matched against beehusName, mainId, ticker, taxId,
              isIn, selicCode — case/accent-insensitive, substring).
      - type: optional securityType filter. If provided but yields no hits,
              falls back to the full cache so the user is never stuck.
      - limit: max results (default 50, max 200).

    Returns: { "results": [...], "cacheCount": N, "poolCount": M, "filteredByType": bool }
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        q        = (request.args.get("q") or "").strip()
        stype    = (request.args.get("type") or "").strip()
        try:
            limit = min(int(request.args.get("limit", 50)), 200)
        except ValueError:
            limit = 50

        cache = get_cache()
        if not cache.is_loaded:
            # Prefer the on-disk snapshot (sub-second); only fall back to a
            # full MongoDB scan when no file exists. This keeps the search
            # endpoint responsive on cold start.
            if not cache.load_from_file():
                cache.load_from_db(db)

        cache_count = cache.count
        # Access the raw list through the private attribute (cache has no public iterator).
        full = cache._securities
        pool = cache.get_by_type(stype) if stype else full
        filtered_by_type = bool(stype)
        # Fallback: type filter matched nothing → search whole cache.
        if stype and not pool:
            pool = full
            filtered_by_type = False

        if not pool:
            return jsonify({
                "results": [], "cacheCount": cache_count, "poolCount": 0,
                "filteredByType": False,
            })

        results = []
        if not q:
            results = pool[:limit]
        else:
            from security_matcher import _strip_accents
            needle = _strip_accents(q.lower())
            search_fields = ("beehusName", "mainId", "ticker", "taxId", "isIn", "selicCode")
            for sec in pool:
                for field in search_fields:
                    val = sec.get(field) or ""
                    if val and needle in _strip_accents(str(val).lower()):
                        results.append(sec)
                        break
                if len(results) >= limit:
                    break

        out = []
        for s in results:
            out.append({
                "securityId":   s.get("_id", ""),
                "beehusName":   s.get("beehusName", ""),
                "mainId":       s.get("mainId", ""),
                "ticker":       s.get("ticker", ""),
                "taxId":        s.get("taxId", ""),
                "isIn":         s.get("isIn", ""),
                "selicCode":    s.get("selicCode", ""),
                "securityType": s.get("securityType", ""),
                "maturityDate": s.get("maturityDate", ""),
            })
        return jsonify({
            "results": out,
            "cacheCount": cache_count,
            "poolCount": len(pool),
            "filteredByType": filtered_by_type,
        })
    except Exception:
        import traceback
        log.error("search_securities error: %s", traceback.format_exc())
        return jsonify({"results": [], "error": "internal error"}), 500


@bp.route("/api/controlpanel/wallet-positions")
def wallet_positions():
    """Suggest securities by reading the most recent processedPosition for the
    given wallets.

    The "Identificar" modal lets the operator pick a security for an
    unprocessedId. Besides the global securities cache search, this endpoint
    powers a second source: the securities that already live in the wallet's
    most recent processed snapshot. That snapshot is the strongest signal that
    a given security is the right counterpart for an unmapped position —
    operators almost always pick from this short list when it exists.

    Query params:
      walletIds  required, comma-separated wallet ids. Multiple wallets are
                 supported because issues are grouped by unprocessedId across
                 every wallet that exhibits them.
      date       optional YYYY-MM-DD. We look up the most recent
                 processedPosition with positionDate <= date (defaults to the
                 latest available when omitted).
      q          optional free-text filter, applied after enrichment with the
                 same fields as /search-securities (beehusName, mainId, …).
      limit      max enriched rows (default 100, max 500).

    Returns: { results: [...], walletsScanned: N, securityCount: M }
    where each result mirrors the /search-securities shape and adds
    `walletIds` / `walletNames` describing which wallets carry the security.
    """
    import logging
    log = logging.getLogger(__name__)
    try:
        raw_ids = (request.args.get("walletIds") or "").strip()
        date    = (request.args.get("date") or "").strip() or None
        q       = (request.args.get("q") or "").strip()
        try:
            limit = min(int(request.args.get("limit", 100)), 500)
        except ValueError:
            limit = 100

        wallet_ids = [w.strip() for w in raw_ids.split(",") if w.strip()]
        if not wallet_ids:
            return jsonify({"results": [], "walletsScanned": 0, "securityCount": 0})

        # Resolve wallet names + enforce company visibility. Without this an
        # operator could exfiltrate positions from a wallet the company filter
        # would otherwise hide.
        wallets = list(db.wallets.find(
            {"_id": {"$in": [ObjectId(w) for w in wallet_ids if ObjectId.is_valid(w)]}},
            {"name": 1, "companyId": 1},
        ))
        cf = get_company_filter()
        visible_wallets = {}
        for w in wallets:
            wid = str(w["_id"])
            cid = str(w.get("companyId") or "")
            if cf and cid not in cf:
                continue
            visible_wallets[wid] = w.get("name") or wid
        if not visible_wallets:
            return jsonify({"results": [], "walletsScanned": 0, "securityCount": 0})

        # Latest processedPosition per wallet. We sort once per wallet so a
        # missing positionDate on the requested day falls back to the most
        # recent prior snapshot — the operator usually wants "whatever the
        # wallet last looked like" rather than an empty list.
        query_base = {"walletId": {"$in": list(visible_wallets.keys())}}
        if date:
            query_base["positionDate"] = {"$lte": date}

        # {securityId: {pu, quantity, pricingType, walletIds[], walletNames[]}}
        by_security = {}
        wallets_with_snapshot = set()
        seen_wallets = set()
        for doc in db.processedPosition.find(
            query_base,
            {"walletId": 1, "positionDate": 1, "securities.securityId": 1,
             "securities.pu": 1, "securities.quantity": 1,
             "securities.pricingType": 1},
        ).sort("positionDate", -1):
            wid = str(doc.get("walletId") or "")
            if not wid or wid in seen_wallets:
                continue
            seen_wallets.add(wid)
            wallets_with_snapshot.add(wid)
            wname = visible_wallets.get(wid, wid)
            for s in doc.get("securities") or []:
                sid = str(s.get("securityId") or "")
                if not sid:
                    continue
                entry = by_security.setdefault(sid, {
                    "pu": s.get("pu"),
                    "quantity": s.get("quantity"),
                    "pricingType": s.get("pricingType"),
                    "walletIds": [],
                    "walletNames": [],
                })
                if wid not in entry["walletIds"]:
                    entry["walletIds"].append(wid)
                    entry["walletNames"].append(wname)
            if len(seen_wallets) >= len(visible_wallets):
                break

        if not by_security:
            return jsonify({
                "results": [], "walletsScanned": len(wallets_with_snapshot),
                "securityCount": 0,
            })

        # Enrich with security metadata. Prefer the in-memory cache (already
        # used by /search-securities) to avoid hitting MongoDB on every
        # modal-open — the cache is warmed by /warmup on page load.
        cache = get_cache()
        cache_by_id = {s.get("_id"): s for s in cache._securities} if cache.is_loaded else {}

        # Fetch metadata for any sids missing from the cache (the cache is
        # rebuilt daily, but a brand-new security registered today might not
        # be there yet).
        missing_ids = [sid for sid in by_security if sid not in cache_by_id]
        if missing_ids:
            oids = []
            for sid in missing_ids:
                try:
                    oids.append(ObjectId(sid))
                except (TypeError, ValueError):
                    continue
            if oids:
                for s in db.securities.find(
                    {"_id": {"$in": oids}},
                    {"beehusName": 1, "mainId": 1, "ticker": 1, "taxId": 1,
                     "isIn": 1, "selicCode": 1, "securityType": 1,
                     "maturityDate": 1},
                ):
                    cache_by_id[str(s["_id"])] = {
                        "_id":          str(s["_id"]),
                        "beehusName":   s.get("beehusName", ""),
                        "mainId":       s.get("mainId", ""),
                        "ticker":       s.get("ticker", ""),
                        "taxId":        s.get("taxId", ""),
                        "isIn":         s.get("isIn", ""),
                        "selicCode":    s.get("selicCode", ""),
                        "securityType": s.get("securityType", ""),
                        "maturityDate": str(s.get("maturityDate") or ""),
                    }

        out = []
        for sid, info in by_security.items():
            meta = cache_by_id.get(sid, {})
            out.append({
                "securityId":   sid,
                "beehusName":   meta.get("beehusName", ""),
                "mainId":       meta.get("mainId", ""),
                "ticker":       meta.get("ticker", ""),
                "taxId":        meta.get("taxId", ""),
                "isIn":         meta.get("isIn", ""),
                "selicCode":    meta.get("selicCode", ""),
                "securityType": meta.get("securityType", ""),
                "maturityDate": str(meta.get("maturityDate", ""))[:10],
                "pu":           info["pu"],
                "quantity":     info["quantity"],
                "pricingType":  info["pricingType"],
                "walletIds":    info["walletIds"],
                "walletNames":  info["walletNames"],
            })

        # Optional free-text filter (same fields as /search-securities). We
        # apply it after enrichment so the operator can search by ticker /
        # mainId / name on the wallet's positions just like on the global
        # cadastro.
        if q:
            from security_matcher import _strip_accents
            needle = _strip_accents(q.lower())
            search_fields = ("beehusName", "mainId", "ticker", "taxId", "isIn", "selicCode")
            filtered = []
            for r in out:
                for f in search_fields:
                    val = r.get(f) or ""
                    if val and needle in _strip_accents(str(val).lower()):
                        filtered.append(r)
                        break
            out = filtered

        # Stable order: prefer those with a beehusName (i.e. enriched), then
        # sort alphabetically. Securities still pending registration would
        # otherwise pollute the top of the list with empty names.
        out.sort(key=lambda r: (not r["beehusName"], r["beehusName"].lower(), r["mainId"]))

        return jsonify({
            "results":        out[:limit],
            "walletsScanned": len(wallets_with_snapshot),
            "securityCount":  len(by_security),
        })
    except Exception:
        import traceback
        log.error("wallet_positions error: %s", traceback.format_exc())
        return jsonify({"results": [], "error": "internal error"}), 500


@bp.route("/api/controlpanel/last-price")
def last_price():
    """Return historyPrice for a single securityId.

    If `date` (YYYY-MM-DD) is provided, returns the price on that date or the
    nearest available; otherwise returns the last entry.
    """
    sid  = (request.args.get("securityId") or "").strip()
    date = (request.args.get("date") or "").strip() or None
    if not sid:
        return jsonify({"lastPrice": None})
    price_map = _batch_last_price([sid], target_date=date)
    return jsonify({"lastPrice": price_map.get(sid)})


# ── Cache endpoints ──────────────────────────────────────────────────────────

@bp.route("/api/controlpanel/cache-status")
def cache_status():
    """Return current cache state so the frontend can show refresh prompts."""
    cache = get_cache()
    return jsonify({
        "loaded":     cache.is_loaded,
        "stale":      cache.is_stale,
        "loadedDate": cache.loaded_date,
        "count":      cache.count,
        "classifierReady": _clf is not None,
    })


@bp.route("/api/controlpanel/warmup", methods=["POST"])
def warmup():
    """Pre-load classifier and cache so the first match request is fast."""
    try:
        actions = []
        # Load cache from file if today's, else from DB
        cache = get_cache()
        if not cache.is_loaded or cache.is_stale:
            if cache.load_from_file():
                actions.append("cache_from_file")
            else:
                cache.load_from_db(db)
                actions.append("cache_from_db")

        # Train classifier if not ready
        if _get_classifier() is not None:
            actions.append("classifier_ready")

        return jsonify({"ok": True, "actions": actions, "count": cache.count})
    except Exception:
        import traceback, logging
        logging.error("warmup error: %s", traceback.format_exc())
        return jsonify({"ok": False, "error": "internal error"}), 500


@bp.route("/api/controlpanel/refresh-cache", methods=["POST"])
def refresh_cache():
    """Force-reload the securities cache from MongoDB."""
    cache = get_cache()
    cache.load_from_db(db)
    _reset_matcher()  # force re-init with fresh cache
    return jsonify({
        "ok":    True,
        "count": cache.count,
        "date":  cache.loaded_date,
    })


@bp.route("/api/controlpanel/export-c3", methods=["POST"])
def export_c3():
    """Generate Excel for C3 assets selected from security_missing_price issues."""
    body  = request.get_json(force=True, silent=True) or {}
    items = body.get("items", [])

    # The "Data" column is what gets pasted into the C3 system. The frontend
    # sends the currently selected date pill (YYYY-MM-DD); convert to the
    # DD/MM/YYYY format C3 expects. Fall back to today's BRT date so a missing
    # field never silently writes a stale year.
    raw_date = (body.get("date") or "").strip()
    try:
        date_brt = date.fromisoformat(raw_date) if raw_date else today_in_brt()
    except ValueError:
        date_brt = today_in_brt()
    date_c3 = date_brt.strftime("%d/%m/%Y")

    cf = get_company_filter()
    if cf:
        items = [it for it in items if str(it.get("companyId", "")) in cf]

    wb = Workbook()
    ws = wb.active
    ws.title = "C3"

    headers = [
        "Data", "SecurityId", "EntityId",
        "CompanyId", "WalletId", "PU", "C3Automatico", "BeehusName",
    ]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for item in items:
        ws.append([
            date_c3,
            item.get("securityId", ""),
            item.get("entityId", ""),
            item.get("companyId", ""),
            item.get("walletId", ""),
            item.get("pu", 0),
            "V" if item.get("consumoAutomatico") else "",
            item.get("beehusName", ""),
        ])

    # Highlight the BeehusName column (last column) — header keeps the bold
    # font from the loop above; we just add a red fill to the whole column
    # (header + every data row) so it stands out as the "informational only"
    # field that doesn't get pasted into the C3 system.
    beehus_col = ws.cell(row=1, column=len(headers)).column_letter
    red_fill = PatternFill("solid", fgColor="FFC7CE")  # soft red, Excel-native
    red_font = Font(color="9C0006", bold=False)
    for row_idx in range(1, ws.max_row + 1):
        cell = ws[f"{beehus_col}{row_idx}"]
        cell.fill = red_fill
        # Preserve bold on header; only restyle data rows.
        if row_idx == 1:
            cell.font = Font(bold=True, color="9C0006")
        else:
            cell.font = red_font

    buf = io.BytesIO()
    wb.save(buf)

    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    resp = make_response(buf.getvalue())
    resp.headers["Content-Type"] = mime
    resp.headers["Content-Disposition"] = "attachment; filename=c3_registro_preco.xlsx"
    return resp


@bp.route("/api/controlpanel/rebuild-mapping", methods=["POST"])
def rebuild():
    from security_type_classifier import rebuild_mapping
    total, mapped = rebuild_mapping(db)
    reset_classifier()
    _reset_matcher()
    # Also refresh cache since securities may have changed
    get_cache().load_from_db(db)
    return jsonify({"total": total, "mapped": mapped})
