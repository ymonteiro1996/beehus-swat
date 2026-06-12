"""Correctness guard for the scoring memoization (performance change).

Proves that reading the memoised derived fields (`_cand_dates`,
`_name_tokens`, `_hay`, `_name_c`, `_main_c`) yields byte-identical scoring
results to a fresh, non-memoised computation — and that a second call (which
hits the memo) matches the first (which populates it). Run directly:

    python scripts/test_scoring_memoization.py
"""
import copy
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import security_matcher as sm
import transaction_security_classifier as tsc


# A spread of securities + feature dicts exercising every scoring branch:
# exact id, date gate (agree / disagree), partial id, indexer, name overlap,
# compressed-name substring bonus, and the L3 haystack pre-filter.
SECURITIES = [
    {"_id": "s1", "securityType": "brazilianCorporateBond",
     "beehusName": "CRI TRUE SECURITIZADORA - SET/2029", "mainId": "24D3691425",
     "ticker": "", "taxId": "", "isIn": "", "selicCode": "",
     "maturityDate": "2029-09-15", "indexer": "IPCA"},
    {"_id": "s2", "securityType": "stock",
     "beehusName": "PETROBRAS PN", "mainId": "PETR4", "ticker": "PETR4",
     "taxId": "", "isIn": "BRPETRACNPR6", "selicCode": "",
     "maturityDate": "", "indexer": ""},
    {"_id": "s3", "securityType": "brazilianGovernmentBond",
     "beehusName": "LFT 01/03/2027", "mainId": "210100", "ticker": "",
     "taxId": "", "isIn": "", "selicCode": "210100",
     "maturityDate": "2027-03-01", "indexer": "SELIC"},
    {"_id": "s4", "securityType": "fund",
     "beehusName": "OIKOS C FICFI MM", "mainId": "12345678000190",
     "ticker": "", "taxId": "12345678000190", "isIn": "", "selicCode": "",
     "maturityDate": "", "indexer": ""},
    {"_id": "s5", "securityType": "options",
     "beehusName": "CALL AAPL 280 17/04/2026", "mainId": "CALL_AAPL_280_17042026",
     "ticker": "AAPLD280", "taxId": "", "isIn": "", "selicCode": "",
     "maturityDate": "2026-04-17", "indexer": ""},
    {"_id": "s6", "securityType": "brazilianCorporateBond",
     "beehusName": "DEB VALE 2031", "mainId": "VALE31", "ticker": "",
     "taxId": "", "isIn": "", "selicCode": "", "maturityDate": "2031-01-01",
     "indexer": "CDI"},
]

FEATURES = [
    {"name": "CRI TRUE SECURITIZADORA", "maturity_date": "2029-09-01",
     "cetip_code": "24D3691425"},
    {"ticker": "PETR4"},
    {"selic_code": "210100", "maturity_date": "2027-03-01",
     "maturity_day_specified": True, "indexer": "SELIC"},
    {"cnpj": "12345678000190", "name": "OIKOS"},
    {"option_type": "CALL", "underlying": "AAPL", "strike": "280.0",
     "expiry": "2026-04-17"},
    # Date-disagree case: same issuer, different year → must hard-zero.
    {"name": "DEB VALE", "maturity_date": "2035-01-01"},
    # No identifying features at all.
    {},
]


def _fresh(sec):
    """A memo-free deep copy (drops any underscore-prefixed derived keys)."""
    return copy.deepcopy({k: v for k, v in sec.items() if not k.startswith("_")})


def _name_bonus_fresh(desc_c, sec):
    s = _fresh(sec)
    return tsc._name_substring_bonus(desc_c, s)


