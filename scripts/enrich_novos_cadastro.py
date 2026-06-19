# -*- coding: utf-8 -*-
"""Gera o JSON de cadastro dos ativos NÃO cadastrados, com dados verificados
(ANBIMA/maisretorno p/ debêntures; web p/ ETFs e fundos offshore) e beehusName
padronizado por categoria. Decisões do usuário aplicadas:
  - BRRBRACRA470 = 103,50% CDI (descarta 107%)
  - BRRBRACRA4A3 = cadastro único 12,57% (prefixado)
  - debêntures incentivadas: VALEB0/UHSM12/TAEEB5/RESA15/MSGT23/EGIEA2 = infra;
    VAMO34 = comum
  - FIP/FIDC/FII BR conforme convenção do sistema
  - beehusName padronizado
Saídas:
  data/temp/cadastro_novos_registration.json
  data/temp/cadastro_novos_revisao.json
"""
import sys, io, json, re
from pathlib import Path
from collections import Counter, OrderedDict

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
BASE = Path(__file__).resolve().parent.parent

items = json.load(open(BASE / "data/temp/cadastro_ativos_com_isin.json", encoding="utf-8"))
res = {r["security"].upper(): r for r in
       json.load(open(BASE / "data/temp/cadastro_ativos_resultado.json", encoding="utf-8"))}
nao_cad = [it for it in items if not res.get(it["security"].upper(), {}).get("jaCadastrado")]

PT = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def date_pt(iso):
    if not iso:
        return ""
    y, mo, d = iso.split("-")
    return f"{d}/{PT[int(mo)]}/{y}"


def fmtnum(n):
    """pt-BR: vírgula decimal, mínimo 2 casas, sem zeros à direita além disso."""
    x = f"{float(n):.4f}".rstrip("0")
    if x.endswith("."):
        x += "00"
    else:
        i, f = x.split(".")
        if len(f) < 2:
            f = f.ljust(2, "0")
        x = f"{i}.{f}"
    return x.replace(".", ",")


def parse_date(name):
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})", name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(\d{2})/(\d{2})/(20\d{2})", name)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    m = re.search(r"(\d{2})/(\d{2})/(\d{2})(?!\d)", name)
    if m:
        return f"20{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def parse_rate(name):
    s = name.replace(",", "."); up = s.upper()
    if "IGPM" in up or "IGP-M" in up:
        m = re.search(r"[+]?\s*(\d+(?:\.\d+)?)\s*%", s)
        return "IGPM", (float(m.group(1)) if m else None), 100
    if "IPCA" in up or "IPC-A" in up:
        m = re.search(r"[+]?\s*(\d+(?:\.\d+)?)\s*%", s)
        return "IPCA", (float(m.group(1)) if m else None), 100
    if "CDI" in up:
        m = re.search(r"CDI\s*\+\s*(\d+(?:\.\d+)?)\s*%", s)
        if m:
            return "CDI", float(m.group(1)), 100
        m = re.search(r"(\d+(?:\.\d+)?)\s*%\s*CDI", s)
        if m:
            return "CDI", 0.0, float(m.group(1))
        return "CDI", 0.0, 100
    m = re.search(r"(\d+(?:\.\d+)?)\s*%", s)
    if m:
        return "PRE", float(m.group(1)), None
    return None, None, None


def rate_label(idx, y, idxp):
    if idx == "PRE":
        return f"{fmtnum(y)}%"
    if idx in ("IPCA", "IGPM"):
        return f"{idx}+{fmtnum(y)}%"
    if idx == "CDI":
        return f"CDI+{fmtnum(y)}%" if (y and y > 0) else f"{fmtnum(idxp)}%CDI"
    return ""


def devedor(name):
    """Extrai o devedor entre o tipo (CRI/CRA/LCI) e o início da taxa."""
    rest = re.sub(r"^(CRI|CRA|LCI)\s+", "", name, flags=re.I)
    m = re.search(r"\s+(\d|IPCA|IPC-A|IGPM|IGP-M|CDI|SELIC|PRE\b)", rest, flags=re.I)
    pref = rest[:m.start()] if m else rest
    pref = pref.strip()
    # title-case leve preservando siglas curtas
    out = []
    for w in pref.split():
        if len(w) <= 3 and w.isupper():
            out.append(w)
        elif w.upper() in ("D'OR", "REDE"):
            out.append(w.title())
        else:
            out.append(w[:1].upper() + w[1:].lower())
    return " ".join(out)


