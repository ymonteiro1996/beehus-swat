"""Tier 2 deterministic field-derivation rules for cadastro de ativos.

Source of truth, in precedence order (per `reference/beehusName-regra-final.md`
§0, the consolidated spec for this exact feature — "criação de ativos sem
match no Mapeamento" — dated 2026-07-03):
  1. `reference/beehusName-regras.md` — primary source.
  2. `reference/REGRAS_BEEHUSNAME.md` / `REGRAS_CAMPOS_CADASTRO.md` — gap-fill
     only (technical fields: indexer/yield/indexerPercentual, synthetic
     ticker, settlement defaults, known API errors; types (1) doesn't cover).
  3. The `/securities-cadastro` AI skill's `SKILL.md` — used only where
     neither (1) nor (2) says anything (e.g. it was the origin of the "sem
     taxa unificada" family rule, confirmed live by the user and by (1)/(3)'s
     own §0 conflict table — supersedes the older, taxed formula
     REGRAS_BEEHUSNAME.md §4.1/§4.3 still documents literally).
No LLM/web-search at runtime in any case — this module is a pure port.

Issuer (bank) and devedor (CRI/CRA/Debênture underlying obligor) name
canonicalization uses the curated tables ported from
`templates/controlpanel.html` (`_BANK_CANONICALS`/`_DEVEDOR_CANONICALS`,
~line 3431-3701) into `beehusname_canonicals.py` via `scripts/port_canonicals.py`
— per beehusName-regra-final.md §3, that JS is the only surviving copy of this
curated data, so re-run the port script after editing the JS tables (or
vice-versa) to keep both in sync.

Covers only asset families with NO public B3/CETIP/Anbima code AS THEIR
PRIMARY path (so `beehus_api.onboarding` can't resolve them) — CDB/LCA/LCI/
LCD/CD/CCB/LC, LF/LF-sub, LIG, NP, Compromissada — plus CRI/CRA/Debênture as a
Tier 2 FALLBACK when the Onboarding API doesn't resolve the code (both fully
spec'd in beehusName-regra-final.md §4.2-4.4, sharing the same formula/
algorithm). Additional families (COE, Swap, depósitos, produtos estruturados,
Bond USD, título público BR, fundo offshore) are intentionally NOT
implemented yet — v1 ships the highest-volume families first and expands by
observed Pendente volume (see `asset_registration.py`'s docstring), not by
guessing priority.

Never guesses: every builder raises `RegistrationPending` (with a specific,
human-readable reason) instead of inventing a value it can't confirm from the
raw text, mirroring the skill's "golden rule".
"""
import re
import unicodedata

from beehusname_canonicals import BANK_CANONICALS, DEVEDOR_CANONICALS

# ── Golden rule ───────────────────────────────────────────────────────────────


