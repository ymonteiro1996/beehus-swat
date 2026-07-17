"""
Security Matcher — identifica se um unprocessedId ja possui cadastro em securities.

Fluxo:
  1. Classificar securityType (via SecurityTypeClassifier)
  2. Extrair features estruturadas do unprocessedId (regex por tipo)
  3. Buscar candidatos em cache indexado + scoring
"""
import re, unicodedata, json, os, logging
from datetime import datetime, date as _date
from collections import defaultdict

# Weekday-only business-day counter (Mon-Fri, no holidays) — shared with the rest
# of the codebase so the maturity-date tolerance uses the same convention. db is a
# leaf module (stdlib + certifi; pymongo is lazy), so this top-level import is
# side-effect-free and circular-import-safe.
from db import biz_days_between

log = logging.getLogger(__name__)

_CACHE_DIR  = os.path.join(os.path.dirname(__file__), "data")
_CACHE_FILE = os.path.join(_CACHE_DIR, "securities_cache.json")
_MAPPING_CACHE_FILE = os.path.join(_CACHE_DIR, "security_mappings_cache.json")

# Fields loaded from MongoDB for each security
_CACHE_FIELDS = {
    "beehusName": 1, "mainId": 1, "ticker": 1, "taxId": 1,
    "isIn": 1, "selicCode": 1, "maturityDate": 1, "indexer": 1,
    "indexerPercentual": 1, "yield": 1, "securityType": 1, "currency": 1,
}

# Fields used as exact-match index keys
_INDEX_KEYS = ["ticker", "taxId", "mainId", "selicCode", "isIn"]


