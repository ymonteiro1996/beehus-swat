"""Repetir Posições — replicate a wallet's most recent processedPosition
into the next business day's `unprocessedSecurityPositions`, applying a
configurable rule chain (buySell quantity, maturity zero-out, price
policy). Full specification: `docs/REPETIR_POSICOES.md`.

Storage:
    data/repeat_positions_config.json — saved presets drive the daily
    routine. The operator builds named lists in the Seleção de Wallets
    tab; the Rotina diária tab shows one checkbox per saved list and
    runs over the **union** of walletIds across the checked lists.
        {
          "wallets": [...],                       # legacy, no longer read
          "priceRepeatSecurities": [{"companyId", "securityId", "addedAt"}, ...],
          "walletLists": [{"name", "walletIds": [...], "addedAt"}, ...]
        }

    `walletLists` is the **single source of truth** for which wallets
    participate in the routine — there's no longer a separate flagged
    set. The legacy `wallets[]` field is left in the file for audit but
    auto-migrated on first read (see `_load_config`).

Endpoints (see spec for the full table):
    GET  /repetir-posicoes                              — render page
    GET  /api/repetir-posicoes/filters/companies        — companies dropdown
    GET  /api/repetir-posicoes/filters/wallets          — wallets for picker
    GET  /api/repetir-posicoes/wallet-lists             — saved presets
                                                          (with enriched wallet
                                                          metadata)
    POST /api/repetir-posicoes/wallet-lists             — upsert a named preset
    DELETE /api/repetir-posicoes/wallet-lists           — delete a named preset
    GET  /api/repetir-posicoes/daily?lists=…            — wallet roster for the
                                                          checked lists + last
                                                          processedPosition date
    GET  /api/repetir-posicoes/results-wallets          — wallets of a company
                                                          with a NAV result on a
                                                          date (/results), no
                                                          divergence filter
    POST /api/repetir-posicoes/preview                  — apply rule chain,
                                                          return side-by-side
                                                          (original × repetição)
    POST /api/repetir-posicoes/b1-prices                — fetch B1 PUs from
                                                          securityPrices.historyPrice
                                                          (type=B1, exact date)
    POST /api/repetir-posicoes/apply                    — build .xlsx per wallet
                                                          and POST upstream
"""
from __future__ import annotations

import io
import json
import os
import re
from datetime import date, datetime, timedelta

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, jsonify, render_template, request
from openpyxl import Workbook

from db import (
    atomic_write_json,
    company_visible,
    get_company_filter,
    get_company_names,
    get_security_names,
    get_wallet_names,
    resolve_wallet,
    sum_cash_by_dates,
)
from beehus_api import (
    upload_unprocessed_security_positions_file,
    BeehusAPIError,
    BeehusAuthError,
)
import beehus_catalog
from pages.precificacao import calculate_curva, _load_lists

bp = Blueprint("repetir_posicoes", __name__)

_CONFIG_FILE = os.path.join(
    os.path.dirname(__file__), "..", "data", "repeat_positions_config.json"
)
_CONFIG_FILE = os.path.abspath(_CONFIG_FILE)

# Per-run diff log reports written by /apply. One JSON file per apply
# call (regardless of how many wallets/companies were in it) under
# `data/repeat_positions_logs/<run_id>.json`. The directory is created
# lazily by `_persist_diff_log` so a fresh checkout doesn't need any
# bootstrap step.
_LOGS_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "repeat_positions_logs"
))

# Per-operator (file-scoped) configuration of which preview-table columns
# are hidden. Persists across sessions so the operator doesn't have to
# rehide UnprocessedId / Transação / Flags every time. The file is
# created lazily on first save; a missing file means "use the built-in
# default set" (`_DEFAULT_HIDDEN_COLUMNS`).
_COLUMNS_FILE = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "posicao_projetada_columns.json"
))
# UnprocessedId is hidden by default per operator request — it's
# informational (audit of the upload mapping) and clutters the visible
# row. Operators can re-show it via the "Colunas" toggle.
_DEFAULT_HIDDEN_COLUMNS = ["unprocessedId", "securityId"]
# Run-id pattern accepted by the GET-log endpoint (kept tight to block
# path traversal — the value flows from the URL straight into a
# filesystem join).
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_\-]{1,80}$")

_SAFE_ID_RE   = re.compile(r"^[A-Za-z0-9_.\-]+$")
_SAFE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Helpers ──────────────────────────────────────────────────────────────────

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


def _round(v, digits):
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return 0


def _next_biz_day(d_iso):
    """Return ISO of the first Mon-Fri strictly after `d_iso`."""
    try:
        d = date.fromisoformat(d_iso)
    except ValueError:
        return d_iso
    cur = d + timedelta(days=1)
    while cur.weekday() >= 5:
        cur += timedelta(days=1)
    return cur.isoformat()


def _add_biz_days(d_iso, n):
    """Avança `n` dias úteis (Mon-Fri) a partir de `d_iso`. `n == 0`
    devolve a mesma data; `n < 0` é normalizado para 0. Não considera
    feriados (mesma simplificação adotada por `_next_biz_day`)."""
    try:
        d = date.fromisoformat(d_iso)
    except ValueError:
        return d_iso
    try:
        n = int(n or 0)
    except (TypeError, ValueError):
        n = 0
    if n <= 0:
        return d_iso
    added = 0
    cur = d
    while added < n:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            added += 1
    return cur.isoformat()


# ── Config persistence ──────────────────────────────────────────────────────

