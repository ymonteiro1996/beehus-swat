"""Exceções — manage per-wallet position-stripping rules.

Captures the case where one wallet's `unprocessedSecurityPositions` should
be split across other wallets on every business day. The user defines a
reusable exception once (source wallet, output wallets, per-security
add/remove rules) and the daily routine applies it: it reads the source
wallet's position for a chosen date, rewrites the affected wallets'
positions, and uploads each as an Excel file via
`POST /beehus/financial/positions/unprocessed-security-positions/file`.

Storage layout:

    data/excecoes/<companyId>/<exceptionId>.json
    {
      "id":              "uuid",
      "companyId":       "...",
      "name":            "...",
      "kind":            "position_strip",
      "sourceWalletId":  "...",
      "outputWalletIds": ["...", "..."],
      "rules": [
        {
          "unprocessedId":      "...",
          "addToWalletId":      "..." | null,
          "removeFromWalletId": "..." | null,
          "caixa":              false
        }
      ],
      "createdAt": "ISO-8601",
      "updatedAt": "ISO-8601",
      "lastApplied": {"date": "YYYY-MM-DD", "at": "ISO-8601"} | null
    }

Only `position_strip` is implemented for now; `kind` is the seam that
lets future exception types live in the same store.
"""
import io
import json
import os
import re
import uuid
from datetime import datetime

from flask import Blueprint, jsonify, render_template, request, send_file
from openpyxl import Workbook

from db import (get_company_filter, company_visible, get_company_names, get_grouping_index,
                get_security_names, resolve_wallet, atomic_write_json)
from beehus_api import (
    upload_unprocessed_security_positions_file,
    update_transaction,
    create_transaction,
    create_provision,
    update_provision,
    process_processed_position,
    calculate_nav_wallets,
    BeehusAPIError, BeehusAuthError,
)
# Re-uses the precificacao curva engine when the operator opts in via
# `useCurvaPrice` on a `position_strip` exception. Imported lazily-shaped
# (only the building blocks) to avoid pulling repetir_posicoes' surface in.
import beehus_catalog
from pages.precificacao import calculate_curva, _load_lists


bp = Blueprint("excecoes", __name__)

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "excecoes"))

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_KIND_POSITION_STRIP    = "position_strip"
_KIND_WALLET_SLICE      = "wallet_slice"
_KIND_CLASS_STRIP       = "class_strip"
_KINDS = (
    _KIND_POSITION_STRIP,
    _KIND_WALLET_SLICE,
    _KIND_CLASS_STRIP,
)

# class_strip migration filters — see docs/EXCECOES.md § Stripping por classe.
# Pulled out as module constants so the plan builder and apply share the same
# whitelist (drift between the two surfaces would silently miss migrations).
_CLASS_STRIP_PROV_TYPES = ("dividend", "interestOnEquity")
_CLASS_STRIP_TXN_TYPES  = ("coupon", "amortization", "securityContributionAdjustment")


# ── Path / IO helpers ─────────────────────────────────────────────────────────

def _safe(segment):
    if not segment or not _SAFE_ID_RE.match(str(segment)):
        raise ValueError(f"invalid segment: {segment!r}")
    return str(segment)


def _safe_date(date):
    if not date or not _SAFE_DATE_RE.match(str(date)):
        raise ValueError(f"invalid date: {date!r}")
    return str(date)


def _company_dir(company_id):
    return os.path.join(_ROOT, _safe(company_id))


def _exception_file(company_id, exception_id):
    return os.path.join(_company_dir(company_id), f"{_safe(exception_id)}.json")


def _wallet_in_company(wallet_id, expected_company_id):
    if not wallet_id or not expected_company_id:
        return False
    w = resolve_wallet(wallet_id, {"companyId": 1})
    return bool(w) and str(w.get("companyId", "")) == str(expected_company_id)


def _wallet_visible(wallet_id):
    """Returns the wallet's `companyId` if the wallet exists AND its
    company is in `company_filter` (visible to the current operator);
    `None` otherwise. Used by `position_strip.outputWalletIds` and
    `wallet_slice.targetWalletId` validators, which now accept
    cross-company target wallets desde que ambas as empresas estejam no
    filtro de visibilidade do operador.
    """
    if not wallet_id:
        return None
    w = resolve_wallet(wallet_id, {"companyId": 1})
    if not w:
        return None
    cid = str(w.get("companyId") or "")
    if not cid or not company_visible(cid):
        return None
    return cid


def _load(company_id, exception_id):
    path = _exception_file(company_id, exception_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _save(blob):
    company_id = _safe(blob["companyId"])
    exception_id = _safe(blob["id"])
    os.makedirs(_company_dir(company_id), exist_ok=True)
    blob["updatedAt"] = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    atomic_write_json(_exception_file(company_id, exception_id), blob, indent=2)


def _delete_file(company_id, exception_id):
    path = _exception_file(company_id, exception_id)
    if os.path.isfile(path):
        os.remove(path)
    try:
        os.rmdir(_company_dir(company_id))
    except OSError:
        pass


def _iter_exceptions(company_ids):
    for cid in company_ids:
        try:
            safe_cid = _safe(cid)
        except ValueError:
            continue
        d = os.path.join(_ROOT, safe_cid)
        if not os.path.isdir(d):
            continue
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for name in names:
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, name), "r", encoding="utf-8") as f:
                    yield json.load(f)
            except (OSError, json.JSONDecodeError):
                continue


def _visible_company_ids():
    cf = get_company_filter()
    ids = set(get_company_names().keys())
    # As exceções são dados LOCAIS (data/excecoes/<companyId>/). Inclui também
    # as empresas que têm exceção salva em disco, para que a listagem não suma
    # quando get_company_names() volta vazio (ex.: token 401 / API instável).
    # Sem isto, um token expirado esconde TODAS as exceções locais.
    try:
        ids |= {name for name in os.listdir(_ROOT)
                if os.path.isdir(os.path.join(_ROOT, name))}
    except OSError:
        pass
    if not cf:
        return sorted(ids)
    return sorted(i for i in ids if i in cf)


# ── Source-position helpers ───────────────────────────────────────────────────

def _aggregate_unprocessed(securities):
    """Group raw `securities` rows by `unprocessedId`, summing qty/balance and
    weight-averaging PU. Mirrors `pages/posicoes._group_unprocessed` so the
    user sees the same "ativos" the rest of the app sees."""
    grouped = {}
    for s in securities or []:
        uid = (s.get("unprocessedId") or "").strip()
        if not uid:
            continue
        qty = float(s.get("quantity") or 0)
        pu = float(s.get("pu") or 0)
        bal = s.get("balance")
        bal = float(bal) if bal is not None else pu * qty
        g = grouped.setdefault(uid, {
            "unprocessedId": uid,
            "quantity": 0.0,
            "balance": 0.0,
        })
        g["quantity"] += qty
        g["balance"] += bal
    for g in grouped.values():
        g["pu"] = (g["balance"] / g["quantity"]) if g["quantity"] else 0.0
    return list(grouped.values())


def _build_unprocessed_to_security_map(company_id):
    """Return `{unprocessedId: securityId}` for a company.

    Mirrors `pages/posicoes._build_mapping` so the strip rules (keyed by
    `unprocessedId`) can find the matching `securityId` on transactions —
    which are stored without the `unprocessedId` field."""
    doc = beehus_catalog.security_mappings_doc(company_id)
    if not doc:
        return {}
    return {m["from"]: m["to"]
            for m in (doc.get("mappings") or [])
            if m.get("from") and m.get("to")}


def _fetch_source_position(wallet_id, date):
    """Return the aggregated `unprocessedSecurityPositions` for a wallet on a
    date — or `None` if no document exists. Tries the requested date first
    and does not silently fall back; the caller decides what to do on miss."""
    doc = beehus_catalog.unprocessed_doc(wallet_id, date)
    if not doc:
        return None
    return _aggregate_unprocessed(doc.get("securities") or [])


# ── Page route ────────────────────────────────────────────────────────────────

@bp.route("/excecoes")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("excecoes.html", companies=companies)


@bp.route("/stripping")
def stripping():
    """Página dedicada para Position Stripping.

    Mesmo painel que o chip "Stripping" do Painel de Controle abre
    inline — só que aqui é uma página própria (acessível pela URL e
    pelo menu lateral) em vez de uma view oculta dentro do dashboard.

    O HTML/CSS/JS são reaproveitados via Jinja includes em
    `templates/partials/_strip_*.html`. Os endpoints `/api/excecoes/*`
    desta blueprint atendem ambas as telas.
    """
    return render_template("stripping.html")


# ── List / Read ──────────────────────────────────────────────────────────────

@bp.route("/api/excecoes")
def list_exceptions():
    """List every exception visible to the user, with company + wallet names."""
    company_ids = _visible_company_ids()
    items = list(_iter_exceptions(company_ids))

    # Prefetch wallet names across every wallet the listed exceptions touch.
    wallet_ids = set()
    for it in items:
        sw = it.get("sourceWalletId")
        if sw:
            wallet_ids.add(sw)
        for w in it.get("outputWalletIds") or []:
            wallet_ids.add(w)
        # wallet_slice exceptions store a single targetWalletId; add it so
        # the row can show the destination's name in the list.
        tw = it.get("targetWalletId")
        if tw:
            wallet_ids.add(tw)
        # class_strip exceptions store N source wallets + classRoutes with
        # one target per variable1. Pull both sides into the name prefetch
        # so the listing row can render whichever subset the UI needs.
        for w in it.get("sourceWalletIds") or []:
            wallet_ids.add(w)
        for r in it.get("classRoutes") or []:
            tw = r.get("targetWalletId") if isinstance(r, dict) else None
            if tw:
                wallet_ids.add(tw)
    wallet_names = {}
    if wallet_ids:
        for wid in wallet_ids:
            swid = beehus_catalog.id_str(wid)
            wallet_names[swid] = (beehus_catalog.wallet_doc(swid) or {}).get("name") or swid

    # NOTE: the "Data Base" of each stripping run is an editable date entered
    # by the operator (defaults to today in the UI). It is no longer seeded
    # from `processedPosition.positionDate` — that Mongo read was removed as
    # part of the Mongo shutdown; the apply flow only ever needed the date the
    # operator chose, not the latest processed position.
    company_names = get_company_names()
    rows = []
    for it in sorted(items, key=lambda x: (x.get("companyId", ""), x.get("name", ""))):
        sw = it.get("sourceWalletId", "")
        kind = it.get("kind") or _KIND_POSITION_STRIP
        tw = it.get("targetWalletId", "") if kind == _KIND_WALLET_SLICE else ""

        # class_strip rows carry the multi-source list + classRoutes through
        # to the UI. The "Origem" column shows the first wallet plus a "+N"
        # suffix in the front-end; "Saídas / Destino" enumerates the unique
        # targets across routes.
        src_ids = list(it.get("sourceWalletIds") or [])
        routes_raw = it.get("classRoutes") or []
        class_routes = []
        unique_targets = []
        seen_t = set()
        for r in routes_raw:
            if not isinstance(r, dict):
                continue
            t = r.get("targetWalletId") or ""
            class_routes.append({
                "variable1":       r.get("variable1") or "",
                "targetWalletId":  t,
                "targetWalletName": wallet_names.get(t, t) if t else "",
            })
            if t and t not in seen_t:
                seen_t.add(t)
                unique_targets.append(t)

        rows.append({
            "id":              it.get("id"),
            "companyId":       it.get("companyId"),
            "companyName":     company_names.get(it.get("companyId", ""), it.get("companyId")),
            "name":            it.get("name") or "",
            "kind":            kind,
            "sourceWalletId":  sw,
            "sourceWalletName": wallet_names.get(sw, sw),
            "outputWalletIds": it.get("outputWalletIds") or [],
            "outputWalletNames": [wallet_names.get(wid, wid) for wid in (it.get("outputWalletIds") or [])],
            "ruleCount":       len(it.get("rules") or []),
            # position_strip opt-in: replace unprocessedPosition PUs with the
            # curva engine output during preview/apply. Surfaced here so the
            # edit modal can re-tick the checkbox.
            "useCurvaPrice":   bool(it.get("useCurvaPrice")) if kind == _KIND_POSITION_STRIP else False,
            # wallet_slice-only fields. Hidden columns on position_strip
            # rows by returning the empty defaults — the front-end checks
            # `kind` before rendering them anyway.
            "targetWalletId":   tw,
            "targetWalletName": wallet_names.get(tw, tw) if tw else "",
            "percent":          it.get("percent") if kind == _KIND_WALLET_SLICE else None,
            # class_strip-only fields. Empty defaults on other kinds so the
            # JS can guard on `kind` without worrying about `undefined`.
            "sourceWalletIds":  src_ids,
            "sourceWalletNames": [wallet_names.get(w, w) for w in src_ids],
            "classRoutes":      class_routes,
            "uniqueTargetIds":  unique_targets,
            "uniqueTargetNames": [wallet_names.get(t, t) for t in unique_targets],
            "lastApplied":     it.get("lastApplied"),
            "updatedAt":       it.get("updatedAt"),
        })
    return jsonify({"exceptions": rows})


def _wallet_docs(wallet_ids):
    """`{walletId_str: wallet_doc}` para os ids dados, via `beehus_catalog`
    (índice global da API). Substitui `db.wallets.find({_id: {$in: ...}})`.
    O doc traz name/currency/currencyId/entityId/companyId (currencyId derivado
    de `currency` no catalog). Ids ausentes do índice são omitidos — mesmo
    comportamento do `find` (só devolve os existentes)."""
    out = {}
    for wid in {str(w) for w in (wallet_ids or []) if w}:
        d = beehus_catalog.wallet_doc(wid)
        if d:
            out[wid] = d
    return out


@bp.route("/api/excecoes/<exception_id>")
def get_exception(exception_id):
    company_id = request.args.get("companyId", "")
    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe(exception_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    blob = _load(company_id, exception_id)
    if not blob:
        return jsonify({"error": "not found"}), 404

    # Resolve names for every wallet the exception touches.
    wids = set([blob.get("sourceWalletId")] + list(blob.get("outputWalletIds") or []))
    if blob.get("targetWalletId"):
        wids.add(blob["targetWalletId"])
    # class_strip-specific wallet set: every source + every route target.
    for w in blob.get("sourceWalletIds") or []:
        wids.add(w)
    for r in blob.get("classRoutes") or []:
        t = r.get("targetWalletId") if isinstance(r, dict) else None
        if t:
            wids.add(t)
    wids.discard(None); wids.discard("")
    wallet_names = {}
    for wid in wids:
        swid = beehus_catalog.id_str(wid)
        wallet_names[swid] = (beehus_catalog.wallet_doc(swid) or {}).get("name") or swid

    return jsonify({"exception": blob, "walletNames": wallet_names})


# ── Wallet picker ─────────────────────────────────────────────────────────────

@bp.route("/api/excecoes/wallets")
def list_company_wallets():
    """Return wallets for the picker. Two modes:

    - `?companyId=<id>` (legacy, default): wallets de UMA empresa, sem
      `companyId` por item (a empresa é implícita).
    - `?crossCompany=1`: wallets de **todas** as empresas visíveis ao
      operador (`company_filter`), com `companyId` + `companyName` em
      cada item. Usado pelos pickers de `outputWalletIds` (position_strip)
      e `targetWalletId` (wallet_slice) que aceitam carteiras de outra
      empresa.
    """
    cross_company = request.args.get("crossCompany") in ("1", "true", "yes")
    if cross_company:
        cf = get_company_filter()
        company_names = get_company_names() or {}
        wallets = []
        for w in beehus_catalog.wallets_index().values():
            cid = str(w.get("companyId") or "")
            if cf and cid not in cf:
                continue
            wallets.append({
                "id":          w["_id"],
                "name":        w.get("name") or w["_id"],
                "currencyId":  _wallet_currency(w),
                "companyId":   cid,
                "companyName": company_names.get(cid, cid),
            })
        wallets.sort(key=lambda x: (x["companyName"].lower(), x["name"].lower()))
        return jsonify({"wallets": wallets})

    company_id = request.args.get("companyId", "")
    if not company_id or not company_visible(company_id):
        return jsonify({"wallets": []})
    wallets = []
    for w in beehus_catalog.wallets_in_company(company_id):
        wallets.append({
            "id":         w["_id"],
            "name":       w.get("name") or w["_id"],
            "currencyId": _wallet_currency(w),
        })
    wallets.sort(key=lambda x: x["name"])
    return jsonify({"wallets": wallets})


# ── Source position view (used by setup wizard) ──────────────────────────────

@bp.route("/api/excecoes/source-position")
def source_position():
    """Return the aggregated unprocessed position for a wallet on a date so
    the setup wizard can list securities the user might want to strip."""
    company_id = request.args.get("companyId", "")
    wallet_id = request.args.get("walletId", "")
    date = request.args.get("date", "")
    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe(wallet_id); _safe_date(date)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not _wallet_in_company(wallet_id, company_id):
        return jsonify({"error": "wallet/company mismatch"}), 403

    secs = _fetch_source_position(wallet_id, date) or []
    secs.sort(key=lambda s: s.get("unprocessedId") or "")
    return jsonify({"date": date, "walletId": wallet_id, "securities": secs})


# ── class_strip — variable1 discovery ────────────────────────────────────────

def _fetch_processed_with_fallback(wallet_id, target_date, max_fallback=5):
    """Return `(securities, date_used, fallback)` for a wallet's processed
    position. Tries the requested date first, then walks back day-by-day
    up to `max_fallback` days. `securities` is the raw `securities[]` array
    from the doc (None if no doc found in the window).

    Mirrors `_resolve_source_with_fallback` (which targets the *unprocessed*
    collection); class_strip needs the processed one for `variable1`,
    `amountDifference` and `executionPrice` — all absent from
    unprocessedSecurityPositions."""
    if not wallet_id or not target_date:
        return None, target_date, False
    from datetime import date as _date, timedelta
    try:
        base = _date.fromisoformat(target_date)
    except (TypeError, ValueError):
        return None, target_date, False

    for offset in range(0, max_fallback + 1):
        probe = (base - timedelta(days=offset)).isoformat()
        doc = beehus_catalog.processed_doc(wallet_id, probe)
        if doc:
            return (doc.get("securities") or []), probe, (offset > 0)
    return None, target_date, False


@bp.route("/api/excecoes/class-strip/variables")
def class_strip_variables():
    """Return the unique `hierarchicalVariable.variable1` values found in
    the processed position of the given wallets on a target date (with
    fallback to the most-recent processed position up to 5 days earlier).

    Powers the `+ Nova rota` autocomplete in the class_strip setup wizard.
    Free-form input is still allowed on the front-end — this endpoint just
    surfaces what's currently in the data so the operator doesn't have to
    guess the exact spelling/accents."""
    company_id = (request.args.get("companyId") or "").strip()
    date = (request.args.get("date") or "").strip()
    raw_wallets = (request.args.get("walletIds") or "").strip()

    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id)
        _safe_date(date)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Comma-separated list — single-pass split + dedup + safety check. Any
    # malformed walletId aborts the request (the front-end is expected to
    # only pass canonical ObjectId strings).
    wallet_ids = [w.strip() for w in raw_wallets.split(",") if w.strip()]
    wallet_ids = list(dict.fromkeys(wallet_ids))
    for w in wallet_ids:
        try:
            _safe(w)
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        if not _wallet_in_company(w, company_id):
            return jsonify({"error": f"wallet {w!r} not in company"}), 403

    if not wallet_ids:
        return jsonify({"date": date, "fallback": False, "variables": []})

    # Aggregate over (wallet, latestDate) pairs — each wallet may need its
    # own fallback date. The "fallback" flag at the top level is True if
    # ANY wallet had to fall back; per-wallet detail isn't surfaced (the
    # UI just needs the option list).
    any_fallback = False
    by_var = {}   # variable1 -> {count, totalBalance}
    for w in wallet_ids:
        secs, used_date, fallback = _fetch_processed_with_fallback(w, date)
        if fallback:
            any_fallback = True
        if not secs:
            continue
        for s in secs:
            hv = s.get("hierarchicalVariable") or {}
            v1 = (hv.get("variable1") or "").strip()
            if not v1:
                continue
            try:
                bal = float(s.get("balance") or 0)
            except (TypeError, ValueError):
                bal = 0.0
            agg = by_var.setdefault(v1, {"count": 0, "totalBalance": 0.0})
            agg["count"] += 1
            agg["totalBalance"] += bal

    out = []
    for v1, agg in by_var.items():
        out.append({
            "variable1":    v1,
            "count":        agg["count"],
            "totalBalance": _round(agg["totalBalance"], 2),
        })
    out.sort(key=lambda r: r["variable1"].lower())
    return jsonify({
        "date":     date,
        "fallback": any_fallback,
        "variables": out,
    })