def _strip_accents(text):
    """Remove accents for accent-insensitive matching."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


_NON_DIGIT_RE = re.compile(r"\D")


def _digits_only(s):
    """Strip every non-digit. Lets a bare 14-digit CNPJ in a transaction text
    (e.g. '12345678000190') match a punctuated taxId on the security
    (e.g. '12.345.678/0001-90') — same identifier, different formatting."""
    return _NON_DIGIT_RE.sub("", str(s or ""))

# ── Month maps ─────────────────────────────────────────────────────────────────

_MONTH_PT = {
    "jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
    "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12,
}
_MONTH_EN = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH_ALL = {**_MONTH_PT, **_MONTH_EN}


# ── Date extraction helpers ────────────────────────────────────────────────────

def _parse_date_ex(text, prefer_mdy=False):
    """Parse a date from an unprocessedId. Returns ``(iso, day_known)``:

      • ``iso``       — ISO string ``YYYY-MM-DD`` or ``None`` if nothing parsed.
      • ``day_known`` — True when the text named a real day; False for the
        month/year-only form (``NOV/2032``, where the day defaults to 01). The
        date gate uses this to compare at the operator's precision: day-strict
        when a day was named, month+year only otherwise.

    `prefer_mdy`: when a numeric date is genuinely ambiguous (both parts ≤ 12,
    e.g. 12/01/2034), interpret it as American MM/DD/YYYY instead of the
    Brazilian DD/MM/YYYY default. The caller sets this from context — a USD
    wallet (description side) or a USD security (candidate side). Unambiguous
    dates (one part > 12) are detected by value and ignore this flag.
    """
    text = text.strip()

    # YYYY-MM-DD
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}", True

    # DD-MM-YYYY (dashes, day-first — common in international bond descriptions)
    m = re.search(r"\b(\d{2})-(\d{2})-(\d{4})\b", text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12 and 1 <= b <= 12:          # unambiguous: a is day
            return f"{y}-{b:02d}-{a:02d}", True
        elif b > 12 and 1 <= a <= 12:        # unambiguous: b is day (MM-DD-YYYY)
            return f"{y}-{a:02d}-{b:02d}", True
        elif 1 <= a <= 31 and 1 <= b <= 12:  # ambiguous — default to DD-MM-YYYY (BR)
            return f"{y}-{b:02d}-{a:02d}", True

    # DD-MM-YY (dashes, 2-digit year — e.g. "15-03-35" in international bond names)
    m = re.search(r"\b(\d{2})-(\d{2})-(\d{2})\b", text)
    if m:
        a, b, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = 2000 + y2 if y2 < 80 else 1900 + y2
        if a > 12 and 1 <= b <= 12:
            return f"{y}-{b:02d}-{a:02d}", True
        elif b > 12 and 1 <= a <= 12:
            return f"{y}-{a:02d}-{b:02d}", True
        elif 1 <= a <= 31 and 1 <= b <= 12:  # ambiguous — default DD-MM (BR)
            return f"{y}-{b:02d}-{a:02d}", True

    # DD/MM/YYYY (or MM/DD/YYYY — auto-detect by checking if day > 12)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12 and 1 <= b <= 12:
            # a is day, b is month (DD/MM/YYYY)
            return f"{y}-{b:02d}-{a:02d}", True
        elif b > 12 and 1 <= a <= 12:
            # a is month, b is day (MM/DD/YYYY — US format)
            return f"{y}-{a:02d}-{b:02d}", True
        elif prefer_mdy:
            # Ambiguous + American context → MM/DD/YYYY (a=month, b=day)
            return f"{y}-{m.group(1)}-{m.group(2)}", True
        else:
            # Ambiguous — default to DD/MM/YYYY (BR standard)
            return f"{y}-{m.group(2)}-{m.group(1)}", True

    # DD/MMM/YYYY  (e.g. 13/NOV/2032)
    m = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", text)
    if m:
        d, mo_str, y = int(m.group(1)), m.group(2).lower(), m.group(3)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            return f"{y}-{mo:02d}-{d:02d}", True

    # MMM/YYYY  (e.g. NOV/2032 — day unknown, use 01)
    m = re.search(r"([A-Za-z]{3})/(\d{4})", text)
    if m:
        mo_str, y = m.group(1).lower(), m.group(2)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            return f"{y}-{mo:02d}-01", False  # day not named

    # DD/Mon/YYYY in text like "02/Jan/2029"
    m = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", text)
    if m:
        d, mo_str, y = int(m.group(1)), m.group(2).lower()[:3], m.group(3)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            return f"{y}-{mo:02d}-{d:02d}", True

    # DD/MM/YY or MM/DD/YY (2-digit year) — disambiguate by value like the
    # 4-digit branch, then prefer_mdy, then default to DD/MM (BR). BR maturities
    # (CRA/CRI/debênture) write 17/07/28; US options write 04/17/26.
    m = re.search(r"(\d{2})/(\d{2})/(\d{2})(?!\d)", text)
    if m:
        a, b, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = 2000 + y2 if y2 < 80 else 1900 + y2
        if a > 12 and 1 <= b <= 12:
            return f"{y}-{b:02d}-{a:02d}", True       # DD/MM/YY (BR)
        elif b > 12 and 1 <= a <= 12:
            return f"{y}-{a:02d}-{b:02d}", True       # MM/DD/YY (US)
        elif prefer_mdy and 1 <= a <= 12 and 1 <= b <= 31:
            return f"{y}-{a:02d}-{b:02d}", True       # ambiguous + American → MM/DD
        elif 1 <= a <= 31 and 1 <= b <= 12:
            return f"{y}-{b:02d}-{a:02d}", True       # ambiguous default → DD/MM (BR)

    return None, False


def _parse_date(text, prefer_mdy=False):
    """Backward-compatible wrapper: returns only the ISO string (or None).
    Use `_parse_date_ex` when the caller needs day-precision."""
    return _parse_date_ex(text, prefer_mdy)[0]


def _set_maturity(features, uid, prefer_mdy=False):
    """Set ``maturity_date`` (and ``maturity_day_specified`` when the text named
    a real day) on ``features``. Centralises the parse+flag so the date gate can
    compare day-strict whenever the operator actually wrote a day."""
    iso, day_known = _parse_date_ex(uid, prefer_mdy)
    if iso:
        features["maturity_date"] = iso
        if day_known:
            features["maturity_day_specified"] = True


def _extract_all_dates(text, prefer_mdy=False):
    """Return list of all dates found in text. `prefer_mdy` mirrors
    `_parse_date`: for an ambiguous numeric date (both parts ≤ 12) it picks
    MM/DD/YYYY (American) over the DD/MM/YYYY default."""
    dates = []
    # YYYY-MM-DD
    for m in re.finditer(r"\d{4}-\d{2}-\d{2}", text):
        dates.append(m.group())
    # DD/MM/YYYY or MM/DD/YYYY — disambiguate by value, then by prefer_mdy
    for m in re.finditer(r"(\d{2})/(\d{2})/(\d{4})", text):
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12 and 1 <= b <= 12:
            dates.append(f"{y}-{b:02d}-{a:02d}")        # DD/MM
        elif b > 12 and 1 <= a <= 12:
            dates.append(f"{y}-{a:02d}-{b:02d}")        # MM/DD
        elif prefer_mdy:
            dates.append(f"{y}-{m.group(1)}-{m.group(2)}")  # MM/DD (American)
        else:
            dates.append(f"{y}-{m.group(2)}-{m.group(1)}")  # DD/MM (BR)
    # DD/MM/YY or MM/DD/YY (2-digit year) — disambiguate by value, then
    # prefer_mdy, then default DD/MM (BR). Mirrors _parse_date.
    for m in re.finditer(r"(\d{2})/(\d{2})/(\d{2})(?!\d)", text):
        a, b, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = 2000 + y2 if y2 < 80 else 1900 + y2
        if a > 12 and 1 <= b <= 12:
            dates.append(f"{y}-{b:02d}-{a:02d}")        # DD/MM/YY (BR)
        elif b > 12 and 1 <= a <= 12:
            dates.append(f"{y}-{a:02d}-{b:02d}")        # MM/DD/YY (US)
        elif prefer_mdy and 1 <= a <= 12 and 1 <= b <= 31:
            dates.append(f"{y}-{a:02d}-{b:02d}")        # ambiguous + American
        elif 1 <= a <= 31 and 1 <= b <= 12:
            dates.append(f"{y}-{b:02d}-{a:02d}")        # ambiguous default → DD/MM
    # DD/MMM/YYYY
    for m in re.finditer(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", text):
        d, mo_str, y = int(m.group(1)), m.group(2).lower(), m.group(3)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            dates.append(f"{y}-{mo:02d}-{d:02d}")
    # MMM/YYYY (approximate — day 01)
    for m in re.finditer(r"(?<!\d/)([A-Za-z]{3})/(\d{4})", text):
        mo_str, y = m.group(1).lower(), m.group(2)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            dates.append(f"{y}-{mo:02d}-01")
    return list(dict.fromkeys(dates))  # dedupe preserving order


# ── Feature extractors per securityType ────────────────────────────────────────

def _extract_brazilian_fund(uid):
    features = {}
    # Fund code in parentheses: (52532)
    m = re.search(r"\((\d{4,6})\)", uid)
    if m:
        features["fund_code"] = m.group(1)
    else:
        # Fund code after dash: - 52532
        m = re.search(r"-\s*(\d{4,6})(?:\s*-|\s*$)", uid)
        if m:
            features["fund_code"] = m.group(1)

    # CNPJ: XX.XXX.XXX/XXXX-XX (pontuado) OU 14 dígitos crus (ex.:
    # "30934757000113 - BTG ...", comum quando o upstream tira a pontuação).
    # As duas formas são o CNPJ do fundo — o identificador canônico; o
    # `_exact_identifier_match` compara só os dígitos contra taxId E mainId
    # (o mainId de fundo brasileiro É o CNPJ).
    m = re.search(r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", uid)
    if m:
        features["cnpj"] = m.group(1)
    else:
        m = re.search(r"(?<!\d)(\d{14})(?!\d)", uid)
        if m:
            features["cnpj"] = m.group(1)

    # ISIN (when the upstream includes the ISIN in the fund description)
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", uid)
    if m:
        features["isin"] = m.group(1)

    # Name: text between code and separator
    name = uid
    name = re.sub(r"\(\d{4,6}\)\s*", "", name)      # remove (code)
    name = re.sub(r"\s*-\s*\d{4,6}(?:\s*-.*)?$", "", name)  # remove trailing code
    name = re.sub(r"\s*-?\s*\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2}", "", name)  # CNPJ formatted
    name = re.sub(r"(?<!\d)\d{14}(?!\d)\s*-?\s*", "", name)  # remove CNPJ cru
    name = name.strip(" -")
    if name:
        features["name"] = name

    return features


def _extract_stock_etf(uid):
    features = {}
    # Ticker in parentheses: (ABCB4)
    m = re.search(r"\(([A-Z]{3,6}\d{1,2})\)", uid)
    if m:
        features["ticker"] = m.group(1)
    else:
        # Standalone ticker at start
        m = re.match(r"([A-Z]{3,6}\d{1,2})\b", uid)
        if m:
            features["ticker"] = m.group(1)

    # Name after ticker
    name = uid
    name = re.sub(r"\([A-Z]{3,6}\d{1,2}\)\s*", "", name)
    name = re.sub(r"^[A-Z]{3,6}\d{1,2}\s*-?\s*", "", name)
    name = re.sub(r"\s*-\s*[A-Z]{3,6}\d{1,2}$", "", name)
    # Remove "ALUGUEL -" prefix
    name = re.sub(r"^ALUGUEL\s*-\s*", "", name, flags=re.IGNORECASE)
    name = name.strip(" -")
    if name:
        features["name"] = name

    # ISIN (present when stock/ETF is identified by its ISIN in the upstream system)
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", uid)
    if m:
        features["isin"] = m.group(1)

    return features


_BOND_INSTRUMENT_RE = re.compile(
    r"\b(DEBENTURE|CDCA|CPRF|FIDC|LFSN|LFS|DEB|CRI|CRA|LCA|LCI|LCD|CDB|CCB|LF|LIG|FND)\b",
    re.IGNORECASE)


def _extract_bond(uid):
    features = {}
    # Instrument type. Longest-first so LFSN/LFS/CDCA win over LF/CRA (the \b
    # already guards, but keep the order explicit).
    m = _BOND_INSTRUMENT_RE.search(uid)
    if m:
        features["instrument"] = m.group(1).upper()
        # 2+ DISTINCT vehicles named (e.g. the combined "CRI/CRA" label the
        # upstream emits when unsure) → no definitive vehicle; flag so the
        # vehicle gate in `_score_candidate` doesn't reject against either one.
        veh = {_canon_vehicle(x) for x in _BOND_INSTRUMENT_RE.findall(uid)} - {""}
        if len(veh) >= 2:
            features["vehicle_ambiguous"] = True

    # CETIP code — two patterns:
    # 1. Explicit prefix from custodian systems: "CETIP_APFD19"
    m = re.search(r"CETIP_([A-Z0-9]+)", uid)
    if m:
        features["cetip_code"] = m.group(1)
    # 2. Standalone 6-char code: 4 uppercase letters + 2 alphanumeric (e.g. APFD19,
    #    ARTP13, BHIAA0). Classic format for Brazilian debenture/CRI/CRA CETIP codes.
    #    Only runs when the prefix pattern didn't fire.
    if "cetip_code" not in features:
        m = re.search(r"\b([A-Z]{4}[A-Z0-9]{2})\b", uid)
        if m and m.group(1) not in (features.get("instrument"), features.get("isin")):
            features["cetip_code"] = m.group(1)

    # ISIN checked first so Pattern 1 below won't also capture it as internal_code
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", uid)
    if m and m.group(1) != features.get("instrument"):
        features["isin"] = m.group(1)

    # Specific code like LF0020003KK, CDB6246IACD, 24G01629552, etc.
    # Guard includes isin and cetip_code so they aren't also stored as internal_code.
    m = re.search(r"\b([A-Z]{2,4}\d{4,}[A-Z0-9]*)\b", uid)
    if m and m.group(1) not in (features.get("instrument"), features.get("isin"),
                                 features.get("cetip_code")):
        features["internal_code"] = m.group(1)

    # Also try alphanumeric code starting with digits (e.g. 24G01629552)
    if "internal_code" not in features:
        m = re.search(r"\b(\d{2,4}[A-Z]\d{5,})\b", uid)
        if m:
            features["internal_code"] = m.group(1)

    # Issuer name (multiple heuristics)
    instrument = features.get("instrument", "")

    # Strategy 1: "INSTRUMENT ISSUER - DATE" pattern (e.g. "DEB LIGHT - NOV/2032")
    if instrument:
        pattern = re.escape(instrument) + r"\s+(.+?)(?:\s*-\s*(?:\d|[A-Z]{3}/\d{4})|$)"
        m = re.search(pattern, uid, re.IGNORECASE)
        if m:
            issuer = m.group(1).strip(" -")
            # Strip "INDEXER+RATE%" spread first (e.g. "IPCA+7,45%", "CDI + 0,80%")
            # Must run before the rate-only strip or "IPCA +" is left behind.
            issuer = re.sub(
                r"\s*(IPCA|CDI|SELIC|DU|PRE|IGPM|IGP-?M|DI)\s*\+\s*\d+(?:[,\.]\d+)?%.*$",
                "", issuer, flags=re.IGNORECASE)
            # Strip remaining "RATE%..." patterns (integer or decimal)
            issuer = re.sub(r"\s*\d+(?:[,\.]\d+)?%.*$", "", issuer)
            issuer = re.sub(r"\s*(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)/\d{4}.*$", "", issuer, flags=re.IGNORECASE)
            # Strip leading indexer keywords
            issuer = re.sub(r"^(PRE|CDI|IPCA|SELIC|DU)\s+", "", issuer, flags=re.IGNORECASE)
            # Strip trailing indexer keywords, including any residual "+" operator
            issuer = re.sub(r"\s+(IPCA|CDI|SELIC|DU|PRE|IGPM|IGP-?M|DI)\s*\+?\s*$", "", issuer, flags=re.IGNORECASE)
            # Strip leading alphanumeric CODES (must contain at least one digit — not pure names)
            issuer = re.sub(r"^(?=.*\d)[A-Z0-9]{8,}\s*-?\s*", "", issuer)
            issuer = issuer.strip(" -")
            if issuer and len(issuer) > 2:
                # Only skip if what's left is the instrument itself (e.g. "CRI CRI…")
                if issuer.upper() != instrument.upper():
                    features["issuer"] = issuer

    # Strategy 2: explicit "- BANK/ISSUER NAME -" in the middle
    if "issuer" not in features:
        parts = [p.strip() for p in uid.split(" - ") if p.strip()]
        for part in parts:
            # Skip parts that look like codes, dates, rates, or known keywords
            if re.match(r"^[\d,\.%]+", part):
                continue
            if re.match(r"^\d{4}-\d{2}-\d{2}$", part):
                continue
            if re.match(r"^(PRE|CDI|IPCA|SELIC|IGPM)\b", part, re.IGNORECASE):
                continue
            if instrument and part.upper().startswith(instrument):
                continue
            if re.search(r"\b(BANCO|S\.?A\.?|INVESTIMENTO|FINANCEIRA)\b", part, re.IGNORECASE):
                # Apply the same cleanup as strategy 1 to avoid capturing rates/codes
                cleaned = re.sub(r"\s*\d+(?:[,\.]\d+)?%.*$", "", part)
                cleaned = re.sub(r"\s*\d{1,2}[-/]\d{2}[-/]\d{2,4}.*$", "", cleaned)
                cleaned = re.sub(r"\s+(?=.*\d)[A-Z0-9]{6,}\s*$", "", cleaned)
                cleaned = cleaned.strip()
                if cleaned:
                    features["issuer"] = cleaned
                break

    # Maturity date
    _set_maturity(features, uid)

    # Rate/yield
    rates = re.findall(r"(\d+(?:[,\.]\d+)?)\s*%", uid)
    if rates:
        features["rate"] = rates[-1].replace(",", ".")  # last rate is usually the yield

    # Indexer / regime — the leftmost ACTIVE regime is the PRIMARY (and feeds the
    # rate-regime gate in `_score_candidate`). `_scan_regimes` is coefficient-
    # aware, so the BR formula "0%CDI+12,05%aa" (a pré-fixado bond) yields PRE,
    # not the zero-weighted CDI; structure words (PRÉ-PAGAMENTO/EMBARQUE) don't
    # shadow the real indexer; and a parenthetical benchmark ("Pré-fixado (110%
    # CDI)") keeps PRE as primary (written first).
    ordered = _scan_regimes(uid)
    if ordered:
        features["indexer"] = ordered[0]

    # Canonical bank issuers named in the description (for the issuer gate). Kept
    # as a sorted, JSON-safe list separate from the heuristic `issuer` string.
    iss = _issuers_in(uid)
    if iss:
        features["issuers"] = sorted(iss)

    return features


# Coupon status by bond type. "NTN-B P" = NTN-B Principal (no coupon).
_GOV_COUPON_MAP = {
    "LFT":   False,   # Tesouro Selic — no coupon
    "LTN":   False,   # Tesouro Prefixado — no coupon
    "NTN-F": True,    # Tesouro Prefixado com Juros Semestrais — coupon
    "NTN-B": True,    # Tesouro IPCA+ com Juros Semestrais — coupon
    "NTN-B P": False, # Tesouro IPCA+ / NTN-B Principal — no coupon
}


def _candidate_gov_coupon(sec):
    """Coupon status of a gov bond candidate from beehusName/mainId.
    Returns True (has coupon), False (no coupon), or None (unknown).
    Memoised on the sec dict."""
    cached = sec.get("_gov_coupon")
    if cached is not None:
        return None if cached == "?" else (cached == "Y")
    text = (str(sec.get("beehusName") or "") + " "
            + str(sec.get("mainId") or "")).upper()
    # NTN-B Principal must be checked before the generic NTN-B pattern
    if re.search(r"\bNTN-?B\s*P(?:RINCIPAL)?\b|\bNTNBP\b", text):
        result = False
    elif re.search(r"\bLFT\b|\bLTN\b", text):
        result = False
    elif re.search(r"\bNTN-?F\b|\bNTNF\b", text):
        result = True
    elif re.search(r"\bNTN-?B\b|\bNTNB\b", text):
        result = True
    else:
        result = None
    sec["_gov_coupon"] = "?" if result is None else ("Y" if result else "N")
    return result


def _extract_gov_bond(uid):
    features = {}
    # Bond type — NTN-B Principal must be checked before the generic NTN-B pattern
    # so "NTN-B PRINCIPAL" isn't just captured as plain "NTN-B".
    if re.search(r"\bNTN-?B\s+P(?:RINCIPAL)?\b|\bNTNBP\b", uid, re.IGNORECASE):
        features["bond_type"] = "NTN-B P"
    else:
        m = re.search(r"\b(LFT|NTN-?B|NTN-?F|LTN|NTNB|NTNF)\b", uid, re.IGNORECASE)
        if m:
            btype = m.group(1).upper().replace("-", "")
            # Normalize: NTNB → NTN-B
            if btype == "NTNB":
                btype = "NTN-B"
            elif btype == "NTNF":
                btype = "NTN-F"
            features["bond_type"] = btype

    # Coupon — derived from bond type
    bt = features.get("bond_type")
    if bt in _GOV_COUPON_MAP:
        features["coupon"] = "Sim" if _GOV_COUPON_MAP[bt] else "Não"

    # SELIC code (6-digit number in specific patterns)
    m = re.search(r"\b(\d{6})\b", uid)
    if m:
        code = m.group(1)
        # Avoid dates (DDMMYY)
        if not re.search(r"\d{2}/\d{2}/\d{2}", uid):
            features["selic_code"] = code

    # Maturity date — first try the general parser (handles DD/MM/YYYY, MMM/YYYY, etc.)
    _set_maturity(features, uid)

    # Fallback: MMM-YY or MMM-YYYY with dash separator (e.g. "LTN JAN-29", "NTN-B AGO-2030").
    # The general parser only handles slash-separated month/year, so dash formats land here.
    # Day is inferred from the bond-type market convention (LTN/LFT/NTN-F → 1st, NTN-B → 15th),
    # but maturity_day_specified is left unset so the date gate compares month+year only —
    # correct because the operator did not name a day explicitly.
    if "maturity_date" not in features:
        m = re.search(r"\b([A-Za-z]{3})-(\d{4}|\d{2})\b", uid)
        if m:
            mo_str, yr_str = m.group(1).lower(), m.group(2)
            mo = _MONTH_ALL.get(mo_str)
            if mo:
                yr = int(yr_str)
                if yr < 100:
                    yr = 2000 + yr if yr < 80 else 1900 + yr
                btype = features.get("bond_type", "")
                day = 15 if btype in ("NTN-B", "NTN-B P") else 1
                features["maturity_date"] = f"{yr}-{mo:02d}-{day:02d}"

    # Indexer
    m = re.search(r"\b(SELIC|IPCA|IPC-A|PRE)\b", uid, re.IGNORECASE)
    if m:
        idx = m.group(1).upper()
        if idx == "IPC-A":
            idx = "IPCA"
        features["indexer"] = idx

    # ISIN (government bonds in the DB always have isIn populated)
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", uid)
    if m:
        features["isin"] = m.group(1)

    return features


def _extract_fund_intl(uid):
    features = {}
    # ISIN: 2-letter country + 9 alphanumeric + 1 numeric check digit. The
    # trailing \d keeps ordinary 11-12 letter words from being read as ISINs.
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{8,9}\d)\b", uid)
    if m:
        features["isin"] = m.group(1)

    # External code (leading digits)
    m = re.match(r"(\d{6,10})", uid)
    if m:
        features["external_code"] = m.group(1)

    # Name after "Ativo Generico -" or "Ativo Genérico -"
    m = re.search(r"Ativo\s+Gen[eé]rico\s*-\s*(.+?)(?:\s*$)", uid, re.IGNORECASE)
    if m:
        features["name"] = m.group(1).strip()
    else:
        # Fallback: last segment after " - "
        parts = uid.split(" - ")
        if len(parts) >= 2:
            features["name"] = parts[-1].strip()

    return features


def _extract_futures(uid):
    features = {}
    # ISIN starting with BRBMEF
    m = re.search(r"(BRBMEF[A-Z0-9]+)", uid)
    if m:
        features["isin"] = m.group(1)

    # Ticker: DI1, DOL, WDO, DAP, DDI, FRC etc + contract code
    # Handles both "DI1FF35" and "DI1_F35" patterns
    m = re.search(r"[_]?(DI1|DOL|WDO|DAP|DDI|FRC|IND|WIN|BGI|SJC)[_]?([A-Z]{1,2}\d{2})\b", uid)
    if m:
        features["ticker_base"] = m.group(1)
        features["contract"] = m.group(2)
        features["ticker"] = m.group(1) + m.group(2)

    # Alternative: FUTDI1F35 pattern
    if "ticker" not in features:
        m = re.search(r"FUT(DI1|DOL|WDO|DAP)([A-Z]\d{2})", uid)
        if m:
            features["ticker_base"] = m.group(1)
            features["contract"] = m.group(2)
            features["ticker"] = m.group(1) + m.group(2)

    # Maturity date
    _set_maturity(features, uid)

    return features


def _extract_options(uid):
    features = {}
    # Option type — extract first so underlying patterns can use it as anchor
    m = re.search(r"\b(CALL|PUT)\b", uid, re.IGNORECASE)
    if m:
        features["option_type"] = m.group(1).upper()

    # Underlying ticker — B3 tickers are 3-5 uppercase letters + optional trailing digit
    # (e.g. BBDC4, PETR4, VALE3). Priority order:
    # 1. Ticker immediately after CALL/PUT keyword (most common format in these UIDs)
    m = re.search(r"\b(?:call|put)\s+([A-Z]{3,5}\d?)\b", uid, re.IGNORECASE)
    if m:
        features["underlying"] = m.group(1).upper()
    else:
        # 2. Ticker between hyphens with optional spaces
        m = re.search(r"-\s*([A-Z]{3,5}\d?)\s*-", uid)
        if m:
            features["underlying"] = m.group(1)
        else:
            # 3. Ticker between generic separators (underscore, space, hyphen)
            m = re.search(r"[_\s-]([A-Z]{3,5}\d?)[_\s-]", uid)
            if m:
                features["underlying"] = m.group(1)

    # Strike price
    m = re.search(r"@\s*(\d+(?:[.,]\d+)?)", uid)
    if m:
        features["strike"] = m.group(1).replace(",", ".")
    else:
        # Pattern: _STRIKE_DATE
        m = re.search(r"_(\d+)_\d{8}", uid)
        if m:
            features["strike"] = m.group(1)

    # Expiry date — options are American: an ambiguous 2-digit numeric date
    # (e.g. 04/05/26) is MM/DD, not the BR DD/MM default.
    date = _parse_date(uid, prefer_mdy=True)
    if date:
        features["expiry"] = date

    # Expiry month + year from Portuguese abbreviated format: "Ago-26", "Jan-2026"
    # Stored separately for partial mainId matching (full expiry may be unavailable).
    _PT_MONTHS = {
        "jan": "01", "fev": "02", "mar": "03", "abr": "04",
        "mai": "05", "jun": "06", "jul": "07", "ago": "08",
        "set": "09", "out": "10", "nov": "11", "dez": "12",
    }
    m = re.search(r"\b(jan|fev|mar|abr|mai|jun|jul|ago|set|out|nov|dez)-(\d{2}|\d{4})\b",
                  uid, re.IGNORECASE)
    if m:
        yr = int(m.group(2))
        if yr < 100:
            yr = 2000 + yr
        features["expiry_month"] = _PT_MONTHS[m.group(1).lower()]
        features["expiry_year"]  = str(yr)

    # B3 option ticker — extracted only when explicitly present in the UID.
    # Format: 4 uppercase letters + 1 letter [A-X] (encodes type+month) +
    #         2+ digits (strike) + optional W-suffix for decimal strikes (W1, W2…).
    # Guard \d{2,} prevents collision with 6-char CETIP codes (e.g. BHIAA0 has
    # only 1 trailing digit and would otherwise match).
    # Examples: PETRH325W1, PETRA36, PETRG346W1 — but NOT "Call PETR4 @ 36 Jan-26"
    # (no explicit ticker in that UID).
    m = re.search(r"\b([A-Z]{4}[A-X]\d{2,}(?:W\d+)?)\b", uid)
    if m:
        features["ticker"] = m.group(1)

    # ISIN
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", uid)
    if m:
        features["isin"] = m.group(1)

    # External code
    m = re.search(r"-\s*([A-Z0-9]{8,12})\s*$", uid)
    if m:
        features["external_code"] = m.group(1)

    return features


def _extract_otc(uid):
    features = {}
    # Pure numeric code (handle repeated pattern: 011311011311 → 011311)
    m = re.match(r"^(\d{6,14})$", uid.strip())
    if m:
        code = m.group(1)
        # Check if it's a repeated 6-digit code (e.g. 011311011311)
        if len(code) == 12 and code[:6] == code[6:]:
            features["external_code"] = code[:6]
        else:
            features["external_code"] = code
        return features

    # Alphanumeric code at start
    m = re.match(r"^([A-Z0-9]{6,14})\b", uid)
    if m:
        features["external_code"] = m.group(1)

    # ISIN (OTC instruments can be identified by ISIN when present in the description)
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b", uid)
    if m:
        features["isin"] = m.group(1)

    # Name after "Ativo Generico -"
    m = re.search(r"Ativo\s+Gen[eé]rico\s*-\s*(.+?)(?:\s*$)", uid, re.IGNORECASE)
    if m:
        features["name"] = m.group(1).strip()

    # Maturity date
    _set_maturity(features, uid)

    return features


def _extract_brazilian_repo(uid):
    features = {}
    # Maturity date
    _set_maturity(features, uid)

    # Indexer
    m = re.search(r"\b(CDI|IPCA|SELIC)\b", uid, re.IGNORECASE)
    if m:
        features["indexer"] = m.group(1).upper()

    # Rate
    m = re.search(r"(\d+[,\.]\d+)\s*%", uid)
    if m:
        features["rate"] = m.group(1).replace(",", ".")

    # Name/type
    if "COMPROMISSADA" in uid.upper():
        features["name"] = "COMPROMISSADA"
    else:
        # General name extraction
        parts = uid.split(" - ")
        if parts:
            features["name"] = parts[0].strip()

    return features


def _extract_sovereign_bonds(uid):
    features = {}
    # ISIN
    m = re.search(r"\b([A-Z]{2}[A-Z0-9]{9,10})\b", uid)
    if m:
        candidate = m.group(1)
        # Skip if it's a known non-ISIN pattern
        if not candidate.startswith("CPN") and not candidate.startswith("REGS"):
            features["isin"] = candidate

    # Issuer (first meaningful segment)
    m = re.search(r"^(.*?)(?:\s*-|\s+CPN|\s+ZERO|\s+\d)", uid)
    if m:
        issuer = m.group(1).strip()
        # Clean known prefixes
        issuer = re.sub(r"^Corporate Fixed Income---[A-Z0-9]+-", "", issuer)
        if issuer and len(issuer) > 3:
            features["issuer"] = issuer

    # Maturity date
    _set_maturity(features, uid)

    return features


def _extract_generic(uid):
    """Fallback extractor for privateMarket, realAssets, poc."""
    features = {}
    # Code prefix (for poc: 006229BRAD...)
    m = re.match(r"^(\d{6})", uid)
    if m:
        features["code_prefix"] = m.group(1)
        features["name"] = uid[6:].strip()
    else:
        features["name"] = uid.strip()
    return features


# ── Extractor registry ─────────────────────────────────────────────────────────

_EXTRACTORS = {
    "brazilianFund":           _extract_brazilian_fund,
    "stockEtf":                _extract_stock_etf,
    "bond":                    _extract_bond,
    "brazilianGovernmentBond": _extract_gov_bond,
    "fund":                    _extract_fund_intl,
    "futures":                 _extract_futures,
    "options":                 _extract_options,
    "otc":                     _extract_otc,
    "brazilianRepo":           _extract_brazilian_repo,
    "sovereignBonds":          _extract_sovereign_bonds,
    "privateMarket":           _extract_generic,
    "realAssets":              _extract_generic,
    "poc":                     _extract_generic,
}


_STRONG_FEATURE_KEYS = (
    "ticker", "isin", "cnpj", "cetip_code", "internal_code", "external_code",
    "fund_code", "underlying", "selic_code",
)


def _extract_generic_codes(uid, features):
    """Extract up to 3 generic alphanumeric codes from `uid` that were not
    captured by any other feature, for comparison against mainId.

    Two-pass strategy per coarse token (spaces and strong separators split the
    uid; hyphens are kept inside the token):

      Pass 1 — the full coarse token qualifies if: purely [A-Za-z0-9-],
               ≥ 6 total chars, AND either (a) ≥ 1 letter AND ≥ 1 digit
               (mixed alphanumeric), or (b) purely numeric ≥ 6 digits (no
               hyphens). Hyphens kept so "CRI-23C2706233" matches a mainId.

      Pass 2 — each qualifying coarse token is split on hyphens; sub-tokens
               qualify if: purely [A-Za-z0-9], ≥ 6 chars, AND either (a)
               mixed letter+digit, or (b) purely numeric. Emitted immediately
               after their parent so both forms are available as candidates.

    Any candidate already present as a feature value (identifier-like codes
    already captured by the per-type extractor) is excluded.
    """
    # Exclusion set: existing feature values that look like identifiers.
    # Add both the value as-is and a hyphen-stripped variant.
    existing = set()
    for v in features.values():
        if v and isinstance(v, str) and len(v) >= 6:
            v_up = v.upper()
            if re.fullmatch(r"[A-Za-z0-9\-]+", v_up):
                existing.add(v_up)
                existing.add(v_up.replace("-", ""))

    # Coarse split: spaces and strong separators; hyphens stay inside tokens.
    coarse_tokens = re.split(r"[\s/%.:()\[\];,@#&=+*!?\"'<>|\\]+", uid)

    seen = set()
    candidates = []

    def _add(tok):
        key = tok.upper()
        if key not in seen and key not in existing:
            seen.add(key)
            candidates.append(tok)

    for coarse in coarse_tokens:
        tok = coarse.strip("-")
        if not tok:
            continue
        # Pass 1: alphanumeric+hyphen, ≥6 chars, mixed letter+digit OR purely numeric
        _p1_mixed   = (re.search(r"[A-Za-z]", tok) and re.search(r"\d", tok))
        _p1_numeric = (re.fullmatch(r"\d+", tok) and len(tok) >= 6)
        if (len(tok) >= 6
                and re.fullmatch(r"[A-Za-z0-9\-]+", tok)
                and (_p1_mixed or _p1_numeric)):
            _add(tok)
            # Pass 2: sub-tokens from splitting on hyphens (mixed OR purely numeric)
            for sub in tok.split("-"):
                if (sub and len(sub) >= 6
                        and re.fullmatch(r"[A-Za-z0-9]+", sub)
                        and (
                            (re.search(r"[A-Za-z]", sub) and re.search(r"\d", sub))
                            or re.fullmatch(r"\d+", sub)
                        )):
                    _add(sub)

    result = {}
    for i, code in enumerate(candidates[:3], 1):
        result[f"generic_code_{i}"] = code
    return result


def extract_features(uid, security_type):
    """Extract structured features from an unprocessedId given its securityType."""
    extractor = _EXTRACTORS.get(security_type, _extract_generic)
    features = extractor(uid)
    features.update(_extract_generic_codes(uid, features))
    return features


# ── Query builders per securityType ────────────────────────────────────────────

def _build_query_brazilian_fund(features):
    or_clauses = []
    if "cnpj" in features:
        or_clauses.append({"taxId": features["cnpj"]})
    if "fund_code" in features:
        or_clauses.append({"mainId": {"$regex": re.escape(features["fund_code"]), "$options": "i"}})
    if "name" in features:
        # Use first 2-3 significant words
        words = [w for w in re.split(r"[\s\-_/,\.\(\)]", features["name"]) if len(w) >= 3]
        if words:
            pattern = ".*".join(re.escape(w) for w in words[:3])
            or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})
    return or_clauses


def _build_query_stock_etf(features):
    or_clauses = []
    if "ticker" in features:
        or_clauses.append({"ticker": features["ticker"]})
        or_clauses.append({"mainId": features["ticker"]})
    if "name" in features:
        words = [w for w in re.split(r"[\s\-_/,\.\(\)]", features["name"]) if len(w) >= 3]
        if words:
            pattern = ".*".join(re.escape(w) for w in words[:2])
            or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})
    return or_clauses


def _build_query_bond(features):
    or_clauses = []

    if "cetip_code" in features:
        or_clauses.append({"mainId": {"$regex": re.escape(features["cetip_code"]), "$options": "i"}})
    if "internal_code" in features:
        or_clauses.append({"mainId": {"$regex": re.escape(features["internal_code"]), "$options": "i"}})

    instrument = features.get("instrument", "")
    issuer = features.get("issuer", "")

    if instrument and issuer:
        issuer_words = [w for w in issuer.split() if len(w) >= 3]

        # Strategy 1: instrument + first 2 issuer words (strict)
        if len(issuer_words) >= 2:
            pattern = ".*".join([re.escape(instrument)] + [re.escape(w) for w in issuer_words[:2]])
            or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})

        # Strategy 2: instrument + each significant issuer word individually (loose)
        for w in issuer_words:
            if len(w) >= 4:  # skip short words like "S.A."
                pattern = re.escape(instrument) + ".*" + re.escape(w)
                or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})

        # Strategy 3: issuer in mainId (e.g. LFBRADESCO...)
        main_issuer = "".join(w for w in issuer_words if len(w) >= 4)
        if main_issuer:
            or_clauses.append({"mainId": {"$regex": re.escape(instrument + main_issuer), "$options": "i"}})

    elif instrument:
        or_clauses.append({"beehusName": {"$regex": re.escape(instrument), "$options": "i"}})

    return or_clauses


def _build_query_gov_bond(features):
    or_clauses = []
    if "selic_code" in features:
        or_clauses.append({"selicCode": features["selic_code"]})
        # Also try adjacent codes (off-by-one is common)
        try:
            code_int = int(features["selic_code"])
            or_clauses.append({"selicCode": str(code_int + 1)})
            or_clauses.append({"selicCode": str(code_int - 1)})
        except ValueError:
            pass

    # Bond type in beehusName
    if "bond_type" in features:
        or_clauses.append({"beehusName": {"$regex": re.escape(features["bond_type"]), "$options": "i"}})

    # Also search ISIN-like mainId patterns for gov bonds
    if "bond_type" in features:
        bt = features["bond_type"].replace("-", "")
        or_clauses.append({"mainId": {"$regex": bt, "$options": "i"}})

    return or_clauses


def _build_query_fund_intl(features):
    or_clauses = []
    if "isin" in features:
        or_clauses.append({"isIn": features["isin"]})
    if "external_code" in features:
        or_clauses.append({"mainId": {"$regex": re.escape(features["external_code"]), "$options": "i"}})
    if "name" in features:
        words = [w for w in re.split(r"[\s\-_/,\.\(\)]", features["name"]) if len(w) >= 3]
        if words:
            pattern = ".*".join(re.escape(w) for w in words[:3])
            or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})
    return or_clauses


def _build_query_futures(features):
    or_clauses = []
    if "isin" in features:
        or_clauses.append({"isIn": {"$regex": re.escape(features["isin"]), "$options": "i"}})
    if "ticker" in features:
        ticker = features["ticker"]
        or_clauses.append({"ticker": ticker})
        or_clauses.append({"ticker": "FUT" + ticker})       # DB stores as FUTDI1F35
        or_clauses.append({"mainId": ticker})
        or_clauses.append({"mainId": "FUT" + ticker})
    if "ticker_base" in features and "contract" in features:
        # Also search partial match for base + contract
        base = features["ticker_base"]
        contract = features["contract"]
        or_clauses.append({"ticker": {"$regex": base + ".*" + re.escape(contract[-3:]), "$options": "i"}})
    return or_clauses


def _build_query_options(features):
    or_clauses = []

    # Strategy 1: structured mainId/ticker pattern (CALL_AAPL_280_17042026)
    if "option_type" in features and "underlying" in features and "strike" in features:
        strike_int = features["strike"].split(".")[0]  # "280.0" → "280"
        pattern = f"{features['option_type']}_{features['underlying']}_{strike_int}"
        or_clauses.append({"mainId": {"$regex": re.escape(pattern), "$options": "i"}})
        or_clauses.append({"ticker": {"$regex": re.escape(pattern), "$options": "i"}})

    # Strategy 2: beehusName pattern (Call Apple @280)
    parts = []
    if "option_type" in features:
        parts.append(re.escape(features["option_type"]))
    if "underlying" in features:
        parts.append(re.escape(features["underlying"]))
    if "strike" in features:
        strike_int = features["strike"].split(".")[0]
        parts.append(re.escape(strike_int))

    if len(parts) >= 2:
        pattern = ".*".join(parts)
        or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})

    if "external_code" in features:
        or_clauses.append({"mainId": {"$regex": re.escape(features["external_code"]), "$options": "i"}})

    if "underlying" in features:
        or_clauses.append({"ticker": {"$regex": re.escape(features["underlying"]), "$options": "i"}})

    return or_clauses


def _build_query_otc(features):
    or_clauses = []
    if "external_code" in features:
        or_clauses.append({"mainId": {"$regex": re.escape(features["external_code"]), "$options": "i"}})
    if "name" in features:
        words = [w for w in re.split(r"[\s\-_/,\.\(\)]", features["name"]) if len(w) >= 3]
        if words:
            pattern = ".*".join(re.escape(w) for w in words[:3])
            or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})
    return or_clauses


def _build_query_repo(features):
    or_clauses = []
    if "name" in features:
        or_clauses.append({"beehusName": {"$regex": re.escape(features["name"]), "$options": "i"}})
    return or_clauses


def _build_query_sovereign(features):
    or_clauses = []
    if "isin" in features:
        or_clauses.append({"isIn": features["isin"]})
    if "issuer" in features:
        words = [w for w in features["issuer"].split() if len(w) >= 3]
        if words:
            pattern = ".*".join(re.escape(w) for w in words[:2])
            or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})
    return or_clauses


def _build_query_generic(features):
    or_clauses = []
    if "name" in features:
        words = [w for w in re.split(r"[\s\-_/,\.\(\)]", features["name"]) if len(w) >= 3]
        if words:
            pattern = ".*".join(re.escape(w) for w in words[:3])
            or_clauses.append({"beehusName": {"$regex": pattern, "$options": "i"}})
            # Also try individual words for accent-tolerant partial matching
            for w in words:
                if len(w) >= 4:
                    or_clauses.append({"beehusName": {"$regex": re.escape(w), "$options": "i"}})
    if "code_prefix" in features:
        or_clauses.append({"mainId": {"$regex": re.escape(features["code_prefix"]), "$options": "i"}})
    return or_clauses


_QUERY_BUILDERS = {
    "brazilianFund":           _build_query_brazilian_fund,
    "stockEtf":                _build_query_stock_etf,
    "bond":                    _build_query_bond,
    "brazilianGovernmentBond": _build_query_gov_bond,
    "fund":                    _build_query_fund_intl,
    "futures":                 _build_query_futures,
    "options":                 _build_query_options,
    "otc":                     _build_query_otc,
    "brazilianRepo":           _build_query_repo,
    "sovereignBonds":          _build_query_sovereign,
    "privateMarket":           _build_query_generic,
    "realAssets":              _build_query_generic,
    "poc":                     _build_query_generic,
}


# ── Scoring ────────────────────────────────────────────────────────────────────

def _tokenize(text):
    return {t.lower() for t in re.split(r"[\s\-_/,\*\.\(\)%]", str(text)) if len(t) >= 2}


# ── Name-token document frequency (corpus rarity) ───────────────────────────
# How many securities' beehusName contain each token. A token in only a
# handful of securities (a fund's proper name, e.g. "delfos") is far more
# identifying than one in thousands ("investimento", "multimercado", "fundo").
# `_score_candidate` uses this to add a rarity bonus on top of the flat
# name-overlap score, so a distinctive name match isn't drowned out by a
# verbose description full of generic fund-type words. Populated by
# SecurityCache._build (module-level so the scorer — which only receives a
# `sec` dict — can read it without the cache handle). Empty until a cache
# loads → the bonus simply doesn't fire (scoring degrades to the old behaviour).
_TOKEN_DF = {}              # accent-stripped token -> # securities containing it
_RARE_DF_THRESHOLD = 20     # overlap tokens in <= this many securities are "rare"
_RARE_NAME_BONUS_CAP = 30   # max points the rarity bonus can add


# Asset-class / fund-structure terms that carry no identifying power on their
# own. They are excluded from BOTH the name-overlap score and the rarity bonus
# (accent-stripped, lowercase). Rationale: beehusNames are abbreviated
# ("Delfos FI MM") while transaction texts spell the same words out
# ("Fundo de Investimento Multimercado"), so the spelled-out generics are
# corpus-rare and would otherwise wrongly earn the rarity bonus
# (e.g. "referenciado" df=2). Verbs/connectives ("RESGATE", "DE") are already
# stripped upstream by transaction_security_classifier._NOISE_TOKENS; this set
# is the asset-class layer, applied in the shared scorer so it covers BOTH the
# securityMapping and the identify-transactions paths.
_GENERIC_NAME_TOKENS = frozenset({
    # Portuguese — fund / asset-class / structure
    "fundo", "fundos", "cotas", "cota",
    "investimento", "investimentos", "invest",
    "multimercado", "multi", "mult", "mercado", "mercados", "multiestrategia",
    "financeiro", "financeira",
    "renda", "fixa", "variavel",
    "credito", "privado", "privada",
    "acoes", "acao",
    "previdencia", "prev",
    "imobiliario", "imobiliaria",
    "referenciado", "referenciada",
    "cambial",
    "classe", "exclusivo", "exclusiva",
    "institucional", "institucionais",
    "qualificado", "qualificados", "profissional", "profissionais",
    "longo", "curto", "prazo",
    "aberto", "fechado", "condominio",
    "responsabilidade", "limitada", "limitado",
    # Portuguese abbreviations carried in beehusName
    "fi", "fic", "fim", "fia", "fidc", "fii",
    "mm", "rf", "rv", "cp", "di",
    "etf", "lci", "lca", "cdb", "cri", "cra",
    # English — fund / asset-class
    "fund", "funds", "investment", "investments", "equity", "income",
    "fixed", "bond", "bonds", "credit", "shares", "trust",
})


def _build_token_df(docs):
    """Document frequency of each accent-stripped beehusName token across the
    securities corpus. One pass; called once per cache (re)build."""
    df = {}
    for s in docs:
        for t in {_strip_accents(t) for t in _tokenize(s.get("beehusName", ""))}:
            df[t] = df.get(t, 0) + 1
    return df


# Score returned for an exact unique-identifier match (ticker / taxId-CNPJ /
# ISIN / SELIC code / a full-equality mainId code). It identifies the asset on
# its own, so it MUST outrank every heuristic pile-up — not merely tie it.
#
# The additive path in `_score_candidate` is UNCAPPED, and its bonuses can
# co-occur. Worst case: partial-id 50 + structured-option 50 (a SEPARATE `if`,
# stacks with the partial-id) + exact date 30 + indexer 10 + name 15 + rare-name
# 30 + name-equiv 50 + type 12 = 247. The Painel de Controle's price-agreement
# then adds up to +30 (→ 277), while an exact match can itself absorb a −25 price
# penalty (→ _EXACT_SCORE − 25). 300 keeps an exact id strictly above any
# heuristic combination (300 − 25 = 275 ≈ 277 — close, so revisit 300 if more
# additive weight is added). The downstream numeric confidence is clamped to 1.0
# (classifier) and the Match badge to 100%, so the >100 raw score surfaces only
# in the score-breakdown tooltip and the ranking.
_EXACT_SCORE = 300

# Instrument / government-bond type agreement (NTN-B, LFT, CDB, CRI…). Added
# ONLY as a tie-breaker on top of an already-plausible candidate (score > 0
# from a date / name / id signal) — never on its own, so it can't promote a
# generic type-only match (every CDB at once). Small enough not to rival a
# partial id (+40-50): it breaks ties between same-date bonds of different
# kinds (the NTN-B beats a corporate bond maturing the same day).
_TYPE_AGREE_BONUS = 12

# ── Fixed-income vehicle / lastro gate (hard reject on conflict) ─────────────
# The vehicle DEFINES a fixed-income security's identity: a CRA is not an LCA, a
# CRI is not a CRA, an LCA is not an LCI. Same issuer + same indexer + same
# maturity but a different vehicle = a DIFFERENT security. Without this, a CRA
# description vs an LCA candidate scored ~83% (date +50, issuer name, mainId
# name) because nothing penalised the vehicle mismatch — the `_TYPE_AGREE_BONUS`
# only *rewards* agreement, it never *rejects* disagreement.
#
# `_score_candidate` (securityType == "bond") rejects (score 0) when the
# description names one of these vehicles AND the candidate clearly is a
# DIFFERENT one — read from the candidate's beehusName (word-boundary) and its
# compacted mainId prefix. Naturally scoped to bonds: `instrument` is only
# extracted by `_extract_bond`, and an exact id (Rule 1) already won before here.
# Conservative on BOTH sides: no reject when the description names no vehicle, or
# when the candidate's vehicle can't be determined.
_VEHICLE_CANON = {            # alias → canonical vehicle token
    "DEBENTURE": "DEB", "DEBENTURES": "DEB", "DEB": "DEB",
    "CRA": "CRA", "CRI": "CRI", "CDCA": "CDCA", "CPRF": "CPRF",
    "LCA": "LCA", "LCI": "LCI", "LCD": "LCD", "LIG": "LIG",
    "CDB": "CDB", "CCB": "CCB",
    "LF": "LF", "LFS": "LFS", "LFSN": "LFS",   # LFS/LFSN = subordinada (≠ LF comum)
    "FIDC": "FIDC",
}
_VEHICLE_SET = frozenset(_VEHICLE_CANON.values())
# Word-boundary detector for the candidate's beehusName (accent-stripped, upper).
# Longest-first so LFSN/LFS win over LF and CDCA over CDB/CRA (the \b also guards,
# but keep the order explicit).
_VEHICLE_WORD_RE = re.compile(
    r"\b(DEBENTURES|DEBENTURE|DEB|CDCA|CPRF|FIDC|LFSN|LFS|LF|LCA|LCI|LCD|LIG|CRA|CRI|CDB|CCB)\b")
# Prefix order for the compacted mainId (longest-first so LFSN beats LFS beats LF).
_VEHICLE_PREFIXES = ("DEBENTURE", "CDCA", "CPRF", "FIDC", "LFSN", "LFS", "DEB",
                     "LCA", "LCI", "LCD", "LIG", "CRA", "CRI",
                     "CDB", "CCB", "LF")


def _canon_vehicle(tok):
    """Canonical vehicle token (DEBENTURE→DEB, upper) or '' if not a vehicle."""
    return _VEHICLE_CANON.get(str(tok or "").strip().upper(), "")


def _candidate_vehicles(sec):
    """Set of fixed-income vehicles the candidate exposes — from beehusName
    words AND the compacted mainId prefix. Memoised on the security dict under
    `_vehicles` (constant per security; the gate runs once per transaction)."""
    cached = sec.get("_vehicles")
    if cached is not None:
        return cached
    name_u = _strip_accents(str(sec.get("beehusName", "") or "")).upper()
    vs = {_canon_vehicle(m) for m in _VEHICLE_WORD_RE.findall(name_u)}
    vs.discard("")
    main_u = re.sub(r"[^A-Z0-9]", "", _strip_accents(str(sec.get("mainId", "") or "")).upper())
    for p in _VEHICLE_PREFIXES:
        if main_u.startswith(p):
            # Trust the prefix only when a DIGIT (a security code) follows — real
            # compacted mainIds pack the vehicle token then a numeric code
            # (CRA00123, LF0020003KK). An issuer name continuing in LETTERS
            # (LIGHT→LIG, CRISTAL→CRI, DEBORA→DEB) is NOT a vehicle; the real
            # vehicle word, when present, is recovered from the beehusName above.
            rest = main_u[len(p):]
            if rest == "" or rest[0].isdigit():
                vs.add(_canon_vehicle(p))
            break
    frozen = frozenset(vs)
    sec["_vehicles"] = frozen
    return frozen


# ── Rate-regime / indexer gate (hard reject on conflict) ────────────────────
# The remuneration REGIME is part of a fixed-income security's identity. A
# pós-fixado / inflation bond (CDI/IPCA/SELIC/IGPM) is NOT the same asset as a
# "Pré-fixado" one. Without this gate, "CRA RAIZEN IPCA+6,40% 17/10/33" matched
# "CRA Raizen Pré-fixado 17/Out/2033" at a high score (same vehicle, issuer,
# date) because the +10 indexer bonus only *rewards* agreement — never *rejects*.
#
# The gate works on COARSE BUCKETS — `PRE` (pré-fixado) vs `POS` (pós-fixado /
# inflation: CDI/IPCA/SELIC/IGPM) — NOT on fine indexers. This matches the
# requirement ("CDI, IPCA é pós-fixado, mas a security diz Pré-fixado → não é o
# mesmo") and avoids false-rejects from CDI↔IPCA data inconsistencies in the
# catalog (the structured `indexer` field disagrees with the name on ~0.4% of
# bonds). Among pós candidates the +10 indexer bonus still ranks the exact
# indexer match higher (soft signal), so "IPCA é palavra forte" survives.
#
# `_score_candidate` (securityType == "bond") rejects (score 0) when the
# description's PRIMARY bucket and the candidate's bucket(s) are both known and
# don't intersect. Conservative: unknown bucket on either side → no reject.
_CDI_REGIMES  = frozenset({"CDI", "SELIC"})     # floating rate, tracks Selic/CDI
_IPCA_REGIMES = frozenset({"IPCA", "IGPM"})     # inflation-linked
_POS_REGIMES  = _CDI_REGIMES | _IPCA_REGIMES    # all post-fixed (kept for other callers)

# A zero coefficient immediately before an index ("0%CDI", "0,00% CDI"): the
# index carries NO weight, so it is not that regime. The BR broker formula
# "0%CDI+12,05%aa" ("0% of CDI + 12.05% per annum") is a PRÉ-FIXADO bond — the
# leftmost "CDI" must NOT be read as the regime. "100%CDI" / "108%CDI" are real.
_ZERO_COEF_RE = re.compile(r"(?:^|[^\d.,])0+(?:[.,]0+)?\s*%\s*$")
_POS_REGIME_PATS = (
    ("CDI",   r"CDI"),
    ("IPCA",  r"IPCA(?:DP)?|IPC-A"),
    ("SELIC", r"SELIC"),
    ("IGPM",  r"IGP-?M"),
)


def _scan_regimes(text):
    """Ordered (left-to-right) list of remuneration regimes named in `text`,
    coefficient-aware. PRE comes from a pré-fix* spelling (Prefixado/Pré-fix/Pré
    fixados…), the English "Fixed", or a BARE "Pré" that is NOT a structure word
    (pré-pagamento/embarque/pago/operacional). A POS index with a ZERO
    coefficient ("0%CDI") is dropped; if every index is zeroed (a pré-fixado
    "0%CDI+NN%aa" formula) the regime is PRE."""
    t = _strip_accents(str(text or "")).upper()
    found = []  # (pos, regime)
    for m in re.finditer(r"\bPRE[\s-]?FIX[A-Z]*\b", t):          # pré-fixado spellings
        found.append((m.start(), "PRE"))
    for m in re.finditer(r"\bFIXED\b", t):                       # English field value
        found.append((m.start(), "PRE"))
    for m in re.finditer(                                        # bare "Pré", not a structure word
            r"\bPRE\b(?![\s./-]*(?:PAGAMENTO|EMBARQUE|PAGO|OPERACIONAL))", t):
        found.append((m.start(), "PRE"))
    has_active_pos = False
    has_zeroed = False
    for reg, pat in _POS_REGIME_PATS:
        for m in re.finditer(r"\b(?:%s)\b" % pat, t):
            if _ZERO_COEF_RE.search(t[:m.start()]):
                has_zeroed = True
            else:
                found.append((m.start(), reg))
                has_active_pos = True
    found.sort()
    regs = [r for _, r in found]
    if has_zeroed and not has_active_pos and "PRE" not in regs:
        regs.append("PRE")          # 0%-coef formula with no active index = pré-fixado
    return regs


def _regimes_in(text):
    """Set of remuneration regimes named in `text` (coefficient-aware)."""
    return set(_scan_regimes(text))


def _regime_bucket(reg):
    """Regime bucket for the rate-regime gate: 'PRE', 'CDI', or 'IPCA', or None.

    Three distinct buckets so CDI ≠ IPCA/IGPM (different risk/return profile) and
    both differ from PRE. SELIC is grouped with CDI (tracks it closely). Returns
    None for unrecognised regimes so the gate stays conservative (no reject)."""
    if reg == "PRE":
        return "PRE"
    if reg in _CDI_REGIMES:
        return "CDI"
    if reg in _IPCA_REGIMES:
        return "IPCA"
    return None


def _candidate_regimes(sec):
    """Set of remuneration regimes the candidate exposes — from its structured
    `indexer` field AND its `beehusName`. Memoised on the security dict under
    `_regimes` (constant per security; the gate runs once per transaction)."""
    cached = sec.get("_regimes")
    if cached is not None:
        return cached
    text = f"{sec.get('beehusName', '') or ''} {sec.get('indexer', '') or ''}"
    frozen = frozenset(_regimes_in(text))
    sec["_regimes"] = frozen
    return frozen


def _candidate_buckets(sec):
    """Fine-grained regime buckets the candidate exposes: any subset of
    {'PRE', 'CDI', 'IPCA'}. CDI and IPCA are kept separate so a CDI-indexed
    candidate is rejected when the description implies IPCA (and vice-versa).
    Memoised on the security dict under `_regbuckets`."""
    cached = sec.get("_regbuckets")
    if cached is not None:
        return cached
    regs = _candidate_regimes(sec)
    b = set()
    if "PRE" in regs:
        b.add("PRE")
    if regs & _CDI_REGIMES:
        b.add("CDI")
    if regs & _IPCA_REGIMES:
        b.add("IPCA")
    frozen = frozenset(b)
    sec["_regbuckets"] = frozen
    return frozen


# ── Issuer / bank gate (hard reject on conflict) ────────────────────────────
# A fixed-income BANK instrument's issuer = the issuing bank, and it is named
# unambiguously (e.g. "CDB BTG Pactual …", "LCA Banco ABC …"). Two clearly
# DIFFERENT bank issuers = a different security, even with the same vehicle,
# regime, rate and maturity. Without this, a "CDB Santander …06/Ago/2027" matched
# a "CDB BTG …06/Ago/2027" at 52 (date+indexer+type, name overlap 0) and showed
# as identified. The issuer is already rewarded by name overlap, so this gate adds
# only the INVALIDATE side (the missing one) — no confirm bonus (that would
# double-count the name signals).
#
# Restricted to BANK vehicles (issuer = bank, unambiguous). Securitized
# CRI/CRA/CDCA are EXCLUDED — there the name carries the securitizadora OR the
# lastro/devedor interchangeably (same asset, two valid names). Debentures (DEB)
# are out too: corporate long-tail issuers the bank dictionary doesn't cover.
# Dictionary is data/issuers.json (canonical → alias tokens), editable; if the
# file is missing the gate is simply inert.
_BANK_VEHICLES = frozenset({"CDB", "RDB", "LF", "LFS", "LCI", "LCA", "LCD", "LIG"})


def _load_issuer_aliases():
    """Build {alias_token → canonical} from data/issuers.json. Multiword canonicals
    (e.g. 'JOHN DEERE') contribute only their single-token aliases ('DEERE'); the
    canonical string is still the resolved value. Missing/broken file → {} (gate
    inert)."""
    path = os.path.join(_CACHE_DIR, "issuers.json")
    alias = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            issuers = (json.load(f).get("issuers") or {})
        for canon, toks in issuers.items():
            for t in [canon] + list(toks or []):
                tt = _strip_accents(str(t)).upper()
                if re.fullmatch(r"[A-Z0-9]+", tt):   # single usable token
                    alias[tt] = canon
    except FileNotFoundError:
        log.info("issuer dict: data/issuers.json not found — issuer gate inert")
    except Exception as exc:
        log.warning("issuer dict: failed to load (%s) — issuer gate inert", exc)
    return alias


_ISSUER_ALIAS = _load_issuer_aliases()


def _issuers_in(text):
    """Set of canonical bank issuers named in `text`. Token-exact against the
    dictionary (so short/digit issuer tokens like XP/BV/C6 match, but rate/date/
    code tokens never do)."""
    if not _ISSUER_ALIAS:
        return set()
    t = _strip_accents(str(text or "")).upper()
    return {_ISSUER_ALIAS[tok] for tok in re.split(r"[^A-Z0-9]+", t) if tok in _ISSUER_ALIAS}


def _candidate_issuers(sec):
    """Canonical bank issuers the candidate's beehusName exposes. Memoised on the
    security dict under `_issuers`."""
    cached = sec.get("_issuers")
    if cached is not None:
        return cached
    frozen = frozenset(_issuers_in(sec.get("beehusName", "")))
    sec["_issuers"] = frozen
    return frozen


# Compressed-name match against mainId (a mainId often packs the asset name with
# spaces/punctuation stripped — "CRA Usina Coruripe" → CRAUSINACORURIPE…). Each
# distinctive description token found inside the compacted mainId adds points,
# capped so it stays a supporting signal (never rivals an exact id or a date).
_MAINID_NAME_PER_HIT = 8
_MAINID_NAME_CAP = 20

# Name-equivalence bonus. A flat overlap score (`ratio * 15`) can't tell a FULL
# name match ("Kayros FIM CP IE" == "Kayros FIM CP IE") from a partial one, so a
# correct identification by name alone tops out at ~45 (15 overlap + 30 rarity)
# and reads as low confidence. When the distinctive token sets agree BOTH ways
# (the candidate has no extra distinctive tokens — guards against a description
# that is a subset of a more-specific asset), add this so an evident name match
# reaches confident territory. Requires ≥2 overlapping tokens.
_NAME_EQUIV_BONUS = 50
_NAME_EQUIV_COVERAGE = 0.8

# brazilianFund share-class / series / seniority discriminators. Two fund names
# can be near-identical yet denote DIFFERENT securities when one of these differs.
# Mapped from real catalog patterns (1.7k funds). Two categories, treated
# differently (see `_fund_discriminator_sig`):
#   • TRANCHE words (seniority/subordination) — a difference EITHER WAY means a
#     different security ("… XI" vs "… XI Senior").
#   • CLASS VALUES (bare letter / series number / roman) — only a CONFLICT (both
#     sides name a value and they differ) means a different security. An
#     asymmetry (one side omits it) is NOT penalised: an unprocessedId may carry
#     an extra "Classe A" the (single, class-less) security name doesn't repeat.
# The class-LABEL words ("classe"/"cl"/"série") are deliberately NOT signals on
# their own — the letter/number after them carries the class.
_FUND_TRANCHE_WORDS = frozenset({
    "senior", "sr", "sub", "subordinada", "subordinado", "mezanino", "mez",
})
_FUND_DISCR_PENALTY = 25   # sibling with a conflicting class/series/tranche
_FUND_CLASS_CONFIRM = 8    # exact class/tranche match — ranks above an asymmetric sibling


def _compact(text):
    """Lowercase, accent-stripped, alphanumeric-only form — for matching against
    compressed mainIds (e.g. 'CRA Usina Coruripe' → 'crausinacoruripe')."""
    return re.sub(r"[^a-z0-9]", "", _strip_accents(str(text)).lower())


def _fund_discriminator_sig(name):
    """Return ``(tranche, values)`` for a fund name — the discriminators the
    plain token overlap misses (the tokenizer drops single letters/digits).
      • tranche: seniority/subordination words (Senior/Sub/Mezanino…).
      • values:  single class letters (A/B/C/O), series romans (i/iv/xv) and
        numbers (2/14)."""
    tranche, values = set(), set()
    for raw in re.split(r"[\s\-_/,\*\.\(\)%]", name or ""):
        s = _strip_accents(raw).lower()
        if not s:
            continue
        if s in _FUND_TRANCHE_WORDS:
            tranche.add(s)
        elif re.fullmatch(r"[ivxl]{1,4}", s):    # roman series (i, iv, xv, xix…)
            values.add(s)
        elif len(s) == 1 and s.isalpha():         # single class letter (A/B/C/O…)
            values.add(s)
        elif s.isdigit():                          # series / vintage number
            values.add(s)
    return tranche, values


def _exact_identifier_match(sec, features):
    """Return a label if an exact, unique identifier matches, else None.

    "Exact" means strict equality (not substring/adjacency): ticker (incl. the
    FUT-prefixed variant), taxId/CNPJ (compared digits-only against the
    candidate's taxId AND its mainId — a brazilianFund mainId IS the bare-digit
    CNPJ), ISIN, SELIC code, OR an extracted code
    (cetip/internal/external/fund) that equals the candidate's `mainId` in FULL.
    A full-equality code identifies the asset as decisively as a ticker/ISIN, so
    it is promoted out of the additive path. Only SUBSTRING/adjacency code
    matches (the code appears inside a longer mainId) stay partial inside
    `_score_candidate`.
    """
    sec_ticker  = str(sec.get("ticker", ""))
    sec_main_id = str(sec.get("mainId", ""))

    tkr = features.get("ticker")
    if tkr:
        if sec_ticker == tkr or sec_main_id == tkr:
            return "ticker"
        fut = "FUT" + tkr
        if sec_ticker == fut or sec_main_id == fut:
            return "ticker/FUT"
    feat_cnpj = features.get("cnpj")
    if feat_cnpj:
        # Punctuation-insensitive: compare digits only so a bare 14-digit CNPJ
        # in the text matches a punctuated taxId. Treated as an exact unique
        # identifier, same weight as ticker/isin. The ≥11-digit guard avoids
        # matching on a stray short number.
        fd = _digits_only(feat_cnpj)
        if len(fd) >= 11:
            sec_taxid = sec.get("taxId")
            if sec_taxid and fd == _digits_only(sec_taxid):
                return "taxId"
            # brazilianFund mainIds ARE the CNPJ (digits-only). A CNPJ in the
            # description whose digits equal the candidate's mainId digits
            # identifies the fund as decisively as its taxId — same exact weight.
            # (`cnpj` is only extracted for brazilianFund, so this is naturally
            # scoped to funds.) Ex.: "30934757000113 - BTG ..." == mainId
            # "30934757000113".
            if sec_main_id and fd == _digits_only(sec_main_id):
                return "mainId/cnpj"
    if features.get("isin") and sec.get("isIn") and sec.get("isIn") == features["isin"]:
        return "isIn"
    # Some asset types (e.g. otc) store the ISIN in mainId with isIn left empty.
    if features.get("isin") and sec_main_id and sec_main_id == features["isin"]:
        return "mainId/isin"
    if features.get("selic_code") and sec.get("selicCode") and sec.get("selicCode") == features["selic_code"]:
        return "selicCode"
    # Full-equality code match: the extracted code IS the entire mainId (not just
    # a substring of it). Case-insensitive. As decisive as a ticker/ISIN — the
    # substring case stays in the additive path below.
    #
    # Length guard (≥ 8): promoting a match to a DECISIVE exact id bypasses the
    # date gate, so a short numeric code (e.g. a 4–6 digit fund_code) that equals
    # a short mainId by coincidence must NOT become an exact match — it would
    # mis-identify the asset. CETIP / external / ISIN-like codes are ≥ 8 chars;
    # shorter codes stay in the additive (non-decisive) path below.
    if sec_main_id and len(sec_main_id) >= 8:
        main_lower = sec_main_id.lower()
        for feat_key, label in (("cetip_code", "mainId/cetip="),
                                 ("internal_code", "mainId/code="),
                                 ("external_code", "mainId/external="),
                                 ("fund_code", "mainId/fund_code=")):
            code = features.get(feat_key)
            if code and str(code).lower() == main_lower:
                return label
    # Complement tokens provided explicitly by the operator: if any token equals
    # the candidate's mainId (case-insensitive), treat as a decisive match.
    # Length guard ≥ 6 mirrors the code guard above — avoids short accidental hits.
    if sec_main_id and len(sec_main_id) >= 6:
        main_lower = sec_main_id.lower()
        for _ci in range(1, 4):
            comp_tok = features.get(f"complement_{_ci}")
            if comp_tok and str(comp_tok).lower() == main_lower:
                return f"complement_{_ci}"
        for _ci in range(1, 4):
            gc_tok = features.get(f"generic_code_{_ci}")
            if gc_tok and str(gc_tok).lower() == main_lower:
                return f"generic_code_{_ci}"
    return None


def _candidate_dates(sec):
    """Every maturity date the candidate exposes — from BOTH sources: the
    structured `maturityDate` field and any date embedded in `beehusName`
    (e.g. '... 02/Jan/2029'). Considering both means a date in the name still
    gates the match even when the field is empty.

    Memoised on the security dict under `_cand_dates`: the candidate's dates
    are constant, but `_score_candidate` runs this once per (transaction,
    candidate) pair. Caching it on the (shared, long-lived) cache dict turns
    the repeated regex date-extraction over `beehusName` into a single pass
    per security per cache lifetime. The result is never mutated by callers."""
    cached = sec.get("_cand_dates")
    if cached is not None:
        return cached
    dates = set()
    sec_mat = sec.get("maturityDate")
    if sec_mat:
        dates.add(str(sec_mat)[:10])
    # A USD security's name dates (when numeric+ambiguous) are American too —
    # use the security's own currency so both sides of the date gate agree.
    prefer_mdy = str(sec.get("currency", "")).upper() == "USD"
    for d in _extract_all_dates(str(sec.get("beehusName", "") or ""), prefer_mdy=prefer_mdy):
        dates.add(d)
    # frozenset: this is shared across WSGI threads via the cache dict —
    # make the "do not mutate" contract enforced by the type, not convention.
    frozen = frozenset(dates)
    sec["_cand_dates"] = frozen
    return frozen


def _candidate_name_tokens(sec):
    """Accent-stripped token set of the candidate's `beehusName`, memoised on
    the security dict under `_name_tokens`. Same rationale as
    `_candidate_dates`: the name is constant, but the name-overlap block in
    `_score_candidate` re-tokenised + re-stripped accents on every scoring
    call. Compute once per security, reuse for every transaction."""
    cached = sec.get("_name_tokens")
    if cached is not None:
        return cached
    toks = frozenset(_strip_accents(t) for t in _tokenize(sec.get("beehusName", "")))
    sec["_name_tokens"] = toks
    return toks


# Business-day tolerance for the maturity-date gate. A maturity rolled by a
# business-day/holiday adjustment is the SAME asset, so when the operator named a
# day we agree within ±this many BUSINESS days (Mon-Fri, weekday-only — same
# convention as `db.biz_days_between`, holidays NOT excluded) instead of demanding
# an exact match. Wide-apart dates (different month/year, or a distinct same-month
# maturity) still fail. Tune here if real data shows over- or under-rejection.
_DATE_TOLERANCE_BIZ_DAYS = 2
# 2 business days span at most 4 calendar days (Thu→Mon / Fri→Tue over a weekend),
# so a calendar gap > 4 is always > 2 biz days — guard with it to skip the
# day-by-day `biz_days_between` loop for the common far-apart candidate.
_DATE_TOLERANCE_CAL_GUARD = 4


def _iso_to_date(s):
    """Parse a 'YYYY-MM-DD...' string to a date, or None if unparseable."""
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _date_agrees(feat_date, cand_dates, day_known):
    """True if the operator's date matches at least one candidate date at the
    precision the operator supplied.

      • day named → agree within ±`_DATE_TOLERANCE_BIZ_DAYS` BUSINESS days of a
        candidate date (tolerates business-day/holiday rolls; a far-apart date
        still fails);
      • month/year only ('SET/2029', day defaults to 01) → compare year+month
        only, so it isn't false-rejected against a day-precise maturityDate in
        the same month."""
    if day_known:
        fd = _iso_to_date(feat_date)
        if fd is None:
            return feat_date in cand_dates
        for d in cand_dates:
            cd = _iso_to_date(d)
            if cd is None:
                continue
            cal = abs((cd - fd).days)
            if cal == 0:
                return True
            # Calendar-gap guard keeps the biz-day count off the hot path for the
            # vast majority of (far-apart) candidate dates.
            if cal <= _DATE_TOLERANCE_CAL_GUARD and \
                    biz_days_between(feat_date, d) <= _DATE_TOLERANCE_BIZ_DAYS:
                return True
        return False
    fym = feat_date[:7]
    return any(d[:7] == fym for d in cand_dates)


def _score_candidate(sec, features, security_type):
    """Score a candidate security against extracted features.

    Three hard rules dominate the additive signals:

      • Exact unique identifier (ticker / taxId / isIn / selicCode / a code that
        equals the whole mainId) → top score (`_EXACT_SCORE`, above the heuristic
        ceiling so it always outranks a partial pile-up). Short-circuits and
        IGNORES the date gate: an exact id identifies the asset on its own.

      • Fixed-income vehicle conflict (bond): the description names a vehicle
        (CRA/LCA/CRI/CDB/…) and the candidate clearly is a DIFFERENT one →
        score 0. The vehicle/lastro defines the asset's identity.

      • Rate-regime conflict (bond): the description states an indexer
        (IPCA/CDI/SELIC/IGPM/Pré) and the candidate clearly is a DIFFERENT
        regime (e.g. IPCA description vs a "Pré-fixado" candidate) → score 0.

      • Issuer conflict (bond, BANK vehicle only): description and candidate name
        DIFFERENT known bank issuers (e.g. CDB Santander vs CDB BTG) → score 0.

      • Otherwise, if the operator named a maturity/expiry date AND the
        candidate exposes a date (maturityDate field OR a date in beehusName)
        that does NOT agree → score 0. A different date means a different asset.

    When no hard rule fires, the score is the sum of the partial signals
    (substring id, structured-option pattern, agreeing date, indexer, name
    token overlap), on a ~0–100 scale.
    """
    # ── Rule 1: exact identifier wins (ignores the date gate) ──────────────
    exact = _exact_identifier_match(sec, features)
    if exact:
        return _EXACT_SCORE, [exact, "exact"]

    sec_main_id = str(sec.get("mainId", ""))

    # ── Rule 1b: fixed-income vehicle gate (hard reject on conflict) ───────
    # A CRA is not an LCA, a CRI is not a CRA. When the description names a
    # vehicle AND the candidate is unambiguously a different one (beehusName word
    # or compacted mainId prefix), reject — no matter how well the issuer /
    # indexer / maturity agree (they would otherwise reach ~83%). Conservative:
    # only when BOTH sides name a vehicle and they don't overlap.
    if security_type == "bond" and not features.get("vehicle_ambiguous"):
        feat_veh = _canon_vehicle(features.get("instrument"))
        if feat_veh in _VEHICLE_SET:
            cand_vehs = _candidate_vehicles(sec)
            if cand_vehs and feat_veh not in cand_vehs:
                return 0, [f"vehicle≠({feat_veh})"]

    # ── Rule 1c: rate-regime gate, PRE vs POS buckets (hard reject) ────────
    # Pré-fixado ≠ pós-fixado/inflation. The description's PRIMARY bucket
    # (from the leftmost active regime) must intersect the candidate's bucket(s);
    # reject when both are known and disjoint. Coarse buckets (not CDI≠IPCA) so a
    # "0%CDI+NN%aa" pré-fixado description (→ PRE) matches its Pré candidate, and
    # CDI↔IPCA catalog inconsistencies don't cause false-rejects. Conservative:
    # unknown bucket on either side → no reject.
    # Exception: a plain numeric rate (e.g. "5,00%") with NO indexer keyword is the
    # hallmark of a pré-fixado bond — the rate IS the full yield, not a spread over
    # a floating benchmark. Treat the absence of an indexer + presence of a rate as
    # an implicit PRE signal so CDI/IPCA candidates are correctly rejected.
    if security_type == "bond":
        _indexer_for_gate = features.get("indexer") or ""
        if not _indexer_for_gate and features.get("rate"):
            _indexer_for_gate = "PRE"
        desc_bucket = _regime_bucket(_indexer_for_gate)
        if desc_bucket:
            cand_buckets = _candidate_buckets(sec)
            if cand_buckets and desc_bucket not in cand_buckets:
                return 0, [f"indexer≠({desc_bucket})"]

    # ── Rule 1d: issuer gate, BANK vehicles only (hard reject on conflict) ──
    # CDB Santander ≠ CDB BTG. Only fires when the description's vehicle is a bank
    # instrument AND both sides resolve to KNOWN, DIFFERENT bank issuers
    # (data/issuers.json). Excludes securitized CRI/CRA/CDCA (securitizadora ×
    # lastro ambiguity) and corporate DEB. Conservative: unknown issuer on either
    # side → no reject.
    if (security_type == "bond" and not features.get("vehicle_ambiguous")
            and _canon_vehicle(features.get("instrument")) in _BANK_VEHICLES):
        desc_iss = set(features.get("issuers") or ())
        if desc_iss:
            cand_iss = _candidate_issuers(sec)
            if cand_iss and desc_iss.isdisjoint(cand_iss):
                return 0, [f"issuer≠({'/'.join(sorted(cand_iss))})"]

    # ── Rule 1e: gov bond coupon gate (hard reject on conflict) ────────────
    # NTN-B (coupon) ≠ NTN-B Principal (no coupon). Both share bond type and
    # maturity day convention but are different instruments with different cash
    # flows. Rejects when description coupon status is known AND the candidate's
    # status (derived from beehusName) is the opposite.
    if security_type == "brazilianGovernmentBond":
        feat_coupon = features.get("coupon")
        if feat_coupon is not None:
            cand_coupon = _candidate_gov_coupon(sec)
            if cand_coupon is not None and (feat_coupon == "Sim") != cand_coupon:
                return 0, ["coupon≠"]

    # ── Rule 2: maturity-date gate (hard reject on disagreement) ───────────
    feat_date = features.get("maturity_date") or features.get("expiry") or ""
    date_agreed = False
    date_agreed_exact = False  # full day/month/year matched (not just month+year)
    if feat_date:
        day_known = bool(features.get("maturity_day_specified")) or ("expiry" in features)
        cand_dates = _candidate_dates(sec)
        if cand_dates:
            if _date_agrees(feat_date, cand_dates, day_known):
                date_agreed = True
                # +50 only on an EXACT day/month/year hit; a within-tolerance
                # (rolled by a few days) or month/year-only agreement stays +25.
                date_agreed_exact = day_known and (feat_date in cand_dates)
            else:
                return 0, ["maturityDate≠"]

    score = 0
    matched_on = []

    # ── Partial identifier matches (substring / adjacency — not decisive) ──
    # A ticker / ISIN embedded INSIDE a longer mainId is strong evidence — same
    # nature as the CETIP-substring signal. The whole-mainId == id case already
    # returned a decisive exact match upstream; here the id is a substring of a
    # bigger mainId. Length-guarded so a short ticker can't match by coincidence
    # (ticker ≥ 4, ISIN ≥ 8). Case-insensitive.
    sec_main_lower = sec_main_id.lower()
    feat_tkr  = features.get("ticker") or ""
    feat_isin = features.get("isin") or ""
    if len(feat_tkr) >= 4 and feat_tkr.lower() in sec_main_lower:
        score += 50
        matched_on.append("mainId/ticker")
    elif len(feat_isin) >= 8 and feat_isin.lower() in sec_main_lower:
        score += 50
        matched_on.append("mainId/isin")
    elif "cetip_code" in features and features["cetip_code"].lower() in sec_main_id.lower():
        score += 50
        matched_on.append("mainId/cetip")
    elif "internal_code" in features and features["internal_code"].lower() in sec_main_id.lower():
        score += 45
        matched_on.append("mainId/code")
    elif "external_code" in features and features["external_code"] in sec_main_id:
        score += 40
        matched_on.append("mainId/external")
    elif "fund_code" in features and features["fund_code"] in sec_main_id:
        score += 35
        matched_on.append("mainId/fund_code")
    elif "selic_code" in features:
        # Exact selicCode already returned above as a perfect match; only the
        # off-by-one (adjacent) case reaches here.
        db_selic = sec.get("selicCode", "")
        if db_selic:
            try:
                if abs(int(db_selic) - int(features["selic_code"])) <= 1:
                    score += 40
                    matched_on.append("selicCode~")
            except ValueError:
                pass

    # Bonus: structured mainId match for options (CALL_AAPL_280_17042026)
    if security_type == "options" and "option_type" in features and "underlying" in features and "strike" in features:
        strike_int = features["strike"].split(".")[0]
        pattern = f"{features['option_type']}_{features['underlying']}_{strike_int}"
        if pattern.lower() in sec_main_id.lower() or pattern.lower() in str(sec.get("ticker", "")).lower():
            score += 50
            matched_on.append("mainId/structured")

    # Agreeing maturity date CONFIRMS a non-exact candidate, but does not
    # dominate. The date is already a hard GATE above (a disagreeing date → 0),
    # so every candidate reaching here shares the date; the additive bonus must
    # not, on its own, lift a same-issuer/same-date sibling to "identified". Kept
    # deliberately BELOW the 50 already-registered threshold (+30 exact / +15
    # month-year) so name/id signals decide. Exact day/month/year > month-only.
    if date_agreed_exact:
        score += 30
        matched_on.append("maturityDate=")
    elif date_agreed:
        score += 15
        matched_on.append("maturityDate")

    # Brazilian government bond domain bonus (+50): bond_type + maturity month/year
    # uniquely identifies the paper — only one LTN matures each Jan/2029. The
    # generic scoring (date +15, type +12 = 27) undersells this certainty; the
    # bonus pushes total above the 50-point auto-check threshold whenever the two
    # decisive signals agree. Gated on date_agreed so it never fires without a
    # date match, and the bond_type check mirrors the generic tie-breaker below.
    if security_type == "brazilianGovernmentBond" and date_agreed:
        bt = (features.get("bond_type") or "").upper()
        if bt:
            hay = (str(sec.get("beehusName", "")) + " "
                   + str(sec.get("mainId", ""))).upper()
            if bt in hay or bt.replace("-", "") in hay.replace("-", ""):
                score += 50
                matched_on.append("govBond=")

    # Indexer match (+10). Both sides must be non-empty: an empty candidate
    # `indexer` would make `"" in "IPCA"` True and grant a spurious bonus to
    # every indexer-less security (which the rate-regime gate above can't reject,
    # since an unknown candidate regime is left alone).
    if features.get("indexer"):
        sec_idx = str(sec.get("indexer", "")).upper()
        if sec_idx and (features["indexer"] in sec_idx or sec_idx in features["indexer"]):
            score += 10
            matched_on.append("indexer")

    # Name token overlap (+15 max) — accent-insensitive
    name_feature = features.get("name") or features.get("issuer") or ""
    if name_feature:
        # Drop generic asset-class terms from BOTH sides so they contribute to
        # neither the overlap score nor the rarity bonus (only distinctive
        # tokens — proper names — count toward a name match).
        feat_tokens = {_strip_accents(t) for t in _tokenize(name_feature)} - _GENERIC_NAME_TOKENS
        sec_tokens = _candidate_name_tokens(sec) - _GENERIC_NAME_TOKENS
        if feat_tokens and sec_tokens:
            overlap = feat_tokens & sec_tokens
            ratio = len(overlap) / len(feat_tokens) if feat_tokens else 0
            name_score = int(ratio * 15)
            if name_score > 0:
                score += name_score
                matched_on.append(f"name({len(overlap)}/{len(feat_tokens)})")
            # Rarity bonus (additive, on top of the flat overlap score): a
            # match on a corpus-rare token — a fund's proper name like
            # "delfos" (in only a couple of securities) — is far more
            # identifying than a match on ubiquitous words ("investimento",
            # "multimercado"). Without this, a verbose description dilutes the
            # ratio and the right security ties with dozens of generic ones.
            # Rarer → more points; capped so it can't dominate an exact id.
            if overlap and _TOKEN_DF:
                rare_w = 0.0
                n_rare = 0
                for t in overlap:
                    df = _TOKEN_DF.get(t)
                    if df and df <= _RARE_DF_THRESHOLD:
                        rare_w += 1.0 - (df - 1) / _RARE_DF_THRESHOLD
                        n_rare += 1
                if rare_w > 0:
                    rare_points = min(_RARE_NAME_BONUS_CAP,
                                      int(round(rare_w * _RARE_NAME_BONUS_CAP)))
                    if rare_points > 0:
                        score += rare_points
                        matched_on.append(f"name~rare({n_rare})")

            cov_feat = len(overlap) / len(feat_tokens)
            cov_sec  = len(overlap) / len(sec_tokens)
            base_names_align = min(cov_feat, cov_sec) >= _NAME_EQUIV_COVERAGE

            # Fund share-class / series discriminator check (brazilianFund only).
            # A TRANCHE difference either way, or a CLASS-VALUE conflict (both
            # sides name a value and they differ), means a sibling — a different
            # security. A class-value asymmetry (one side omits it) is left alone
            # so an extra "Classe A" in the description doesn't sink a correct,
            # class-less security. An exact match earns a small confirm bonus.
            discr_penalize = False
            class_confirm = False
            if security_type == "brazilianFund":
                tr_f, val_f = _fund_discriminator_sig(name_feature)
                tr_c, val_c = _fund_discriminator_sig(sec.get("beehusName", ""))
                tranche_diff   = tr_f != tr_c
                value_conflict = bool(val_f) and bool(val_c) and val_f != val_c
                discr_penalize = tranche_diff or value_conflict
                class_confirm = not discr_penalize and (
                    (bool(val_f) and val_f == val_c) or (bool(tr_f) and tr_f == tr_c))

            # Name-equivalence bonus: distinctive token sets agree BOTH ways
            # (high coverage on the description AND the candidate), so the names
            # are essentially the same — not just a partial overlap. Date-gated
            # like the other name signals; ≥2 overlapping tokens so a single
            # shared word can't trigger it; blocked when fund discriminators differ.
            if (len(overlap) >= 2 and base_names_align and not discr_penalize
                    and (date_agreed or not feat_date)):
                score += _NAME_EQUIV_BONUS
                matched_on.append("name~equiv")

            # Discriminator penalty: the base name lines up but the fund's
            # class/series/seniority conflicts → a sibling, not the same security.
            if discr_penalize and base_names_align:
                score -= _FUND_DISCR_PENALTY
                matched_on.append("fund~discr≠")
            # Confirm bonus: same base name AND same class/tranche — ranks the
            # exact class above an asymmetric sibling (Bogari A vs the base class).
            elif class_confirm and base_names_align:
                score += _FUND_CLASS_CONFIRM
                matched_on.append("fund~class=")

        # Compressed-name match against the candidate's mainId. Many mainIds pack
        # the asset name with spaces/punctuation stripped (CRA Usina Coruripe →
        # CRAUSINACORURIPE…), which the beehusName-token overlap above misses.
        # Test the description's distinctive tokens against the compacted mainId.
        # DATE-GATED: only when the operator's date agreed (or none was given) —
        # so a name that is a prefix of a longer, MORE-specific mainId (a
        # different asset) can't score on the name when the dates disagree (that
        # path already returned 0). Fires even when beehusName tokens were sparse.
        if feat_tokens and (date_agreed or not feat_date):
            main_compact = _compact(sec_main_id)
            if main_compact:
                hits = sorted({t for t in feat_tokens
                               if len(_compact(t)) >= 4 and _compact(t) in main_compact})
                if hits:
                    pts = min(_MAINID_NAME_CAP, _MAINID_NAME_PER_HIT * len(hits))
                    score += pts
                    matched_on.append(f"mainId/name({len(hits)})")

    # Type-agreement tie-breaker. Applied LAST and only when the candidate is
    # already plausible (score > 0 from date/name/id) — never alone, so a bare
    # "CDB"/"CRI" can't lift every same-type security off zero. Matches the
    # description's bond_type/instrument against the candidate's name/mainId,
    # dash-insensitive ("NTN-B" ~ "NTNB").
    if score > 0:
        bt = (features.get("bond_type") or features.get("instrument") or "").upper()
        if len(bt) >= 2:
            hay = (str(sec.get("beehusName", "")) + " "
                   + str(sec.get("mainId", ""))).upper()
            if bt in hay or bt.replace("-", "") in hay.replace("-", ""):
                score += _TYPE_AGREE_BONUS
                matched_on.append(f"type={bt}")

    # Floor at 0 — the discriminator penalty is the only signal that can push the
    # additive score negative, and a negative score has no meaning downstream.
    return max(0, score), matched_on


# ── Score breakdown (display-only) ──────────────────────────────────────────
# Human-readable decomposition of a `_score_candidate` result for the "como o
# score foi calculado" tooltips (Identificar Transações + Painel de Controle /
# securityMapping). The point weights MIRROR `_score_candidate` above (and the
# classifier's `_name_substring_bonus`). Display-only and self-correcting: any
# unexplained remainder (the dynamic rare-name bonus, or a drifted weight) is
# folded into a residual entry, so the listed parts ALWAYS sum to the real
# score and the tooltip can never misreport the total.
_BREAKDOWN_LABELS = {
    "mainId/ticker":     (50, "Ticker encontrado no mainId"),
    "mainId/isin":       (50, "ISIN encontrado no mainId"),
    "mainId/cetip":      (50, "Código CETIP encontrado no mainId"),
    "mainId/code":       (45, "Código interno encontrado no mainId"),
    "mainId/external":   (40, "Código externo encontrado no mainId"),
    "mainId/fund_code":  (35, "Código do fundo encontrado no mainId"),
    "selicCode~":        (40, "Código SELIC adjacente (±1)"),
    "mainId/structured": (50, "Padrão estruturado de opção no mainId/ticker"),
    "maturityDate=":     (30, "Vencimento exato (dia/mês/ano)"),
    "maturityDate":      (15, "Vencimento (mês/ano)"),
    "govBond=":          (50, "Título público: tipo + vencimento identificam o papel"),
    "coupon≠":           (0,  "Conflito de cupom (com vs sem juros semestrais)"),
    "indexer":           (10, "Indexador bate (CDI/IPCA/SELIC/…)"),
    "name~equiv":        (_NAME_EQUIV_BONUS, "Nome equivalente (mesmos tokens distintivos)"),
}
_EXACT_ID_LABELS = {
    "ticker":              "ticker",
    "ticker/FUT":          "ticker (futuro)",
    "taxId":               "CNPJ",
    "mainId/cnpj":         "CNPJ = mainId",
    "isIn":                "ISIN",
    "selicCode":           "código SELIC",
    "mainId/cetip=":       "código CETIP = mainId (exato)",
    "mainId/code=":        "código interno = mainId (exato)",
    "mainId/external=":    "código externo = mainId (exato)",
    "mainId/fund_code=":   "código do fundo = mainId (exato)",
    "generic_code_1":      "código genérico 1 = mainId",
    "generic_code_2":      "código genérico 2 = mainId",
    "generic_code_3":      "código genérico 3 = mainId",
}


def _score_breakdown(reasons, total_score):
    """Decompose ``reasons``/``total_score`` into ``[{code, points, label}]`` for
    the UI tooltip. Mirrors ``_score_candidate`` point weights; any unexplained
    remainder (e.g. the dynamic rare-name bonus) is folded into a residual entry
    so the parts always sum to ``total_score``."""
    reasons = list(reasons or [])
    try:
        total = int(total_score)
    except (TypeError, ValueError):
        total = 0

    # Hard rule: an exact unique identifier is a perfect match (100) and the
    # date gate is bypassed. `reasons` is then [<id_type>, "exact"].
    if "exact" in reasons:
        id_code = next((r for r in reasons if r != "exact"), "")
        idlbl = _EXACT_ID_LABELS.get(id_code, id_code or "identificador")
        return [{
            "code": id_code or "exact",
            "points": total,
            "label": f"Identificador exato ({idlbl}) — match perfeito, ignora a data",
        }]

    comps = []
    rare_idx = None       # comps index of the rare-name entry (absorbs residual)
    explained = 0
    for r in reasons:
        if r in _BREAKDOWN_LABELS:
            pts, lbl = _BREAKDOWN_LABELS[r]
            comps.append({"code": r, "points": pts, "label": lbl})
            explained += pts
        elif r.startswith("name(") and "/" in r:
            # name(o/t) → int((o / t) * 15)  (matcher's name-overlap formula)
            try:
                inside = r[r.index("(") + 1:r.index(")")]
                o_s, t_s = inside.split("/")
                o, t = int(o_s), int(t_s)
                pts = int((o / t) * 15) if t else 0
            except (ValueError, ZeroDivisionError):
                o = t = pts = 0
            comps.append({"code": r, "points": pts,
                          "label": f"Sobreposição de nome ({o} de {t} tokens)"})
            explained += pts
        elif r.startswith("mainId/name("):
            # mainId/name(N) → min(cap, per_hit * N)  (compacted-name in mainId)
            try:
                n = int(r[r.index("(") + 1:r.index(")")])
            except (ValueError, IndexError):
                n = 0
            pts = min(_MAINID_NAME_CAP, _MAINID_NAME_PER_HIT * n) if n else 0
            comps.append({"code": r, "points": pts,
                          "label": f"Nome no mainId comprimido ({n} token(s))"})
            explained += pts
        elif r.startswith("name~rare"):
            # Rarity bonus = min(30, round(rare_w*30)); not recoverable from the
            # code alone, so it takes the residual (computed after the loop).
            comps.append({"code": r, "points": 0,
                          "label": "Token de nome raro no acervo"})
            rare_idx = len(comps) - 1
        elif r.startswith("type="):
            comps.append({"code": r, "points": 12,
                          "label": f"Tipo de instrumento bate ({r[len('type='):]})"})
            explained += 12
        elif r.startswith("name~="):
            comps.append({"code": r, "points": 35,
                          "label": "Nome (comprimido) é substring da descrição"})
            explained += 35
        elif r.startswith("mainId~="):
            comps.append({"code": r, "points": 25,
                          "label": "mainId (comprimido) é substring da descrição"})
            explained += 25
        elif r == "fund~discr≠":
            comps.append({"code": r, "points": -_FUND_DISCR_PENALTY,
                          "label": "Classe/série/tranche do fundo difere — penalidade"})
            explained += -_FUND_DISCR_PENALTY
        elif r == "fund~class=":
            comps.append({"code": r, "points": _FUND_CLASS_CONFIRM,
                          "label": "Classe/tranche do fundo confere"})
            explained += _FUND_CLASS_CONFIRM
        elif r == "maturityDate≠":
            comps.append({"code": r, "points": 0,
                          "label": "Vencimento diverge — candidato rejeitado"})
        elif r.startswith("vehicle≠"):
            comps.append({"code": r, "points": 0,
                          "label": "Veículo/lastro diverge (CRA≠LCA, CRI≠CRA, …) — candidato rejeitado"})
        elif r.startswith("indexer≠"):
            comps.append({"code": r, "points": 0,
                          "label": "Indexador/regime diverge (IPCA/CDI ≠ Pré-fixado, …) — candidato rejeitado"})
        elif r.startswith("issuer≠"):
            comps.append({"code": r, "points": 0,
                          "label": "Emissor bancário diverge (ex.: Santander ≠ BTG) — candidato rejeitado"})
        else:
            comps.append({"code": r, "points": 0, "label": r})

    residual = total - explained
    if residual:
        if rare_idx is not None:
            comps[rare_idx]["points"] = residual
        else:
            comps.append({"code": "outros", "points": residual,
                          "label": "Outros sinais"})
    return comps


def _confidence_label(score):
    if score >= 75:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


# ── Security cache ─────────────────────────────────────────────────────────────

class SecurityCache:
    """
    In-memory indexed cache of the securities collection.

    Loads once per day from MongoDB, saves to data/securities_cache.json.
    Index lookups replace per-item MongoDB queries → ~10x faster matching.
    """

    def __init__(self):
        self._securities = []       # flat list of dicts
        self._by_type = {}          # securityType → [secs]
        self._indexes = {}          # field_name → {value_lower → [secs]}
        self._loaded_date = None    # date string "YYYY-MM-DD"
        self._count = 0

    @property
    def is_loaded(self):
        return self._count > 0

    @property
    def is_stale(self):
        """True if cache wasn't loaded today."""
        return self._loaded_date != str(_date.today())

    @property
    def loaded_date(self):
        return self._loaded_date

    @property
    def count(self):
        return self._count

    def load_from_db(self, db=None):
        """Rebuild indexes from the securities catalog (API-backed via
        beehus_catalog, with Mongo fallback). `db` is accepted for backward
        compatibility but no longer used."""
        import beehus_catalog
        docs = []
        for sec in beehus_catalog.all_securities():
            row = {"_id": str(sec["_id"])}
            for k in _CACHE_FIELDS:
                v = sec.get(k)
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    v = str(v)
                row[k] = v if v is not None else ""
            docs.append(row)

        today = str(_date.today())
        self._build(docs, today)
        if docs:
            self._save(docs, today)
        else:
            log.warning("SecurityCache: all_securities() returned 0 docs — skipping file save.")
        log.info("SecurityCache: loaded %d securities from DB", len(docs))

    def load_from_file(self):
        """Load from the JSON cache file if it exists and is from today."""
        if not os.path.exists(_CACHE_FILE):
            return False
        try:
            with open(_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded_date = data.get("date", "")
            if loaded_date != str(_date.today()):
                return False
            securities = data.get("securities", [])
            if not securities:
                log.warning("SecurityCache: file for today has 0 securities — ignoring.")
                return False
            self._build(securities, loaded_date)
            log.info("SecurityCache: loaded %d securities from file (date=%s)",
                     self._count, loaded_date)
            return True
        except Exception as exc:
            log.warning("SecurityCache: failed to load file: %s", exc)
            return False

    def ensure_loaded(self, db):
        """Load from file if today's cache exists, otherwise from DB."""
        if self.is_loaded and not self.is_stale:
            return False  # already up to date
        if self.load_from_file():
            return False  # loaded from file, no DB hit
        self.load_from_db(db)
        return True  # had to rebuild from DB

    def _build(self, docs, loaded_date):
        """Build in-memory indexes from a flat list of security dicts."""
        self._securities = docs
        self._count = len(docs)
        self._loaded_date = loaded_date

        # Group by securityType
        by_type = defaultdict(list)
        for s in docs:
            by_type[s.get("securityType", "")].append(s)
        self._by_type = dict(by_type)

        # Build exact-match indexes (lowercased keys)
        indexes = {}
        for key in _INDEX_KEYS:
            idx = defaultdict(list)
            for s in docs:
                val = s.get(key, "")
                if val:
                    idx[str(val).lower()].append(s)
            indexes[key] = dict(idx)
        self._indexes = indexes

        # Corpus token rarity for the name-match bonus in _score_candidate.
        # Module-level so the scorer can read it from just a `sec` dict.
        global _TOKEN_DF
        _TOKEN_DF = _build_token_df(docs)

    def _save(self, docs, loaded_date):
        """Persist cache to JSON file.

        Serialise only the canonical fields (`_id` + `_CACHE_FIELDS`). Scoring
        memoises derived data on the live dicts under underscore-prefixed keys
        (`_cand_dates`, `_name_tokens`, `_hay`, `_name_c`, `_main_c`); some of
        those hold `set` objects (not JSON-serialisable) and could be added by
        a concurrent request mid-save during the once-daily reload. Copying the
        known fields out sidesteps both the serialisation error and the
        dict-changed-size-during-iteration race."""
        try:
            from db import atomic_write_json
            clean = [
                {"_id": d.get("_id"), **{k: d.get(k, "") for k in _CACHE_FIELDS}}
                for d in docs
            ]
            atomic_write_json(_CACHE_FILE,
                              {"date": loaded_date, "securities": clean},
                              indent=None)
        except Exception as exc:
            log.warning("SecurityCache: failed to save file: %s", exc)

    def get_by_type(self, security_type):
        """Return list of securities for a given securityType."""
        return self._by_type.get(security_type, [])

    def lookup(self, field, value):
        """Exact-match lookup on an indexed field. Returns list of secs."""
        idx = self._indexes.get(field, {})
        return idx.get(str(value).lower(), [])

    def lookup_prefix(self, field, prefix):
        """Prefix-match lookup on an indexed field. Returns list of secs."""
        idx = self._indexes.get(field, {})
        prefix_lower = str(prefix).lower()
        results = []
        for key, secs in idx.items():
            if key.startswith(prefix_lower):
                results.extend(secs)
        return results

    def search(self, security_type, features, limit=30):
        """
        Find candidate securities using indexed lookups + regex fallback.

        Returns list of matching security dicts (unscored).
        """
        # Sem security_type (classificador indisponível — ver _get_classifier):
        # não existe "pool" pra esse caso (não há bucket None em self._by_type),
        # mas os índices exatos (lookup/lookup_prefix) são GLOBAIS, não
        # escopados por tipo — então ainda vale rodar as Fases 1/1b (ISIN,
        # CNPJ, mainId, ticker, código CETIP são identificadores fortes o
        # suficiente pra não precisar do tipo previsto). Só a Fase 2 (busca
        # fuzzy por nome) precisa mesmo de um pool tipado; ela é pulada abaixo
        # quando security_type é vazio. Quando security_type É informado, o
        # comportamento abaixo é IDÊNTICO ao de antes (guards são aditivos).
        pool = self.get_by_type(security_type) if security_type else None
        if security_type and not pool:
            return []

        candidates = {}  # _id → sec (dedup)

        # brazilianFund CNPJ → both taxId and mainId (the fund's mainId IS the
        # bare-digit CNPJ). Looking up mainId by the digits-only form pulls in
        # the right fund even when the description carries the CNPJ unpunctuated
        # and the name tokens don't overlap.
        feat_cnpj = features.get("cnpj")
        feat_cnpj_digits = _digits_only(feat_cnpj) if feat_cnpj else ""
        feat_cnpj_digits = feat_cnpj_digits if len(feat_cnpj_digits) >= 11 else None

        # Phase 1: exact index lookups (fast)
        _exact_search = [
            ("ticker",    features.get("ticker")),
            ("ticker",    "FUT" + features["ticker"] if "ticker" in features else None),
            ("taxId",     features.get("cnpj")),
            ("mainId",    feat_cnpj_digits),
            ("isIn",      features.get("isin")),
            ("mainId",    features.get("isin")),
            ("selicCode", features.get("selic_code")),
            ("mainId",    features.get("external_code")),
            ("mainId",    features.get("internal_code")),
            ("mainId",    features.get("cetip_code")),
            ("mainId",    features.get("fund_code")),
            ("mainId",    features.get("ticker")),
            ("mainId",    "FUT" + features["ticker"] if "ticker" in features else None),
            ("mainId",    features.get("complement_1")),
            ("mainId",    features.get("complement_2")),
            ("mainId",    features.get("complement_3")),
            ("mainId",    features.get("generic_code_1")),
            ("mainId",    features.get("generic_code_2")),
            ("mainId",    features.get("generic_code_3")),
        ]

        for field, value in _exact_search:
            if not value:
                continue
            for sec in self.lookup(field, value):
                if not security_type or sec.get("securityType") == security_type:
                    candidates[sec["_id"]] = sec

        # Phase 1b-x: cross-type mainId lookup for complement and generic-code tokens.
        # These are strong identifier signals — match against mainId in ANY securityType
        # without the type filter. _score_candidate ranks them via _EXACT_SCORE when
        # the token equals the candidate's mainId exactly.
        for _ci in range(1, 4):
            _comp_tok = features.get(f"complement_{_ci}")
            if not _comp_tok:
                continue
            for sec in self.lookup("mainId", _comp_tok):
                candidates[sec["_id"]] = sec
        for _ci in range(1, 4):
            _gc_tok = features.get(f"generic_code_{_ci}")
            if not _gc_tok:
                continue
            for sec in self.lookup("mainId", _gc_tok):
                candidates[sec["_id"]] = sec

        # Phase 1b: prefix lookups for structured patterns (options, futures)
        _prefix_search = []
        if "option_type" in features and "underlying" in features and "strike" in features:
            strike_int = features["strike"].split(".")[0]
            prefix = f"{features['option_type']}_{features['underlying']}_{strike_int}"
            _prefix_search.append(("mainId", prefix))
            _prefix_search.append(("ticker", prefix))
        if "underlying" in features:
            _prefix_search.append(("ticker", features["underlying"]))
            _prefix_search.append(("mainId", features["underlying"]))
        # B3 option ticker prefix: "PETRH325" finds "PETRH325W1" when the UID omits
        # the W-suffix but the catalog entry has it, or vice versa.
        if features.get("ticker") and "option_type" in features:
            _prefix_search.append(("mainId", features["ticker"]))
            _prefix_search.append(("ticker", features["ticker"]))
        # cetip_code as mainId prefix: catches mainIds like "CHSF13incentivada" when
        # the extracted code is "CHSF13" (catalog appends suffixes like "incentivada").
        if features.get("cetip_code"):
            _prefix_search.append(("mainId", features["cetip_code"]))

        for field, prefix in _prefix_search:
            if not prefix:
                continue
            for sec in self.lookup_prefix(field, prefix):
                if not security_type or sec.get("securityType") == security_type:
                    candidates[sec["_id"]] = sec

        # Phase 2: scan type pool for name/keyword matches — precisa de um pool
        # tipado (`pool` é None quando security_type é vazio), então é pulada
        # nesse caso; Fases 1/1b acima já cobrem os identificadores exatos.
        if security_type and len(candidates) < limit:
            # Collect all searchable terms
            search_terms = []
            for key in ["name", "issuer"]:
                val = features.get(key, "")
                if val:
                    search_terms.extend(w.lower() for w in re.split(r"[\s\-_/,\.\(\)]", val) if len(w) >= 3)
            # Include bond_type, instrument even if short (LFT, LF, DEB...)
            for key in ["bond_type", "instrument"]:
                val = features.get(key, "")
                if val and len(val) >= 2:
                    search_terms.append(val.lower())
            # Include internal_code and isin as substring search
            for key in ["internal_code", "isin"]:
                val = features.get(key, "")
                if val and len(val) >= 6:
                    search_terms.append(val.lower())

            if search_terms:
                search_terms_unique = list(dict.fromkeys(search_terms))
                longest = max(search_terms_unique, key=len)
                longest_lower = longest.lower()
                mat = features.get("maturity_date", "")  # for narrowing

                # First pass: name + maturityDate (precise)
                if mat:
                    for sec in pool:
                        if sec["_id"] in candidates:
                            continue
                        sec_mat = str(sec.get("maturityDate", ""))[:10]
                        if sec_mat != mat:
                            continue
                        bn  = (sec.get("beehusName") or "").lower()
                        mid = (sec.get("mainId") or "").lower()
                        tkr = (sec.get("ticker") or "").lower()
                        haystack = f"{bn} {mid} {tkr}"
                        if longest_lower in haystack:
                            candidates[sec["_id"]] = sec

                # Second pass: name only (broader)
                if len(candidates) < limit:
                    for sec in pool:
                        if sec["_id"] in candidates:
                            continue
                        bn  = (sec.get("beehusName") or "").lower()
                        mid = (sec.get("mainId") or "").lower()
                        tkr = (sec.get("ticker") or "").lower()
                        haystack = f"{bn} {mid} {tkr}"
                        if longest_lower in haystack:
                            candidates[sec["_id"]] = sec
                            if len(candidates) >= limit:
                                break

        # Phase 3: selicCode adjacent (off-by-one) for gov bonds
        if "selic_code" in features and security_type == "brazilianGovernmentBond":
            try:
                code_int = int(features["selic_code"])
                for delta in [-1, 1]:
                    for sec in self.lookup("selicCode", str(code_int + delta)):
                        if sec.get("securityType") == security_type:
                            candidates[sec["_id"]] = sec
            except ValueError:
                pass

        return list(candidates.values())


# Module-level cache singleton
_cache = SecurityCache()


def get_cache():
    """Return the module-level SecurityCache singleton."""
    return _cache


class MappingCache:
    """In-memory cache of every securityMappings `from→to` pair
    (unprocessedId → securityId) across all companies.

    Loaded once per day from the Beehus API
    (`beehus_catalog.all_security_mappings()`, GET
    /beehus/financial/security-mappings), saved to
    data/security_mappings_cache.json. Mirrors `SecurityCache`'s lifecycle
    (file-of-today → API → persist, daily staleness).

    Replaces the all-company `db.unprocessedSecurityPositions` scan that
    `security_type_classifier.rebuild_mapping` used only to enumerate the
    distinct unprocessedIds — those are exactly the mapping `from` values, so
    the catalog gives them directly and no Mongo read is needed.
    """

    def __init__(self):
        self._mapping = {}        # {unprocessedId_str: securityId_str}
        self._loaded_date = None  # date string "YYYY-MM-DD"
        self._count = 0

    @property
    def is_loaded(self):
        return self._count > 0

    @property
    def is_stale(self):
        """True if cache wasn't loaded today."""
        return self._loaded_date != str(_date.today())

    @property
    def loaded_date(self):
        return self._loaded_date

    @property
    def count(self):
        return self._count

    def as_dict(self):
        """Copy of the `{unprocessedId: securityId}` map (safe to iterate)."""
        return dict(self._mapping)

    def load_from_api(self):
        """Rebuild the map from the security-mappings catalog (API-backed via
        beehus_catalog). Last-write-wins on a `from` shared across companies —
        same as the previous in-place `mapping[from] = to` build."""
        import beehus_catalog
        mapping = {}
        for doc in beehus_catalog.all_security_mappings():
            for m in (doc.get("mappings") or []):
                f, t = m.get("from"), m.get("to")
                if f and t:
                    mapping[str(f)] = str(t)
        today = str(_date.today())
        self._build(mapping, today)
        self._save(mapping, today)
        log.info("MappingCache: loaded %d unprocessedId->securityId pairs from API", len(mapping))

    def load_from_file(self):
        """Load from the JSON cache file if it exists and is from today."""
        if not os.path.exists(_MAPPING_CACHE_FILE):
            return False
        try:
            with open(_MAPPING_CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            loaded_date = data.get("date", "")
            if loaded_date != str(_date.today()):
                return False
            self._build(data.get("mappings", {}), loaded_date)
            log.info("MappingCache: loaded %d pairs from file (date=%s)",
                     self._count, loaded_date)
            return True
        except Exception as exc:
            log.warning("MappingCache: failed to load file: %s", exc)
            return False

    def ensure_loaded(self):
        """Load from today's file if present, otherwise from the API.
        Returns True if the API had to be hit."""
        if self.is_loaded and not self.is_stale:
            return False
        if self.load_from_file():
            return False
        self.load_from_api()
        return True

    def refresh(self):
        """Force-reload from the API."""
        self.load_from_api()

    def _build(self, mapping, loaded_date):
        self._mapping = dict(mapping)
        self._count = len(self._mapping)
        self._loaded_date = loaded_date

    def _save(self, mapping, loaded_date):
        try:
            from db import atomic_write_json
            atomic_write_json(_MAPPING_CACHE_FILE,
                              {"date": loaded_date, "mappings": mapping},
                              indent=None)
        except Exception as exc:
            log.warning("MappingCache: failed to save file: %s", exc)


# Module-level cache singleton
_mapping_cache = MappingCache()


def get_mapping_cache():
    """Return the module-level MappingCache singleton."""
    return _mapping_cache


# ── Main matcher class ─────────────────────────────────────────────────────────

class SecurityMatcher:
    """
    Match an unprocessedId against the securities collection using an
    in-memory indexed cache (no per-item MongoDB queries).

    Usage:
        matcher = SecurityMatcher(db)
        result = matcher.match("(ABCB4) ABC BRASILPN N2")
    """

    def __init__(self, db, classifier=None, cache=None):
        self.db = db
        self._classifier = classifier
        self._cache = cache or _cache

    def _get_classifier(self):
        if self._classifier is None:
            from security_type_classifier import SecurityTypeClassifier
            c = SecurityTypeClassifier()
            try:
                c.train()
            except Exception:
                # Sem dados de treino ainda (ambiente novo, zero mapeamentos
                # históricos) — não envenena self._classifier com uma instância
                # quebrada; sem isso, TODA chamada seguinte a match() sem
                # security_type explícito voltava a tentar essa mesma instância
                # já marcada "trained=False" e explodia "Classifier not
                # trained – call .train() first." pro resto da vida do processo.
                # Mesma filosofia do _get_classifier module-level em
                # pages/controlpanel.py: só cacheia depois de treinar com sucesso.
                return None
            self._classifier = c
        return self._classifier

    def ensure_cache(self):
        """Ensure cache is loaded (from file or DB). Returns True if DB was hit."""
        return self._cache.ensure_loaded(self.db)

    def refresh_cache(self):
        """Force-reload cache from DB."""
        self._cache.load_from_db(self.db)

    def match(self, unprocessed_id, security_type=None, type_confidence=None, limit=10,
              inject_features=None):
        """
        Match an unprocessedId against securities via indexed cache.

        Returns dict with:
            predicted_type, type_confidence, extracted, candidates, already_registered
        """
        # Ensure cache is ready
        self._cache.ensure_loaded(self.db)

        # Step 1: classify type if not provided
        if not security_type:
            clf = self._get_classifier()
            if clf is not None:
                prediction = clf.predict(unprocessed_id)
                security_type = prediction["type"]
                type_confidence = prediction["confidence"]
            # clf is None: sem base de treino ainda — segue sem tipo previsto
            # em vez de estourar. O operador escolhe manualmente ("— selecionar").

        # Step 2: extract features
        features = extract_features(unprocessed_id, security_type)
        if inject_features:
            features.update(inject_features)

        # Step 3: search cache (index lookups + regex fallback)
        raw_candidates = self._cache.search(security_type, features, limit=limit * 3)

        # Step 4: score candidates
        candidates = []
        for sec in raw_candidates:
            score, matched_on = _score_candidate(sec, features, security_type)
            candidates.append({
                "securityId":   sec["_id"],
                "beehusName":   sec.get("beehusName", ""),
                "mainId":       sec.get("mainId", ""),
                "ticker":       sec.get("ticker", ""),
                "maturityDate": str(sec.get("maturityDate", ""))[:10],
                "indexer":      sec.get("indexer", ""),
                "score":        score,
                "confidence":   _confidence_label(score),
                "matched_on":   matched_on,
            })

        candidates.sort(key=lambda x: x["score"], reverse=True)
        candidates = candidates[:limit]

        already_registered = bool(candidates and candidates[0]["score"] >= 50)

        return {
            "unprocessedId":  unprocessed_id,
            "predicted_type": security_type,
            "type_confidence": type_confidence,
            "extracted":      features,
            "candidates":     candidates,
            "already_registered": already_registered,
        }


# ── Standalone test (extraction only, no DB needed) ────────────────────────────

if __name__ == "__main__":
    test_cases = [
        ("stockEtf",                "(ABCB4) ABC BRASILPN N2"),
        ("stockEtf",                "ABCB4 - ABC BRASIL - ABC BRASIL"),
        ("brazilianFund",           "(52532) MANAGED PORTFOLIO 3 - 19.358.594/0001-35"),
        ("brazilianFund",           "(54079) PRECISION ADVANCED - 54079 - ITAU"),
        ("brazilianGovernmentBond", "BACEN-BANCO CENTRAL DO BRASIL - RJ - LFT - SELIC + 0,05% - 2027-03-01"),
        ("brazilianGovernmentBond", "NTN-B 760198 - TESOURO NACIONAL - 2035-05-15 - 100% - IPC-A - 6.9 - 760198"),
        ("bond",                    "+ DEB LIGHT - NOV/2032 16,19% - 13/NOV/2032"),
        ("bond",                    "0%CDI+10,22%aa - LF - LF BRADESCO 10,22% 02/Jan/2029"),
        ("bond",                    "BANCO BRADESCO S.A. - LF-LF0020003KK - 10,22% a.a. - 2029-01-02"),
        ("bond",                    "LCA PRE 24G01629552 - BANCO BOCOM BBM SA - 2028-07-17 - 10.95 - 24G01629552"),
        ("options",                 "APPLE INC CALL 04/17/26 @ 280 - AAPL - 0378331MA"),
        ("options",                 "PUT_AAPL_200_17042026 -  - PUT APPLE @200 17/Apr/2026"),
        ("futures",                 "BRBMEFD1I6J9_DI1FF35"),
        ("futures",                 "FUTDI1F35 - Mercado Futuro - DI FUTURO Jan/2035"),
        ("fund",                    "007533630 - Ativo Generico - TWO SIGMA PREMIUM SELECTION"),
        ("fund",                    "KYG7231R3239 - Ativo Generico - POINT72 PLUS"),
        ("brazilianRepo",           "COMPROMISSADA 01/04/2026 - Bacen RJ"),
        ("brazilianRepo",           "100%ipcadp+7,5779%aa - CRI/CRA - COMPROMISSADA INCENTIVADA"),
        ("otc",                     "011311011311"),
        ("otc",                     "06417YR27 - Ativo Generico - S&P 500 DUAL DIRECTIONAL 16/Mar/2026"),
        ("sovereignBonds",          "GERMAN TREAS BILL ZERO CPN 05/13/2026 DTD 05/14/2025 HELD BY EUROCLEAR ISIN DE000BU0E295"),
        ("realAssets",              "FAZENDA RIBEIRAO PRETO 500HA"),
        ("privateMarket",           "1/3 DA CASA RESIDENCIAL"),
        ("poc",                     "006229BRAD PRIV MULT ASSET"),
    ]

    print("=" * 90)
    print("SECURITY MATCHER — Feature Extraction Tests")
    print("=" * 90)

    for sec_type, uid in test_cases:
        features = extract_features(uid, sec_type)
        print(f"\n[{sec_type}]")
        print(f"  Input:    {uid}")
        print(f"  Features: {features}")
