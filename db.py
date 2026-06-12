from pymongo import MongoClient
from datetime import date, timedelta, datetime, timezone
import json, os, certifi, tempfile, time, threading

# America/Sao_Paulo is UTC-3 year-round (no DST since 2019). A fixed offset
# avoids a zoneinfo dependency that's missing on some Windows Python builds.
_BRT = timezone(timedelta(hours=-3))


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
    if not os.path.exists(USER_CONNECTIONS_FILE):
        return {}
    with open(USER_CONNECTIONS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def save_user_connections(conns):
    atomic_write_json(USER_CONNECTIONS_FILE, conns)


# ── DB proxy ─────────────────────────────────────────────────────────────────
# A single object whose internal reference can be swapped after registration,
# so all existing `from db import db` imports see the live database immediately.

class _DbProxy:
    def __init__(self):
        self._db = None
        # Serialises swap-the-handle reconnects from /api/setup/save-connection.
        # Without this, two concurrent saves both build a MongoClient + probe,
        # both call _init, and the loser's client leaks (never closed).
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
            raise RuntimeError("Database not initialized – please register at /setup.")
        return getattr(self._db, name)

    def __getitem__(self, name):
        if self._db is None:
            raise RuntimeError("Database not initialized – please register at /setup.")
        return self._db[name]


db     = _DbProxy()
client = None
_client_swap_lock = threading.Lock()


def swap_mongo_client(new_client, db_name):
    """Atomically replace `client` and rebind `db`. Closes the prior client
    so concurrent /setup saves don't leak Mongo connections."""
    global client
    with _client_swap_lock:
        old = client
        client = new_client
        db._init(new_client[db_name])
    if old is not None and old is not new_client:
        try:
            old.close()
        except Exception:
            pass

# Try to connect for the current Windows user
_user  = get_windows_user()
_conns = load_user_connections()
if _user in _conns:
    client = MongoClient(_conns[_user], tlsCAFile=certifi.where())
    db._init(client[DB_NAME])


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

ensure_indexes()


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
    """{company_id_str: name_str}. Cached 5 min — do not mutate."""
    return _cached_ttl(
        "company_names",
        lambda: {str(c["_id"]): (c.get("name") or "")
                 for c in db.companies.find({}, {"name": 1})},
    )


def get_entity_names():
    """{entity_id_str: name_str}. Cached 5 min — do not mutate."""
    return _cached_ttl(
        "entity_names",
        lambda: {str(e["_id"]): (e.get("name") or "")
                 for e in db.entities.find({}, {"name": 1})},
    )


def get_wallet_names():
    """{wallet_id_str: name_str}. Cached 5 min — do not mutate."""
    return _cached_ttl(
        "wallet_names",
        lambda: {str(w["_id"]): (w.get("name") or "")
                 for w in db.wallets.find({}, {"name": 1})},
    )


def get_grouping_index():
    """Return {grouping_id_str: {"name", "companyId", "trashed", "walletIds"}}.

    Source of truth for the `groupings` collection. Wallet ids live inside
    `wallets[].walletId` (not a flat `walletIds` array — that field does not
    exist on the documents). `companyId` is stored directly on the grouping;
    callers should filter on it instead of joining through wallet membership.
    Cached 5 min — do not mutate."""
    def _load():
        out = {}
        cursor = db.groupings.find(
            {},
            {"name": 1, "companyId": 1, "trashed": 1, "wallets.walletId": 1},
        )
        for g in cursor:
            out[str(g["_id"])] = {
                "name":      (g.get("name") or ""),
                "companyId": (g.get("companyId") or ""),
                "trashed":   bool(g.get("trashed")),
                "walletIds": [
                    str(w.get("walletId"))
                    for w in (g.get("wallets") or [])
                    if w.get("walletId")
                ],
            }
        return out
    return _cached_ttl("grouping_index", _load)


def get_wallet_currencies():
    """{wallet_id_str: currency_str} for wallets that carry a `currency`.
    Cached 5 min — do not mutate. Used to disambiguate American-format dates
    (MM/DD/YYYY) in transaction descriptions: a USD wallet signals that an
    ambiguous date like 12/01/2034 should be read month-first."""
    return _cached_ttl(
        "wallet_currencies",
        lambda: {str(w["_id"]): (w.get("currency") or "")
                 for w in db.wallets.find(
                     {"currency": {"$exists": True}}, {"currency": 1})},
    )


def get_security_names():
    """{security_id_str: beehusName_str}. Cached 5 min — do not mutate."""
    return _cached_ttl(
        "security_names",
        lambda: {str(s["_id"]): (s.get("beehusName") or "")
                 for s in db.securities.find({}, {"beehusName": 1})},
    )


# ── Helpers ──────────────────────────────────────────────────────────────────

def valid_wallet_ids():
    """Set of walletId strings that exist in `wallets`. Cached 5 min."""
    return _cached_ttl(
        "valid_wallet_ids",
        lambda: {str(w["_id"]) for w in db.wallets.find({}, {"_id": 1})},
    )


def sum_cash_by_dates(wallet_id, dates):
    """Return `{date: total_or_None}` for each entry in `dates`.

    One `find` over `cashAccounts` for this wallet, grouped in Python —
    collapses N separate aggregations. Matters because `cashAccounts` has
    no index on `walletId` in production, so every query is a full
    collection scan. None/empty input dates map to None; dates with no
    matching cash values also map to None (rather than 0)."""
    wanted = {d[:10] for d in dates if d}
    if not wanted:
        return {d: None for d in dates}
    sums  = {k: 0.0  for k in wanted}
    found = {k: False for k in wanted}
    for doc in db.cashAccounts.find({"walletId": wallet_id}, {"values": 1}):
        for v in doc.get("values", []) or []:
            d_raw = v.get("date")
            if d_raw is None:
                continue
            k = str(d_raw)[:10]
            if k in wanted:
                sums[k]  += float(v.get("value") or 0)
                found[k] = True
    out = {}
    for d in dates:
        if not d:
            out[d] = None
            continue
        k = d[:10]
        out[d] = sums[k] if found.get(k) else None
    return out


def sum_cash(wallet_id, pos_date):
    """Sum cashAccounts.values for a wallet on a specific date.
    Single-date convenience over `sum_cash_by_dates`; callers fetching
    multiple dates per wallet should call `sum_cash_by_dates` directly."""
    if not pos_date:
        return None
    return sum_cash_by_dates(wallet_id, [pos_date])[pos_date]


def resolve_wallet(wallet_id, projection=None):
    """Find a wallet by id. `_id` in `wallets` is always ObjectId; callers
    typically pass a string, so coerce once and look up. Returns the doc
    or None.

    Replaces the wrong-then-retry idiom that was scattered around the codebase
    (`find_one(string)` → falls through → retry with `ObjectId(string)`),
    which spent one wasted DB op per call since string `_id` never matches.
    """
    if not wallet_id:
        return None
    from bson import ObjectId
    from bson.errors import InvalidId
    try:
        oid = ObjectId(str(wallet_id))
    except (InvalidId, TypeError):
        return None
    return db.wallets.find_one({"_id": oid}, projection)


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


def wallet_filter_query(settings):
    """Build a MongoDB filter dict based on active settings toggles."""
    q = {}
    if settings.get("only_daily_position"):
        q["hasDailyPosition"] = True
    if settings.get("only_with_consumption"):
        q["consumptionIdentifiers"] = {"$exists": True, "$not": {"$size": 0}}
    return q


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
    """Returns (wallet_to_pair, pair_total) filtered by settings.
    Cached 5 min per toggle combination (at most 4 entries)."""
    s = settings or {}
    key = ("wallet_map",
           bool(s.get("only_daily_position")),
           bool(s.get("only_with_consumption")))

    def _loader():
        query = wallet_filter_query(s)
        wallet_to_pair = {}
        pair_total     = {}
        for w in db.wallets.find(query, {"companyId": 1, "entityId": 1}):
            wid = str(w["_id"])
            cid = str(w.get("companyId", ""))
            eid = str(w.get("entityId", ""))
            if cid and eid:
                wallet_to_pair[wid] = (cid, eid)
                pair_total[(cid, eid)] = pair_total.get((cid, eid), 0) + 1
        return wallet_to_pair, pair_total

    return _cached_ttl(key, _loader)
