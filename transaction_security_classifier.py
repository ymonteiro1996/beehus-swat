"""Transaction → securityId classifier (no securityType gating).

Scores a transaction's `description` directly against actual security docs,
using the same scoring function (`_score_candidate`) that the
*Issues > Mapeamento* page uses to rank candidates against unprocessedIds.

The previous implementation had `SecurityTypeClassifier` in front of the
matcher: predict a securityType, then search only within that type's pool.
That gates everything on a noisy classifier (the type model was trained
on unprocessedIds, which look different from transaction descriptions),
so a wrong type prediction returned zero candidates.

This version drops the type-classifier step entirely:

    description
        ↓ extract features (run ALL type-specific extractors and merge —
                            each one only matches what it can recognise)
        ↓
    Score against the wallet's L1 holdings (current processedPosition)
        ↓ if best L1 score < threshold:
    Score against L2 (T-1, T-2 holdings)
        ↓ if best (L1 ∪ L2) score < threshold:
    Score against L3 (entire SecurityCache)
        ↓
    Top-3 → ambiguity check → buySell tie-breaker → return

The buySell tie-breaker (unchanged) walks the wallet's processedPositions
and picks the candidate whose `qty_T − qty_(T−1)` is non-zero on a NAV
date such that `navDate + offset ≈ liquidationDate`, where
`offset = settlementDays − navDays` (subscription/redemption fields on
the security, depending on the sign of the delta).
"""
import logging
import re
from datetime import date as _date, datetime, timedelta
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId

import security_matcher  # _score_candidate, get_cache, SecurityCache, _parse_date

log = logging.getLogger(__name__)


# ── Generic feature extraction (no type gate, conservative) ───────────────────

# Action verbs / cash-flow tokens that appear in transaction descriptions but
# never in a securityName/mainId. Used to filter the "name" feature so we
# don't poison the matcher's name-token-overlap score.
_NOISE_TOKENS = {
    "PGTO", "PAGAMENTO", "JUROS", "RECEBIMENTO", "VENCIMENTO", "VCTO",
    "COMPRA", "VENDA", "RESGATE", "AQUISICAO", "APLICACAO", "EMISSAO",
    "DIVIDEND", "DIVIDENDS", "DIVIDENDO", "DIVIDENDOS",
    "RENDIMENTO", "RENDIMENTOS", "DEPOSITO", "DEPOSIT", "WITHDRAWAL",
    "TED", "DOC", "PIX", "WIRE", "TAXA", "TARIFA", "FEE", "CHARGE",
    "IRRF", "IOF", "IR", "JCP", "ESTORNO", "AJUSTE", "LIQUID", "LIQ",
    "BOLSA", "CAIXA", "CONTA", "BANCO", "BANK",
    "DE", "DA", "DO", "DAS", "DOS", "E", "A", "O", "AS", "OS",
    "S", "SA", "S/A", "LTDA", "EM", "NA", "NO", "NAS", "NOS", "PARA", "POR",
    "OF", "THE", "AND", "FOR", "TO", "INC", "LLC", "CORP", "PER", "SHARE",
    "COTAS", "COTA", "FUND", "FUNDO",
    "VS", "PRO", "TRADE", "ID",
}

