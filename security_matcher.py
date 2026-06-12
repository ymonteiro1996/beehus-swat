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

log = logging.getLogger(__name__)

_CACHE_DIR  = os.path.join(os.path.dirname(__file__), "data")
_CACHE_FILE = os.path.join(_CACHE_DIR, "securities_cache.json")

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

def _parse_date(text, prefer_mdy=False):
    """
    Try to extract a date from various formats found in unprocessedIds.
    Returns ISO string (YYYY-MM-DD) or None.

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
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    # DD/MM/YYYY (or MM/DD/YYYY — auto-detect by checking if day > 12)
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), m.group(3)
        if a > 12 and 1 <= b <= 12:
            # a is day, b is month (DD/MM/YYYY)
            return f"{y}-{b:02d}-{a:02d}"
        elif b > 12 and 1 <= a <= 12:
            # a is month, b is day (MM/DD/YYYY — US format)
            return f"{y}-{a:02d}-{b:02d}"
        elif prefer_mdy:
            # Ambiguous + American context → MM/DD/YYYY (a=month, b=day)
            return f"{y}-{m.group(1)}-{m.group(2)}"
        else:
            # Ambiguous — default to DD/MM/YYYY (BR standard)
            return f"{y}-{m.group(2)}-{m.group(1)}"

    # DD/MMM/YYYY  (e.g. 13/NOV/2032)
    m = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", text)
    if m:
        d, mo_str, y = int(m.group(1)), m.group(2).lower(), m.group(3)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            return f"{y}-{mo:02d}-{d:02d}"

    # MMM/YYYY  (e.g. NOV/2032 — day unknown, use 01)
    m = re.search(r"([A-Za-z]{3})/(\d{4})", text)
    if m:
        mo_str, y = m.group(1).lower(), m.group(2)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            return f"{y}-{mo:02d}-01"

    # DD/Mon/YYYY in text like "02/Jan/2029"
    m = re.search(r"(\d{1,2})/([A-Za-z]{3})/(\d{4})", text)
    if m:
        d, mo_str, y = int(m.group(1)), m.group(2).lower()[:3], m.group(3)
        mo = _MONTH_ALL.get(mo_str)
        if mo:
            return f"{y}-{mo:02d}-{d:02d}"

    # MM/DD/YY (US format, common in options like 04/17/26)
    m = re.search(r"(\d{2})/(\d{2})/(\d{2})(?!\d)", text)
    if m:
        mo, d, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if 1 <= mo <= 12 and 1 <= d <= 31:
            y = 2000 + y2 if y2 < 80 else 1900 + y2
            return f"{y}-{mo:02d}-{d:02d}"

    return None


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

    # CNPJ: XX.XXX.XXX/XXXX-XX
    m = re.search(r"(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})", uid)
    if m:
        features["cnpj"] = m.group(1)

    # Name: text between code and separator
    name = uid
    name = re.sub(r"\(\d{4,6}\)\s*", "", name)      # remove (code)
    name = re.sub(r"\s*-\s*\d{4,6}(?:\s*-.*)?$", "", name)  # remove trailing code
    name = re.sub(r"\s*-\s*\d{2}\.\d{3}\..*$", "", name)    # remove CNPJ part
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

    return features


def _extract_bond(uid):
    features = {}
    # Instrument type
    m = re.search(r"\b(DEB|CRI|CRA|LCA|LCI|CDB|CCB|LF|LIG|FIDC|FND)\b", uid, re.IGNORECASE)
    if m:
        features["instrument"] = m.group(1).upper()

    # CETIP code
    m = re.search(r"CETIP_([A-Z0-9]+)", uid)
    if m:
        features["cetip_code"] = m.group(1)

    # Specific code like LF0020003KK, CDB6246IACD, 24G01629552, etc.
    m = re.search(r"\b([A-Z]{2,4}\d{4,}[A-Z0-9]*)\b", uid)
    if m and m.group(1) != features.get("instrument"):
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
            issuer = re.sub(r"\s*\d+[,\.]\d+%.*$", "", issuer)
            issuer = re.sub(r"\s*(?:JAN|FEV|MAR|ABR|MAI|JUN|JUL|AGO|SET|OUT|NOV|DEZ)/\d{4}.*$", "", issuer, flags=re.IGNORECASE)
            # Strip leading indexer keywords + alphanumeric codes
            issuer = re.sub(r"^(PRE|CDI|IPCA|SELIC|DU)\s+", "", issuer, flags=re.IGNORECASE)
            issuer = re.sub(r"^[A-Z0-9]{8,}\s*-?\s*", "", issuer)  # strip long codes
            issuer = issuer.strip(" -")
            if issuer and len(issuer) > 2:
                words = issuer.split()
                if len(words) <= 2 and all(len(w) <= 3 for w in words):
                    pass  # skip, try strategy 2
                else:
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
                features["issuer"] = part
                break

    # Maturity date
    date = _parse_date(uid)
    if date:
        features["maturity_date"] = date

    # Rate/yield
    rates = re.findall(r"(\d+[,\.]\d+)\s*%", uid)
    if rates:
        features["rate"] = rates[-1].replace(",", ".")  # last rate is usually the yield

    # Indexer
    m = re.search(r"\b(CDI|IPCA|SELIC|PRE|IGPM|IPC-A|IPCADP)\b", uid, re.IGNORECASE)
    if m:
        idx = m.group(1).upper()
        if idx == "IPC-A" or idx == "IPCADP":
            idx = "IPCA"
        features["indexer"] = idx

    return features


def _extract_gov_bond(uid):
    features = {}
    # Bond type
    m = re.search(r"\b(LFT|NTN-?B|NTN-?F|LTN|NTNB|NTNF)\b", uid, re.IGNORECASE)
    if m:
        btype = m.group(1).upper().replace("-", "")
        # Normalize: NTNB → NTN-B
        if btype == "NTNB":
            btype = "NTN-B"
        elif btype == "NTNF":
            btype = "NTN-F"
        features["bond_type"] = btype

    # SELIC code (6-digit number in specific patterns)
    m = re.search(r"\b(\d{6})\b", uid)
    if m:
        code = m.group(1)
        # Avoid dates (DDMMYY)
        if not re.search(r"\d{2}/\d{2}/\d{2}", uid):
            features["selic_code"] = code

    # Maturity date
    date = _parse_date(uid)
    if date:
        features["maturity_date"] = date

    # Indexer
    m = re.search(r"\b(SELIC|IPCA|IPC-A|PRE)\b", uid, re.IGNORECASE)
    if m:
        idx = m.group(1).upper()
        if idx == "IPC-A":
            idx = "IPCA"
        features["indexer"] = idx

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
    date = _parse_date(uid)
    if date:
        features["maturity_date"] = date

    return features


def _extract_options(uid):
    features = {}
    # Underlying ticker (common patterns)
    # Pattern: "TICKER CALL/PUT" or "_TICKER_"
    m = re.search(r"[_\s-]([A-Z]{2,5})[_\s-]", uid)
    if m:
        features["underlying"] = m.group(1)

    # Better: ticker after known pattern
    m = re.search(r"-\s*([A-Z]{2,5})\s*-", uid)
    if m:
        features["underlying"] = m.group(1)

    # Option type
    m = re.search(r"\b(CALL|PUT)\b", uid, re.IGNORECASE)
    if m:
        features["option_type"] = m.group(1).upper()

    # Strike price
    m = re.search(r"@\s*(\d+(?:[.,]\d+)?)", uid)
    if m:
        features["strike"] = m.group(1).replace(",", ".")
    else:
        # Pattern: _STRIKE_DATE
        m = re.search(r"_(\d+)_\d{8}", uid)
        if m:
            features["strike"] = m.group(1)

    # Expiry date
    date = _parse_date(uid)
    if date:
        features["expiry"] = date

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

    # Name after "Ativo Generico -"
    m = re.search(r"Ativo\s+Gen[eé]rico\s*-\s*(.+?)(?:\s*$)", uid, re.IGNORECASE)
    if m:
        features["name"] = m.group(1).strip()

    # Maturity date
    date = _parse_date(uid)
    if date:
        features["maturity_date"] = date

    return features


def _extract_brazilian_repo(uid):
    features = {}
    # Maturity date
    date = _parse_date(uid)
    if date:
        features["maturity_date"] = date

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
    date = _parse_date(uid)
    if date:
        features["maturity_date"] = date

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


def extract_features(uid, security_type):
    """Extract structured features from an unprocessedId given its securityType."""
    extractor = _EXTRACTORS.get(security_type, _extract_generic)
    return extractor(uid)


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
# stacks with the partial-id) + exact date 50 + indexer 10 + name 15 + rare-name
# 30 + type 12 = 217. The Painel de Controle's price-agreement then adds up to
# +30 (→ 247), while an exact match can itself absorb a −25 price penalty
# (→ _EXACT_SCORE − 25). 300 keeps an exact id strictly above any heuristic
# combination (300 − 25 = 275 > 247). The downstream numeric confidence is
# clamped to 1.0 (classifier) and the Match badge to 100%, so the >100 raw
# score surfaces only in the score-breakdown tooltip and the ranking.
_EXACT_SCORE = 300

# Instrument / government-bond type agreement (NTN-B, LFT, CDB, CRI…). Added
# ONLY as a tie-breaker on top of an already-plausible candidate (score > 0
# from a date / name / id signal) — never on its own, so it can't promote a
# generic type-only match (every CDB at once). Small enough not to rival a
# partial id (+40-50): it breaks ties between same-date bonds of different
# kinds (the NTN-B beats a corporate bond maturing the same day).
_TYPE_AGREE_BONUS = 12


def _exact_identifier_match(sec, features):
    """Return a label if an exact, unique identifier matches, else None.

    "Exact" means strict equality (not substring/adjacency): ticker (incl. the
    FUT-prefixed variant), taxId/CNPJ, ISIN, SELIC code, OR an extracted code
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
    sec_taxid = sec.get("taxId")
    if feat_cnpj and sec_taxid:
        # Punctuation-insensitive: compare digits only so a bare 14-digit CNPJ
        # in the text matches a punctuated taxId. Treated as an exact unique
        # identifier, same weight as ticker/isin. The ≥11-digit guard avoids
        # matching on a stray short number.
        fd = _digits_only(feat_cnpj)
        if len(fd) >= 11 and fd == _digits_only(sec_taxid):
            return "taxId"
    if features.get("isin") and sec.get("isIn") and sec.get("isIn") == features["isin"]:
        return "isIn"
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