def _behavioral_checks():
    """Assert the two scoring rules added on 2026-06:
      1. CNPJ match is punctuation-insensitive and scores as a perfect (100)
         exact identifier — both bare→punctuated and punctuated→bare.
      2. An exact day/month/year date coincidence adds +50 ("maturityDate=");
         a month/year-only coincidence keeps +25 ("maturityDate")."""
    fails = 0

    def check(label, cond):
        nonlocal fails
        if not cond:
            fails += 1
            print(f"FAIL behavioral: {label}")

    # ── CNPJ punctuation-insensitive exact match (exact id → _EXACT_SCORE) ──
    sec_punct = {"_id": "p1", "securityType": "fund", "beehusName": "ALPHA FIC FIM",
                 "mainId": "FUNDALPHA", "taxId": "12.345.678/0001-90"}
    sec_bare  = {"_id": "p2", "securityType": "fund", "beehusName": "BETA FIC FIM",
                 "mainId": "FUNDBETA", "taxId": "12345678000190"}
    score, reasons = sm._score_candidate(sec_punct, {"cnpj": "12345678000190"}, "fund")
    check("bare cnpj → punctuated taxId == EXACT", score == sm._EXACT_SCORE and "taxId" in reasons)
    score, reasons = sm._score_candidate(sec_bare, {"cnpj": "12.345.678/0001-90"}, "fund")
    check("punctuated cnpj → bare taxId == EXACT", score == sm._EXACT_SCORE and "taxId" in reasons)
    # A different CNPJ must NOT match.
    score, _ = sm._score_candidate(sec_punct, {"cnpj": "99999999000199"}, "fund")
    check("different cnpj does not match", score != sm._EXACT_SCORE)
    # The transaction extractor must pull a bare 14-digit CNPJ out of the text.
    feats = tsc._extract_generic_safe("RESGATE COTAS ALPHA FIC FIM 12345678000190")
    check("bare 14-digit CNPJ extracted from txn text", feats.get("cnpj") == "12345678000190")

    # ── Graduated date bonus ──
    sec_dated = {"_id": "d1", "securityType": "brazilianCorporateBond",
                 "beehusName": "DEB ACME", "mainId": "ACME1", "maturityDate": "2029-09-15"}
    # Exact day named and matches → +50 ("maturityDate=")
    s_exact, r_exact = sm._score_candidate(
        sec_dated, {"maturity_date": "2029-09-15", "maturity_day_specified": True},
        "brazilianCorporateBond")
    check("exact day match tags maturityDate=", "maturityDate=" in r_exact)
    check("exact day match adds 50", s_exact == 50)
    # Month/year only (day defaulted) → +25 ("maturityDate")
    s_my, r_my = sm._score_candidate(
        sec_dated, {"maturity_date": "2029-09-01", "maturity_day_specified": False},
        "brazilianCorporateBond")
    check("month/year match tags maturityDate", "maturityDate" in r_my and "maturityDate=" not in r_my)
    check("month/year match adds 25", s_my == 25)
    # Disagreeing date still hard-zeros.
    s_bad, _ = sm._score_candidate(
        sec_dated, {"maturity_date": "2030-09-15", "maturity_day_specified": True},
        "brazilianCorporateBond")
    check("disagreeing date hard-zeros", s_bad == 0)

    # ── Rare-token (corpus rarity) name bonus ──
    saved_df = sm._TOKEN_DF
    try:
        # "delfos" appears in 2 securities (rare), "fundo" in 5000 (generic).
        sm._TOKEN_DF = {"delfos": 2, "fundo": 5000, "fi": 4000, "mm": 4000}
        sec_rare = {"_id": "r1", "securityType": "brazilianFund",
                    "beehusName": "Delfos FI MM", "mainId": "DLF1"}
        sec_gen  = {"_id": "r2", "securityType": "brazilianFund",
                    "beehusName": "Generico Fundo", "mainId": "GEN1"}
        feats = {"name": "RESGATE DELFOS INVESTIMENTO MULTIMERCADO FUNDO"}
        s_rare, r_rare = sm._score_candidate(sec_rare, feats, "brazilianFund")
        s_gen,  r_gen  = sm._score_candidate(sec_gen, feats, "brazilianFund")
        check("rare token tags name~rare", any("name~rare" in x for x in r_rare))
        check("rare-token match outscores generic-token match", s_rare > s_gen)
        check("generic-only token gets no rarity bonus",
              not any("name~rare" in x for x in r_gen))

        # A generic asset-class term that is corpus-rare (abbreviation
        # mismatch) must NOT earn the rarity bonus nor any name score.
        sm._TOKEN_DF = {"referenciado": 2, "delfos": 2}
        sec_genrare = {"_id": "r3", "securityType": "brazilianFund",
                       "beehusName": "ABC Referenciado DI", "mainId": "ABC1"}
        s_gr, r_gr = sm._score_candidate(
            sec_genrare, {"name": "RESGATE FUNDO REFERENCIADO DI"}, "brazilianFund")
        check("corpus-rare GENERIC term earns no name score", s_gr == 0)
        check("corpus-rare GENERIC term earns no rarity bonus",
              not any("name~rare" in x for x in r_gr))
    finally:
        sm._TOKEN_DF = saved_df

    # ── Type-agreement tie-breaker (NTN-B vs same-date corporate bond) ──
    feats_ntnb = {"bond_type": "NTN-B", "maturity_date": "2035-08-15",
                  "maturity_day_specified": True}
    ntnb = {"_id": "t1", "securityType": "brazilianGovernmentBond",
            "beehusName": "NTN-B Ago/2035", "mainId": "NTNB2035",
            "maturityDate": "2035-08-15"}
    corp = {"_id": "t2", "securityType": "bond",
            "beehusName": "Comcast - 4,40% - 15/Aug/2035", "mainId": "US20030N",
            "maturityDate": "2035-08-15"}
    s_ntnb, r_ntnb = sm._score_candidate(ntnb, feats_ntnb, "brazilianGovernmentBond")
    s_corp, r_corp = sm._score_candidate(corp, feats_ntnb, "bond")
    check("NTN-B gets type= tag", any(x.startswith("type=") for x in r_ntnb))
    check("same-date corporate bond gets NO type bonus",
          not any(x.startswith("type=") for x in r_corp))
    check("type bonus breaks the same-date tie (NTN-B > corporate)", s_ntnb > s_corp)
    # ── American date format (prefer_mdy) ──
    check("ambiguous date defaults to DD/MM", sm._parse_date("12/01/2034") == "2034-01-12")
    check("ambiguous date + prefer_mdy -> MM/DD",
          sm._parse_date("12/01/2034", prefer_mdy=True) == "2034-12-01")
    check("unambiguous DD/MM ignores prefer_mdy",
          sm._parse_date("15/08/2040", prefer_mdy=True) == "2040-08-15")
    check("unambiguous MM/DD detected by value",
          sm._parse_date("12/15/2034") == "2034-12-15")
    # _candidate_dates uses the security's OWN currency for ambiguity.
    usd_sec = {"_id": "u1", "securityType": "bond", "currency": "USD",
               "beehusName": "Foo Bar 12/01/2034", "maturityDate": ""}
    brl_sec = {"_id": "u2", "securityType": "bond", "currency": "BRL",
               "beehusName": "Foo Bar 12/01/2034", "maturityDate": ""}
    check("USD security name date read MM/DD", "2034-12-01" in sm._candidate_dates(usd_sec))
    check("BRL security name date read DD/MM", "2034-01-12" in sm._candidate_dates(brl_sec))

    # Type alone (no other signal) must NOT score — guard against generic promo.
    s_typeonly, r_typeonly = sm._score_candidate(
        {"_id": "t3", "securityType": "bond", "beehusName": "Algum CDB Banco X",
         "mainId": "CDBX"},
        {"instrument": "CDB"}, "bond")
    check("type-only (no other signal) earns nothing", s_typeonly == 0)
    check("type-only adds no type= tag", not any(x.startswith("type=") for x in r_typeonly))

    # ── Exact-identifier dominance (full-equality code + ranking) ──
    # A code that equals the WHOLE mainId is an exact id → _EXACT_SCORE.
    full_sec = {"_id": "e1", "securityType": "bond", "beehusName": "CRI XPTO",
                "mainId": "21I0183215"}
    s_full, r_full = sm._score_candidate(full_sec, {"cetip_code": "21I0183215"}, "bond")
    check("full-equality code → exact score", s_full == sm._EXACT_SCORE and "exact" in r_full)
    # A code that is only a SUBSTRING of a longer mainId stays partial (+50).
    sub_sec = {"_id": "e2", "securityType": "bond", "beehusName": "CRI XPTO",
               "mainId": "CRI21I0183215XYZ"}
    s_sub, r_sub = sm._score_candidate(sub_sec, {"cetip_code": "21I0183215"}, "bond")
    check("substring code stays partial (+50, not exact)",
          s_sub == 50 and "mainId/cetip" in r_sub and "exact" not in r_sub)
    # A SHORT code equal to a short mainId must NOT be promoted to exact (length
    # guard ≥ 8) — it stays in the additive path (+35 fund_code substring).
    short_sec = {"_id": "e3", "securityType": "fund", "beehusName": "FUNDO X",
                 "mainId": "5253"}
    s_short, r_short = sm._score_candidate(short_sec, {"fund_code": "5253"}, "fund")
    check("short full-equality code is NOT exact (length guard)",
          "exact" not in r_short and s_short == 35)
    # An exact ticker MUST outrank a coincidental additive pile-up that itself
    # exceeds 100 (the bug being fixed: a 100 exact match used to tie/lose).
    exact_cand  = {"_id": "r1", "securityType": "stockEtf",
                   "beehusName": "Banco do Brasil", "ticker": "BBAS3", "mainId": "BBAS3"}
    pileup_cand = {"_id": "r2", "securityType": "brazilianCorporateBond",
                   "beehusName": "DELFOS CAPITAL CRI", "mainId": "CRIDELFOS99XX",
                   "maturityDate": "2030-01-15", "indexer": "IPCA"}
    rank_feats = {"ticker": "BBAS3", "cetip_code": "DELFOS99",
                  "maturity_date": "2030-01-15", "maturity_day_specified": True,
                  "indexer": "IPCA", "name": "DELFOS"}
    s_exact_rank, _        = sm._score_candidate(exact_cand,  rank_feats, "stockEtf")
    s_pileup_rank, r_pile  = sm._score_candidate(pileup_cand, rank_feats, "brazilianCorporateBond")
    check("additive pile-up exceeds 100 (the original tie risk)", s_pileup_rank > 100)
    check("exact id outranks additive pile-up", s_exact_rank > s_pileup_rank and "exact" not in r_pile)
    return fails


