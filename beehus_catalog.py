"""API-backed catalog for `securities` and `securityMappings`.

This module is the **read seam** that lets the dashboard stop querying the
`securities` and `securityMappings` Mongo collections directly. It fetches the
data over HTTP from the Beehus API (`GET /beehus/securities`,
`GET /beehus/financial/security-mappings`) and caches it in-process with a
5-minute TTL — the same cadence as `db.get_security_names` — because both
collections are small and change rarely.

API as the single source of truth
----------------------------------
The Beehus API is the **only** source for the collections this module serves.
Each helper is self-validating: if the API call fails, returns a non-list, or
yields a structurally empty index (no usable ids), the helper returns an
empty/None result and logs a warning — it does **not** read Mongo. The
transitional Mongo fallback was removed once the API shapes were validated in
production; when the API cannot serve the data the dashboard simply shows
nothing ("quando não houver, não mostrar"). securityPrices
(`filtered-security-price`) and securityEvents (`security-events`) now have
endpoints and are served through this seam too. What still reads Mongo
*directly in the pages* are the genuinely-uncovered reads (issues `/detail`,
navPackages diagnostics, processedPosition for the Identificar tools), the
console transaction write, and offline ETL/classifier scans — gaps outside
this seam, not fallbacks.
"""
import datetime
import logging
import threading
import time

import concurrent.futures

from beehus_api import (list_securities, get_security_mappings,
                        list_companies, list_entities, list_provisions,
                        get_processed_position, get_unprocessed_security_positions,
                        get_preprocessing_status, list_transactions,
                        get_nav_contribution, get_nav_results,
                        partner_wallets, list_groupings, filtered_security_price,
                        security_events, list_execution_prices, get_security)
from beehus_api.exceptions import BeehusAPIError, BeehusAuthError

_log = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 300       # após isso, revalida (em background se houver valor)
_STALE_MAX_SECONDS = 3600      # além disso, NÃO serve stale: bloqueia e recarrega
_cache_lock = threading.RLock()
_cache_store = {}   # key -> (inserted_at_monotonic, value)
_refreshing = set()  # chaves com refresh em background em andamento (dedup)


def _bg_refresh(key, loader):
    """Recarrega `key` em segundo plano. Só substitui o cache em sucesso
    NÃO-VAZIO — assim um loader que falha/retorna vazio (API fora, 429) mantém o
    último valor bom em vez de zerar a tela. Roda `loader()` FORA do lock para
    não bloquear quem está servindo o valor stale."""
    try:
        value = loader()
    except Exception as exc:  # noqa: BLE001
        _log.warning("bg refresh de %s falhou (%s); mantém valor anterior.", key, exc)
        value = None
    with _cache_lock:
        _refreshing.discard(key)
        if value:
            _cache_store[key] = (time.monotonic(), value)


def _cached_ttl(key, loader):
    """Cache TTL com stale-while-revalidate.

    - Dentro do TTL → serve o valor cacheado.
    - Vencido, mas com idade < `_STALE_MAX_SECONDS` → serve o valor stale NA HORA
      e dispara UM refresh em background por chave (dedup via `_refreshing`).
      Nenhuma requisição bloqueia no re-warm (ex.: o catálogo de securities, que
      leva ~20-35s no upstream).
    - Sem valor (a frio) OU stale além do teto → carrega de forma síncrona
      (bloqueia uma vez), com double-checked locking contra a estampida.

    O refresh é disparado por requisição (não por timer): app ocioso não recarrega.
    `invalidate()`/`refresh()` (botão "Atualizar") continuam forçando recarga a
    frio. Callers NÃO devem mutar o valor retornado (pode ser servido stale)."""
    entry = _cache_store.get(key)
    if entry is not None:
        age = time.monotonic() - entry[0]
        if age < _CACHE_TTL_SECONDS:
            return entry[1]                       # fresco
        if age < _STALE_MAX_SECONDS:
            # stale aceitável → serve agora, revalida atrás (1 refresh por chave).
            with _cache_lock:
                if key not in _refreshing:
                    _refreshing.add(key)
                    threading.Thread(
                        target=_bg_refresh, args=(key, loader),
                        name=f"catalog-refresh:{key}", daemon=True,
                    ).start()
            return entry[1]
        # stale demais → cai para a recarga síncrona abaixo.
    with _cache_lock:
        entry = _cache_store.get(key)
        if entry is not None and (time.monotonic() - entry[0]) < _CACHE_TTL_SECONDS:
            return entry[1]
        value = loader()
        _cache_store[key] = (time.monotonic(), value)
        return value


def invalidate(key=None):
    """Drop cached entries (e flags de refresh). `key=None` clears everything."""
    with _cache_lock:
        if key is None:
            _cache_store.clear()
            _refreshing.clear()
        else:
            _cache_store.pop(key, None)
            _refreshing.discard(key)


# ── id normalisation ─────────────────────────────────────────────────────────

def id_str(v):
    """Public alias of `_idstr` for callers that need to normalise a possibly
    populated id (string / ObjectId / {"$oid"} / {"_id"}) to its string form."""
    return _idstr(v)


def _idstr(v):
    """Normalise a Mongo/JSON id to its plain string form.

    Handles plain strings, ObjectId, and extended-JSON shapes like
    `{"$oid": "..."}` or a populated sub-doc `{"_id": ...}` that the API may
    return for reference fields."""
    if v is None:
        return ""
    if isinstance(v, dict):
        if "$oid" in v:
            return str(v["$oid"])
        if "_id" in v:
            return _idstr(v["_id"])
        return ""
    return str(v)


# ── securities index ─────────────────────────────────────────────────────────

def _index_from_docs(docs):
    """Build `{id_str: doc}` from a list of security docs. Returns {} if the
    input is not a usable list of id-bearing docs (empty result; no Mongo read).

    Each doc's `_id` is normalised to its plain string form in place, so a
    consumer that re-derives the key via `str(doc["_id"])` (the pattern the
    migrated `find({"_id": {"$in": …}})` callers use) gets the same value as
    the dict key — regardless of whether the API sent a string, an ObjectId,
    or extended-JSON `{"$oid": …}`."""
    if not isinstance(docs, list):
        return {}
    out = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        sid = _idstr(d.get("_id"))
        if sid:
            d["_id"] = sid
            out[sid] = d
    return out


def _load_securities_index():
    """Fetch the full securities catalog from the API, normalised to
    `{id_str: doc}`. Returns {} on failure / invalid shape (no Mongo fallback)."""
    try:
        idx = _index_from_docs(list_securities())
        # Self-validation: a real catalog always has at least one beehusName.
        # If the API returns structurally-valid docs but the field is gone
        # (schema drift), treat as invalid (return {}) instead of serving
        # blank names everywhere.
        if idx and any(d.get("beehusName") for d in idx.values()):
            return idx
        _log.warning("list_securities() returned an empty/invalid index.")
    except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
        _log.warning("list_securities() failed (%s).", exc)
    return {}


def securities_index():
    """`{security_id_str: full_security_doc}` for the whole catalog. Cached
    5 min — do not mutate the returned dict or its docs."""
    return _cached_ttl("securities_index", _load_securities_index)


def securities_by_ids(ids):
    """`{id_str: doc}` for the requested security ids (any mix of str/ObjectId;
    normalised to str). Ids not in the catalog are simply absent. Replaces the
    per-page `db.securities.find({"_id": {"$in": ids}})`."""
    want = {_idstr(i) for i in (ids or []) if i is not None}
    if not want:
        return {}
    idx = securities_index()
    return {sid: idx[sid] for sid in want if sid in idx}


def security_doc(security_id):
    """Single security doc or None. Replaces `db.securities.find_one({_id})`."""
    return securities_index().get(_idstr(security_id))


def security_by_main_id(main_id):
    """Resolve a security by its `mainId` (used by precificação's upload
    fallback). Returns the doc or None."""
    if not main_id:
        return None
    target = str(main_id).strip()
    for d in securities_index().values():
        if str(d.get("mainId") or "").strip() == target:
            return d
    return None


def security_names():
    """`{security_id_str: beehusName_str}`. Drop-in for db.get_security_names."""
    return {sid: (d.get("beehusName") or "")
            for sid, d in securities_index().items()}


def all_securities():
    """List of every security doc in the catalog. Replaces full-collection
    scans `db.securities.find({}, ...)` (matcher cache, classifiers)."""
    return list(securities_index().values())


def search(query, limit=20):
    """Securities whose `beehusName` or `mainId` contains `query`
    (case-insensitive), or whose id equals `query`. Replaces the precificação
    typeahead `find({"$or": [regex beehusName, regex mainId, _id]}).limit(20)`."""
    q = (query or "").strip()
    if not q:
        return []
    ql = q.lower()
    out = []
    for d in securities_index().values():
        name = str(d.get("beehusName") or "")
        mid = str(d.get("mainId") or "")
        if ql in name.lower() or ql in mid.lower() or _idstr(d.get("_id")) == q:
            out.append(d)
            if len(out) >= limit:
                break
    return out


def _benchmark_visible(doc, company_id):
    """A `securityType == "benchmark"` doc is visible to a company when its
    `companyIds` is missing/empty (global) or contains the company. With
    `company_id=None`, all benchmarks match (company-agnostic, e.g. CDI)."""
    if (doc.get("securityType") or "") != "benchmark":
        return False
    if company_id is None:
        return True
    cids = doc.get("companyIds")
    if not cids:
        return True
    return str(company_id) in [str(c) for c in cids]


def benchmarks(company_id=None):
    """List of benchmark securities visible to `company_id` (None = all).
    Replaces `db.securities.find({"securityType": "benchmark", "$or": [...]})`."""
    return [d for d in securities_index().values()
            if _benchmark_visible(d, company_id)]


# ── securityMappings (per company) ───────────────────────────────────────────

def _normalise_mapping_doc(raw, company_id):
    """Coerce an API security-mappings payload into a single
    `{"_id", "companyId", "mappings"}` doc for `company_id`, or None.

    The endpoint may return a single doc or a list of docs (one per company)."""
    candidates = []
    if isinstance(raw, list):
        candidates = [d for d in raw if isinstance(d, dict)]
    elif isinstance(raw, dict):
        # Either the doc itself, or an envelope like {"data": [...]} / {"data": {...}}
        if "mappings" in raw or "_id" in raw:
            candidates = [raw]
        else:
            data = raw.get("data")
            if isinstance(data, list):
                candidates = [d for d in data if isinstance(d, dict)]
            elif isinstance(data, dict):
                candidates = [data]
    if not candidates:
        return None
    cid = str(company_id)
    for d in candidates:
        if _idstr(d.get("companyId")) == cid:
            return d
    # If the API already scoped by companyId there may be exactly one doc.
    return candidates[0] if len(candidates) == 1 else None


def _load_mapping_doc(company_id):
    try:
        doc = _normalise_mapping_doc(
            get_security_mappings(company_id=company_id), company_id)
        if doc is not None:
            return doc
        _log.warning("get_security_mappings(%s) yielded no usable doc.", company_id)
    except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
        _log.warning("get_security_mappings(%s) failed (%s).", company_id, exc)
    return None


def security_mappings_doc(company_id):
    """The `securityMappings` doc for a company (`{_id, companyId, mappings}`)
    or None. Drop-in for `db.securityMappings.find_one({"companyId": cid})`.
    Cached 5 min per company — do not mutate."""
    if not company_id:
        return None
    return _cached_ttl(f"mappings::{company_id}",
                       lambda: _load_mapping_doc(company_id))


def security_mapping_id(company_id):
    """The Mongo `_id` (str) of a company's securityMappings doc, or None.
    Needed as the path param for the PATCH update endpoint."""
    doc = security_mappings_doc(company_id)
    return _idstr(doc.get("_id")) if doc else None


# ── companies / entities (reference data) ────────────────────────────────────

def _names_index(docs):
    """`{id_str: name_str}` from a list of `{_id, name}` docs. {} if not usable."""
    if not isinstance(docs, list):
        return {}
    out = {}
    for d in docs:
        if not isinstance(d, dict):
            continue
        rid = _idstr(d.get("_id"))
        if rid:
            out[rid] = (d.get("name") or "")
    return out


def _load_company_names():
    try:
        idx = _names_index(list_companies())
        if idx and any(idx.values()):
            return idx
        _log.warning("list_companies() empty/invalid.")
    except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
        _log.warning("list_companies() failed (%s).", exc)
    return {}


def _load_entity_names():
    try:
        idx = _names_index(list_entities())
        if idx and any(idx.values()):
            return idx
        _log.warning("list_entities() empty/invalid.")
    except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
        _log.warning("list_entities() failed (%s).", exc)
    return {}


