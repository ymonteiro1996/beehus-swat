"""Leitores das correções pendentes em disco (extraído de `pages/correcoes.py`).

Apenas as funções de LEITURA usadas por `conciliacao_unprocessed` e
`conciliacao_shared` — sem MongoDB, sem escrita. As correções ficam em
`data/correcoes/<companyId>/<YYYY-MM-DD>/<walletId>.json`.
"""
import json
import os
import re

# Raiz da árvore de correções (igual ao projeto original: data/correcoes).
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "correcoes"))

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SAFE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Path helpers ──────────────────────────────────────────────────────────────

def _safe(segment):
    """Rejeita qualquer segmento de caminho que não seja alfanumérico/dash/underscore."""
    if not segment or not _SAFE_ID_RE.match(str(segment)):
        raise ValueError(f"invalid segment: {segment!r}")
    return str(segment)


def _safe_date(date):
    if not date or not _SAFE_DATE_RE.match(str(date)):
        raise ValueError(f"invalid date: {date!r}")
    return str(date)


def _company_dir(company_id):
    return os.path.join(_ROOT, _safe(company_id))


def _wallet_file(company_id, date, wallet_id):
    return os.path.join(_ROOT, _safe(company_id), _safe_date(date), f"{_safe(wallet_id)}.json")


# ── Leitores ──────────────────────────────────────────────────────────────────

def load_all_pending_provisions_by_wallet(company_id):
    """Varre toda a árvore de correções da empresa numa passada, devolvendo
    `{wallet_id: [provisions]}`. Cada provisão ganha `acceptanceDate` (pasta de
    aceite). Provisões `inputed=True` (já enviadas ao upstream) são excluídas.
    Retorna `{}` em qualquer erro de I/O / caminho inválido."""
    if not company_id:
        return {}
    try:
        _safe(company_id)
    except ValueError:
        return {}
    comp_dir = _company_dir(company_id)
    if not os.path.isdir(comp_dir):
        return {}
    try:
        date_names = os.listdir(comp_dir)
    except OSError:
        return {}
    out = {}
    for d in date_names:
        if not _SAFE_DATE_RE.match(d):
            continue
        date_dir = os.path.join(comp_dir, d)
        try:
            files = os.listdir(date_dir)
        except OSError:
            continue
        for name in files:
            if not name.endswith(".json"):
                continue
            wid = name[:-len(".json")]
            if not _SAFE_ID_RE.match(wid):
                continue
            try:
                with open(os.path.join(date_dir, name), "r", encoding="utf-8") as f:
                    blob = json.load(f)
            except (OSError, json.JSONDecodeError):
                continue
            bucket = out.setdefault(wid, [])
            for p in (blob.get("provisions") or []):
                if p.get("inputed"):
                    continue
                row = dict(p)
                row["acceptanceDate"] = d
                bucket.append(row)
    return out


def load_all_pending_provisions(company_id, wallet_id):
    """Toda provisão pendente de uma carteira em todas as pastas de aceite (sem
    filtro de data). Cada linha ganha `acceptanceDate`. Exclui `inputed=True`."""
    if not company_id or not wallet_id:
        return []
    try:
        _safe(company_id); _safe(wallet_id)
    except ValueError:
        return []
    comp_dir = _company_dir(company_id)
    if not os.path.isdir(comp_dir):
        return []
    results = []
    try:
        date_names = os.listdir(comp_dir)
    except OSError:
        return []
    for d in date_names:
        if not _SAFE_DATE_RE.match(d):
            continue
        wallet_file = os.path.join(comp_dir, d, f"{_safe(wallet_id)}.json")
        if not os.path.isfile(wallet_file):
            continue
        try:
            with open(wallet_file, "r", encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for p in (blob.get("provisions") or []):
            if p.get("inputed"):
                continue
            row = dict(p)
            row["acceptanceDate"] = d
            results.append(row)
    return results


def load_corrections_for_wallet(company_id, date, wallet_id):
    """`(transactions, provisions, deletions)` salvos para (companyId, date,
    walletId). Exclui transações/provisões `inputed=True`. Retorna ([], [], [])
    em qualquer entrada ausente/ caminho inválido/ arquivo ilegível."""
    if not company_id or not date or not wallet_id:
        return [], [], []
    try:
        _safe(company_id); _safe_date(date); _safe(wallet_id)
    except ValueError:
        return [], [], []
    path = _wallet_file(company_id, date, wallet_id)
    if not os.path.isfile(path):
        return [], [], []
    try:
        with open(path, "r", encoding="utf-8") as f:
            blob = json.load(f)
    except (OSError, json.JSONDecodeError):
        return [], [], []
    txns_pending  = [t for t in (blob.get("transactions") or []) if not t.get("inputed")]
    provs_pending = [p for p in (blob.get("provisions") or []) if not p.get("inputed")]
    return txns_pending, provs_pending, (blob.get("deletions") or [])


def load_pending_execution_prices(company_id, wallet_id):
    """Linhas `executionPrices` aceitas para a carteira em todas as pastas de
    aceite. Cada linha ganha `acceptanceDate`. Pipelines devem filtrar por
    `inputed == False` (linhas já enviadas ao upstream estão baked-in)."""
    if not company_id or not wallet_id:
        return []
    try:
        _safe(company_id); _safe(wallet_id)
    except ValueError:
        return []
    comp_dir = _company_dir(company_id)
    if not os.path.isdir(comp_dir):
        return []
    results = []
    try:
        date_names = os.listdir(comp_dir)
    except OSError:
        return []
    for d in date_names:
        if not _SAFE_DATE_RE.match(d):
            continue
        wallet_file = os.path.join(comp_dir, d, f"{_safe(wallet_id)}.json")
        if not os.path.isfile(wallet_file):
            continue
        try:
            with open(wallet_file, "r", encoding="utf-8") as f:
                blob = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        for ep in (blob.get("executionPrices") or []):
            row = dict(ep)
            row["acceptanceDate"] = d
            results.append(row)
    return results
