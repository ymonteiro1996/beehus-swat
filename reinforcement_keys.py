"""Shared helpers for the Identificar Transações reinforcement table.

Three responsibilities split out of `pages/beehus_console.py` so they can
be reused by the build/migration scripts under `scripts/`:

1. **`mask_variable_tokens`** — collapse per-transaction identifiers
   (operation codes, CDB issuance codes) into stable placeholders so a
   single rule can match many concrete descriptions that only differ in
   those tokens. Only tokens that are *clearly* per-transaction variable
   are masked; dates and rate values stay intact because they're often
   the discriminator between distinct securities. This also strips the
   leading transaction-type counter (`RENDIMENTO_6` → `RENDIMENTO`) so a
   security's many numbered events collapse to one rule.

2. **`normalize_reinforcement_key`** — the canonical normalisation
   applied before storing or looking up a reinforcement key.
   `transaction_type_classifier.normalize` (uppercase / strip accents /
   collapse spaces) runs first, then `mask_variable_tokens` on top.

3. **`apply_type_overrides`** — codified fixes for descriptions where
   the historical classification is provably wrong (FII distributions
   labelled as `coupon` instead of `dividend` in legacy data, for
   instance). Applied at write-time by the build / migration scripts so
   the file ends up internally consistent and `_lookup_reinforcement`
   doesn't need runtime patches.

4. **`strip_negating_prefix`** — detects the "tax/fee on something else"
   prefixes (`IR -`, `IRRF`, `IOF`, etc.) and returns the inner
   description so the lookup can recover the underlying security match.
   The caller is responsible for forcing the type to `taxes` when this
   strip succeeds.
"""
import re

from transaction_type_classifier import normalize as _base_normalize


# ── Token masking ────────────────────────────────────────────────────────────

# Transaction operation codes: 2-digit year + uppercase letter + 7-8 digits
# (e.g. `25F8630698`, `26C4374746`, `24G1967273`). These vary per-event
# inside the same contract, so consolidating them is a big win.
_OPCODE_RE = re.compile(r"\b\d{2}[A-Z]\d{7,8}\b")
# CDB issuance codes: `CDB` + 4 digits + 4 uppercase letters (e.g.
# `CDB2194QTJV`, `CDB2205HILD`). Same rationale.
_CDB_CODE_RE = re.compile(r"\bCDB\d{4}[A-Z]{4}\b")
# Reference-period tokens that follow the literal `REF.` (e.g. billing
# fees: `FEE FIXO - COBRANCA REF. ABR26`, `... REF. MAR26`). The
# 3-letter + 2-digit shape is variable per month while the surrounding
# description stays the same, so masking it consolidates monthly
# billings into one rule. We restrict to the post-`REF.` context to
# avoid colliding unrelated short codes (e.g. ticker-class suffixes
# that happen to match the same shape).
_REF_PERIOD_RE = re.compile(r"\bREF\.\s*[A-Z]{3}\d{2}\b")
# Leading transaction-type counter: a type word (or words, e.g. `VENDA
# TOTAL`) immediately followed by `_<digits>` at the very start of the
# description (e.g. `RENDIMENTO_6 - ...`, `AMORTIZACAO_5 - ...`, `CUPOM_1
# - ...`). Brokers emit one numbered description per recurring event
# (coupon #5, coupon #6, ...) so without this collapse a single security
# accreted dozens of near-identical rules. Anchored at `^` and limited to
# a letters/spaces prefix so it can NEVER touch a security-id token like
# `CETIP_21I0183215` or `NTNB_07032006_15052035` that appears after the
# first " - " separator. Runs on the already-normalised (UPPER, ASCII)
# text, hence the `[A-Z ]` character class.
_COUNTER_PREFIX_RE = re.compile(r"^([A-Z][A-Z ]*?)_\d+(?=[\s\-]|$)")
# Standalone account / product codes embedded in fund descriptions: a 4-8
# digit number that the broker tacks on per-account but that does NOT
# identify the security (the fund name already does). Two shapes are
# masked: bracketed/parenthesised (`[54582]`, `(53632)`) anywhere, and a
# trailing code optionally wrapped in dashes (`... FI 54582`, `... FI -
# 54582 -`). Guards keep the real discriminators intact:
#   * `(?<![\w/.])` before the trailing form rejects CNPJ fragments
#     (`.../0001-97`) and date components (`/2026`);
#   * the `\b` boundaries reject ISINs (`IE0030624948`) and maturity
#     tokens (`NTNB_07032006_15052035`) whose digits sit against a letter
#     or underscore;
#   * mid-string routing blocks (`... CTA 31359001 - ...`) are left alone
#     because only the trailing position is masked there.
# Verified against the live rule set: zero cross-security collisions.
_ACCT_BRACKET_RE = re.compile(r"[\[(]\s*\d{4,8}\s*[\])]")
_ACCT_TRAILING_RE = re.compile(r"(?<![\w/.])\s?-?\s*\b\d{4,8}\b\s*-?\s*$")


