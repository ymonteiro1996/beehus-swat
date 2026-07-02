from datetime import date, timedelta, datetime, timezone
import json, os, certifi, tempfile, time, threading
# NOTE: `pymongo` is imported LAZILY (inside the connection block below) so an
# instance with Mongo disabled (SWAT_IDENTIFICAR=0) never loads it.

# America/Sao_Paulo is UTC-3 year-round (no DST since 2019). A fixed offset
# avoids a zoneinfo dependency that's missing on some Windows Python builds.
_BRT = timezone(timedelta(hours=-3))


# ── Feature flags (per-instance, via env) ────────────────────────────────────
# Transaction/security IDENTIFICATION runs in a separate instance. Set
# `SWAT_IDENTIFICAR=0` on instances that should NOT serve it: the guarded routes
# short-circuit before their MongoDB reads (the last data reads in the runtime)
# and the UI hides the identification affordances. Default ON so existing
# deployments and the identification instance are unaffected.
IDENTIFICAR_ENABLED = os.environ.get("SWAT_IDENTIFICAR", "1") != "0"


def today_in_brt():
    """Return today's date in BRT.

    Mongo `positionDate` documents are written under BRT business cadence,
    but `date.today()` reads OS-local time and `datetime.utcnow().date()`
    rolls to the next day at 21:00 BRT. Using BRT consistently keeps every
    "hoje" computation aligned with stored dates regardless of host TZ."""
    return datetime.now(_BRT).date()


# ── Atomic JSON write ───────────────────────────────────────────────────────
# Every config/settings file in this project lives on a OneDrive-synced path,
# where `open(..., "w") ; json.dump(...)` races with the sync agent and with
# concurrent WSGI threads: readers between the truncate and the rewrite see
# an empty file. Route every JSON save through here.

def atomic_write_json(path, obj, *, indent=2, ensure_ascii=False):
    """Write `obj` to `path` atomically: write to a sibling .tmp, then os.replace.
    `os.replace` is atomic on NTFS and tolerates OneDrive lock contention better
    than an in-place truncate-rewrite."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".json", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=indent, ensure_ascii=ensure_ascii)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path, text):
    """Same OneDrive-safe atomic-replace pattern as atomic_write_json, for
    plain-text sentinel files (e.g. .analysis_status with values like
    'pending'/'done'/'error')."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", suffix=".txt", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise

# Force dnspython to use public DNS servers so that mongodb+srv:// SRV record
# resolution doesn't time out through restrictive local routers (e.g. 192.168.x.x).
try:
    import dns.resolver
    _public_resolver = dns.resolver.Resolver(configure=False)
    _public_resolver.nameservers = ["8.8.8.8", "8.8.4.4", "1.1.1.1"]
    _public_resolver.timeout = 5
    _public_resolver.lifetime = 10
    dns.resolver.default_resolver = _public_resolver
except Exception:
    pass

DB_NAME                = "Beehus"
CONFIG_FILE            = os.path.join(os.path.dirname(__file__), "data", "config.json")
SETTINGS_FILE          = os.path.join(os.path.dirname(__file__), "data", "settings.json")
NAV_SETTINGS_FILE      = os.path.join(os.path.dirname(__file__), "data", "nav_settings.json")
USER_CONNECTIONS_FILE  = os.path.join(os.path.dirname(__file__), "data", "user_connections.json")


# ── Windows user ────────────────────────────────────────────────────────────

def get_windows_user():
    """Returns the current Windows username in lowercase."""
    return os.environ.get("USERNAME", "unknown").lower()


# ── Per-user connection storage ─────────────────────────────────────────────