class RegistrationPending(Exception):
    """Raised when a required field can't be confirmed from the raw text.

    Never caught to fall back to a guess — callers surface `reason` to the
    human (Tier 3 of the pipeline)."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


# ── REGRAS_BEEHUSNAME.md §1.1/§1.3 — months and title-casing ─────────────────

_MONTHS_PT = {1: "Jan", 2: "Fev", 3: "Mar", 4: "Abr", 5: "Mai", 6: "Jun",
              7: "Jul", 8: "Ago", 9: "Set", 10: "Out", 11: "Nov", 12: "Dez"}
_MONTHS_EN = {1: "Jan", 2: "Feb", 3: "Mar", 4: "Apr", 5: "May", 6: "Jun",
              7: "Jul", 8: "Aug", 9: "Sep", 10: "Oct", 11: "Nov", 12: "Dec"}

# beehusName-regra-final.md §2 (primary — beehusName-regras.md §1, no conflict,
# just more entries here per the "Complemento" note in §2 item 1).
_ACRONYMS = {
    "fidc", "fic", "fii", "fip", "ficfip", "fim", "fif", "etf", "bdr", "ucits",
    "cdb", "cri", "cra", "lci", "lca", "lcd", "lf", "deb", "coe", "ltn", "lft",
    "ntn-b", "ntn-f", "cdca", "ccb", "cce",
    "rl", "rf", "cp", "ie", "mm", "iq", "fi",
    "xp", "btg", "cef", "bb", "bndes", "anbima",
    "cdi", "ipca", "igpm", "selic", "di",
    "msci", "acwi", "ssac", "tips", "s&p", "nasdaq",
    "jgp", "man", "map", "maxp", "ms", "cs", "glg", "spy", "mchi", "espo",
    "1x1", "lfsn",
    "s.a.", "s/a",
}
_PREPOSITIONS = {"de", "do", "da", "dos", "das", "e", "a", "o", "em", "com", "para", "no", "na", "por"}
_ROMAN_OR_CLASS = {"i", "ii", "iii", "iv", "v", "vi", "a", "b", "c", "d"}


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", text)
                  if unicodedata.category(c) != "Mn")


def title_br(text: str) -> str:
    """beehusName-regra-final.md §2 — word-by-word capitalization: known
    acronyms kept upper, prepositions kept lower (except the first word),
    Roman numerals (I-VI) and isolated series letters (A-D) kept upper,
    mixed-case and digit-leading words preserved, hyphenated words split."""
    words = (text or "").split()
    out = []
    for i, w in enumerate(words):
        lw = w.lower()
        if lw in _ACRONYMS or lw in _ROMAN_OR_CLASS:
            out.append(w.upper())
        elif lw in _PREPOSITIONS and i > 0:
            out.append(lw)
        elif any(c.islower() for c in w) and any(c.isupper() for c in w):
            out.append(w)  # already mixed-case (e.g. "JPMorgan") — preserve
        elif w and w[0].isdigit():
            out.append(w)
        elif "-" in w:
            out.append("-".join(title_br(part) for part in w.split("-")))
        else:
            out.append(w.capitalize())
    return " ".join(out)


# ── beehusName-regra-final.md §3 — emissor/devedor canonicalization ─────────
# Curated de-para lookup, case-insensitive substring match, first pattern
# match wins (tables are pre-ordered by specificity). `_BANK_CANONICALS` for
# the issuing bank (CDB/LCA/LCI/LCD/CD/CCB/LC/LF/LIG); `_DEVEDOR_CANONICALS`
# for the underlying obligor of CRI/CRA/Debênture.

def _lookup_canonical(raw_emissor: str, table):
    if not raw_emissor:
        return None
    up = raw_emissor.upper().strip()
    for canonical, patterns in table:
        for pat in patterns:
            if pat in up:
                return canonical
    return None


def get_short_emissor(raw_emissor: str) -> str:
    """Canonical bank name, or the title_br'd fallback (strip 'Banco '/'
    S.A.'/' S/A' then title-case) when not in `_BANK_CANONICALS`."""
    c = _lookup_canonical(raw_emissor, BANK_CANONICALS)
    if c:
        return c
    s = (raw_emissor or "").strip()
    s = re.sub(r"^BANCO\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+S\.?A\.?\s*$", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+S/A\s*$", "", s, flags=re.IGNORECASE)
    return title_br(s.strip())


def get_devedor_canonical(raw_emissor: str) -> str:
    """Canonical devedor/lastro name (CRI/CRA/Debênture), or the title_br'd
    raw value when not in `_DEVEDOR_CANONICALS`."""
    c = _lookup_canonical(raw_emissor, DEVEDOR_CANONICALS)
    if c:
        return c
    return title_br(raw_emissor or "")


def format_date_br(iso_date: str, day_known: bool = True) -> str:
    y, m, d = (int(x) for x in iso_date.split("-"))
    return f"{d:02d}/{_MONTHS_PT[m]}/{y}" if day_known else f"{_MONTHS_PT[m]}/{y}"


def format_date_en(iso_date: str) -> str:
    y, m, d = (int(x) for x in iso_date.split("-"))
    return f"{d:02d}/{_MONTHS_EN[m]}/{y}"


# ── Shared renda-fixa-BR settlement defaults ─────────────────────────────────
# REGRAS_CAMPOS_CADASTRO.md §4.2 / §4.5: all four fields are `0` for this
# family (no special-cased fund/offshore settlement window applies here).

def _rf_br_settlement() -> dict:
    return {
        "subscriptionSettlementDays": 0,
        "subscriptionNAVDays": 0,
        "redemptionNAVDays": 0,
        "redemptionSettlementDays": 0,
    }


def _synthetic_ticker(instrument: str, issuer_name: str, indexer, maturity_iso: str) -> str:
    """Fallback ticker — last resort, only when no real CETIP/internal/ISIN
    code was extracted from the raw text. Per the user's live correction:
    carries the indexer but NOT the rate value (the "sem taxa" convention
    applies to the ticker too, not just the beehusName). Uses full DDMMYYYY
    for uniqueness across issuer/maturity collisions."""
    issuer_compact = re.sub(r"[^A-Z0-9]", "", _strip_accents(issuer_name or "").upper())
    y, m, d = maturity_iso.split("-")
    return f"{instrument}{issuer_compact}{indexer or ''}{d}{m}{y}"


# ── Rate/indexer mode detection (cadastro-specific — not needed by the matcher) ─
# REGRAS_BEEHUSNAME.md §4.1/§5: distinguishes "% do CDI" (multiplicative, no
# "+") from "CDI+spread"/"IPCA+spread" (additive) from "Pré" (flat rate).

def _detect_rate_mode(raw_text: str, indexer: str | None):
    """Returns `("pct_indexer"|"spread"|"pre", value: float)` or `(None, None)`
    when the modality can't be confirmed from `raw_text`."""
    t = raw_text.upper()
    if indexer == "PRE" or not indexer:
        # Sem indexador nenhum detectado (nem CDI, nem IPCA) + uma taxa
        # numérica presente é o padrão "pré-fixado implícito" da renda fixa
        # BR — ex. "12,80% a.a." nunca diz a palavra "Pré" explicitamente.
        # extract_features só marca indexer="PRE" quando o texto tem "Pré"/
        # "Prefixado"/"Fixed" literal, então sem este fallback todo pré-
        # fixado "silencioso" cairia em Pendente por engano.
        m = re.search(r"(\d+(?:[,.]\d+)?)\s*%", t)
        return ("pre", float(m.group(1).replace(",", "."))) if m else (None, None)
    if indexer in ("CDI", "IPCA"):
        m_spread = re.search(rf"{indexer}\s*\+\s*(\d+(?:[,.]\d+)?)\s*%", t)
        if m_spread:
            return ("spread", float(m_spread.group(1).replace(",", ".")))
        if indexer == "CDI":
            m_pct = re.search(r"(\d+(?:[,.]\d+)?)\s*%", t)
            if m_pct:
                return ("pct_indexer", float(m_pct.group(1).replace(",", ".")))
    return (None, None)


def _rate_fields(mode: str, value: float, indexer: str):
    """`REGRAS_CAMPOS_CADASTRO.md` §5 table -> (indexer, yield, indexerPercentual)."""
    if mode == "pct_indexer":
        return "CDI", 0, value
    if mode == "spread":
        return indexer, value, 100
    return "PRE", value, None  # pre


# ── beehusName-regra-final.md §4.2-4.4 — bonds onshore, mesma fórmula/algoritmo ─
# CDB, LCA, LCI, LCD, CD, CCB, LC, LF, LF-sub, LIG (§4.2) e, como fallback
# quando a Onboarding API não resolve, CRI, CRA (§4.3) e Debênture (§4.4)
# compartilham a MESMA fórmula de nome — `[TIPO] [Emissor] [DD/Mmm/AAAA]`
# (pós-fixado) ou `[TIPO] [Emissor] Pré [DD/Mmm/AAAA]` (pré-fixado) — sem
# taxa, sem indexador, sem label "Pós-fixado"/"Pré-fixado" no nome. A taxa/
# indexador continuam nos campos estruturados (`indexer`/`yield`/
# `indexerPercentual`), só saem do nome. O ticker sintético (§6.2, só usado
# quando não há código real) carrega o indexador mas NÃO o valor da taxa.
# CRI/CRA/Debênture usam `_DEVEDOR_CANONICALS` (o devedor/lastro) em vez de
# `_BANK_CANONICALS` para o token `EMISSOR`; Debênture normaliza o prefixo
# para `DEB` e infere `infrastructureDebenture` quando o texto bruto contém
# INFRA/INCENT/INCENTIVADA.

_RENDA_FIXA_BR_TYPES = {
    "CDB": "cdb", "LCA": "lca", "LCI": "lci", "LCD": "lcd", "CD": "cd",
    "CCB": "ccb", "LC": "lc", "LF": "lf", "LFS": "lf-sub", "LFSN": "lf-sub",
    "LIG": "lig", "CRI": "cri", "CRA": "cra", "CDCA": "cd",
}
_RENDA_FIXA_BR_LABEL = {  # instrumento -> sigla exibida no nome (LFS/LFSN mostram "LF")
    "CDB": "CDB", "LCA": "LCA", "LCI": "LCI", "LCD": "LCD", "CD": "CD",
    "CCB": "CCB", "LC": "LC", "LF": "LF", "LFS": "LF", "LFSN": "LF",
    "LIG": "LIG", "CRI": "CRI", "CRA": "CRA", "CDCA": "CDCA",
}
_DEVEDOR_INSTRUMENTS = {"CRI", "CRA", "DEB", "DEBENTURE"}
_INFRA_DEBENTURE_RE = re.compile(r"INFRA|INCENT", re.IGNORECASE)


def build_renda_fixa_simples(instrument: str, raw_text: str, features: dict):
    """CDB/LCA/LCI/LCD/CD/CCB/LC/LF/LF-sub/LIG/CRI/CRA/Debênture —
    `[TIPO] [Emissor] [DD/Mmm/AAAA]` (`[TIPO] [Emissor] Pré [DD/Mmm/AAAA]` se
    pré-fixado). Returns `(beehus_name, payload)`. Raises
    `RegistrationPending` when the issuer, maturity, or rate/indexer can't be
    confirmed from `raw_text`."""
    is_debenture = instrument in ("DEB", "DEBENTURE")
    tipo = ("infrastructureDebenture" if is_debenture and _INFRA_DEBENTURE_RE.search(raw_text)
            else "debenture" if is_debenture
            else _RENDA_FIXA_BR_TYPES.get(instrument))
    if not tipo:
        raise RegistrationPending(f"instrumento '{instrument}' fora da família renda-fixa-BR sem código público")

    issuer = features.get("issuer")
    if not issuer:
        raise RegistrationPending("emissor/devedor não identificado no texto bruto")

    maturity = features.get("maturity_date")
    if not maturity:
        raise RegistrationPending("data de vencimento não identificada no texto bruto")

    indexer_raw = features.get("indexer")
    mode, value = _detect_rate_mode(raw_text, indexer_raw)
    if mode is None:
        raise RegistrationPending(f"taxa/indexador não confirmados no texto bruto (indexer detectado={indexer_raw!r})")

    canonicalize = get_devedor_canonical if instrument in _DEVEDOR_INSTRUMENTS else get_short_emissor
    issuer_name = canonicalize(issuer)
    date_str = format_date_br(maturity, bool(features.get("maturity_day_specified")))
    out_indexer, out_yield, out_pct = _rate_fields(mode, value, indexer_raw)
    label = "DEB" if is_debenture else _RENDA_FIXA_BR_LABEL[instrument]

    beehus_name = (f"{label} {issuer_name} Pré {date_str}" if mode == "pre"
                  else f"{label} {issuer_name} {date_str}")
    # Ticker SEMPRE sintético pra essa família inteira (§6.2:
    # {TIPO}{EMISSOR}{INDEXADOR}{VENCIMENTO}) — confirmado explicitamente com
    # o usuário: mesmo quando `_extract_bond` capturou um cetip_code/
    # internal_code do texto bruto, NÃO usar como ticker aqui. Isso vale
    # também pra CRI/CRA/Debênture nesta função (só chegam aqui quando a
    # Onboarding API já tentou e não achou o código, então não dá pra
    # confiar nele mesmo assim).
    ticker = _synthetic_ticker(label, issuer_name, out_indexer, maturity)

    payload = {
        "securityType": "bond",
        "type": tipo,
        "beehusName": beehus_name,
        "ticker": ticker,
        "maturityDate": maturity,
        "indexer": out_indexer,
        "yield": out_yield,
        "indexerPercentual": out_pct,
        "currency": "BRL",
        "country": "BR",
        **_rf_br_settlement(),
    }
    return beehus_name, payload


def build_lf(raw_text: str, features: dict, *, subordinada: bool = False):
    """LF / LF-sub — mesma fórmula "sem taxa" de `build_renda_fixa_simples`
    (thin wrapper kept for call-site clarity: callers dispatching on the raw
    `LF`/`LFS`/`LFSN` instrument token don't need to know it's the same
    formula as CDB now)."""
    instrument = "LFS" if subordinada else "LF"
    return build_renda_fixa_simples(instrument, raw_text, features)


# ── §4.3 — NP ──────────────────────────────────────────────────────────────
# NOT part of the "sem taxa unificada" family the user confirmed live for
# CDB/LF/LIG (SKILL.md's own unified list doesn't include NP either) — NP
# keeps REGRAS_BEEHUSNAME.md §4.3's dedicated formula: no issuer in the name,
# rate instead — per beehusName-regra-final.md §4.2, NP has NEITHER emissor
# NOR taxa in the name (`[TIPO] [DD/Mmm/AAAA]`), superseding
# REGRAS_BEEHUSNAME.md §4.3's `NP [TAXA] [DD/Mmm/AAAA]`. Unreachable today
# (`security_matcher._BOND_INSTRUMENT_RE` doesn't extract an "NP" token) —
# kept ready for when that's wired up. The rate/indexer are still required
# for the structured fields even though they don't show in the name.

_NP_TYPES = {"NP": "np"}


def build_np(raw_text: str, features: dict):
    """beehusName-regra-final.md §4.2 — `NP [DD/Mmm/AAAA]` (no issuer, no
    rate in the name; "Pré" inserted before the date if pré-fixado)."""
    tipo = _NP_TYPES["NP"]
    instrument = "NP"

    maturity = features.get("maturity_date")
    if not maturity:
        raise RegistrationPending("data de vencimento não identificada no texto bruto")

    indexer_raw = features.get("indexer")
    mode, value = _detect_rate_mode(raw_text, indexer_raw)
    if mode is None:
        raise RegistrationPending(f"taxa/indexador não confirmados no texto bruto (indexer detectado={indexer_raw!r})")

    date_str = format_date_br(maturity, bool(features.get("maturity_day_specified")))
    out_indexer, out_yield, out_pct = _rate_fields(mode, value, indexer_raw)

    beehus_name = (f"{instrument} Pré {date_str}" if mode == "pre"
                  else f"{instrument} {date_str}")
    # Ticker sempre sintético pra esta família — ver nota em
    # build_renda_fixa_simples (mesma decisão, confirmada com o usuário).
    ticker = _synthetic_ticker(instrument, "", out_indexer, maturity)

    payload = {
        "securityType": "bond",
        "type": tipo,
        "beehusName": beehus_name,
        "ticker": ticker,
        "maturityDate": maturity,
        "indexer": out_indexer,
        "yield": out_yield,
        "indexerPercentual": out_pct,
        "currency": "BRL",
        "country": "BR",
        **_rf_br_settlement(),
    }
    return beehus_name, payload


# ── §4.6 — Compromissada ──────────────────────────────────────────────────────
# beehusName-regra-final.md §4.9: compromissadas are classified under
# `securityType="otc"` (not a standalone "brazilianRepo" securityType) with
# `type="brazilianRepo"` — a temporary decision (2026-07-03) until a dedicated
# tab exists. So the classifier hands this endpoint `security_type="otc"` for
# these, same as COE/swap/etc — `looks_like_compromissada` is what tells them
# apart from raw text, since the securityType alone doesn't.
#
# Raw custodian shapes observed: SKILL.md's "Compromissada [TIPO] [...]
# [CÓDIGO] - [EMPRESA] - [PCT]% - CDI - [CÓDIGO]", and this app's own
# "{EMISSOR} - Comp*{VEÍCULO}-{CÓDIGO} - {PCT}%CDI - {DATA}" (confirmed live,
# e.g. "AMBIENTAL CEARA 1 SPE S.A. - Comp*DEB-CYAR11 - 85%CDI - 17/Nov/2026").
# We only need the CDI% and the subjacent-asset code out of either; the rest
# is free text we don't use.

_COMPROMISSADA_HINT_RE = re.compile(r"\bCOMPROMISSADA\b|COMP\*", re.IGNORECASE)
_COMPROMISSADA_CODE_AFTER_COMP_RE = re.compile(r"COMP\*(?:[A-Z]{2,6}-)?([A-Z0-9]{4,})", re.IGNORECASE)
_COMPROMISSADA_CODE_GENERIC_RE = re.compile(r"\b([A-Z]{2,6}\d+[A-Z0-9]*)\b")
_PCT_RE = re.compile(r"(\d+(?:[,.]\d+)?)\s*%")


def looks_like_compromissada(raw_text: str) -> bool:
    """True when the raw text confidently signals a compromissada (repo) —
    used to route `securityType="otc"` rows here, since the classifier
    doesn't separately flag `type="brazilianRepo"` before this point."""
    return bool(_COMPROMISSADA_HINT_RE.search(raw_text))


def parse_compromissada(raw_text: str):
    """Best-effort `(pct, codigo)` extraction from a raw compromissada string.
    Returns `(None, None)` for either piece it can't find — never guesses."""
    m_pct = _PCT_RE.search(raw_text)
    pct = float(m_pct.group(1).replace(",", ".")) if m_pct else None
    up = raw_text.upper()
    m_code = _COMPROMISSADA_CODE_AFTER_COMP_RE.search(up) or _COMPROMISSADA_CODE_GENERIC_RE.search(up)
    codigo = m_code.group(1) if m_code else None
    return pct, codigo


def build_compromissada(raw_text: str, features: dict | None = None):
    """`REGRAS_BEEHUSNAME.md` §4.6 — `Compromissada - {pct}% CDI - {código}`,
    ticker `COMP{pct}CDI{código}`. `maturityDate` uses the `2099-01-01`
    placeholder (compromissadas have no real maturity in this schema)."""
    pct, codigo = parse_compromissada(raw_text)
    if pct is None or not codigo:
        raise RegistrationPending(
            "compromissada: percentual do CDI ou código do ativo subjacente "
            "não identificados no texto bruto"
        )
    pct_str = f"{pct:g}"  # sem casas decimais se inteiro, com casas se fracionário
    beehus_name = f"Compromissada - {pct_str}% CDI - {codigo}"
    ticker = f"COMP{pct_str}CDI{codigo}"

    payload = {
        "securityType": "otc",
        "type": "brazilianRepo",
        "beehusName": beehus_name,
        "ticker": ticker,
        "maturityDate": "2099-01-01",
        "indexer": "CDI",
        "indexerPercentual": pct,
        "yield": 0,
        "currency": "BRL",
        "country": "BR",
        **_rf_br_settlement(),
    }
    return beehus_name, payload


# ── beehusName-regra-final.md §4.5 / beehusName-regras.md §2.2 — stockEtf BR ──
# `<TICKER> - <nome limpo> <sufixo>` — ticker first, recognized suffixes
# (FII/ETF/PN/ON/BDR) moved to the end uppercase; FIDC/FIP/FIAGRO are left
# wherever they already are in the name. Built from the ALREADY-CLEANED name
# `security_matcher._extract_stock_etf` derives from the raw custodian uid
# (it strips the leading ticker already) — NOT from the Onboarding API's own
# `beehusName`, which comes from a raw exchange feed and carries noise
# (listing-segment codes etc.) the real production convention doesn't have.
_STOCK_SUFFIX_RE = re.compile(r"\b(FII|ETF|PN|ON|BDR)\b", re.IGNORECASE)


def build_stock_etf_name_br(ticker: str, raw_name: str) -> str:
    ticker_u = (ticker or "").upper().strip()
    name = raw_name or ""
    if ticker_u:
        name = re.sub(re.escape(ticker_u), "", name, flags=re.IGNORECASE)
    name = re.sub(r"^\s*-\s*", "", name)  # leading " - " left over from ticker removal

    suffix = ""
    m = _STOCK_SUFFIX_RE.search(name)
    if m and m.group(1).upper() not in ("FIDC", "FIP", "FIAGRO"):
        suffix = m.group(1).upper()
        name = name[:m.start()] + name[m.end():]

    name = re.sub(r"\s*-\s*$", "", name)  # trailing " - " left after removing an inline suffix
    name = re.sub(r"\s+", " ", name).strip(" -")
    name = title_br(name)
    return f"{ticker_u} - {name} {suffix}".strip() if suffix else f"{ticker_u} - {name}".strip()
