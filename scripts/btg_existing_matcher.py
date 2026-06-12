"""Identifica se um ativo extraído de rawBTGPosition já existe na collection
`securities`, reusando o mesmo SecurityCache que alimenta o securityMapping.

Estratégia (varia por categoria do BTG):

  FixedIncome (CDB/LCA/LCI/Deb/CRI/CRA/LF)
    1. lookup(mainId  == CetipCode)              (canônico)
    2. lookup(ticker  == CetipCode)
    3. lookup(isIn    == ISIN)
    4. lookup(selicCode == SelicCode)
    Disambiguação: filtra por maturityDate igual; entre os restantes pega o
    yield mais próximo do BTG (NTNB/CDI/IPCA podem ter múltiplos rates p/ o
    mesmo Cetip code, e.g. "CDB222OO4MD" e "CDB222OO4MD.7.30").

  InvestmentFund + PensionInformations
    1. lookup(taxId  == CNPJ) em qualquer variação (formato 14-dígitos ou
       "XX.XXX.XXX/XXXX-XX").
    2. lookup(mainId == CNPJ) idem.
    securityType permitido: brazilianFund, fund, otc, poc.

  FixedIncomeStructuredNote (COE)
    1. lookup(mainId == CetipCode)
    2. lookup(ticker == CetipCode)
    3. fallback: scan otc/bond com nome + maturityDate equivalentes.

  CryptoCoin
    1. scan stockEtf/otc cujo mainId contém Asset.Code e o nome bate.
"""
from __future__ import annotations

import re
from typing import Iterable, Optional, Tuple

# Tipos da securities collection que aceitamos como "match" por categoria.
_TYPES_FOR_CATEGORY = {
    "FixedIncome":                 {"bond"},
    "InvestmentFund":              {"brazilianFund", "fund", "otc", "poc"},
    "PensionInformations":         {"brazilianFund", "fund", "otc", "poc"},
    "FixedIncomeStructuredNote":   {"otc", "bond"},
    "CryptoCoin":                  {"stockEtf", "otc"},
}


def _cnpj_variants(cnpj) -> list[str]:
    d = re.sub(r"\D", "", str(cnpj or ""))
    if len(d) != 14:
        return [d] if d else []
    formatted = f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    return [d, formatted]