# ── Validation helpers for save/preview/apply ────────────────────────────────

def _validate_payload(body):
    company_id = (body.get("companyId") or "").strip()
    name       = (body.get("name") or "").strip()
    src_w      = (body.get("sourceWalletId") or "").strip()
    out_ws     = list(dict.fromkeys(body.get("outputWalletIds") or []))
    rules_in   = body.get("rules") or []

    if not company_id or not company_visible(company_id):
        raise ValueError("forbidden")
    _safe(company_id)
    if not name:
        raise ValueError("name required")
    if not src_w or not _wallet_in_company(src_w, company_id):
        raise ValueError("invalid sourceWalletId")
    if not out_ws:
        raise ValueError("outputWalletIds required")
    # Cross-company allowed: cada output só precisa existir e a empresa
    # dele estar visível ao operador (via `company_filter`). Não exigimos
    # mais que `outputWalletIds` pertençam à mesma empresa de
    # `sourceWalletId` — strip pode mover ativos pra carteira de outra
    # empresa desde que o operador tenha permissão nas duas.
    for w in out_ws:
        if not _wallet_visible(w):
            raise ValueError(f"output wallet {w!r} não existe ou empresa não visível")

    allowed = set(out_ws) | {src_w}
    rules = []
    for r in rules_in:
        uid = (r.get("unprocessedId") or "").strip()
        if not uid:
            continue
        add_to    = r.get("addToWalletId") or None
        remove_fr = r.get("removeFromWalletId") or None
        if add_to and add_to not in allowed:
            raise ValueError(f"addToWalletId {add_to!r} not in allowed wallets")
        if remove_fr and remove_fr not in allowed:
            raise ValueError(f"removeFromWalletId {remove_fr!r} not in allowed wallets")
        if not add_to and not remove_fr:
            # Skip no-op rules silently rather than fail — the user may have
            # toggled a security on, then left both dropdowns empty.
            continue
        rules.append({
            "unprocessedId":      uid,
            "addToWalletId":      add_to,
            "removeFromWalletId": remove_fr,
            "caixa":              bool(r.get("caixa")),
        })
    if not rules:
        raise ValueError("at least one rule with addTo or removeFrom is required")

    return {
        "companyId":       company_id,
        "name":            name,
        "sourceWalletId":  src_w,
        "outputWalletIds": out_ws,
        "rules":           rules,
        # Opt-in: override unprocessedPosition PUs with the curve PU
        # computed by the precificacao engine over the wallet's saved
        # template. `_build_plan` consulta `_compute_curva_pu_map` quando
        # ligado e marca cada row com `priceSource: "curva" | "unprocessed"`.
        "useCurvaPrice":   bool(body.get("useCurvaPrice")),
    }


def _validate_payload_slice(body):
    """Validator for the `wallet_slice` exception kind.

    Body shape: `{companyId, name, sourceWalletId, targetWalletId, percent}`.
    `percent` is interpreted as a percentage (0 < p ≤ 100); applying it
    scales source securities/cashAccount/provisions/transactions onto the
    target wallet. Source and target must belong to the same company and
    be different wallets (slicing onto self would be a no-op upload that
    risks clobbering the source's position with a partial copy)."""
    company_id = (body.get("companyId") or "").strip()
    name       = (body.get("name") or "").strip()
    src_w      = (body.get("sourceWalletId") or "").strip()
    tgt_w      = (body.get("targetWalletId") or "").strip()
    raw_pct    = body.get("percent")

    if not company_id or not company_visible(company_id):
        raise ValueError("forbidden")
    _safe(company_id)
    if not name:
        raise ValueError("name required")
    if not src_w or not _wallet_in_company(src_w, company_id):
        raise ValueError("invalid sourceWalletId")
    # Cross-company allowed no target: o slice envia a fatia para uma
    # carteira que pode estar em outra empresa, desde que essa empresa
    # esteja no filtro de visibilidade do operador. O `_apply_slice`
    # resolve a `companyId` real da target em runtime e roteia o upload
    # de posições + provisions + transactions pra ela.
    if not tgt_w or not _wallet_visible(tgt_w):
        raise ValueError("invalid targetWalletId")
    if src_w == tgt_w:
        raise ValueError("sourceWalletId and targetWalletId must differ")
    try:
        pct = float(raw_pct)
    except (TypeError, ValueError):
        raise ValueError("percent must be a number")
    if not (0 < pct <= 100):
        raise ValueError("percent must be in (0, 100]")

    return {
        "companyId":       company_id,
        "name":            name,
        "sourceWalletId":  src_w,
        "targetWalletId":  tgt_w,
        "percent":         pct,
    }


def _validate_payload_class(body):
    """Validator for the `class_strip` exception kind.

    Body shape: `{companyId, name, sourceWalletIds: [...], classRoutes: [
        {variable1, targetWalletId}, ...
    ]}`. Validates that every wallet belongs to the company, that
    sourceWalletIds is non-empty + unique, that classRoutes is non-empty
    with unique `variable1` values, and that no `targetWalletId` overlaps
    with `sourceWalletIds` (a wallet stripping into itself would be a
    no-op upload at best, a duplicate-securities upload at worst)."""
    company_id = (body.get("companyId") or "").strip()
    name       = (body.get("name") or "").strip()
    src_raw    = body.get("sourceWalletIds") or []
    routes_raw = body.get("classRoutes") or []

    if not company_id or not company_visible(company_id):
        raise ValueError("forbidden")
    _safe(company_id)
    if not name:
        raise ValueError("name required")

    # Deduplicate sourceWalletIds while preserving order — order doesn't
    # affect semantics (the apply iterates the list deterministically by
    # walletId), but dedup avoids the same wallet being uploaded twice.
    src_ws = list(dict.fromkeys(str(w).strip() for w in src_raw if w))
    if not src_ws:
        raise ValueError("sourceWalletIds required")
    for w in src_ws:
        if not _wallet_in_company(w, company_id):
            raise ValueError(f"source wallet {w!r} not in company")
    src_set = set(src_ws)

    if not isinstance(routes_raw, list) or not routes_raw:
        raise ValueError("classRoutes required")

    routes = []
    seen_vars = set()
    for r in routes_raw:
        if not isinstance(r, dict):
            raise ValueError("classRoutes entries must be objects")
        # `variable1` is matched verbatim against
        # `processedPosition.securities[].hierarchicalVariable.variable1`
        # at apply time — accents/case must match what the upstream wrote.
        var1 = (r.get("variable1") or "").strip()
        tgt  = (r.get("targetWalletId") or "").strip()
        if not var1:
            raise ValueError("classRoutes[].variable1 required")
        if var1 in seen_vars:
            raise ValueError(f"duplicate variable1 in classRoutes: {var1!r}")
        seen_vars.add(var1)
        if not tgt or not _wallet_in_company(tgt, company_id):
            raise ValueError(f"invalid targetWalletId for variable1 {var1!r}: {tgt!r}")
        if tgt in src_set:
            # A target that is also a source would either no-op (the upload
            # zeroes the same wallet it's filling) or, if processed in the
            # wrong order, double-count the migrated securities. Refuse it.
            raise ValueError(f"targetWalletId {tgt!r} overlaps sourceWalletIds")
        routes.append({"variable1": var1, "targetWalletId": tgt})

    return {
        "companyId":       company_id,
        "name":            name,
        "sourceWalletIds": src_ws,
        "classRoutes":     routes,
    }


# ── Create / Update / Delete ──────────────────────────────────────────────────

def _kind_from_body(body):
    """Pick the exception kind from the payload, defaulting to the
    legacy `position_strip` so the old front-end (which never sent a
    `kind`) still works. Unknown kinds are rejected — every new kind
    must be wired into `_KINDS` and have a matching validator."""
    k = (body.get("kind") or _KIND_POSITION_STRIP).strip()
    if k not in _KINDS:
        raise ValueError(f"unknown kind: {k!r}")
    return k


def _validate_by_kind(kind, body):
    """Dispatch to the right validator for a payload kind. Centralizing
    this keeps create_exception / update_exception in sync — a new kind
    only needs to be added here, not in two places."""
    if kind == _KIND_WALLET_SLICE:
        return _validate_payload_slice(body)
    if kind == _KIND_CLASS_STRIP:
        return _validate_payload_class(body)
    return _validate_payload(body)


@bp.route("/api/excecoes", methods=["POST"])
def create_exception():
    body = request.get_json() or {}
    try:
        kind = _kind_from_body(body)
        clean = _validate_by_kind(kind, body)
    except ValueError as e:
        msg = str(e)
        return jsonify({"error": msg}), 403 if msg == "forbidden" else 400

    blob = {
        "id":              str(uuid.uuid4()),
        "kind":            kind,
        "createdAt":       datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "lastApplied":     None,
        **clean,
    }
    _save(blob)
    return jsonify({"exception": blob}), 201