# ── Overrides verificados (web/ANBIMA/maisretorno) keyed por ISIN ───────────
V = {
    # Debêntures (maisretorno: incentivada + taxa de emissão)
    "BRVAMODBS074": dict(st="bond", ty="debenture", idx="IPCA", idxp=100, y=7.6897, mat="2031-10-15", dev="Vamos"),
    "BRVALEDBS0B3": dict(st="bond", ty="infrastructureDebenture", idx="IPCA", idxp=100, y=6.4368, mat="2036-10-15", dev="Vale"),
    "BRUHSMDBS023": dict(st="bond", ty="infrastructureDebenture", idx="IPCA", idxp=100, y=5.8198, mat="2036-10-15", dev="UHE São Simão"),
    "BRTAEEDBS0X0": dict(st="bond", ty="infrastructureDebenture", idx="IGPM", idxp=100, y=5.8438, mat="2034-03-15", dev="Taesa"),
    "BRRESADBS054": dict(st="bond", ty="infrastructureDebenture", idx="IPCA", idxp=100, y=5.80, mat="2030-06-15", dev="Raízen"),
    "BRMSGTDBS050": dict(st="bond", ty="infrastructureDebenture", idx="IPCA", idxp=100, y=6.0762, mat="2037-11-15", dev="Mata de Santa Genebra"),
    "BREGIEDBS0H0": dict(st="bond", ty="infrastructureDebenture", idx="PRE", idxp=None, y=12.4974, mat="2029-08-15", dev="Engie"),
    # FII/FIDC listados (...11) → stockEtf
    "BRKNCECTF008": dict(st="stockEtf", tk="KNCE11", cur="BRL", ctry="BR", bn="Kinea Crédito Estruturado - FIDC"),
    "BRKFENCTF001": dict(st="stockEtf", tk="KFEN11", cur="BRL", ctry="BR", bn="Kinea Fênix - FII"),
    "BRILOGCTF006": dict(st="stockEtf", tk="ILOG11", cur="BRL", ctry="BR", bn="Itaú RBR Log - FII"),
    # Fundos BR (brazilianFund) + cvmKlass
    "BRIPW1CTF009": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Renda Fixa", cnpj="29.152.383/0001-03", bn="Itaú Private Wealth IQ Renda Fixa FIC FI"),
    "BRANG6CTF008": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Multimercados", cnpj="23.034.819/0001-75", bn="Angá Crédito Estruturado FIC FIM CP"),
    "BR0NXPCTF008": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Multimercados", cnpj="59.194.673/0001-72", bn="UBS Evolution Gold Nasdaq", rev="confirmar classe CVM"),
    "BR0DB7CTF009": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Private Equity", cnpj="47.155.574/0001-00", bn="Spectra VI Latam Pro FIC FIP Mult RL"),
    "BR0CYVCTF015": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Private Equity", cnpj="43.120.902/0001-74", bn="Patria Private Equity VII Advisory FIP - B"),
    "BR0AA5CTF007": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Direitos Creditórios", bn="Estímulo FIDC SR4", rev="CNPJ não localizado — confirmar"),
    "BR07JICTF009": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Multimercados", cnpj="40.212.899/0001-20", bn="Trend Bolsas Emergentes FIM"),
    "BR05AZCTF006": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Renda Fixa", cnpj="37.910.132/0001-60", bn="Trend INB FIC FIRF Simples", rev="CNPJ provável (há variantes RICO/V) — confirmar"),
    "BR0308CTF009": dict(st="brazilianFund", cur="BRL", ctry="BR", cvm="Renda Fixa", cnpj="34.475.424/0001-24", bn="Trend Inflação Geral FIRF"),
    # ETFs / ações estrangeiras → stockEtf (web)
    "US98149E3036": dict(st="stockEtf", tk="GLDM", cur="USD", ctry="US", bn="SPDR Gold MiniShares - GLDM"),
    "US7170811035": dict(st="stockEtf", tk="PFE", cur="USD", ctry="US", bn="Pfizer - PFE"),
    "US4642888360": dict(st="stockEtf", tk="IHE", cur="USD", ctry="US", bn="iShares US Pharmaceuticals - IHE"),
    "US4642876555": dict(st="stockEtf", tk="IWM", cur="USD", ctry="US", bn="iShares Russell 2000 - IWM"),
    "US4642872349": dict(st="stockEtf", tk="EEM", cur="USD", ctry="US", bn="iShares MSCI Emerging Markets - EEM"),
    "US46090E1038": dict(st="stockEtf", tk="QQQ", cur="USD", ctry="US", bn="Invesco QQQ Trust - QQQ"),
    "US37954Y6730": dict(st="stockEtf", tk="PAVE", cur="USD", ctry="US", bn="Global X US Infrastructure Development - PAVE"),
    "US30303M1027": dict(st="stockEtf", tk="META", cur="USD", ctry="US", bn="Meta Platforms - META"),
    "IE00B6R52259": dict(st="stockEtf", tk="SSAC", cur="USD", ctry="IE", bn="iShares MSCI ACWI UCITS - SSAC"),
    "IE000YYE6WK":  dict(st="stockEtf", tk="DFNS", cur="USD", ctry="IE", isin_fix="IE000YYE6WK5", bn="VanEck Defense UCITS - DFNS", rev="ISIN corrigido p/ IE000YYE6WK5"),
    "IE0002PG6CA6": dict(st="stockEtf", tk="REMX", cur="USD", ctry="IE", bn="VanEck Rare Earth & Strategic Metals UCITS - REMX"),
    # Fundos offshore → fund/mutualFund (web: todos USD)
    "USG6S30B7606": dict(st="fund", ty="mutualFund", cur="USD", bn="Neuberger Berman Short Duration EMD - Class I"),
    "LU2630425226": dict(st="fund", ty="mutualFund", cur="USD", ctry="LU", bn="MS EM Debt Opportunities A USD Acc"),
    "IE00B839Y076": dict(st="fund", ty="mutualFund", cur="USD", ctry="IE", bn="Wellington Strategic European Equity D USD Acc"),
    "KYG6834P1485": dict(st="fund", ty="mutualFund", cur="USD", bn="Blue Owl SP2 - Class C", rev="private credit Cayman — confirmar fund vs privateMarket"),
    "YY0157495060": dict(st="fund", ty="mutualFund", cur="USD", bn="VG Equities Fund - Class A Series 1", rev="ISIN placeholder (YY) — confirmar ISIN/moeda reais"),
    "JKHDZ":        dict(st="fund", ty="mutualFund", cur="USD", bn="BlackRock Luxembourg S.A.", rev="identificador JKHDZ inválido — confirmar fundo/ISIN real"),
    # Bonds US → bond/fixed (cupom+venc do nome)
    "US95000U3B74": dict(st="bond", ty="fixed", cur="USD", ctry="US", idx="fixed", y=4.897, mat="2033-07-25", bn="Wells Fargo - 4,90% - 25/Jul/2033"),
    "US80282KBM71": dict(st="bond", ty="fixed", cur="USD", ctry="US", idx="fixed", y=5.353, mat="2030-09-06", bn="Santander Holdings - 5,35% - 06/Set/2030"),
    "US61747YEU55": dict(st="bond", ty="fixed", cur="USD", ctry="US", idx="fixed", y=4.889, mat="2033-07-20", bn="Morgan Stanley - 4,89% - 20/Jul/2033"),
    "US37045VAU44": dict(st="bond", ty="fixed", cur="USD", ctry="US", idx="fixed", y=6.80, mat="2027-10-01", bn="General Motors - 6,80% - 01/Out/2027"),
    "US912797UP00": dict(st="sovereignBonds", ty="treasuryNote", cur="USD", ctry="US", y=0.0, mat="2026-07-14", bn="US Treasury - 0,00% - 14/Jul/2026"),
    # Structured notes (XS, privadas) → otc/structuredNote
    "XS2999202752": dict(st="otc", ty="structuredNote", cur="USD", mat="2027-04-19", bn="BNP UNC Auto - 19/Abr/2027", rev="nota privada — sem lookup público"),
    "XS2986286271": dict(st="otc", ty="structuredNote", cur="USD", y=6.55, mat="2030-01-07", bn="CLN Oracle - 6,55% - 07/Jan/2030", rev="nota privada"),
    "XS2786965702": dict(st="otc", ty="structuredNote", cur="USD", mat="2029-03-20", bn="CP Note SPY - 20/Mar/2029", rev="nota privada"),
    "XS2730623431": dict(st="otc", ty="structuredNote", cur="USD", y=7.00, mat="2029-02-06", bn="CLN Brazil - 7,00% - 06/Fev/2029", rev="nota privada"),
    "XS2706980005": dict(st="otc", ty="structuredNote", cur="USD", mat="2029-03-22", bn="CA Up Note SPY - 22/Mar/2029", rev="nota privada"),
    "XS2669980588": dict(st="otc", ty="structuredNote", cur="USD", mat="2029-01-03", bn="BNP Rainbow SPX TPX - 03/Jan/2029", rev="nota privada"),
}