def _load_config():
    """Read the config JSON. Missing file → empty skeleton (idempotent).

    Side-effect migration: if the legacy `wallets[]` field has entries
    and no `walletLists[]` exists yet, convert the flagged set into a
    single "Lista padrão" preset and write the file back. This keeps
    pre-refactor configs working (the daily routine now reads only from
    saved lists — `wallets[]` is no longer authoritative). After the
    one-time migration `wallets[]` is left intact as an audit trail but
    never read again.

    OneDrive transiently locks files during sync, which races with
    `atomic_write_json`'s `os.replace`. A bare `OSError` here would silently
    make every preview/apply/daily call see "nothing flagged" — so retry
    once after a short pause before falling back to the empty skeleton.
    """
    if not os.path.isfile(_CONFIG_FILE):
        return {"wallets": [], "priceRepeatSecurities": [], "walletLists": []}
    last_err = None
    for attempt in range(2):
        try:
            with open(_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            data.setdefault("wallets", [])
            data.setdefault("priceRepeatSecurities", [])
            data.setdefault("walletLists", [])
            # One-time migration from the pre-refactor model (flat
            # flagged set) to the named-presets model. The migration
            # only fires when there's data to migrate AND no presets
            # already exist, so it never clobbers operator changes.
            if not data["walletLists"]:
                legacy_ids = [
                    w.get("walletId")
                    for w in (data.get("wallets") or [])
                    if w.get("walletId")
                ]
                if legacy_ids:
                    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
                    data["walletLists"] = [{
                        "name":      "Lista padrão",
                        "walletIds": legacy_ids,
                        "addedAt":   now,
                    }]
                    try:
                        atomic_write_json(_CONFIG_FILE, data)
                    except OSError:
                        # Non-fatal: the in-memory config has the
                        # migration applied; the disk catches up on the
                        # next successful write.
                        pass
            return data
        except (OSError, json.JSONDecodeError) as e:
            last_err = e
            if attempt == 0:
                import time
                time.sleep(0.05)
    # Two consecutive failures — return the skeleton but log so the operator
    # has a breadcrumb if the file is genuinely corrupt (vs. a sync blip).
    import logging
    logging.getLogger(__name__).warning(
        "repetir _load_config falling back to empty skeleton: %s", last_err
    )
    return {"wallets": [], "priceRepeatSecurities": [], "walletLists": []}


def _save_config(cfg):
    atomic_write_json(_CONFIG_FILE, cfg)


def _load_columns_config():
    """Return the hidden-columns config for the preview table.

    Missing file → built-in defaults (`_DEFAULT_HIDDEN_COLUMNS`). The
    JSON file shape is `{hiddenColumns: ["unprocessedId", ...]}` — list
    of column keys matching the `data-col` attribute on each `<th>`/`<td>`
    in the preview table (see `_renderPreview` in
    `templates/repetir_posicoes.html`)."""
    if not os.path.isfile(_COLUMNS_FILE):
        return {"hiddenColumns": list(_DEFAULT_HIDDEN_COLUMNS)}
    try:
        with open(_COLUMNS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
    except (OSError, json.JSONDecodeError):
        return {"hiddenColumns": list(_DEFAULT_HIDDEN_COLUMNS)}
    hidden = data.get("hiddenColumns")
    if not isinstance(hidden, list):
        hidden = list(_DEFAULT_HIDDEN_COLUMNS)
    # Coerce to strings + dedupe, preserve order so the UI rendering is
    # stable across reads.
    seen = set()
    clean = []
    for c in hidden:
        s = str(c).strip()
        if s and s not in seen:
            seen.add(s)
            clean.append(s)
    return {"hiddenColumns": clean}


def _save_columns_config(cfg):
    atomic_write_json(_COLUMNS_FILE, cfg)


@bp.route("/api/repetir-posicoes/columns")
def columns_get():
    """Return `{hiddenColumns: [...]}` — the list of preview-table
    column keys the operator has chosen to hide. Defaults are baked
    into `_DEFAULT_HIDDEN_COLUMNS` when no config file exists."""
    return jsonify(_load_columns_config())


@bp.route("/api/repetir-posicoes/columns", methods=["POST"])
def columns_set():
    """Persist the hidden-columns list. Body: `{hiddenColumns: [...]}`.
    Unknown keys flow through unchanged — the UI is the authority on
    what column keys exist, and the backend only mirrors them."""
    body = request.get_json(silent=True) or {}
    hidden = body.get("hiddenColumns")
    if not isinstance(hidden, list):
        return jsonify({"error": "hiddenColumns must be a list"}), 400
    clean = []
    seen = set()
    for c in hidden:
        if not isinstance(c, str):
            continue
        s = c.strip()
        if not s:
            continue
        # Light input gate — column keys are short identifiers chosen
        # by the UI, so reject anything funky.
        if not re.match(r"^[A-Za-z0-9_\-]{1,40}$", s):
            continue
        if s in seen:
            continue
        seen.add(s)
        clean.append(s)
    cfg = {"hiddenColumns": clean}
    try:
        _save_columns_config(cfg)
    except OSError as e:
        return jsonify({"error": f"falha ao salvar: {e}"}), 500
    return jsonify({"ok": True, **cfg})


def _persist_diff_log(report):
    """Write a per-run diff report to disk and return the run id.

    Used by `/apply` to leave an audit trail of every repetir run —
    operators can later pull `data/repeat_positions_logs/<run_id>.json`
    via `/api/repetir-posicoes/logs/<run_id>` to inspect what diverged
    when the upload was filed (especially useful on the "Executar sem
    prévia" path, where there's no on-screen prévia for retrospective
    review).

    The file is best-effort: failures only get logged, never raise — a
    disk hiccup shouldn't gate the upload's success path."""
    run_id = report.get("runId")
    if not run_id:
        return None
    try:
        os.makedirs(_LOGS_DIR, exist_ok=True)
        path = os.path.join(_LOGS_DIR, f"{run_id}.json")
        atomic_write_json(path, report)
        return run_id
    except OSError as e:
        import logging
        logging.getLogger(__name__).warning(
            "repetir _persist_diff_log failed for %s: %s", run_id, e
        )
        return None


def _build_diff_report(preview_data, *, wallet_id, company_id, company_name,
                       source_date, target_date, accepted_set):
    """Distil the wallet-level diff information already produced by
    `_build_preview_for_wallet` into a flat per-wallet entry for the log
    report. Only rows that were actually uploaded (filtered through
    `accepted_set`, or every row when `accepted_set is None`) are
    included in `differences`, so o log espelha o que o upstream recebeu.

    Estrutura do payload (operadora pediu três blocos resumidos no
    topo, antes da listagem detalhada):

      cashStatus          — "ok" quando |new−target| < R$ 0,01,
                            "diverge" caso contrário ou "noTarget"
                            quando o `cashAccounts(targetDate)` é vazio.
      cashDetail          — {former, new, target, targetDelta}.
      differencesCount    — quantidade de linhas em `differences`
                            (diverged ∪ missingInTarget) — coincide
                            com o que aparece amarelo na prévia.
      orphanCount         — quantidade de transações órfãs detectadas
                            por `_find_orphan_transactions` (tipos que
                            exigem ativo sem `amountDifference` nem
                            provisão correspondente, considerando o
                            offset settlement−NAV).
      orphanTransactions  — lista detalhada (mesma do payload da prévia).

    Régua de "diverged" alinhada ao restante do código (ver
    `_build_preview_for_wallet`): **impacto em $ ≥ R$ 0,01** em qualquer
    componente (qty × pu_ref, pu × qty_ref, |bal|). Drift puro de
    quantidade que não move dinheiro deixa de entrar como divergência.
    """
    target_summary = dict(preview_data.get("targetSummary") or {})
    differences = []

    _CENT = 0.01

    for row in preview_data.get("rows") or []:
        sid = row.get("securityId")
        if accepted_set is not None and sid not in accepted_set:
            continue
        diff = row.get("diff")
        tp   = row.get("targetProcessed")
        rep  = row.get("repetition") or {}
        if diff is None:
            # Sem entrada no target. Só conta como missingInTarget
            # quando a posição replicada tem saldo material — caso
            # contrário, é uma linha residual e não vira ruído no log.
            try:
                rep_bal_abs = abs(float(rep.get("balance") or 0))
            except (TypeError, ValueError):
                rep_bal_abs = 0.0
            if rep_bal_abs < _CENT:
                continue
            differences.append({
                "kind":         "missingInTarget",
                "securityId":   sid,
                "securityName": row.get("securityName"),
                "unprocessedId": row.get("unprocessedId") or "",
                "repetition":   rep,
                "targetProcessed": None,
                "diff":         None,
                "flags":        row.get("flags") or [],
            })
            continue
        # diff presente → ou bate (skip) ou diverge. Usa os impacts em $
        # já calculados em `_build_preview_for_wallet`; cai num fallback
        # local se vier o diff em formato antigo (sem impactos).
        try:
            qi = abs(float(diff.get("qtyImpact")     or 0))
            pi = abs(float(diff.get("puImpact")      or 0))
            bi = abs(float(diff.get("balanceImpact") or 0))
        except (TypeError, ValueError):
            qi = pi = bi = 0.0
        if qi == 0 and pi == 0 and bi == 0:
            # Payload sem impactos pré-calculados — fallback no
            # |bal_d|, fonte mais direta de impacto financeiro.
            try:
                bi = abs(float(diff.get("balance") or 0))
            except (TypeError, ValueError):
                bi = 0.0
        if qi < _CENT and pi < _CENT and bi < _CENT:
            continue
        differences.append({
            "kind":         "diverged",
            "securityId":   sid,
            "securityName": row.get("securityName"),
            "unprocessedId": row.get("unprocessedId") or "",
            "repetition":   rep,
            "targetProcessed": tp,
            "diff":         diff,
            "flags":        row.get("flags") or [],
        })

    # Caixa: status binário (ok/diverge/noTarget) + os números brutos.
    cash = dict(preview_data.get("cash") or {})
    td_raw = cash.get("targetDelta")
    if cash.get("target") is None:
        cash_status = "noTarget"
    else:
        try:
            cash_status = "ok" if abs(float(td_raw or 0)) < _CENT else "diverge"
        except (TypeError, ValueError):
            cash_status = "diverge"

    orphans = preview_data.get("orphanTransactions") or []

    return {
        "walletId":    wallet_id,
        "walletName":  preview_data.get("walletName") or wallet_id,
        "companyId":   company_id,
        "companyName": company_name,
        "sourceDate":  source_date,
        "targetDate":  target_date,
        "currencyId":  preview_data.get("currencyId"),
        # Resumo estruturado no topo — pedido do operador. Mantém os
        # blocos `cash`, `summary`, `differences` abaixo intactos para
        # quem já consome a estrutura detalhada.
        "cashStatus":         cash_status,
        "cashDetail":         {
            "former":      cash.get("former"),
            "new":         cash.get("new"),
            "target":      cash.get("target"),
            "targetDelta": cash.get("targetDelta"),
        },
        "differencesCount":   len(differences),
        "orphanCount":        len(orphans),
        "orphanTransactions": orphans,
        "cash":        cash,
        "provisions":  preview_data.get("provisions"),
        "summary":     target_summary,
        "differences": differences,
    }


def _wallets_for_lists(cfg, list_names=None):
    """Return the union of walletIds from the named presets (or from all
    presets when `list_names` is None/empty).

    Used by both `/daily` (which only reads the lists the operator
    checked) and `_validate_items` (which checks against every saved
    list so a wallet in *any* preset can be previewed/applied). The
    union dedupes — a wallet in two lists shows up once."""
    lists = cfg.get("walletLists") or []
    if list_names:
        wanted = {str(n) for n in list_names if n}
        lists = [l for l in lists if str(l.get("name") or "") in wanted]
    out = set()
    for entry in lists:
        for wid in (entry.get("walletIds") or []):
            if wid:
                out.add(wid)
    return out


def _all_enabled_wallets():
    """Set of every walletId that belongs to at least one saved preset.
    The daily routine now reads from named lists rather than a flat
    flagged set, so "enabled" means "appears in some saved list" — used
    as the security gate by `_validate_items`."""
    return _wallets_for_lists(_load_config())


def _wallet_company_map(wallet_ids):
    """Resolve `{walletId: companyId}` for the given ids by reading
    `db.wallets`. Source of truth — the config's stored `companyId` is only
    a hint (a wallet could have been re-assigned upstream after being
    added to the list)."""
    ids = [str(w) for w in (wallet_ids or [])]
    if not ids:
        return {}
    return {
        wid: str(cid or "")
        for wid, cid in beehus_catalog.wallet_company_map(ids).items()
    }


# ── Page route ───────────────────────────────────────────────────────────────

@bp.route("/repetir-posicoes")
def index():
    return render_template("repetir_posicoes.html")


# ── Filters: companies / wallets ─────────────────────────────────────────────

@bp.route("/api/repetir-posicoes/filters/companies")
def list_companies():
    cf = get_company_filter()
    out = []
    for cid, name in get_company_names().items():
        if cf and cid not in cf:
            continue
        out.append({"id": cid, "name": name or cid})
    out.sort(key=lambda c: (c["name"] or "").lower())
    return jsonify(out)


@bp.route("/api/repetir-posicoes/filters/wallets")
def list_wallets():
    """Wallets for a company (browsing helper for the config tab), each
    marked `enabled` if it's in the **global** flagged list. The company
    filter is just for ergonomics — the saved list is not scoped by
    company."""
    company_id = (request.args.get("companyId") or "").strip()
    if not company_visible(company_id):
        return jsonify([])
    enabled = _all_enabled_wallets()
    wallet_names = get_wallet_names()
    items = []
    for wid, nm in beehus_catalog.wallets_for_company(company_id).items():
        items.append({
            "id":      wid,
            "name":    nm or wallet_names.get(wid, wid),
            "enabled": wid in enabled,
        })
    items.sort(key=lambda x: (x["name"] or "").lower())
    return jsonify(items)


# ── Saved wallet lists (named presets) ──────────────────────────────────────
# The named-presets list IS the daily routine's source of truth. The
# operator builds and names lists in the Seleção de Wallets tab; the
# Rotina diária tab shows checkboxes (one per saved list) and runs the
# routine over the union of walletIds across the checked lists.

@bp.route("/api/repetir-posicoes/wallet-lists")
def list_wallet_lists():
    """Return saved presets with enriched wallet metadata so the UI can
    render wallet/company names without follow-up lookups.

    Shape:
        {
          "lists": [
            {
              "name", "addedAt",
              "walletIds": ["<id>", ...],          # raw ids (preserves order)
              "wallets":   [{"walletId", "walletName",
                             "companyId", "companyName"}, ...]
            },
            ...
          ]
        }

    Wallets whose `db.wallets` document has vanished still surface — the
    UI shouldn't lose visibility of an orphaned id sitting in a saved
    preset. The operator's `company_filter` is honoured here only for
    the *enriched* view (i.e. `wallets[]`): the raw `walletIds[]` array
    is left intact so editing a preset doesn't silently lose ids the
    operator can't currently see."""
    cfg            = _load_config()
    wallet_names   = get_wallet_names()
    company_names  = get_company_names()
    cf             = get_company_filter()

    # Single batch lookup across every walletId in every saved preset —
    # avoids N find() calls when a preset has many wallets.
    all_ids = set()
    for entry in cfg.get("walletLists") or []:
        for wid in (entry.get("walletIds") or []):
            if wid:
                all_ids.add(wid)
    wallet_to_co = _wallet_company_map(list(all_ids))

    out = []
    for entry in cfg.get("walletLists") or []:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        raw_ids = [w for w in (entry.get("walletIds") or []) if w]
        wallets = []
        for wid in raw_ids:
            cid = wallet_to_co.get(wid) or ""
            if cf and cid and cid not in cf:
                continue
            wallets.append({
                "walletId":    wid,
                "walletName":  wallet_names.get(wid) or wid,
                "companyId":   cid,
                "companyName": company_names.get(cid, cid) if cid else "",
            })
        wallets.sort(key=lambda w: ((w["companyName"] or "").lower(),
                                    (w["walletName"]  or "").lower()))
        out.append({
            "name":      name,
            "addedAt":   entry.get("addedAt"),
            "walletIds": raw_ids,
            "wallets":   wallets,
        })
    out.sort(key=lambda l: (l["name"] or "").lower())
    return jsonify({"lists": out})


@bp.route("/api/repetir-posicoes/wallet-lists", methods=["POST"])
def save_wallet_list():
    """Upsert a named preset. Body: `{name, walletIds[]}`.

    Each walletId must exist in `db.wallets` and pass the operator's
    `company_filter`; invalid ids are silently dropped — the preset is
    the **clean** intersection of what the operator picked and what
    they can see. Saving an empty list is allowed (overwrites with
    zero ids)."""
    body = request.get_json(silent=True) or {}
    name = str(body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name é obrigatório"}), 400

    wallet_ids = list(body.get("walletIds") or [])
    try:
        for w in wallet_ids:
            _safe(str(w))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    cf = get_company_filter()
    visible = set()
    if wallet_ids:
        comp_map = beehus_catalog.wallet_company_map([str(w) for w in wallet_ids])
        for wid, cid in comp_map.items():
            cid = str(cid or "")
            if cf and cid and cid not in cf:
                continue
            visible.add(wid)
    clean_ids = [w for w in wallet_ids if w in visible]

    cfg = _load_config()
    lists = list(cfg.get("walletLists") or [])
    now = datetime.utcnow().isoformat(timespec="seconds") + "Z"
    for entry in lists:
        if str(entry.get("name") or "").strip() == name:
            entry["walletIds"] = clean_ids
            entry["addedAt"]   = now
            break
    else:
        lists.append({"name": name, "walletIds": clean_ids, "addedAt": now})
    cfg["walletLists"] = lists
    _save_config(cfg)
    return jsonify({"ok": True, "name": name, "count": len(clean_ids)})


@bp.route("/api/repetir-posicoes/wallet-lists", methods=["DELETE"])
def delete_wallet_list():
    """Delete a named preset (pass `?name=<list_name>`)."""
    name = (request.args.get("name") or "").strip()
    if not name:
        return jsonify({"error": "name é obrigatório"}), 400
    cfg = _load_config()
    lists = list(cfg.get("walletLists") or [])
    new_lists = [l for l in lists if str(l.get("name") or "").strip() != name]
    if len(new_lists) == len(lists):
        return jsonify({"error": "Lista não encontrada"}), 404
    cfg["walletLists"] = new_lists
    _save_config(cfg)
    return jsonify({"ok": True})


# ── Daily routine: listing ───────────────────────────────────────────────────

@bp.route("/api/repetir-posicoes/daily")
def daily_listing():
    """Return the daily routine's wallet roster — the union of walletIds
    across the saved presets the operator has checked. The daily routine
    is company-agnostic by design — the apply step splits the upstream
    upload per company internally (one .xlsx per `companyId`, because
    the multipart endpoint requires `companyId` in the form data).

    Query params:
      • `lists` (required for any rows) — comma-separated preset names
        the operator wants to include. Empty / missing → no rows (the
        UI should land with zero rows until the operator picks a list).
      • `date` (optional, YYYY-MM-DD) — the **Data-fonte**: the date to
        repeat the positions FROM. When given, each roster wallet is marked
        eligible (`lastDate` = this date, `suggestedTarget` = next business
        day) **iff** it has a processed position on that exact date, checked
        via the pre-processing endpoint (E) instead of a Mongo read. Omitted →
        rows come with empty `lastDate` and the operator fills the source date
        per wallet by hand.

    Presets the operator passes that don't exist in `walletLists` are
    silently ignored (no 404) — a stale UI shouldn't error out just
    because someone deleted a preset in another tab."""
    date_filter = (request.args.get("date") or "").strip() or None
    try:
        if date_filter:
            _safe_date(date_filter)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # `lists` comes in as a comma-separated string. Empty → no rows (we
    # don't fall back to "all lists" by default because that would mask
    # the operator forgetting to check anything on the standalone page).
    # The sentinel value `*` opts into "union of every saved list" —
    # used by the Painel de Controle's fast path, where the modal's
    # explicit semantics is "rodar tudo" rather than picking presets.
    raw_lists = (request.args.get("lists") or "").strip()
    list_names = [n.strip() for n in raw_lists.split(",") if n.strip()]
    if not list_names:
        return jsonify({"wallets": []})
    cfg = _load_config()
    if "*" in list_names:
        all_flagged = list(_wallets_for_lists(cfg))  # union of all presets
    else:
        all_flagged = list(_wallets_for_lists(cfg, list_names))
    if not all_flagged:
        return jsonify({"wallets": []})

    # Resolve company per wallet from db.wallets (live mapping). Drop
    # wallets that no longer exist, and apply the operator's company_filter.
    wallet_to_co  = _wallet_company_map(all_flagged)
    cf            = get_company_filter()
    visible = [
        wid for wid in all_flagged
        if wallet_to_co.get(wid) and (not cf or wallet_to_co[wid] in cf)
    ]
    if not visible:
        return jsonify({"wallets": []})

    wallet_names  = get_wallet_names()
    company_names = get_company_names()

    # Source date = the "Data-fonte" the operator informs (the date to repeat
    # FROM). We no longer discover each wallet's own latest processed date via
    # a Mongo `MAX(positionDate)` aggregate. Instead, for the chosen date we
    # ask the pre-processing endpoint (E) which of these wallets actually have
    # a processed position on that date — those are the rows with something to
    # repeat. Companies come from the visible wallets (the roster is
    # company-agnostic), so E is fanned out only over the companies present and
    # only when the operator has picked a date (no fan-out on a bare page load).
    source_date = date_filter  # `date` query param, already validated above
    processed_set = set()
    if source_date:
        companies = sorted({wallet_to_co[wid] for wid in visible if wallet_to_co.get(wid)})
        statuses = beehus_catalog.preprocessing_status_many(companies, source_date)
        for _cid, st in statuses.items():
            for it in (st.get("processedWalletsDetailed") or []):
                if not isinstance(it, dict):
                    continue
                w = beehus_catalog.id_str(it.get("walletId")) or str(it.get("walletId") or "")
                if w:
                    processed_set.add(w)

    rows = []
    for wid in visible:
        cid  = wallet_to_co[wid]
        # A wallet is repeatable from `source_date` only if it has a processed
        # position on that exact date. Without a chosen date (or when the
        # wallet has no position on it), `lastDate` stays empty: the UI shows
        # the hint and a blank source input the operator can fill manually.
        has_pos = bool(source_date) and wid in processed_set
        last = source_date if has_pos else None
        rows.append({
            "walletId":    wid,
            "walletName":  wallet_names.get(wid, wid),
            "companyId":   cid,
            "companyName": company_names.get(cid, cid),
            "lastDate":    last,
            "suggestedTarget": _next_biz_day(source_date) if has_pos else "",
        })
    rows.sort(key=lambda r: ((r["companyName"] or "").lower(),
                             (r["walletName"]  or "").lower()))
    return jsonify({"wallets": rows})


@bp.route("/api/repetir-posicoes/results-wallets")
def results_wallets():
    """Carteiras da empresa COM resultado de NAV na data (endpoint consolidado
    `/results` → `walletsWithNavDetailed`).

    Lista TODAS as carteiras que o `/results` devolve — **sem** filtro de
    divergência (a Posição Projetada deixou de embutir a Conciliação NAV).
    Cada carteira aqui tem navPackage/posição processada na data, então é uma
    origem válida para projetar; o front gera a prévia com `source = data` e
    `target = próximo dia útil`.

    Query: `companyId`, `date` (YYYY-MM-DD).
    Retorna `{wallets:[{walletId, walletName, nav, navPerShare, amount}], date}`.
    `{wallets: []}` quando a empresa não é visível ou o `/results` não responde.
    """
    company_id = (request.args.get("companyId") or "").strip()
    date_str   = (request.args.get("date") or "").strip()
    if not company_id or not company_visible(company_id) or not date_str:
        return jsonify({"wallets": [], "date": date_str})
    try:
        _safe_date(date_str)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    res = beehus_catalog.nav_results(company_id, date_str)
    wallet_names = get_wallet_names()
    wallets = []
    for w in res.get("walletsWithNavDetailed", []):
        wid = beehus_catalog.id_str(w.get("walletId"))
        if not wid:
            continue
        wallets.append({
            "walletId":    wid,
            "walletName":  w.get("walletName") or wallet_names.get(wid, wid),
            "nav":         w.get("nav"),
            "navPerShare": w.get("navPerShare"),
            "amount":      w.get("amount"),
        })
    wallets.sort(key=lambda x: (x["walletName"] or "").lower())
    return jsonify({"wallets": wallets, "date": date_str})


# ── Rule chain ──────────────────────────────────────────────────────────────
# Each rule receives a snapshot of the row plus a shared context (txns,
# security metadata, config) and returns the rewritten row. The chain is
# applied in array order — keep dependencies in mind when reordering. See
# `docs/REPETIR_POSICOES.md` for the rule semantics.


def _rule_buysell_qty(row, ctx):
    """Rule a: apply Σ buySell quantity-delta at targetDate, with the
    **cash-convention sign inverted**.

    In this database, both `transactions.balance` and `transactions.quantity`
    are stored in cash-flow convention: negative = money/quantity leaving
    the wallet (i.e. a purchase from the buyer's perspective), positive =
    money/quantity entering (a sale). The position delta is the opposite
    — a purchase increases the holding. So we negate either side.

    Per-transaction delta is computed as:
      • if `tx.quantity` is present → `-tx.quantity`
      • else if `tx.balance` is present and the row's PU is non-zero →
        `-tx.balance / row.pu` (cash divided by unit price → quantity)
      • else → skip (no usable info)

    The row's `pu` at this point is the seeded source PU (before
    rule_price runs). For B1/curva overrides applied later via the
    preview buttons, the operator can see the recomputed balance in the
    UI but the quantity stays as derived here — the rule chain runs
    once per preview build."""
    sec_id = row.get("securityId")
    if not sec_id:
        return row
    txns = ctx["txn_by_security"].get(sec_id) or []
    if not txns:
        return row
    # PU para o fallback `balance / pu`: prefere a PU corrente da
    # repetição (já passou por target/curva), mas cai na PU **da
    # source** quando a corrente é 0 (caso típico: security
    # vencida/resgatada cuja PU em target processedPosition é 0). Sem
    # esse fallback, transações com apenas `balance` (RESGATE/RECOMPRA)
    # seriam silenciosamente ignoradas e a quantidade ficaria igual à
    # da source, divergindo do target onde a posição zerou.
    pu_current = row.get("pu") or 0
    pu_source  = row.get("_sourcePu") or 0
    pu_for_div = pu_current if pu_current else pu_source
    delta_qty = 0.0
    for t in txns:
        try:
            q = t.get("quantity")
            if q is not None:
                # Sign inversion — see docstring.
                delta_qty += -float(q)
                continue
            b = t.get("balance")
            if b is not None and pu_for_div:
                delta_qty += -float(b) / float(pu_for_div)
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    new_qty = (row.get("quantity") or 0) + delta_qty
    row["quantity"] = new_qty
    # Saldo da repetição usa a PU corrente (que pode ter sido zerada
    # pelo target) — então saldo = qty × 0 = 0, refletindo o estado
    # "posição liquidada" no target.
    row["balance"]  = new_qty * pu_current
    row.setdefault("flags", []).append("buySell")
    # `txnIds` is kept for backward compatibility / audit; `txns` carries
    # the full detail (the preview UI reads `txns`).
    row.setdefault("txnIds", []).extend(t["id"] for t in txns)
    row.setdefault("txns",   []).extend(txns)
    return row


def _rule_maturity(row, ctx):
    """Rule b: if security.maturityDate == targetDate, zero quantity/balance."""
    sec_id = row.get("securityId")
    if not sec_id:
        return row
    meta = ctx["sec_meta_by_id"].get(sec_id) or {}
    maturity = str(meta.get("maturityDate") or "")[:10]
    if maturity and maturity == ctx["targetDate"]:
        row["quantity"] = 0.0
        row["balance"]  = 0.0
        row.setdefault("flags", []).append("maturity")
    return row


def _rule_target_processed_price(row, ctx):
    """Rule pre-pre-a: override `row.pu` from the target-date
    `processedPosition` when an entry for the security exists.

    Rationale: the operator wants the repetition to honour whatever the
    upstream already produced for `targetDate` (when available), since
    that's the closest thing to ground truth. The rule runs **first** in
    the chain so subsequent rules (curva fallback, buySell qty) see the
    target PU.

    When the security is absent from the target processedPosition (or
    the doc doesn't exist), the rule is a no-op and the next rules
    apply normally (curva / source PU).

    Lookup is precomputed once per wallet (see
    `_build_preview_for_wallet`), so this is O(1) per row."""
    sec_id = row.get("securityId")
    if not sec_id:
        return row
    target_map = ctx.get("target_processed") or {}
    entry = target_map.get(sec_id)
    if not entry:
        return row
    pu = entry.get("pu")
    if pu is None:
        return row
    try:
        pu_f = float(pu)
    except (TypeError, ValueError):
        return row
    row["pu"]      = pu_f
    row["balance"] = pu_f * float(row.get("quantity") or 0)
    row.setdefault("flags", []).append("targetPrice")
    return row


def _rule_curva_price(row, ctx):
    """Rule pre-a: override `row.pu` from the curva PU lookup map when
    a match exists for `(walletId, securityId, targetDate)`. **Skipped**
    when the row already has `targetPrice` in flags — the target
    processedPosition is authoritative and the curva fallback would
    silently overwrite it. Runs **before** `_rule_buysell_qty` so the
    balance/pu fallback in that rule uses the curva price.

    Lookup é **estrito por wallet**: somente a chave
    `<walletId>|<securityId>|<targetDate>` é consultada. Não há
    fallback agnóstico — se o ativo não estiver no template da curva
    sob aquele `walletId` específico, o PU original é preservado.
    Isso evita "vazamento" de PU entre wallets distintas que
    compartilham o mesmo `securityId` (ex.: duas wallets com a mesma
    LCA mas yields/lotes diferentes).

    O mapa é precomputado uma vez por request (ver
    `_compute_curva_pu_map`), então a regra é O(1) por linha."""
    sec_id = row.get("securityId")
    if not sec_id:
        return row
    # Target processedPosition wins over the curva — it's already
    # priced upstream for this exact date, so re-deriving from the
    # template would be a regression.
    if "targetPrice" in (row.get("flags") or []):
        return row
    pu_map = ctx.get("curva_pu_map") or {}
    wid = ctx.get("walletId") or ""
    td  = ctx.get("targetDate") or ""
    if not wid:
        return row
    pu = pu_map.get(f"{wid}|{sec_id}|{td}")
    if pu is None:
        return row
    row["pu"]      = float(pu)
    row["balance"] = float(pu) * float(row.get("quantity") or 0)
    row.setdefault("flags", []).append("curvaPrice")
    return row


def _rule_price(row, ctx):
    """Rule c: tag the row based on pricingType, unless an earlier rule
    already overrode the PU (target processedPosition or curva) — in
    those cases the existing flag is informative enough and adding
    `priceUnchanged` on top would be a contradiction."""
    flags = row.get("flags") or []
    if "targetPrice" in flags or "curvaPrice" in flags:
        return row
    pt = (row.get("pricingType") or "").strip()
    if pt == "B1":
        row.setdefault("flags", []).append("priceUnchanged")
    elif pt == "C3":
        row.setdefault("flags", []).append("pendingPrice")
    else:
        row.setdefault("flags", []).append("priceUnchanged")
    return row


# Registry: extend here when adding a rule (see docs/REPETIR_POSICOES.md §6).
# Order matters:
#   • `_rule_target_processed_price` runs FIRST so the target-date PU
#     (the new authoritative price source) is visible to every rule
#     that follows.
#   • `_rule_curva_price` is a fallback for securities that don't appear
#     in the target processedPosition; skipped when targetPrice already
#     set the PU.
#   • Both price rules MUST run before `_rule_buysell_qty` so that
#     rule's balance/pu fallback picks up the correct PU.
_RULES = [
    ("RULE_TARGET_PROCESSED_PRICE", _rule_target_processed_price),
    ("RULE_CURVA_PRICE",            _rule_curva_price),
    ("RULE_BUYSELL_QTY",            _rule_buysell_qty),
    ("RULE_MATURITY",               _rule_maturity),
    ("RULE_PRICE",                  _rule_price),
]


def _compute_curva_pu_map(target_dates, wallet_ids=None):
    """Run the precificacao curva engine over the saved global template
    and return a lookup map keyed estritamente por
    `walletId|securityId|date`.

    Não existe chave agnóstica (`*|sid|dt`): o lookup no Repetir
    Posições exige match exato por wallet. Entradas do template sem
    `walletId` são ignoradas e logadas — operador precisa preencher
    o `walletId` no template para que o ativo seja localizado.

    `target_dates` is an iterable of ISO date strings — used only to
    decide whether the calc bothered producing the date we care about,
    not as input to the engine itself. The engine emits one row per
    business day in the benchmark calendar; we keep only the ones the
    preview will look up.

    `wallet_ids` (opcional) — quando informado, **filtra o template** para
    apenas as securities cujo `walletId` está sendo previsto nesta rodada.
    Como o lookup é estrito por `walletId|securityId|date`, securities de
    outras wallets jamais casariam — rodar o motor da curva sobre elas é
    trabalho jogado fora. A prévia roda **uma wallet por vez**, então sem
    esse filtro cada clique em "Gerar prévia" recalcularia a curva inteira
    do template (N securities × calendário de dias úteis × queries de
    cupom/amortização) só para usar 1/N do resultado. `None` preserva o
    comportamento antigo (todo o template) para chamadas que não passam
    wallets.

    Failures are non-fatal: an empty map just disables curva overrides
    for this preview run (the operator sees the original source PU)."""
    try:
        saved_lists = _load_lists()
    except Exception:
        return {}
    wanted_wallets = (
        {str(w) for w in wallet_ids if w} if wallet_ids is not None else None
    )
    # Flatten every saved list and dedupe by (id, calcType) — later wins,
    # mirroring the prior single-template behaviour now that multiple named
    # lists may coexist. Quando `wanted_wallets` está definido, descartamos
    # entradas de outras wallets aqui (antes do motor rodar) — match estrito
    # por walletId garante que isso é semanticamente idêntico a filtrar o
    # resultado depois, só que sem pagar o custo do cálculo.
    merged = {}
    for entry in saved_lists or []:
        for sec in (entry.get("securities") or []):
            sid = sec.get("id")
            if not sid:
                continue
            if wanted_wallets is not None and str(sec.get("walletId") or "") not in wanted_wallets:
                continue
            merged[(sid, sec.get("calcType") or "")] = sec
    securities = list(merged.values())
    if not securities:
        return {}
    try:
        results = calculate_curva(securities)
    except Exception:
        return {}

    wanted_dates = {d for d in target_dates if d}
    out = {}
    skipped_no_wid = 0
    for r in results or []:
        if r.get("error"):
            continue
        sid = r.get("securityId")
        dt  = (r.get("date") or "")[:10]
        pu  = r.get("pu")
        if not sid or not dt or pu is None:
            continue
        if wanted_dates and dt not in wanted_dates:
            continue
        wid = r.get("walletId") or ""
        try:
            pu_f = float(pu)
        except (TypeError, ValueError):
            continue
        if not wid:
            # Sem walletId no template não há como identificar a wallet
            # destino — pulamos em vez de fallback agnóstico, que poderia
            # contaminar wallets distintas com o PU de outra.
            skipped_no_wid += 1
            continue
        out[f"{wid}|{sid}|{dt}"] = pu_f
    if skipped_no_wid:
        import logging
        logging.getLogger(__name__).warning(
            "repetir _compute_curva_pu_map: %d resultado(s) da curva ignorado(s) "
            "por falta de walletId no template — preencha o walletId para "
            "incluir esses ativos.", skipped_no_wid,
        )
    return out


# ── Preview ─────────────────────────────────────────────────────────────────

def _load_source_processed(wallet_id, source_date):
    """Return the processedPosition.securities[] for a wallet+date, or []."""
    doc = beehus_catalog.processed_doc(wallet_id, source_date)
    if not doc:
        return []
    return doc.get("securities") or []


def _security_meta(security_ids):
    """Return `{securityId: {maturityDate, beehusName, subscriptionOffset,
    redemptionOffset}}` for the given ids.

    O `subscriptionOffset` / `redemptionOffset` é a diferença entre
    `*SettlementDays` e `*NavDays` (mesmo cálculo de
    `transaction_security_classifier._fetch_offsets`) — é o "offset"
    que o operador usa pra cruzar transação com a NAV correspondente
    no `amountDifference`. Quando o campo upstream é `None`, assume 0.
    """
    out = {}
    oids = _to_oids(security_ids)
    if not oids:
        return out
    for s in beehus_catalog.securities_by_ids(oids).values():
        sid = str(s["_id"])
        sub_off = (s.get("subscriptionSettlementDays") or 0) - (s.get("subscriptionNavDays") or 0)
        red_off = (s.get("redemptionSettlementDays")   or 0) - (s.get("redemptionNavDays")   or 0)
        out[sid] = {
            "maturityDate":       str(s.get("maturityDate") or "")[:10] or None,
            "beehusName":         s.get("beehusName") or sid,
            "subscriptionOffset": int(sub_off),
            "redemptionOffset":   int(red_off),
        }
    return out


def _unprocessed_id_map(company_id, wallet_id, source_date):
    """Return `{securityId: unprocessedId}` for the wallet at `source_date`,
    read DIRECTLY from the raw `unprocessed-security-positions` snapshots: every
    `securities[]` entry carries the authoritative pair
    `preProcessingData.securityId → unprocessedId` for that wallet. Per
    securityId we take the `unprocessedId` from the most-recent snapshot ON OR
    BEFORE `source_date` that holds it — the source-date snapshot when present,
    else the latest prior (operators occasionally request a `sourceDate` that
    has a `processedPosition` but no same-day raw snapshot, e.g. the wallet was
    brought in mid-day, and we'd rather surface a slightly older `unprocessedId`
    than an empty cell).

    Supersedes the previous company-`securityMappings` cross-reference: that
    `from→to` map COLLIDES (one securityId carries several historical `from`
    labels — ~38% of the catalog in practice), so the inversion kept whichever
    `from` happened to appear in the snapshot and silently picked the "last
    entry seen" on collision. Reading `preProcessingData.securityId` straight
    from the snapshot is the exact pair the wallet actually held — a strict
    superset that never loses a security and never guesses the label. Shares the
    contract with `pages/carteira._unprocessed_id_maps`.

    Failures (no snapshot) → empty dict; the caller renders the column as "—"
    and the upload falls back to the legacy label."""
    if not wallet_id or not source_date:
        return {}
    # Single wallet → the seam returns {} or a one-entry {walletId: {sid: uid}};
    # take that map directly (robust to walletId id-string normalization).
    m = beehus_catalog.unprocessed_sid_uid_map(company_id, [wallet_id], source_date)
    return next(iter(m.values()), {})


#: Tipos de transações considerados **fluxos de caixa do investidor**
#: (entradas/saídas) para o cálculo de `navPerShare`. Operadora pediu:
#: aportes/resgates, ajustes contábeis, transferências de ativos e
#: taxas. Outros tipos (buySell, coupon, dividend, …) movem caixa mas
#: são fluxos internos da carteira — não somam no `inAndOutFlows`.
_TXN_TYPES_IN_OUT_FLOWS = {
    "withdrawalDeposit",
    "withdrawalDepositAdjustment",
    "securityTransfer",
    "taxes",
}

#: Tipos de transações que entram em `walletContribution` (contribuição
#: da carteira que não está atrelada a security individual). Ver
#: §8 de `docs/CONCILIACAO_RECALCULO.md`.
_TXN_TYPES_WALLET_CONTRIB = {
    "gainsExpenses", "rebate", "contributionAdjustment", "other",
}

#: Tipos de transações que somam em `eventContribution` por security
#: (lado caixa). Os dividendos vêm de `db.securityEvents` (não de
#: `db.transactions`).
_TXN_TYPES_SEC_EVENT = {"coupon", "amortization"}


def _empty_txn_bundle():
    return {
        "all_cash_delta":       0.0,
        "inflows_total":        0.0,
        "wallet_contrib_total": 0.0,
        "buysell_by_sid":       {},
        "cash_events_by_sid":   {},
        "coupon_amort_by_sid":  {},
        "required_sec_txns":    [],
    }


def _load_window_transactions(company_id, wallet_id, source_date, target_date):
    """Single-scan loader for every transaction-derived metric used by
    the preview. Substitui as 6 funções antigas (`_buysell_txns_by_security`,
    `_cash_events_by_security`, `_all_txns_cash_delta`, `_in_and_out_flows`,
    `_wallet_contribution_in_window`, `_coupon_amort_by_sid`) que faziam
    queries separadas em `db.transactions` sobre a **mesma** janela
    `(source_date, target_date]` com filtros de tipo diferentes — cada
    uma um round-trip + scan de índice. Aqui carregamos a janela inteira
    em uma única cursora e dispatchamos por tipo em Python.

    Estrutura retornada:
      all_cash_delta        — Σ balance (todos os tipos)
      inflows_total         — Σ balance para `_TXN_TYPES_IN_OUT_FLOWS`
      wallet_contrib_total  — Σ balance para `_TXN_TYPES_WALLET_CONTRIB`
      buysell_by_sid        — `{sid: [tx,…]}` apenas com `liq == target`
      cash_events_by_sid    — `{sid: [tx,…]}` cash events (display)
      coupon_amort_by_sid   — `{sid: Σ balance}` coupon+amortization
      required_sec_txns     — lista de raw docs cuja `type` está em
                              `_TXN_TYPES_REQUIRING_SECURITY` (consumida
                              por `_find_orphan_transactions` sem nova
                              query).

    `trashed != True` filtrado uma vez, como antes. Janela é
    `(source, target]` (strict-greater no source pra não dupla-contar
    transações já refletidas no `former_cash`).
    """
    if not wallet_id or not target_date:
        return _empty_txn_bundle()

    # Endpoint G só faz range inclusivo [initial, final] por liquidationDate.
    # A janela original é `(source_date, target_date]` (strict-greater no
    # source). Buscamos com initial_date = source_date (ou um piso bem antigo
    # quando não há source) e final_date = target_date, depois reaplicamos no
    # cliente o `> source_date` estrito e o guard `trashed != True`.
    initial_date = source_date if source_date else "0001-01-01"
    cursor = beehus_catalog.transactions_search(
        company_id,
        initial_date=initial_date,
        final_date=target_date,
        wallet_ids=[wallet_id],
    )

    bundle = _empty_txn_bundle()
    for d in cursor:
        if d.get("trashed"):
            continue
        liq_full = str(d.get("liquidationDate") or "")[:10]
        if source_date and not (liq_full > source_date):
            continue
        ttype = d.get("beehusTransactionType") or ""
        sid   = str(d.get("securityId") or "")
        liq   = str(d.get("liquidationDate") or "")[:10]
        try:
            bal_f = float(d.get("balance") or 0)
        except (TypeError, ValueError):
            bal_f = 0.0

        # all_cash_delta — captura a janela (yields, fees,
        # deposits/withdrawals, buySells, …) para projeção de caixa.
        # `securityTransfer` é **excluído** por pedido da operadora: a
        # transferência de ativo move `balance` mas não é fluxo de caixa
        # real do investidor para fins do SALDO PROJETADO do Caixa.
        if ttype != "securityTransfer":
            bundle["all_cash_delta"] += bal_f

        if ttype in _TXN_TYPES_IN_OUT_FLOWS:
            bundle["inflows_total"] += bal_f
        if ttype in _TXN_TYPES_WALLET_CONTRIB:
            bundle["wallet_contrib_total"] += bal_f

        # `buysell_by_sid` mantém o filtro **estrito** `liq == target`
        # (rule `a` — alimenta a régua de qty). Janela mais ampla não
        # serve aqui: trades de dias anteriores já estão refletidos em
        # `source_secs.quantity`.
        if ttype == "buySell" and sid and liq == target_date:
            bundle["buysell_by_sid"].setdefault(sid, []).append({
                "id":            str(d["_id"]),
                "type":          "buySell",
                "quantity":      d.get("quantity"),
                "balance":       d.get("balance"),
                "description":   d.get("description") or "",
                "operationDate": str(d.get("operationDate") or "")[:10],
            })

        if ttype in _TXN_TYPES_SEC_CASH_EVENT and sid:
            bundle["cash_events_by_sid"].setdefault(sid, []).append({
                "id":              str(d["_id"]),
                "type":            ttype,
                "quantity":        d.get("quantity"),
                "balance":         d.get("balance"),
                "description":     d.get("description") or "",
                "operationDate":   str(d.get("operationDate") or "")[:10],
                "liquidationDate": liq,
            })

        if ttype in _TXN_TYPES_SEC_EVENT and sid:
            bundle["coupon_amort_by_sid"][sid] = (
                bundle["coupon_amort_by_sid"].get(sid, 0.0) + bal_f
            )

        # Tipos que **exigem** securityId — alimentam o orphan check.
        # Guardamos o doc bruto (em vez de uma cópia trimada) pra que
        # `_find_orphan_transactions` continue acessando `_id`, etc.
        if ttype in _TXN_TYPES_REQUIRING_SECURITY:
            bundle["required_sec_txns"].append(d)

    return bundle


def _dividend_events_by_sid(security_ids, position_date):
    """`{securityId: Σ balance}` dos securityEvents dos `security_ids` cujo
    `operationDate == position_date` e `eventType` está nos eventos "tipo
    dividendo" (`cashDividend` + `interestOnEquity`; cupom/amortização já entram
    via transactions). `balance` é o dividendo por cota.

    Lê via API (`beehus_catalog.dividend_events_by_sid` → endpoint
    `/beehus/security-events?securities=<csv>`), não mais `db.securityEvents`.
    Escopa pelos `security_ids` da carteira: só ativos com posição-base
    (`formerQuantity`>0) contribuem dividendo (`dividendPerShare × formerQuantity`),
    e esses estão todos no set — equivalente ao antigo scan global por data."""
    return beehus_catalog.dividend_events_by_sid(security_ids, position_date)


def _security_contribution(former_quantity, former_pu, quantity, pu,
                           coupon_amort=0.0, dividend_per_share=0.0,
                           execution_price=None,
                           intraday_quantity=None):
    """Calcula `{daily, intraday, event, total}` para um security.

    Fórmulas 4–7 do `docs/CONCILIACAO_RECALCULO.md`:

        dailyContribution    = formerQuantity × (PU − formerPU)
        intradayContribution = (quantity − formerQuantity) × (PU − executionPrice)
        eventContribution    = couponAmort + dividendPerShare × formerQuantity
        totalContribution    = daily + intraday + event

    `intraday_quantity` (opcional) sobrescreve o `quantity` apenas na
    Fórmula 5 — útil para a contribuição projetada quando a projeção
    não capturou um trade que o upstream já gravou. Sem isso, a
    `rep.quantity` permanece igual ao `formerQuantity` (Δqty = 0) e o
    intraday zera, escondendo o efeito do trade real na carteira. Ao
    passar `target.quantity` como `intraday_quantity`, o intraday
    reflete o Δqty efetivo registrado pelo upstream.

    `executionPrice` cai em `PU` (intraday = 0) quando não há valor
    explícito — operadora não digita preço de execução na prévia.
    """
    fq = float(former_quantity or 0)
    fp = float(former_pu       or 0)
    q  = float(quantity        or 0)
    p  = float(pu              or 0)
    iq = float(intraday_quantity) if intraday_quantity is not None else q
    ep = float(execution_price) if execution_price is not None else p
    daily    = fq * (p - fp)
    intraday = (iq - fq) * (p - ep)
    event    = float(coupon_amort or 0) + float(dividend_per_share or 0) * fq
    return {
        "daily":    _round(daily,    2),
        "intraday": _round(intraday, 2),
        "event":    _round(event,    2),
        "total":    _round(daily + intraday + event, 2),
    }


def _nav_package_for(wallet_id, position_date, company_id=None):
    """Snapshot de navPackages para `(walletId, positionDate)`.

    Retorna `{nav, navPerShare, amount, formerAmount, inAndOutFlows,
    currency}` ou `None` quando não há doc para a data. Usado pelos
    blocos NAV (anterior/atual) do header da prévia e como base do
    cálculo da NAV projetada.

    Lê via API (cache consolidado da empresa quando quente, senão 1 chamada
    direta a nav-contribution) com fallback Mongo; `trashed != True` já
    garantido pela camada. `company_id` (opcional) evita resolver a empresa
    via `db.wallets` quando o chamador já a conhece.
    """
    if not wallet_id or not position_date:
        return None
    doc = beehus_catalog.nav_doc_for_entity_date(wallet_id, position_date, company_id)
    if not doc:
        return None
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return {
        "nav":                _f(doc.get("nav")),
        "navPerShare":        _f(doc.get("navPerShare")),
        "amount":             _f(doc.get("amount")),
        "formerAmount":       _f(doc.get("formerAmount")),
        "inAndOutFlows":      _f(doc.get("inAndOutFlows")),
        "returnNavPerShare":  _f(doc.get("returnNavPerShare")),
        "returnContribution": _f(doc.get("returnContribution")),
        "currency":           doc.get("currency"),
    }


def _provisions_detail(company_id, wallet_id, target_date):
    """Return the individual provision documents whose
    `[initialDate, liquidationDate)` window covers `target_date`.

    The preview lists provisions **linha a linha** (operator request)
    rather than only the aggregated sum. Each entry carries enough
    metadata for the UI to render a row:
        {id, description, balance, initialDate, liquidationDate, kind}

    `kind` is the provision's type field when present (some upstream
    feeds expose `provisionType`/`type`), otherwise empty.

    Same overlap-window rule as `pages/carteira.py::_provisions_by_wallet_date`.
    Sorted by `liquidationDate` ASC so the soonest-to-liquidate
    provisions appear first — matches the order the operator already
    sees in `Painel de Controle > Carteira`."""
    if not wallet_id or not target_date:
        return []
    out = []
    cursor = beehus_catalog.provisions_active(
        company_id, target_date, wallet_ids=[wallet_id])
    for d in cursor:
        bal = d.get("balance")
        try:
            bal_f = float(bal) if bal is not None else None
        except (TypeError, ValueError):
            bal_f = None
        out.append({
            "id":              str(d.get("_id") or ""),
            "description":     (d.get("description") or "").strip(),
            "balance":         _round(bal_f, 2) if bal_f is not None else None,
            "initialDate":     str(d.get("initialDate") or "")[:10],
            "liquidationDate": str(d.get("liquidationDate") or "")[:10],
            "kind":            d.get("provisionType") or d.get("type") or "",
            "securityId":      str(d.get("securityId") or ""),
        })
    out.sort(key=lambda p: (p["liquidationDate"] or "", p["initialDate"] or ""))
    return out


#: Tipos de `db.transactions.beehusTransactionType` que **exigem**
#: identificação de ativo (`securityId`). Mantido em sincronia com
#: o `_txnTypesNeedSecurity` da UI em `templates/repetir_posicoes.html`.
#: `taxes` foi removido — taxas são genéricas e não exigem ativo.
_TXN_TYPES_REQUIRING_SECURITY = {
    "amortization", "buySell", "coupon", "dividend", "dividendOnboarding",
    "interestOnEquity", "maturity", "securityContributionAdjustment",
    "securityTransfer",
}

#: Subset de `_TXN_TYPES_REQUIRING_SECURITY` que representa
#: **eventos de caixa** atrelados a um security (cupom, amortização,
#: dividendo, JCP). Diferente de `buySell`/`maturity`/`securityTransfer`,
#: estes tipos **não** movem `quantity` — só o caixa. Tratados de
#: forma especial em duas frentes:
#:
#:   1. Aparecem na lista `txns` da row do security (UI mostra junto
#:      com buySells) — alimentam a coluna "Transação" da tabela de
#:      posições.
#:   2. Não são checados pela régua de Δqty no `_find_orphan_transactions`
#:      (porque `qty` não mudaria mesmo com a transação presente). Em
#:      vez disso, a orfandade é validada pela **presença do security**
#:      em source ou target — coupon de um security ausente das duas
#:      posições é suspeito; coupon de um security ativo é esperado.
_TXN_TYPES_SEC_CASH_EVENT = {
    "coupon", "amortization", "dividend", "dividendOnboarding",
    "interestOnEquity",
}


def _find_orphan_transactions(wallet_id, target_date, *,
                              txns, qty_source, qty_target, sec_meta):
    """Detecta transações **órfãs** na janela `(source_date, target_date]`.

    Assinatura **stateless**: toda E/S de Mongo foi removida (transactions
    já carregadas em `_load_window_transactions`, qty_source/qty_target
    vêm do `source_secs`/`target_processed` que `_build_preview_for_wallet`
    já tem em memória, e `sec_meta` traz `beehusName` + offsets já
    calculados via `_security_meta`). A única query restante é o
    aggregate em `db.provisions` (regra secundária do gating).

    Para cada transaction cuja `beehusTransactionType` está em
    `_TXN_TYPES_REQUIRING_SECURITY` e que tem `securityId`:

      1. **Regra primária (amountDifference entre source e target):**
         compara `qty(target, sid)` com `qty(source, sid)`. Se a
         diferença for ≥ 1e-6, a transação está explicada por um
         movimento real no ativo dentro da janela → **não-órfã**.

      2. **Regra secundária (provisão ativa em `target_date`):** se
         nenhuma amountDifference foi encontrada, ainda pode existir
         uma provisão para `(walletId, securityId)` cobrindo a **data
         alvo** —
         `initialDate ≤ target_date  AND  liquidationDate > target_date`.
         Mesma régua de `_provisions_detail`.

      3. Caso contrário → **órfã**.

    Transações sem `securityId` (em tipos que exigem) caem como órfãs
    com `problem = "sem securityId"`. Direção e offset são apenas
    expostos no payload para diagnóstico.

    Argumentos:
        wallet_id:    id da wallet (usado só pra filtrar provisões).
        target_date:  data alvo da prévia.
        txns:         lista pré-carregada de raw transactions cujo tipo
                      está em `_TXN_TYPES_REQUIRING_SECURITY` (proveniente
                      de `_load_window_transactions(...).required_sec_txns`).
        qty_source:   `{sid: float}` — qty por security em source_date.
        qty_target:   `{sid: float}` — qty por security em target_date.
        sec_meta:     `{sid: {beehusName, subscriptionOffset,
                      redemptionOffset, …}}` (saída do `_security_meta`).
    """
    if not wallet_id or not target_date or not txns:
        return []

    # Provisões ativas em `target_date` — regra **unificada** com o
    # `_provisions_detail` (mesma usada pelo total/chip da prévia):
    #
    #     initialDate <= target_date  AND  liquidationDate > target_date
    #
    # Não dá pra reaproveitar a lista do `_provisions_detail` porque ela
    # filtra por `companyId` (não-essencial aqui — walletId já discrimina)
    # **e** o conjunto `{sid}` aqui basta. Mantida como aggregate única
    # pra continuar barato (índice em walletId + initialDate).
    prov_sids_covering_target = set()
    for p in beehus_catalog.provisions_active(
            None, target_date, wallet_ids=[wallet_id]):
        sid = str(p.get("securityId") or "")
        if sid:
            prov_sids_covering_target.add(sid)

    orphans = []
    for t in txns:
        sid    = str(t.get("securityId") or "")
        liq    = str(t.get("liquidationDate") or "")[:10]
        ttype  = t.get("beehusTransactionType") or ""
        bal    = t.get("balance") or 0
        try:
            bal_f = float(bal)
        except (TypeError, ValueError):
            bal_f = 0.0

        if not sid:
            orphans.append({
                "id":              str(t.get("_id") or ""),
                "type":            ttype,
                "securityId":      "",
                "securityName":    "",
                "balance":         bal_f,
                "quantity":        t.get("quantity"),
                "liquidationDate": liq,
                "description":     (t.get("description") or "").strip(),
                "offset":          None,
                "direction":       "",
                "problem":         "sem securityId",
            })
            continue

        # `sec_meta` já contém os offsets pré-calculados (settle − nav)
        # pelos campos `subscriptionOffset`/`redemptionOffset`, então
        # não precisamos refazer a aritmética com os raw days.
        info = sec_meta.get(sid, {}) or {}
        if bal_f < 0:
            offset = int(info.get("subscriptionOffset") or 0)
            direction = "subscription"
        else:
            offset = int(info.get("redemptionOffset") or 0)
            direction = "redemption"

        qty_s = qty_source.get(sid, 0.0)
        qty_t = qty_target.get(sid, 0.0)

        # ── Cash events (coupon/amortization/dividend/JCP) ─────────
        # Esses tipos não movem `quantity` — só caixa. A régua de
        # Δqty não se aplica; em vez disso, marcamos como órfã apenas
        # quando o security está **ausente** das duas posições
        # (source e target). Coupon de um security ativo é evento
        # esperado, não órfã.
        if ttype in _TXN_TYPES_SEC_CASH_EVENT:
            if qty_s != 0.0 or qty_t != 0.0:
                continue  # security presente em alguma data → não-órfã
            problem = "cash event sem ativo correspondente em source nem target"
            orphans.append({
                "id":              str(t.get("_id") or ""),
                "type":            ttype,
                "securityId":      sid,
                "securityName":    info.get("beehusName") or sid,
                "balance":         _round(bal_f, 2),
                "quantity":        t.get("quantity"),
                "liquidationDate": liq,
                "description":     (t.get("description") or "").strip(),
                "offset":          offset,
                "direction":       direction,
                "problem":         problem,
            })
            continue

        # ── Trade events (buySell/securityTransfer/maturity/…) ─────
        # Regra primária — `amountDifference` entre source e target.
        # Se a quantidade do ativo se moveu entre a posição de origem
        # e a posição alvo, qualquer transação na janela está
        # explicada pelo movimento. Bate com a intuição de quem olha
        # a prévia: "o ativo se moveu na minha carteira, então as
        # transações dele não são órfãs".
        amt_d_window = qty_t - qty_s
        if abs(amt_d_window) >= 1e-6:
            continue

        # Regra secundária — provisão ativa em `target_date` para o
        # security da transação. Mesma régua de `_provisions_detail`:
        # `initialDate <= target_date AND liquidationDate > target_date`.
        # Não usa mais a data da transação como referência — alinha
        # com o que o operador vê na lista de provisões da prévia.
        if sid in prov_sids_covering_target:
            continue

        # Diagnóstico do motivo. Quando o ativo nem aparece em source
        # nem em target → "ativo ausente das posições"; quando aparece
        # mas com qty igual → "qty inalterada entre source e target".
        if qty_s == 0.0 and qty_t == 0.0:
            problem = "ativo ausente das posições source e target"
        else:
            problem = (
                "qty inalterada entre source e target "
                f"({qty_s:g} → {qty_t:g}) e sem provisão"
            )

        orphans.append({
            "id":              str(t.get("_id") or ""),
            "type":            ttype,
            "securityId":      sid,
            "securityName":    info.get("beehusName") or sid,
            "balance":         _round(bal_f, 2),
            "quantity":        t.get("quantity"),
            "liquidationDate": liq,
            "description":     (t.get("description") or "").strip(),
            "offset":          offset,
            "direction":       direction,
            "problem":         problem,
        })

    orphans.sort(key=lambda o: (o.get("liquidationDate") or "",
                                 o.get("securityName") or ""))
    return orphans


def _target_processed_position(wallet_id, target_date):
    """Return `{securityId: {quantity, pu, balance}}` from
    `processedPosition` at `(walletId, target_date)`. Empty dict when
    no doc exists at that exact date.

    Used by:
      • `RULE_TARGET_PROCESSED_PRICE` — overrides `row.pu` with the
        target-date PU when available (the new primary price source).
      • Preview-time diff report — each row compares the calculated
        (quantity, pu, balance) against the target processedPosition's
        values so the operator can see where the repetition diverges
        from the actual upstream snapshot for the same date."""
    if not wallet_id or not target_date:
        return {}
    doc = beehus_catalog.processed_doc(wallet_id, target_date)
    if not doc:
        return {}
    out = {}
    for s in doc.get("securities") or []:
        sid = str(s.get("securityId") or "")
        if not sid:
            continue
        pu  = s.get("pu")
        qty = s.get("quantity")
        bal = s.get("balance")
        if bal is None and pu is not None and qty is not None:
            try:
                bal = float(pu) * float(qty)
            except (TypeError, ValueError):
                bal = None
        out[sid] = {
            "quantity":       qty,
            "pu":             pu,
            "balance":        bal,
            # `executionPrice` é gravado pelo motor de cota no
            # processedPosition quando a security teve trade na data.
            # Usado pelo `intradayContribution` (Fórmula 5 do
            # `docs/CONCILIACAO_RECALCULO.md`).
            "executionPrice": s.get("executionPrice"),
        }
    return out


def _build_preview_for_wallet(*, company_id, wallet_id, source_date, target_date,
                              curva_pu_map=None):
    """Apply the rule chain and return a preview dict for a single wallet.

    `curva_pu_map` (optional) is the precomputed lookup produced by
    `_compute_curva_pu_map`. Threading it through `ctx` lets the curva
    rule run as the **first** step of the chain, so PU overrides are
    visible to the buysell rule's balance/pu fallback. When not
    provided, the curva rule is a no-op (the preview falls back to
    source PUs).

    The response also carries a per-row `targetProcessed` block (qty/pu/
    balance from `processedPosition` at `target_date`, when available)
    plus a `diff` block computed from `repetition - targetProcessed`.
    The wallet-level `targetSummary` aggregates row counts and totals so
    the UI can highlight divergences between the calculated repetition
    and the upstream's actual snapshot for the same date."""
    source_secs = _load_source_processed(wallet_id, source_date)

    # Carrega TODAS as transactions da janela (source, target] numa só
    # varredura e dispatcha em Python por tipo. Substitui as 6 queries
    # antigas (`_buysell_txns_by_security`, `_cash_events_by_security`,
    # `_all_txns_cash_delta`, `_in_and_out_flows`, `_wallet_contribution_in_window`,
    # `_coupon_amort_by_sid`) que repetiam o mesmo $match com filtros de
    # tipo diferentes. Ver `_load_window_transactions`.
    txn_bundle = _load_window_transactions(
        company_id, wallet_id, source_date, target_date,
    )
    txn_by_security      = txn_bundle["buysell_by_sid"]
    cash_events_by_sid   = txn_bundle["cash_events_by_sid"]
    coupon_amort_by_sid  = txn_bundle["coupon_amort_by_sid"]
    wallet_contrib_total = txn_bundle["wallet_contrib_total"]

    sec_ids = {str(s.get("securityId") or "") for s in source_secs}
    sec_ids.discard("")
    # Adiciona os securityIds dos cash events ao set principal **antes**
    # de chamar `_security_meta`, pra que esses ativos também ganhem
    # `beehusName` quando aparecem só via coupon (sem source/buySell).
    for _sid in cash_events_by_sid.keys():
        if _sid:
            sec_ids.add(_sid)
    sec_meta = _security_meta(sec_ids)
    # `unprocessedId` per security, sourced from the wallet's snapshot at
    # `source_date` and translated via `securityMappings`. Used both as a
    # display column in the preview and as the `Ativo` field in the
    # upstream .xlsx (replacing the previous `beehusName` label).
    uid_by_sid = _unprocessed_id_map(company_id, wallet_id, source_date)

    # Target processedPosition: drives both the new authoritative PU
    # rule (`RULE_TARGET_PROCESSED_PRICE`) and the per-row diff report.
    target_processed = _target_processed_position(wallet_id, target_date)

    # Dividends via endpoint `/beehus/security-events` (escopado aos ativos da
    # carteira — `sec_ids` já reúne os securityIds da posição-fonte + cash events).
    dividend_evt_by_sid = _dividend_events_by_sid(sec_ids, target_date)

    # Securities que aparecem só em transactions/target/cash events e
    # ainda não foram cobertos pelo `_security_meta` inicial. Coletamos
    # primeiro num set e fazemos **um único** batch — o loop anterior
    # chamava `_security_meta([sid])` por sid, fazendo N round-trips
    # ao Mongo quando bastava 1 com `$in`.
    _extra_sids = set()
    for _src in (txn_by_security.keys(),
                 target_processed.keys(),
                 cash_events_by_sid.keys()):
        for sid in _src:
            if sid and sid not in sec_ids:
                _extra_sids.add(sid)
    if _extra_sids:
        sec_ids |= _extra_sids
        sec_meta.update(_security_meta(list(_extra_sids)))

    ctx = {
        "companyId":        company_id,
        "walletId":         wallet_id,
        "sourceDate":       source_date,
        "targetDate":       target_date,
        "txn_by_security":  txn_by_security,
        "sec_meta_by_id":   sec_meta,
        "curva_pu_map":     curva_pu_map or {},
        "target_processed": target_processed,
    }

    # Index the source rows by securityId so we can attach a "synthetic"
    # row for new securities introduced by transactions only.
    source_by_sid = {}
    for s in source_secs:
        sid = str(s.get("securityId") or "")
        if not sid:
            continue
        source_by_sid[sid] = s

    # Provisões ativas em `target_date` indexadas por `securityId` —
    # cada security na tabela ganha uma coluna mostrando o somatório
    # das provisões dela. A lista completa é mantida pra o tooltip
    # (descrição + datas de liquidação por provisão individual).
    # Provisões **sem** `securityId` ficam na lista geral só (chip do
    # header), não entram em nenhuma row.
    provisions_list = _provisions_detail(company_id, wallet_id, target_date)
    provisions_by_sid_sum = {}
    provisions_by_sid_list = {}
    for _p in provisions_list:
        _sid = (_p.get("securityId") or "").strip()
        if not _sid:
            continue
        try:
            provisions_by_sid_sum[_sid] = provisions_by_sid_sum.get(_sid, 0.0) + float(_p.get("balance") or 0)
        except (TypeError, ValueError):
            pass
        provisions_by_sid_list.setdefault(_sid, []).append(_p)

    rows = []
    # Wallet-level diff aggregates (counts/totals across rows).
    diff_summary = {
        "matched":              0,  # security exists in both, no quantity/PU/balance diff
        "diverged":             0,  # security exists in both, any diff
        "missingInTarget":      0,  # row computed but target has no entry
        "missingInRepetition":  0,  # target has entry but the rep row is dropped (qty==0 in source)
        "quantityMismatches":   0,  # subset of diverged where qty differs (operator-flagged)
        "totalBalanceDiff":     0.0,
    }
    seen_in_rows = set()
    for sid in sorted(sec_ids,
                      key=lambda x: (sec_meta.get(x, {}).get("beehusName") or x).lower()):
        src = source_by_sid.get(sid, {})
        # Filtro: ativos que existiam no source_date com quantity == 0 (ou
        # null) são normalmente descartados — significam posições já
        # liquidadas em datas anteriores. **EXCEÇÃO**: quando o target
        # traz `qty > 0` para essa security (apareceu de novo via
        # troca de cadastro, onboarding novo, ou trade não capturado),
        # mantemos a row pra que o operador veja e possa criar a
        # provisão pendente correspondente. Ativos `isNew` (só em
        # transactions, sem entrada no source) continuam, porque
        # representam compras na target_date.
        in_source = sid in source_by_sid
        in_txns   = sid in txn_by_security
        try:
            _src_q = float(src.get("quantity") or 0) if in_source else 0.0
        except (TypeError, ValueError):
            _src_q = 0.0
        try:
            _tgt_q = float((target_processed.get(sid) or {}).get("quantity") or 0)
        except (TypeError, ValueError):
            _tgt_q = 0.0
        _has_target_qty = abs(_tgt_q) >= 1e-6

        if in_source and _src_q == 0 and not _has_target_qty and not in_txns:
            # Source zerado + target sem qty + sem transação → descarta.
            continue
        if (not in_source) and (not in_txns):
            # Target-only — mantém como row se target.qty > 0 pra que o
            # operador possa verificar o ativo "novo" (provavelmente
            # troca de cadastro upstream). Caso contrário, conta como
            # `missingInRepetition` e descarta.
            if sid in target_processed and not _has_target_qty:
                diff_summary["missingInRepetition"] += 1
                try:
                    tb = float(target_processed[sid].get("balance") or 0)
                    diff_summary["totalBalanceDiff"] -= tb
                except (TypeError, ValueError):
                    pass
                continue
            if sid not in target_processed:
                continue
            # cai para o seed da row abaixo (com original zerado e
            # rep inicializado em zero; o override de qty abaixo do
            # rule chain vai preencher rep.quantity = target.quantity).
        original = {
            "quantity": src.get("quantity"),
            "pu":       src.get("pu"),
            "balance":  src.get("balance"),
        }
        # Seed the repetition row from the source (or zero if security only
        # appears in transactions on the target date). `_sourcePu` é a
        # PU **antes** de qualquer regra rodar — usada pelo fallback de
        # `RULE_BUYSELL_QTY` quando o target zera a PU (security
        # vencida/resgatada na data alvo): nesse caso `row.pu` chega em
        # 0 e `balance / row.pu` falharia silenciosamente; cair na PU
        # original permite que a quantidade seja zerada pela compra.
        rep = {
            "securityId":  sid,
            "quantity":    float(src.get("quantity") or 0),
            "pu":          float(src.get("pu") or 0),
            "balance":     float(src.get("balance") or 0),
            "pricingType": src.get("pricingType"),
            "flags":       [],
            "txnIds":      [],
            "txns":        [],
            "_sourcePu":   float(src.get("pu") or 0),
        }
        # Run the chain.
        for _name, fn in _RULES:
            rep = fn(rep, ctx)

        # ── Override de quantity por amountDifference ───────────────
        # Quando o upstream gravou um Δqty entre source e target que
        # a projeção não capturou (não há buySell na janela nem
        # provisão cobrindo `target_date`), adotamos a `quantity` do
        # `targetProcessed` — a "verdade" do movimento está lá. Sem
        # esse override, `rep.quantity` ficaria travada no nível da
        # source e a row apareceria como divergência de qty na prévia.
        # O caixa correspondente é tratado separadamente pelo
        # `expectedProvisions` (sugestão de provisão pendente).
        if sid and sid in target_processed:
            _has_buysell  = bool(txn_by_security.get(sid))
            _has_prov     = sid in provisions_by_sid_sum
            if not _has_buysell and not _has_prov:
                try:
                    _src_qty = float(src.get("quantity") or 0)
                    _tgt_qty = float(target_processed[sid].get("quantity") or 0)
                    if abs(_tgt_qty - _src_qty) >= 1e-6:
                        rep["quantity"] = _tgt_qty
                        try:
                            rep["balance"] = _tgt_qty * float(rep.get("pu") or 0)
                        except (TypeError, ValueError):
                            pass
                        _flags = list(rep.get("flags") or [])
                        if "amtDiffOverride" not in _flags:
                            _flags.append("amtDiffOverride")
                        rep["flags"] = _flags
                except (TypeError, ValueError):
                    pass

        # Normalise tx payload for the UI (round balance, trim description).
        # `rep.txns` carrega só buySells (alimenta a régua de qty).
        # Aqui mesclamos com os cash events (coupon/amortization/dividend/JCP)
        # do `cash_events_by_sid` pra que **todas** as transações do
        # security apareçam juntas na coluna "Transação".
        tx_rows = []
        def _push_tx(t, default_type="buySell"):
            bal = t.get("balance")
            try:
                bal = _round(bal, 2) if bal is not None else None
            except (TypeError, ValueError):
                bal = None
            tx_rows.append({
                "id":            t.get("id"),
                "type":          t.get("type") or default_type,
                "quantity":      t.get("quantity"),
                "balance":       bal,
                "description":   (t.get("description") or "").strip(),
                "operationDate": t.get("operationDate") or "",
            })
        for t in rep.get("txns") or []:
            _push_tx(t, default_type="buySell")
        for t in cash_events_by_sid.get(sid, []) or []:
            _push_tx(t, default_type="coupon")

        # Diff vs target processedPosition (when available). The diff is
        # `repetition - target` so a positive number means the repetition
        # overshot the upstream's snapshot. Numeric diffs are rounded to
        # 8/2 decimals for stable display; the wallet-level summary uses
        # the raw values so very small drifts still aggregate.
        tp = target_processed.get(sid)
        rep_qty = _round(rep.get("quantity"), 8)
        rep_pu  = _round(rep.get("pu"),       8)
        rep_bal = _round(rep.get("balance"),  2)
        # `quantity_mismatch` is the standalone "qty differ" signal the
        # operator asked for — set independently of the broader matched/
        # diverged categorisation so future logic (action TBD) can
        # branch on quantity-specific divergences without re-deriving.
        # Tolerância única — **impacto em $** de no mínimo 1 centavo.
        # Cada componente (qty, pu, balance) é convertido em dinheiro
        # antes da comparação:
        #   qty_$ = |qty_d × pu_ref|   (qty drift × preço relevante)
        #   pu_$  = |pu_d  × qty_ref|  (pu drift × tamanho da posição)
        #   bal_$ = |bal_d|            (já é o impacto)
        # `pu_ref` / `qty_ref` usam o **máximo** entre repetição e target
        # — pega o cenário "pior" caso um dos lados esteja zerado
        # (security liquidada, security ausente do source, etc.).
        # Diverge se qualquer impacto ≥ 0.01.
        _DIV_CENT = 0.01
        quantity_mismatch = False
        if tp is not None:
            try:
                tp_qty = float(tp.get("quantity") or 0)
                tp_pu  = float(tp.get("pu")       or 0)
                tp_bal = float(tp.get("balance")  or 0)
            except (TypeError, ValueError):
                tp_qty = tp_pu = tp_bal = 0.0
            qty_d = (rep.get("quantity") or 0) - tp_qty
            pu_d  = (rep.get("pu")       or 0) - tp_pu
            bal_d = (rep.get("balance")  or 0) - tp_bal
            rep_q = float(rep.get("quantity") or 0)
            rep_p = float(rep.get("pu")       or 0)
            pu_ref  = max(abs(rep_p), abs(tp_pu))
            qty_ref = max(abs(rep_q), abs(tp_qty))
            qty_impact = abs(qty_d) * pu_ref
            pu_impact  = abs(pu_d)  * qty_ref
            bal_impact = abs(bal_d)
            diff = {
                "quantity": _round(qty_d, 8),
                "pu":       _round(pu_d,  8),
                "balance":  _round(bal_d, 2),
                # Impactos em $ por componente — o frontend usa esses
                # valores pra decidir quais linhas (qtd/pu/sld) mostrar
                # no diff cell, mantendo a mesma régua do backend.
                "qtyImpact":     _round(qty_impact, 4),
                "puImpact":      _round(pu_impact,  4),
                "balanceImpact": _round(bal_impact, 2),
            }
            target_block = {
                "quantity":       tp.get("quantity"),
                "pu":             tp.get("pu"),
                "balance":        tp.get("balance"),
                # `executionPrice` gravado pelo motor de cota upstream
                # — alimenta a Fórmula 5 (intradayContribution) abaixo.
                "executionPrice": tp.get("executionPrice"),
            }
            quantity_mismatch = qty_impact >= _DIV_CENT
            same = (qty_impact < _DIV_CENT
                    and pu_impact  < _DIV_CENT
                    and bal_impact < _DIV_CENT)
            if same:
                diff_summary["matched"] += 1
            else:
                diff_summary["diverged"] += 1
                diff_summary["totalBalanceDiff"] += bal_d
                if quantity_mismatch:
                    diff_summary["quantityMismatches"] = (
                        diff_summary.get("quantityMismatches", 0) + 1
                    )
        else:
            diff = None
            target_block = None
            # No target entry → conceptually a qty mismatch too (target
            # qty implicit zero vs rep qty != 0). Mesma régua do bloco
            # principal: o impacto em $ aqui é simplesmente o saldo da
            # repetição (qty × pu = balance), então marca como mismatch
            # só quando esse saldo é ≥ 1 centavo.
            try:
                rep_bal_abs = abs(float(rep.get("balance") or 0))
            except (TypeError, ValueError):
                rep_bal_abs = 0.0
            quantity_mismatch = rep_bal_abs >= _DIV_CENT
            if quantity_mismatch:
                diff_summary["quantityMismatches"] = (
                    diff_summary.get("quantityMismatches", 0) + 1
                )
            diff_summary["missingInTarget"] += 1
            try:
                diff_summary["totalBalanceDiff"] += float(rep.get("balance") or 0)
            except (TypeError, ValueError):
                pass

        seen_in_rows.add(sid)
        sec_m = sec_meta.get(sid, {})

        # ── Contribuições por security (Fórmulas 4–7 do
        #     `docs/CONCILIACAO_RECALCULO.md`) ─────────────────────
        # Computa para os DOIS cenários:
        #   - `contributionProjected`: usa rep.pu (PU da projeção)
        #   - `contributionActual`:    usa targetProcessed.pu (PU já
        #     gravado pelo upstream)
        # Ambos contra o mesmo former (source).
        #
        # **Quantity** usada no intraday é a do `targetProcessed`
        # (quando existe) — não a `rep.quantity`. Justificativa: a
        # parcela `intraday = (q − fq) × (PU − executionPrice)`
        # representa o **trade efetivamente executado** na janela
        # (Δqty source→target × spread entre PU registrado e preço
        # de execução). Esse trade é um fato imutável do upstream;
        # usar `rep.quantity` (que pode ficar travada em
        # `formerQuantity` quando a projeção não tem buySell pra
        # mover a posição) zera artificialmente o intraday e quebra
        # a conservação `daily + intraday ≈ 0` em resgates ao
        # `executionPrice`. Quando `targetProcessed` não existe (row
        # ausente da posição atual), caímos em `rep.quantity` como
        # melhor estimativa.
        #
        # `executionPrice` vem do `processedPosition.securities[]` na
        # data alvo — é o preço de execução gravado pelo motor de
        # cota upstream. Quando o security não está no processedPosition
        # (ainda não rodou ou foi resgatado), ou quando o campo está
        # vazio, passamos `None` e o helper cai no fallback `ep = PU`
        # → intraday = 0.
        former_qty = float(original.get("quantity") or 0)
        former_pu  = float(original.get("pu")       or 0)
        coupon_amort_sid = coupon_amort_by_sid.get(sid, 0.0)
        dividend_sid     = dividend_evt_by_sid.get(sid,    0.0)
        exec_price_sid   = (target_block or {}).get("executionPrice")
        # Δqty efetivo do trade — usa a quantity registrada pelo
        # upstream em `target_block` quando disponível. Sem isso, uma
        # projeção que não capturou um trade (ex: resgate sem buySell
        # gravado) zera o intraday e o total exibe só a perda de PU
        # (`daily`), divergindo do contributionActual. Quando rep e
        # target casam (caso normal), `rep.quantity ==
        # target.quantity` e o resultado é idêntico.
        intraday_qty_eff = (target_block.get("quantity")
                            if target_block is not None
                            else rep.get("quantity"))
        contrib_proj = _security_contribution(
            former_qty, former_pu,
            rep.get("quantity"), rep.get("pu"),
            coupon_amort=coupon_amort_sid,
            dividend_per_share=dividend_sid,
            execution_price=exec_price_sid,
            intraday_quantity=intraday_qty_eff,
        )
        contrib_actual = None
        if target_block:
            contrib_actual = _security_contribution(
                former_qty, former_pu,
                target_block.get("quantity"), target_block.get("pu"),
                coupon_amort=coupon_amort_sid,
                dividend_per_share=dividend_sid,
                execution_price=exec_price_sid,
            )

        rows.append({
            "securityId":    sid,
            "securityName":  sec_m.get("beehusName") or sid,
            "unprocessedId": uid_by_sid.get(sid) or "",
            "pricingType":   rep.get("pricingType"),
            "original":      original,
            "repetition":    {
                "quantity": rep_qty,
                "pu":       rep_pu,
                "balance":  rep_bal,
            },
            "targetProcessed":  target_block,
            "diff":             diff,
            "quantityMismatch": quantity_mismatch,
            "flags":     rep.get("flags") or [],
            "txnIds":    rep.get("txnIds") or [],
            "txns":      tx_rows,
            "accepted":  True,
            "isNew":     not in_source,
            # Offsets settlement−NAV pra subscription e redemption. UI
            # mostra os dois quando divergem; quando iguais, mostra um
            # número só. Vem do `db.securities.*SettlementDays` /
            # `*NavDays` via `_security_meta`.
            "subscriptionOffset": sec_m.get("subscriptionOffset", 0),
            "redemptionOffset":   sec_m.get("redemptionOffset",   0),
            # Contribuição calculada por security (projetada + atual).
            # Cada bloco carrega `{daily, intraday, event, total}`.
            # `contributionActual = None` quando a security não existe
            # no processedPosition da data atual.
            "contributionProjected": contrib_proj,
            "contributionActual":    contrib_actual,
            # Provisões ativas em `target_date` para este security
            # (mesma régua do header: `initialDate ≤ target_date AND
            # liquidationDate > target_date`). `provisionsBalance` é a
            # soma — null quando o security não tem nenhuma provisão
            # associada (UI mostra `—`). `provisionsList` carrega os
            # docs individuais pra o tooltip detalhar liquidationDate/
            # descrição.
            "provisionsBalance": _round(provisions_by_sid_sum[sid], 2)
                                  if sid in provisions_by_sid_sum else None,
            "provisionsList":    provisions_by_sid_list.get(sid) or [],
        })

    diff_summary["totalBalanceDiff"] = _round(diff_summary["totalBalanceDiff"], 2)
    diff_summary["hasTarget"] = bool(target_processed)

    # Cash projection: former_cash (at sourceDate) + Σ balance of every
    # transaction whose liquidationDate is in (sourceDate, targetDate].
    # Unlike the prior implementation, the delta is no longer scoped to
    # buySell — yields, fees, deposits/withdrawals etc. all move caixa
    # and the operator wants the full picture, matching how
    # `Painel de Controle > Carteira` displays caixa.
    # Uma única varredura de `cashAccounts` cobre source + target.
    # `cashAccounts` não tem índice em `walletId` em produção (ver
    # comentário em db.sum_cash_by_dates), então cada chamada separada
    # era um full-scan — dobrá-las para 1 reduz materialmente o tempo da
    # prévia.
    _cash_by_dt = sum_cash_by_dates(wallet_id, [source_date, target_date])
    former_cash = _cash_by_dt.get(source_date)
    delta_cash  = txn_bundle["all_cash_delta"]
    new_cash    = (former_cash or 0) + delta_cash if former_cash is not None else None
    # Target cash — valor já registrado em `cashAccounts` na data alvo
    # (não é projeção; é o snapshot do upstream). Operador compara com
    # `new_cash` para ver se a projeção bate com o que o upstream gravou.
    target_cash = _cash_by_dt.get(target_date)
    target_delta = (
        (new_cash - target_cash)
        if (new_cash is not None and target_cash is not None)
        else None
    )

    # Provisions at targetDate — display-only context, same rationale as
    # `Painel de Controle > Carteira` (provisions overlap a date window
    # and aren't part of the position upload). `provisions_list` é
    # carregado antes do loop de rows (linha ~1860) pra alimentar a
    # coluna `provisions` de cada security; aqui só agregamos o total.
    provisions = sum((p.get("balance") or 0) for p in provisions_list)

    # `amountDifferenceBySecurityId` — Δqty entre source e target por
    # security. Cobre dois usos:
    #   1. UI de transações: cada transação com `securityId` mostra a
    #      coluna "Δ Qtd posição" lendo dessa map (rastreabilidade
    #      direta — operadora confirma que o ativo se moveu).
    #   2. Conferência da régua de órfãs (a função
    #      `_find_orphan_transactions` recompõe esse mesmo mapa
    #      internamente; manter os dois consistentes evita falsos
    #      positivos).
    source_qty_by_sid = {}
    for s in source_secs:
        sid = str(s.get("securityId") or "")
        if not sid:
            continue
        try:
            source_qty_by_sid[sid] = float(s.get("quantity") or 0)
        except (TypeError, ValueError):
            pass
    amt_diff_by_sid = {}
    all_sids = set(source_qty_by_sid) | set(target_processed.keys())
    for sid in all_sids:
        tq = 0.0
        tp_entry = target_processed.get(sid) or {}
        try:
            tq = float(tp_entry.get("quantity") or 0)
        except (TypeError, ValueError):
            tq = 0.0
        sq = source_qty_by_sid.get(sid, 0.0)
        delta = tq - sq
        if abs(delta) >= 1e-6:
            amt_diff_by_sid[sid] = _round(delta, 8)

    # ── Provisões esperadas (diagnóstico) ──────────────────────────
    # Quando um ativo tem `amountDifference != 0` mas **nenhuma**
    # buySell na janela e **nenhuma provisão** cobrindo a data alvo,
    # a operação ainda não foi refletida no caixa — o sistema sugere
    # a provisão que deveria existir para fechar o gap.
    #
    # Fórmula (combinada com o operador):
    #   initialDate     = target_date
    #   liquidationDate = target_date + offset(dias úteis)
    #   balance         = executionPrice × amountDifference × (-1)
    #   description     = "Provisão de ajuste por diferença na
    #                      quantidade do ativo <beehusName>"
    #   provisionType   = "buySell"
    #   provisionSource = "amountDifference"
    #
    # Offset escolhido por **direção**:
    #   amountDifference > 0 → compra  → subscriptionOffset
    #   amountDifference < 0 → resgate → redemptionOffset
    # (Mesma convenção já usada em `_find_orphan_transactions`.)
    expected_provisions_list = []
    expected_provision_by_sid = {}
    for sid, delta in amt_diff_by_sid.items():
        if not sid:
            continue
        # Skip: buySell na janela já explica o movimento (qty é
        # justificada pela transação).
        if txn_by_security.get(sid):
            continue
        # Skip: já existe provisão cobrindo target_date pra esse sid
        # (a expectativa já foi atendida — não sugerir duplicata).
        if sid in provisions_by_sid_sum:
            continue
        try:
            delta_f = float(delta or 0)
        except (TypeError, ValueError):
            continue
        if abs(delta_f) < 1e-6:
            continue

        sec_m = sec_meta.get(sid, {})
        if delta_f > 0:
            offset_days = int(sec_m.get("subscriptionOffset") or 0)
            _direction  = "subscription"
        else:
            offset_days = int(sec_m.get("redemptionOffset") or 0)
            _direction  = "redemption"

        # Liquidação **nunca** cai no mesmo dia da `target_date`: a
        # régua do `_provisions_detail` é estrita (`liquidationDate >
        # target_date`), então uma provisão com `liquidationDate ==
        # target_date` não entraria no NAV projetado nem apareceria
        # no painel de provisões — frustraria a sugestão. Quando o
        # offset cadastral do ativo é 0 (ou ausente), avançamos
        # **1 dia útil** pra garantir que a provisão sugerida fica
        # ativa em `target_date`.
        liq_offset_days = offset_days if offset_days > 0 else 1
        liq_date = _add_biz_days(target_date, liq_offset_days)

        # `executionPrice` vem do processedPosition alvo (preço de
        # execução gravado pelo motor de cota upstream). Sem ele,
        # cai em `pu` como aproximação — sem preço, não dá pra
        # estimar o balance e a sugestão é abandonada.
        tp_entry = target_processed.get(sid) or {}
        ep_raw = tp_entry.get("executionPrice")
        if ep_raw in (None, 0):
            ep_raw = tp_entry.get("pu")
        if ep_raw in (None, 0):
            continue
        try:
            ep_f = float(ep_raw)
        except (TypeError, ValueError):
            continue

        balance = -1.0 * ep_f * delta_f
        sec_name = sec_m.get("beehusName") or sid
        expected = {
            "securityId":      sid,
            "securityName":    sec_name,
            "description":     f"Provisão de ajuste por diferença na quantidade do ativo {sec_name}",
            "balance":         _round(balance, 2),
            "initialDate":     target_date,
            "liquidationDate": liq_date,
            "provisionType":   "buySell",
            "provisionSource": "amountDifference",
            "direction":       _direction,
            "offset":          offset_days,
            "amountDifference": _round(delta_f, 8),
            "executionPrice":   _round(ep_f, 10),
        }
        expected_provisions_list.append(expected)
        expected_provision_by_sid[sid] = expected

    # ── Provisões esperadas por transação sem efeito em qty ────────
    # Caso espelho do bloco anterior: existe `buySell`/`maturity` na
    # janela `(source, target]` para um security cujo `Δqty source→target ≈ 0`.
    # Significa que o caixa mexeu (transação tem `balance`) mas a
    # posição não foi atualizada — o sistema sugere uma provisão
    # pra compensar o "caixa em trânsito" não materializado em qty.
    #
    # Régua:
    #   provisionSource = "transaction"
    #   provisionType   = "buySell"  (mesmo para maturity, por
    #                                 alinhamento com o tipo cadastral
    #                                 da provisão upstream)
    #   initialDate     = target_date
    #   liquidationDate = target_date + offset(dias úteis)
    #   balance         = (Σ balance das transações) × (-1)
    #   description     = "Provisão de ajuste por transação no ativo
    #                      <beehusName>"
    #
    # Direção pela soma de balances:
    #   total < 0 → compra/saída de caixa → subscriptionOffset
    #   total > 0 → resgate/entrada       → redemptionOffset
    #
    # Skip se já existe provisão cobrindo `target_date` ou se o sid
    # já recebeu sugestão pelo bloco de `amountDifference` (mutuamente
    # exclusivos por construção, mas guard mantido por defesa).
    _TXN_TYPES_QTY_AFFECTING = {"buySell", "maturity"}
    txn_by_sid_for_provision = {}
    for t in txn_bundle["required_sec_txns"]:
        ttype = t.get("beehusTransactionType") or ""
        if ttype not in _TXN_TYPES_QTY_AFFECTING:
            continue
        sid = str(t.get("securityId") or "")
        if not sid:
            continue
        # Δqty não-trivial entre source e target → a transação é
        # explicada pelo movimento, não vira provisão sugerida.
        if sid in amt_diff_by_sid:
            continue
        # Provisão já existe → não duplicar.
        if sid in provisions_by_sid_sum:
            continue
        # Já sugerida pelo bloco anterior (defesa).
        if sid in expected_provision_by_sid:
            continue
        try:
            bal_f = float(t.get("balance") or 0)
        except (TypeError, ValueError):
            continue
        txn_by_sid_for_provision.setdefault(sid, []).append({
            "id":      str(t.get("_id") or ""),
            "type":    ttype,
            "balance": bal_f,
        })

    for sid, tlist in txn_by_sid_for_provision.items():
        total_bal = 0.0
        for t in tlist:
            total_bal += t["balance"]
        if abs(total_bal) < 1e-6:
            # Soma desprezível (transações que se anulam) — nada a
            # provisionar. Caso raro mas vale o guard.
            continue
        sec_m = sec_meta.get(sid, {})
        if total_bal < 0:
            offset_days = int(sec_m.get("subscriptionOffset") or 0)
            _direction  = "subscription"
        else:
            offset_days = int(sec_m.get("redemptionOffset") or 0)
            _direction  = "redemption"
        # Mesma régua de "offset 0 → 1 dia útil" do bloco anterior:
        # garante `liquidationDate > target_date` (estrito) pra que a
        # provisão entre tanto no NAV projetado quanto no painel.
        liq_offset_days = offset_days if offset_days > 0 else 1
        liq_date = _add_biz_days(target_date, liq_offset_days)
        balance = -1.0 * total_bal
        sec_name = sec_m.get("beehusName") or sid
        expected = {
            "securityId":      sid,
            "securityName":    sec_name,
            "description":     f"Provisão de ajuste por transação no ativo {sec_name}",
            "balance":         _round(balance, 2),
            "initialDate":     target_date,
            "liquidationDate": liq_date,
            "provisionType":   "buySell",
            "provisionSource": "transaction",
            "direction":       _direction,
            "offset":          offset_days,
            "transactionIds":  [t["id"] for t in tlist],
            "transactionsTotal": _round(total_bal, 2),
        }
        expected_provisions_list.append(expected)
        expected_provision_by_sid[sid] = expected

    expected_provisions_list.sort(
        key=lambda x: (x.get("liquidationDate") or "", x.get("securityName") or "")
    )

    # Anexa `expectedProvision` em cada row de security — UI pode
    # mostrar inline (ex.: tooltip da coluna Provisões) quando o ativo
    # tiver expectativa pendente.
    for _r in rows:
        _sid = _r.get("securityId") or ""
        _r["expectedProvision"] = expected_provision_by_sid.get(_sid)

    # ── Blocos NAV (anterior / projetada / atual) ──────────────────
    # Operadora pediu três snapshots de NAV no header da prévia:
    #   anterior  → `db.navPackages` em sourceDate (snapshot upstream)
    #   atual     → `db.navPackages` em targetDate (snapshot upstream)
    #   projetada → calculado a partir da prévia + flows do investidor
    #
    # Fórmula da projetada (ditada pelo operador):
    #   nav            = Σ saldos(rep) + caixa(target) + provisões(target)
    #   navPerShare    = (NAV_projetada + inAndOutFlows) / amount_anterior
    #   amount         = nav / navPerShare
    #
    # `inAndOutFlows` = somatório de `transactions.balance` em
    # `(sourceDate, targetDate]` para tipos em `_TXN_TYPES_IN_OUT_FLOWS`
    # (withdrawalDeposit, …, taxes) — fluxo investidor, distinto do
    # delta de caixa total (que inclui buySell, coupons, etc).
    nav_anterior = _nav_package_for(wallet_id, source_date, company_id)
    nav_atual    = _nav_package_for(wallet_id, target_date, company_id)
    inflows      = txn_bundle["inflows_total"]

    # NAV projetada — soma dos saldos da repetição + caixa projetado
    # + provisões (todas na data alvo) + **provisões esperadas**
    # (sugestões pra ativos com `amountDifference` sem buySell/
    # provisão). Sem o termo das esperadas, o NAV ficaria descasado
    # do `gapPct` por exatamente Σ(expectedProvisions.balance) —
    # essas linhas representam o caixa "em trânsito" que o upstream
    # ainda não materializou em transaction, mas que existe
    # economicamente (a venda/compra já foi precificada via
    # `executionPrice` no `processedPosition` alvo).
    rep_balance_total = 0.0
    for r in rows:
        try:
            rep_balance_total += float((r.get("repetition") or {}).get("balance") or 0)
        except (TypeError, ValueError):
            pass
    # Σ saldos da posição **atual** (target_processed) — alimenta a
    # composição "calculada" do NAV Atual no header. Lido direto do
    # `target_processed` (fonte upstream) e não de `r.targetProcessed`
    # pra incluir securities que não viraram row (descartados pelo
    # filtro de "source==0 + target==0 + sem txn").
    target_balance_total = 0.0
    for _sid, _tp in (target_processed or {}).items():
        try:
            target_balance_total += float((_tp or {}).get("balance") or 0)
        except (TypeError, ValueError):
            pass
    expected_provisions_total = 0.0
    for ep in expected_provisions_list:
        try:
            expected_provisions_total += float(ep.get("balance") or 0)
        except (TypeError, ValueError):
            pass
    projected_nav = (
        rep_balance_total
        + (float(new_cash) if new_cash is not None else 0.0)
        + (float(provisions) if provisions is not None else 0.0)
        + expected_provisions_total
    )

    # navPerShare projetada — usa o **NAV projetado** (na data alvo) +
    # `inAndOutFlows` dividido pelo `formerAmount`. Sinal upstream do
    # `_in_and_out_flows` é preservado: aporte → balance < 0, resgate
    # → balance > 0. Como o NAV reflete o efeito líquido do fluxo
    # (NAV_target = NAV_source + price_change − inAndOutFlows), somar
    # `inAndOutFlows` ao NAV_target neutraliza o flow e isola o
    # price_change — exatamente o que o navPerShare deve refletir
    # (preço por cota livre de distorção por aporte/resgate).
    projected_nps = None
    projected_amount = None
    if nav_anterior is not None:
        ant_amount = nav_anterior.get("amount")
        if ant_amount not in (None, 0):
            projected_nps = (projected_nav + inflows) / ant_amount
            if projected_nps not in (None, 0):
                projected_amount = projected_nav / projected_nps

    # ── Contribuições agregadas (Fórmulas 8–10 + GAP) ──────────────
    # Soma dos `totalContribution` por security para os dois cenários
    # (projetada e atual). `walletContribution` é o termo a parte (não
    # atrelado a security). `returnContribution = total / formerNav`,
    # `returnNavPerShare = navPerShare / formerNavPerShare − 1`,
    # `gapPct = returnNavPerShare − returnContribution`,
    # `gapCash = gapPct × formerNav`.
    sec_total_proj    = sum((r.get("contributionProjected") or {}).get("total") or 0 for r in rows)
    sec_total_actual  = sum((r.get("contributionActual")    or {}).get("total") or 0 for r in rows)
    former_nav        = nav_anterior.get("nav")        if nav_anterior else None
    former_nps        = nav_anterior.get("navPerShare") if nav_anterior else None

    def _safe_div(a, b):
        try:
            if b in (None, 0) or a is None:
                return None
            return a / b
        except (TypeError, ZeroDivisionError):
            return None

    ret_nps_proj = _safe_div(projected_nps, former_nps)
    ret_nps_proj = (ret_nps_proj - 1) if ret_nps_proj is not None else None
    total_contrib_proj = sec_total_proj + wallet_contrib_total
    ret_contrib_proj   = _safe_div(total_contrib_proj, former_nav)
    gap_pct_proj  = (ret_nps_proj - ret_contrib_proj) if (ret_nps_proj is not None and ret_contrib_proj is not None) else None
    gap_cash_proj = (gap_pct_proj * former_nav) if (gap_pct_proj is not None and former_nav is not None) else None

    nav_projetada = {
        "nav":           _round(projected_nav, 2),
        "navPerShare":   _round(projected_nps, 10) if projected_nps is not None else None,
        "amount":        _round(projected_amount, 10) if projected_amount is not None else None,
        "inAndOutFlows": _round(inflows, 2),
        "formerAmount":  nav_anterior.get("amount") if nav_anterior else None,
        "currency":      (nav_atual or nav_anterior or {}).get("currency") if (nav_atual or nav_anterior) else None,
        # Métricas de retorno e GAP — Fórmulas 8–10 + GAP.
        "walletContribution":  _round(wallet_contrib_total, 2),
        "securitiesContribution": _round(sec_total_proj, 2),
        "totalContribution":   _round(total_contrib_proj, 2),
        "returnNavPerShare":   _round(ret_nps_proj,  10) if ret_nps_proj  is not None else None,
        "returnContribution":  _round(ret_contrib_proj, 10) if ret_contrib_proj is not None else None,
        "gapPct":              _round(gap_pct_proj,  10) if gap_pct_proj  is not None else None,
        "gapCash":             _round(gap_cash_proj, 2)  if gap_cash_proj is not None else None,
        # Decomposição da NAV calculada (operador pediu ver as parcelas
        # no header). Para projetada, `total == nav` por construção.
        "composition": {
            "securitiesBalance":  _round(rep_balance_total, 2),
            "provisions":         _round(provisions, 2) if provisions is not None else 0.0,
            "expectedProvisions": _round(expected_provisions_total, 2),
            "cash":               _round(new_cash, 2) if new_cash is not None else None,
            "total":              _round(projected_nav, 2),
        },
    }

    # ── Métricas da Atual ──────────────────────────────────────────
    # Para a atual usamos os valores **já gravados** em
    # `db.navPackages` quando disponíveis (returnNavPerShare /
    # returnContribution). Esses números representam o cálculo
    # canônico do upstream e batem com a "Análise de fundo".
    # Computamos `gap = retNps − retContrib` e `gapCash = gap × formerNav`
    # localmente pra fechar o trio na UI.
    # Adicionalmente expomos `securitiesContribution` (somado das
    # contribuições das securities calculadas com os valores atuais)
    # e `walletContribution` (mesma fórmula da projetada, idêntica
    # já que vem das mesmas transactions) — operadora pediu pra ver
    # a contribuição "dos ativos e da wallet" para os dois grupos.
    # Decomposição "calculada" da NAV Atual — vale também quando o
    # navPackage não existe (a composição é só Σ saldos + provisões +
    # caixa, números que sempre temos a partir de target_processed +
    # provisions + cashAccounts). Não inclui `expectedProvisions`
    # porque são conceito da projetada (sugestões pra ativos com Δqty
    # sem buySell/provisão real). `total` permite comparação direta
    # com `nav_atual.nav` (que vem do navPackage): bate → o upstream
    # reconciliou; diverge → tem algo fora do lugar.
    _atual_total = float(target_balance_total or 0)
    _atual_total += float(provisions or 0)
    _atual_total += float(target_cash or 0) if target_cash is not None else 0.0
    nav_atual_composition = {
        "securitiesBalance":  _round(target_balance_total, 2),
        "provisions":         _round(provisions, 2) if provisions is not None else 0.0,
        "expectedProvisions": 0.0,
        "cash":               _round(target_cash, 2) if target_cash is not None else None,
        "total":              _round(_atual_total, 2),
    }
    if nav_atual:
        ret_nps_actual    = nav_atual.get("returnNavPerShare")
        ret_contrib_actual = nav_atual.get("returnContribution")
        gap_pct_actual  = (ret_nps_actual - ret_contrib_actual) if (ret_nps_actual is not None and ret_contrib_actual is not None) else None
        gap_cash_actual = (gap_pct_actual * former_nav) if (gap_pct_actual is not None and former_nav is not None) else None
        nav_atual = {
            **nav_atual,
            "walletContribution":     _round(wallet_contrib_total, 2),
            "securitiesContribution": _round(sec_total_actual, 2),
            "totalContribution":      _round(sec_total_actual + wallet_contrib_total, 2),
            "gapPct":                 _round(gap_pct_actual,  10) if gap_pct_actual  is not None else None,
            "gapCash":                _round(gap_cash_actual, 2)  if gap_cash_actual is not None else None,
            "composition":            nav_atual_composition,
        }
    else:
        # Sem navPackage ainda — emitimos um stub com campos nulos pros
        # blocos "extraídos do navPackage" (nav/navPerShare/amount/
        # inAndOutFlows) e mantemos a `composition` calculada localmente.
        # Permite que a UI mostre a decomposição mesmo antes do upstream
        # gravar o navPackage da data alvo.
        nav_atual = {
            "nav":           None,
            "navPerShare":   None,
            "amount":        None,
            "formerAmount":  None,
            "inAndOutFlows": None,
            "currency":      (nav_anterior or {}).get("currency") if nav_anterior else None,
            "returnNavPerShare":  None,
            "returnContribution": None,
            "walletContribution":     _round(wallet_contrib_total, 2),
            "securitiesContribution": _round(sec_total_actual, 2),
            "totalContribution":      _round(sec_total_actual + wallet_contrib_total, 2),
            "gapPct":  None,
            "gapCash": None,
            "composition": nav_atual_composition,
        }

    # Transactions órfãs em `(sourceDate, targetDate]`: tipos que exigem
    # ativo e que não casam com `amountDifference` (qty source ≠ qty
    # target) nem com provisão ativa. Toda a entrada é pré-carregada
    # (txns vêm de `_load_window_transactions`, qty_source vem do
    # `source_qty_by_sid` que já computamos pra `amt_diff_by_sid`,
    # qty_target sai do `target_processed`, sec_meta já tem offsets).
    # O orphan só faz um aggregate em `db.provisions`; tudo resto é
    # in-memory.
    _qty_target_by_sid = {}
    for _sid, _tp in target_processed.items():
        try:
            _qty_target_by_sid[_sid] = float((_tp or {}).get("quantity") or 0)
        except (TypeError, ValueError):
            pass
    orphan_txns = _find_orphan_transactions(
        wallet_id, target_date,
        txns=txn_bundle["required_sec_txns"],
        qty_source=source_qty_by_sid,
        qty_target=_qty_target_by_sid,
        sec_meta=sec_meta,
    )

    # Currency for the xlsx upload. `resolve_wallet` serve do índice da API
    # (catálogo) e deriva `currencyId` do campo `currency` da carteira.
    currency_id = "BRL"
    w_doc = resolve_wallet(
        wallet_id, {"currencyId": 1, "name": 1}, company_id=company_id,
    )
    if w_doc:
        currency_id = str(w_doc.get("currencyId") or "BRL")

    return {
        "walletId":   wallet_id,
        "walletName": get_wallet_names().get(wallet_id, wallet_id),
        "sourceDate": source_date,
        "targetDate": target_date,
        "currencyId": currency_id,
        "cash": {
            "former":      _round(former_cash, 2) if former_cash is not None else None,
            "delta":       _round(delta_cash, 2),
            "new":         _round(new_cash, 2) if new_cash is not None else None,
            "target":      _round(target_cash, 2) if target_cash is not None else None,
            "targetDelta": _round(target_delta, 2) if target_delta is not None else None,
        },
        "provisions":     _round(provisions, 2),
        "provisionsList": provisions_list,
        # Provisões esperadas (diagnóstico) — uma por ativo com
        # `amountDifference != 0` sem buySell na janela e sem
        # provisão existente cobrindo `target_date`. Ver
        # `_expected_provisions_for_amount_diff` (lógica inline acima).
        "expectedProvisions": expected_provisions_list,
        "orphanTransactions": orphan_txns,
        "amountDifferenceBySecurityId": amt_diff_by_sid,
        # Blocos NAV — operadora pediu três snapshots no header.
        # `navAnterior` / `navAtual` são lidos direto de
        # `db.navPackages`. `navProjetada` é calculado em tempo de
        # prévia (Σ saldos rep + caixa + provisões + fórmula do nps).
        "navAnterior":  nav_anterior,
        "navAtual":     nav_atual,
        "navProjetada": nav_projetada,
        "targetSummary":  diff_summary,
        "rows":     rows,
        "warnings": [] if source_secs else ["sem processedPosition em sourceDate"],
    }


def _validate_items(body, *, allow_adhoc=False):
    """Validate the `items[]` envelope used by /preview and /apply.

    The daily routine is company-agnostic — there is no top-level
    `companyId`. For each item this resolves `walletId → companyId` via
    `db.wallets` (live mapping), checks that the wallet is in the global
    flagged list, and that the wallet's company passes the operator's
    `company_filter` visibility gate. The resolved `companyId` is
    attached to each item so callers can group/apply per company without
    re-hitting `db.wallets`.

    Body flag `adhoc=True` (opcional): pula a checagem de "wallet flagged
    in walletLists". Usado pelo atalho da Conciliação NAV (operador
    seleciona explicitamente uma carteira fora da rotina diária). A
    verificação de `company_visible` segue valendo — guardrail principal
    para evitar exposição cross-tenant. **Não é aceito em `/apply`**:
    o upload upstream segue exigindo wallets registradas em walletLists
    (régua de "produção"; ad-hoc serve só para inspecionar a prévia)."""
    items = list(body.get("items") or [])
    if not items:
        raise ValueError("items required")

    # `adhoc` só é honrado quando o **caller** opta in (`allow_adhoc=True`).
    # Isso garante que `/apply` (que usa o default `False`) **nunca**
    # aceita o flag mesmo se o cliente mandar no body — proteção contra
    # bypass acidental do walletLists na rota de upload.
    adhoc = allow_adhoc and bool(body.get("adhoc"))

    enabled = _all_enabled_wallets() if not adhoc else set()
    item_wids = [(it.get("walletId") or "").strip() for it in items]
    wallet_to_co = _wallet_company_map([w for w in item_wids if w])
    cf = get_company_filter()

    clean = []
    for it in items:
        wid = (it.get("walletId") or "").strip()
        sd  = (it.get("sourceDate") or "").strip()
        td  = (it.get("targetDate") or "").strip()
        _safe(wid); _safe_date(sd); _safe_date(td)
        if td <= sd:
            # Repeating onto the same date (or earlier) would overwrite the
            # source `processedPosition` with itself, or worse, fabricate a
            # past `unprocessedSecurityPositions` — never what the operator
            # means by "repeat forward". Reject before the rule chain runs.
            raise ValueError(
                f"targetDate ({td}) must be strictly after sourceDate ({sd}) for wallet {wid!r}"
            )
        if not adhoc and wid not in enabled:
            raise ValueError(f"wallet {wid!r} not flagged in config")
        cid = wallet_to_co.get(wid)
        if not cid:
            raise ValueError(f"wallet {wid!r} has no companyId in db.wallets")
        if cf and cid not in cf:
            raise PermissionError(f"company {cid!r} not visible")
        accepted = it.get("acceptedSecurityIds")
        clean.append({
            "walletId":            wid,
            "companyId":           cid,
            "sourceDate":          sd,
            "targetDate":          td,
            "acceptedSecurityIds": list(accepted) if accepted is not None else None,
        })
    return clean


@bp.route("/api/repetir-posicoes/preview", methods=["POST"])
def preview():
    body = request.get_json(silent=True) or {}
    try:
        # `allow_adhoc=True` permite que o atalho da Conciliação NAV
        # rode prévia em wallets fora do walletLists. `/apply` continua
        # com a régua estrita (allow_adhoc=False default).
        items = _validate_items(body, allow_adhoc=True)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Precompute the curva PU map once per request — the precificacao
    # template is global, so a single calcular pass covers every wallet
    # in this batch. Failures are absorbed by `_compute_curva_pu_map`
    # (returns an empty dict), so a misconfigured template doesn't
    # break the preview.
    curva_pu_map = _compute_curva_pu_map(
        [it["targetDate"] for it in items],
        wallet_ids=[it["walletId"] for it in items],
    )

    results = []
    for it in items:
        # `companyId` resolved per item by _validate_items (db.wallets is
        # the live source of truth, not the config's stored hint).
        results.append(_build_preview_for_wallet(
            company_id=it["companyId"],
            wallet_id=it["walletId"],
            source_date=it["sourceDate"],
            target_date=it["targetDate"],
            curva_pu_map=curva_pu_map,
        ))

    # Persiste um log de prévia em `data/repeat_positions_logs/` —
    # operador pediu pra ver logs mesmo antes de rodar /apply. Cada
    # geração de prévia (mesmo sem upload) deixa uma entrada com
    # `mode: "preview"`, `uploaded: false` e o `runId` no formato
    # `preview_<timestamp>_<uuid>` (vs `repeat_*` do /apply).
    #
    # A escrita em disco vai numa **thread daemon**: o OneDrive é
    # significativamente mais lento que disco local (sync de arquivos),
    # e o usuário não precisa esperar o flush pra ver a prévia. Falha
    # de IO continua best-effort dentro de `_persist_diff_log`. O
    # `runId` é gerado em `_build_preview_log_report` antes da escrita,
    # então retornamos ele independentemente do resultado do flush.
    diff_report = _build_preview_log_report(results, items)
    if diff_report:
        import threading
        threading.Thread(
            target=_persist_diff_log,
            args=(diff_report,),
            daemon=True,
        ).start()

    return jsonify({
        "results":    results,
        "runId":      diff_report.get("runId") if diff_report else None,
        "diffReport": diff_report,
    })


def _build_preview_log_report(results, items):
    """Empacota uma rodada de prévia (`/api/repetir-posicoes/preview`)
    no mesmo formato do log de `/apply`, mas com `mode = "preview"` e
    `uploaded = false`. Persiste em `data/repeat_positions_logs/` via
    `_persist_diff_log`.

    Compartilha `_build_diff_report` com o /apply pra manter
    `cashStatus`, `differencesCount`, `orphanCount` etc. consistentes
    entre as duas rotas. `accepted_set=None` aqui — uma prévia ainda
    não filtrou linhas; o log captura tudo que a operadora viu na tela.

    Retorna `None` quando não há resultados (nada a logar)."""
    if not results:
        return None
    import uuid
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    run_id = f"preview_{timestamp}_{uuid.uuid4().hex[:8]}"

    wallets_meta = {it["walletId"]: it for it in items}
    diff_log_wallets = []
    for pv in results:
        wid = pv.get("walletId") or ""
        meta = wallets_meta.get(wid) or {}
        diff_log_wallets.append(_build_diff_report(
            pv,
            wallet_id=wid,
            company_id=meta.get("companyId") or "",
            company_name=get_company_names().get(meta.get("companyId") or "", ""),
            source_date=pv.get("sourceDate") or "",
            target_date=pv.get("targetDate") or "",
            accepted_set=None,
        ))

    diff_totals = {
        "wallets":             len(diff_log_wallets),
        "diverged":            0,
        "missingInTarget":     0,
        "missingInRepetition": 0,
        "matched":             0,
        "totalBalanceDiff":    0.0,
        "withTarget":          0,
    }
    for w in diff_log_wallets:
        s = w.get("summary") or {}
        for k in ("matched", "diverged", "missingInTarget", "missingInRepetition"):
            try:
                diff_totals[k] += int(s.get(k) or 0)
            except (TypeError, ValueError):
                pass
        try:
            diff_totals["totalBalanceDiff"] += float(s.get("totalBalanceDiff") or 0)
        except (TypeError, ValueError):
            pass
        if s.get("hasTarget"):
            diff_totals["withTarget"] += 1
    diff_totals["totalBalanceDiff"] = _round(diff_totals["totalBalanceDiff"], 2)

    return {
        "runId":     run_id,
        "mode":      "preview",
        "uploaded":  False,
        "createdAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "uploads":   [],
        "totalRows": sum(len(pv.get("rows") or []) for pv in results),
        "totals":    diff_totals,
        "wallets":   diff_log_wallets,
    }


# ── B1 system prices (hold-to-maturity, type == "B1") ──────────────────────

def _b1_price_on_date(history, target_date):
    """Return the PU stored in `securityPrices.historyPrice[]` for the
    given `target_date`, or `None` if no entry matches.

    Matching rule (strict — no carry-forward):
      • `entry.date == target_date` (string compare on YYYY-MM-DD; the
        slice tolerates `datetime` values whose str repr starts with the
        ISO date)
      • `entry.value` is not null

    "B1" is a UI label for the hold-to-maturity quote, not a field on
    the historyPrice entry — the upstream stores only `date`, `value`,
    `adjustedQuantity`. Filtering by `type == "B1"` always rejected
    every entry (the field doesn't exist in the docs we've observed),
    which made the batch endpoint silently return `{}`.

    Carry-forward is deliberately omitted: a missing date is **not**
    the same as "use yesterday's PU" — operators need to see the gap
    explicitly, surfaced via the UI's `missingB1Price` flag."""
    # `historyPrice` is normally a list of entries but `_extract_hp` in
    # precificacao.py shows it can also arrive as a single dict in some
    # docs — coerce to a list so the iteration is uniform.
    if isinstance(history, dict):
        entries = [history]
    else:
        entries = history or []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        d = str(entry.get("date") or "")[:10]
        if d != target_date:
            continue
        v = entry.get("value")
        if v is None:
            continue
        return v
    return None


@bp.route("/api/repetir-posicoes/b1-prices", methods=["POST"])
def b1_prices():
    """Return B1 (hold-to-maturity) PUs for a batch of
    `(securityId, targetDate)` pairs. Source: endpoint `filtered-security-price`
    via `beehus_catalog.security_prices_resolved` — sem contexto de empresa/
    carteira, a resolução de escopo cai no record **B1 global** (o único que casa
    sem company/wallet), coerente com o caráter company-agnostic da rota. O PU é
    pego na `targetDate` exata (`_b1_price_on_date`, semântica estrita).

    Body: `{items: [{securityId, targetDate}, ...]}` — the daily routine
    is company-agnostic, so this endpoint has no `companyId` gate either.
    Returns: `{prices: {"<securityId>|<targetDate>": {"pu", "date"}}}`.
    Missing pairs are simply absent from the response — the UI flags
    them as `missingB1Price` so the operator can decide whether to wait
    for the upstream feed or fall back to the source PU."""
    body = request.get_json(silent=True) or {}
    items = list(body.get("items") or [])
    try:
        clean = []
        for it in items:
            sid = (it.get("securityId") or "").strip()
            td  = (it.get("targetDate") or "").strip()
            _safe(sid); _safe_date(td)
            clean.append((sid, td))
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Group target dates per securityId — one find() over the whole batch.
    by_sec = {}
    for sid, td in clean:
        by_sec.setdefault(sid, set()).add(td)
    if not by_sec:
        return jsonify({"prices": {}})

    # Endpoint company/wallet-agnostic → resolve o B1 global (sem contexto, só o
    # B1 casa). Via seam (filtered-security-price; aprovados/não-trashed; aceita
    # securityId string ou ObjectId internamente). Substitui o read direto de
    # `db.securityPrices`.
    prices = {}
    hp_map = beehus_catalog.security_prices_resolved(list(by_sec.keys()))
    for sid, tds in by_sec.items():
        hp = hp_map.get(sid) or []
        for td in tds:
            pu = _b1_price_on_date(hp, td)
            if pu is None:
                continue
            try:
                prices[f"{sid}|{td}"] = {"pu": float(pu), "date": td}
            except (TypeError, ValueError):
                continue
    return jsonify({"prices": prices})


# ── Apply: build a single .xlsx and POST upstream ───────────────────────────

def _build_combined_xlsx(rows):
    """Build a SINGLE workbook containing rows from every wallet in the
    batch. The upstream's `Carteira` column already routes rows to the
    correct wallet, and `Data` is per-row, so wallets with different
    `targetDate`s coexist in the same file. Eliminates the N HTTP calls
    that the per-wallet variant used to do.

    Caixa rows: each `r` with `caixa=True` is emitted as a `Caixa = "Sim"`
    line with `Ativo="Caixa"`, `Quant=0`, `PU=0` and the cash amount in
    `SaldoBruto` — same convention used by `/carteira` and `/excecoes`."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(["Data", "Carteira", "Ativo", "Quant", "PU",
               "SaldoBruto", "Caixa", "Moeda"])
    for r in rows:
        is_cash = bool(r.get("caixa"))
        ws.append([
            r.get("date") or "",
            r.get("walletId") or "",
            ("Caixa" if is_cash else (r.get("ativo") or "")),
            (0 if is_cash else (r.get("quantity") or 0)),
            (0 if is_cash else (r.get("pu") or 0)),
            r.get("balance") or 0,
            "Sim" if is_cash else "Não",
            r.get("currencyId") or "",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


@bp.route("/api/repetir-posicoes/apply", methods=["POST"])
def apply_repeat():
    """Group `items[]` by company and POST one .xlsx per company to the
    upstream `unprocessed-security-positions/file` endpoint (the multipart
    form data carries `companyId`, so cross-company uploads must be
    split). Within a company every wallet still rides in a **single**
    file — the operator's "one click, one upload" experience is preserved
    per company.

    This endpoint is used by **both** flows:
      • Default ("Executar"): no preview was generated; every security
        produced by the rule chain is accepted automatically. The
        operator omits `acceptedSecurityIds` on the item (or sets it to
        null) and the apply behaves as a one-click run.
      • Preview-first ("Revisar e confirmar"): the operator vets the
        prévia and confirms; `acceptedSecurityIds` filters the rows to
        what was checked.

    Optional body field `puOverrides`: a `{<walletId>|<securityId>|<targetDate>: pu}`
    map produced by the "Incluir preços B1" button. When a key matches
    the (wallet, security, target) of a row, the override wins over the
    rule-chain output for that row's PU (and the balance is recomputed).
    Rows not present in the map use the rule-chain PU as-is.

    Each wallet also emits a **Caixa** row (`Ativo="Caixa"`, `Caixa="Sim"`,
    `SaldoBruto=new_cash`) when the projected cash is known — same
    convention as `/carteira` and `/excecoes`. Provisions are display-
    only context and are NOT uploaded (they're an out-of-band cash-flow
    expectation, not a position)."""
    body = request.get_json(silent=True) or {}
    try:
        items = _validate_items(body)
    except PermissionError as e:
        return jsonify({"error": str(e)}), 403
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    pu_overrides = body.get("puOverrides") or {}
    if not isinstance(pu_overrides, dict):
        pu_overrides = {}

    # `includeCash` (default True) lets the caller disable the Caixa row
    # if the wallet's caixa is being managed by another routine. Same
    # contract as `pages/carteira.py::apply_edits` — sending nothing
    # for cash means "compute it, include it".
    include_cash = bool(body.get("includeCash", True))

    sec_names = get_security_names()
    company_names = get_company_names()

    # Same precomputed curva PU map as /preview — keeps the apply step
    # deterministic with what the operator saw in the prévia. Filtrado às
    # wallets desta rodada (match estrito por walletId), igual à prévia.
    curva_pu_map = _compute_curva_pu_map(
        [it["targetDate"] for it in items],
        wallet_ids=[it["walletId"] for it in items],
    )

    # rows_by_company: companyId -> [xlsx row dicts]
    rows_by_company = {}
    per_wallet = []
    # Diff log accumulated per wallet — persisted at the end of the run
    # to `data/repeat_positions_logs/<run_id>.json` so operators have an
    # audit trail of every upload's divergence vs target processedPosition.
    diff_log_wallets = []

    for it in items:
        wallet_id   = it["walletId"]
        company_id  = it["companyId"]
        target_date = it["targetDate"]
        preview_data = _build_preview_for_wallet(
            company_id=company_id,
            wallet_id=wallet_id,
            source_date=it["sourceDate"],
            target_date=target_date,
            curva_pu_map=curva_pu_map,
        )

        accepted = it["acceptedSecurityIds"]
        accepted_set = set(accepted) if accepted is not None else None

        # Build the diff entry for the log report *before* we filter to
        # the upload rows — the report should reflect every row the
        # operator authorised (whether or not it ends up in the xlsx
        # for other reasons, e.g. cash-only wallets). Uses the cached
        # `preview_data` so there's no extra DB round-trip.
        diff_log_wallets.append(_build_diff_report(
            preview_data,
            wallet_id=wallet_id,
            company_id=company_id,
            company_name=company_names.get(company_id, company_id),
            source_date=it["sourceDate"],
            target_date=target_date,
            accepted_set=accepted_set,
        ))

        wallet_row_count = 0
        for row in preview_data["rows"]:
            if accepted_set is not None and row["securityId"] not in accepted_set:
                continue
            # Apply PU override (system-prices button) if present. Both the
            # PU and the balance must move together — the upstream re-derives
            # nothing from PU × Quant.
            override_key = f"{wallet_id}|{row['securityId']}|{target_date}"
            pu  = row["repetition"]["pu"]
            qty = row["repetition"]["quantity"]
            override = pu_overrides.get(override_key)
            if override is not None:
                try:
                    pu = float(override)
                except (TypeError, ValueError):
                    pass
            balance = (pu or 0) * (qty or 0)

            # `ativo` carries the row's `unprocessedId` — the same upstream
            # identifier that `/excecoes` writes to the spreadsheet. Falls
            # back to the security's beehusName when the wallet has no
            # `unprocessedSecurityPositions` snapshot at `sourceDate` (and
            # the mapping reverse-lookup turned up nothing), so the upload
            # doesn't silently ship an empty `Ativo` cell.
            ativo = row.get("unprocessedId") or sec_names.get(row["securityId"]) or row["securityName"]
            rows_by_company.setdefault(company_id, []).append({
                "date":       target_date,
                "walletId":   wallet_id,
                "ativo":      ativo,
                "quantity":   qty,
                "pu":         pu,
                "balance":    _round(balance, 2),
                "caixa":      False,
                "currencyId": preview_data["currencyId"],
            })
            wallet_row_count += 1

        # Append the Caixa row for this wallet (one per (wallet,
        # targetDate)) when we have a projected cash value. Skipped
        # when:
        #   • includeCash=False (operator opted out),
        #   • cash.new is None (no cashAccounts data at source — we
        #     don't want to fabricate a zero caixa),
        #   • the wallet contributed zero security rows (nothing to
        #     upload at all; preserves the old "skipped" semantics).
        cash_block = preview_data.get("cash") or {}
        new_cash = cash_block.get("new")
        cash_appended = False
        if include_cash and wallet_row_count > 0 and new_cash is not None:
            rows_by_company.setdefault(company_id, []).append({
                "date":       target_date,
                "walletId":   wallet_id,
                "ativo":      "Caixa",
                "quantity":   0,
                "pu":         0,
                "balance":    _round(new_cash, 2),
                "caixa":      True,
                "currencyId": preview_data["currencyId"],
            })
            cash_appended = True

        per_wallet.append({
            "walletId":    wallet_id,
            "walletName":  preview_data["walletName"],
            "companyId":   company_id,
            "companyName": company_names.get(company_id, company_id),
            "targetDate":  target_date,
            "rows":        wallet_row_count,
            "cashSent":    cash_appended,
            "status":      "ok" if wallet_row_count else "skipped",
        })

    if not rows_by_company:
        return jsonify({
            "uploaded": False,
            "results":  per_wallet,
            "error":    "nenhum ativo aceito",
        }), 400

    # One .xlsx per company — short-circuit on auth error so we don't keep
    # banging the upstream after the token expires mid-batch.
    timestamp  = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    uploads    = []
    auth_error = None

    for cid, rows in rows_by_company.items():
        xlsx     = _build_combined_xlsx(rows)
        filename = f"repeat_{cid}_{timestamp}.xlsx"
        try:
            up = upload_unprocessed_security_positions_file(
                company_id=cid,
                file_bytes=xlsx,
                filename=filename,
            )
            uploads.append({
                "companyId":   cid,
                "companyName": company_names.get(cid, cid),
                "status":      "ok",
                "rows":        len(rows),
                "filename":    filename,
                "upstream":    up,
            })
        except BeehusAuthError as e:
            auth_error = {"error": str(e), "status": getattr(e, "status", None)}
            uploads.append({
                "companyId":   cid,
                "companyName": company_names.get(cid, cid),
                "status":      "auth_error",
                "error":       str(e),
            })
            break
        except BeehusAPIError as e:
            uploads.append({
                "companyId":       cid,
                "companyName":     company_names.get(cid, cid),
                "status":          "error",
                "error":           str(e),
                "upstream_status": getattr(e, "status", None),
                "upstream_body":   getattr(e, "body", None),
            })

    failed     = [u for u in uploads if u["status"] not in ("ok",)]
    total_rows = sum(len(r) for r in rows_by_company.values())

    # Persist the diff log. The runId follows the upload timestamp so
    # operators can correlate the .xlsx filenames with the log file
    # without an extra lookup. UUID suffix keeps two parallel runs in
    # the same second from colliding.
    import uuid
    run_id = f"repeat_{timestamp}_{uuid.uuid4().hex[:8]}"
    diff_totals = {
        "wallets":             len(diff_log_wallets),
        "diverged":            0,
        "missingInTarget":     0,
        "missingInRepetition": 0,
        "matched":             0,
        "totalBalanceDiff":    0.0,
        "withTarget":          0,
    }
    for w in diff_log_wallets:
        s = w.get("summary") or {}
        for k in ("matched", "diverged", "missingInTarget", "missingInRepetition"):
            try:
                diff_totals[k] += int(s.get(k) or 0)
            except (TypeError, ValueError):
                pass
        try:
            diff_totals["totalBalanceDiff"] += float(s.get("totalBalanceDiff") or 0)
        except (TypeError, ValueError):
            pass
        if s.get("hasTarget"):
            diff_totals["withTarget"] += 1
    diff_totals["totalBalanceDiff"] = _round(diff_totals["totalBalanceDiff"], 2)

    diff_report = {
        "runId":     run_id,
        "createdAt": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "uploads":   [{
            "companyId":   u.get("companyId"),
            "companyName": u.get("companyName"),
            "status":      u.get("status"),
            "rows":        u.get("rows"),
            "filename":    u.get("filename"),
            "error":       u.get("error"),
        } for u in uploads],
        "totalRows":  total_rows,
        "uploaded":   not failed,
        "totals":     diff_totals,
        "wallets":    diff_log_wallets,
    }
    persisted = _persist_diff_log(diff_report)

    response = {
        "uploaded":   not failed,
        "uploads":    uploads,
        "results":    per_wallet,
        "totalRows":  total_rows,
        "runId":      run_id if persisted else None,
        "diffTotals": diff_totals,
        # Inline the per-wallet diff so the UI can render the log
        # without an extra round-trip — the persisted file is the
        # canonical copy for later audit, but the immediate response
        # already carries everything needed for the post-run modal.
        "diffReport": diff_report,
    }
    if auth_error:
        return jsonify({**response, **auth_error}), 401
    if failed:
        return jsonify(response), 502
    return jsonify(response)


@bp.route("/api/repetir-posicoes/logs/<run_id>")
def fetch_log(run_id):
    """Return a persisted diff log report. Used by the UI's "Ver log"
    affordance and by operators who want to audit a past run
    (`data/repeat_positions_logs/<runId>.json`).

    Defensive parsing: the run_id is matched against `_RUN_ID_RE`
    before the filesystem join, so a crafted URL like
    `../../../etc/passwd` is rejected by the regex rather than the
    filesystem."""
    rid = (run_id or "").strip()
    if not _RUN_ID_RE.match(rid):
        return jsonify({"error": "invalid runId"}), 400
    path = os.path.join(_LOGS_DIR, f"{rid}.json")
    if not os.path.isfile(path):
        return jsonify({"error": "log não encontrado"}), 404
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        return jsonify({"error": f"erro ao ler log: {e}"}), 500
    return jsonify(data)


@bp.route("/api/repetir-posicoes/logs")
def list_logs():
    """List persisted diff log reports (most recent first). Returns
    just a manifest — the full report still needs a follow-up GET on
    `/logs/<runId>` to keep this listing cheap when there are many
    runs. Limit defaults to 50; the UI never asks for more."""
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 500))

    if not os.path.isdir(_LOGS_DIR):
        return jsonify({"logs": []})

    entries = []
    try:
        names = os.listdir(_LOGS_DIR)
    except OSError:
        return jsonify({"logs": []})
    for name in names:
        if not name.endswith(".json"):
            continue
        rid = name[:-5]
        if not _RUN_ID_RE.match(rid):
            continue
        path = os.path.join(_LOGS_DIR, name)
        try:
            st = os.stat(path)
        except OSError:
            continue
        # Cheap metadata pass — read the header fields only so listing
        # a hundred logs doesn't slurp megabytes of differences arrays.
        meta = {"runId": rid, "mtime": int(st.st_mtime)}
        try:
            with open(path, "r", encoding="utf-8") as f:
                doc = json.load(f) or {}
            meta["createdAt"]  = doc.get("createdAt")
            meta["uploaded"]   = bool(doc.get("uploaded"))
            # `mode` distingue prévia (`"preview"`) de aplicação (default,
            # ausente nos logs de `/apply`). UI usa pra mostrar
            # "prévia" em vez do "falhou" enganoso — uma rodada de
            # prévia nem tentou subir nada, então não cabe "falhou".
            meta["mode"]       = doc.get("mode") or "apply"
            meta["totalRows"]  = doc.get("totalRows")
            meta["totals"]     = doc.get("totals") or {}
            up = doc.get("uploads") or []
            meta["companies"]  = sorted({u.get("companyName") or u.get("companyId") or "" for u in up if u})
            meta["walletCount"] = len(doc.get("wallets") or [])
        except (OSError, json.JSONDecodeError):
            pass
        entries.append(meta)
    entries.sort(key=lambda e: e.get("mtime") or 0, reverse=True)
    return jsonify({"logs": entries[:limit]})