def _date_agrees(feat_date, cand_dates, day_known):
    """True if the operator's date matches at least one candidate date at the
    precision the operator supplied: full day when a day was named, otherwise
    year+month only (so 'SET/2029', whose day `_parse_date` defaults to 01,
    isn't false-rejected against a day-precise maturityDate in the same month)."""
    if day_known:
        return feat_date in cand_dates
    fym = feat_date[:7]
    return any(d[:7] == fym for d in cand_dates)


def _score_candidate(sec, features, security_type):
    """Score a candidate security against extracted features.

    Two hard rules dominate the additive signals:

      • Exact unique identifier (ticker / taxId / isIn / selicCode / a code that
        equals the whole mainId) → top score (`_EXACT_SCORE`, above the heuristic
        ceiling so it always outranks a partial pile-up). Short-circuits and
        IGNORES the date gate: an exact id identifies the asset on its own.

      • Otherwise, if the operator named a maturity/expiry date AND the
        candidate exposes a date (maturityDate field OR a date in beehusName)
        that does NOT agree → score 0. A different date means a different asset.

    When neither hard rule fires, the score is the sum of the partial signals
    (substring id, structured-option pattern, agreeing date, indexer, name
    token overlap), on a ~0–100 scale.
    """
    # ── Rule 1: exact identifier wins (ignores the date gate) ──────────────
    exact = _exact_identifier_match(sec, features)
    if exact:
        return _EXACT_SCORE, [exact, "exact"]

    sec_main_id = str(sec.get("mainId", ""))

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
                # When the day was named AND it agreed, the match was on the
                # full YYYY-MM-DD (the gate requires feat_date in cand_dates),
                # so this is an exact day/month/year coincidence.
                date_agreed_exact = day_known
            else:
                return 0, ["maturityDate≠"]

    score = 0
    matched_on = []

    # ── Partial identifier matches (substring / adjacency — not decisive) ──
    if "cetip_code" in features and features["cetip_code"].lower() in sec_main_id.lower():
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

    # Agreeing maturity date confirms a non-exact candidate. An exact
    # day/month/year coincidence is strong evidence (+50, reaches the
    # "confident" threshold); a month+year-only agreement stays at +25.
    if date_agreed_exact:
        score += 50
        matched_on.append("maturityDate=")
    elif date_agreed:
        score += 25
        matched_on.append("maturityDate")

    # Indexer match (+10)
    if "indexer" in features:
        sec_idx = str(sec.get("indexer", "")).upper()
        if features["indexer"] in sec_idx or sec_idx in features["indexer"]:
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

    return score, matched_on