# Overrides p/ os dois ISINs duplicados (decisão do usuário)
DUP_CHOICE = {
    "BRRBRACRA470": "CRA BTG PACTUAL 103,50% CDI 16/11/33",   # 103,5% CDI
    "BRRBRACRA4A3": "CRA BTG PACTUAL 12,57% 16/11/33",        # único 12,57%
}


def build_entry(name, isin):
    pre = isin[:2].upper()
    e = {"beehusName": name, "securityType": None,
         "subscriptionSettlementDays": 0, "subscriptionNAVDays": 0,
         "redemptionNAVDays": 0, "redemptionSettlementDays": 0,
         "currency": "BRL" if pre == "BR" else "USD",
         "walletIds": [], "companyIds": [], "feederIds": []}
    conf, obs = "alta", []

    v = V.get(isin)
    if v:
        e["securityType"] = v["st"]
        if v.get("ty"):
            e["type"] = v["ty"]
        e["currency"] = v.get("cur", e["currency"])
        if v.get("ctry"):
            e["country"] = v["ctry"]
        if v.get("mat"):
            e["maturityDate"] = v["mat"]
        if v.get("idx"):
            e["indexer"] = v["idx"]
        if v.get("idxp") is not None:
            e["indexerPercentual"] = v["idxp"]
        if v.get("y") is not None:
            e["yield"] = v["y"]
        if v.get("tk"):
            e["ticker"] = v["tk"]
        if v.get("cvm"):
            e["cvmKlass"] = v["cvm"]
        if v.get("cnpj"):
            e["mainId"] = v["cnpj"]
            e["taxId"] = v["cnpj"]
        e["isIn"] = v.get("isin_fix", isin)
        # beehusName: deb usa rate_label; resto usa bn explícito
        if v["st"] == "bond" and v.get("ty") in ("debenture", "infrastructureDebenture"):
            lbl = rate_label(v["idx"], v.get("y"), v.get("idxp"))
            e["beehusName"] = f"Debênture {v['dev']} {lbl} {date_pt(v['mat'])}".strip()
        elif v.get("bn"):
            e["beehusName"] = v["bn"]
        conf = "media" if v.get("rev") else "alta"
        if v.get("rev"):
            obs.append(v["rev"])
        return e, conf, obs

    # CRI / CRA / LCI — derivado do nome
    up = name.upper()
    m = re.match(r"^(CRI|CRA|LCI)\b", up)
    if m:
        kind = m.group(1).lower()
        idx, y, idxp = parse_rate(name)
        mat = parse_date(name)
        dev = devedor(name)
        e["securityType"] = "bond"; e["type"] = kind; e["country"] = "BR"
        e["maturityDate"] = mat; e["isIn"] = isin
        if idx:
            e["indexer"] = idx
        if idxp is not None:
            e["indexerPercentual"] = idxp
        if y is not None:
            e["yield"] = y
        lbl = rate_label(idx, y, idxp)
        e["beehusName"] = f"{kind.upper()} {dev} {lbl} {date_pt(mat)}".strip()
        return e, "alta", obs

    # fallback (não deveria ocorrer)
    e["securityType"] = "?"; e["isIn"] = isin
    return e, "baixa", ["NÃO classificado — revisar"]