def main():
    failures = 0
    for sec in SECURITIES:
        for feats in FEATURES:
            stype = sec["securityType"]

            ref = sm._score_candidate(_fresh(sec), feats, stype)
            got1 = sm._score_candidate(sec, feats, stype)   # populates memo
            got2 = sm._score_candidate(sec, feats, stype)   # reads memo
            if not (ref == got1 == got2):
                failures += 1
                print(f"FAIL _score_candidate sec={sec['_id']} feats={feats}\n"
                      f"  ref ={ref}\n  got1={got1}\n  got2={got2}")

            # _name_substring_bonus across a few compressed descriptions
            for desc_c in ("CRITRUESECURITIZADORASET2029", "OIKOSCFICFIMM",
                           "PETROBRASPN", "RANDOMTEXT12345"):
                ref_b = _name_bonus_fresh(desc_c, sec)
                got_b1 = tsc._name_substring_bonus(desc_c, sec)
                got_b2 = tsc._name_substring_bonus(desc_c, sec)
                if not (ref_b == got_b1 == got_b2):
                    failures += 1
                    print(f"FAIL _name_substring_bonus sec={sec['_id']} "
                          f"desc={desc_c}\n  ref={ref_b} got1={got_b1} got2={got_b2}")

    # Verify the memoised derived fields equal a fresh computation.
    for sec in SECURITIES:
        ref_dates = sm._candidate_dates(_fresh(sec))
        memo_dates = sm._candidate_dates(sec)
        if ref_dates != memo_dates:
            failures += 1
            print(f"FAIL _candidate_dates sec={sec['_id']}: {ref_dates} != {memo_dates}")
        ref_tok = sm._candidate_name_tokens(_fresh(sec))
        memo_tok = sm._candidate_name_tokens(sec)
        if ref_tok != memo_tok:
            failures += 1
            print(f"FAIL _candidate_name_tokens sec={sec['_id']}: {ref_tok} != {memo_tok}")

        # L3 haystack: the memo must equal the inline build (incl. the
        # digit-stripped taxId added for bare-CNPJ matching).
        def _build_hay(d):
            tax = str(d.get("taxId", ""))
            return (str(d.get("mainId", "")) + " "
                    + str(d.get("ticker", "")) + " "
                    + str(d.get("beehusName", ""))[:80] + " "
                    + tax + " "
                    + sm._digits_only(tax) + " "
                    + str(d.get("isIn", "")) + " "
                    + str(d.get("selicCode", ""))).upper()
        expected_hay = _build_hay(_fresh(sec))
        if "_hay" not in sec:
            sec["_hay"] = _build_hay(sec)
        if sec["_hay"] != expected_hay:
            failures += 1
            print(f"FAIL _hay sec={sec['_id']}: {sec['_hay']!r} != {expected_hay!r}")

    failures += _behavioral_checks()

    if failures:
        print(f"\n{failures} FAILURE(S)")
        sys.exit(1)
    print(f"OK — {len(SECURITIES)*len(FEATURES)} score combos + derived fields "
          f"all identical between memoised and fresh paths")


if __name__ == "__main__":
    main()