# Bond instruments (CRI/CRA/etc.) in the security_matcher.bond extractor.
_BOND_INSTRUMENTS  = re.compile(r"\b(DEB|CRI|CRA|LCA|LCI|CDB|CCB|LF|LIG|FIDC|FND)\b")
_GOV_BOND_TYPES    = re.compile(r"\b(LFT|NTN-?B|NTN-?F|LTN|NTNB|NTNF)\b")
_INDEXERS          = re.compile(r"\b(CDI|IPCA|SELIC|PRE|IGPM|IPC-A|IPCADP)\b")
_TICKER_LIKE       = re.compile(r"\b([A-Z]{3,6}\d{1,2})\b")
_CNPJ              = re.compile(r"\b(\d{2}\.\d{3}\.\d{3}/\d{4}-\d{2})\b")
# Bare (unpunctuated) 14-digit CNPJ — some transaction texts carry the raw
# digits. \b on both sides so a longer digit run (15+) isn't sliced into a
# spurious 14-digit match. Matched against the security's punctuated taxId
# via digits-only comparison in security_matcher._exact_identifier_match.
_CNPJ_BARE         = re.compile(r"\b(\d{14})\b")
# ISIN = 2-letter country code + 9 alphanumeric NSIN + 1 numeric check digit.
# The trailing \d is what stops ordinary 12-letter words ("INVESTIMENTO") from
# being misread as an ISIN — a valid ISIN always ends in a digit.
_ISIN              = re.compile(r"\b([A-Z]{2}[A-Z0-9]{9}\d)\b")
_FUND_CODE_PARENS  = re.compile(r"\((\d{4,6})\)")
_INTERNAL_CODE     = re.compile(r"\b([A-Z0-9]{8,})\b")  # alnum codes (must mix L+D)
_RATE              = re.compile(r"(\d+[,\.]\d+)\s*%")
_NUMERIC_6         = re.compile(r"\b(\d{6})\b")


def _extract_generic_safe(description, prefer_mdy=False):
    """Build a features dict from a transaction description without trusting
    any securityType prediction. Conservative on every key — set a feature
    only when the pattern is *specific enough* that it won't poison
    `_score_candidate`'s if/elif id-match chain.

    `prefer_mdy`: read ambiguous numeric dates as American MM/DD/YYYY (set by
    the caller when the wallet currency is USD).

    What's intentionally NOT set unless context warrants it:
      - `selic_code`: only when the description also names a gov-bond type
      - `external_code`: never set; leading words like "AQUISICAO" are noise
      - `name`: stripped of action verbs / stop-words
    """
    features = {}
    upper = description.upper()

    m = _CNPJ.search(upper)
    if m:
        features["cnpj"] = m.group(1)
    else:
        mb = _CNPJ_BARE.search(upper)
        if mb:
            features["cnpj"] = mb.group(1)

    m = _ISIN.search(upper)
    if m:
        features["isin"] = m.group(1)

    m = _BOND_INSTRUMENTS.search(upper)
    if m:
        features["instrument"] = m.group(1)

    m_gov = _GOV_BOND_TYPES.search(upper)
    if m_gov:
        bt = m_gov.group(1).replace("-", "")
        if bt == "NTNB":
            bt = "NTN-B"
        elif bt == "NTNF":
            bt = "NTN-F"
        features["bond_type"] = bt
        # selic_code only when paired with a bond_type (otherwise any 6-digit
        # number — strike, rate, ID — would be misread as selic).
        m = _NUMERIC_6.search(upper)
        if m:
            features["selic_code"] = m.group(1)

    # Fund code in parens: (52532)
    m = _FUND_CODE_PARENS.search(description)
    if m:
        features["fund_code"] = m.group(1)

    # Ticker — first standalone 3-6 letter + 1-2 digit token. Skip if it's
    # actually a noise word that happens to look ticker-shaped (rare).
    m = _TICKER_LIKE.search(upper)
    if m and m.group(1) not in _NOISE_TOKENS:
        features["ticker"] = m.group(1)

    # Internal code: long alnum string mixing letters and digits.
    # Examples: 24D3691425, LF0020003KK, BRBMEFD1I6J9
    for cm in _INTERNAL_CODE.finditer(upper):
        cand = cm.group(1)
        if len(cand) < 8:
            continue
        has_letter = any(c.isalpha() for c in cand)
        has_digit  = any(c.isdigit() for c in cand)
        if has_letter and has_digit and cand not in _NOISE_TOKENS:
            features["internal_code"] = cand
            break

    # Indexer
    m = _INDEXERS.search(upper)
    if m:
        idx = m.group(1).upper()
        if idx in ("IPC-A", "IPCADP"):
            idx = "IPCA"
        features["indexer"] = idx

    # Maturity / expiry — security_matcher._parse_date knows every format.
    parsed = security_matcher._parse_date(description, prefer_mdy=prefer_mdy)
    if parsed:
        features["maturity_date"] = parsed
        # Was a specific day named in the text, or only month+year?
        # Patterns that include an explicit day: YYYY-MM-DD, DD/MM/YYYY,
        # DD/MMM/YYYY, MM/DD/YY. The MMM/YYYY shortcut (e.g. "SET/2029")
        # has _parse_date default the day to 01, which would otherwise
        # trigger a false day-mismatch penalty in the scorer — this flag
        # tells the scorer to skip the day check when the day was a default.
        features["maturity_day_specified"] = bool(
            re.search(r"\d{4}-\d{2}-\d{2}", description) or
            re.search(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b", description) or
            re.search(r"\b\d{1,2}/[A-Za-z]{3}/\d{4}\b", description)
        )

    # Rate (informational; not scored, but we set it for completeness).
    m = _RATE.search(upper)
    if m:
        features["rate"] = m.group(1).replace(",", ".")

    # Name = the longest meaningful chunks of text after stripping noise.
    name_tokens = []
    for tok in re.split(r"[\s\-_/,\.\|\(\)\[\]\*]+", upper):
        if len(tok) >= 3 and tok not in _NOISE_TOKENS and not tok.isdigit():
            # Skip the things we've already captured as structured features
            if tok == features.get("ticker"):
                continue
            if tok == features.get("internal_code"):
                continue
            if tok == features.get("instrument") or tok == features.get("bond_type"):
                continue
            name_tokens.append(tok)
    if name_tokens:
        features["name"] = " ".join(name_tokens[:8])

    return features


def _compressed(s):
    """'ABC def-123/MM' → 'ABCDEF123MM'. Used for substring matching that
    survives shifts in spacing/punctuation between description and security
    name. (E.g., description 'OIKOS C FIC FIMM' vs beehusName 'Oikos C
    FICFI MM' both compress to 'OIKOSCFICFIMM'.)"""
    if not s:
        return ""
    return _NON_ALNUM_RE.sub("", str(s).upper())


_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]")