@bp.route("/api/excecoes/<exception_id>", methods=["PUT"])
def update_exception(exception_id):
    try:
        _safe(exception_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    body = request.get_json() or {}
    try:
        kind = _kind_from_body(body)
        clean = _validate_by_kind(kind, body)
    except ValueError as e:
        msg = str(e)
        return jsonify({"error": msg}), 403 if msg == "forbidden" else 400

    existing = _load(clean["companyId"], exception_id)
    if not existing:
        return jsonify({"error": "not found"}), 404

    # Switching kinds on an existing exception would silently strip
    # fields that only belong to the old kind. Refuse the update so the
    # operator deletes + recreates instead.
    existing_kind = existing.get("kind") or _KIND_POSITION_STRIP
    if existing_kind != kind:
        return jsonify({"error": f"cannot change kind ({existing_kind} → {kind})"}), 400

    blob = {
        **existing,
        **clean,
        "id":   exception_id,
        "kind": kind,
    }
    _save(blob)
    return jsonify({"exception": blob})


@bp.route("/api/excecoes/<exception_id>", methods=["DELETE"])
def delete_exception(exception_id):
    company_id = request.args.get("companyId", "")
    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe(exception_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not _load(company_id, exception_id):
        return jsonify({"error": "not found"}), 404
    _delete_file(company_id, exception_id)
    return jsonify({"ok": True})


# ── Daily routine: preview + apply ────────────────────────────────────────────

def _resolve_source_with_fallback(wallet_id, target_date):
    """Try `target_date` first, then walk back up to 5 calendar days for a
    document. The `unprocessedSecurityPositions` collection sometimes lags a
    day behind during weekends/holidays, so the user's selected business day
    may not have a doc even though the wallet is valid. Returns
    `(securities, used_date, fallback)`."""
    from datetime import date as _date, timedelta as _td
    try:
        d0 = _date.fromisoformat(target_date)
    except ValueError:
        return None, None, False
    for i in range(0, 6):
        d = (d0 - _td(days=i)).isoformat()
        secs = _fetch_source_position(wallet_id, d)
        if secs is not None:
            return secs, d, (i > 0)
    return None, None, False


def _build_plan(blob, target_date, fx_overrides=None):
    """Compute the per-wallet write plan for `target_date`. Returns dict:
    {
      "sourceDate": "...",       # date actually used for source securities
      "fallback":   bool,        # True if sourceDate != target_date
      "missingRules": [...],     # rule unprocessedIds absent from source
      "wallets": {
        walletId: {
          "rows": [ {unprocessedId, quantity, pu, balance, caixa, currencyId, walletId, op} ],
          "currencyId": "..."
        }
      }
    }
    """
    src_w = blob["sourceWalletId"]
    rules = blob.get("rules") or []
    out_ws = list(blob.get("outputWalletIds") or [])

    src_secs, src_date, fallback = _resolve_source_with_fallback(src_w, target_date)
    if src_secs is None:
        return {"error": f"sem unprocessedSecurityPosition para {src_w} em {target_date} (ou nos 5 dias anteriores)"}

    by_uid = {s["unprocessedId"]: s for s in src_secs}

    # Currency map: every wallet referenced by the plan, looked up via the
    # API wallet index. `_wallet_currency` prefers the wallet's `currency`
    # code (fallback "BRL") so the preview never renders a blank "Moeda".
    referenced = {w for w in ({src_w} | set(out_ws)) if w}
    cur_map = {wid: _wallet_currency(w) for wid, w in _wallet_docs(referenced).items()}

    # Existing positions for every wallet that may be touched (outputs and
    # the source itself — a rule's `removeFromWalletId` can be the source
    # wallet, in which case we also need to display its post-strip view).
    candidates = list(dict.fromkeys(out_ws + [src_w]))
    positions = {}
    for w in candidates:
        existing = _fetch_source_position(w, target_date) or []
        positions[w] = {s["unprocessedId"]: dict(s) for s in existing}

    # `ops[walletId][uid]` tracks how a uid in a given wallet was touched by
    # the rules: "added", "removed", or "both" (one rule added it, another
    # removed it). Untouched baseline rows have no entry. The UI uses this
    # to mark each row and to label the wallet in the preview header.
    ops = {w: {} for w in candidates}

    def _record_op(wallet, uid, op):
        cur = ops[wallet].get(uid)
        if cur is None or cur == op:
            ops[wallet][uid] = op
        else:
            ops[wallet][uid] = "both"

    # Source securities are valued in the source wallet's currency. A rule
    # that moves an asset into a wallet pricing in a different currency must
    # convert PU/balance via fxRates on the target date — quantity is
    # currency-agnostic and never converts, and the per-wallet `pu` below is
    # re-derived from the (converted) balance ÷ quantity. A missing rate is
    # fatal: `fx_errors` short-circuits the whole plan so nothing is ever
    # uploaded with an unconverted value.
    src_cur = cur_map.get(src_w)
    fx_pending = []   # list of (from, to, date) tuples uncovered by fxRates+overrides

    def _conv_bal(value, to_w):
        """Convert a source-currency balance into wallet `to_w`'s currency.
        Returns None (and records the missing pair in `fx_pending`) when no
        stored rate or operator override covers it — the caller surfaces
        the pairs to the UI for manual input via `#sp-fx-prompt`."""
        to_cur = cur_map.get(to_w)
        if value is None:
            return 0.0
        if not src_cur or not to_cur or src_cur == to_cur:
            return value
        rate = _fx_rate(src_cur, to_cur, target_date, overrides=fx_overrides)
        if rate is None:
            fx_pending.append((src_cur, to_cur, target_date))
            return None
        return value * rate

    # Curva PU override (operator opt-in via the setup checkbox). The
    # actual substitution happens **after** all rules apply, in a single
    # pass over every wallet's positions (baseline + touched rows) — so
    # the curva is the source of truth for whichever line has a curva
    # entry for `(walletId, securityId, target_date)`, regardless of
    # whether that line was added, removed, or just rode along untouched.
    # See the `curva_priced` pass below.
    use_curva    = bool(blob.get("useCurvaPrice"))
    # Filtro de input da engine: como o lookup downstream é estrito por
    # walletId, só precisamos processar securities cujo `walletId` no
    # template esteja entre as wallets do plano (origem + saídas). Corta
    # significativamente o trabalho da `calculate_curva` em bases com
    # templates grandes.
    curva_pu_map = (
        _compute_strip_curva_pu_map(target_date, allowed_wallets={src_w, *out_ws})
        if use_curva else {}
    )
    uid_to_sec   = _build_unprocessed_to_security_map(blob["companyId"]) if use_curva else {}
    curva_priced = set()   # set of (walletId, unprocessedId) tuples re-priced from curva

    # Apply the rules.
    missing_rules    = []
    # uids cuja origem (`by_uid[uid]`) trouxe quantity/balance zerados — a
    # regra dispara mas contribui 0 pro destino (e zero a remover da
    # origem). Surfaceia no preview pra evitar o falso positivo de "FX
    # não converteu" quando na verdade não há nada pra mover.
    src_empty_uids = set()
    # Pares `(walletId, unprocessedId)` cuja baseline da carteira destino
    # foi descartada pela semântica de "replace" (operator-confirmada para
    # exceções position_strip). Quando uma regra adiciona um ativo com
    # contribuição não-zero a uma wallet destino, a linha pré-existente
    # baseline daquele uid é zerada na primeira regra que tocar o par,
    # de modo que o PU final reflete só a contribuição convertida da
    # origem (sem média ponderada com o baseline). Regras subsequentes
    # sobre o mesmo (add_to, uid) acumulam normalmente em cima do zero.
    # Origem vazia (`srcEmpty`) NÃO dispara replace — o baseline fica
    # intacto e o pill já avisa o operador que a regra não moveu nada.
    replaced_baseline = set()
    for rule in rules:
        uid = rule["unprocessedId"]
        src = by_uid.get(uid)
        if src is None:
            missing_rules.append(uid)
            continue
        qty = float(src.get("quantity") or 0)
        pu  = float(src.get("pu") or 0)
        bal = float(src.get("balance") or (qty * pu))
        if qty == 0 and bal == 0:
            src_empty_uids.add(uid)
        caixa = bool(rule.get("caixa"))

        add_to = rule.get("addToWalletId")
        remove_from = rule.get("removeFromWalletId")

        if add_to and add_to in positions:
            # Asset sent to another wallet → value it in the target currency.
            add_bal = _conv_bal(bal, add_to)
            if add_bal is not None:
                # Replace semantics no lado ADD: a primeira regra que tocar
                # `(add_to, uid)` com contribuição **não-zero** zera a
                # baseline pré-existente da carteira destino — o PU final
                # do ativo na destino reflete só o que veio da origem
                # convertido, em vez de média ponderada com o baseline.
                # Regras subsequentes sobre o mesmo par acumulam em cima
                # do zero. Origem vazia (qty=0 + bal=0 → add_bal=0) NÃO
                # dispara replace: o baseline fica preservado e o pill
                # `srcEmpty` no preview marca a linha.
                if (qty != 0 or add_bal != 0) and (add_to, uid) not in replaced_baseline:
                    positions[add_to][uid] = {
                        "unprocessedId": uid, "quantity": 0.0, "pu": 0.0, "balance": 0.0,
                    }
                    replaced_baseline.add((add_to, uid))
                cur = positions[add_to].setdefault(uid, {
                    "unprocessedId": uid, "quantity": 0.0, "pu": 0.0, "balance": 0.0,
                })
                new_qty = (cur.get("quantity") or 0) + qty
                new_bal = (cur.get("balance") or 0) + add_bal
                cur["quantity"] = new_qty
                cur["balance"]  = new_bal
                cur["pu"]       = (new_bal / new_qty) if new_qty else 0.0
                cur["caixa"]    = caixa
                _record_op(add_to, uid, "added")

        if remove_from and remove_from in positions:
            # Removal stays in the removing wallet's own currency (usually the
            # source, so a no-op conversion).
            rem_bal = _conv_bal(bal, remove_from)
            if rem_bal is not None:
                cur = positions[remove_from].setdefault(uid, {
                    "unprocessedId": uid, "quantity": 0.0, "pu": 0.0, "balance": 0.0,
                })
                new_qty = (cur.get("quantity") or 0) - qty
                new_bal = (cur.get("balance") or 0) - rem_bal
                cur["quantity"] = new_qty
                cur["balance"]  = new_bal
                cur["pu"]       = (new_bal / new_qty) if new_qty else 0.0
                cur["caixa"]    = caixa
                _record_op(remove_from, uid, "removed")

    # Curva pass: substitui o PU de cada linha (baseline + tocadas por
    # regra) em todas as wallets pelas PUs calculadas pela engine de
    # Preços na Curva, quando o operador optou pelo `useCurvaPrice`. O
    # balance é re-derivado como `quantity × pu_curva`. Lookup estrito
    # por `(walletId, securityId, target_date)` — sem fallback agnóstico.
    # Linhas sem securityId mapeado, sem entrada na curva, ou com qty=0
    # ficam inalteradas e voltam ao caminho unprocessedPosition.
    if use_curva and curva_pu_map:
        for w in candidates:
            for uid, sec in positions[w].items():
                sec_id = uid_to_sec.get(uid) if uid_to_sec else None
                if not sec_id:
                    continue
                curva_pu = curva_pu_map.get(f"{w}|{sec_id}|{target_date}")
                if curva_pu is None:
                    continue
                qty_now = float(sec.get("quantity") or 0)
                sec["pu"]      = curva_pu
                sec["balance"] = qty_now * curva_pu
                curva_priced.add((w, uid))

    plan_wallets = {}
    for w in candidates:
        wallet_ops = ops.get(w) or {}
        if not wallet_ops:
            # Wallet not touched by any rule — skip it entirely (uploading
            # the untouched baseline would be a no-op write that risks
            # clobbering any other simultaneous edit upstream).
            continue
        op_set = set(wallet_ops.values())
        if op_set == {"added"}:
            wallet_op = "added"
        elif op_set == {"removed"}:
            wallet_op = "removed"
        else:
            wallet_op = "both"
        rows = []
        for uid, sec in sorted(positions[w].items()):
            # `priceSource` reflete a origem do PU **desta linha desta
            # carteira** após o passe da curva. Três rótulos possíveis:
            #   - "curva"       → PU substituído pela engine de Preços na Curva
            #   - "unprocessed" → tocada por regra mas sem entrada na curva
            #                     (ou opt-in desligado) — PU veio do upstream
            #   - "baseline"    → não tocada por regra **e** sem curva — só
            #                     ride-along do unprocessedSecurityPositions
            row_op = wallet_ops.get(uid)
            if (w, uid) in curva_priced:
                price_source = "curva"
            elif row_op is None:
                price_source = "baseline"
            else:
                price_source = "unprocessed"
            rows.append({
                "unprocessedId": uid,
                "quantity":      _round(sec.get("quantity"), 8),
                "pu":            _round(sec.get("pu"), 8),
                "balance":       _round(sec.get("balance"), 2),
                "caixa":         bool(sec.get("caixa")),
                "currencyId":    cur_map.get(w, ""),
                "walletId":      w,
                "priceSource":   price_source,
                # `srcEmpty == true` quando a regra disparou mas a origem
                # tinha quantity=0 e balance=0 pro ativo nesta data — a
                # regra contribuiu zero pro destino. Aparece apenas em rows
                # tocadas por regra (op != None); rows baseline ficam false.
                "srcEmpty":      bool(row_op is not None and uid in src_empty_uids),
                # `op` is None for baseline rows that weren't touched by any
                # rule but still ride along in the upload because they were
                # already in the wallet's existing position.
                "op":            wallet_ops.get(uid),
            })
        plan_wallets[w] = {
            "rows":       rows,
            "currencyId": cur_map.get(w, ""),
            "op":         wallet_op,
            "isSource":   (w == src_w),
        }

    transactions_plan = _collect_transaction_migrations(
        company_id=blob["companyId"],
        rules=rules,
        target_date=target_date,
        cur_map=cur_map,
        fx_overrides=fx_overrides,
    )
    fx_pending.extend(transactions_plan.get("fxPending") or [])

    # A missing fx rate anywhere (positions or transactions) blocks the whole
    # apply — uploading a half-converted plan would corrupt the target
    # wallet's balances. Surface the missing pairs in `pendingFxRates` so the
    # `#sp-fx-prompt` modal can collect manual rates from the operator and
    # the front-end can retry the call with `fxOverrides`. The `error` field
    # is kept so older clients (and the bulk-preview rendering) still see a
    # clear failure pill.
    if fx_pending:
        pares = sorted({(f, t, d) for (f, t, d) in fx_pending})
        pretty = ", ".join(f"{f}→{t} em {d}" for (f, t, d) in pares)
        return {
            "error": f"Conversão de câmbio indisponível em fxRates ({pretty}) — informe a taxa para continuar.",
            "pendingFxRates": [{"from": f, "to": t, "date": d} for (f, t, d) in pares],
        }

    return {
        "sourceDate":   src_date,
        "targetDate":   target_date,
        "fallback":     fallback,
        "missingRules": missing_rules,
        "wallets":      plan_wallets,
        "transactions": transactions_plan["rows"],
        "transactionsUnmapped": transactions_plan["unmapped"],
        # Flags whether the preview/apply actually ran with the curva PU
        # override on. Surfaced so the FE can show a banner / pill header
        # and the per-row `priceSource` mix makes sense in context.
        "useCurvaPrice": use_curva,
        # uids cuja origem estava zerada (qty=0 + balance=0) na data alvo —
        # a regra disparou mas contribuiu zero. FE mostra um banner topo
        # + pill "origem vazia" por linha.
        "emptySourceUids": sorted(src_empty_uids),
    }


def _collect_transaction_migrations(*, company_id, rules, target_date, cur_map=None, fx_overrides=None):
    """For every rule with both `addTo` and `removeFrom`, find transactions
    that should follow the security to the new wallet:

      walletId         == removeFromWalletId
      securityId       == mapping[unprocessedId]
      liquidationDate  == target_date
      companyId        == <exception's companyId>      # cross-company guard

    Returns `{"rows": [...], "unmapped": [...], "fxErrors": [...]}`. `rows` is
    a list of dicts ready for the preview; `unmapped` lists rule
    unprocessedIds with no `securityMappings` entry. When the destination
    wallet prices in a different currency than the source, `balance`/`price`
    are converted via fxRates (quantity unchanged) and `toCurrencyId` is set
    to the destination currency; a missing rate is reported in `fxErrors`
    (the caller blocks the whole apply). `cur_map` maps walletId → currency.
    """
    if not rules:
        return {"rows": [], "unmapped": [], "fxPending": []}
    cur_map = cur_map or {}
    uid_to_sec = _build_unprocessed_to_security_map(company_id)

    targets = []        # (rule, securityId)
    unmapped = []
    for rule in rules:
        if not rule.get("addToWalletId") or not rule.get("removeFromWalletId"):
            # Without both sides there is no destination to migrate to.
            # Pure removes (no addTo) are intentionally ignored — the user
            # didn't tell us where the transaction should land.
            continue
        sec = uid_to_sec.get(rule["unprocessedId"])
        if not sec:
            unmapped.append(rule["unprocessedId"])
            continue
        targets.append((rule, sec))
    if not targets:
        return {"rows": [], "unmapped": unmapped, "fxPending": []}

    # One query covering every (wallet, security) pair we care about.
    # `companyId` is also pinned to defend against a stale exception that
    # references a wallet that has since been moved to a different company.
    # The endpoint can't express the (walletId, securityId) $or of pairs, so we
    # fetch by the union of from-wallets + securityIds on target_date and keep
    # the per-pair filter via `pair_index` below (same semantics as the $or).
    pair_index = {}
    from_wallets = set()
    sec_ids = set()
    for rule, sec in targets:
        from_w = rule["removeFromWalletId"]
        to_w   = rule["addToWalletId"]
        from_wallets.add(from_w)
        sec_ids.add(sec)
        pair_index[(from_w, sec)] = (rule, to_w)

    rows = []
    fx_pending = []
    for tx in beehus_catalog.transactions_search(
        company_id, initial_date=target_date, final_date=target_date,
        wallet_ids=list(from_wallets), security_ids=list(sec_ids),
    ):
        from_w = str(tx.get("walletId") or "")
        sec    = str(tx.get("securityId") or "")
        match  = pair_index.get((from_w, sec))
        if not match:
            # Defensive: $or might pick up a doc that doesn't actually map
            # back to one of our pairs (shouldn't happen, but be paranoid).
            continue
        rule, to_w = match

        # Currency conversion: the migrated transaction lands in `to_w`, which
        # may price in a different currency than the source. Convert balance
        # and price (PU) by the fxRate; quantity is units, not money, so it
        # stays. A missing rate is fatal (recorded, caller blocks). When the
        # currencies match (the common case) nothing is converted.
        orig_bal   = tx.get("balance")
        orig_price = tx.get("price")
        from_cur   = cur_map.get(from_w) or str(tx.get("currencyId") or "")
        to_cur     = cur_map.get(to_w)
        conv_bal, conv_price = orig_bal, orig_price
        eff_currency, converted = (to_cur or from_cur), False
        if from_cur and to_cur and from_cur != to_cur:
            rate = _fx_rate(from_cur, to_cur, target_date, overrides=fx_overrides)
            if rate is None:
                fx_pending.append((from_cur, to_cur, target_date))
                continue
            conv_bal   = (orig_bal   * rate) if orig_bal   is not None else None
            conv_price = (orig_price * rate) if orig_price is not None else None
            converted  = True

        row = {
            "id":               str(tx["_id"]),
            "unprocessedId":    rule["unprocessedId"],
            "securityId":       sec,
            "fromWalletId":     from_w,
            "toWalletId":       to_w,
            "balance":          _round(conv_bal, 2) if conv_bal is not None else None,
            "price":            _round(conv_price, 8) if conv_price is not None else None,
            "quantity":         _round(tx.get("quantity"), 8) if tx.get("quantity") is not None else None,
            "liquidationDate":  str(tx.get("liquidationDate") or "")[:10],
            "operationDate":    str(tx.get("operationDate") or "")[:10],
            "description":      tx.get("description") or "",
            "type":             tx.get("beehusTransactionType") or "",
            "currencyId":       str(eff_currency or ""),
            # Conversion bookkeeping — consumed by apply (PATCH balance/price/
            # currencyId on the migrated txn) and by _build_adjustment_plans
            # (from-side keeps source currency/balance; to-side uses target).
            "converted":        converted,
            "origBalance":      _round(orig_bal, 2) if orig_bal is not None else None,
            "fromCurrencyId":   str(from_cur or ""),
            "toCurrencyId":     str(eff_currency or ""),
        }
        row["adjustments"] = _build_adjustment_plans(row)
        rows.append(row)
    rows.sort(key=lambda r: (r["fromWalletId"], r["unprocessedId"], r["id"]))
    return {"rows": rows, "unmapped": sorted(set(unmapped)), "fxPending": fx_pending}


def _build_adjustment_plans(tx):
    """Two `securityTransfer` adjustments paired with one migration.

    One stays on the sending wallet (same balance/quantity sign), the
    other lands on the receiving wallet (signs inverted). Description is
    `"Ajuste " + descrição original`, dates/security carry over.

    Currency-aware: the from-side stays in the source wallet's currency with
    the *original* (unconverted) balance; the to-side lands in the target
    wallet's currency with the *converted* balance (so each wallet's ledger
    stays internally consistent). When no conversion happened both sides use
    the same value/currency, matching the legacy behaviour. `currencyId` is
    carried on each side so apply doesn't re-default it to the source."""
    conv_balance  = tx.get("balance")  or 0        # already converted for to_w
    orig_balance  = tx.get("origBalance")
    if orig_balance is None:
        orig_balance = conv_balance                # same-currency: identical
    base_quantity = tx.get("quantity") or 0
    base_desc     = (tx.get("description") or "").strip()
    adj_desc      = f"Ajuste {base_desc}".rstrip()
    from_currency = tx.get("fromCurrencyId") or tx.get("currencyId") or ""
    to_currency   = tx.get("toCurrencyId")   or tx.get("currencyId") or ""
    return [
        {
            "side":            "from",
            "walletId":        tx.get("fromWalletId") or "",
            "balance":         _round(orig_balance, 2),
            "quantity":        _round(base_quantity, 8),
            "currencyId":      from_currency,
            "description":     adj_desc,
            "type":            "securityTransfer",
            "liquidationDate": tx.get("liquidationDate") or "",
            "operationDate":   tx.get("operationDate") or "",
            "securityId":      tx.get("securityId") or "",
        },
        {
            "side":            "to",
            "walletId":        tx.get("toWalletId") or "",
            "balance":         _round(-conv_balance, 2),
            "quantity":        _round(-base_quantity, 8),
            "currencyId":      to_currency,
            "description":     adj_desc,
            "type":            "securityTransfer",
            "liquidationDate": tx.get("liquidationDate") or "",
            "operationDate":   tx.get("operationDate") or "",
            "securityId":      tx.get("securityId") or "",
        },
    ]


def _round(v, digits):
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return 0


def _wallet_currency(w):
    """The wallet's currency for display + position upload.

    The `wallets` collection stores the ISO code in `currency` (e.g. "BRL",
    "USD") — the same value `processedPosition.currency` is denormalized
    from. Older code read `currencyId`; we now prefer `currency` and only
    fall back to `currencyId`/"BRL" so a wallet missing the field still
    renders a non-blank "Moeda" column.
    """
    return str((w.get("currency") or w.get("currencyId") or "BRL"))


# Taxas de câmbio agora vivem num arquivo JSON local (`data/fx_rates.json`),
# não mais no Mongo (`db.fxRates`). Shape do arquivo:
#   {currencyId: {"YYYY-MM-DD": value}}  com value = "1 currencyId = value BRL".
# BRL é a âncora implícita (=1, sem entrada). Chaves iniciadas por "_" (docs)
# são ignoradas. Cacheado por mtime — editar o arquivo reflete na hora.
_FX_REF_CURRENCY = "BRL"
_FX_RATES_FILE = os.path.join(os.path.dirname(__file__), "..", "data", "fx_rates.json")
_fx_rates_cache = {"mtime": None, "rates": {}}


def _load_fx_rates():
    """`{currencyId: {YYYY-MM-DD: value_BRL}}` de `data/fx_rates.json`. Cacheado
    por mtime; `{}` se o arquivo faltar/for inválido. Lockless: uma corrida
    benigna apenas recarrega o arquivo (pequeno)."""
    try:
        mt = os.path.getmtime(_FX_RATES_FILE)
    except OSError:
        return _fx_rates_cache["rates"]
    if _fx_rates_cache["mtime"] == mt:
        return _fx_rates_cache["rates"]
    try:
        with open(_FX_RATES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, ValueError):
        return _fx_rates_cache["rates"]
    rates = {k: v for k, v in data.items()
             if not k.startswith("_") and isinstance(v, dict)}
    _fx_rates_cache["mtime"] = mt
    _fx_rates_cache["rates"] = rates
    return rates


def _fx_rate(base, quote, date, overrides=None):
    """Rate to convert an amount in `base` currency into `quote` on `date`.

    Schema de `data/fx_rates.json` (via `_load_fx_rates`): `{currencyId:
    {date: value}}`, onde `value` é "1 currencyId = value BRL" (BRL = âncora).
    Daí:
      - `X  → BRL`: multiplica por `value(X)`.
      - `BRL → X` : divide por `value(X)` (ou seja, multiplica por `1/value`).
      - `X  → Y` : cross via BRL = `value(X) / value(Y)`.

    Olha apenas a `date` exata (sem fallback temporal — uma conversão
    financeira tem que ser na data alvo). `overrides` é um
    `{(from, to, date): rate}` opcional, consultado **só depois** que
    `fxRates` falha — uma taxa armazenada sempre vence sobre input manual.
    Retorna `None` quando nem `fxRates` nem `overrides` cobrem o par."""
    if not base or not quote:
        return None
    if base == quote:
        return 1.0

    def _fetch_value(currency):
        """Lookup do `value` em `data/fx_rates.json` para a moeda na data — i.e.
        a taxa `1 currency = value BRL`. Retorna float positivo ou `None`."""
        if currency == _FX_REF_CURRENCY:
            return 1.0
        raw = (_load_fx_rates().get(currency) or {}).get(str(date)[:10])
        if raw is None:
            return None
        try:
            v = float(raw)
            return v if v else None
        except (TypeError, ValueError):
            return None

    v_base  = _fetch_value(base)
    v_quote = _fetch_value(quote)
    if v_base is not None and v_quote:
        # 1 base = v_base BRL  e  1 quote = v_quote BRL
        # ⇒ 1 base = (v_base / v_quote) quote
        return v_base / v_quote

    # Operator-supplied manual overrides — last resort, by design.
    if overrides:
        if (base, quote, date) in overrides:
            try:
                v = float(overrides[(base, quote, date)])
                if v:
                    return v
            except (TypeError, ValueError):
                pass
        if (quote, base, date) in overrides:
            try:
                v = float(overrides[(quote, base, date)])
                if v:
                    return 1.0 / v
            except (TypeError, ValueError):
                pass
    return None


def _parse_fx_overrides(raw):
    """Normalize the incoming `fxOverrides` payload into a `(from, to, date)
    → float` dict. Accepts `[{from, to, date, rate}, ...]` (the wire shape
    used by the front-end prompt). Silently drops malformed entries — the
    caller still re-runs the conversion pass and will re-surface any pair
    that ends up uncovered."""
    out = {}
    if not raw:
        return out
    for o in raw:
        if not isinstance(o, dict):
            continue
        f = (o.get("from") or "").strip()
        t = (o.get("to")   or "").strip()
        d = (o.get("date") or "").strip()
        if not (f and t and d):
            continue
        try:
            rate = float(o.get("rate"))
        except (TypeError, ValueError):
            continue
        if not rate:
            continue
        out[(f, t, d)] = rate
    return out


def _compute_strip_curva_pu_map(target_date, allowed_wallets=None):
    """Run the precificacao curva engine over the saved global templates
    and return `{walletId|securityId|date: pu}` for `target_date`.

    Mirrors `pages.repetir_posicoes._compute_curva_pu_map` but scoped to a
    single date and to the strip use case: entries without `walletId` in
    the curva template are silently skipped (strip needs a strict wallet
    match — using an agnostic `*|sid|dt` key would risk contaminating an
    unrelated wallet with another's curva PU). Failures (load_lists/engine
    crashes) collapse to an empty map so the strip falls back transparently
    to the unprocessedPosition PU.

    `allowed_wallets` (optional iterable of walletIds): filtra o template
    pra mandar à engine apenas as securities cujo `walletId` está nessa
    lista — tipicamente `{sourceWalletId} ∪ outputWalletIds`. Reduz o
    custo de `calculate_curva` sem mudar a semântica (o lookup downstream
    já era estrito por `walletId`). `None` → manda tudo (comportamento
    pré-otimização).
    """
    if not target_date:
        return {}
    try:
        saved_lists = _load_lists()
    except Exception:
        return {}
    allowed = set(allowed_wallets) if allowed_wallets else None
    # Deduplicate by (id, calcType) across all saved templates — later wins,
    # matching the Repetir Posições convention now that multiple named lists
    # may coexist. Quando `allowed` está setado, descartamos no input
    # securities sem `walletId` (que sempre seriam descartadas no result
    # level) e securities cujo `walletId` não é uma wallet do plano.
    merged = {}
    for entry in saved_lists or []:
        for sec in (entry.get("securities") or []):
            sid = sec.get("id")
            if not sid:
                continue
            if allowed is not None:
                wid = (sec.get("walletId") or "").strip()
                if not wid or wid not in allowed:
                    continue
            merged[(sid, sec.get("calcType") or "")] = sec
    securities = list(merged.values())
    if not securities:
        return {}
    try:
        results = calculate_curva(securities)
    except Exception:
        return {}
    out = {}
    for r in results or []:
        if r.get("error"):
            continue
        sid = (r.get("securityId") or "").strip()
        dt  = (r.get("date") or "")[:10]
        wid = (r.get("walletId") or "").strip()
        pu  = r.get("pu")
        if not (sid and dt and wid) or pu is None:
            continue
        if dt != target_date:
            continue
        try:
            out[f"{wid}|{sid}|{dt}"] = float(pu)
        except (TypeError, ValueError):
            continue
    return out


@bp.route("/api/excecoes/<exception_id>/preview", methods=["POST"])
def preview_exception(exception_id):
    body = request.get_json() or {}
    company_id  = body.get("companyId", "")
    target_date = body.get("date", "")
    fx_overrides = _parse_fx_overrides(body.get("fxOverrides"))
    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe(exception_id); _safe_date(target_date)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    blob = _load(company_id, exception_id)
    if not blob:
        return jsonify({"error": "not found"}), 404

    # Dispatch by kind. `wallet_slice` and `class_strip` have their own
    # plan/response shapes — see `_build_slice_plan` / `_build_class_strip_plan`
    # for the fields the FE consumes.
    kind = blob.get("kind") or _KIND_POSITION_STRIP
    if kind == _KIND_WALLET_SLICE:
        return _preview_slice(blob, target_date)
    if kind == _KIND_CLASS_STRIP:
        return _preview_class_strip(blob, target_date)

    plan = _build_plan(blob, target_date, fx_overrides=fx_overrides)
    if "error" in plan:
        return jsonify(plan), 422

    # Resolve names for the UI. Always include the source wallet so the
    # "Origem" pill in the preview header has a label even when the source
    # itself wasn't touched by any rule. Also include from/to wallets that
    # appear only on the transactions list (not in the position plan).
    wids = set(plan["wallets"].keys()) | {blob.get("sourceWalletId")}
    for tx in plan.get("transactions") or []:
        wids.add(tx.get("fromWalletId"))
        wids.add(tx.get("toWalletId"))
    wids.discard(None); wids.discard("")
    wallet_names = {}
    for wid in wids:
        swid = beehus_catalog.id_str(wid)
        wallet_names[swid] = (beehus_catalog.wallet_doc(swid) or {}).get("name") or swid

    # Security names for the transactions list — looked up via the cached
    # `get_security_names` helper to keep the preview cheap.
    sec_ids = {tx.get("securityId") for tx in (plan.get("transactions") or [])
               if tx.get("securityId")}
    sec_names = {}
    if sec_ids:
        from db import get_security_names
        all_names = get_security_names()
        for sid in sec_ids:
            sec_names[sid] = all_names.get(sid, sid)

    return jsonify({**plan,
                    "walletNames":    wallet_names,
                    "securityNames":  sec_names})


def _build_xlsx(rows, target_date):
    """Build the upstream-shaped workbook in memory.
    Columns: Data, Carteira, Ativo, Quant, PU, SaldoBruto, Caixa, Moeda."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(["Data", "Carteira", "Ativo", "Quant", "PU",
               "SaldoBruto", "Caixa", "Moeda"])
    for r in rows:
        ws.append([
            target_date,
            r.get("walletId") or "",
            r.get("unprocessedId") or "",
            r.get("quantity") or 0,
            r.get("pu") or 0,
            r.get("balance") or 0,
            "Sim" if r.get("caixa") else "Não",
            r.get("currencyId") or "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


@bp.route("/api/excecoes/<exception_id>/excel")
def download_excel(exception_id):
    """Download the generated workbook for inspection without uploading."""
    company_id  = request.args.get("companyId", "")
    target_date = request.args.get("date", "")
    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe(exception_id); _safe_date(target_date)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    blob = _load(company_id, exception_id)
    if not blob:
        return jsonify({"error": "not found"}), 404

    kind = blob.get("kind") or _KIND_POSITION_STRIP
    if kind == _KIND_CLASS_STRIP:
        # class_strip emits one set of rows per wallet touched (every source
        # gets a zero-out of the migrated unprocessedIds; every target gets
        # the additions from its routed classes). One workbook, one Posicoes
        # sheet with all of them stacked — same shape position_strip uses.
        cs_plan = _build_class_strip_plan(blob, target_date)
        rows = list(cs_plan.get("xlsxRows") or [])
        buf = io.BytesIO(_build_xlsx(rows, target_date))
        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"classe_{exception_id}_{target_date}.xlsx",
        )

    # Wallet-slice exceptions have their own row shape (target wallet
    # only, with a sliced Caixa row). Bypass the position_strip plan.
    if kind == _KIND_WALLET_SLICE:
        slice_plan = _build_slice_plan(blob, target_date)
        slice_rows = []
        for s in slice_plan["securities"]:
            slice_rows.append({
                "walletId":      s["walletId"],
                "unprocessedId": s["unprocessedId"],
                "quantity":      s["quantity"],
                "pu":            s["pu"],
                "balance":       s["balance"],
                "caixa":         False,
                "currencyId":    s["currencyId"],
            })
        cash = slice_plan.get("cashAccount")
        if cash is not None:
            slice_rows.append({
                "walletId":      cash["walletId"],
                "unprocessedId": cash["unprocessedId"],
                "quantity":      0,
                "pu":            0,
                "balance":       cash["balance"],
                "caixa":         True,
                "currencyId":    cash["currencyId"],
            })
        buf = io.BytesIO(_build_xlsx(slice_rows, target_date))
        return send_file(
            buf,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            as_attachment=True,
            download_name=f"fatiar_{exception_id}_{target_date}.xlsx",
        )

    plan = _build_plan(blob, target_date)
    if "error" in plan:
        return jsonify(plan), 422

    rows = []
    for w_rows in (p["rows"] for p in plan["wallets"].values()):
        rows.extend(w_rows)

    buf = io.BytesIO(_build_xlsx(rows, target_date))
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"excecao_{exception_id}_{target_date}.xlsx",
    )


@bp.route("/api/excecoes/<exception_id>/apply", methods=["POST"])
def apply_exception(exception_id):
    """Generate one Excel per affected wallet and upload it via the Beehus API.

    Returns per-wallet status. On at least one upstream failure, the response
    is 502 and successful wallets are still reported so the caller can
    distinguish partial-success from a clean failure."""
    body = request.get_json() or {}
    company_id  = body.get("companyId", "")
    target_date = body.get("date", "")
    fx_overrides = _parse_fx_overrides(body.get("fxOverrides"))
    if not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id); _safe(exception_id); _safe_date(target_date)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    blob = _load(company_id, exception_id)
    if not blob:
        return jsonify({"error": "not found"}), 404

    # Dispatch by kind. `wallet_slice` and `class_strip` each have their
    # own multi-step apply pipelines (position upload + provisions +
    # transactions + adjustments). See `_apply_slice` / `_apply_class_strip`.
    kind = blob.get("kind") or _KIND_POSITION_STRIP
    if kind == _KIND_WALLET_SLICE:
        return _apply_slice(blob, target_date)
    if kind == _KIND_CLASS_STRIP:
        return _apply_class_strip(blob, target_date)

    plan = _build_plan(blob, target_date, fx_overrides=fx_overrides)
    if "error" in plan:
        return jsonify(plan), 422

    auth_error = None

    # ── Step 1: migrate transactions ─────────────────────────────────────
    # Done before positions so the upstream has the new wallet ownership in
    # place when it ingests the rebuilt unprocessedSecurityPositions. A
    # mid-flight auth error short-circuits everything (no point pushing
    # positions if the token is dead).
    #
    # Every successful migration is paired with two synthetic
    # `securityTransfer` transactions ("Ajuste …"): one stays on the
    # sending wallet with the same balance/quantity as the original; the
    # other lands on the receiving wallet with the signs inverted. This
    # leaves a matched +/− audit trail in both wallets so the operator
    # can reconcile the move after the fact.
    plan_txs = plan.get("transactions") or []
    adjust_wallet_ids = {wid for tx in plan_txs
                         for wid in (tx.get("fromWalletId"), tx.get("toWalletId"))
                         if wid}
    wallet_meta = _resolve_wallet_meta(adjust_wallet_ids) if adjust_wallet_ids else {}

    txn_results = []
    for tx in plan_txs:
        try:
            # Move the transaction to the new wallet. When the destination
            # prices in a different currency, also push the converted
            # balance/price and the destination currencyId so the migrated
            # row matches the target wallet's books (quantity is unchanged).
            patch = {"walletId": tx["toWalletId"]}
            if tx.get("converted"):
                if tx.get("balance") is not None:
                    patch["balance"] = tx["balance"]
                if tx.get("price") is not None:
                    patch["price"] = tx["price"]
                if tx.get("toCurrencyId"):
                    patch["currencyId"] = tx["toCurrencyId"]
            up = update_transaction(tx["id"], patch)
            txn_results.append({"id": tx["id"], "status": "ok",
                                "kind": "migrate",
                                "fromWalletId": tx["fromWalletId"],
                                "toWalletId":   tx["toWalletId"],
                                "securityId":   tx["securityId"],
                                "upstream":     up})
        except BeehusAuthError as e:
            auth_error = {"error": str(e), "status": getattr(e, "status", None)}
            txn_results.append({"id": tx["id"], "status": "auth_error",
                                "kind": "migrate",
                                "error": str(e)})
            break
        except BeehusAPIError as e:
            txn_results.append({"id": tx["id"], "status": "error",
                                "kind": "migrate",
                                "error": str(e),
                                "upstream_status": getattr(e, "status", None),
                                "upstream_body": getattr(e, "body", None)})
            # Skip the paired adjustments when the migration itself
            # failed — they only make sense alongside a successful move.
            continue

        # Paired adjustments. Plans come from `_build_adjustment_plans`
        # (also used by the preview) so what the operator approved is
        # exactly what gets sent. Failure here is reported per-row but
        # does NOT abort subsequent migrations — only auth break does.
        for adj in tx.get("adjustments") or []:
            wid = adj.get("walletId") or ""
            meta = wallet_meta.get(wid) or {}
            entity_id = meta.get("entityId") or ""
            # Each side carries its own currency: the from-side keeps the
            # source currency, the to-side uses the target's (cross-currency
            # migrations). Fall back to the migrated txn / wallet currency.
            currency_id = (adj.get("currencyId")
                           or tx.get("currencyId")
                           or meta.get("currencyId") or "BRL")
            base_result = {
                "id":            tx["id"],
                "kind":          "adjust",
                "side":          adj.get("side"),
                "walletId":      wid,
                "fromWalletId":  wid if adj.get("side") == "from" else "",
                "toWalletId":    wid if adj.get("side") == "to"   else "",
                "securityId":    adj.get("securityId") or "",
                "balance":       adj.get("balance"),
                "description":   adj.get("description") or "",
            }
            if not entity_id:
                txn_results.append({**base_result, "status": "error",
                                    "error": "wallet sem entityId"})
                continue
            try:
                created = create_transaction(
                    company_id=company_id,
                    entity_id=entity_id,
                    wallet_id=wid,
                    balance=adj.get("balance") or 0,
                    operation_date=adj.get("operationDate") or target_date,
                    liquidation_date=adj.get("liquidationDate") or target_date,
                    currency_id=currency_id,
                    transaction_type=adj.get("type") or "securityTransfer",
                    description=adj.get("description") or "",
                    security_id=adj.get("securityId") or None,
                    quantity=adj.get("quantity") or 0,
                )
                created_id = ""
                if isinstance(created, dict):
                    created_id = str(created.get("_id")
                                     or created.get("id") or "")
                txn_results.append({**base_result, "status": "ok",
                                    "createdId": created_id,
                                    "upstream":  created})
            except BeehusAuthError as e:
                auth_error = {"error": str(e),
                              "status": getattr(e, "status", None)}
                txn_results.append({**base_result, "status": "auth_error",
                                    "error": str(e)})
                break
            except BeehusAPIError as e:
                txn_results.append({**base_result, "status": "error",
                                    "error": str(e),
                                    "upstream_status": getattr(e, "status", None),
                                    "upstream_body":   getattr(e, "body", None)})
        if auth_error:
            break

    # ── Step 2: upload positions ─────────────────────────────────────────
    # Para suporte a outputs cross-company: resolvemos a `companyId` real
    # de CADA wallet do plano e roteamos o upload pra essa empresa em vez
    # de usar a `companyId` da exceção. Beehus indexa posições por
    # `(companyId, walletId)`; subir uma carteira de outra empresa sob o
    # `companyId` errado faz o upload sumir do lugar certo.
    plan_wallet_ids = list(plan["wallets"].keys())
    plan_wallet_meta = _resolve_wallet_meta(plan_wallet_ids) if plan_wallet_ids else {}
    results = []
    if not auth_error:
        for w, payload in plan["wallets"].items():
            rows = payload["rows"]
            if not rows:
                results.append({"walletId": w, "status": "skipped", "reason": "no rows"})
                continue
            xlsx = _build_xlsx(rows, target_date)
            wallet_company_id = (
                (plan_wallet_meta.get(w) or {}).get("companyId") or company_id
            )
            try:
                up = upload_unprocessed_security_positions_file(
                    company_id=wallet_company_id,
                    file_bytes=xlsx,
                    filename=f"excecao_{w}_{target_date}.xlsx",
                )
                results.append({"walletId": w, "status": "ok",
                                "rows": len(rows), "upstream": up,
                                "uploadCompanyId": wallet_company_id})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                results.append({"walletId": w, "status": "auth_error",
                                "error": str(e)})
                break
            except BeehusAPIError as e:
                results.append({"walletId": w, "status": "error",
                                "error": str(e),
                                "upstream_status": getattr(e, "status", None),
                                "upstream_body": getattr(e, "body", None)})

    failed_txn = [r for r in txn_results if r["status"] not in ("ok",)]
    failed_pos = [r for r in results if r["status"] not in ("ok", "skipped")]
    failed = failed_txn + failed_pos
    if not failed:
        blob["lastApplied"] = {
            "date": target_date,
            "at":   datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        _save(blob)

    response = {"results": results,
                "transactionResults":   txn_results,
                "transactionsUnmapped": plan.get("transactionsUnmapped") or [],
                "sourceDate":           plan["sourceDate"],
                "fallback":             plan["fallback"],
                "missingRules":         plan["missingRules"]}
    if auth_error:
        return jsonify({**response, **auth_error}), 401
    if failed:
        return jsonify(response), 502
    return jsonify(response)


# ── Wallet slice (Fatiar carteira) ────────────────────────────────────────────
# Send X% of a source wallet to a target wallet on a given date. The %
# scales (a) unprocessedSecurityPositions security rows + cashAccounts
# row, (b) provisions covering the date, and (c) transactions liquidating
# on the date. The source wallet is NOT modified — the operator only
# uploads sliced data onto the target. The source's existing position is
# the snapshot the percentage is computed from, nothing more.

def _slice_source_securities(wallet_id, target_date):
    """Aggregated `unprocessedSecurityPositions.securities` for a wallet
    on a date, with fallback to the previous 5 days when the requested
    date has no doc (same window the position_strip flow uses).

    Returns `(securities, used_date, fallback)`. Each security row has
    `unprocessedId`, `quantity`, `pu`, `balance` — the schema produced
    by `_aggregate_unprocessed`."""
    return _resolve_source_with_fallback(wallet_id, target_date)


def _slice_source_cash(wallet_id, target_date):
    """Sum `cashAccounts.values[].value` for `wallet_id` on `target_date`.

    Returns `(total, unprocessedId)` where `total` is the cash amount as
    float (None when no cashAccounts doc exists for the wallet) and
    `unprocessedId` is the cashAccounts row's `unprocessedId` used as the
    `Ativo` cell on the upstream upload (falls back to the literal
    "Caixa" when the wallet has no cashAccounts doc on file)."""
    if not wallet_id or not target_date:
        return None, ""
    total = 0.0
    found = False
    uid = ""
    key = target_date[:10]
    for doc in beehus_catalog.cash_accounts_docs(wallet_id, date=target_date):
        if not uid:
            cur_uid = (doc.get("unprocessedId") or "").strip()
            if cur_uid:
                uid = cur_uid
        for v in doc.get("values") or []:
            d = v.get("date")
            if d is None:
                continue
            if str(d)[:10] == key:
                try:
                    total += float(v.get("value") or 0)
                    found = True
                except (TypeError, ValueError):
                    continue
    return (total if found else None), (uid or "Caixa")


def _slice_source_provisions(company_id, wallet_id, target_date):
    """Provisions for `wallet_id` whose
    `[initialDate, liquidationDate)` window covers `target_date`. Same
    overlap rule used by `pages/carteira._provisions_by_wallet_date` and
    `pages/repetir_posicoes._provisions_detail` — keeps the wallet_slice
    preview in line with what the operator already sees on
    `Painel de Controle > Carteira`."""
    if not wallet_id or not target_date:
        return []
    out = []
    cursor = beehus_catalog.provisions_active(
        company_id, target_date, wallet_ids=[wallet_id])
    for d in cursor:
        bal = d.get("balance")
        try:
            bal_f = float(bal) if bal is not None else 0.0
        except (TypeError, ValueError):
            bal_f = 0.0
        out.append({
            "id":              str(d.get("_id") or ""),
            "description":     (d.get("description") or "").strip(),
            "balance":         bal_f,
            "initialDate":     str(d.get("initialDate") or "")[:10],
            "liquidationDate": str(d.get("liquidationDate") or "")[:10],
            "provisionType":   d.get("provisionType") or d.get("type") or "",
            "provisionSource": d.get("provisionSource") or "",
            "currencyId":      str(d.get("currencyId") or ""),
            "securityId":      str(d.get("securityId") or ""),
        })
    out.sort(key=lambda p: (p["liquidationDate"] or "", p["initialDate"] or ""))
    return out


def _slice_source_transactions(company_id, wallet_id, target_date):
    """Transactions for `wallet_id` with `liquidationDate == target_date`.

    The slice mirrors what landed on the source on that date — same
    granularity the position_strip flow uses for its migrations. The
    `trashed` guard keeps soft-deleted rows out (consistent with the
    rest of the codebase)."""
    if not wallet_id or not target_date:
        return []
    cursor = [
        t for t in beehus_catalog.transactions_on_date(
            company_id, [wallet_id], target_date)
        if not t.get("trashed")
    ]
    out = []
    for tx in cursor:
        try:
            bal = float(tx.get("balance")) if tx.get("balance") is not None else 0.0
        except (TypeError, ValueError):
            bal = 0.0
        try:
            qty = float(tx.get("quantity")) if tx.get("quantity") is not None else None
        except (TypeError, ValueError):
            qty = None
        out.append({
            "id":               str(tx["_id"]),
            "securityId":       str(tx.get("securityId") or ""),
            "balance":          bal,
            "quantity":         qty,
            "liquidationDate":  str(tx.get("liquidationDate") or "")[:10],
            "operationDate":    str(tx.get("operationDate") or "")[:10],
            "description":      tx.get("description") or "",
            "beehusTransactionType": tx.get("beehusTransactionType") or "",
            "currencyId":       str(tx.get("currencyId") or ""),
        })
    out.sort(key=lambda t: (t["beehusTransactionType"], t["id"]))
    return out


def _build_slice_plan(blob, target_date):
    """Compute the sliced state to be uploaded onto the target wallet
    for a `wallet_slice` exception on `target_date`.

    Returns a dict ready for the front-end (and for `_apply_slice`):

      {
        "kind":         "wallet_slice",
        "percent":      <float 0–100>,
        "ratio":        <percent/100>,
        "sourceDate":   "YYYY-MM-DD",   # date actually used (with fallback)
        "targetDate":   "YYYY-MM-DD",
        "fallback":     bool,
        "sourceWalletId":  "...",
        "targetWalletId":  "...",
        "sourceCurrencyId": "...",
        "targetCurrencyId": "...",
        "summary": {
          "source": {nav, securities, cashAccount, provisions, transactions},
          "sliced": {nav, securities, cashAccount, provisions, transactions},
        },
        "securities":    [...],         # sliced rows ready for upload
        "cashAccount":   {...} | None,  # sliced cash row (None when source has none)
        "provisions":    [...],         # sliced provisions to create on target
        "transactions":  [...],         # sliced transactions to create on target
        "walletNames":   {...},
        "securityNames": {...},
        "targetMeta":    {entityId, currencyId},
      }
    """
    src_w = blob["sourceWalletId"]
    tgt_w = blob["targetWalletId"]
    company_id = blob["companyId"]
    percent = float(blob.get("percent") or 0)
    ratio = percent / 100.0

    src_secs, src_date, fallback = _slice_source_securities(src_w, target_date)
    if src_secs is None:
        src_secs = []
        # Allow the slice to proceed without a position snapshot — the
        # operator may still want to slice provisions/transactions only.
        # `sourceDate` falls back to the requested date for the UI label.
        src_date = target_date
        fallback = False

    cash_total, cash_uid = _slice_source_cash(src_w, target_date)
    provisions = _slice_source_provisions(company_id, src_w, target_date)
    transactions = _slice_source_transactions(company_id, src_w, target_date)

    # Currencies — looked up the same way the position_strip plan does
    # (string-coerced, fallback BRL) so the upload's "Moeda" column never
    # ends up blank. Also pull entityId for the target so transactions
    # and provisions can attach the same entity the wallet already uses.
    meta = {}
    for wid, w in _wallet_docs({src_w, tgt_w}).items():
        meta[wid] = {
            "currencyId": _wallet_currency(w),
            "entityId":   str(w.get("entityId") or "") if w.get("entityId") else "",
            "name":       w.get("name") or "",
        }
    src_meta = meta.get(src_w, {"currencyId": "BRL", "entityId": "", "name": src_w})
    tgt_meta = meta.get(tgt_w, {"currencyId": "BRL", "entityId": "", "name": tgt_w})
    tgt_currency = tgt_meta["currencyId"]

    # Totals on the source snapshot — surfaced in the summary so the
    # operator sees the absolute values the percentage is being applied
    # to. `nav ≈ securities + cashAccount`; provisions/transactions are
    # tracked separately because they're cash-flow / pending entries, not
    # part of the wallet's current position.
    sec_total = sum(float(s.get("balance") or 0) for s in src_secs)
    prov_total = sum(float(p.get("balance") or 0) for p in provisions)
    tx_total = sum(float(t.get("balance") or 0) for t in transactions)
    cash_val = float(cash_total or 0)
    nav_total = sec_total + cash_val

    # Sliced rows for the target's unprocessedSecurityPositions upload.
    # Quantity, PU, balance all scale by `ratio`. PU itself is invariant
    # under uniform scaling of qty + balance, but recomputing it from the
    # scaled numbers keeps the math consistent for rows where `balance`
    # is provided directly rather than via qty × PU.
    sliced_secs = []
    for s in src_secs:
        qty = float(s.get("quantity") or 0) * ratio
        bal = float(s.get("balance") or 0) * ratio
        pu  = (bal / qty) if qty else float(s.get("pu") or 0)
        sliced_secs.append({
            "unprocessedId": s.get("unprocessedId") or "",
            "sourceQuantity": _round(s.get("quantity"), 8),
            "sourcePu":       _round(s.get("pu"),       8),
            "sourceBalance":  _round(s.get("balance"),  2),
            "quantity":      _round(qty, 8),
            "pu":            _round(pu,  8),
            "balance":       _round(bal, 2),
            "caixa":         False,
            "currencyId":    tgt_currency,
            "walletId":      tgt_w,
        })

    sliced_cash = None
    if cash_total is not None:
        sliced_cash = {
            "unprocessedId": cash_uid,
            "sourceBalance": _round(cash_val, 2),
            "balance":       _round(cash_val * ratio, 2),
            "quantity":      0,
            "pu":            0,
            "caixa":         True,
            "currencyId":    tgt_currency,
            "walletId":      tgt_w,
        }

    sliced_provs = []
    for p in provisions:
        sliced_provs.append({
            "sourceId":        p["id"],
            "description":     p["description"],
            "sourceBalance":   _round(p["balance"], 2),
            "balance":         _round(p["balance"] * ratio, 2),
            "initialDate":     p["initialDate"],
            "liquidationDate": p["liquidationDate"],
            "provisionType":   p["provisionType"] or "adjustments",
            "provisionSource": p["provisionSource"] or "adjustments",
            "currencyId":      p["currencyId"] or tgt_currency,
            "securityId":      p["securityId"] or "",
        })

    sliced_txs = []
    for t in transactions:
        qty = t.get("quantity")
        qty_sliced = (float(qty) * ratio) if qty is not None else None
        sliced_txs.append({
            "sourceId":        t["id"],
            "securityId":      t["securityId"],
            "sourceBalance":   _round(t["balance"], 2),
            "sourceQuantity":  (_round(qty, 8) if qty is not None else None),
            "balance":         _round(t["balance"] * ratio, 2),
            "quantity":        (_round(qty_sliced, 8) if qty_sliced is not None else None),
            "liquidationDate": t["liquidationDate"],
            "operationDate":   t["operationDate"] or t["liquidationDate"],
            "description":     t["description"],
            "beehusTransactionType": t["beehusTransactionType"] or "withdrawalDeposit",
            "currencyId":      t["currencyId"] or tgt_currency,
        })

    wallet_names = {src_w: src_meta["name"] or src_w,
                    tgt_w: tgt_meta["name"] or tgt_w}
    sec_ids = ({s.get("unprocessedId") for s in src_secs} |
               {t["securityId"] for t in sliced_txs if t.get("securityId")} |
               {p["securityId"] for p in sliced_provs if p.get("securityId")})
    sec_ids.discard(""); sec_ids.discard(None)
    sec_names = {}
    if sec_ids:
        all_names = get_security_names()
        for sid in sec_ids:
            if sid in all_names:
                sec_names[sid] = all_names[sid]

    summary = {
        "source": {
            "nav":           _round(nav_total,  2),
            "securities":    _round(sec_total,  2),
            "cashAccount":   (_round(cash_val, 2) if cash_total is not None else None),
            "provisions":    _round(prov_total, 2),
            "transactions":  _round(tx_total,   2),
        },
        "sliced": {
            "nav":           _round(nav_total * ratio, 2),
            "securities":    _round(sec_total * ratio, 2),
            "cashAccount":   (_round(cash_val * ratio, 2) if cash_total is not None else None),
            "provisions":    _round(prov_total * ratio, 2),
            "transactions":  _round(tx_total   * ratio, 2),
        },
    }

    return {
        "kind":             _KIND_WALLET_SLICE,
        "percent":          percent,
        "ratio":            ratio,
        "sourceDate":       src_date or target_date,
        "targetDate":       target_date,
        "fallback":         fallback,
        "sourceWalletId":   src_w,
        "targetWalletId":   tgt_w,
        "sourceCurrencyId": src_meta["currencyId"],
        "targetCurrencyId": tgt_currency,
        "summary":          summary,
        "securities":       sliced_secs,
        "cashAccount":      sliced_cash,
        "provisions":       sliced_provs,
        "transactions":     sliced_txs,
        "walletNames":      wallet_names,
        "securityNames":    sec_names,
        "targetMeta":       tgt_meta,
    }


def _preview_slice(blob, target_date):
    plan = _build_slice_plan(blob, target_date)
    return jsonify(plan)


def _apply_slice(blob, target_date):
    """Run the wallet_slice apply pipeline.

    Step 1 — upload the sliced position (securities + cashAccount Caixa
    row) onto the target wallet using the same upstream endpoint the
    position_strip flow uses. One XLSX, one HTTP call.

    Step 2 — create one provision on the target wallet per source
    provision covering `target_date`, with balance × ratio.

    Step 3 — create one transaction on the target wallet per source
    transaction liquidating on `target_date`, with balance/quantity ×
    ratio.

    Each step records its own per-row results so partial successes are
    visible to the operator. An auth error (401) short-circuits the
    remaining steps to avoid burning the token. `lastApplied` is only
    written when nothing failed across all three steps."""
    company_id = blob["companyId"]
    tgt_w = blob["targetWalletId"]
    plan = _build_slice_plan(blob, target_date)
    auth_error = None

    # Resolve a `companyId` da carteira destino — pode diferir da exceção
    # quando o operador escolhe target cross-company. Beehus indexa o
    # upload de posições + provisions + transactions por `(companyId,
    # walletId)`; usar a `companyId` errada manda o write pra empresa
    # errada (ou Beehus rejeita). Fallback pro company da exceção quando
    # a target não tem `companyId` resolvido (defesa contra dado faltando).
    tgt_meta = _resolve_wallet_meta([tgt_w]).get(tgt_w) or {}
    tgt_company_id = tgt_meta.get("companyId") or company_id

    # ── Step 1: upload positions ─────────────────────────────────────────
    rows = []
    for s in plan["securities"]:
        rows.append({
            "walletId":      s["walletId"],
            "unprocessedId": s["unprocessedId"],
            "quantity":      s["quantity"],
            "pu":            s["pu"],
            "balance":       s["balance"],
            "caixa":         False,
            "currencyId":    s["currencyId"],
        })
    cash = plan.get("cashAccount")
    if cash is not None:
        rows.append({
            "walletId":      cash["walletId"],
            "unprocessedId": cash["unprocessedId"],
            "quantity":      0,
            "pu":            0,
            "balance":       cash["balance"],
            "caixa":         True,
            "currencyId":    cash["currencyId"],
        })

    position_result = None
    if rows:
        xlsx = _build_xlsx(rows, target_date)
        try:
            up = upload_unprocessed_security_positions_file(
                company_id=tgt_company_id,
                file_bytes=xlsx,
                filename=f"fatiar_{tgt_w}_{target_date}.xlsx",
            )
            position_result = {"walletId": tgt_w, "status": "ok",
                               "rows": len(rows), "upstream": up}
        except BeehusAuthError as e:
            auth_error = {"error": str(e), "status": getattr(e, "status", None)}
            position_result = {"walletId": tgt_w, "status": "auth_error",
                               "error": str(e)}
        except BeehusAPIError as e:
            position_result = {"walletId": tgt_w, "status": "error",
                               "error": str(e),
                               "upstream_status": getattr(e, "status", None),
                               "upstream_body":   getattr(e, "body", None)}
    else:
        position_result = {"walletId": tgt_w, "status": "skipped",
                           "reason": "no rows"}

    # ── Step 2: provisions ──────────────────────────────────────────────
    prov_results = []
    target_meta = plan.get("targetMeta") or {}
    target_currency = plan.get("targetCurrencyId") or "BRL"
    if not auth_error:
        for p in plan["provisions"]:
            base_result = {
                "sourceId":      p.get("sourceId"),
                "description":   p.get("description"),
                "balance":       p.get("balance"),
                "initialDate":   p.get("initialDate"),
                "liquidationDate": p.get("liquidationDate"),
                "provisionType": p.get("provisionType"),
            }
            try:
                created = create_provision(
                    company_id=tgt_company_id,
                    wallet_id=tgt_w,
                    balance=p.get("balance") or 0,
                    initial_date=p.get("initialDate") or target_date,
                    liquidation_date=p.get("liquidationDate") or target_date,
                    provision_type=p.get("provisionType") or "adjustments",
                    provision_source=p.get("provisionSource") or "adjustments",
                    currency_id=p.get("currencyId") or target_currency,
                    description=p.get("description") or "",
                    security_id=(p.get("securityId") or None),
                )
                created_id = ""
                if isinstance(created, dict):
                    created_id = str(created.get("_id") or created.get("id") or "")
                prov_results.append({**base_result, "status": "ok",
                                     "createdId": created_id,
                                     "upstream":   created})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                prov_results.append({**base_result, "status": "auth_error",
                                     "error": str(e)})
                break
            except BeehusAPIError as e:
                prov_results.append({**base_result, "status": "error",
                                     "error": str(e),
                                     "upstream_status": getattr(e, "status", None),
                                     "upstream_body":   getattr(e, "body", None)})

    # ── Step 3: transactions ────────────────────────────────────────────
    txn_results = []
    if not auth_error:
        entity_id = target_meta.get("entityId") or ""
        for t in plan["transactions"]:
            base_result = {
                "sourceId":      t.get("sourceId"),
                "securityId":    t.get("securityId"),
                "balance":       t.get("balance"),
                "quantity":      t.get("quantity"),
                "description":   t.get("description"),
                "type":          t.get("beehusTransactionType"),
                "liquidationDate": t.get("liquidationDate"),
            }
            if not entity_id:
                # Without an entityId on the target wallet the upstream
                # rejects the create with a 400; surface that as a
                # per-row skip instead of trying and inevitably failing.
                txn_results.append({**base_result, "status": "error",
                                    "error": "wallet sem entityId"})
                continue
            try:
                created = create_transaction(
                    company_id=tgt_company_id,
                    entity_id=entity_id,
                    wallet_id=tgt_w,
                    balance=t.get("balance") or 0,
                    operation_date=t.get("operationDate") or target_date,
                    liquidation_date=t.get("liquidationDate") or target_date,
                    currency_id=t.get("currencyId") or target_currency,
                    transaction_type=t.get("beehusTransactionType") or "withdrawalDeposit",
                    description=t.get("description") or "",
                    security_id=(t.get("securityId") or None),
                    quantity=t.get("quantity"),
                )
                created_id = ""
                if isinstance(created, dict):
                    created_id = str(created.get("_id") or created.get("id") or "")
                txn_results.append({**base_result, "status": "ok",
                                    "createdId": created_id,
                                    "upstream":   created})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                txn_results.append({**base_result, "status": "auth_error",
                                    "error": str(e)})
                break
            except BeehusAPIError as e:
                txn_results.append({**base_result, "status": "error",
                                    "error": str(e),
                                    "upstream_status": getattr(e, "status", None),
                                    "upstream_body":   getattr(e, "body", None)})

    failed = []
    if position_result and position_result["status"] not in ("ok", "skipped"):
        failed.append(position_result)
    failed.extend([r for r in prov_results if r["status"] not in ("ok",)])
    failed.extend([r for r in txn_results  if r["status"] not in ("ok",)])
    if not failed and not auth_error:
        blob["lastApplied"] = {
            "date": target_date,
            "at":   datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        _save(blob)

    response = {
        "kind":               _KIND_WALLET_SLICE,
        "percent":            plan.get("percent"),
        "sourceDate":         plan.get("sourceDate"),
        "targetDate":         plan.get("targetDate"),
        "fallback":           plan.get("fallback"),
        "positionResult":     position_result,
        "provisionResults":   prov_results,
        "transactionResults": txn_results,
        "walletNames":        plan.get("walletNames") or {},
        "securityNames":      plan.get("securityNames") or {},
    }
    if auth_error:
        return jsonify({**response, **auth_error}), 401
    if failed:
        return jsonify(response), 502
    return jsonify(response)


# ── class_strip — plan / preview / apply ─────────────────────────────────────

def _aggregate_processed_securities(securities):
    """Group raw `processedPosition.securities[]` rows by `securityId`,
    summing `quantity` / `balance` and carrying forward `executionPrice` /
    `amountDifference` / `hierarchicalVariable` from the first non-null seen.

    The processed collection can carry multiple rows per security (e.g. tax
    lots, intraday slices). For class_strip the upload onto the target
    wallet's `unprocessedSecurityPositions` is keyed by `unprocessedId`, so
    we consolidate to one row per security. `executionPrice` /
    `amountDifference` are taken from the **last** non-null row seen because
    they're position-level (not lot-level) — the upstream pricing engine
    rewrites them at the close of each cycle; lots later in the array carry
    the freshest values."""
    grouped = {}
    for s in securities or []:
        sid = (s.get("securityId") or "").strip()
        if not sid:
            continue
        try:
            qty = float(s.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        try:
            bal = float(s.get("balance") or 0)
        except (TypeError, ValueError):
            bal = 0.0
        g = grouped.setdefault(sid, {
            "securityId":          sid,
            "quantity":            0.0,
            "balance":             0.0,
            "executionPrice":      None,
            "amountDifference":    0.0,
            "hierarchicalVariable": None,
            "currency":            None,
        })
        g["quantity"] += qty
        g["balance"]  += bal
        ep = s.get("executionPrice")
        if ep is not None:
            try:
                g["executionPrice"] = float(ep)
            except (TypeError, ValueError):
                pass
        ad = s.get("amountDifference")
        if ad is not None:
            try:
                # amountDifference sums across lots — it's a per-lot delta.
                g["amountDifference"] += float(ad)
            except (TypeError, ValueError):
                pass
        hv = s.get("hierarchicalVariable")
        if hv and not g["hierarchicalVariable"]:
            g["hierarchicalVariable"] = hv
        cur = s.get("currency")
        if cur and not g["currency"]:
            g["currency"] = cur
    # Recompute PU per group so the upload row has consistent qty/balance/pu.
    for g in grouped.values():
        g["pu"] = (g["balance"] / g["quantity"]) if g["quantity"] else 0.0
    return list(grouped.values())


def _build_class_strip_plan(blob, target_date):
    """Compute the full class_strip plan for `target_date`.

    Reads `processedPosition.securities[]` from each source wallet (with
    per-wallet fallback up to 5 days back), buckets each security by its
    `hierarchicalVariable.variable1`, and routes it to the configured
    `targetWalletId`. Also collects provisions (`dividend`/`interestOnEquity`)
    and transactions (`coupon`/`amortization`/`securityContributionAdjustment`)
    that reference any migrated security, and pre-computes the three
    adjustment categories (amountDifference, provision settlement, transaction
    settlement).

    Returns a dict with keys: `kind`, `targetDate`, `sourceWalletIds`,
    `classRoutes`, `perSource[]`, `perTarget[]`, `summary`, `xlsxRows[]`,
    `walletNames`, `securityNames`.

    The `xlsxRows[]` field is built by the download_excel handler — one row
    per (wallet, unprocessedId) covering both source-side zeroing and
    target-side additions.
    """
    company_id = blob["companyId"]
    source_ids = list(blob.get("sourceWalletIds") or [])
    routes_raw = blob.get("classRoutes") or []
    route_by_var = {r["variable1"]: r["targetWalletId"] for r in routes_raw
                    if isinstance(r, dict) and r.get("variable1") and r.get("targetWalletId")}

    # securityId ↔ unprocessedId mapping — built once per company so each
    # matched security can be translated to the unprocessedId the upload
    # endpoint uses as the row key.
    sec_to_unp = _build_security_to_unprocessed_map(company_id)

    # Wallet meta (currency, entity, name) for the target side of adjustments.
    all_wallet_ids = set(source_ids) | {r["targetWalletId"] for r in routes_raw if isinstance(r, dict)}
    wallet_meta = {}
    for wid, w in _wallet_docs(all_wallet_ids).items():
        wallet_meta[wid] = {
            "name":       w.get("name") or wid,
            "currencyId": _wallet_currency(w),
            "entityId":   str(w.get("entityId") or "") if w.get("entityId") else "",
        }

    # ── Pass 1: per-source security match ────────────────────────────────
    # Each entry of `per_source` keeps the matched rows + the skipped ones
    # so the preview UI can show "X rotas executadas / Y sem rota".
    per_source = []
    # Aggregator keyed by `targetWalletId` so the same security coming from
    # multiple sources lands as a single (qty/balance-summed) row on the
    # target's upload. Also collects the per-security raw amountDifference
    # contributions so the adjustment phase can issue one transaction per
    # security-on-target.
    target_acc = {}   # wid -> {variable1s: set, securities: {sid: {...}}, securityToSourceIds: {sid: set(sourceWalletId)}}
    matched_security_ids_per_source = {}  # sourceWid -> set(securityId) — for the source-side zero-out

    for src_w in source_ids:
        raw_secs, used_date, fallback = _fetch_processed_with_fallback(src_w, target_date)
        agg_secs = _aggregate_processed_securities(raw_secs or [])

        # The processedPosition carries the *computed* PU; what the operator
        # actually wants uploaded to the destination is the **input** PU
        # from `unprocessedSecurityPositions` (matches the original upload
        # for that wallet/date). Build a `unp_index: unprocessedId -> {qty,
        # pu, balance}` from that collection and let it override the
        # processed values when present. Falls back silently to the
        # processed values when the wallet has no unprocessed doc on that
        # date (rare; surfaces as `puSource: "processed"` so the FE can
        # flag the row).
        unp_rows = _fetch_source_position(src_w, used_date) or []
        unp_index = {}
        for row in unp_rows:
            uid = (row.get("unprocessedId") or "").strip()
            if uid:
                unp_index[uid] = row

        matched = []
        skipped = []
        matched_sids = set()
        for s in agg_secs:
            hv = s.get("hierarchicalVariable") or {}
            v1 = (hv.get("variable1") or "").strip()
            sid = s["securityId"]
            if not v1:
                skipped.append({"securityId": sid, "variable1": "", "reason": "sem variable1"})
                continue
            tgt = route_by_var.get(v1)
            if not tgt:
                skipped.append({"securityId": sid, "variable1": v1, "reason": "variable1 sem rota"})
                continue
            unp = (sec_to_unp.get(sid) or [None])[0]
            if not unp:
                skipped.append({"securityId": sid, "variable1": v1, "reason": "sem mapping unprocessed"})
                continue
            unp_row = unp_index.get(unp)
            if unp_row is not None:
                # Prefer the unprocessed values — quantity/pu/balance came
                # straight from the operator's last upload, so re-uploading
                # them keeps the wallet's input ledger consistent.
                qty_use = float(unp_row.get("quantity") or 0)
                pu_use  = float(unp_row.get("pu") or 0)
                bal_use = float(unp_row.get("balance") or 0)
                pu_source = "unprocessed"
            else:
                qty_use = float(s.get("quantity") or 0)
                pu_use  = float(s.get("pu") or 0)
                bal_use = float(s.get("balance") or 0)
                pu_source = "processed"
            matched.append({
                "securityId":       sid,
                "unprocessedId":    unp,
                "variable1":        v1,
                "targetWalletId":   tgt,
                "quantity":         _round(qty_use, 8),
                "pu":               _round(pu_use,  8),
                "balance":          _round(bal_use, 2),
                "puSource":         pu_source,
                "executionPrice":   s.get("executionPrice"),
                "amountDifference": _round(s.get("amountDifference") or 0, 8),
                "currency":         s.get("currency") or wallet_meta.get(tgt, {}).get("currencyId") or "BRL",
            })
            matched_sids.add(sid)
            # Aggregate onto the target — sum quantity/balance, keep the
            # last `pu` seen (per-source PU may differ; the target row
            # ends up using the consolidated balance/quantity to recompute
            # PU before upload, so per-source PU here is only carried
            # through for diagnostic display).
            t_acc = target_acc.setdefault(tgt, {
                "walletId":   tgt,
                "variable1s": set(),
                "securities": {},
                "amountDiff": {},   # sid -> {amountDifference, executionPrice, sourceWalletIds, currency}
            })
            t_acc["variable1s"].add(v1)
            t_sec = t_acc["securities"].setdefault(sid, {
                "securityId":     sid,
                "unprocessedId":  unp,
                "quantity":       0.0,
                "balance":        0.0,
                "currencyId":     s.get("currency") or wallet_meta.get(tgt, {}).get("currencyId") or "BRL",
                "sourceWalletIds": [],
                "puSources":      set(),
                "perSourcePu":    [],
            })
            t_sec["quantity"] += qty_use
            t_sec["balance"]  += bal_use
            t_sec["sourceWalletIds"].append(src_w)
            t_sec["puSources"].add(pu_source)
            t_sec["perSourcePu"].append({"walletId": src_w, "pu": _round(pu_use, 8), "puSource": pu_source})
            # amountDifference adjustments accumulate per (target, security).
            ad = float(s.get("amountDifference") or 0)
            if ad:
                ad_entry = t_acc["amountDiff"].setdefault(sid, {
                    "securityId":      sid,
                    "amountDifference": 0.0,
                    "executionPrice":   s.get("executionPrice"),
                    "sourceWalletIds":  [],
                    "currency":         s.get("currency") or wallet_meta.get(tgt, {}).get("currencyId") or "BRL",
                })
                ad_entry["amountDifference"] += ad
                ad_entry["sourceWalletIds"].append(src_w)
                if ad_entry["executionPrice"] is None and s.get("executionPrice") is not None:
                    ad_entry["executionPrice"] = s.get("executionPrice")
        matched_security_ids_per_source[src_w] = matched_sids
        per_source.append({
            "walletId":   src_w,
            "sourceDate": used_date,
            "fallback":   fallback,
            "matched":    sorted(matched, key=lambda r: (r["variable1"], r["securityId"])),
            "skipped":    sorted(skipped, key=lambda r: (r["variable1"], r["securityId"])),
        })

    # ── Pass 2: provisions + transactions migration plans ────────────────
    # Migrate provisions whose securityId was matched on a source wallet,
    # provisionType is in the whitelist, and the (initialDate, liquidationDate)
    # window covers target_date. Routing is via the same variable1→target
    # map (i.e., the security's classification at apply time). One DB query
    # per source wallet keeps the cost bounded.
    migrated_provisions = []
    for src_w in source_ids:
        sids = matched_security_ids_per_source.get(src_w) or set()
        if not sids:
            continue
        _sid_set = {str(s) for s in sids}
        _ptypes = set(_CLASS_STRIP_PROV_TYPES)
        cursor = [p for p in beehus_catalog.provisions_active(
                      company_id, target_date, wallet_ids=[src_w])
                  if str(p.get("securityId") or "") in _sid_set
                  and (p.get("provisionType") or "") in _ptypes]
        for d in cursor:
            sid = str(d.get("securityId") or "")
            # Look up the target via the routes (a security's variable1 has
            # to be in the route map — that's why it was matched).
            # We find the route by looking up which target the security
            # landed on in target_acc.
            tgt_wid = None
            for twid, tacc in target_acc.items():
                if sid in tacc["securities"]:
                    tgt_wid = twid
                    break
            if not tgt_wid:
                continue
            try:
                bal = float(d.get("balance") or 0)
            except (TypeError, ValueError):
                bal = 0.0
            migrated_provisions.append({
                "sourceId":         str(d.get("_id") or ""),
                "sourceWalletId":   src_w,
                "targetWalletId":   tgt_wid,
                "securityId":       sid,
                "balance":          _round(bal, 2),
                "description":      (d.get("description") or "").strip(),
                "initialDate":      str(d.get("initialDate") or "")[:10],
                "liquidationDate":  str(d.get("liquidationDate") or "")[:10],
                "provisionType":    d.get("provisionType") or "",
                "provisionSource":  d.get("provisionSource") or "",
                "currencyId":       str(d.get("currencyId") or wallet_meta.get(tgt_wid, {}).get("currencyId") or "BRL"),
            })

    # Migrate transactions whose securityId was matched, type is in the
    # whitelist, liquidationDate == target_date, and walletId == source.
    migrated_transactions = []
    for src_w in source_ids:
        sids = matched_security_ids_per_source.get(src_w) or set()
        if not sids:
            continue
        cursor = [
            t for t in beehus_catalog.transactions_search(
                company_id, initial_date=target_date, final_date=target_date,
                wallet_ids=[src_w], security_ids=list(sids))
            if t.get("beehusTransactionType") in _CLASS_STRIP_TXN_TYPES
            and not t.get("trashed")
        ]
        for tx in cursor:
            sid = str(tx.get("securityId") or "")
            tgt_wid = None
            for twid, tacc in target_acc.items():
                if sid in tacc["securities"]:
                    tgt_wid = twid
                    break
            if not tgt_wid:
                continue
            try:
                bal = float(tx.get("balance")) if tx.get("balance") is not None else 0.0
            except (TypeError, ValueError):
                bal = 0.0
            try:
                qty = float(tx.get("quantity")) if tx.get("quantity") is not None else None
            except (TypeError, ValueError):
                qty = None
            migrated_transactions.append({
                "sourceId":              str(tx["_id"]),
                "sourceWalletId":        src_w,
                "targetWalletId":        tgt_wid,
                "securityId":            sid,
                "balance":               _round(bal, 2),
                "quantity":              (_round(qty, 8) if qty is not None else None),
                "liquidationDate":       str(tx.get("liquidationDate") or "")[:10],
                "operationDate":         str(tx.get("operationDate") or "")[:10],
                "description":           tx.get("description") or "",
                "beehusTransactionType": tx.get("beehusTransactionType") or "",
                "currencyId":            str(tx.get("currencyId") or wallet_meta.get(tgt_wid, {}).get("currencyId") or "BRL"),
            })

    # ── Pass 3: per-target consolidated view + adjustments ───────────────
    sec_name_index = get_security_names()
    per_target = []
    for tgt_wid, t_acc in target_acc.items():
        # Consolidated securities list for this target (one row per
        # securityId; quantity/balance summed across source wallets).
        secs_out = []
        for sid, s_acc in t_acc["securities"].items():
            qty = s_acc["quantity"]
            bal = s_acc["balance"]
            pu  = (bal / qty) if qty else 0.0
            secs_out.append({
                "securityId":      sid,
                "securityName":    sec_name_index.get(sid, sid),
                "unprocessedId":   s_acc["unprocessedId"],
                "quantity":        _round(qty, 8),
                "pu":              _round(pu,  8),
                "balance":         _round(bal, 2),
                "currencyId":      s_acc["currencyId"],
                "sourceWalletIds": s_acc["sourceWalletIds"],
                # `puSources` is a Set in the accumulator (multi-source rows
                # may mix "unprocessed" and "processed" fallbacks). Serialise
                # as a sorted list so the FE can show a pill like
                # `unprocessed` / `processed` / `unprocessed+processed`.
                "puSources":       sorted(list(s_acc.get("puSources") or [])),
                "perSourcePu":     list(s_acc.get("perSourcePu") or []),
            })
        secs_out.sort(key=lambda r: r["securityId"])

        # Provisions/transactions filtered to this target (already routed
        # per source pass above).
        provs_here = [p for p in migrated_provisions     if p["targetWalletId"] == tgt_wid]
        txs_here   = [t for t in migrated_transactions   if t["targetWalletId"] == tgt_wid]

        # ── Adjustment (i): per security with amountDifference != 0 ────
        adj_amount = []
        for sid, ad_entry in t_acc["amountDiff"].items():
            ad = float(ad_entry["amountDifference"] or 0)
            ep = ad_entry["executionPrice"]
            if not ad or ep is None:
                continue
            bal = ad * float(ep)
            if not bal:
                continue
            adj_amount.append({
                "securityId":       sid,
                "securityName":     sec_name_index.get(sid, sid),
                "amountDifference": _round(ad, 8),
                "executionPrice":   _round(float(ep), 8),
                "balance":          _round(bal, 2),
                "currencyId":       ad_entry["currency"],
                "description":      f"Ajuste classe — diferença {sec_name_index.get(sid, sid)}",
                "sourceWalletIds":  ad_entry["sourceWalletIds"],
            })
        adj_amount.sort(key=lambda r: r["securityId"])

        # ── Adjustment (ii): provision settling on target_date ─────────
        adj_prov = []
        for p in provs_here:
            if p["liquidationDate"] == target_date:
                adj_prov.append({
                    "provisionSourceId": p["sourceId"],
                    "balance":           _round(-float(p["balance"] or 0), 2),
                    "currencyId":        p["currencyId"],
                    "securityId":        p["securityId"],
                    "description":       f"Ajuste classe — provisão liquidando {p['description']}".strip(),
                })

        # ── Adjustment (iii): transaction settling on target_date ──────
        # Every migrated transaction has liquidationDate == target_date by
        # the migration filter, so each gets a paired adjustment.
        adj_tx = []
        for t in txs_here:
            adj_tx.append({
                "transactionSourceId": t["sourceId"],
                "balance":             _round(-float(t["balance"] or 0), 2),
                "currencyId":          t["currencyId"],
                "securityId":          t["securityId"],
                "description":         f"Ajuste classe — tx liquidando {t['description']}".strip(),
            })

        per_target.append({
            "walletId":     tgt_wid,
            "walletName":   wallet_meta.get(tgt_wid, {}).get("name", tgt_wid),
            "currencyId":   wallet_meta.get(tgt_wid, {}).get("currencyId", "BRL"),
            "variable1s":   sorted(t_acc["variable1s"]),
            "securities":   secs_out,
            "provisions":   provs_here,
            "transactions": txs_here,
            "adjustments": {
                "amountDifference":      adj_amount,
                "provisionSettlement":   adj_prov,
                "transactionSettlement": adj_tx,
            },
        })
    per_target.sort(key=lambda r: r["walletName"].lower())

    # ── XLSX rows: one for each (sourceWallet, matched unprocessedId) at
    # qty=0/balance=0 (zero-out), plus one for each (targetWallet,
    # unprocessedId) with the consolidated values. Operators can inspect
    # this offline via the /excel endpoint. cashAccount is intentionally
    # left untouched — class_strip only moves securities + provisions +
    # transactions of the configured types; the operator handles cash
    # movement separately if needed.
    xlsx_rows = []
    for ps in per_source:
        wid = ps["walletId"]
        cur = wallet_meta.get(wid, {}).get("currencyId", "BRL")
        for m in ps["matched"]:
            xlsx_rows.append({
                "walletId":      wid,
                "unprocessedId": m["unprocessedId"],
                "quantity":      0,
                "pu":            0,
                "balance":       0,
                "caixa":         False,
                "currencyId":    cur,
            })
    for pt in per_target:
        wid = pt["walletId"]
        cur = pt["currencyId"]
        for s in pt["securities"]:
            xlsx_rows.append({
                "walletId":      wid,
                "unprocessedId": s["unprocessedId"],
                "quantity":      s["quantity"],
                "pu":            s["pu"],
                "balance":       s["balance"],
                "caixa":         False,
                "currencyId":    cur,
            })

    # Aggregate counters for the summary card.
    summary = {
        "matchedSecurities":      sum(len(ps["matched"]) for ps in per_source),
        "skippedSecurities":      sum(len(ps["skipped"]) for ps in per_source),
        "migratedProvisions":     len(migrated_provisions),
        "migratedTransactions":   len(migrated_transactions),
        "adjustmentTransactions": sum(
            len(pt["adjustments"]["amountDifference"]) +
            len(pt["adjustments"]["provisionSettlement"]) +
            len(pt["adjustments"]["transactionSettlement"])
            for pt in per_target
        ),
    }

    # Resolve wallet + security name maps for the response so the FE doesn't
    # have to call /api/excecoes/wallets to render labels.
    wallet_names = {wid: meta["name"] for wid, meta in wallet_meta.items()}
    sec_ids_used = set()
    for ps in per_source:
        for m in ps["matched"]:
            sec_ids_used.add(m["securityId"])
    for pt in per_target:
        for s in pt["securities"]:
            sec_ids_used.add(s["securityId"])
    security_names = {sid: sec_name_index.get(sid, sid) for sid in sec_ids_used}

    return {
        "kind":            _KIND_CLASS_STRIP,
        "targetDate":      target_date,
        "sourceWalletIds": source_ids,
        "classRoutes":     list(routes_raw),
        "perSource":       per_source,
        "perTarget":       per_target,
        "summary":         summary,
        "xlsxRows":        xlsx_rows,
        "walletNames":     wallet_names,
        "securityNames":   security_names,
        # Side-channels carried through for `_apply_class_strip` so the
        # apply doesn't have to rebuild the plan twice (preview already
        # ran it once for the FE summary).
        "migratedProvisions":   migrated_provisions,
        "migratedTransactions": migrated_transactions,
        "walletMeta":           wallet_meta,
    }


def _preview_class_strip(blob, target_date):
    plan = _build_class_strip_plan(blob, target_date)
    # Drop the internal side-channels before serialising — the FE doesn't
    # consume them and they bloat the payload.
    public = {k: v for k, v in plan.items()
              if k not in ("xlsxRows", "migratedProvisions", "migratedTransactions", "walletMeta")}
    return jsonify(public)


def _apply_class_strip(blob, target_date):
    """Run the class_strip apply pipeline.

    Pipeline (any step's 401 short-circuits the rest):
      1a. Upload XLSX for every source wallet (matched unprocessedIds zeroed,
          everything else preserved by upstream merge semantics).
      1b. Upload XLSX for every target wallet (consolidated additions).
      2.  PATCH each migrated provision's walletId to its routed target.
      3.  PATCH each migrated transaction's walletId to its routed target.
      4.  POST `withdrawalDepositAdjustment` for every adjustment row:
          (i) amountDifference, (ii) provision settlement, (iii) tx settlement.
    """
    company_id = blob["companyId"]
    plan = _build_class_strip_plan(blob, target_date)
    auth_error = None

    # Resolve currency once per touched wallet — needed for both the upload
    # XLSX rows (already set) and the adjustment transactions.
    wallet_meta = plan.get("walletMeta") or {}

    # ── Step 1a: source uploads (zero-out matched unprocessedIds) ─────────
    source_results = []
    for ps in plan["perSource"]:
        wid = ps["walletId"]
        if not ps["matched"]:
            source_results.append({"walletId": wid, "status": "skipped", "reason": "no matched rows"})
            continue
        cur = wallet_meta.get(wid, {}).get("currencyId", "BRL")
        rows = [{
            "walletId":      wid,
            "unprocessedId": m["unprocessedId"],
            "quantity":      0,
            "pu":            0,
            "balance":       0,
            "caixa":         False,
            "currencyId":    cur,
        } for m in ps["matched"]]
        try:
            up = upload_unprocessed_security_positions_file(
                company_id=company_id,
                file_bytes=_build_xlsx(rows, target_date),
                filename=f"classe_src_{wid}_{target_date}.xlsx",
            )
            source_results.append({"walletId": wid, "status": "ok",
                                   "rows": len(rows), "upstream": up})
        except BeehusAuthError as e:
            auth_error = {"error": str(e), "status": getattr(e, "status", None)}
            source_results.append({"walletId": wid, "status": "auth_error", "error": str(e)})
            break
        except BeehusAPIError as e:
            source_results.append({"walletId": wid, "status": "error",
                                   "error": str(e),
                                   "upstream_status": getattr(e, "status", None),
                                   "upstream_body":   getattr(e, "body", None)})

    # ── Step 1b: target uploads (consolidated securities only) ───────────
    target_results = []
    if not auth_error:
        for pt in plan["perTarget"]:
            wid = pt["walletId"]
            if not pt["securities"]:
                target_results.append({"walletId": wid, "status": "skipped", "reason": "no securities"})
                continue
            rows = [{
                "walletId":      wid,
                "unprocessedId": s["unprocessedId"],
                "quantity":      s["quantity"],
                "pu":            s["pu"],
                "balance":       s["balance"],
                "caixa":         False,
                "currencyId":    pt["currencyId"],
            } for s in pt["securities"]]
            try:
                up = upload_unprocessed_security_positions_file(
                    company_id=company_id,
                    file_bytes=_build_xlsx(rows, target_date),
                    filename=f"classe_tgt_{wid}_{target_date}.xlsx",
                )
                target_results.append({"walletId": wid, "status": "ok",
                                       "rows": len(rows), "upstream": up})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                target_results.append({"walletId": wid, "status": "auth_error", "error": str(e)})
                break
            except BeehusAPIError as e:
                target_results.append({"walletId": wid, "status": "error",
                                       "error": str(e),
                                       "upstream_status": getattr(e, "status", None),
                                       "upstream_body":   getattr(e, "body", None)})

    # ── Step 2: provisions PATCH (walletId -> target) ────────────────────
    prov_results = []
    if not auth_error:
        for p in plan.get("migratedProvisions") or []:
            base = {
                "sourceId":       p["sourceId"],
                "sourceWalletId": p["sourceWalletId"],
                "targetWalletId": p["targetWalletId"],
                "securityId":     p["securityId"],
                "balance":        p["balance"],
            }
            try:
                up = update_provision(p["sourceId"], {"walletId": p["targetWalletId"]})
                prov_results.append({**base, "status": "ok", "upstream": up})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                prov_results.append({**base, "status": "auth_error", "error": str(e)})
                break
            except BeehusAPIError as e:
                prov_results.append({**base, "status": "error",
                                     "error": str(e),
                                     "upstream_status": getattr(e, "status", None),
                                     "upstream_body":   getattr(e, "body", None)})

    # ── Step 3: transactions PATCH (walletId -> target) ──────────────────
    txn_results = []
    if not auth_error:
        for t in plan.get("migratedTransactions") or []:
            base = {
                "sourceId":       t["sourceId"],
                "sourceWalletId": t["sourceWalletId"],
                "targetWalletId": t["targetWalletId"],
                "securityId":     t["securityId"],
                "balance":        t["balance"],
                "type":           t["beehusTransactionType"],
            }
            try:
                up = update_transaction(t["sourceId"], {"walletId": t["targetWalletId"]})
                txn_results.append({**base, "status": "ok", "upstream": up})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                txn_results.append({**base, "status": "auth_error", "error": str(e)})
                break
            except BeehusAPIError as e:
                txn_results.append({**base, "status": "error",
                                    "error": str(e),
                                    "upstream_status": getattr(e, "status", None),
                                    "upstream_body":   getattr(e, "body", None)})

    # ── Step 4: adjustments ──────────────────────────────────────────────
    # Three categories, all `withdrawalDepositAdjustment` on the **target**
    # wallet with `liquidationDate = operationDate = target_date`. Wallets
    # missing `entityId` short-circuit to per-row error (mirrors wallet_slice).
    adj_amount_results = []
    adj_prov_results   = []
    adj_tx_results     = []

    def _create_adjustment(target_wid, security_id, balance, currency_id, description):
        entity_id = wallet_meta.get(target_wid, {}).get("entityId") or ""
        base = {
            "walletId":    target_wid,
            "securityId":  security_id or "",
            "balance":     balance,
            "currencyId":  currency_id,
            "description": description,
        }
        if not entity_id:
            return {**base, "status": "error", "error": "wallet sem entityId"}, None
        try:
            created = create_transaction(
                company_id=company_id,
                entity_id=entity_id,
                wallet_id=target_wid,
                balance=balance,
                operation_date=target_date,
                liquidation_date=target_date,
                currency_id=currency_id,
                transaction_type="withdrawalDepositAdjustment",
                description=description,
                security_id=(security_id or None),
            )
            created_id = ""
            if isinstance(created, dict):
                created_id = str(created.get("_id") or created.get("id") or "")
            return {**base, "status": "ok", "createdId": created_id, "upstream": created}, None
        except BeehusAuthError as e:
            return {**base, "status": "auth_error", "error": str(e)}, \
                   {"error": str(e), "status": getattr(e, "status", None)}
        except BeehusAPIError as e:
            return {**base, "status": "error",
                    "error": str(e),
                    "upstream_status": getattr(e, "status", None),
                    "upstream_body":   getattr(e, "body", None)}, None

    if not auth_error:
        for pt in plan["perTarget"]:
            wid = pt["walletId"]
            cur = pt["currencyId"]
            if auth_error:
                break
            for adj in pt["adjustments"]["amountDifference"]:
                r, ae = _create_adjustment(wid, adj["securityId"], adj["balance"],
                                           adj.get("currencyId") or cur, adj["description"])
                adj_amount_results.append(r)
                if ae:
                    auth_error = ae
                    break
            if auth_error:
                break
            for adj in pt["adjustments"]["provisionSettlement"]:
                r, ae = _create_adjustment(wid, adj.get("securityId"), adj["balance"],
                                           adj.get("currencyId") or cur, adj["description"])
                adj_prov_results.append(r)
                if ae:
                    auth_error = ae
                    break
            if auth_error:
                break
            for adj in pt["adjustments"]["transactionSettlement"]:
                r, ae = _create_adjustment(wid, adj.get("securityId"), adj["balance"],
                                           adj.get("currencyId") or cur, adj["description"])
                adj_tx_results.append(r)
                if ae:
                    auth_error = ae
                    break

    # ── Step 5: process processedPosition for every target wallet ────────
    # Same upstream call that "Painel de Controle > Processamento" fires:
    # after the unprocessedSecurityPositions upload + provision/transaction
    # repointing, the destination needs its processedPosition rebuilt for
    # the target date so the operator sees the merged state immediately.
    # One call per target wallet keeps the upstream load bounded; a 401
    # short-circuits the rest.
    target_wallet_ids = [pt["walletId"] for pt in plan["perTarget"]]
    process_results = []
    if not auth_error and target_wallet_ids:
        for wid in target_wallet_ids:
            try:
                up = process_processed_position(
                    company_id=company_id,
                    position_date=target_date,
                    wallets=[wid],
                )
                process_results.append({"walletId": wid, "status": "ok", "upstream": up})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                process_results.append({"walletId": wid, "status": "auth_error", "error": str(e)})
                break
            except BeehusAPIError as e:
                process_results.append({"walletId": wid, "status": "error",
                                        "error": str(e),
                                        "upstream_status": getattr(e, "status", None),
                                        "upstream_body":   getattr(e, "body", None)})

    # ── Step 6: NAV calculation for every target wallet ──────────────────
    # Mirrors "Painel de Controle > NAV Wallets" — recomputes the NAV
    # contribution of each target wallet on `target_date` so the daily
    # consolidation reflects the just-migrated securities. Runs only after
    # processamento because the NAV calc reads `processedPosition` rows.
    nav_results = []
    if not auth_error and target_wallet_ids:
        for wid in target_wallet_ids:
            try:
                up = calculate_nav_wallets(
                    company_id=company_id,
                    position_date=target_date,
                    wallets=[wid],
                )
                nav_results.append({"walletId": wid, "status": "ok", "upstream": up})
            except BeehusAuthError as e:
                auth_error = {"error": str(e), "status": getattr(e, "status", None)}
                nav_results.append({"walletId": wid, "status": "auth_error", "error": str(e)})
                break
            except BeehusAPIError as e:
                nav_results.append({"walletId": wid, "status": "error",
                                    "error": str(e),
                                    "upstream_status": getattr(e, "status", None),
                                    "upstream_body":   getattr(e, "body", None)})
        # NAV recalculado upstream p/ as carteiras-alvo → invalida o cache
        # consolidado da empresa (grade de divergência / publish-state).
        if nav_results:
            beehus_catalog.invalidate_nav(company_id)

    # Aggregate failure flags. Only stamp `lastApplied` when nothing failed
    # across the entire pipeline (including process + nav steps — operators
    # should be able to rely on lastApplied meaning "destination is ready
    # to be reviewed").
    failed = []
    failed.extend([r for r in source_results if r["status"] not in ("ok", "skipped")])
    failed.extend([r for r in target_results if r["status"] not in ("ok", "skipped")])
    failed.extend([r for r in prov_results    if r["status"] != "ok"])
    failed.extend([r for r in txn_results     if r["status"] != "ok"])
    failed.extend([r for r in adj_amount_results if r["status"] != "ok"])
    failed.extend([r for r in adj_prov_results   if r["status"] != "ok"])
    failed.extend([r for r in adj_tx_results     if r["status"] != "ok"])
    failed.extend([r for r in process_results    if r["status"] != "ok"])
    failed.extend([r for r in nav_results        if r["status"] != "ok"])
    if not failed and not auth_error:
        blob["lastApplied"] = {
            "date": target_date,
            "at":   datetime.utcnow().isoformat(timespec="seconds") + "Z",
        }
        _save(blob)

    response = {
        "kind":               _KIND_CLASS_STRIP,
        "targetDate":         target_date,
        "sourceResults":      source_results,
        "targetResults":      target_results,
        "provisionResults":   prov_results,
        "transactionResults": txn_results,
        "adjustmentResults": {
            "amountDifference":      adj_amount_results,
            "provisionSettlement":   adj_prov_results,
            "transactionSettlement": adj_tx_results,
        },
        "processResults": process_results,
        "navResults":     nav_results,
        "walletNames":   plan.get("walletNames") or {},
        "securityNames": plan.get("securityNames") or {},
    }
    if auth_error:
        return jsonify({**response, **auth_error}), 401
    if failed:
        return jsonify(response), 502
    return jsonify(response)


# ── Ajustes Day-Trade ─────────────────────────────────────────────────────────
# Detects same-day buy+sell transactions whose security never landed in the
# wallet's `processedPosition` for that liquidationDate, then builds a patched
# `unprocessedSecurityPositions` payload that zeroes out the corresponding
# unprocessedId rows so the upstream pricing pipeline stops carrying ghost
# quantities/balances. The detection mirrors the filters from Funções >
# Identificar Transações (company, dates, optional groupings/wallets).

def _build_security_to_unprocessed_map(company_id):
    """Return `{securityId: [unprocessedId, ...]}` for a company.

    Reverse of `_build_unprocessed_to_security_map`. The mapping `to` field
    can collide (multiple unprocessedIds → same securityId), so the value is
    a list. Used to translate a day-traded `securityId` (carried by
    transactions) back to the unprocessedId rows that need to be zeroed out
    in the position payload."""
    doc = beehus_catalog.security_mappings_doc(company_id)
    if not doc:
        return {}
    out = {}
    # Normalise both sides to `str()` so the lookup works regardless of
    # whether the mapping was stored with ObjectId or string values — the
    # detection step always uses `str(securityId)` for group keys.
    for m in doc.get("mappings") or []:
        f, t = m.get("from"), m.get("to")
        if not f or not t:
            continue
        out.setdefault(str(t), []).append(str(f))
    return out


def _intraday_resolve_wallets(*, company_id, grouping_ids, wallet_ids):
    """Resolve the candidate wallet set from the filters.

    Mirrors the wallet-resolution logic in
    `pages/beehus_console.transactions_search`: company-wide by default,
    narrowed by `groupingIds` (union of their walletIds), then intersected
    with explicit `walletIds` if any.
    """
    # Company-wide candidate set from the cached wallet index (ids são str).
    candidate = set(beehus_catalog.wallets_for_company(company_id).keys())
    if grouping_ids:
        gindex = get_grouping_index()
        ids = set()
        for gid in grouping_ids:
            g = gindex.get(gid)
            if not g or g.get("trashed"):
                continue
            if g.get("companyId") and g["companyId"] != company_id:
                continue  # cross-company guard
            ids.update(g.get("walletIds") or [])
        if not ids:
            return set()
        # Narrow to the grouping wallets, keeping the implicit company
        # intersection the original `find({companyId, _id:$in})` enforced.
        candidate &= {beehus_catalog.id_str(w) for w in ids if w}
    explicit = set(wallet_ids or [])
    if explicit:
        candidate &= explicit
    return candidate


def _intraday_detect(*, company_id, initial_date, final_date, candidate_wallets):
    """Return a list of day-trade groups.

    Selection criteria:
      a. `beehusTransactionType == "buySell"`
      b. ≥ 2 transactions sharing `(walletId, securityId, liquidationDate)`
      c. `securityId` absent from `processedPosition.securities` for the
         same `walletId` on `positionDate == liquidationDate`

    The combination means the security was bought and sold on the same day
    and never settled into the processed position — a classic intraday
    round-trip ("day-trade") that the upstream pipeline still records as a
    holding in `unprocessedSecurityPositions`.

    Returns: `[{walletId, securityId, date, transactions: [...], contribution}]`
    where `contribution` is the algebraic sum of transaction balances (≈ 0
    for a clean round-trip, but may carry costs/taxes).
    """
    if not candidate_wallets:
        return []

    cursor = [
        t for t in beehus_catalog.transactions_search(
            company_id, initial_date=initial_date, final_date=final_date,
            wallet_ids=list(candidate_wallets))
        if t.get("beehusTransactionType") == "buySell"
        and not t.get("trashed")
    ]

    groups = {}
    for d in cursor:
        wid = str(d.get("walletId") or "")
        sid = str(d.get("securityId") or "") if d.get("securityId") else ""
        date = str(d.get("liquidationDate") or "")[:10]
        if not wid or not sid or not date:
            continue
        key = (wid, sid, date)
        g = groups.setdefault(key, {"transactions": [], "contribution": 0.0,
                                    "currencyId": ""})
        g["transactions"].append({
            "id":             str(d["_id"]),
            "balance":        _round(d.get("balance"), 2) if d.get("balance") is not None else None,
            "quantity":       _round(d.get("quantity"), 8) if d.get("quantity") is not None else None,
            "operationDate":  str(d.get("operationDate") or "")[:10],
            "liquidationDate": date,
            "description":    d.get("description") or "",
            "type":           d.get("beehusTransactionType") or "",
            "currencyId":     str(d.get("currencyId") or ""),
        })
        try:
            g["contribution"] += float(d.get("balance") or 0)
        except (TypeError, ValueError):
            pass
        if not g["currencyId"]:
            g["currencyId"] = str(d.get("currencyId") or "")

    candidates = {k: v for k, v in groups.items() if len(v["transactions"]) > 1}
    if not candidates:
        return []

    # Batch-fetch processed positions per (walletId, date) via endpoint A
    # (`processed_positions_map`: 1 chamada por data, walletIds plural; fallback
    # Mongo num único find multi-data). Evita o N+1 de um find_one por par.
    pos_keys = {(wid, date) for (wid, _, date) in candidates.keys()}
    _pos_map = beehus_catalog.processed_positions_map(
        company_id, sorted({w for (w, _d) in pos_keys}),
        sorted({_d for (w, _d) in pos_keys}))
    processed_ids = {}
    for wid, date in pos_keys:
        processed_ids[(wid, date)] = {
            str(s["securityId"]) for s in _pos_map.get((wid, date), [])
            if s.get("securityId")
        }

    out = []
    for (wid, sid, date), g in candidates.items():
        if sid in processed_ids.get((wid, date), set()):
            continue  # security settled into processed position — not a day-trade
        contribution = _round(g["contribution"], 2)
        # A perfectly-cancelling round-trip (Σ balance == 0 at 2 decimals) is
        # noise here: the position contribution is already neutralised, so
        # there is nothing to zero in `unprocessedSecurityPositions`. Drop
        # these silently to keep the operator focused on actionable rows.
        if contribution == 0:
            continue
        out.append({
            "walletId":         wid,
            "securityId":       sid,
            "date":             date,
            "transactionCount": len(g["transactions"]),
            "contribution":     contribution,
            "currencyId":       g["currencyId"],
            "transactions":     g["transactions"],
        })
    out.sort(key=lambda x: (x["date"], x["walletId"], x["securityId"]))
    return out


def _intraday_build_patched_positions(*, company_id, groups):
    """For every (walletId, date) touched by a day-trade group, fetch the
    `unprocessedSecurityPositions` doc and produce a patched payload.

    Rule for the day-trade "Ativo" identifier: the row carries the
    security's **`beehusName`** (read from `db.securities` via
    `get_security_names()`). When the upstream replaces the wallet's full
    position on apply, this is the row that surfaces as the day-trade
    placeholder — we explicitly avoid the legacy `unprocessedId` form
    (e.g. `BRBMEFDOL819_DOL_K26_2026-05-04`) because the operator-facing
    name (e.g. `DOLFK26`) is what matches the rest of the surface area.

    Algorithm per (walletId, date):

      1. Aggregate the source position by `unprocessedId`.
      2. Compute the set of source `unprocessedId`s that map (via
         `securityMappings`) to one of the day-traded securityIds — those
         rows are **dropped** from the baseline so we don't ship two
         names for the same logical security.
      3. Append one synthetic zero row per day-traded securityId,
         identified by `beehusName`. Round-trips that left no carry
         (very common for futures) hit only this branch.
      4. Day-traded securityIds with no `beehusName` in `db.securities`
         are reported in `unmappedSecurityIds[]` and skipped — without
         a name we can't construct a valid Ativo.

    Returns: `[{walletId, date, currencyId, rows: [...], zeroedUnprocessedIds,
                addedUnprocessedIds, replacedUnprocessedIds, hasPosition,
                dayTradeSecurityIds, unmappedSecurityIds}]`. Each `rows[]`
    entry carries:
      - `zeroed: bool` — the row will be uploaded at zero;
      - `added: bool`  — the row is the synthetic day-trade placeholder
                         (always implies `zeroed`).
    """
    sec_to_uids = _build_security_to_unprocessed_map(company_id)
    sec_name_index = get_security_names()  # {securityId: beehusName}

    by_wallet_date = {}
    for g in groups:
        key = (g["walletId"], g["date"])
        entry = by_wallet_date.setdefault(key, {"securityIds": set(),
                                                "currencyId":  ""})
        entry["securityIds"].add(g["securityId"])
        # Use the first non-empty transaction currency we see for this
        # (wallet, date). It feeds the day-trade rows below — typically a
        # security trades in a single currency on a given day.
        if not entry["currencyId"]:
            entry["currencyId"] = g.get("currencyId") or ""

    cur_map = {}
    if by_wallet_date:
        wid_set = {wid for (wid, _) in by_wallet_date.keys()}
        cur_map = {wid: _wallet_currency(w) for wid, w in _wallet_docs(wid_set).items()}

    # Pre-fetch unprocessed positions for all (wallet, date) pairs via endpoint
    # B (`unprocessed_docs_map`: 1 chamada de range; fallback Mongo multi-data),
    # em vez de um find_one por par (evita N+1).
    _unp_map = beehus_catalog.unprocessed_docs_map(
        company_id, [w for (w, _d) in by_wallet_date.keys()],
        [_d for (w, _d) in by_wallet_date.keys()])
    out = []
    for (wid, date), entry in by_wallet_date.items():
        sec_set = entry["securityIds"]
        doc = _unp_map.get((wid, date))
        secs = _aggregate_unprocessed((doc or {}).get("securities") or [])

        # Source `unprocessedId`s that should be replaced by a beehusName
        # row. We reach them through securityMappings: `from` is a
        # legacy unprocessedId, `to` is the canonical securityId.
        replaced_uids = set()
        for sid in sec_set:
            for uid in sec_to_uids.get(sid, []):
                replaced_uids.add(uid)
        present_uids = {s["unprocessedId"] for s in secs}
        replaced_uids &= present_uids  # only what was actually in baseline

        # Default currency for the day-trade row: prefer the wallet's
        # registered currency (matches what other rows in the position
        # carry) and fall back to the transaction's currency.
        default_currency = cur_map.get(wid, entry["currencyId"] or "")

        rows = []
        # Baseline rows — keep untouched, but drop the ones being
        # replaced by a beehusName-identified day-trade row.
        for s in secs:
            uid = s["unprocessedId"]
            if uid in replaced_uids:
                continue
            rows.append({
                "unprocessedId": uid,
                "quantity":      _round(s.get("quantity"), 8),
                "pu":            _round(s.get("pu"),       8),
                "balance":       _round(s.get("balance"),  2),
                "caixa":         False,
                "currencyId":    default_currency,
                "walletId":      wid,
                "zeroed":        False,
                "added":         False,
            })

        # One day-trade row per securityId, keyed by beehusName.
        added_names   = []
        unmapped_sids = []
        for sid in sorted(sec_set):
            name = sec_name_index.get(sid) or ""
            if not name:
                # No beehusName in db.securities for this id — we can't
                # produce an Ativo string the upstream will recognise.
                unmapped_sids.append(sid)
                continue
            rows.append({
                "unprocessedId": name,
                "securityId":    sid,
                "quantity":      0.0,
                "pu":            0.0,
                "balance":       0.0,
                "caixa":         False,
                "currencyId":    default_currency,
                "walletId":      wid,
                "zeroed":        True,
                "added":         True,
            })
            added_names.append(name)
        rows.sort(key=lambda r: r["unprocessedId"])

        out.append({
            "walletId":               wid,
            "date":                   date,
            "currencyId":             default_currency,
            "rows":                   rows,
            "zeroedUnprocessedIds":   sorted(added_names),
            "addedUnprocessedIds":    sorted(added_names),
            "replacedUnprocessedIds": sorted(replaced_uids),
            "dayTradeSecurityIds":    sorted(sec_set),
            "unmappedSecurityIds":    unmapped_sids,
            "hasPosition":            doc is not None,
        })

    out.sort(key=lambda x: (x["date"], x["walletId"]))
    return out


def _resolve_wallet_meta(wallet_ids):
    """Return `{walletId: {entityId, currencyId, companyId}}` for the given
    wallets. Used by the transaction-creation step to populate `entityId`/
    `currencyId` the same way `Funções > Criar Transações` does (auto-set
    from the wallet's own fields). `companyId` é necessário pra strip/slice
    cross-company: o upload de posições e os `create_transaction` /
    `create_provision` precisam casar com a empresa REAL da carteira destino
    (não a da exceção)."""
    if not wallet_ids:
        return {}
    out = {}
    for wid, w in _wallet_docs(wallet_ids).items():
        out[wid] = {
            "entityId":   str(w.get("entityId") or "") if w.get("entityId") else "",
            "currencyId": _wallet_currency(w),
            "companyId":  str(w.get("companyId") or "") if w.get("companyId") else "",
        }
    return out


def _intraday_plan_transactions(*, groups, wallet_meta, sec_name_index):
    """Plan one `securityContributionAdjustment` transaction per detected
    day-trade group. Mirrors the payload shape of `Funções > Criar
    Transações` (companyId/walletId/entityId/currencyId/operationDate/
    liquidationDate/description/balance/beehusTransactionType) plus the
    `securityId` the adjustment refers to.

    Returns one plan per group:
      {walletId, securityId, date, balance, currencyId, entityId,
       beehusTransactionType, description, operationDate, liquidationDate,
       securityBeehusName, skip, skipReason}

    Plans are marked `skip=True` when something prevents creation:
    wallet without `entityId`, security without `beehusName`. The apply
    step honours `skip` and reports the reason — without an entityId
    upstream rejects the create with a 400, and without a beehusName the
    description would be the literal string `day-trade ` (trailing
    space), which is useless."""
    plans = []
    for g in groups:
        wid  = g["walletId"]
        sid  = g["securityId"]
        date = g["date"]
        meta = wallet_meta.get(wid) or {}
        beehus_name = sec_name_index.get(sid) or ""
        # Currency precedence: transaction's currency (carries the day's
        # actual quote) → wallet currency (fallback for any future case
        # where the txn doc lacks currencyId) → BRL safety net.
        currency_id = (g.get("currencyId") or
                       meta.get("currencyId") or "BRL")
        plan = {
            "walletId":              wid,
            "securityId":            sid,
            "date":                  date,
            "balance":               _round(g.get("contribution"), 2),
            "currencyId":            currency_id,
            "entityId":              meta.get("entityId") or "",
            "beehusTransactionType": "securityContributionAdjustment",
            "description":           f"day-trade {beehus_name}".strip(),
            "operationDate":         date,
            "liquidationDate":       date,
            "securityBeehusName":    beehus_name,
            "skip":                  False,
            "skipReason":            None,
        }
        if not plan["entityId"]:
            plan["skip"] = True
            plan["skipReason"] = "wallet sem entityId"
        elif not beehus_name:
            plan["skip"] = True
            plan["skipReason"] = "security sem beehusName"
        plans.append(plan)
    plans.sort(key=lambda p: (p["date"], p["walletId"], p["securityBeehusName"]))
    return plans


def _intraday_validate(body):
    """Validate and normalize the intraday request payload. Returns
    `(company_id, initial_date, final_date, grouping_ids, wallet_ids)`."""
    company_id   = (body.get("companyId") or "").strip()
    initial_date = (body.get("initialDate") or "").strip()
    final_date   = (body.get("finalDate") or initial_date).strip()
    grouping_ids = list(body.get("groupingIds") or [])
    wallet_ids   = list(body.get("walletIds") or [])

    if not company_id or not company_visible(company_id):
        raise ValueError("forbidden")
    _safe(company_id)
    _safe_date(initial_date)
    _safe_date(final_date)
    if initial_date > final_date:
        raise ValueError("initialDate > finalDate")

    for v in grouping_ids:
        if not isinstance(v, str) or not v:
            raise ValueError("groupingIds must be non-empty strings")
    for v in wallet_ids:
        if not isinstance(v, str) or not v:
            raise ValueError("walletIds must be non-empty strings")

    return company_id, initial_date, final_date, grouping_ids, wallet_ids


def _intraday_resolve_names(groups, patched):
    """Resolve wallet + security display names for everything the response
    will reference. Wallet names come from `db.wallets`; security names
    come from the cached `get_security_names` map."""
    wid_set = {g["walletId"] for g in groups}
    wid_set |= {p["walletId"] for p in patched}
    sid_set = {g["securityId"] for g in groups}

    wallet_names = {}
    if wid_set:
        for wid in wid_set:
            swid = beehus_catalog.id_str(wid)
            wallet_names[swid] = (beehus_catalog.wallet_doc(swid) or {}).get("name") or swid

    sec_names = {}
    if sid_set:
        all_names = get_security_names()
        for sid in sid_set:
            sec_names[sid] = all_names.get(sid, sid)

    return wallet_names, sec_names


def _intraday_filter_groups_by_keys(groups, selected_keys):
    """Return the subset of `groups` whose `(walletId, securityId, date)` is
    listed in `selected_keys` (an iterable of `[wid, sid, date]` triples,
    typically coming from the JSON body). When `selected_keys` is `None`
    the input is returned unchanged — letting endpoints accept "no
    selection given" as "use everything detected"."""
    if selected_keys is None:
        return list(groups)
    wanted = {(str(k.get("walletId") or ""),
               str(k.get("securityId") or ""),
               str(k.get("date") or ""))
              for k in selected_keys
              if isinstance(k, dict)}
    return [g for g in groups
            if (g["walletId"], g["securityId"], g["date"]) in wanted]


def _intraday_filter_patches_by_keys(patched, selected_keys):
    """Filter `patched[]` by `(walletId, date)` selection. Same conventions
    as `_intraday_filter_groups_by_keys`."""
    if selected_keys is None:
        return list(patched)
    wanted = {(str(k.get("walletId") or ""), str(k.get("date") or ""))
              for k in selected_keys
              if isinstance(k, dict)}
    return [p for p in patched
            if (p["walletId"], p["date"]) in wanted]


@bp.route("/api/excecoes/intraday/check", methods=["POST"])
def intraday_check():
    """Step 1 — detect day-trade groups. Returns *only* the detected
    groups; patched positions are computed in a follow-up call once the
    operator picks which groups to act on. Splitting the steps lets the UI
    show a checkbox list and a separate "Gerar posições patcheadas" step."""
    body = request.get_json() or {}
    try:
        company_id, ini, fin, gids, wids = _intraday_validate(body)
    except ValueError as e:
        msg = str(e)
        return jsonify({"error": msg}), 403 if msg == "forbidden" else 400

    candidate = _intraday_resolve_wallets(
        company_id=company_id, grouping_ids=gids, wallet_ids=wids,
    )
    groups = []
    if candidate:
        groups = _intraday_detect(
            company_id=company_id, initial_date=ini, final_date=fin,
            candidate_wallets=candidate,
        )

    wallet_names, sec_names = _intraday_resolve_names(groups, [])
    return jsonify({
        "companyId":            company_id,
        "initialDate":          ini,
        "finalDate":            fin,
        "candidateWalletCount": len(candidate),
        "groups":               groups,
        "walletNames":          wallet_names,
        "securityNames":        sec_names,
    })


@bp.route("/api/excecoes/intraday/build-patches", methods=["POST"])
def intraday_build_patches():
    """Step 2 — given the same filter envelope plus the operator's
    `selectedGroups` (list of `{walletId, securityId, date}`), recompute
    detection, restrict to the selected groups, and build the patched
    positions. The detection is rerun server-side so the client can't
    inject groups the filter wouldn't have produced (defense in depth)."""
    body = request.get_json() or {}
    try:
        company_id, ini, fin, gids, wids = _intraday_validate(body)
    except ValueError as e:
        msg = str(e)
        return jsonify({"error": msg}), 403 if msg == "forbidden" else 400

    selected_groups = body.get("selectedGroups")
    if not isinstance(selected_groups, list) or not selected_groups:
        return jsonify({"error": "selectedGroups required (non-empty list)"}), 400

    candidate = _intraday_resolve_wallets(
        company_id=company_id, grouping_ids=gids, wallet_ids=wids,
    )
    if not candidate:
        return jsonify({"groups": [], "patched": [],
                        "walletNames": {}, "securityNames": {}})

    detected = _intraday_detect(
        company_id=company_id, initial_date=ini, final_date=fin,
        candidate_wallets=candidate,
    )
    groups = _intraday_filter_groups_by_keys(detected, selected_groups)
    patched = _intraday_build_patched_positions(
        company_id=company_id, groups=groups,
    )
    # Plan the securityContributionAdjustment transactions so the UI can
    # show the operator exactly what will be created on apply.
    wallet_meta = _resolve_wallet_meta({g["walletId"] for g in groups})
    sec_name_index = get_security_names()
    transactions = _intraday_plan_transactions(
        groups=groups,
        wallet_meta=wallet_meta,
        sec_name_index=sec_name_index,
    )
    wallet_names, sec_names = _intraday_resolve_names(groups, patched)
    return jsonify({
        "companyId":     company_id,
        "groups":        groups,
        "patched":       patched,
        "transactions":  transactions,
        "walletNames":   wallet_names,
        "securityNames": sec_names,
    })


def _intraday_build_xlsx_multi(patched):
    """Variant of `_build_xlsx` that writes one workbook with rows from
    multiple `(wallet, date)` pairs — `Data` becomes per-row instead of a
    single header value. Used by the inspection download (one file with
    every patched position concatenated)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(["Data", "Carteira", "Ativo", "Quant", "PU",
               "SaldoBruto", "Caixa", "Moeda"])
    for p in patched:
        for r in p["rows"]:
            ws.append([
                p["date"],
                r.get("walletId") or "",
                r.get("unprocessedId") or "",
                r.get("quantity") or 0,
                r.get("pu") or 0,
                r.get("balance") or 0,
                "Sim" if r.get("caixa") else "Não",
                r.get("currencyId") or "",
            ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


@bp.route("/api/excecoes/intraday/excel", methods=["POST"])
def intraday_excel():
    """Download a single XLSX with the patched positions for the operator's
    current selection. If `selectedGroups` is present, the workbook covers
    only those groups; otherwise it falls back to every detected group
    (useful for a "snapshot of everything" download right after Verificar)."""
    body = request.get_json() or {}
    try:
        company_id, ini, fin, gids, wids = _intraday_validate(body)
    except ValueError as e:
        msg = str(e)
        return jsonify({"error": msg}), 403 if msg == "forbidden" else 400

    candidate = _intraday_resolve_wallets(
        company_id=company_id, grouping_ids=gids, wallet_ids=wids,
    )
    if not candidate:
        return jsonify({"error": "no candidate wallets"}), 422
    detected = _intraday_detect(
        company_id=company_id, initial_date=ini, final_date=fin,
        candidate_wallets=candidate,
    )
    selected_groups = body.get("selectedGroups")
    groups = _intraday_filter_groups_by_keys(detected, selected_groups)
    patched = _intraday_build_patched_positions(
        company_id=company_id, groups=groups,
    )
    if not patched:
        return jsonify({"error": "no patched positions for the current selection"}), 422

    buf = io.BytesIO(_intraday_build_xlsx_multi(patched))
    return send_file(
        buf,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        as_attachment=True,
        download_name=f"intraday_{company_id}_{ini}_{fin}.xlsx",
    )


@bp.route("/api/excecoes/intraday/apply", methods=["POST"])
def intraday_apply():
    """Step 3 — upload one patched-position XLSX per selected
    `(walletId, date)` via the same upstream endpoint used by the
    position-strip flow
    (`POST /beehus/financial/positions/unprocessed-security-positions/file`).

    Body shape:
      `{filter envelope, selectedGroups[], selectedPatches[]}`

    `selectedGroups` controls which day-trade groups are considered when
    rebuilding patched positions; `selectedPatches` (`[{walletId, date}]`)
    further restricts which of those built positions are actually uploaded.
    Both selections are required so the operator's intent travels with the
    request — the server never trusts cached client state.

    Skips pairs where the day-traded unprocessedId rows are not present in
    the position doc (zeroing would be a no-op write that risks clobbering
    a concurrent edit). Returns per-pair status; on at least one upstream
    failure, the response is 502 and successful pairs are still reported.
    """
    body = request.get_json() or {}
    try:
        company_id, ini, fin, gids, wids = _intraday_validate(body)
    except ValueError as e:
        msg = str(e)
        return jsonify({"error": msg}), 403 if msg == "forbidden" else 400

    selected_groups  = body.get("selectedGroups")
    selected_patches = body.get("selectedPatches")
    if not isinstance(selected_groups, list) or not selected_groups:
        return jsonify({"error": "selectedGroups required (non-empty list)"}), 400
    if not isinstance(selected_patches, list) or not selected_patches:
        return jsonify({"error": "selectedPatches required (non-empty list)"}), 400

    candidate = _intraday_resolve_wallets(
        company_id=company_id, grouping_ids=gids, wallet_ids=wids,
    )
    if not candidate:
        return jsonify({"error": "no candidate wallets"}), 422
    detected = _intraday_detect(
        company_id=company_id, initial_date=ini, final_date=fin,
        candidate_wallets=candidate,
    )
    groups = _intraday_filter_groups_by_keys(detected, selected_groups)
    patched_all = _intraday_build_patched_positions(
        company_id=company_id, groups=groups,
    )
    patched = _intraday_filter_patches_by_keys(patched_all, selected_patches)
    if not patched:
        return jsonify({"error": "no patched positions match selectedPatches"}), 422

    # Apply uploads patched positions only. The day-trade transactions
    # are submitted one-by-one via `/api/excecoes/intraday/transactions/submit`
    # — same per-row pattern used by Correções > Transações > Enviar
    # selecionadas via API. The front-end fans out the txn submits before
    # invoking this endpoint, which keeps the position upload bulk-per-
    # (walletId, date) (the upstream replaces the entire wallet position
    # in one call, so per-row HTTP would gain nothing here).
    auth_error = None
    results = []
    for p in patched:
        if not p["rows"]:
            results.append({"walletId": p["walletId"], "date": p["date"],
                            "status": "skipped", "reason": "empty position"})
            continue
        if not p["zeroedUnprocessedIds"]:
            results.append({"walletId": p["walletId"], "date": p["date"],
                            "status": "skipped",
                            "reason": "day-trade rows absent from position"})
            continue
        xlsx = _build_xlsx(p["rows"], p["date"])
        try:
            up = upload_unprocessed_security_positions_file(
                company_id=company_id,
                file_bytes=xlsx,
                filename=f"intraday_{p['walletId']}_{p['date']}.xlsx",
            )
            results.append({"walletId": p["walletId"], "date": p["date"],
                            "status":  "ok",
                            "rows":    len(p["rows"]),
                            "zeroed":  len(p["zeroedUnprocessedIds"]),
                            "upstream": up})
        except BeehusAuthError as e:
            auth_error = {"error": str(e), "status": getattr(e, "status", None)}
            results.append({"walletId": p["walletId"], "date": p["date"],
                            "status": "auth_error", "error": str(e)})
            break
        except BeehusAPIError as e:
            results.append({"walletId": p["walletId"], "date": p["date"],
                            "status": "error", "error": str(e),
                            "upstream_status": getattr(e, "status", None),
                            "upstream_body": getattr(e, "body", None)})

    failed_pos = [r for r in results if r["status"] not in ("ok", "skipped")]
    response = {"results":      results,
                "groupCount":   len(groups),
                "patchedCount": len(patched)}
    if auth_error:
        return jsonify({**response, **auth_error}), 401
    if failed_pos:
        return jsonify(response), 502
    return jsonify(response)


@bp.route("/api/excecoes/intraday/transactions/submit", methods=["POST"])
def intraday_transactions_submit():
    """Submit one `securityContributionAdjustment` transaction for a single
    detected day-trade group.

    Mirrors the contract of `Correções > Transações > Enviar selecionadas
    via API` (`/api/correcoes/transactions/submit`): one HTTP call per row,
    so the operator gets per-row progress and a 401 short-circuits the
    whole batch on the client side. Same upstream call too —
    `beehus_api.create_transaction` → `POST /beehus/financial/transactions`.

    Body shape:
        {filter envelope, group: {walletId, securityId, date}}

    The server re-runs detection from the filter envelope and refuses to
    create a transaction for a group that doesn't survive the detection
    — defense in depth, identical to how `/apply` rebuilds the patches
    server-side rather than trusting the client's snapshot.

    Response (success): `{ok: true, status: "ok", createdId, upstream,
    walletId, securityId, date, balance, description, securityBeehusName,
    entityId, currencyId}`. Errors map to:

      - 400 — bad body / unknown group / wallet has no entityId / security
              has no beehusName (matching the `skipReason` from the
              build-patches preview)
      - 401 — upstream auth error (same shape as the bulk apply)
      - 502 — upstream non-auth error (`upstream_status`, `upstream_body`)
    """
    body = request.get_json() or {}
    try:
        company_id, ini, fin, gids, wids = _intraday_validate(body)
    except ValueError as e:
        msg = str(e)
        return jsonify({"error": msg}), 403 if msg == "forbidden" else 400

    group_key = body.get("group")
    if not isinstance(group_key, dict):
        return jsonify({"error": "group required ({walletId, securityId, date})"}), 400
    wid_in  = str(group_key.get("walletId")   or "")
    sid_in  = str(group_key.get("securityId") or "")
    date_in = str(group_key.get("date")       or "")
    if not (wid_in and sid_in and date_in):
        return jsonify({"error": "group requires walletId, securityId, date"}), 400

    candidate = _intraday_resolve_wallets(
        company_id=company_id, grouping_ids=gids, wallet_ids=wids,
    )
    if not candidate:
        return jsonify({"error": "no candidate wallets"}), 422
    detected = _intraday_detect(
        company_id=company_id, initial_date=ini, final_date=fin,
        candidate_wallets=candidate,
    )
    target = next(
        (g for g in detected
         if g["walletId"] == wid_in
            and g["securityId"] == sid_in
            and g["date"] == date_in),
        None,
    )
    if not target:
        return jsonify({"error": "group not present in current detection"}), 404

    wallet_meta    = _resolve_wallet_meta({wid_in})
    sec_name_index = get_security_names()
    plan = _intraday_plan_transactions(
        groups=[target],
        wallet_meta=wallet_meta,
        sec_name_index=sec_name_index,
    )[0]
    out_plan = {k: plan[k] for k in (
        "walletId", "securityId", "date",
        "balance", "currencyId", "entityId",
        "beehusTransactionType", "description",
        "operationDate", "liquidationDate",
        "securityBeehusName",
    )}
    if plan["skip"]:
        # 400 mirrors the front-end expectation: the row is unsendable for
        # a known reason (skipReason) — not a transient upstream failure.
        return jsonify({**out_plan,
                        "ok":     False,
                        "status": "skipped",
                        "error":  plan["skipReason"]}), 400

    try:
        result = create_transaction(
            company_id=company_id,
            entity_id=plan["entityId"],
            wallet_id=plan["walletId"],
            balance=plan["balance"],
            operation_date=plan["operationDate"],
            liquidation_date=plan["liquidationDate"],
            currency_id=plan["currencyId"],
            transaction_type=plan["beehusTransactionType"],
            description=plan["description"],
            security_id=plan["securityId"],
        )
    except BeehusAuthError as e:
        return jsonify({**out_plan, "ok": False, "status": "auth_error",
                        "error": str(e),
                        "upstream_status": getattr(e, "status", None),
                        "upstream_body":   getattr(e, "body", None)}), 401
    except BeehusAPIError as e:
        return jsonify({**out_plan, "ok": False, "status": "error",
                        "error": str(e),
                        "upstream_status": getattr(e, "status", None),
                        "upstream_body":   getattr(e, "body", None)}), 502

    return jsonify({**out_plan,
                    "ok":         True,
                    "status":     "ok",
                    "createdId":  (result or {}).get("_id") or (result or {}).get("id"),
                    "upstream":   result})