def mask_variable_tokens(text):
    """Replace per-transaction codes with stable placeholders.

    Intentionally conservative — only masks patterns observed to vary
    *within* a single security across many rows. Dates (`JUN/2034`),
    rates (`11.5`), bank routing numbers (`BCO 17 AGE 1 CTA 1032 4`)
    are left untouched because they often discriminate between
    distinct securities.

    The leading transaction-type counter (`RENDIMENTO_6 → RENDIMENTO`) is
    also stripped so the many numbered events of one security collapse to
    a single rule.
    """
    if not text:
        return text
    text = _OPCODE_RE.sub("<OPCODE>", text)
    text = _CDB_CODE_RE.sub("CDB<CODE>", text)
    text = _REF_PERIOD_RE.sub("REF. <PERIOD>", text)
    text = _COUNTER_PREFIX_RE.sub(lambda m: m.group(1), text)
    text = _ACCT_BRACKET_RE.sub(" <ACCT>", text)
    text = _ACCT_TRAILING_RE.sub(" <ACCT>", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return text


# ── Canonical key ────────────────────────────────────────────────────────────

def normalize_reinforcement_key(desc):
    """Build the lookup key used by the reinforcement table.

    Pipeline:
        raw description
        → `transaction_type_classifier.normalize` (mojibake fix, strip
          accents, UPPER, collapse spaces)
        → `mask_variable_tokens` (operation codes, CDB codes)

    The two steps live in different modules because the type
    classifier's `normalize` is also consumed by the rule cascade / ML
    pipeline where masking would harm precision.
    """
    if desc is None:
        return ""
    try:
        normalized = _base_normalize(desc)
    except Exception:
        # Defensive: fall back to a minimal normalisation so a misbehaving
        # classifier import doesn't break the reinforcement save path.
        normalized = str(desc).strip().upper()
    return mask_variable_tokens(normalized)


# ── Type overrides (apply at write-time) ─────────────────────────────────────

# Each entry: predicate(normalized_key) → forced beehusTransactionType.
# Used by the build/migration scripts to overwrite historical labels
# that are demonstrably wrong.
#
# DESIGN NOTE — be very conservative about adding entries here.
# A previous "FII rendimento → dividend" rule looked correct in
# theory (FII distributions are dividends in most accounting
# conventions) but the May/2026 test against entity 67cf6a5c…0505
# showed the production data uses `coupon` for those rows
# *consistently*. Forcing the type to `dividend` broke ~100 matches
# that were already right. Lesson: don't introduce overrides based on
# external convention — only when the production data itself
# disagrees with the historical label by overwhelming majority and
# you have ground truth that contradicts the stored value.
_TYPE_OVERRIDES = []  # No overrides active; intentionally empty.


def apply_type_overrides(normalized_key, current_type):
    """Return the corrected `beehusTransactionType` for this key.

    Falls through to `current_type` when no override fires. Callers
    that want to know whether an override fired (e.g. for logging the
    fix) can compare the return value to `current_type`.
    """
    for predicate, forced_type, _reason in _TYPE_OVERRIDES:
        try:
            if predicate(normalized_key):
                return forced_type
        except Exception:
            continue
    return current_type


def type_override_reason(normalized_key):
    """Human-readable reason string for the override that would fire on
    this key (or `None` when no override applies)."""
    for predicate, _forced_type, reason in _TYPE_OVERRIDES:
        try:
            if predicate(normalized_key):
                return reason
        except Exception:
            continue
    return None


# ── Negating prefixes (lookup-time) ──────────────────────────────────────────

# Order matters: more specific (longer) prefixes first so `DEBITO CBLC
# IRRF` isn't shadowed by the shorter `IRRF` pattern. Patterns are run
# against the *normalised* key (UPPER, no accents, no masking yet —
# masking happens after stripping).
_NEGATING_TAX_PATTERNS = [
    re.compile(r"^DEBITO\s+CBLC\s+IRRF\b\s*S?/?\s*[-]?\s*"),
    re.compile(r"^DEBITO\s+IOF\b\s*[-]?\s*"),
    re.compile(r"^IRRF\b\s*S?/?\s*[-]?\s*"),
    re.compile(r"^IOF\b\s*S?/?\s*[-]?\s*"),
    re.compile(r"^IR\b\s*[-]\s*"),  # standalone "IR -" (avoid matching IRRF)
]


def strip_negating_prefix(normalized_key):
    """If the description starts with a tax/fee prefix, return the
    inner key so the caller can look it up against the security
    reinforcements. Returns `None` when no negating prefix matches.

    The returned key has the prefix removed and gets re-masked so a
    masking that depended on the prefix being absent still produces a
    sensible key (in practice the masks don't touch the prefix region,
    but applying mask once more is cheap and future-proof).
    """
    if not normalized_key:
        return None
    for pattern in _NEGATING_TAX_PATTERNS:
        m = pattern.match(normalized_key)
        if m:
            inner = normalized_key[m.end():].strip()
            if not inner:
                return None
            return mask_variable_tokens(inner)
    return None