def _name_substring_bonus(desc_compressed, sec):
    """Return (bonus, reason) if the security's compressed beehusName/mainId
    appears as a substring of the description (also compressed).

    The bonus is sized so a clean substring hit beats a raw _score_candidate
    name-token overlap (≤ 15) AND beats a maturity-only match (25), but
    doesn't outright dethrone an id-style match (50). It's additive, so a
    real id match plus a compressed-name match gets both bonuses.
    """
    # `_name_c` / `_main_c` are the compressed forms of the candidate's
    # constant beehusName / mainId, memoised on the (shared, long-lived) cache
    # dict. _compressed runs an uppercase + regex-sub; doing it per
    # (transaction, candidate) pair across a 16k-security L3 sweep was pure
    # repeated work. Compute once per security, reuse for every transaction.
    name_c = sec.get("_name_c")
    if name_c is None:
        name_c = _compressed(sec.get("beehusName", "") or "")
        sec["_name_c"] = name_c
    # Require enough chars that a coincidental substring match is unlikely.
    # 6-char minimum filters out generic suffixes like "FIA" or "ETF".
    if len(name_c) >= 6 and name_c in desc_compressed:
        return 35, f"name~={name_c[:24]}"
    main_c = sec.get("_main_c")
    if main_c is None:
        main_c = _compressed(sec.get("mainId", "") or "")
        sec["_main_c"] = main_c
    if len(main_c) >= 8 and main_c in desc_compressed:
        return 25, f"mainId~={main_c[:24]}"
    return 0, ""


# ── Wallet-scoped candidate pool ──────────────────────────────────────────────