def _as_float(v):
    if v in (None, ""):
        return None
    try:
        return float(str(v).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _date_only(v) -> str:
    return str(v)[:10] if v else ""


def _pick_best(candidates, *, maturity: str = "", btg_yield: Optional[float] = None):
    """Entre N candidatos, prefere os com maturityDate igual; entre os
    sobrantes, o que tem yield mais próximo do BTG. Retorna o melhor + razão.
    """
    if not candidates:
        return None, None
    if len(candidates) == 1:
        return candidates[0], "único candidato"

    by_mat = [c for c in candidates if maturity and _date_only(c.get("maturityDate")) == maturity]
    pool = by_mat if by_mat else candidates

    if len(pool) == 1:
        return pool[0], ("maturity match" if by_mat else "único após filtro")

    if btg_yield is not None:
        scored = []
        for c in pool:
            cy = _as_float(c.get("yield"))
            if cy is None:
                continue
            scored.append((abs(cy - btg_yield), c))
        if scored:
            scored.sort(key=lambda t: t[0])
            return scored[0][1], (
                f"maturity+yield (Δ={scored[0][0]:.4f})" if by_mat
                else f"yield (Δ={scored[0][0]:.4f})"
            )

    return pool[0], ("maturity match (sem yield)" if by_mat else "primeiro candidato")


def _lookup_first(cache, field: str, value, allowed_types: Iterable[str]) -> list[dict]:
    """Wrapper sobre cache.lookup() filtrando por securityType."""
    if not value:
        return []
    return [
        s for s in cache.lookup(field, value)
        if s.get("securityType") in allowed_types
    ]


def find_existing(ativo: dict, cache) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Retorna (securityId, beehusName, matchedOn) ou (None, None, None)."""
    category = ativo.get("category")
    allowed = _TYPES_FOR_CATEGORY.get(category, set())
    maturity = ativo.get("maturityDate") or ""
    btg_yield = _as_float(ativo.get("yield"))

    # ── FixedIncome ──────────────────────────────────────────────────────
    if category == "FixedIncome":
        cetip = ativo.get("cetipCode") or ""
        isin = ativo.get("isin") or ""
        selic = ativo.get("selicCode") or ""

        # Ordem: CetipCode em mainId/ticker, ISIN, SelicCode.
        for field, val in (("mainId", cetip), ("ticker", cetip),
                           ("isIn", isin), ("selicCode", selic)):
            cands = _lookup_first(cache, field, val, allowed)
            if cands:
                best, reason = _pick_best(cands, maturity=maturity,
                                           btg_yield=btg_yield)
                if best:
                    return best["_id"], best.get("beehusName", ""), \
                           f"{field}={val} | {reason}"
        return None, None, None

    # ── InvestmentFund / PensionInformations ────────────────────────────
    # CNPJ é canônico — match SÓ por CNPJ (variantes formato/raw).
    # Fallback por nome foi removido após produzir falsos positivos:
    # CNPJ ausente no cache significa "não cadastrado", sem chute por substring.
    if category in ("InvestmentFund", "PensionInformations"):
        cnpj = ativo.get("taxId") or ""
        for v in _cnpj_variants(cnpj):
            for field in ("taxId", "mainId"):
                cands = _lookup_first(cache, field, v, allowed)
                if cands:
                    best, reason = _pick_best(cands)
                    if best:
                        return best["_id"], best.get("beehusName", ""), \
                               f"{field}={v} | {reason}"
        return None, None, None

    # ── FixedIncomeStructuredNote (COE) ──────────────────────────────────
    if category == "FixedIncomeStructuredNote":
        cetip = ativo.get("cetipCode") or ""
        for field in ("mainId", "ticker"):
            cands = _lookup_first(cache, field, cetip, allowed)
            if cands:
                best, reason = _pick_best(cands, maturity=maturity)
                if best:
                    return best["_id"], best.get("beehusName", ""), \
                           f"{field}={cetip} | {reason}"
        # Fallback: scan COE da otc por maturityDate + fragmento do fantasy
        fantasy = (ativo.get("fantasyName") or "").upper()
        if maturity and fantasy:
            kw = [w for w in fantasy.split() if len(w) >= 4][:2]
            if kw:
                joined = " ".join(kw)
                hits = []
                for s in cache.get_by_type("otc"):
                    if _date_only(s.get("maturityDate")) != maturity:
                        continue
                    name_u = (s.get("beehusName") or "").upper()
                    if all(k in name_u for k in kw):
                        hits.append(s)
                if len(hits) == 1:
                    return hits[0]["_id"], hits[0].get("beehusName", ""), \
                           f"fantasy~{joined} + maturity"
        return None, None, None

    # ── CryptoCoin ──────────────────────────────────────────────────────
    if category == "CryptoCoin":
        code = (ativo.get("securityCode") or "").upper()
        # BTG não emite o ticker B3 (IBIT/QBTC/XBT_BITCOIN_CRIPTO); busca pelo nome
        asset_name = (ativo.get("issuerRaw") or ativo.get("beehusName") or "").upper()
        for s in cache.get_by_type("stockEtf"):
            mid = (s.get("mainId") or "").upper()
            if "CRIPTO" in mid or "BITCOIN" in mid:
                bn = (s.get("beehusName") or "").upper()
                if "BITCOIN" in asset_name and "BITCOIN" in bn and "CRIPTO" in mid:
                    return s["_id"], s.get("beehusName", ""), f"name~Bitcoin"
                if "ETHEREUM" in asset_name and "ETHEREUM" in bn and "CRIPTO" in mid:
                    return s["_id"], s.get("beehusName", ""), f"name~Ethereum"
                if "SOLANA" in asset_name and "SOLANA" in bn:
                    return s["_id"], s.get("beehusName", ""), f"name~Solana"
        return None, None, None

    return None, None, None
