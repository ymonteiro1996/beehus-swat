"""Unit tests for asset_registration_rules.py.

Offline, no network/token needed. Checks against the concrete, already-validated
examples in reference/beehusName-regra-final.md and reference/beehusName-regras.md
(the primary, precedence-1 source for this feature) wherever available, falling
back to REGRAS_BEEHUSNAME.md examples for families the primary source doesn't
cover (Compromissada).

Run: python scripts/test_asset_registration_rules.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asset_registration_rules as r

failures = []


def check(label, actual, expected):
    if actual != expected:
        failures.append(f"{label}: esperado {expected!r}, obtido {actual!r}")


def check_raises(label, fn):
    try:
        fn()
        failures.append(f"{label}: esperava RegistrationPending, não levantou nada")
    except r.RegistrationPending:
        pass


# ── title_br / canonicalização ───────────────────────────────────────────────
check("title_br acronym+preposition", r.title_br("BANCO XP S.A."), "Banco XP S.A.")
check("title_br roman numeral", r.title_br("CORP LOG III"), "Corp Log III")
check("get_short_emissor tabela (XP)", r.get_short_emissor("BANCO XP"), "XP")
check("get_short_emissor tabela (Banco Master, mantém 'Banco')", r.get_short_emissor("BANCO MASTER"), "Banco Master")
check("get_short_emissor fallback (sem tabela)", r.get_short_emissor("BANCO OMEGA"), "Omega")
check("get_devedor_canonical tabela (Allos)", r.get_devedor_canonical("ALLOS"), "Allos")
check("get_devedor_canonical fallback (sem tabela)", r.get_devedor_canonical("ENGIE"), "Engie")

# ── beehusName-regra-final.md §4.2 — CDB/LCA/LCI/CDCA (via _BANK_CANONICALS) ─
name, payload = r.build_renda_fixa_simples(
    "CDB", "CDB Pan IPCA+5.55% 24/03/2027",
    {"issuer": "PAN", "maturity_date": "2027-03-24",
     "maturity_day_specified": True, "indexer": "IPCA"},
)
check("CDB Pan (doc real)", name, "CDB Pan 24/Mar/2027")
check("CDB Pan indexerPercentual", payload["indexerPercentual"], 100)
check("CDB Pan yield", payload["yield"], 5.55)
check("CDB Pan type", payload["type"], "cdb")

name, payload = r.build_renda_fixa_simples(
    "CDB", "CDB BTG Pactual 100.72%CDI 16/07/2029",
    {"issuer": "BTG PACTUAL", "maturity_date": "2029-07-16",
     "maturity_day_specified": True, "indexer": "CDI"},
)
check("CDB BTG Pactual (doc real)", name, "CDB BTG Pactual 16/Jul/2029")
check("CDB BTG Pactual indexerPercentual", payload["indexerPercentual"], 100.72)

# O símbolo do exemplo real do doc não traz taxa numérica ("Pré-fixado" sem
# %) — na prática o texto bruto do custodiante sempre traz a taxa (é campo
# obrigatório pro yield); simulamos isso aqui mantendo o beehusName esperado
# idêntico ao do doc (a taxa nunca aparece no nome de qualquer forma).
name, payload = r.build_renda_fixa_simples(
    "CDB", "CDB Banco Master Pré-fixado 15.00% 22/02/2029",
    {"issuer": "BANCO MASTER", "maturity_date": "2029-02-22",
     "maturity_day_specified": True, "indexer": "PRE"},
)
check("CDB Banco Master pré (doc real)", name, "CDB Banco Master Pré 22/Fev/2029")

name, payload = r.build_renda_fixa_simples(
    "LCI", "LCI CEF 96,50%CDI 15/03/2027",
    {"issuer": "CEF", "maturity_date": "2027-03-15",
     "maturity_day_specified": True, "indexer": "CDI"},
)
check("LCI CEF (doc real)", name, "LCI CEF 15/Mar/2027")
check("LCI CEF type", payload["type"], "lci")

name, payload = r.build_renda_fixa_simples(
    "LCA", "LCA Itaú 95% CDI 21/10/2027",
    {"issuer": "ITAÚ", "maturity_date": "2027-10-21",
     "maturity_day_specified": True, "indexer": "CDI"},
)
check("LCA Itaú (doc real)", name, "LCA Itaú 21/Out/2027")

name, payload = r.build_renda_fixa_simples(
    "CDCA", "CDCA Vamos IPCA + 7.91% 15/09/2031",
    {"issuer": "VAMOS", "maturity_date": "2031-09-15",
     "maturity_day_specified": True, "indexer": "IPCA"},
)
check("CDCA Vamos (doc real)", name, "CDCA Vamos 15/Set/2031")
check("CDCA type (mesmo securityType bond, type=cd)", payload["type"], "cd")

check_raises("CDB sem emissor", lambda: r.build_renda_fixa_simples(
    "CDB", "CDB 105% CDI 05/04/2027",
    {"maturity_date": "2027-04-05", "indexer": "CDI"}))

# ── LF (mesma fórmula "sem taxa" de CDB) ────────────────────────────────────
name, payload = r.build_lf(
    "LF BANCO OMEGA 100% CDI 20/09/2028",
    {"issuer": "BANCO OMEGA", "maturity_date": "2028-09-20",
     "maturity_day_specified": True, "indexer": "CDI"},
)
check("LF beehusName (sem taxa/label)", name, "LF Omega 20/Set/2028")
check("LF type", payload["type"], "lf")

name, payload = r.build_lf(
    "LF BANCO OMEGA 100% CDI 20/09/2028",
    {"issuer": "BANCO OMEGA", "maturity_date": "2028-09-20",
     "maturity_day_specified": True, "indexer": "CDI"},
    subordinada=True,
)
check("LF-sub type", payload["type"], "lf-sub")

# ── LIG (regra unificada — mesma fórmula de CDB, com emissor, sem taxa) ─────
name, payload = r.build_renda_fixa_simples(
    "LIG", "LIG BRADESCO 11.25% 10/07/2029",
    {"issuer": "BRADESCO", "maturity_date": "2029-07-10",
     "maturity_day_specified": True, "indexer": "PRE"},
)
check("LIG beehusName (sem taxa, com emissor)", name, "LIG Bradesco Pré 10/Jul/2029")
check("LIG type", payload["type"], "lig")

# ── §4.2 NP (sem emissor, sem taxa) ──────────────────────────────────────────
name, payload = r.build_np(
    "NP 13.00% 30/03/2026",
    {"maturity_date": "2026-03-30", "maturity_day_specified": True, "indexer": "PRE"},
)
check("NP beehusName (pré, sem taxa)", name, "NP Pré 30/Mar/2026")
check("NP type", payload["type"], "np")

name, payload = r.build_np(
    "NP 100% CDI 30/03/2026",
    {"maturity_date": "2026-03-30", "maturity_day_specified": True, "indexer": "CDI"},
)
check("NP beehusName (pós, sem taxa)", name, "NP 30/Mar/2026")

check_raises("renda fixa simples instrumento inválido", lambda: r.build_renda_fixa_simples(
    "XYZ", "XYZ 10% 01/01/2030", {"issuer": "X", "maturity_date": "2030-01-01", "indexer": "PRE"}))

# ── beehusName-regra-final.md §4.3 — CRI/CRA (via _DEVEDOR_CANONICALS) ──────
name, payload = r.build_renda_fixa_simples(
    "CRI", "CRI Allos 105%CDI 16/04/2029",
    {"issuer": "ALLOS", "maturity_date": "2029-04-16",
     "maturity_day_specified": True, "indexer": "CDI"},
)
check("CRI Allos (doc real)", name, "CRI Allos 16/Abr/2029")
check("CRI type", payload["type"], "cri")

name, payload = r.build_renda_fixa_simples(
    "CRI", "CRI Corp Log III Pós-fixado 100% CDI 15/09/2028",
    {"issuer": "CORP LOG III", "maturity_date": "2028-09-15",
     "maturity_day_specified": True, "indexer": "CDI"},
)
check("CRI Corp Log III — devedor fora da tabela + numeral romano (doc real)", name, "CRI Corp Log III 15/Set/2028")

name, payload = r.build_renda_fixa_simples(
    "CRI", "CRI Solfacil Pré-fixado 12.00% 07/06/2032",
    {"issuer": "SOLFACIL", "maturity_date": "2032-06-07",
     "maturity_day_specified": True, "indexer": "PRE"},
)
check("CRI Solfacil pré (doc real)", name, "CRI Solfacil Pré 07/Jun/2032")

name, payload = r.build_renda_fixa_simples(
    "CRA", "CRA SLC CDI+0,60% 15/07/2031",
    {"issuer": "SLC", "maturity_date": "2031-07-15",
     "maturity_day_specified": True, "indexer": "CDI"},
)
check("CRA SLC (doc real)", name, "CRA SLC 15/Jul/2031")
check("CRA type", payload["type"], "cra")

# ── beehusName-regra-final.md §4.4 — Debênture (prefixo DEB, devedor) ───────
name, payload = r.build_renda_fixa_simples(
    "DEBENTURE", "Debênture Vamos IPCA + 5.55% - 15/10/2031",
    {"issuer": "VAMOS", "maturity_date": "2031-10-15",
     "maturity_day_specified": True, "indexer": "IPCA"},
)
check("Debênture Vamos -> prefixo DEB (doc real)", name, "DEB Vamos 15/Out/2031")
check("Debênture type (não infra)", payload["type"], "debenture")

name, payload = r.build_renda_fixa_simples(
    "DEB", "Debênture Engie Pré-fixado 9.50% - 15/08/2029",
    {"issuer": "ENGIE", "maturity_date": "2029-08-15",
     "maturity_day_specified": True, "indexer": "PRE"},
)
check("Debênture Engie pré (doc real)", name, "DEB Engie Pré 15/Ago/2029")

name, payload = r.build_renda_fixa_simples(
    "DEB", "Debênture Infraestrutura Incentivada Vamos IPCA + 5.55% - 15/10/2031",
    {"issuer": "VAMOS", "maturity_date": "2031-10-15",
     "maturity_day_specified": True, "indexer": "IPCA"},
)
check("Debênture incentivada -> infrastructureDebenture", payload["type"], "infrastructureDebenture")

# ── §4.6 Compromissada (gap-fill, sem conflito) ─────────────────────────────
name, payload = r.build_compromissada("Compromissada 90% CDI CRA024006N4")
check("Compromissada beehusName", name, "Compromissada - 90% CDI - CRA024006N4")
check("Compromissada ticker", payload["ticker"], "COMP90CDICRA024006N4")
check("Compromissada maturityDate placeholder", payload["maturityDate"], "2099-01-01")
check("Compromissada indexerPercentual", payload["indexerPercentual"], 90.0)

check_raises("Compromissada sem código", lambda: r.build_compromissada("Compromissada 90% CDI"))

# Caso real confirmado ao vivo (2026-07-30): "Comp*VEÍCULO-CÓDIGO", código com
# poucos dígitos (CYAR11, não o CRA024006N4 de 6 dígitos do exemplo do skill).
check("looks_like_compromissada detecta 'Comp*'", r.looks_like_compromissada(
    "AMBIENTAL CEARA 1 SPE S.A. - Comp*DEB-CYAR11 - 85%CDI - 17/11/2026"), True)
check("looks_like_compromissada não falso-positiva", r.looks_like_compromissada("CDB Banco XP 105% CDI 05/04/2027"), False)

name, payload = r.build_compromissada("AMBIENTAL CEARA 1 SPE S.A. - Comp*DEB-CYAR11 - 85%CDI - 17/11/2026")
check("Compromissada código curto (Comp*DEB-CYAR11)", name, "Compromissada - 85% CDI - CYAR11")
check("Compromissada código curto ticker", payload["ticker"], "COMP85CDICYAR11")

# ── beehusName-regra-final.md §4.5 — stockEtf BR (a partir do nome já limpo) ─
check("stockEtf BR (Bradesco - PN)", r.build_stock_etf_name_br("BBDC4", "Bradesco PN"), "BBDC4 - Bradesco PN")
check("stockEtf BR (Kinea FII)", r.build_stock_etf_name_br("KNHY11", "Kinea High Yield FII"), "KNHY11 - Kinea High Yield FII")
check("stockEtf BR (Kinea FIDC nao move)", r.build_stock_etf_name_br("KNCE11", "Kinea Crédito Estruturado FIDC"), "KNCE11 - Kinea Crédito Estruturado FIDC")

# ── Report ───────────────────────────────────────────────────────────────
if failures:
    print(f"[FAIL] {len(failures)} teste(s) falharam:")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("[ok] todos os testes de asset_registration_rules.py passaram")