class _WalletCandidatePool:
    """Lazily-loaded per-wallet sets of held security ids.

    Cache keys are ``(wallet_id, ref_date)`` rather than ``wallet_id`` alone:
    the pool is shared via the singleton classifier (one instance for the
    process), and concurrent requests can target the same wallet at
    different reference dates. Keying by both fields lets parallel requests
    co-exist without overwriting each other's cached candidate set."""

    def __init__(self, db):
        self._db = db
        self._l1_by_wallet = {}
        self._l2_by_wallet = {}

    def _load_l1(self, wallet_id, on_or_before):
        if not wallet_id:
            return set()
        cursor = self._db.processedPosition.find(
            {"walletId": wallet_id, "positionDate": {"$lte": on_or_before},
             "trashed": {"$ne": True}},
            {"securities.securityId": 1, "positionDate": 1},
        ).sort("positionDate", -1).limit(1)
        for doc in cursor:
            return {str(s.get("securityId", ""))
                    for s in (doc.get("securities") or [])
                    if s.get("securityId")}
        return set()

    def _load_l2(self, wallet_id, on_or_before):
        """Union of securityIds in T-1 ∪ T-2 (positions just before the most
        recent on/before `on_or_before`). Excludes T itself."""
        if not wallet_id:
            return set()
        dates = [d.get("positionDate") for d in self._db.processedPosition.find(
            {"walletId": wallet_id, "positionDate": {"$lte": on_or_before},
             "trashed": {"$ne": True}},
            {"positionDate": 1},
        ).sort("positionDate", -1).limit(3)]
        if len(dates) <= 1:
            return set()
        prior = dates[1:]   # T-1, T-2
        ids = set()
        for doc in self._db.processedPosition.find(
            {"walletId": wallet_id, "positionDate": {"$in": prior},
             "trashed": {"$ne": True}},
            {"securities.securityId": 1},
        ):
            for s in (doc.get("securities") or []):
                sid = str(s.get("securityId", ""))
                if sid:
                    ids.add(sid)
        return ids

    def get_l1(self, wallet_id, ref_date):
        key = (wallet_id, ref_date)
        if key not in self._l1_by_wallet:
            self._l1_by_wallet[key] = self._load_l1(wallet_id, ref_date)
        return self._l1_by_wallet[key]

    def get_l2(self, wallet_id, ref_date):
        key = (wallet_id, ref_date)
        if key not in self._l2_by_wallet:
            self._l2_by_wallet[key] = self._load_l2(wallet_id, ref_date)
        return self._l2_by_wallet[key]


# ── SecurityCache helpers (resolve sec ids to full docs) ──────────────────────

class _SecLookup:
    """Index a SecurityCache by `_id` once so per-id resolution is O(1)."""

    def __init__(self, cache):
        self._cache = cache
        self._by_id = None
        self._indexed_date = None

    def _ensure_index(self):
        # Rebuild the {_id: sec} map only when the underlying SecurityCache was
        # (re)loaded since we last indexed — not on every request. The cache
        # singleton reloads at most once per day, so indexing ~16k securities
        # on each reset_request_cache was wasted O(16k) work per request.
        if self._by_id is None or self._indexed_date != self._cache.loaded_date:
            self._by_id = {s["_id"]: s for s in self._cache._securities}
            self._indexed_date = self._cache.loaded_date

    def get(self, sid):
        self._ensure_index()
        return self._by_id.get(sid)

    def get_many(self, ids):
        self._ensure_index()
        return [self._by_id[sid] for sid in ids if sid in self._by_id]

    def all(self):
        return self._cache._securities


# ── amountDifference + offset tie-breaker ─────────────────────────────────────

_DATE_TOLERANCE_DAYS = 3