def company_names():
    """`{company_id_str: name_str}`. Drop-in for db.get_company_names."""
    idx = _cached_ttl("company_names", _load_company_names)
    if not idx:
        # Token ausente/expirado (ou falha transitória) faz `_load_company_names`
        # devolver {}. Não deixe esse vazio "grudar" pelos 5 min do TTL: solte a
        # entrada para que a próxima chamada tente de novo assim que o operador
        # re-colar o token — senão o dropdown de empresas ficaria vazio por até
        # 5 min mesmo já com token válido.
        invalidate("company_names")
    return idx


def entity_names():
    """`{entity_id_str: name_str}`. Drop-in for db.get_entity_names."""
    return _cached_ttl("entity_names", _load_entity_names)


def all_security_mappings():
    """List of every `securityMappings` doc (one per company). Replaces the
    all-company scan `db.securityMappings.find({})` in the classifiers. Cached
    5 min; returns [] on API failure / invalid shape (no Mongo fallback)."""
    def _load():
        try:
            raw = get_security_mappings()  # no company filter → all docs
            docs = None
            if isinstance(raw, list):
                docs = [d for d in raw if isinstance(d, dict)]
            elif isinstance(raw, dict):
                data = raw.get("data")
                if isinstance(data, list):
                    docs = [d for d in data if isinstance(d, dict)]
                elif "mappings" in raw or "_id" in raw:
                    docs = [raw]
            if docs:
                return docs
            _log.warning("get_security_mappings() (all) yielded no usable docs.")
        except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
            _log.warning("get_security_mappings() (all) failed (%s).", exc)
        return []
    return _cached_ttl("all_mappings", _load)


# ── wallets (referência; índice global via partner_wallets, lazy + TTL 5 min) ─
# `partner_wallets(companyId)` é POR EMPRESA; os helpers de `db.py`
# (resolve_wallet, nomes, moedas, valid_ids, build_wallet_map) varrem TODAS as
# carteiras. Montamos um índice global {walletId: doc} fazendo fan-out de
# partner_wallets sobre `all_company_ids()` (~19 chamadas — barato vs. o limite
# de 429; nada a ver com o warm de nav, que eram 3k+ entidades). RESILIENTE:
# empresa que falha é pulada e logada (índice parcial), e só devolve {} quando
# NENHUMA empresa responde (ex.: sem token). Antes era tudo-ou-nada — uma única
# empresa falhando zerava o índice global e, com ele, os totais cross-empresa do
# Painel (colunas processadas/NAV/published viravam "—"). `companyId`/`entityId`
# vêm POPULADOS (dict) na API → normalizados p/ id-string (forma do Mongo, que os
# callers usam via str(w.get("companyId"))).

def _normalize_wallet_doc(w):
    """Normaliza um doc de carteira da API: `_id`/`companyId`/`entityId`/
    `currencyId` → id-string. Mantém name/accountCode/currency/trashed/etc.
    Não muta o original.

    A API NÃO traz `currencyId` top-level, mas traz `currency` (o CÓDIGO da
    moeda da carteira, ex. "BRL") em toda carteira. Como todos os consumidores
    tratam `currencyId` como código de moeda (str, default "BRL" — ver
    `db.resolve_wallet`, o upload xlsx de carteira/repetir e
    `excecoes._wallet_currency`), derivamos `currencyId := currency` quando
    ausente. Isso completa o doc da API e elimina o gap-read no Mongo de
    `resolve_wallet`. (Também corrige um bug latente: o Mongo às vezes guardava
    `currencyId` como ObjectId, e `str(ObjectId)` virava hex no upload.)"""
    d = dict(w)
    d["_id"] = _idstr(w.get("_id"))
    for k in ("companyId", "entityId", "currencyId"):
        if isinstance(d.get(k), dict):
            d[k] = _idstr(d[k])
    if not d.get("currencyId") and d.get("currency"):
        d["currencyId"] = d["currency"]
    return d