# ── Score breakdown (display-only) ──────────────────────────────────────────
# Human-readable decomposition of a `_score_candidate` result for the "como o
# score foi calculado" tooltips (Identificar Transações + Painel de Controle /
# securityMapping). The point weights MIRROR `_score_candidate` above (and the
# classifier's `_name_substring_bonus`). Display-only and self-correcting: any
# unexplained remainder (the dynamic rare-name bonus, or a drifted weight) is
# folded into a residual entry, so the listed parts ALWAYS sum to the real
# score and the tooltip can never misreport the total.
_BREAKDOWN_LABELS = {
    "mainId/cetip":      (50, "Código CETIP encontrado no mainId"),
    "mainId/code":       (45, "Código interno encontrado no mainId"),
    "mainId/external":   (40, "Código externo encontrado no mainId"),
    "mainId/fund_code":  (35, "Código do fundo encontrado no mainId"),
    "selicCode~":        (40, "Código SELIC adjacente (±1)"),
    "mainId/structured": (50, "Padrão estruturado de opção no mainId/ticker"),
    "maturityDate=":     (50, "Vencimento exato (dia/mês/ano)"),
    "maturityDate":      (25, "Vencimento (mês/ano)"),
    "indexer":           (10, "Indexador bate (CDI/IPCA/SELIC/…)"),
}
_EXACT_ID_LABELS = {
    "ticker":              "ticker",
    "ticker/FUT":          "ticker (futuro)",
    "taxId":               "CNPJ",
    "isIn":                "ISIN",
    "selicCode":           "código SELIC",
    "mainId/cetip=":       "código CETIP = mainId (exato)",
    "mainId/code=":        "código interno = mainId (exato)",
    "mainId/external=":    "código externo = mainId (exato)",
    "mainId/fund_code=":   "código do fundo = mainId (exato)",
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
        elif r == "maturityDate≠":
            comps.append({"code": r, "points": 0,
                          "label": "Vencimento diverge — candidato rejeitado"})
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

    def load_from_db(self, db):
        """Fetch all securities from MongoDB and rebuild indexes."""
        docs = []
        for sec in db.securities.find({}, _CACHE_FIELDS):
            row = {"_id": str(sec["_id"])}
            for k in _CACHE_FIELDS:
                v = sec.get(k)
                if v is not None and not isinstance(v, (str, int, float, bool)):
                    v = str(v)
                row[k] = v if v is not None else ""
            docs.append(row)

        today = str(_date.today())
        self._build(docs, today)
        self._save(docs, today)
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
            self._build(data.get("securities", []), loaded_date)
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
        pool = self.get_by_type(security_type)
        if not pool:
            return []

        candidates = {}  # _id → sec (dedup)

        # Phase 1: exact index lookups (fast)
        _exact_search = [
            ("ticker",    features.get("ticker")),
            ("ticker",    "FUT" + features["ticker"] if "ticker" in features else None),
            ("taxId",     features.get("cnpj")),
            ("isIn",      features.get("isin")),
            ("selicCode", features.get("selic_code")),
            ("mainId",    features.get("external_code")),
            ("mainId",    features.get("internal_code")),
            ("mainId",    features.get("cetip_code")),
            ("mainId",    features.get("fund_code")),
            ("mainId",    features.get("ticker")),
            ("mainId",    "FUT" + features["ticker"] if "ticker" in features else None),
        ]

        for field, value in _exact_search:
            if not value:
                continue
            for sec in self.lookup(field, value):
                if sec.get("securityType") == security_type:
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

        for field, prefix in _prefix_search:
            if not prefix:
                continue
            for sec in self.lookup_prefix(field, prefix):
                if sec.get("securityType") == security_type:
                    candidates[sec["_id"]] = sec

        # Phase 2: scan type pool for name/keyword matches
        if len(candidates) < limit:
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
            self._classifier = SecurityTypeClassifier()
            self._classifier.train()
        return self._classifier

    def ensure_cache(self):
        """Ensure cache is loaded (from file or DB). Returns True if DB was hit."""
        return self._cache.ensure_loaded(self.db)

    def refresh_cache(self):
        """Force-reload cache from DB."""
        self._cache.load_from_db(self.db)

    def match(self, unprocessed_id, security_type=None, type_confidence=None, limit=10):
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
            prediction = clf.predict(unprocessed_id)
            security_type = prediction["type"]
            type_confidence = prediction["confidence"]

        # Step 2: extract features
        features = extract_features(unprocessed_id, security_type)

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