def _to_date(yyyy_mm_dd):
    if not yyyy_mm_dd or len(str(yyyy_mm_dd)) < 10:
        return None
    try:
        return datetime.strptime(str(yyyy_mm_dd)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _fetch_offsets(db, security_ids):
    """{securityId: {sub_offset, red_offset}} from db.securities."""
    if not security_ids:
        return {}
    oid_query, str_query = [], []
    for s in security_ids:
        str_query.append(s)
        try:
            oid_query.append(ObjectId(s))
        except (InvalidId, TypeError):
            pass
    out = {}
    for sec in db.securities.find(
        {"_id": {"$in": oid_query + str_query}},
        {"subscriptionSettlementDays": 1, "subscriptionNavDays": 1,
         "redemptionSettlementDays": 1, "redemptionNavDays": 1},
    ):
        sid = str(sec["_id"])
        out[sid] = {
            "sub_offset": (sec.get("subscriptionSettlementDays") or 0)
                          - (sec.get("subscriptionNavDays") or 0),
            "red_offset": (sec.get("redemptionSettlementDays") or 0)
                          - (sec.get("redemptionNavDays") or 0),
        }
    return out


def _amountdiff_tiebreak(db, candidate_ids, wallet_id, liquidation_date, balance):
    target = _to_date(liquidation_date)
    if not target or not wallet_id or not candidate_ids:
        return None, "no_target_or_wallet_or_candidates"

    horizon_lo = (target - timedelta(days=45)).isoformat()
    horizon_hi = (target + timedelta(days=10)).isoformat()
    positions_by_date = {}
    for doc in db.processedPosition.find(
        {"walletId": wallet_id, "trashed": {"$ne": True},
         "positionDate": {"$gte": horizon_lo, "$lte": horizon_hi}},
        {"positionDate": 1, "securities.securityId": 1, "securities.quantity": 1},
    ).sort("positionDate", 1):
        d = str(doc.get("positionDate", ""))[:10]
        if not d:
            continue
        positions_by_date[d] = {
            str(s.get("securityId", "")): (s.get("quantity") or 0)
            for s in (doc.get("securities") or [])
        }
    sorted_dates = sorted(positions_by_date.keys())
    if len(sorted_dates) < 2:
        return None, "need_two_positions"

    offsets = _fetch_offsets(db, candidate_ids)
    best = None
    for sid in candidate_ids:
        off = offsets.get(sid, {"sub_offset": 0, "red_offset": 0})
        for i in range(1, len(sorted_dates)):
            d_prev, d_curr = sorted_dates[i - 1], sorted_dates[i]
            qty_prev = positions_by_date[d_prev].get(sid, 0)
            qty_curr = positions_by_date[d_curr].get(sid, 0)
            delta = (qty_curr or 0) - (qty_prev or 0)
            if not delta:
                continue
            offset = off["sub_offset"] if delta > 0 else off["red_offset"]
            curr_d = _to_date(d_curr)
            if not curr_d:
                continue
            expected_settlement = curr_d + timedelta(days=offset)
            if balance is not None and balance != 0:
                # Buy → cash leaves (balance < 0); sell → cash arrives (> 0).
                if (delta > 0 and balance > 0) or (delta < 0 and balance < 0):
                    continue
            day_gap = abs((expected_settlement - target).days)
            if day_gap > _DATE_TOLERANCE_DAYS:
                continue
            if best is None or day_gap < best[0]:
                best = (day_gap, sid, {
                    "navDate":            d_curr,
                    "amountDifference":   delta,
                    "offset":             offset,
                    "expectedSettlement": expected_settlement.isoformat(),
                    "actualSettlement":   target.isoformat(),
                    "dayGap":             day_gap,
                })
    if best:
        return best[1], best[2]
    return None, "no_candidate_matches_amountdiff"


# ── Classifier ────────────────────────────────────────────────────────────────

# Score thresholds. matcher's _score_candidate is on a ~0–100 scale with two
# hard rules: an exact unique identifier (ticker/taxId/isIn/selicCode) → 100
# (perfect, ignores date), and a disagreeing maturity date → 0 (rejected, then
# dropped by the `score > 0` filter below). Non-exact matches sum partial
# signals: 50 ≈ a single id-style/substring match, 75+ ≈ id + name + maturity.
HIGH_THRESHOLD       = 50      # top-1 ≥ this → confident, freeze the level
FALLBACK_THRESHOLD   = 25      # top-1 < this from L1+L2 → fall to L3
AMBIGUOUS_RATIO      = 0.85
L3_PREFILTER_TOKLEN  = 4       # token length to use as a cheap haystack filter
L3_MAX_CANDIDATES    = 200     # cap on L3 sweep; we sort + truncate after


class TransactionSecurityClassifier:
    """
    Suggest a `securityId` for a transaction description without trusting any
    securityType prediction.

    Usage:
        clf = TransactionSecurityClassifier(db)
        result = clf.predict(
            description="Pgto Juros 24D3691425 | CRI TRUE SECURITIZADORA - SET/2029",
            wallet_id="5f...",
            liquidation_date="2026-05-06",
            transaction_type="coupon",
            balance=-1234.56,
        )
        # result = {securityId, beehusName, mainId, maturityDate,
        #           confidence, ambiguous, source, score, reasons,
        #           alternatives, tiebreak}
    """

    def __init__(self, db, sec_cache=None):
        self._db = db
        self._cache = sec_cache or security_matcher.get_cache()
        self._lookup = _SecLookup(self._cache)
        self._pool = _WalletCandidatePool(db)

    def reset_request_cache(self):
        # Only the wallet candidate pool is request-scoped. `_lookup` is keyed
        # off the SecurityCache singleton and self-invalidates against its
        # loaded_date, so it must NOT be discarded here — recreating it forced
        # a full O(16k) {_id: sec} rebuild on every request.
        self._pool = _WalletCandidatePool(self._db)

    # ── Public API ────────────────────────────────────────────────────────

    def predict(
        self,
        description: str,
        wallet_id: Optional[str] = None,
        liquidation_date: Optional[str] = None,
        transaction_type: Optional[str] = None,
        balance: Optional[float] = None,
    ) -> dict:
        if not description:
            return _empty_result("empty_description")

        try:
            self._cache.ensure_loaded(self._db)
        except Exception as exc:
            log.warning("SecurityCache load failed: %s", exc)

        # American date format inference: a USD wallet signals that an
        # ambiguous numeric date (12/01/2034) in the description should be
        # read month-first (MM/DD). Unambiguous dates are unaffected.
        prefer_mdy = False
        if wallet_id:
            try:
                from db import get_wallet_currencies
                cur = (get_wallet_currencies().get(str(wallet_id)) or "").upper()
                prefer_mdy = (cur == "USD")
            except Exception:
                prefer_mdy = False

        # Generic, conservative features (no type gate). Sets only fields
        # that are unambiguous in the description, so we don't poison the
        # matcher's if/elif id-match chain.
        features = _extract_generic_safe(description, prefer_mdy=prefer_mdy)
        # Pre-compute once for the compressed-name substring bonus (used by
        # _score_pool/_score_l3 to catch cases where the description and the
        # security's beehusName have the same letters but different spacing).
        desc_compressed = _compressed(description)

        ref_date = liquidation_date or _date.today().isoformat()

        # ── L1: wallet's current holdings ─────────────────────────────────
        # We tag L1 candidates with source="level1" (and L2 with "level2"
        # below) so the security-edit modal can group alternatives by their
        # provenance — L1 (carteira hoje), L2 (carteira T-1/T-2), L3
        # (cadastro completo) — instead of mixing them into a single
        # "processedPosition" bucket. The cascade itself is unchanged.
        l1_ids = self._pool.get_l1(wallet_id, ref_date) if wallet_id else set()
        l1_docs = self._lookup.get_many(l1_ids)
        l1_scored = self._score_pool(l1_docs, features, desc_compressed,
                                     source="level1")

        top_score = l1_scored[0]["score"] if l1_scored else 0
        used_source = "level1" if l1_scored else "level3"  # default until proven

        # ── L2: T-1, T-2 holdings (additive when L1 is weak) ──────────────
        l2_scored = []
        merged = list(l1_scored)
        if top_score < HIGH_THRESHOLD:
            l2_ids = (self._pool.get_l2(wallet_id, ref_date) - l1_ids) if wallet_id else set()
            l2_docs = self._lookup.get_many(l2_ids)
            l2_scored = self._score_pool(l2_docs, features, desc_compressed,
                                         source="level2")
            merged = sorted(l1_scored + l2_scored, key=lambda x: -x["score"])
            top_score = merged[0]["score"] if merged else 0
            if l2_scored and (not l1_scored or l2_scored[0]["score"] >= (l1_scored[0]["score"] if l1_scored else 0)):
                used_source = "level2"

        # ── L3: full SecurityCache (only when wallet-scoped is weak) ──────
        if top_score < FALLBACK_THRESHOLD:
            already = {d["_id"] for d in (l1_docs + l2_docs) if "_id" in d}
            l3_scored = self._score_l3(features, desc_compressed, exclude_ids=already)
            if l3_scored and (not merged or l3_scored[0]["score"] > merged[0]["score"]):
                used_source = "level3"
            merged = sorted(merged + l3_scored, key=lambda x: -x["score"])

        if not merged or merged[0]["score"] <= 0:
            return _empty_result("no_match")

        top_alts = merged[:5]
        top1 = top_alts[0]
        top2 = top_alts[1] if len(top_alts) > 1 else None

        ambiguous = bool(
            top2 and top1["score"] > 0
            and top2["score"] / top1["score"] >= AMBIGUOUS_RATIO
        )

        # ── buySell tie-breaker (post-step) ───────────────────────────────
        # Operates on the top-3 by score (deeper alternatives are too weak to
        # be the right answer), but the modal still receives all 5 so the
        # operator has more visible options.
        tiebreak_detail = None
        if ambiguous and (transaction_type or "").lower() == "buysell" and wallet_id:
            tiebreak_pool = top_alts[:3]
            cand_ids = [c["doc"]["_id"] for c in tiebreak_pool]
            winner_id, detail = _amountdiff_tiebreak(
                self._db, cand_ids, wallet_id, liquidation_date, balance,
            )
            if winner_id:
                top_alts = [c for c in top_alts if c["doc"]["_id"] == winner_id] + \
                           [c for c in top_alts if c["doc"]["_id"] != winner_id]
                top1 = top_alts[0]
                ambiguous = False
                used_source = "amount_diff_tiebreaker"
                tiebreak_detail = detail

        confidence = max(0.0, min(1.0, top1["score"] / 100.0))
        if ambiguous:
            confidence = min(confidence, 0.65)

        return {
            "securityId":   top1["doc"]["_id"],
            "beehusName":   top1["doc"].get("beehusName", "") or "",
            "mainId":       top1["doc"].get("mainId", "") or "",
            "maturityDate": str(top1["doc"].get("maturityDate", ""))[:10] if top1["doc"].get("maturityDate") else "",
            "confidence":   round(confidence, 4),
            "ambiguous":    ambiguous,
            "source":       used_source,
            "score":        top1["score"],
            "reasons":      top1["reasons"],
            "alternatives": [_alt(c) for c in top_alts],
            "tiebreak":     tiebreak_detail,
            # Surface the merged feature dict so the UI can show what was
            # extracted from the description (debugging aid).
            "extracted":    features,
        }

    # ── Internals ─────────────────────────────────────────────────────────

    def _score_pool(self, docs, features, desc_compressed, source="level1"):
        """Tag each scored candidate with `source` so the security-edit modal
        can group alternatives by provenance: 'level1' (carteira em T),
        'level2' (carteira em T-1/T-2), 'collection' (cadastro completo,
        fallback L3). Callers MUST pass the correct level — the default of
        'level1' is just a safety net for direct call sites."""
        scored = []
        for doc in docs:
            sec_type = doc.get("securityType") or ""
            score, reasons = security_matcher._score_candidate(doc, features, sec_type)
            bonus, br = _name_substring_bonus(desc_compressed, doc)
            if bonus:
                score += bonus
                reasons = list(reasons) + [br]
            if score > 0:
                scored.append({"doc": doc, "score": score, "reasons": reasons,
                               "source": source})
        scored.sort(key=lambda x: -x["score"])
        return scored

    def _score_l3(self, features, desc_compressed, exclude_ids=None):
        """Sweep the entire SecurityCache. Cheap pre-filter: skip docs that
        share zero ≥4-char tokens with the description's most identifying
        features (mainId-style codes, ticker, name pieces). Returns top-10."""
        exclude_ids = exclude_ids or set()
        # Build a haystack-needle set from the most-identifying features.
        needles = set()
        for k in ("ticker", "cnpj", "isin", "selic_code", "cetip_code",
                  "internal_code", "external_code", "fund_code"):
            v = features.get(k)
            if v and len(str(v)) >= L3_PREFILTER_TOKLEN:
                needles.add(str(v).upper())
        # Also include long-enough name tokens so we don't miss name-only matches.
        for key in ("name", "issuer"):
            v = features.get(key, "") or ""
            for tok in re.split(r"[\s\-_/,\.\(\)]", v):
                if len(tok) >= L3_PREFILTER_TOKLEN:
                    needles.add(tok.upper())
        # Instrument / government-bond type (NTN-B, LFT, CDB, CRI…) and the
        # maturity year. Bonds are catalogued by type+date ("NTN-B Ago/2040"),
        # so a description led by generic words ("Compra de Tesouro Direto: …")
        # shares NO token with the bond's name — without these needles the
        # right bond is filtered out before scoring and only "Tesouro"-named
        # funds survive. The instrument/type and year pull the correct vintage
        # in; the date gate + exact-date bonus then rank it.
        for k in ("bond_type", "instrument"):
            v = (features.get(k) or "").upper()
            if len(v) >= 3:
                needles.add(v)
                nodash = v.replace("-", "")
                if len(nodash) >= 3 and nodash != v:
                    needles.add(nodash)  # catalog may store "NTNB" or "NTN-B"
        md = features.get("maturity_date") or features.get("expiry") or ""
        if len(md) >= 4 and md[:4].isdigit():
            needles.add(md[:4])  # maturity year, e.g. "2040"

        scored = []
        for sec in self._lookup.all():
            if sec["_id"] in exclude_ids:
                continue
            if needles:
                # `_hay` is the candidate's constant uppercased search blob,
                # memoised on the (shared, long-lived) cache dict. Rebuilding
                # it (6 str() coercions + concat + .upper()) for all ~16k
                # securities on EVERY transaction was the dominant cost of the
                # L3 sweep. Build once per security, reuse for every txn.
                hay = sec.get("_hay")
                if hay is None:
                    tax = str(sec.get("taxId", ""))
                    # Include the digit-stripped taxId too so a bare 14-digit
                    # CNPJ needle from the description matches a security whose
                    # taxId is stored punctuated (12.345.678/0001-90).
                    hay = (str(sec.get("mainId", "")) + " "
                           + str(sec.get("ticker", "")) + " "
                           + str(sec.get("beehusName", ""))[:80] + " "
                           + tax + " "
                           + security_matcher._digits_only(tax) + " "
                           + str(sec.get("isIn", "")) + " "
                           + str(sec.get("selicCode", ""))).upper()
                    sec["_hay"] = hay
                if not any(n in hay for n in needles):
                    continue
            sec_type = sec.get("securityType") or ""
            score, reasons = security_matcher._score_candidate(sec, features, sec_type)
            bonus, br = _name_substring_bonus(desc_compressed, sec)
            if bonus:
                score += bonus
                reasons = list(reasons) + [br]
            if score > 0:
                scored.append({"doc": sec, "score": score, "reasons": reasons,
                               "source": "collection"})
                if len(scored) >= L3_MAX_CANDIDATES:
                    break
        scored.sort(key=lambda x: -x["score"])
        return scored[:10]


def _alt(c):
    d = c["doc"]
    return {
        "securityId":   d["_id"],
        "beehusName":   d.get("beehusName", "") or "",
        "mainId":       d.get("mainId", "") or "",
        "maturityDate": str(d.get("maturityDate", ""))[:10] if d.get("maturityDate") else "",
        "score":        c["score"],
        "reasons":      c["reasons"],
        # Per-candidate score decomposition for the UI tooltip ("como o score
        # foi calculado"). Sums to `score`. See security_matcher._score_breakdown.
        "breakdown":    security_matcher._score_breakdown(c["reasons"], c["score"]),
        # 'level1' for L1 hits (carteira em T), 'level2' for L2 hits
        # (carteira em T-1/T-2), 'collection' for L3 fallback hits
        # (cadastro completo). Empty when the candidate came from a code
        # path that didn't tag it.
        "source":       c.get("source", ""),
    }


def _empty_result(reason):
    return {
        "securityId":   "",
        "beehusName":   "",
        "mainId":       "",
        "maturityDate": "",
        "confidence":   0.0,
        "ambiguous":    False,
        "source":       reason,
        "score":        0,
        "reasons":      [],
        "alternatives": [],
        "tiebreak":     None,
        "extracted":    {},
    }