def _wallets_index_from_api():
    """`{walletId_str: doc_normalizado}` de TODAS as empresas via partner_wallets.

    Resiliente: empresas que falham (4xx/timeout/None) são PULADAS e logadas, não
    derrubam o índice inteiro — uma única empresa problemática zerava todos os
    totais cross-empresa do Painel (colunas processadas/NAV/published viravam
    "—"). Retorna {} só quando NENHUMA empresa respondeu (ex.: sem token)."""
    cids = all_company_ids()
    if not cids:
        return {}
    out = {}
    failed = []

    def _one(cid):
        try:
            r = partner_wallets(cid)
            return r if isinstance(r, list) else None
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            return None

    workers = min(_NAV_WARM_WORKERS, max(1, len(cids)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, r in zip(cids, ex.map(_one, cids)):
            if r is None:
                failed.append(cid)
                continue
            for w in r:
                if isinstance(w, dict):
                    nd = _normalize_wallet_doc(w)
                    if nd.get("_id"):
                        out[nd["_id"]] = nd
    if failed:
        _log.warning("wallets_index: %d/%d empresas falharam em partner_wallets; "
                     "índice parcial (faltam: %s).", len(failed), len(cids), failed)
    return out


def _load_wallets_index():
    try:
        idx = _wallets_index_from_api()
    except Exception as exc:  # noqa: BLE001
        _log.warning("wallets index via API crashed (%s).", exc)
        idx = {}
    if not idx:
        _log.warning("wallets index via API vazio/falhou.")
    return idx


def wallets_index():
    """`{walletId_str: wallet_doc}` de todas as carteiras. Cacheado 5 min — não
    mutar. Drop-in para os scans `db.wallets.find({})`."""
    idx = _cached_ttl("wallets_index", _load_wallets_index)
    if not idx:
        # Não deixe um vazio TRANSITÓRIO (token ainda não pronto no startup,
        # token recém-colado, ou hiccup/429 no fan-out por empresa) grudar pelos
        # 5 min do TTL: um índice vazio zera os totais (denominadores) das colunas
        # de progresso do Painel — Posições/NAV/Published viram "—" mesmo já com
        # token válido. Espelha company_names(): solta a entrada p/ a próxima
        # chamada re-tentar assim que a API responder.
        invalidate("wallets_index")
    return idx


def wallet_doc(wallet_id):
    """Doc de uma carteira por id, ou None. Substitui `resolve_wallet` no
    caminho API (db.resolve_wallet delega a isto). O doc já carrega
    `currencyId` (derivado de `currency` em `_normalize_wallet_doc`) — não há
    mais gap de campo nem leitura no Mongo."""
    if not wallet_id:
        return None
    return wallets_index().get(_idstr(wallet_id))


def wallet_names():
    """`{walletId_str: name_str}`. Drop-in para db.get_wallet_names."""
    return {wid: (d.get("name") or "") for wid, d in wallets_index().items()}


def wallet_currencies():
    """`{walletId_str: currency_str}` das carteiras com `currency`. Drop-in para
    db.get_wallet_currencies."""
    return {wid: (d.get("currency") or "")
            for wid, d in wallets_index().items() if d.get("currency")}


def valid_wallet_ids():
    """Set de walletId_str existentes. Drop-in para db.valid_wallet_ids."""
    return set(wallets_index().keys())


def wallets_for_company(company_id):
    """`{walletId_str: name_str}` das carteiras de UMA empresa via
    `partner_wallets` (1 chamada — não monta o índice global de ~19 empresas).
    Para telas que listam as carteiras de uma empresa (vazio se a API falhar)."""
    if not company_id:
        return {}
    try:
        out = partner_wallets(company_id)
        if isinstance(out, list):
            return {_idstr(w.get("_id")): (w.get("name") or _idstr(w.get("_id")))
                    for w in out if isinstance(w, dict) and _idstr(w.get("_id"))}
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        pass
    return {}


def wallets_in_company(company_id):
    """Lista dos docs (índice) das carteiras de uma empresa. Para queries
    `db.wallets.find({companyId}, ...)` que precisam de name/_id/entityId/
    currency/currencyId (este derivado de `currency` no índice — ver
    `_normalize_wallet_doc`)."""
    cid = _idstr(company_id)
    if not cid:
        return []
    return [d for d in wallets_index().values() if _idstr(d.get("companyId")) == cid]


def wallet_company_map(wallet_ids):
    """`{walletId_str: companyId_str}` para uma lista de ids (lookups O(1) no
    índice cacheado — NÃO é N+1). Ids ausentes do índice saem do dict."""
    out = {}
    for w in (wallet_ids or []):
        d = wallet_doc(w)
        if d:
            out[_idstr(w)] = _idstr(d.get("companyId"))
    return out


def wallet_pairs():
    """`(wallet_to_pair, pair_total)` sobre TODAS as carteiras: cada carteira →
    `(companyId, entityId)` e a contagem por par. Drop-in para o corpo de
    db.build_wallet_map (caminho sem filtro)."""
    wallet_to_pair, pair_total = {}, {}
    for wid, d in wallets_index().items():
        cid = _idstr(d.get("companyId"))
        eid = _idstr(d.get("entityId"))
        if cid and eid:
            wallet_to_pair[wid] = (cid, eid)
            pair_total[(cid, eid)] = pair_total.get((cid, eid), 0) + 1
    return wallet_to_pair, pair_total


# ── groupings (referência; índice global via list_groupings, lazy + TTL 5 min) ─
# Mesmo padrão do índice de carteiras: `list_groupings(companyId)` é por empresa,
# mas `db.get_grouping_index()` precisa de TODOS os agrupamentos. Fan-out sobre
# all_company_ids() (~19 chamadas), RESILIENTE (empresa que falha é pulada e
# logada; {} só quando nenhuma responde). Produz a MESMA forma de db.get_grouping_index:
# {gid: {name, companyId, trashed, walletIds}}. `_idstr` normaliza tanto o
# `walletId` POPULADO da API quanto o id cru do Mongo — um builder serve aos dois.

def _grouping_entry(g):
    """`{name, companyId, trashed, walletIds}` a partir de um doc de agrupamento
    (API populado OU Mongo cru — `_idstr` cobre os dois)."""
    return {
        "name": (g.get("name") or ""),
        "companyId": _idstr(g.get("companyId")),
        "trashed": bool(g.get("trashed")),
        "walletIds": [_idstr(w.get("walletId"))
                      for w in (g.get("wallets") or [])
                      if isinstance(w, dict) and _idstr(w.get("walletId"))],
    }


def _grouping_index_from_api():
    """`{grouping_id_str: entry}` de TODAS as empresas via list_groupings.

    Resiliente (mesma lógica de `_wallets_index_from_api`): empresa que falha é
    pulada e logada, não zera o índice inteiro. {} só quando NENHUMA responde."""
    cids = all_company_ids()
    if not cids:
        return {}
    out = {}
    failed = []

    def _one(cid):
        try:
            r = list_groupings(cid)
            return r if isinstance(r, list) else None
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            return None

    workers = min(_NAV_WARM_WORKERS, max(1, len(cids)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, r in zip(cids, ex.map(_one, cids)):
            if r is None:
                failed.append(cid)
                continue
            for g in r:
                if isinstance(g, dict):
                    gid = _idstr(g.get("_id"))
                    if gid:
                        out[gid] = _grouping_entry(g)
    if failed:
        _log.warning("grouping_index: %d/%d empresas falharam em list_groupings; "
                     "índice parcial (faltam: %s).", len(failed), len(cids), failed)
    return out


def _load_grouping_index():
    try:
        idx = _grouping_index_from_api()
    except Exception as exc:  # noqa: BLE001
        _log.warning("grouping index via API crashed (%s).", exc)
        idx = {}
    if not idx:
        _log.warning("grouping index via API vazio/falhou.")
    return idx


def grouping_index():
    """`{grouping_id_str: {name, companyId, trashed, walletIds}}` de TODOS os
    agrupamentos. Cacheado 5 min — não mutar. Drop-in para db.get_grouping_index."""
    idx = _cached_ttl("grouping_index", _load_grouping_index)
    if not idx:
        # Mesmo motivo de wallets_index(): um vazio transitório não pode grudar
        # pelo TTL — zera os totais de agrupamentos das colunas NAV Grouping /
        # Published do Painel ("—"). Solta a entrada p/ re-tentar.
        invalidate("grouping_index")
    return idx


# ── provisions (LIVE — NÃO cacheado; mudam durante a conciliação) ────────────
# Estratégia de paridade: buscar uma JANELA LARGA p/ trás (initialDate desde
# 2000-01-01 até a data alvo) e aplicar o filtro estrito no cliente. Assim o
# resultado independe da semântica exata de janela do endpoint, desde que ele
# devolva um SUPERSET (provisões iniciadas até a data). Vazio em erro/sem token
# (a UI não mostra). Campos consumidos: walletId, securityId, balance, amount, initialDate,
# liquidationDate, provisionType, provisionSource, type, description, currencyId.

def _dedup_by_id(docs):
    """Drop duplicate provision docs sharing the same `_id`. Mongo's live
    collection has one doc per `_id`; this guarantees the same invariant on the
    API path so the client-side aggregations can never double-count a provision
    even if the upstream ever returns historical/duplicate copies."""
    seen = set()
    out = []
    for p in docs:
        pid = _idstr(p.get("_id"))
        if pid:
            if pid in seen:
                continue
            seen.add(pid)
        out.append(p)
    return out


def _normalize_provision(p):
    """A API devolve `walletId`/`securityId`/`companyId` POPULADOS como dict
    (`{_id, name, …}`); o Mongo guarda id string/ObjectId. Normaliza-os para o
    id string puro (forma do Mongo) — assim os consumidores que agrupam/casam
    por id (ex.: `prov_map[str(securityId)]` contra o securityId da posição)
    funcionam sem mudança. Só toca campos que vêm como dict; deixa o `_id` e
    valores escalares intactos. None permanece None."""
    for k in ("walletId", "securityId", "companyId"):
        v = p.get(k)
        if isinstance(v, dict):
            p[k] = _idstr(v)
    return p


def _fetch_provisions(company_id, initial_date, final_date, wallet_ids=None):
    if company_id:
        try:
            # O endpoint /beehus/provisions NÃO aceita `walletIds` (responde
            # 400): busca por empresa+janela e a filtragem por carteira é no
            # cliente (callers aplicam `want`).
            out = list_provisions(company_id=company_id,
                                  initial_date=initial_date, final_date=final_date)
            if isinstance(out, list):
                return _dedup_by_id(_normalize_provision(p)
                                    for p in out if isinstance(p, dict))
            _log.warning("list_provisions(%s) non-list.", company_id)
        except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
            _log.warning("list_provisions(%s) failed (%s).", company_id, exc)
    return []


def _pwallet(p):
    return _idstr(p.get("walletId"))


def _pdate(v):
    return str(v)[:10] if v else ""


def _norm_wids(wallet_ids):
    """Normalise wallet_ids (scalar or iterable) to a list[str] or None."""
    if wallet_ids is None:
        return None
    if isinstance(wallet_ids, (list, tuple, set)):
        return [str(w) for w in wallet_ids if w]
    return [str(wallet_ids)] if wallet_ids else None


def _resolve_company(company_id, wids):
    """Use the given companyId, else resolve it from the first wallet
    (TRANSITORY: db.wallets via resolve_wallet — wallets not yet migrated)."""
    if company_id:
        return company_id
    if wids:
        return _company_of_wallet(wids[0])
    return ""


def provisions_active(company_id, date, wallet_ids=None):
    """Provisões ATIVAS em `date` (`initialDate <= D < liquidationDate`, não
    trashed), opcionalmente restritas a `wallet_ids`. Drop-in para
    `find({initialDate:{$lte:D}, liquidationDate:{$gt:D}, trashed:{$ne:true}})`.
    Os docs retornados têm `walletId` normalizado a string em `_walletId_str`
    apenas via _pwallet — os campos originais permanecem intactos."""
    if not date:
        return []
    d = str(date)[:10]
    wids = _norm_wids(wallet_ids)
    want = set(wids) if wids else None
    cid = _resolve_company(company_id, wids)
    out = []
    # Janela LARGA p/ trás (2000-01-01 .. D): o endpoint /beehus/provisions
    # filtra por `initialDate` DENTRO da janela (provisões INICIADAS no
    # intervalo), NÃO por sobreposição — confirmado em produção (uma (D,D) perdia
    # as provisões iniciadas antes de D e ainda ativas). Buscar desde 2000 traz
    # todas iniciadas até D (superset de ativas-em-D); o filtro estrito abaixo
    # reaplica a regra do Mongo `initialDate <= D < liquidationDate`.
    for p in _fetch_provisions(cid, "2000-01-01", d, wids):
        if p.get("trashed"):
            continue
        ini, liq = _pdate(p.get("initialDate")), _pdate(p.get("liquidationDate"))
        if not (ini and liq and ini <= d < liq):
            continue
        if want is not None and _pwallet(p) not in want:
            continue
        out.append(p)
    return out


def provisions_overlapping(company_id, initial_date, final_date, wallet_ids=None):
    """Provisões cuja janela [initialDate, liquidationDate) cruza [ini, fin]
    (`initialDate <= fin AND liquidationDate >= ini`), não trashed. Drop-in para
    o scan de `_provisions_by_wallet_date` (carteira)."""
    ini, fin = str(initial_date)[:10], str(final_date)[:10]
    wids = _norm_wids(wallet_ids)
    want = set(wids) if wids else None
    cid = _resolve_company(company_id, wids)
    out = []
    # Janela larga p/ trás: o endpoint filtra por initialDate-na-janela, então
    # buscar desde 2000 captura provisões iniciadas antes de `ini` e ainda
    # ativas no intervalo (o filtro de overlap abaixo refina). Ver provisions_active.
    for p in _fetch_provisions(cid, "2000-01-01", fin, wids):
        if p.get("trashed"):
            continue
        pi, pl = _pdate(p.get("initialDate")), _pdate(p.get("liquidationDate"))
        if not (pi and pl and pi <= fin and pl >= ini):
            continue
        if want is not None and _pwallet(p) not in want:
            continue
        out.append(p)
    return out


def provisions_search(company_id, *, initial_date=None, final_date=None,
                      security_id=None, provision_type=None, limit=1000):
    """Busca de provisões (console): empresa + janela + securityId +
    provisionType, ordenada por liquidationDate desc, limitada. Substitui
    `find(query).sort('liquidationDate', -1).limit(1000)`."""
    if not company_id:
        return []
    ini = str(initial_date)[:10] if initial_date else "2000-01-01"
    fin = str(final_date)[:10] if final_date else "2999-12-31"
    sid = _idstr(security_id) if security_id else None
    out = []
    for p in _fetch_provisions(company_id, ini, fin):
        if p.get("trashed"):
            continue
        if sid is not None and _idstr(p.get("securityId")) != sid:
            continue
        if provision_type and (p.get("provisionType") or "") != provision_type:
            continue
        out.append(p)
    out.sort(key=lambda p: _pdate(p.get("liquidationDate")), reverse=True)
    return out[:limit]


# ── cash (from processed-position response; LIVE — NÃO cacheado) ─────────────
# A resposta de GET /beehus/financial/positions/processed-position é uma lista
# de objetos {position, provisions, cashAccounts}. Cada cashAccounts tem
# `values: [{date, value}]` (MESMO shape do Mongo) com o HISTÓRICO completo,
# independente da data consultada. Logo, somar caixa por data = somar os values
# casados. Precisa de companyId (resolvido da carteira via índice da API) e de
# uma `date` (usamos a maior data pedida; se a carteira não tiver posição
# processada nessa data a resposta vem vazia → a UI não mostra).

def _company_of_wallet(wallet_id):
    """companyId (str) de uma walletId via índice de carteiras (API). '' se
    desconhecido."""
    if not wallet_id:
        return ""
    try:
        d = wallet_doc(wallet_id)
        return _idstr((d or {}).get("companyId"))
    except Exception:  # noqa: BLE001
        return ""


def _position_objects(company_id, wallet_id, date):
    """Lista crua de objetos {position, provisions, cashAccounts} da API para a
    carteira na data, ou None se indisponível."""
    if not (company_id and wallet_id and date):
        return None
    try:
        out = get_processed_position(company_id=company_id, date=str(date)[:10],
                                     wallet_ids=[wallet_id])
    except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
        _log.warning("get_processed_position(%s,%s) failed (%s).", wallet_id, date, exc)
        return None
    return out if isinstance(out, list) else None


def _cash_values_for_wallet(objs, wallet_id):
    """Junta os `values` de todos os cashAccounts da carteira nos objetos."""
    wid = str(wallet_id)
    vals = []
    for obj in (objs or []):
        if not isinstance(obj, dict):
            continue
        for ca in (obj.get("cashAccounts") or []):
            if not isinstance(ca, dict):
                continue
            # Strict walletId match (mirrors the Mongo `$in` filter, which
            # excludes docs whose walletId is missing/other).
            if _idstr(ca.get("walletId")) != wid:
                continue
            for v in (ca.get("values") or []):
                if isinstance(v, dict):
                    vals.append(v)
    return vals


def _sum_cash_values(vals, dates):
    """{date: total_or_None} somando os `{date,value}` de `vals` para cada entrada
    de `dates` (chave por `[:10]`). Núcleo compartilhado por `cash_sums_by_dates`
    e `carteira_position_bundle`."""
    wanted = {str(d)[:10] for d in dates if d}
    sums = {k: 0.0 for k in wanted}
    found = {k: False for k in wanted}
    for v in (vals or []):
        d_raw = v.get("date")
        if d_raw is None:
            continue
        k = str(d_raw)[:10]
        if k in wanted:
            try:
                sums[k] += float(v.get("value") or 0)
                found[k] = True
            except (TypeError, ValueError):
                pass
    out = {}
    for d in dates:
        if not d:
            out[d] = None
            continue
        k = str(d)[:10]
        out[d] = sums[k] if found.get(k) else None
    return out


def cash_sums_by_dates(wallet_id, dates, company_id=None):
    """{date: total_or_None} de caixa por data. Drop-in para
    db.sum_cash_by_dates: soma cashAccounts.values vindos da resposta de
    processed-position; {date: None} em falha/resposta vazia (sem Mongo)."""
    wanted = {str(d)[:10] for d in dates if d}
    if not wanted:
        return {d: None for d in dates}
    cid = company_id or _company_of_wallet(wallet_id)
    objs = _position_objects(cid, wallet_id, max(wanted)) if cid else None
    vals = _cash_values_for_wallet(objs, wallet_id) if objs else None
    if not vals:
        return {d: None for d in dates}
    return _sum_cash_values(vals, dates)


def cash_accounts_docs(wallet_id, company_id=None, date=None):
    """Lista de docs cashAccounts (`{_id, unprocessedId, walletId, currency,
    values:[{date,value}]}`) da carteira, via processed-position (vazio se a API
    falhar). Substitui o export `db.cashAccounts.find({walletId},{_id:0})`."""
    cid = company_id or _company_of_wallet(wallet_id)
    qdate = str(date)[:10] if date else None
    objs = _position_objects(cid, wallet_id, qdate) if (cid and qdate) else None
    if objs:
        wid = str(wallet_id)
        docs = []
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            for ca in (obj.get("cashAccounts") or []):
                # Strict walletId match (mirrors Mongo `$in`); drop `_id` to
                # mirror the original projection `{_id: 0}` on export.
                if isinstance(ca, dict) and _idstr(ca.get("walletId")) == wid:
                    docs.append({k: v for k, v in ca.items() if k != "_id"})
        if docs:
            return docs
    return []


def cash_unprocessed_ids(wallet_ids, company_id, date):
    """{walletId_str: unprocessedId} (rótulo 'Caixa - …') por carteira, via
    processed-position (vazio se a API falhar). Substitui o map de _cash_unprocessed_ids."""
    want = [str(w) for w in (wallet_ids or []) if w]
    if not want:
        return {}
    out = {}
    if company_id and date:
        # processed-position aceita walletIds (plural) — busca em lote:
        try:
            objs = get_processed_position(company_id=company_id,
                                          date=str(date)[:10], wallet_ids=want)
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            objs = None
        if isinstance(objs, list):
            for obj in objs:
                if not isinstance(obj, dict):
                    continue
                for ca in (obj.get("cashAccounts") or []):
                    if not isinstance(ca, dict):
                        continue
                    w = _idstr(ca.get("walletId"))
                    uid = (ca.get("unprocessedId") or "").strip()
                    if w and uid and w not in out:
                        out[w] = uid
            if out:
                return out
    return out


# ── unprocessedSecurityPositions (posição bruta; LIVE — não cacheado) ────────
# Endpoint B aceita RANGE (initialDate/finalDate) — validado. walletId/companyId/
# entityId vêm POPULADOS (dict) → normalizados p/ id-string; securities[] já vêm
# com unprocessedId/pu/quantity/balance (sem refs populadas). `_fetch_unprocessed`
# distingue ERRO de API (→ None → a UI não mostra) de resposta VAZIA legítima
# (carteira sem posição na data → [] → vazio). walletId é STRING nesta coleção
# (os callers consultam com a string crua). companyId resolvido da carteira
# (índice de wallets) quando omitido.

def _normalize_unprocessed_doc(d):
    """Normaliza refs de topo (walletId/companyId/entityId) de um doc unproc da
    API p/ id-string (só quando vierem como dict). Não muta o original."""
    d = dict(d)
    for k in ("walletId", "companyId", "entityId"):
        if isinstance(d.get(k), dict):
            d[k] = _idstr(d[k])
    return d


def _fetch_unprocessed(company_id, initial_date, final_date, wallet_ids=None):
    """Lista normalizada de docs unproc da API na janela [ini,fin], ou None em
    ERRO/não-lista (→ caller retorna vazio). Resposta vazia legítima vira []."""
    if not company_id:
        return None
    try:
        out = get_unprocessed_security_positions(
            company_id=company_id, initial_date=str(initial_date)[:10],
            final_date=str(final_date)[:10], wallet_ids=wallet_ids)
        if isinstance(out, list):
            return [_normalize_unprocessed_doc(d) for d in out if isinstance(d, dict)]
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        pass
    return None


def unprocessed_doc(wallet_id, date, company_id=None):
    """Doc unproc da carteira na `date` EXATA, ou None. Drop-in para
    `db.unprocessedSecurityPositions.find_one({walletId, positionDate})`."""
    wid = _idstr(wallet_id)
    dd = str(date)[:10]
    if not wid or not dd:
        return None
    cid = company_id or _company_of_wallet(wid)
    docs = _fetch_unprocessed(cid, dd, dd, [wid]) if cid else None
    if docs is not None:
        for d in docs:
            if _idstr(d.get("walletId")) == wid and str(d.get("positionDate"))[:10] == dd:
                return d
        return None  # API respondeu (lista); carteira sem posição bruta na data
    return None      # API falhou → a UI não mostra


def unprocessed_existing_wallets(company_id, date, wallet_ids=None):
    """Set de walletId_str com posição bruta na `date` (opcionalmente dentro de
    `wallet_ids`). Drop-in para
    `find({walletId:$in, positionDate}, {walletId:1})` → set."""
    dd = str(date)[:10]
    wids = _norm_wids(wallet_ids)
    docs = _fetch_unprocessed(company_id, dd, dd, wids)
    if docs is not None:
        want = set(wids) if wids else None
        out = set()
        for d in docs:
            w = _idstr(d.get("walletId"))
            if w and str(d.get("positionDate"))[:10] == dd and (want is None or w in want):
                out.add(w)
        return out
    return set()


def unprocessed_sid_uid_map(company_id, wallet_ids, on_or_before, floor="2000-01-01"):
    """`{walletId_str: {securityId_str: unprocessedId}}` lido DIRETO dos snapshots
    brutos (`unprocessed-security-positions`): cada item de `securities[]` carrega
    o par AUTORITATIVO `preProcessingData.securityId` → `unprocessedId` daquela
    carteira. 1 fetch cobre [floor..on_or_before] e todas as carteiras (B aceita
    range + walletIds plural). {} se a API falhar (sem Mongo).

    Varre os snapshots do MAIS NOVO p/ o mais antigo e, por (carteira, securityId),
    fica com o `unprocessedId` do snapshot mais recente que contém o ativo — o
    rótulo "Ativo" vigente. Snapshots são esparsos por ativo (cada upload traz só
    parte da posição), então olhar TODOS os snapshots ≤ data (não só o último)
    maximiza a cobertura.

    Por que isto e não o `securityMappings` (`from→to`) cruzado com o snapshot:
    aquele mapa company-level COLIDE — um securityId costuma ter vários `from`
    históricos (~38% do catálogo nesta empresa), e qualquer ativo cujo sid tinha
    múltiplos candidatos e não estava fixado pelo snapshot caía em "—". Ler
    `preProcessingData.securityId` direto do snapshot pega o par EXATO que a
    carteira de fato carregou, sem chutar: superconjunto estrito do cruzamento
    antigo (nunca perde um ativo que o antigo resolvia, escolhe o MESMO uid,
    resolve mais). Ativos presentes só na posição processada e em NENHUM snapshot
    bruto (ex.: componentes de look-through / linhas criadas por evento) seguem
    sem `unprocessedId` — não há rótulo de upload bruto a anexar, por construção."""
    wids = _norm_wids(wallet_ids) or []
    if not company_id or not wids:
        return {}
    cap = str(on_or_before)[:10]
    docs = _fetch_unprocessed(company_id, str(floor)[:10], cap, wids)
    if docs is None:
        return {}
    want_w = set(wids)
    # Mais novo primeiro → o primeiro a gravar (carteira, sid) vence (rótulo vigente).
    docs_sorted = sorted(docs, key=lambda d: str(d.get("positionDate"))[:10], reverse=True)
    out = {}
    for d in docs_sorted:
        w = _idstr(d.get("walletId"))
        if w not in want_w:
            continue
        if str(d.get("positionDate"))[:10] > cap:
            continue
        wmap = out.setdefault(w, {})
        for s in (d.get("securities") or []):
            if not isinstance(s, dict):
                continue
            ppd = s.get("preProcessingData")
            sid = _idstr(ppd.get("securityId")) if isinstance(ppd, dict) else ""
            uid = s.get("unprocessedId")
            if sid and uid and sid not in wmap:
                wmap[sid] = uid
    return out


def unprocessed_docs_map(company_id, wallet_ids, dates):
    """`{(walletId_str, date): doc}` para `wallet_ids` em `dates`. B aceita
    range → 1 chamada cobrindo [min(dates)..max(dates)]; indexa por (wid,date)
    mantendo só as datas pedidas. {} se a API falhar (sem Mongo). Substitui
    loops de N `find_one` por par (evita N+1)."""
    wids = _norm_wids(wallet_ids) or []
    want_dates = {str(d)[:10] for d in (dates or []) if d}
    if not wids or not want_dates:
        return {}
    docs = _fetch_unprocessed(company_id, min(want_dates), max(want_dates), wids)
    if docs is not None:
        want_w = set(wids)
        out = {}
        for d in docs:
            w = _idstr(d.get("walletId"))
            pd = str(d.get("positionDate"))[:10]
            if w in want_w and pd in want_dates:
                out[(w, pd)] = d
        return out
    return {}


# ── processedPosition (posição processada; LIVE — não cacheado) ──────────────
# Endpoint A devolve [{position, provisions, cashAccounts}] por carteira numa
# DATA ÚNICA (sem range — validado). `position` == doc Mongo (mesmas chaves;
# walletId/companyId/entityId POPULADOS → normalizados; securities[].securityId
# é STRING, não normaliza). Aceita walletIds plural → 1 chamada cobre N carteiras
# numa data; multi-data = N chamadas (1/data). `_fetch_processed` distingue ERRO
# de API (→ None → a UI não mostra) de resposta VAZIA. walletId/positionDate são
# STRING nesta coleção.

def _normalize_position_doc(pos):
    """Normaliza refs de topo (walletId/companyId/entityId) só quando dict."""
    pos = dict(pos)
    for k in ("walletId", "companyId", "entityId"):
        if isinstance(pos.get(k), dict):
            pos[k] = _idstr(pos[k])
    return pos


def _fetch_processed_envelopes(company_id, date, wallet_ids=None):
    """Lista crua de envelopes `{position, provisions, cashAccounts}` da API numa
    data (com `position` normalizado), ou None em ERRO/não-lista. Inclui só
    envelopes cujo `position` é dict (mesma regra de `_fetch_processed`). Permite
    à Carteira derivar securities + caixa + provisões de UMA resposta por data
    (em vez de re-buscar o mesmo endpoint para cada bloco)."""
    if not company_id:
        return None
    try:
        out = get_processed_position(company_id=company_id, date=str(date)[:10],
                                     wallet_ids=wallet_ids)
        if isinstance(out, list):
            res = []
            for env in out:
                if isinstance(env, dict) and isinstance(env.get("position"), dict):
                    e = dict(env)
                    e["position"] = _normalize_position_doc(env["position"])
                    res.append(e)
            return res
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        pass
    return None


def _fetch_processed(company_id, date, wallet_ids=None):
    """Lista de `position` docs normalizados da API numa data, ou None em
    ERRO/não-lista (→ caller retorna vazio). Extrai item["position"] de cada envelope."""
    envs = _fetch_processed_envelopes(company_id, date, wallet_ids)
    return None if envs is None else [e["position"] for e in envs]


def processed_doc(wallet_id, date, company_id=None):
    """`position` doc da carteira na `date` EXATA, ou None. Drop-in para
    `db.processedPosition.find_one({walletId, positionDate})`."""
    wid = _idstr(wallet_id)
    dd = str(date)[:10]
    if not wid or not dd:
        return None
    cid = company_id or _company_of_wallet(wid)
    docs = _fetch_processed(cid, dd, [wid]) if cid else None
    if docs is not None:
        for d in docs:
            if _idstr(d.get("walletId")) == wid and str(d.get("positionDate"))[:10] == dd:
                return d
    return None


def processed_envelope(wallet_id, date, company_id=None):
    """Envelope processed-position `{position, provisions, cashAccounts}` da carteira
    na `date` EXATA (`position` normalizado), ou None. UMA leitura que serve PU
    (`position.securities[].pu`), quantidade (`.quantity`), caixa (`cashAccounts`) e
    provisões — consolida o que antes eram chamadas SEPARADAS ao MESMO endpoint
    (`processed_doc` p/ PU + `wallet_cash_and_provisions` p/ caixa/provisões)."""
    wid = _idstr(wallet_id)
    dd = str(date)[:10]
    if not wid or not dd:
        return None
    cid = company_id or _company_of_wallet(wid)
    envs = _fetch_processed_envelopes(cid, dd, [wid]) if cid else None
    for e in (envs or []):
        pos = e.get("position") or {}
        if _idstr(pos.get("walletId")) == wid and str(pos.get("positionDate"))[:10] == dd:
            return e
    return None


def processed_envelopes_map(company_id, date, wallet_ids=None):
    """`{walletId_str: envelope}` p/ `wallet_ids` numa data EXATA — versão BATCH de
    `processed_envelope` (1 chamada cobre todas as carteiras). Mesma normalização e
    mesma regra de match (position dict + walletId + positionDate). `{}` se a API
    falhar. Carteiras sem processed-position na data simplesmente não entram no dict.
    Usado pela projeção em LOTE da Conciliação (mov.) p/ não chamar o endpoint por
    carteira."""
    dd = str(date)[:10]
    wids = _norm_wids(wallet_ids)
    envs = _fetch_processed_envelopes(company_id, dd, wids)
    out = {}
    for e in (envs or []):
        pos = e.get("position") or {}
        w = _idstr(pos.get("walletId"))
        if w and str(pos.get("positionDate"))[:10] == dd and w not in out:
            out[w] = e
    return out


def processed_existing_wallets(company_id, date, wallet_ids=None):
    """Set de walletId_str com processedPosition na `date` (opcionalmente dentro
    de `wallet_ids`). Drop-in para
    `find({positionDate, walletId:$in}, {walletId:1})` → set."""
    dd = str(date)[:10]
    wids = _norm_wids(wallet_ids)
    docs = _fetch_processed(company_id, dd, wids)
    if docs is not None:
        want = set(wids) if wids else None
        out = set()
        for d in docs:
            w = _idstr(d.get("walletId"))
            if w and str(d.get("positionDate"))[:10] == dd and (want is None or w in want):
                out.add(w)
        return out
    return set()


def processed_positions_map(company_id, wallet_ids, dates):
    """`{(walletId_str, date): securities_list}` para `wallet_ids` em `dates`.
    A é single-date → 1 chamada por data (walletIds plural cobre as carteiras);
    se QUALQUER data falhar, retorna {} (sem Mongo, nunca mapa parcial).
    Drop-in para o loop `find({walletId:$in, positionDate:$in})`."""
    wids = _norm_wids(wallet_ids) or []
    want_dates = [str(d)[:10] for d in (dates or []) if d]
    want_w = set(wids)
    if company_id and want_dates and wids:
        out = {}
        ok = True
        for dd in want_dates:
            docs = _fetch_processed(company_id, dd, wids)
            if docs is None:
                ok = False
                break
            for d in docs:
                w = _idstr(d.get("walletId"))
                pd = str(d.get("positionDate"))[:10]
                if w and pd == dd and (not want_w or w in want_w):
                    out[(w, pd)] = d.get("securities") or []
        if ok:
            return out
    return {}


def carteira_position_bundle(company_id, wallet_ids, dates):
    """Para a Carteira: securities + caixa (saldo e unprocessedId) + provisões de
    UMA resposta processed-position por data — em vez das 3+ chamadas redundantes
    ao MESMO endpoint (`processed_positions_map` + N× `cash_sums_by_dates` +
    `cash_unprocessed_ids`) MAIS a chamada separada de `/beehus/provisions`.

    Retorna `(pos_map, cash_map, cash_uid_map, prov_map)`:
      pos_map[(wid, date)]  = securities list (idem `processed_positions_map`).
      cash_map[(wid, date)] = soma de caixa na data (ou None) — de cashAccounts.values
                              (histórico completo) da resposta da MAIOR data,
                              espelhando `cash_sums_by_dates(max(dates))`.
      cash_uid_map[wid]     = cashAccounts.unprocessedId (idem `cash_unprocessed_ids`).
      prov_map[(wid, date)] = soma de `balance` das provisões ATIVAS na data, lidas do
                              bloco `provisions` do PRÓPRIO envelope daquela data. O
                              envelope traz exatamente as provisões com
                              `initialDate <= date < liquidationDate` — inclusive as
                              longas iniciadas antes (verificado em produção) — então
                              dispensa a chamada `/beehus/provisions` e o piso 2000-01-01
                              (cujo workaround só existia porque aquele endpoint filtra
                              por initialDate-na-janela).

    Uma chamada por data (walletIds plural cobre as carteiras). `({},{},{},{})` se
    QUALQUER data falhar (sem mapa parcial — espelha `processed_positions_map`)."""
    wids = _norm_wids(wallet_ids) or []
    want_dates = [str(d)[:10] for d in (dates or []) if d]
    want_w = set(wids)
    if not (company_id and want_dates and wids):
        return {}, {}, {}, {}

    pos_map, prov_map = {}, {}
    envs_by_date = {}
    for dd in want_dates:
        envs = _fetch_processed_envelopes(company_id, dd, wids)
        if envs is None:
            return {}, {}, {}, {}
        envs_by_date[dd] = envs
        for e in envs:
            pos = e.get("position") or {}
            w = _idstr(pos.get("walletId"))
            pd = str(pos.get("positionDate"))[:10]
            if not (w and pd == dd and (not want_w or w in want_w)):
                continue
            pos_map[(w, pd)] = pos.get("securities") or []
            total = 0.0
            for p in (e.get("provisions") or []):
                if not isinstance(p, dict) or p.get("trashed"):
                    continue
                bal = p.get("balance")
                if bal is None:
                    continue
                try:
                    total += float(bal)
                except (TypeError, ValueError):
                    pass
            prov_map[(w, pd)] = total

    # Caixa: cashAccounts.values é histórico completo (independe da data
    # consultada) → usa a resposta da MAIOR data, espelhando exatamente o que
    # `cash_sums_by_dates(max(dates))` / `cash_unprocessed_ids(final)` faziam.
    max_envs = envs_by_date.get(max(want_dates)) or []
    cash_map, cash_uid_map = {}, {}
    for w in wids:
        vals = _cash_values_for_wallet(max_envs, w)
        if vals:
            for dt, total in _sum_cash_values(vals, want_dates).items():
                cash_map[(w, dt)] = total
        for e in max_envs:
            uid_found = False
            for ca in (e.get("cashAccounts") or []):
                if isinstance(ca, dict) and _idstr(ca.get("walletId")) == w:
                    uid = (ca.get("unprocessedId") or "").strip()
                    if uid:
                        cash_uid_map[w] = uid
                        uid_found = True
                        break
            if uid_found:
                break
    return pos_map, cash_map, cash_uid_map, prov_map


# ── issues (blocking-by-wallet via pre-processing endpoint; LIVE) ────────────
# Só o caso "issues bloqueantes por carteira" mapeia para o endpoint
# pre-processing (os outros 4 sites de issues seguem no Mongo como gap). O shape
# exato da resposta de pre-processing NÃO foi confirmado; o parser abaixo é
# best-effort e retorna {} (a UI não mostra) se não reconhecer a estrutura.

def blocking_issues_by_wallet(company_id, date, wallet_ids, blocking_types, label_by_type):
    """`{walletId: [{type,label,count}, ...]}` para issues pendentes bloqueantes.
    Tenta o endpoint pre-processing; {} se a API falhar/não reconhecer (a UI não mostra)."""
    want = {str(w) for w in (wallet_ids or [])}
    blocking = list(blocking_types)
    counts = None  # {walletId: {type: count}}
    if company_id and date and want:
        try:
            resp = get_preprocessing_status(company_id=company_id,
                                            position_date=str(date)[:10])
            counts = _parse_preprocessing_blocking(resp, want, set(blocking))
        except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
            _log.warning("get_preprocessing_status failed (%s); sem dados (a UI não mostra).", exc)
            counts = None
    if counts is None:
        return {}
    # Monta a saída na ordem canônica dos tipos bloqueantes.
    result = {}
    for wid, type_counts in counts.items():
        items = []
        for typ in blocking:
            if typ in type_counts:
                items.append({"type": typ,
                              "label": label_by_type.get(typ, typ),
                              "count": int(type_counts[typ])})
        if items:
            result[wid] = items
    return result


def _parse_preprocessing_blocking(resp, want_wallets, blocking_set):
    """Best-effort: extrai {walletId: {type: count}} da resposta de
    pre-processing. Retorna None se o shape não for reconhecível (→ caller {})."""
    # Aceita: lista de itens por carteira OU {data:[...]} OU {walletId:[issues]}.
    items = None
    if isinstance(resp, list):
        items = resp
    elif isinstance(resp, dict):
        if isinstance(resp.get("data"), list):
            items = resp["data"]
        elif isinstance(resp.get("wallets"), list):
            items = resp["wallets"]
        else:
            # talvez já seja {walletId: [...]}
            items = None
            out = {}
            recognised = False
            for k, v in resp.items():
                if isinstance(v, list):
                    recognised = True
                    wid = str(k)
                    if want_wallets and wid not in want_wallets:
                        continue
                    tc = {}
                    for it in v:
                        t = (it or {}).get("type") if isinstance(it, dict) else None
                        if t in blocking_set:
                            tc[t] = tc.get(t, 0) + 1
                    if tc:
                        out[wid] = tc
            if recognised:
                return out
            return None
    if items is None:
        return None
    out = {}
    for it in items:
        if not isinstance(it, dict):
            return None
        wid = _idstr(it.get("walletId") or it.get("wallet"))
        if not wid or (want_wallets and wid not in want_wallets):
            continue
        issues = it.get("issues") or it.get("blockingIssues") or []
        tc = out.setdefault(wid, {})
        for iss in issues:
            if not isinstance(iss, dict):
                continue
            t = iss.get("type")
            if t in blocking_set:
                tc[t] = tc.get(t, 0) + int(iss.get("count") or 1)
    return out


# ── transactions (endpoint G; LIVE — não cacheado) ──────────────────────────
# list_transactions(company, range, walletIds/groupingIds/entityIds/securityIds,
# dateType) — shape == Mongo; refs walletId/companyId/entityId/securityId/
# groupingId/currencyId vêm POPULADAS (dict) → normalizadas p/ id-string
# (validado ao vivo). Filtros adicionais (balance, beehusTransactionType, _id) e
# sort ficam no CLIENTE (G não os expõe). dateType: 'liquidation'|'operation'.
# Sem fallback: se a API falhar, retorna [] (a UI não mostra). Leitura por `_id`
# NÃO tem endpoint (segue no Mongo como gap, ver conciliacao/console).

_TXN_REF_KEYS = ("walletId", "companyId", "entityId", "securityId",
                 "groupingId", "currencyId")

# Campos do `securityId` populado que o funil de diagnóstico consome (mesmas
# chaves do catálogo `/beehus/securities`, então sourcing por transação OU por
# catálogo é equivalente para o motor). A API usa `...NAVDays` (NAV maiúsculo);
# os leitores (diagnostic_engine / _orphan_transactions) foram alinhados a esse
# case (antes liam `...NavDays` → navDays do offset era sempre 0).
_SEC_INFO_FIELDS = ("subscriptionSettlementDays", "subscriptionNAVDays",
                    "redemptionSettlementDays", "redemptionNAVDays", "securityType")


def _normalize_txn(t):
    """Normaliza refs populadas de uma transação p/ id-string (só quando dict).

    Antes de achatar `securityId`, captura do objeto populado: o nome em
    `securityBeehusName` e os campos de liquidação/NAV+tipo em `securitySecInfo`
    — assim a UI exibe o nome e o diagnóstico obtém o sec_info do ativo da
    transação SEM ler o catálogo (a API já os devolve populados)."""
    t = dict(t)
    sec = t.get("securityId")
    if isinstance(sec, dict):
        if sec.get("beehusName"):
            t["securityBeehusName"] = sec.get("beehusName")
        info = {k: sec.get(k) for k in _SEC_INFO_FIELDS if k in sec}
        if info:
            t["securitySecInfo"] = info
    for k in _TXN_REF_KEYS:
        if isinstance(t.get(k), dict):
            t[k] = _idstr(t[k])
    return t


def transactions_search(company_id, *, initial_date, final_date, wallet_ids=None,
                        security_ids=None, grouping_ids=None, entity_ids=None,
                        date_type="liquidation"):
    """Transações no range [initial,final] por `date_type`, filtradas por
    empresa + carteiras/agrupamentos/entidades/securities. Normalizadas; [] se a
    API falhar (a UI não mostra). Para data exata use initial==final. Filtros
    extras (balance/type/_id) e ordenação: aplicar no cliente sobre o resultado.

    `company_id` é resolvido de uma única carteira (quando omitido) p/ habilitar
    a API; sem ele e com várias carteiras, retorna [] (não há chamada à API)."""
    ini, fin = str(initial_date)[:10], str(final_date)[:10]
    wl = _norm_wids(wallet_ids)
    if not company_id and wl and len(wl) == 1:
        company_id = _company_of_wallet(wl[0])
    if company_id:
        try:
            out = list_transactions(
                company_id=company_id, initial_date=ini, final_date=fin,
                wallet_ids=wl, grouping_ids=grouping_ids, entity_ids=entity_ids,
                security_ids=security_ids, date_type=date_type)
            if isinstance(out, list):
                return [_normalize_txn(t) for t in out if isinstance(t, dict)]
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            pass
    return []


def transactions_on_date(company_id, wallet_ids, date, date_type="liquidation"):
    """Conveniência: transações de `wallet_ids` numa data exata (range D..D)."""
    return transactions_search(company_id, initial_date=date, final_date=date,
                               wallet_ids=wallet_ids, date_type=date_type)


def transactions_search_many(company_ids, *, initial_date, final_date,
                             date_type="liquidation"):
    """`{company_id: [txns_normalizadas]}` para várias empresas no range, em
    paralelo (telas cross-empresa, ex.: contagem de transações não-identificadas
    do Painel). Sem filtro de carteira → todas as transações da empresa no range.
    Empresas sem transações saem do dict; [] por empresa que a API não servir."""
    ids = [c for c in (company_ids or []) if c]
    out = {}
    if not ids:
        return out
    workers = min(_NAV_WARM_WORKERS, max(1, len(ids)))

    def _one(cid):
        try:
            return transactions_search(cid, initial_date=initial_date,
                                       final_date=final_date, date_type=date_type)
        except Exception:  # noqa: BLE001
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, res in zip(ids, ex.map(_one, ids)):
            if res:
                out[cid] = res
    return out


# ── navPackages cache (consolidated cross-wallet reads) ──────────────────────
# A grade de divergência / contagens (Conciliação, Painel, NAV) é um agregado em
# TODAS as carteiras+agrupamentos da empresa. O endpoint nav-contribution é
# por-entidade, então montamos o conjunto completo via N chamadas paralelas
# (1 por carteira + 1 por agrupamento) e CACHEAMOS. navPackages MUDA na
# conciliação (recalc/publish) → a frescura vem de invalidação explícita
# (botão "Atualizar" + mutações), com TTL de backstop. Se QUALQUER chamada
# por-entidade falhar, devolvemos lista vazia para a empresa inteira (nunca
# servir grade silenciosamente incompleta; a UI não mostra). Os docs já vêm com
# walletId (carteira) ou groupingId (agrupamento) como string e os mesmos campos do Mongo.

_NAV_TTL_SECONDS = 1800        # backstop; frescura real = invalidação
_NAV_WARM_WORKERS = 10
_nav_cache_lock = threading.RLock()
_nav_cache = {}               # company_id -> (inserted_monotonic, [docs])


def _nav_today():
    try:
        from db import today_in_brt  # lazy
        return today_in_brt().isoformat()
    except Exception:  # noqa: BLE001
        from datetime import date
        return date.today().isoformat()


def _warm_nav_from_api(company_id):
    """Conjunto completo de navPackages (carteira + agrupamento) da empresa via
    N chamadas paralelas a nav-contribution. Retorna a lista, ou None se QUALQUER
    entidade falhar (→ lista vazia p/ não servir grade incompleta)."""
    # Teto bem no futuro (não _nav_today): os positionDate podem estar adiante de
    # "hoje" e um teto em hoje truncaria a série (espelha find sem teto de data).
    final = "2999-12-31"
    try:
        wids = [_idstr(w.get("_id")) for w in (partner_wallets(company_id) or [])]
        gids = [_idstr(g.get("_id")) for g in (list_groupings(company_id) or [])]
    except (BeehusAPIError, BeehusAuthError, Exception) as exc:  # noqa: BLE001
        _log.warning("nav warm: listing entities failed for %s (%s)", company_id, exc)
        return None
    tasks = ([("wallet", w) for w in wids if w]
             + [("grouping", g) for g in gids if g])
    if not tasks:
        return []

    def _fetch(t):
        scope, eid = t
        try:
            r = get_nav_contribution(entity_id=eid, company_id=company_id,
                                     scope=scope, initial_date="2000-01-01",
                                     final_date=final)
            return r if isinstance(r, list) else None
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            return None

    docs = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=_NAV_WARM_WORKERS) as ex:
        for r in ex.map(_fetch, tasks):
            if r is None:
                _log.warning("nav warm: an entity call failed for %s; empty "
                             "result for the whole company.", company_id)
                return None
            for d in r:
                if isinstance(d, dict) and not d.get("trashed"):
                    docs.append(d)
    return docs


def nav_packages(company_id):
    """Todos os navPackages (não-trashed) da empresa — nível carteira E
    agrupamento — no MESMO shape de `db.navPackages.find({companyId})`. Montado
    via nav-contribution (N chamadas, cacheado); [] se a API falhar (a UI não
    mostra). Consumidores consolidados filtram/agrupam sobre esta lista em
    Python. NÃO mutar."""
    if not company_id:
        return []
    ent = _nav_cache.get(company_id)
    if ent is not None and (time.monotonic() - ent[0]) < _NAV_TTL_SECONDS:
        return ent[1]
    with _nav_cache_lock:
        ent = _nav_cache.get(company_id)
        if ent is not None and (time.monotonic() - ent[0]) < _NAV_TTL_SECONDS:
            return ent[1]
        try:
            docs = _warm_nav_from_api(company_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning("nav warm crashed for %s (%s)", company_id, exc)
            docs = None
        if docs is None:
            docs = []
        _nav_cache[company_id] = (time.monotonic(), docs)
        return docs


def invalidate_nav(company_id=None):
    """Drop cached navPackages for a company (or all). Chamado pelo botão
    "Atualizar" e após mutações de NAV (calc/publish/unpublish)."""
    with _nav_cache_lock:
        if company_id:
            _nav_cache.pop(company_id, None)
        else:
            _nav_cache.clear()


def refresh(company_id=None):
    """Limpa caches de referência (securities/mappings/companies/entities) e de
    navPackages. Backing do botão "Atualizar" do sidebar."""
    invalidate()          # reference TTL caches (securities/mappings/companies/entities)
    invalidate_nav(company_id)


def all_company_ids():
    """IDs de todas as empresas (via company_names). Usado por `nav_results_many`
    (telas cross-empresa, ex.: contagens do Painel). [] se a API falhar."""
    try:
        return sorted(company_names().keys())
    except Exception:  # noqa: BLE001
        return []


# `_nav_pd` — extrai o positionDate (YYYY-MM-DD) de um navPackage. Usado pelos
# helpers por-entidade (nav_doc_for_entity_date / nav_former_for_entity) abaixo.

def _nav_pd(d):
    return str(d.get("positionDate"))[:10] if d.get("positionDate") else ""


def nav_grouping_docs(company_id, date):
    """navPackages de NÍVEL AGRUPAMENTO (groupingId não nulo) da empresa na data,
    via endpoint consolidado `/results` (groupingsDetailed) — **1 chamada AO
    VIVO** ([] se a API falhar). Não-trashed (validado: totalGroupings = grupos
    não-trashed). Cada item tem groupingId, published, returnNavPerShare,
    returnContribution, nav, navPerShare, amount, returnDifference."""
    res = nav_results(company_id, date)
    return [g for g in res.get("groupingsDetailed", []) if _idstr(g.get("groupingId"))]


# ── navPackages por-entidade (1 chamada direta, NÃO aquece a empresa toda) ────
# find_one/find por carteira (ou agrupamento) usados por telas de DETALHE.
# Servem do cache da empresa se já estiver quente (sem chamada à API); senão
# fazem UMA chamada direta a nav-contribution (rápida ~0.06s) — jamais disparam
# o warm de 1k+ entidades; [] se a API falhar (a UI não mostra). company_id
# é resolvido via _company_of_wallet quando omitido (escopo carteira).
# nav-contribution devolve walletId/groupingId como string.

def _nav_cache_hit(company_id):
    """Lista cacheada da empresa se ainda fresca, senão None (sem warm)."""
    if not company_id:
        return None
    ent = _nav_cache.get(company_id)
    if ent is not None and (time.monotonic() - ent[0]) < _NAV_TTL_SECONDS:
        return ent[1]
    return None


def nav_series_for_entity(entity_id, company_id=None, scope="wallet"):
    """Série de navPackages (não-trashed) de UMA entidade (carteira/agrupamento),
    no mesmo shape de `db.navPackages.find({walletId|groupingId, trashed!=True})`.
    Cache-da-empresa-first → 1 chamada direta a nav-contribution → [] se falhar."""
    eid = _idstr(entity_id)
    if not eid:
        return []
    key = "walletId" if scope == "wallet" else "groupingId"
    cached = _nav_cache_hit(company_id)
    if cached is not None:
        return [d for d in cached if _idstr(d.get(key)) == eid]
    if not company_id and scope == "wallet":
        company_id = _company_of_wallet(eid)
        cached = _nav_cache_hit(company_id)
        if cached is not None:
            return [d for d in cached if _idstr(d.get(key)) == eid]
    if company_id:
        try:
            r = get_nav_contribution(entity_id=eid, company_id=company_id,
                                     scope=scope, initial_date="2000-01-01",
                                     final_date="2999-12-31")  # teto futuro: há positionDate adiante de hoje
            if isinstance(r, list):
                return [d for d in r
                        if isinstance(d, dict) and not d.get("trashed")]
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            pass
    return []


def nav_doc_for_entity_date(entity_id, date, company_id=None, scope="wallet"):
    """navPackage não-trashed da entidade em `date` exata, ou None. Porta de
    `db.navPackages.find_one({walletId, positionDate, trashed!=True})`."""
    dd = str(date)[:10]
    for d in nav_series_for_entity(entity_id, company_id, scope):
        if _nav_pd(d) == dd:
            return d
    return None


def nav_former_for_entity(entity_id, date, company_id=None, scope="wallet"):
    """(former_date_str|None, former_nav|None): navPackage não-trashed mais
    recente ESTRITAMENTE antes de `date`. Porta de `_find_former_nav`
    (find_one sort positionDate -1, positionDate < date)."""
    dd = str(date)[:10]
    best = None  # (positionDate, nav)
    for d in nav_series_for_entity(entity_id, company_id, scope):
        pd = _nav_pd(d)
        if not pd or pd >= dd:
            continue
        if best is None or pd > best[0]:
            best = (pd, d.get("nav"))
    if best is None:
        return None, None
    return best[0], best[1]


# ── /results — consolidado por empresa+data (1 chamada, AO VIVO) ──────────────
# Endpoint nav-contribution-calculation/results: devolve, por empresa+data,
# TODAS as carteiras e agrupamentos com NAV + contagens + divergência
# (returnDifference) e valor financeiro do gap pré-calculados. Validado 1:1 vs
# navPackages (389 carteiras / 19 agrupamentos, 0 divergências). Servimos AO
# VIVO (sem cache → sempre fresco, reflete o upstream na hora); {} se a API
# falhar (a UI não mostra). Substitui o warm por-entidade nas telas
# consolidadas (Painel, Conciliação grade, Console).

def nav_results(company_id, date):
    """Consolidado /results da empresa+data (carteiras + agrupamentos + contagens
    + divergência). 1 chamada AO VIVO; {} se a API falhar (a UI não mostra).
    Retorna sempre dict (nunca None)."""
    if not company_id or not date:
        return {}
    pd = str(date)[:10]
    try:
        r = get_nav_results(company_id=company_id, position_date=pd)
        if isinstance(r, dict) and r:
            return r
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        pass
    return {}


def nav_results_many(company_ids, date):
    """`{company_id: results_dict}` para várias empresas na mesma data, em
    paralelo (telas cross-empresa, ex.: contagens do Painel). {} por empresa que
    a API não servir. Empresas sem retorno saem do dict."""
    ids = [c for c in (company_ids or []) if c]
    out = {}
    if not ids:
        return out
    workers = min(_NAV_WARM_WORKERS, max(1, len(ids)))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, res in zip(ids, ex.map(lambda c: nav_results(c, date), ids)):
            if res:
                out[cid] = res
    return out


def _nav_results_is_gap(entry, threshold):
    """Mesma semântica do mismatch da Conciliação/Painel: threshold>0 →
    abs(rnps-rc) > threshold; senão (estrito) rnps != rc."""
    rnps = entry.get("returnNavPerShare")
    rc = entry.get("returnContribution")
    if threshold and threshold > 0:
        return abs((rnps or 0) - (rc or 0)) > float(threshold)
    return rnps != rc


# ── pre-processing (endpoint E) — fan-out por empresa ────────────────────────
# get_preprocessing_status é POR (empresa, data) e devolve, numa única chamada:
# contagens de issues por tipo no top-level (missingWallet/missingPosition/
# securityUnmapped/securityMissingClassification/securityMissingPrice/
# securityMissingHistoryPrice) + processedWallets/totalWallets. O Painel de
# Controle precisa disso para TODAS as empresas numa data → fan-out paralelo
# (mesmo padrão de nav_results_many). {} por empresa que a API não servir.

def preprocessing_status_many(company_ids, date):
    """`{company_id: status_dict}` do pre-processing (E) p/ várias empresas na
    mesma data, em paralelo (Painel de Controle). Empresas sem retorno saem do
    dict. [] / {} se a API falhar (a UI não mostra)."""
    ids = [c for c in (company_ids or []) if c]
    out = {}
    if not ids or not date:
        return out
    pd = str(date)[:10]
    workers = min(_NAV_WARM_WORKERS, max(1, len(ids)))

    def _one(cid):
        try:
            r = get_preprocessing_status(company_id=cid, position_date=pd)
            return r if isinstance(r, dict) else None
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for cid, res in zip(ids, ex.map(_one, ids)):
            if res:
                out[cid] = res
    return out


def wallets_with_position(company_id, date):
    """Carteiras da empresa COM posição processada na data — `[{id, name}]`.

    Lê `processedWalletsDetailed` do pre-processing (E): a lista das carteiras
    efetivamente processadas em `(company_id, date)`, com `walletId` + `name`.
    Substitui o read Mongo `processedPosition.find({companyId, positionDate})`
    do "Excluir Posições" (single-date, existência company-wide). `[]` se a API
    falhar (sem Mongo). De-dup por walletId."""
    cid = id_str(company_id) or str(company_id or "")
    pd = str(date or "")[:10]
    if not cid or not pd:
        return []
    try:
        st = get_preprocessing_status(company_id=cid, position_date=pd)
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        return []
    if not isinstance(st, dict):
        return []
    out, seen = [], set()
    for it in st.get("processedWalletsDetailed") or []:
        if not isinstance(it, dict):
            continue
        wid = id_str(it.get("walletId")) or str(it.get("walletId") or "")
        if not wid or wid in seen:
            continue
        seen.add(wid)
        out.append({"id": wid, "name": it.get("name") or ""})
    return out


# ── issues drill-down (detalhe por empresa+data+tipo, via pre-processing E) ──
# O grid do Painel já busca o E (contagens). O `/detail` (drill-down on-click)
# reusa os MESMOS arrays `*Detailed` do E — expandindo `affectedWallets` (cada
# item = {id, name, entity, entityId}) em 1 linha por security×carteira, igual à
# coleção `issues`. Validado ao vivo == Mongo nos 5 tipos. Substitui o read
# direto `db.issues.find({companyId, status:'pending', date, type})`.

_ISSUE_TYPE_TO_E_DETAILED = {
    "missing_wallet":                  "missingWalletDetailed",
    "missing_unprocessed_position":    "missingPositionDetailed",
    "security_unmapped":               "securityUnmappedDetailed",
    "security_missing_classification": "securityMissingClassificationDetailed",
    "security_missing_price":          "securityMissingPriceDetailed",
    "security_missing_history_price":  "securityMissingHistoryPriceDetailed",
}


def _aw_wallet_id(w):
    """walletId de um item de `affectedWallets` (dict `{id,…}`) ou id cru."""
    if isinstance(w, dict):
        return str(w.get("id") or w.get("_id") or "")
    return id_str(w)


def _parse_iso_dt(s):
    """ISO (ex. '2026-06-18T16:11:33.149Z') → datetime, ou None. (O `/detail`
    formata via `.strftime`, então devolvemos datetime como o Mongo fazia.)"""
    if not s:
        return None
    if isinstance(s, datetime.datetime):
        return s
    try:
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _rows_from_status(st, issue_type):
    """Linhas de issue de UM `issue_type` a partir de um status do
    pre-processing (E) JÁ BUSCADO — expandido por `affectedWallets` (1 linha
    por security×carteira). Extraído de `issues_detail` para ser reusado sem
    refazer a chamada à API: `issues_by_wallet_detail` varre os 6 tipos de uma
    única resposta. `[]` se o tipo é desconhecido ou o status não é dict."""
    key = _ISSUE_TYPE_TO_E_DETAILED.get(issue_type)
    if not key or not isinstance(st, dict):
        return []
    rows = []
    for it in (st.get(key) or []):
        if not isinstance(it, dict):
            continue
        base = {
            "type":                  issue_type,
            "description":           "",
            "externalOrigin":        "",
            "externalId":            str(it.get("externalId") or ""),
            "securityId":            id_str(it.get("securityId")),
            "unprocessedSecurityId": str(it.get("unprocessedSecurityId") or ""),
            "createdAt":             _parse_iso_dt(it.get("createdAt")),
        }
        aw = it.get("affectedWallets")
        if isinstance(aw, list) and aw:
            for w in aw:
                rows.append({**base, "walletId": _aw_wallet_id(w)})
        else:
            rows.append({**base, "walletId": id_str(it.get("walletId"))})
    return rows


def issues_detail(company_id, date, issue_type):
    """Linhas de issue por `(company_id, date, issue_type)` — drop-in do
    `db.issues.find({companyId, status:'pending', date, type})`. Vem do array
    `*Detailed` do pre-processing (E), **expandido** por `affectedWallets`
    (1 linha por security×carteira). Validado ao vivo == Mongo nos 5 tipos.
    `[]` se a API falhar (sem Mongo) ou tipo desconhecido.

    Cada linha tem o shape da projeção atual: `type`, `description` (''),
    `externalOrigin` (''), `externalId`, `securityId`, `unprocessedSecurityId`,
    `walletId`, `createdAt` (datetime). `description`/`externalOrigin` não
    existem no E e não são exibidos nas tabelas do /detail (só o cabeçalho usa
    `description`). O enriquecimento walletName/beehusName/mainId continua na
    rota (cache/securities API), igual a hoje."""
    key = _ISSUE_TYPE_TO_E_DETAILED.get(issue_type)
    cid = id_str(company_id) or str(company_id or "")
    pd = str(date or "")[:10]
    if not key or not cid or not pd:
        return []
    try:
        st = get_preprocessing_status(company_id=cid, position_date=pd)
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        return []
    if not isinstance(st, dict):
        return []
    rows = []
    for it in (st.get(key) or []):
        if not isinstance(it, dict):
            continue
        base = {
            "type":                  issue_type,
            "description":           "",
            "externalOrigin":        "",
            "externalId":            str(it.get("externalId") or ""),
            "securityId":            id_str(it.get("securityId")),
            "unprocessedSecurityId": str(it.get("unprocessedSecurityId") or ""),
            "createdAt":             _parse_iso_dt(it.get("createdAt")),
        }
        aw = it.get("affectedWallets")
        if isinstance(aw, list) and aw:
            for w in aw:
                rows.append({**base, "walletId": _aw_wallet_id(w)})
        else:
            rows.append({**base, "walletId": id_str(it.get("walletId"))})
    return rows


def issues_by_wallet_detail(company_id, date):
    """`{walletId: [row, ...]}` — TODAS as issues (os 6 tipos do Painel) de
    `(company_id, date)` agrupadas por carteira, de UMA única chamada ao
    pre-processing (E). Cada `row` tem o mesmo shape de `issues_detail` (com o
    campo `type`). Backing do modal "Verificar por carteira" (seletor de
    carteira → issues da carteira): como `get_preprocessing_status` NÃO é
    cacheado, varrer os 6 tipos aqui evita 6 chamadas à mesma rota. `{}` se a
    API falhar (sem Mongo) ou faltar company/date."""
    cid = id_str(company_id) or str(company_id or "")
    pd = str(date or "")[:10]
    if not cid or not pd:
        return {}
    try:
        st = get_preprocessing_status(company_id=cid, position_date=pd)
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        return {}
    if not isinstance(st, dict):
        return {}
    by_wallet = {}
    for issue_type in _ISSUE_TYPE_TO_E_DETAILED:
        for row in _rows_from_status(st, issue_type):
            wid = row.get("walletId") or ""
            by_wallet.setdefault(wid, []).append(row)
    return by_wallet


# ── securityPrices (preço/PU; LIVE — não cacheado) ───────────────────────────
# Endpoint `filtered-security-price` (ver beehus_api.filtered_security_price)
# devolve TODOS os records de escopo (pricingType B1/B2/C1/C2/C3) dos ativos
# pedidos; o cliente resolve o mais específico p/ o contexto (company, entity,
# wallet) na ordem C3→C2→C1→B2→B1. Filtra `status=="approved"` e `trashed!=true`
# (decisão do usuário, jun/2026). `securityId`/`companyId`/`entityId`/`walletId`
# vêm POPULADOS → normalizados p/ id-string. Substitui os reads diretos de
# `db.securityPrices` (precificacao/controlpanel/repetir) e CORRIGE o escopo:
# os reads Mongo pegavam um record qualquer (sort por data), ignorando carteira.

_PRICING_RANK = {"C3": 5, "C2": 4, "C1": 3, "B2": 2, "B1": 1}
_MAX_PRICE_IDS_PER_REQUEST = 150  # securityIds como PARÂMETRO REPETIDO (não CSV — o
# backend devolve vazio p/ CSV multi-id); ~37 chars/id na URL, 150 ≈ 5,5KB — evita 414


def _normalize_price_record(r):
    """Record de preço da API → refs populadas normalizadas p/ id-string,
    mantendo type/status/trashed/historyPrice."""
    return {
        "securityId": _idstr(r.get("securityId")),
        "type":       r.get("type"),
        "companyId":  _idstr(r.get("companyId")) or None,
        "entityId":   _idstr(r.get("entityId")) or None,
        "walletId":   _idstr(r.get("walletId")) or None,
        "status":     r.get("status"),
        "trashed":    bool(r.get("trashed")),
        "historyPrice": r.get("historyPrice") or [],
    }


def _price_record_matches(rec, *, company_id, entity_id, wallet_id):
    """True se o ESCOPO do record casa com o contexto, pela definição do `type`.
    Eixo ausente no contexto (None) → tipos que exigem esse eixo NÃO casam."""
    t = rec.get("type")
    cid = company_id or None
    eid = entity_id or None
    wid = wallet_id or None
    if t == "B1":
        return True
    if t == "B2":
        return eid is not None and rec.get("entityId") == eid
    if t == "C1":
        return cid is not None and rec.get("companyId") == cid
    if t == "C2":
        return (cid is not None and eid is not None
                and rec.get("companyId") == cid and rec.get("entityId") == eid)
    if t == "C3":
        return (cid is not None and wid is not None
                and rec.get("companyId") == cid and rec.get("walletId") == wid)
    return False


def security_price_records(security_ids):
    """Records de preço NORMALIZADOS (só `status=="approved"` e não-trashed) p/
    `security_ids`, via filtered-security-price. `[]` se a API falhar (sem Mongo,
    nunca mapa parcial). LIVE (não cacheado — preço muda). Chunka os ids p/ 414."""
    seen = []
    src = security_ids if isinstance(security_ids, (list, tuple, set)) else [security_ids]
    for s in src:
        s = _idstr(s)
        if s and s not in seen:
            seen.append(s)
    if not seen:
        return []
    chunks = [seen[i:i + _MAX_PRICE_IDS_PER_REQUEST]
              for i in range(0, len(seen), _MAX_PRICE_IDS_PER_REQUEST)]
    recs = []
    try:
        # Chunks em PARALELO (união grande de sids no lote da Conciliação mov.):
        # `ex.map` preserva a ordem de concatenação; QUALQUER falha → [] (nunca
        # mapa parcial), mesma semântica do loop sequencial.
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(5, len(chunks))) as ex:
            outs = list(ex.map(
                lambda c: filtered_security_price(security_ids=c), chunks))
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        return []  # falha → sem Mongo, sem mapa parcial
    for out in outs:
        for r in (out or []):
            if not isinstance(r, dict):
                continue
            nr = _normalize_price_record(r)
            if nr["trashed"] or nr["status"] != "approved" or not nr["securityId"]:
                continue
            recs.append(nr)
    return recs


def resolve_price_record(security_id, records=None, *, company_id=None,
                         entity_id=None, wallet_id=None):
    """Record de preço MAIS ESPECÍFICO p/ `security_id` no contexto dado, na
    ordem C3→C2→C1→B2→B1. `records` opcional (lista já buscada, p/ batch); se
    None, busca só este ativo. Retorna o record normalizado ou None."""
    sid = _idstr(security_id)
    if not sid:
        return None
    if records is None:
        records = security_price_records([sid])
    best = None
    best_rank = 0
    for rec in records:
        if rec.get("securityId") != sid:
            continue
        if not _price_record_matches(rec, company_id=company_id,
                                     entity_id=entity_id, wallet_id=wallet_id):
            continue
        rank = _PRICING_RANK.get(rec.get("type"), 0)
        if rank > best_rank:
            best_rank, best = rank, rec
    return best


def security_prices_resolved(security_ids, *, company_id=None, entity_id=None,
                             wallet_id=None, records=None):
    """`{securityId_str: historyPrice_list}` — resolve o record de preço mais
    específico de cada ativo p/ o contexto (company/entity/wallet) e devolve o
    `historyPrice` dele. `{}` se a API falhar. 1 fetch (chunked) p/ todo o batch.
    Ativos sem record que case o contexto ficam de fora do mapa.

    `records`: lista de records JÁ buscada (ex.: união de várias carteiras numa
    projeção em lote) — quando fornecida, NÃO faz fetch (resolução é client-side,
    por contexto). A resolução `_price_record_matches`/rank é a MESMA, então o
    resultado é idêntico ao do fetch por-carteira; um superconjunto de records é
    seguro (os que não casam o contexto são ignorados)."""
    seen = []
    src = security_ids if isinstance(security_ids, (list, tuple, set)) else [security_ids]
    for s in src:
        s = _idstr(s)
        if s and s not in seen:
            seen.append(s)
    if not seen:
        return {}
    if records is None:
        records = security_price_records(seen)
    out = {}
    for sid in seen:
        rec = resolve_price_record(sid, records, company_id=company_id,
                                   entity_id=entity_id, wallet_id=wallet_id)
        if rec is not None:
            out[sid] = rec.get("historyPrice") or []
    return out


# ── securityEvents (eventos corporativos; LIVE — não cacheado) ───────────────
# Endpoint `/beehus/security-events?securities=<csv>` (ver beehus_api.security_events)
# devolve TODOS os eventos dos ativos pedidos (todas as datas/tipos); o cliente
# filtra por operationDate + eventType e agrega. Substitui o read direto de
# `db.securityEvents` da curva (repetir_posicoes._dividend_events_by_sid).

_DIVIDEND_EVENT_TYPES = ("cashDividend", "interestOnEquity")
_MAX_EVENT_IDS_PER_REQUEST = 150  # csv de `securities` — evita 414


def dividend_events_by_sid(security_ids, date, *, event_types=_DIVIDEND_EVENT_TYPES):
    """`{securityId_str: Σ balance}` dos securityEvents dos `security_ids` cujo
    `operationDate == date` e `eventType ∈ event_types` (default: dividendo =
    `cashDividend` + `interestOnEquity`). `balance` é o dividendo POR COTA. `{}`
    se a API falhar (sem Mongo). Chunka os ids p/ 414. Drop-in do antigo read
    `db.securityEvents.find({operationDate, eventType:$in, trashed≠true})`."""
    seen = []
    src = security_ids if isinstance(security_ids, (list, tuple, set)) else [security_ids]
    for s in src:
        s = _idstr(s)
        if s and s not in seen:
            seen.append(s)
    d = str(date or "")[:10]
    if not seen or not d:
        return {}
    types = set(event_types or ())
    out = {}
    for i in range(0, len(seen), _MAX_EVENT_IDS_PER_REQUEST):
        chunk = seen[i:i + _MAX_EVENT_IDS_PER_REQUEST]
        try:
            evts = security_events(security_ids=chunk)
        except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
            return {}  # falha → sem Mongo, sem mapa parcial
        for e in (evts or []):
            if not isinstance(e, dict):
                continue
            if str(e.get("operationDate") or "")[:10] != d:
                continue
            if types and e.get("eventType") not in types:
                continue
            if e.get("trashed"):  # defensivo (a API não devolve `trashed` hoje)
                continue
            sid = _idstr(e.get("securityId"))
            if not sid:
                continue
            try:
                out[sid] = out.get(sid, 0.0) + float(e.get("balance") or 0)
            except (TypeError, ValueError):
                pass
    return out


# ── execution-prices (preço de execução; LIVE — não cacheado) ────────────────
# Endpoint `/beehus/financial/execution-prices` (ver beehus_api.list_execution_prices)
# devolve o preço de execução gravado por (carteira, ativo, positionDate).

def execution_prices_by_sid(company_id, wallet_id, initial_date, final_date):
    """`{securityId_str: {"price", "id", "positionDate"}}` dos execution-prices da
    carteira em `[initial_date, final_date]` (por `positionDate`). Quando há mais de
    um record por ativo na janela, vence o de `positionDate` MAIS RECENTE. O `id`
    (record `_id`) permite atualizar (PATCH) um placeholder. `{}` se a API falhar
    ou sem dados. LIVE (não cacheado) — específico por carteira/janela."""
    wid = _idstr(wallet_id)
    cid = company_id or _company_of_wallet(wid)
    d0, d1 = str(initial_date)[:10], str(final_date)[:10]
    if not cid or not wid or not d0 or not d1:
        return {}
    try:
        recs = list_execution_prices(company_id=cid, wallet_id=wid,
                                     initial_date=d0, final_date=d1)
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        return {}
    out, best = {}, {}
    for r in (recs or []):
        if not isinstance(r, dict) or r.get("trashed"):
            continue
        sid = _idstr(r.get("securityId"))
        ep = r.get("executionPrice")
        if not sid or ep is None:
            continue
        pd = str(r.get("positionDate") or "")[:10]
        if sid not in out or pd >= best.get(sid, ""):
            out[sid] = {"price": ep, "id": _idstr(r.get("_id")), "positionDate": pd}
            best[sid] = pd
    return out


def execution_prices_by_wallet_sid(company_id, initial_date, final_date):
    """`{walletId_str: {securityId_str: {"price","id","positionDate"}}}` — versão BATCH
    de `execution_prices_by_sid` p/ TODAS as carteiras da empresa na janela (1 chamada,
    SEM filtro de carteira; cada doc traz `walletId`). Mesma regra de desempate
    (positionDate MAIS RECENTE vence). `{}` se a API falhar. Usado pela projeção em LOTE."""
    cid = company_id
    d0, d1 = str(initial_date)[:10], str(final_date)[:10]
    if not cid or not d0 or not d1:
        return {}
    try:
        recs = list_execution_prices(company_id=cid, initial_date=d0, final_date=d1)
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        return {}
    out, best = {}, {}
    for r in (recs or []):
        if not isinstance(r, dict) or r.get("trashed"):
            continue
        wid = _idstr(r.get("walletId"))
        sid = _idstr(r.get("securityId"))
        ep = r.get("executionPrice")
        if not wid or not sid or ep is None:
            continue
        pd = str(r.get("positionDate") or "")[:10]
        wmap = out.setdefault(wid, {})
        bkey = (wid, sid)
        if sid not in wmap or pd >= best.get(bkey, ""):
            wmap[sid] = {"price": ep, "id": _idstr(r.get("_id")), "positionDate": pd}
            best[bkey] = pd
    return out


# ── Helpers portados do Tombamento (conciliação não-processada) ──────────────
# Consumidos por pages/conciliacao_unprocessed.py e pages/conciliacao_shared.py.
# Aditivos — todas as dependências (cache SWR, _fetch_processed_envelopes,
# partner_wallets, _normalize_wallet_doc, _cash_values_for_wallet, _sum_cash_values)
# já existem acima.

def securities_index_is_warm():
    """True se o catálogo de securities já está em cache utilizável — i.e., uma
    leitura NÃO vai bloquear no warm de ~20s. Entrada presente, não-vazia e
    dentro do teto de stale (stale serve na hora + revalida atrás; só além do
    teto a leitura bloqueia). Usado para decidir entre resolver nome no catálogo
    (quente) ou disparar warm em background + spinner (frio)."""
    entry = _cache_store.get("securities_index")
    if not entry or not entry[1]:
        return False
    return (time.monotonic() - entry[0]) < _STALE_MAX_SECONDS


def warm_securities_index_async():
    """Aquece o catálogo em background se estiver frio (dedup via `_refreshing`).
    Não bloqueia. Retorna True se já está quente (nada a fazer)."""
    if securities_index_is_warm():
        return True
    with _cache_lock:
        if "securities_index" not in _refreshing:
            _refreshing.add("securities_index")
            threading.Thread(
                target=_bg_refresh, args=("securities_index", _load_securities_index),
                name="catalog-warm:securities_index", daemon=True,
            ).start()
    return False


def _load_company_wallets(company_id):
    """`{walletId_str: doc_norm}` de UMA empresa via partner_wallets. {} em falha."""
    out = {}
    try:
        for w in (partner_wallets(company_id) or []):
            if isinstance(w, dict):
                nd = _normalize_wallet_doc(w)
                if nd.get("_id"):
                    out[nd["_id"]] = nd
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        _log.warning("partner_wallets(%s) failed.", company_id)
    return out


def company_wallets_index(company_id):
    """`{walletId_str: doc_norm}` de UMA empresa, CACHEADO (TTL/SWR) — NÃO monta o
    índice global de ~19 empresas. Chave `company_wallets::{companyId}`."""
    if not company_id:
        return {}
    return _cached_ttl(f"company_wallets::{company_id}",
                       lambda: _load_company_wallets(company_id))


def wallet_doc_in_company(wallet_id, company_id):
    """Doc de UMA carteira via `company_wallets_index(company_id)` (partner_wallets
    cacheado, 1 empresa) — sem montar o índice GLOBAL que o `wallet_doc` dispara.
    Mesmo shape de `wallet_doc` (companyId/entityId/currencyId → string). Fallback
    para o índice global se `company_id` ausente ou a carteira não estiver naquela
    empresa (o caller valida companyId resolvido + company_visible, então um hint
    errado é seguro)."""
    wid = _idstr(wallet_id)
    if not wid:
        return None
    if company_id:
        doc = company_wallets_index(company_id).get(wid)
        if doc:
            return doc
    return wallet_doc(wid)   # fallback: índice global


# ── explosion chain (dependências de processamento) ──────────────────────────
# Ao processar uma carteira que tem um ATIVO DE EXPLOSÃO, a(s) carteira(s) que
# esse ativo aponta precisam ser enviadas JUNTO no `process` — senão o
# processamento não conclui. A relação pode ser ENCADEADA (A→B→C→…): resolvemos
# a cadeia inteira, achatada, via BFS. Ver docs/EXPLOSION_CHAIN.md.
#
# Fontes (ambas já cacheadas 5 min neste seam, então a cadeia sai barata):
#   • `company_wallets_index` → `securitiesForExplosion` de cada carteira.
#   • `security_doc`/`get_security` → `correspondingWallet` do ativo de explosão.

def wallet_explosion_map(company_id):
    """`{walletId_str: [securityId_str, ...]}` — só as carteiras da empresa que
    têm `securitiesForExplosion` não-vazio (ativo de explosão). Deriva do índice
    de carteiras da empresa (`company_wallets_index`, partner_wallets já cacheado
    5 min) — sem chamada extra ao upstream. `{}` quando nenhuma tem explosão."""
    out = {}
    for wid, doc in company_wallets_index(company_id).items():
        ids = [_idstr(s) for s in (doc.get("securitiesForExplosion") or []) if s]
        ids = [s for s in ids if s]
        if ids:
            out[wid] = ids
    return out


def _extract_corresponding_wallet(doc, company_id=None):
    """De um doc de ativo, extrai `{"id","name"}` da `correspondingWallet` (a
    carteira que sofre a explosão), ou None. Aceita tanto `correspondingWallet`
    populado (`{_id, name}`) quanto id cru (resolve o nome pelo índice da
    empresa, quando `company_id` é dado)."""
    cw = (doc or {}).get("correspondingWallet")
    if isinstance(cw, dict):
        wid = _idstr(cw.get("_id"))
        if wid:
            return {"id": wid, "name": cw.get("name") or ""}
    elif cw:
        wid = _idstr(cw)
        if wid:
            name = ""
            if company_id:
                w = company_wallets_index(company_id).get(wid)
                name = (w or {}).get("name") or ""
            return {"id": wid, "name": name}
    return None


def _load_corresponding_wallet(security_id, company_id=None):
    """Resolve `correspondingWallet` de um ativo de explosão, tentando primeiro o
    catálogo já cacheado (`security_doc`) e só caindo no GET pontual
    `/beehus/securities/{id}` se o catálogo não trouxer o campo. `{"id","name"}`
    ou None."""
    got = _extract_corresponding_wallet(security_doc(security_id), company_id)
    if got:
        return got
    try:
        return _extract_corresponding_wallet(
            get_security(security_id=security_id), company_id)
    except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
        _log.warning("get_security(%s) falhou ao resolver correspondingWallet.",
                     security_id)
        return None


def security_corresponding_wallet(security_id, company_id=None):
    """`{"id","name"}` da carteira que o ativo de explosão aponta, ou None.
    Cacheado por ativo (`corresponding_wallet::{id}`, TTL/SWR) — `securities`
    muda pouco, então uma cadeia repetida não refaz os lookups."""
    sid = _idstr(security_id)
    if not sid:
        return None
    return _cached_ttl(f"corresponding_wallet::{sid}",
                       lambda: _load_corresponding_wallet(sid, company_id))


def explosion_chain(company_id, wallet_id):
    """Todas as carteiras arrastadas por `wallet_id` via ativos de explosão, em
    TODOS os níveis, achatadas (BFS). Cada item:
    `{"securityId", "walletId", "name", "level", "viaWalletId"}`. `[]` quando a
    carteira não tem explosão.

    Robusto às bordas validadas em produção: auto-referência (ativo aponta p/ a
    própria carteira), ciclo entre carteiras (A→B→A), carteira-folha (a cadeia
    para) e múltiplos ativos apontando p/ a mesma correspondente (dedup). O
    `seen` inclui a raiz, então nenhum ciclo/auto-ref entra na lista."""
    root = _idstr(wallet_id)
    if not company_id or not root:
        return []
    secmap = wallet_explosion_map(company_id)
    if not secmap:
        return []
    seen = {root}
    out = []
    queue = [(root, 1)]
    while queue:
        current, level = queue.pop(0)
        for sid in secmap.get(current, []):
            cw = security_corresponding_wallet(sid, company_id)
            if not cw:
                continue                      # ativo sem correspondingWallet
            wid = cw["id"]
            if wid in seen:
                continue                      # ciclo / auto-ref / duplicata
            seen.add(wid)
            out.append({
                "securityId":  sid,
                "walletId":    wid,
                "name":        cw["name"],
                "level":       level,
                "viaWalletId": current,
            })
            queue.append((wid, level + 1))    # reexpande os níveis abaixo
    return out


def expand_wallets_with_explosion(company_id, wallet_ids):
    """Expande uma lista de carteiras a processar em `[raízes...] + cadeias de
    explosão`, DEDUPLICADA e preservando a ordem (raízes primeiro, arrastadas
    depois). `wallet_ids` vazio = "todas as carteiras da empresa" no contrato do
    /process → devolve como veio (nada a expandir). Não muta a entrada."""
    roots = [_idstr(w) for w in (wallet_ids or []) if w]
    roots = [w for w in roots if w]
    if not roots:
        return list(wallet_ids or [])
    seen, out = set(), []
    for w in roots:
        if w not in seen:
            seen.add(w)
            out.append(w)
    for w in roots:
        for p in explosion_chain(company_id, w):
            if p["walletId"] not in seen:
                seen.add(p["walletId"])
                out.append(p["walletId"])
    return out


def cash_and_provisions_from_envelope(env, dates, wallet_id):
    """`(cash_by_date, provisions)` derivados de UM envelope processed-position já
    buscado (`processed_envelope`) — SEM I/O. `cash_by_date[date]` = soma dos
    `cashAccounts.values` da carteira na data (ou None); provisions = bloco
    `provisions` do envelope, não-trashed. `env` None/inválido → `({date: None,…}, [])`.
    Núcleo compartilhado: o caller que JÁ tem o envelope (ex.: Conciliação mov., que
    o lê para o PU) deriva caixa+provisões sem 2ª chamada ao endpoint."""
    wid = _idstr(wallet_id)
    if not isinstance(env, dict):
        return {d: None for d in dates}, []
    vals = _cash_values_for_wallet([env], wid)
    cash = _sum_cash_values(vals, dates) if vals else {d: None for d in dates}
    provs = [p for p in (env.get("provisions") or [])
             if isinstance(p, dict) and not p.get("trashed")]
    return cash, provs


def wallet_cash_and_provisions(wallet_id, dates, company_id=None):
    """Para UMA carteira: `(cash_by_date, provisions)` de UMA única resposta
    `processed-position` (na MAIOR data de `dates`) — evita as DUAS chamadas
    idênticas ao mesmo endpoint que as telas faziam (uma p/ caixa, outra p/
    provisões). `cash_by_date[date]` = soma de caixa na data (ou None); provisions
    = bloco `provisions` do envelope na MAIOR data, não-trashed. Sem posição
    processada na maior data (ou API falha) → `({date: None,...}, [])`."""
    want = [str(d)[:10] for d in (dates or []) if d]
    wid = _idstr(wallet_id)
    if not wid or not want:
        return {d: None for d in (dates or [])}, []
    cid = company_id or _company_of_wallet(wid)
    env = processed_envelope(wid, max(want), cid) if cid else None
    return cash_and_provisions_from_envelope(env, dates, wid)