# ── Dedup por ISIN, aplicando escolha do usuário p/ os 2 duplicados ─────────
seen, out, table = set(), [], []
for it in nao_cad:
    isin = (it["isin"] or "").strip()
    if isin in seen:
        continue
    seen.add(isin)
    name = DUP_CHOICE.get(isin, it["security"])
    e, conf, obs = build_entry(name, isin)
    out.append(e)
    table.append({"isin": isin, "sourceName": it["security"], "beehusName": e["beehusName"],
                  "securityType": e["securityType"], "type": e.get("type", ""),
                  "currency": e["currency"], "country": e.get("country", ""),
                  "maturityDate": e.get("maturityDate", ""), "indexer": e.get("indexer", ""),
                  "indexerPercentual": e.get("indexerPercentual", ""), "yield": e.get("yield", ""),
                  "ticker": e.get("ticker", ""), "cvmKlass": e.get("cvmKlass", ""),
                  "cnpj": e.get("mainId", ""),
                  "isIn": e.get("isIn", ""), "conf": conf, "obs": "; ".join(obs)})

json.dump(out, open(BASE / "data/temp/cadastro_novos_registration.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
json.dump(table, open(BASE / "data/temp/cadastro_novos_revisao.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)

c = Counter((t["securityType"], t["type"]) for t in table)
print("total distintos:", len(table))
for k, v in c.most_common():
    print(f"  {v:3d}  {k}")
print()
for t in table:
    flag = "  ⚠" if t["conf"] != "alta" else "   "
    print(f"{flag} {t['isin']:14} {str(t['securityType']):14} {str(t['type']):20} {t['currency']:3} {str(t['country']):3} {str(t['maturityDate']):10} | {t['beehusName']}")
    if t["obs"]:
        print(f"        ↳ {t['obs']}")