def load_user_connections():
    # Read-only since the /setup UI was removed: the connection map is now
    # populated out-of-band (manual edit / provisioning) and consumed only by
    # the boot-time connect below for the offline Mongo-backed CLIs (e.g.
    # `transaction_type_classifier --rebuild`).
    if not os.path.exists(USER_CONNECTIONS_FILE):
        return {}
    with open(USER_CONNECTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# ── DB proxy ─────────────────────────────────────────────────────────────────
# A single object whose internal reference is bound once at boot (when a Mongo
# connection is configured), so all `from db import db` imports see it.

class _DbProxy:
    def __init__(self):
        self._db = None
        self._lock = threading.Lock()

    def _init(self, mongo_db):
        with self._lock:
            self._db = mongo_db

    def _ready(self):
        return self._db is not None

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if self._db is None:
            raise RuntimeError(
                "Database not initialized – no MongoDB connection configured "
                "(set SWAT_IDENTIFICAR and a saved connection for Mongo-backed tools).")
        return getattr(self._db, name)

    def __getitem__(self, name):
        if self._db is None:
            raise RuntimeError(
                "Database not initialized – no MongoDB connection configured "
                "(set SWAT_IDENTIFICAR and a saved connection for Mongo-backed tools).")
        return self._db[name]


db     = _DbProxy()
client = None


def connect_for_cli(uri=None, *, db_name=DB_NAME, run_ensure_indexes=False):
    """Explicitly connect the `db` proxy to MongoDB — for OFFLINE CLIs only.

    The web app NEVER calls this: it runs fully Mongo-free (every route reads
    the Beehus API). There is no implicit connect-at-import anymore, so the
    dashboard never opens a Mongo socket regardless of `SWAT_IDENTIFICAR` or a
    saved `user_connections.json` entry.

    Maintenance scripts that still need raw collection access (e.g.
    `transaction_type_classifier --rebuild`, `scripts/update_liquidation_dates`)
    call this once at startup. URI resolution order:
      1. explicit `uri` argument
      2. `$SWAT_MONGO_URI`
      3. the saved per-user connection in `user_connections.json`
    Raises RuntimeError when none is available. Returns the live MongoClient
    (the caller may `.close()` it)."""
    global client
    if uri is None:
        uri = (os.environ.get("SWAT_MONGO_URI", "").strip()
               or load_user_connections().get(get_windows_user()))
    if not uri:
        raise RuntimeError(
            "No MongoDB URI: pass one, set $SWAT_MONGO_URI, or add an entry for "
            "this Windows user to user_connections.json.")
    from pymongo import MongoClient
    client = MongoClient(uri, tlsCAFile=certifi.where())
    db._init(client[db_name])
    if run_ensure_indexes:
        ensure_indexes()
    return client


def ensure_indexes():
    """Create compound indexes for heavy queries (idempotent)."""
    if not db._ready():
        return
    try:
        db.unprocessedSecurityPositions.create_index(
            [("walletId", 1), ("positionDate", 1)])
        db.processedPosition.create_index(
            [("walletId", 1), ("positionDate", 1)])
        db.navPackages.create_index(
            [("walletId", 1), ("positionDate", 1), ("trashed", 1)])
        db.wallets.create_index(
            [("companyId", 1), ("entityId", 1)])
        db.transactions.create_index(
            [("walletId", 1), ("liquidationDate", 1)])
        # Hot path on the painel: queries that scope by companyId + a single
        # liquidationDate (e.g. global-execution-prices, transaction-check
        # cohorts). Without this, Mongo prefix-scans the (walletId, liq...)
        # index per element of a wallet $in array.
        db.transactions.create_index(
            [("companyId", 1), ("liquidationDate", 1)])
        db.provisions.create_index(
            [("walletId", 1), ("initialDate", 1), ("liquidationDate", 1)])
        db.cashAccounts.create_index(
            [("walletId", 1)])
        # Issues page hot paths
        db.issues.create_index(
            [("status", 1), ("date", 1)])
        db.issues.create_index(
            [("companyId", 1), ("status", 1), ("date", 1), ("type", 1)])
        db.unprocessedSecurityPositions.create_index(
            [("companyId", 1), ("positionDate", -1)])
        db.securityPrices.create_index(
            [("securityId", 1)])
        db.securityMappings.create_index(
            [("companyId", 1)])
        # Posição Projetada (repetir_posicoes) reads securityEvents by
        # operationDate + eventType on every preview build
        # (_dividend_events_by_sid). Without this the query is a full
        # collection scan per wallet previewed.
        db.securityEvents.create_index(
            [("operationDate", 1), ("eventType", 1)])
        # Painel de Controle (controlpanel page) extra columns query the whole
        # collection by positionDate alone — the existing (walletId,
        # positionDate) compound can't serve this, forcing a coll scan
        # on every date pill click. Single-field indexes leading with
        # positionDate give us index-only counting.
        db.processedPosition.create_index([("positionDate", 1)])
        db.navPackages.create_index([("positionDate", 1), ("trashed", 1)])
    except Exception as exc:
        import logging
        logging.warning("ensure_indexes failed (non-critical): %s", exc)
# Not auto-run anymore: indexes are only relevant to a connected proxy, which
# now happens exclusively inside connect_for_cli (pass run_ensure_indexes=True).


# ── TTL cache for full-collection lookups ──────────────────────────────────
# `companies`, `entities`, and the wallet→pair map are hit on *every* page
# load but change rarely (new wallet registrations, renames). A 5-minute TTL
# turns N full scans per minute into at most one, per process. No writes to
# these collections happen from this app, so TTL alone is enough — no
# explicit invalidation hooks needed on mutation paths.

_CACHE_TTL_SECONDS = 300
# RLock (reentrant), NOT Lock: a cached loader may legitimately call another
# cached function on the SAME thread (e.g. a composed filter response whose
# loader calls get_security_names()/get_entity_names()/get_grouping_index()).
# A plain Lock would deadlock the thread against itself while still holding the
# lock, hanging every other cache reader app-wide. RLock allows same-thread
# re-entry while still serialising distinct threads (thundering-herd guard).
_cache_lock        = threading.RLock()
_cache_store       = {}  # key -> (inserted_at_monotonic, value)


def _cached_ttl(key, loader):
    """Return cached value for `key`, or call `loader()` to refresh it.
    Thread-safe under the WSGI pool via double-checked locking so concurrent
    requests don't each trigger a refresh. Loaders may call other `_cached_ttl`
    helpers (the lock is reentrant). Callers must NOT mutate the value."""
    entry = _cache_store.get(key)
    if entry is not None and (time.monotonic() - entry[0]) < _CACHE_TTL_SECONDS:
        return entry[1]
    with _cache_lock:
        entry = _cache_store.get(key)
        if entry is not None and (time.monotonic() - entry[0]) < _CACHE_TTL_SECONDS:
            return entry[1]
        value = loader()
        _cache_store[key] = (time.monotonic(), value)
        return value


def invalidate_cache(key=None):
    """Drop cached entries. `key=None` clears every entry."""
    with _cache_lock:
        if key is None:
            _cache_store.clear()
        else:
            _cache_store.pop(key, None)


def get_company_names():
    """{company_id_str: name_str}. Cached 5 min — do not mutate.

    Sourced from the Beehus API (`GET /beehus/partners/companies`) via
    `beehus_catalog`, with Mongo fallback. Lazy import avoids a cycle."""
    import beehus_catalog
    return beehus_catalog.company_names()


def get_entity_names():
    """{entity_id_str: name_str}. Cached 5 min — do not mutate.

    Sourced from the Beehus API (`GET /beehus/entities`) via `beehus_catalog`,
    with Mongo fallback. Lazy import avoids a cycle."""
    import beehus_catalog
    return beehus_catalog.entity_names()


def get_wallet_names():
    """{wallet_id_str: name_str}. Cached 5 min — do not mutate.

    Sourced from the Beehus API (`partner_wallets` per company, índice global)
    via `beehus_catalog`, with Mongo fallback. Lazy import avoids a cycle."""
    import beehus_catalog
    return beehus_catalog.wallet_names()


def get_grouping_index():
    """Return {grouping_id_str: {"name", "companyId", "trashed", "walletIds"}}.

    Source of truth for the `groupings` collection. Wallet ids live inside
    `wallets[].walletId`. `companyId` is stored directly on the grouping;
    callers should filter on it instead of joining through wallet membership.
    Cached 5 min — do not mutate.

    Sourced from the Beehus API (`list_groupings` per company, índice global)
    via `beehus_catalog`, with Mongo fallback. Lazy import avoids a cycle."""
    import beehus_catalog
    return beehus_catalog.grouping_index()


def get_wallet_currencies():
    """{wallet_id_str: currency_str} for wallets that carry a `currency`.
    Cached 5 min — do not mutate. Used to disambiguate American-format dates
    (MM/DD/YYYY) in transaction descriptions: a USD wallet signals that an
    ambiguous date like 12/01/2034 should be read month-first.

    Sourced from the Beehus API via `beehus_catalog` (índice global de
    carteiras), with Mongo fallback. Lazy import avoids a cycle."""
    import beehus_catalog
    return beehus_catalog.wallet_currencies()


def get_security_names():
    """{security_id_str: beehusName_str}. Cached 5 min — do not mutate.

    Now sourced from the Beehus API (`GET /beehus/securities`) via
    `beehus_catalog`, which caches and falls back to a direct Mongo read if the
    API is unavailable. Lazy import keeps `db` importable without the API layer.
    """
    import beehus_catalog
    return beehus_catalog.security_names()


# ── Helpers ──────────────────────────────────────────────────────────────────

def valid_wallet_ids():
    """Set of walletId strings that exist in `wallets`. Cached 5 min.

    Sourced from the Beehus API via `beehus_catalog` (índice global de
    carteiras), with Mongo fallback. Lazy import avoids a cycle."""
    import beehus_catalog
    return beehus_catalog.valid_wallet_ids()


def sum_cash_by_dates(wallet_id, dates):
    """Return `{date: total_or_None}` for each entry in `dates`.

    Sourced from the processed-position API response (its `cashAccounts.values`
    array, same `{date,value}` shape as Mongo) via `beehus_catalog`, with a
    direct Mongo scan as fallback. None/empty input dates map to None; dates
    with no matching cash values also map to None (rather than 0). Lazy import
    avoids an import cycle (the catalog resolves companyId via resolve_wallet)."""
    import beehus_catalog
    return beehus_catalog.cash_sums_by_dates(wallet_id, dates)


def sum_cash(wallet_id, pos_date):
    """Sum cashAccounts.values for a wallet on a specific date.
    Single-date convenience over `sum_cash_by_dates`; callers fetching
    multiple dates per wallet should call `sum_cash_by_dates` directly."""
    if not pos_date:
        return None
    return sum_cash_by_dates(wallet_id, [pos_date])[pos_date]


def resolve_wallet(wallet_id, projection=None, company_id=None):
    """Find a wallet by id. Returns the doc or None.

    API-only, **zero Mongo**: serve from `beehus_catalog`. Com `company_id`,
    resolve via `partner_wallets(company_id)` (1 chamada, cacheada) em vez do
    índice global de ~19 empresas — o caller que conhece a empresa passa o hint.
    `projection` is accepted for call-site compatibility but ignored — the
    indexed doc already carries every field callers project (companyId / entityId
    / name / currency / currencyId, the last derived from the wallet's `currency`
    code in the catalog). Returns None when the wallet isn't in the API index
    (the dashboard simply doesn't resolve it — no Mongo fallback).
    """
    if not wallet_id:
        return None
    import beehus_catalog
    if company_id:
        return beehus_catalog.wallet_doc_in_company(wallet_id, company_id)
    return beehus_catalog.wallet_doc(wallet_id)


def get_biz_dates(limit, end_date=None):
    """Last `limit` business days (Mon-Fri) ending on end_date (or today), oldest → newest."""
    result  = []
    current = date.fromisoformat(end_date) if end_date else today_in_brt()
    while len(result) < limit:
        if current.weekday() < 5:
            result.append(current.strftime("%Y-%m-%d"))
        current -= timedelta(days=1)
    return list(reversed(result))


def biz_days_between(start_iso, end_iso):
    """Count business days (Mon-Fri) strictly after the earlier date up to and
    including the later date.

    Order-insensitive (Fri→Mon and Mon→Fri both return the same magnitude);
    returns 0 when either date is missing/invalid or the two dates are equal.
    Holidays are NOT excluded — this is a weekday-only approximation. Examples:
    Fri→Mon = 1, Fri→Wed = 3, same date = 0.
    """
    try:
        a = date.fromisoformat(str(start_iso)[:10])
        b = date.fromisoformat(str(end_iso)[:10])
    except (ValueError, TypeError):
        return 0
    if a == b:
        return 0
    lo, hi = (a, b) if a < b else (b, a)
    count = 0
    cur = lo
    while cur < hi:
        cur += timedelta(days=1)
        if cur.weekday() < 5:
            count += 1
    return count


def business_days_before(date_iso, n):
    """Return the ISO date `n` business days (Mon-Fri) before `date_iso`.

    Holidays are NOT excluded — weekday-only approximation, consistent with
    the rest of this module (`get_biz_dates`/`biz_days_between`). `n=0` returns
    the input date unchanged. Returns "" when `date_iso` is missing/invalid.
    Example: Mon → (1) → Fri, Mon → (2) → Thu.
    """
    try:
        d = date.fromisoformat(str(date_iso)[:10])
    except (ValueError, TypeError):
        return ""
    steps = 0
    while steps < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            steps += 1
    return d.isoformat()


def load_config_full():
    """Read config.json once; return (selected, delays, methods, responsible)."""
    if not os.path.exists(CONFIG_FILE):
        return set(), {}, {}, {}
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        items = json.load(f)
    selected    = {(i["companyId"], i["entityId"]) for i in items}
    delays      = {(i["companyId"], i["entityId"]): int(i.get("delay", 0))   for i in items}
    methods     = {(i["companyId"], i["entityId"]): i.get("method", "")      for i in items}
    responsible = {(i["companyId"], i["entityId"]): i.get("responsible", "") for i in items}
    return selected, delays, methods, responsible


def load_config():
    selected, _, _, _ = load_config_full()
    return selected


def load_config_delays():
    """Returns {(companyId, entityId): delay_in_biz_days}"""
    _, delays, _, _ = load_config_full()
    return delays


def load_config_methods():
    """Returns {(companyId, entityId): method_string}"""
    _, _, methods, _ = load_config_full()
    return methods


def load_config_responsible():
    """Returns {(companyId, entityId): responsible_string}"""
    _, _, _, responsible = load_config_full()
    return responsible


def load_settings():
    # `only_daily_position` and `only_with_consumption` are persisted on disk
    # for legacy reasons (the old Cargas/NAV toggle wrote them) but no live
    # code path reads them — pages/nav.py uses load_nav_settings() instead.
    # `wizard_blacklist` likewise has no consumer. Only `company_filter` is
    # actively used (db.get_company_filter / pages/config.settings_save).
    defaults = {"company_filter": []}
    if not os.path.exists(SETTINGS_FILE):
        return defaults
    with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {**defaults, **(data or {})}


def load_nav_settings():
    defaults = {"only_daily_position": False, "only_with_consumption": False}
    if not os.path.exists(NAV_SETTINGS_FILE):
        return defaults
    with open(NAV_SETTINGS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    data.setdefault("only_daily_position", False)
    data.setdefault("only_with_consumption", False)
    return data


def get_company_filter():
    """Returns set of visible company ID strings. Empty set means show all."""
    return set(load_settings().get("company_filter", []))


def company_visible(company_id):
    """True if `company_id` is within the currently-configured visibility filter.
    An empty filter means show all. Empty / None `company_id` is treated as visible
    (callers must handle the missing-param case explicitly)."""
    if not company_id:
        return True
    cf = get_company_filter()
    return (not cf) or (str(company_id) in cf)


# ── Shared helpers (nav / other pages) ───────────────────────────────────────

def biz_days_elapsed(date_str):
    """Count business days from date_str up to (and including) today."""
    try:
        d = date.fromisoformat(date_str)
    except ValueError:
        return 0
    today = today_in_brt()
    if d >= today:
        return 0
    # Fast formula: count full weeks × 5, then add remaining weekdays
    delta = (today - d).days  # calendar days between d and today
    full_weeks, remainder = divmod(delta, 7)
    count = full_weeks * 5
    # Count remaining days after full weeks (from d's weekday forward)
    wd = d.weekday()  # 0=Mon
    for i in range(1, remainder + 1):
        if (wd + i) % 7 < 5:
            count += 1
    return count


def cell_cls(count, total, expected=True):
    if not expected:
        return "bg-gray-50 text-gray-300"
    if count == total:
        return "bg-green-100 text-green-700"
    if count > 0:
        return "bg-yellow-100 text-yellow-700"
    return "bg-red-100 text-red-600"


def wallet_cls(has_value):
    return "bg-green-50 text-green-700" if has_value else "bg-red-50 text-red-600"


def build_wallet_map(settings=None):
    """Returns (wallet_to_pair, pair_total). Cached 5 min.

    Served from the Beehus API via `beehus_catalog.wallet_pairs()` (global wallet
    index). `settings` is accepted for call-site compatibility but ignored — the
    legacy `only_daily_position` / `only_with_consumption` toggles had no live
    consumer, and the filtered Mongo scan they triggered was removed (the API
    wallet shape doesn't carry `hasDailyPosition`/`consumptionIdentifiers`)."""
    import beehus_catalog
    return _cached_ttl(("wallet_map", False, False), beehus_catalog.wallet_pairs)
