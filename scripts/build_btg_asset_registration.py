"""Constrói tabela de cadastro a partir de rawBTGPosition (companyId+date).

Aplica o padrão de beehusName definido em templates/controlpanel.html
(modal "Cadastrar ativos" — funções _parseRate / _formatRateForBeehusName /
_buildCriCraBeehusName / _buildDebentureBeehusName / _titleCaseEmissor).

Saídas:
  data/temp/btg_registration_<company>_<date>.md   (tabela legível)
  data/temp/btg_registration_<company>_<date>.json (payload pronto para POST
                                                    /api/.../register-securities ou
                                                    download via "Gerar JSON Cadastro")
"""
from __future__ import annotations

import io
import json
import re
import sys
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

if getattr(sys.stdout, "encoding", "").lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if getattr(sys.stderr, "encoding", "").lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402

COMPANY_ID = "58454495000109"
CONSUME_DATE = "2026-05-21"

# ── Tabelas canônicas (extraídas de templates/controlpanel.html §14/§16) ────
# Forma curta: lista (canonical, [patterns]). Primeiro padrão que casar vence.

_BANK_CANONICALS = [
    ("Banco C6", ["BANCO C6 CONSIGNADO S.A.", "BANCO C6 CONSIGNADO", "BANCO C6 PRÉ", "BANCO C6 PÓS", "BANCO C6", "C6 BANK", "C6"]),
    ("Bradesco", ["BANCO BRADESCO S.A.", "BANCO BRADESCO", "BRADESCO"]),
    ("BTG Pactual", ["BANCO BTG PACTUAL S.A", "BANCO BTG PACTUAL SA", "BANCO BTG PACTUAL", "BTG PACTUAL"]),
    ("Itaú", ["ITAU", "ITAÚ"]),
    ("BMG", ["BANCO BMG S.A", "BANCO BMG", "BMG"]),
    ("Pine", ["BANCO PINE", "PINE"]),
    ("Caixa Econômica", ["CAIXA ECONOMICA", "CAIXA ECONÔMICA"]),
    ("Safra", ["BANCO SAFRA", "SAFRA"]),
    ("Original", ["BANCO ORIGINAL S/A", "BANCO ORIGINAL", "ORIGINAL"]),
    ("BNDES", ["BNDES"]),
    ("XP", ["BANCO XP S.A.", "BANCO XP", "XP"]),
    ("Pan", ["BANCO PAN S/A", "BANCO PAN", "PAN"]),
    ("Banco ABC", ["BANCO ABC", "ABC"]),
    ("Santander", ["BANCO SANTANDER", "SANTANDER"]),
    ("Bocom BBM", ["BANCO BOCOM BBM SA", "BANCO BOCOM BBM", "BOCOM BBM"]),
    ("Agibank", ["BANCO AGIBANK S.A", "BANCO AGIBANK", "AGIBANK"]),
    ("Daycoval", ["BANCO DAYCOVAL", "DAYCOVAL"]),
    ("Fibra", ["BANCO FIBRA SA", "BANCO FIBRA", "FIBRA"]),
    ("BS2", ["BANCO BS2", "BS2"]),
    ("BV", ["BANCO BV S/A", "BANCO BV", "BV S.A", "BV"]),
    ("Master", ["BANCO MASTER S/A", "BANCO MASTER", "MASTER"]),
    ("Inter", ["BANCO INTER S/A", "BANCO INTER", "INTER"]),
    ("Banco do Brasil", ["BANCO DO BRASIL"]),
    ("Sofisa", ["BANCO SOFISA S.A.", "BANCO SOFISA", "SOFISA"]),
    ("Rendimento", ["BANCO RENDIMENTO S.A.", "BANCO RENDIMENTO"]),
    ("Andbank", ["BANCO ANDBANK", "ANDBANK"]),
    ("Votorantim", ["BANCO VOTORANTIM", "VOTORANTIM"]),
    ("Nubank", ["BANCO NUBANK", "NUBANK"]),
    ("Sicoob", ["SICOOB"]),
    ("Sicredi", ["BANCO SICREDI", "SICREDI"]),
    ("Banestes", ["BANCO BANESTES S.A.", "BANCO BANESTES", "BANESTES"]),
    ("Industrial", ["BANCO INDUSTRIAL DO BRASIL S.A.", "BANCO INDUSTRIAL DO BRASIL"]),
    ("Triângulo", ["BANCO TRIANGULO S/A", "BANCO TRIANGULO", "TRIANGULO"]),
    ("BNB", ["BANCO DO NORDESTE DO BRASIL S.A.", "BANCO DO NORDESTE", "BNB"]),
    ("Pleno", ["BANCO PLENO SA", "PLENO"]),
    ("BRB", ["BRB"]),
    ("Banco BBM", ["BANCO BBM", "BBM"]),
    ("Stellantis", ["STELLANTIS"]),
    ("Rabobank", ["BANCO RABOBANK", "RABOBANK"]),
    ("BRDE", ["BANCO BRDE", "BRDE"]),
    ("Picpay", ["PICPAY"]),
    ("Genial", ["GENIAL"]),
]

_DEVEDOR_CANONICALS = [
    ("Petrobrás", ["PETROLEO BRASILEIRO S A PETROBRAS", "PETRÓLEO BRASILEIRO S.A", "PETROBRAS"]),
    ("Rede D'Or", ["REDE D'OR SÃO LUIZ S.A", "REDE D'OR", "REDE DOR"]),
    ("Vale", ["VALE"]),
    ("Klabin", ["KLABIN"]),
    ("Eletrobras", ["ELETROBRAS", "ELETROBRÁS"]),
    ("Energisa", ["ENERGISA"]),
    ("Sabesp", ["SABESP"]),
    ("Vibra Energia S.A.", ["VIBRA ENERGIA S.A"]),
    ("Itaú", ["ITAÚ", "ITAU"]),
    ("BTG Pactual", ["BANCO BTG PACTUAL", "BTG PACTUAL"]),
    ("BTG", ["BTG"]),
    ("Localiza", ["LOCALIZA"]),
    ("Movida", ["MOVIDA"]),
    ("Vamos", ["VAMOS"]),
    ("Light", ["LIGHT"]),
    ("Eneva", ["ENEVA"]),
    ("Taesa", ["TAESA"]),
    ("Iguatemi", ["IGUATEMI"]),
    ("Aliansce Sonae", ["ALIANSCE SONAE"]),
    ("Multiplan", ["MULTIPLAN"]),
    ("Cury", ["CURY"]),
    ("MRV", ["MRV"]),
    ("Direcional", ["DIRECIONAL"]),
    ("Cyrela", ["CYRELA"]),
    ("Trisul", ["TRISUL"]),
    ("Hapvida", ["HAPVIDA"]),
    ("Dasa", ["DASA"]),
    ("Hypera", ["HYPERA"]),
    ("Cogna", ["COGNA"]),
    ("Yduqs", ["YDUQS"]),
    ("JBS", ["JBS"]),
    ("Minerva", ["MINERVA"]),
    ("Marfrig", ["MARFRIG"]),
    ("Camil", ["CAMIL"]),
    ("BRF", ["BRF"]),
    ("Seara", ["SEARA"]),
    ("Raízen", ["RAIZEN", "RAÍZEN"]),
    ("Ipiranga", ["IPIRANGA"]),
    ("Cosan", ["COSAN"]),
    ("Ultrapar", ["ULTRAPAR"]),
    ("CPFL", ["CPFL"]),
    ("Equatorial", ["EQUATORIAL"]),
    ("Engie", ["ENGIE"]),
    ("Copel", ["COPEL"]),
    ("Cemig", ["CEMIG"]),
    ("Ambev", ["AMBEV"]),
    ("Natura", ["NATURA"]),
    ("Raia Drogasil", ["RAIA DROGASIL"]),
    ("Magazine Luiza", ["MAGAZINE LUIZA", "MAGAZ"]),
    ("Bradesco", ["BRADESCO"]),
    ("Santander", ["SANTANDER"]),
    ("Banco do Brasil", ["BANCO DO BRASIL"]),
    ("Rumo", ["RUMO"]),
    ("CCR", ["CCR"]),
    ("Eco Securitizadora", ["ECO SECURITIZADORA"]),
    ("Opea Securitizadora", ["OPEA SECURITIZADORA S/A", "OPEA SECURITIZADORA S.A", "OPEA SECURITIZADORA"]),
    ("Virgo Securitizadora", ["VIRGO COMPANHIA DE SECURITIZAÇÃO", "VIRGO"]),
    ("True Securitizadora", ["TRUE SECURITIZADORA"]),
    ("Bari Securitizadora", ["BARI SECURITIZADORA"]),
    ("Riza Securitizadora", ["RIZA SECURITIZADORA"]),
    ("Mercado Livre", ["MERCADO LIVRE"]),
    ("BR Foods", ["BR FOODS"]),
    ("Assaí", ["ASSAI", "ASSAÍ"]),
    ("Atacadão", ["ATACADÃO", "ATACADAO"]),
    ("GPA", ["GPA"]),
    ("Sendas", ["SENDAS"]),
]

_PT_MONTH_ABBR = ["", "Jan", "Fev", "Mar", "Abr", "Mai", "Jun",
                  "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

_PT_CONNECTORS = {"do", "de", "da", "dos", "das", "no", "na", "nos", "nas",
                  "e", "o", "a", "os", "as", "em", "para"}


# ── Acrônimos preservados em UPPER pelos smart-casers ──────────────────────
# Lista curada a partir do que aparece em fundos/previdência/COE BTG.
_ACRONYMS = {
    # Tipos de fundo
    "FI", "FIM", "FIA", "FII", "FIP", "FIE", "FIDC", "FIC", "FIF",
    "FICFI", "FICFIM", "FICFIA", "FICFII", "FICFIDC", "FICFIE", "FICFIRF",
    "FIRF", "FIREFDI", "FIRFREFDI", "FIRFREF",
    # Indexers / taxas
    "CDI", "IPCA", "SELIC", "DI", "PRE", "POS",
    # Tags de crédito / regime
    "RF", "CP", "LP", "LS", "MM", "RL", "BP", "FC", "HY", "HG", "MS",
    "PCO", "MD", "ABS", "ETF", "ESG", "EUA", "USA",
    # Produtos
    "CRA", "CRI", "CDB", "LCA", "LCI", "LF", "CCB", "LIG", "COE",
    "DEB", "DEBI", "VGBL", "PGBL", "DPGE", "LFT", "LTN",
    # Brands / acrônimos comuns
    "BTG", "XP", "BBM", "BNP", "BBI", "DTVM", "TVM",
    "ACS", "EVE", "WHG", "JGP", "JHSF", "MRV", "WA", "FY", "PIB",
    "JBS", "BRF", "MCA", "M8", "V8", "B3", "BR", "BRL", "USD", "EUR",
    "AZ", "AC", "BB", "BV", "BVR", "CT", "CV", "FN", "HE", "MO",
    "EM", "EP", "NA",  # cuidados especiais via connectors abaixo
    # Sociedades
    "SA", "S/A", "S.A.", "LTDA", "EPP",
    # Numerais romanos
    "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XV", "XX",
    # Numéricos / códigos
    "K10", "K2", "10B", "K1", "M1", "BP1", "FY1",
}
# Aviso: tokens curtos em _PT_CONNECTORS (de, do, da, e, em, ...) vencem por
# precedência mesmo se também estiverem em _ACRONYMS.


def _title_case_emissor(s: str) -> str:
    """Title-case usado nos bonds (replicando JS controlpanel.html)."""
    words = (s or "").lower().split()
    out = []
    for i, w in enumerate(words):
        if not w:
            out.append(w)
            continue
        if w in _PT_CONNECTORS:
            out.append(w if i > 0 else w[0].upper() + w[1:])
            continue
        if len(w) <= 2:
            out.append(w.upper())
            continue
        if re.fullmatch(r"s/a", w):
            out.append("S/A")
            continue
        if re.fullmatch(r"s\.?a\.?", w):
            out.append("S.A.")
            continue
        # Extensão pt-BR: tokens 3 chars sem vogal são acrônimos (CNH, BTG,
        # BNP, BBM, MRV, JBS, DTVM…). Vogais aqui inclui acentuadas.
        if 3 <= len(w) <= 5 and not re.search(r"[aeiouáéíóúâêîôûãõ]", w):
            out.append(w.upper())
            continue
        if w.upper() in _ACRONYMS:
            out.append(w.upper())
            continue
        out.append(w[0].upper() + w[1:])
    return " ".join(out).strip()


# ── Smart title-case para nomes "livres" (fundos, COE, crypto, previdência) ─

def _is_acronym_token(t: str) -> bool:
    """True se o token deve ser preservado em UPPER."""
    if not t:
        return False
    cu = t.upper()
    if cu in _ACRONYMS:
        return True
    # contém dígitos -> código (K10, V8, 10B, M1…)
    if any(c.isdigit() for c in t):
        return True
    # token curto sem vogais -> acrônimo (BTG, CNH, BNP, BBM, DTVM…)
    if 2 <= len(t) <= 6 and t.isalpha() and not re.search(
            r"[aeiouáéíóúâêîôûãõAEIOUÁÉÍÓÚÂÊÎÔÛÃÕ]", t):
        return True
    # token 2 chars puro alfabético -> normalmente acrônimo (DI, RF, FC, MS)
    if len(t) == 2 and t.isalpha():
        return True
    return False


# Convenções de mercado em que o case canônico não é só "Title" — quando o
# upper bater, restauramos a forma canônica.
_CASE_OVERRIDES = {
    "CRPR": "CrPr",
    "REFDI": "RefDI",
    "FIREFDI": "FIRefDI",
    "FIRFREFDI": "FIRFRefDI",
    "FIRFREF": "FIRFRef",
}


def _smart_title_name(s: str) -> str:
    """Normaliza nome livre: acrônimos em UPPER, conectores em minúsculo,
    demais palavras em Title Case. Idempotente para nomes já bem formatados:
    só normaliza quando ≥50% das palavras "longas" (≥4 letras alfabéticas)
    estão em ALL-CAPS no original.
    """
    if not s:
        return s
    raw = s.strip()
    if not raw:
        return raw

    # 1. Single-token all-caps -> preserva (códigos como BNPPARIBASBM, BON0034)
    if " " not in raw and re.search(r"[A-Z]", raw) and raw == raw.upper():
        return raw

    words = raw.split()
    long_words = [w for w in words if sum(1 for c in w if c.isalpha()) >= 4]
    if long_words:
        all_upper = sum(
            1 for w in long_words
            if any(c.isalpha() for c in w) and w == w.upper()
        )
        if all_upper / len(long_words) < 0.5:
            # já está bem formatado (ex.: "BTG Tesouro Selic FIRFRefDI")
            return raw

    # 2. Tickers que aparecem entre parênteses (ex.: "XET ETHEREUM (XET)")
    #    devem ser preservados em UPPER mesmo onde aparecem fora dos parens.
    tickers_in_parens = {
        m.upper() for m in re.findall(r"\(([A-Z][A-Z0-9]*)\)", raw)
    }

    # Tokeniza preservando separadores. Grupos `(TICKER)` em ALL-CAPS são
    # capturados como UM único token para preservar o case de tickers.
    parts = re.findall(
        r"\([A-Z][A-Z0-9]*\)|[A-Za-zÀ-ÿ0-9.]+(?:/[A-Za-zÀ-ÿ0-9.]+)?|.",
        raw,
    )
    out = []
    word_seen = False
    for p in parts:
        if not re.search(r"[A-Za-zÀ-ÿ0-9]", p):
            out.append(p)
            continue
        # token "(XSO)" — preserva case original
        if p.startswith("(") and p.endswith(")") and p[1:-1] == p[1:-1].upper():
            out.append(p)
            word_seen = True
            continue
        cu = p.upper()
        cl = p.lower()
        # conectores em minúsculo no meio do nome
        if cl in _PT_CONNECTORS:
            out.append(cl if word_seen else cl[0].upper() + cl[1:])
            word_seen = True
            continue
        # S.A. / S/A
        if cl in ("s.a.", "s.a", "sa") and len(p) <= 4:
            out.append("S.A.")
            word_seen = True
            continue
        if cl == "s/a":
            out.append("S/A")
            word_seen = True
            continue
        # override de mercado (CrPr, RefDI…)
        if cu in _CASE_OVERRIDES:
            out.append(_CASE_OVERRIDES[cu])
            word_seen = True
            continue
        # token reaparece como (TICKER) — preserva upper
        if cu in tickers_in_parens:
            out.append(cu)
            word_seen = True
            continue
        # acrônimos / códigos
        if _is_acronym_token(p):
            out.append(cu)
            word_seen = True
            continue
        # default: Title Case (primeira maiúscula, resto minúsculo)
        out.append(p[0].upper() + p[1:].lower())
        word_seen = True
    result = "".join(out)
    return re.sub(r"\s{2,}", " ", result).strip()


def _lookup_canonical(raw, table):
    if not raw:
        return None
    up = str(raw).upper().strip()
    for canonical, patterns in table:
        for p in patterns:
            if p in up:
                return canonical
    return None


def _short_emissor(raw):
    c = _lookup_canonical(raw, _BANK_CANONICALS)
    if c:
        return c
    s = (raw or "").strip()
    s = re.sub(r"^BANCO\s+", "", s, flags=re.I)
    s = re.sub(r"\s+S\.?A\.?\s*$", "", s, flags=re.I)
    s = re.sub(r"\s+S/A\s*$", "", s, flags=re.I)
    return _title_case_emissor(s.strip())


def _devedor_canonical(raw):
    c = _lookup_canonical(raw, _DEVEDOR_CANONICALS)
    if c:
        return c
    return _title_case_emissor(raw)


def _date_to_pt_abbr(iso_or_ddmmyyyy):
    """Aceita 'YYYY-MM-DD' ou 'DD/MM/YYYY' e devolve 'DD/Mmm/YYYY'."""
    if not iso_or_ddmmyyyy:
        return ""
    s = str(iso_or_ddmmyyyy)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        y, mo, d = m.group(1), int(m.group(2)), m.group(3)
        return f"{d}/{_PT_MONTH_ABBR[mo]}/{y}"
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    if m:
        d, mo, y = m.group(1), int(m.group(2)), m.group(3)
        return f"{d}/{_PT_MONTH_ABBR[mo]}/{y}"
    return s


def _to_iso(date_str):
    if not date_str:
        return ""
    s = str(date_str)
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.match(r"^(\d{2})/(\d{2})/(\d{4})$", s)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return ""


def _parse_rate(rate_info):
    """Retorna (indexer, yield, indexerPercentual) replicando _parseRate()."""
    if not rate_info:
        return None, None, None
    clean = str(rate_info).strip().replace(",", ".")
    up = clean.upper()

    if "IPC-A" in up or "IPCA" in up:
        m = re.search(r"[+]?\s*(\d+(?:\.\d+)?)\s*%", clean)
        return "IPCA", (float(m.group(1)) if m else None), 100

    if "CDI" in up:
        m = re.match(r"^CDI\s*\+\s*(\d+(?:\.\d+)?)\s*%", clean, flags=re.I)
        if m:
            return "CDI", float(m.group(1)), 100
        m = re.match(r"^(\d+(?:\.\d+)?)\s*%\s*CDI", clean, flags=re.I)
        if m:
            pct = float(m.group(1))
            sp = re.search(r"CDI\s*\+\s*(\d+(?:\.\d+)?)\s*%", clean, flags=re.I)
            if sp:
                return "CDI", float(sp.group(1)), pct
            return "CDI", 0.0, pct

    if clean.startswith("+") or re.fullmatch(r"\d+(\.\d+)?%", clean):
        m = re.search(r"[+]?\s*(\d+(?:\.\d+)?)\s*%", clean)
        return "PRE", (float(m.group(1)) if m else None), None

    if "SELIC" in up:
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", clean)
        return "SELIC", (float(m.group(1)) if m else 0.0), 100

    m = re.search(r"(\d+(?:\.\d+)?)\s*%", clean)
    return "PRE", (float(m.group(1)) if m else None), None


def _fmt_rate_num(n, min_decimals=2):
    try:
        x = float(n)
    except Exception:
        return ""
    s = repr(x)
    if "." not in s:
        s += "."
    intp, fp = s.split(".")
    while len(fp) < min_decimals:
        fp += "0"
    return f"{intp}.{fp}"


def _format_rate_for_name(indexer, y, idx_pct):
    if indexer == "PRE":
        return f"{_fmt_rate_num(y)}%"
    if indexer == "IPCA":
        return f"IPCA + {_fmt_rate_num(y)}%"
    if indexer == "CDI":
        if y and float(y) > 0:
            return f"CDI + {_fmt_rate_num(y)}%"
        return f"{_fmt_rate_num(idx_pct)}%CDI"
    if indexer == "SELIC":
        if y and float(y) > 0:
            return f"SELIC + {_fmt_rate_num(y)}%"
        return f"{_fmt_rate_num(idx_pct)}%SELIC"
    return ""


def _is_infra_debenture(text):
    T = (text or "").upper()
    return bool(re.search(r"\bINFRA\b|\bINCENT\b|INCENTIVADA", T))


# ── AccountingGroupCode → tipo Beehus para FixedIncome ──────────────────────
_FI_TYPE_MAP = {
    "DEBÊNTURE": "debenture",
    "DEBENTURE": "debenture",
    "CDB": "cdb",
    "LCA": "lca",
    "LCI": "lci",
    "LF": "lf",
    "LFSC": "lf",
    "LFSN": "lf",
    "LFS": "lf",
    "CRI": "cri",
    "CRA": "cra",
    "CCB": "ccb",
    "LIG": "lig",
    "TPF": "publicBond",
    "TÍTULOS PÚBLICOS": "publicBond",
    "TITULOS PUBLICOS": "publicBond",
    "NTN-B": "publicBond",
    "LTN": "publicBond",
    "LFT": "publicBond",
    "NTN-F": "publicBond",
}


def _classify_fi(item):
    """Retorna (typeUpper, jsonType). Usa AccountingGroupCode primário."""
    ag = (item.get("AccountingGroupCode") or "").strip()
    ag_up = ag.upper()
    if ag_up in _FI_TYPE_MAP:
        jt = _FI_TYPE_MAP[ag_up]
        if jt == "debenture":
            return "DEB", jt
        return ag_up if ag_up != "DEBÊNTURE" else "DEB", jt
    # fallback via ticker
    ticker = (item.get("Ticker") or "").upper()
    for prefix, (tu, jt) in (("CDB-", ("CDB", "cdb")), ("LCA-", ("LCA", "lca")),
                              ("LCI-", ("LCI", "lci")), ("LF-", ("LF", "lf")),
                              ("CRI-", ("CRI", "cri")), ("CRA-", ("CRA", "cra")),
                              ("DEB-", ("DEB", "debenture"))):
        if ticker.startswith(prefix):
            return tu, jt
    return ag_up or "?", "bond"


def _build_fi_name(item):
    type_up, json_type = _classify_fi(item)
    issuer_raw = item.get("Issuer") or ""
    maturity = item.get("MaturityDate") or ""
    maturity_iso = _to_iso(maturity)
    date_pt = _date_to_pt_abbr(maturity_iso)
    rate_info = item.get("IndexYieldRate") or item.get("ReferenceIndexValue") or ""
    indexer, y, idx_pct = _parse_rate(rate_info)
    # Yield numérico do BTG (ex.: "3.9") quando há
    btg_yield = item.get("Yield")
    if y is None and btg_yield is not None:
        try:
            y = float(btg_yield)
        except Exception:
            pass

    if type_up in ("CDB", "LCA", "LCI", "CCB", "LIG"):
        emissor = _short_emissor(issuer_raw)
        rate_fmt = _format_rate_for_name(indexer, y, idx_pct)
        name = f"{type_up} {emissor} {rate_fmt} {date_pt}".strip()
        name = re.sub(r"\s+", " ", name)
        return name, json_type, emissor, indexer, y, idx_pct, maturity_iso
    if type_up == "LF":
        emissor = _short_emissor(issuer_raw)
        label = "Pré-fixado" if indexer == "PRE" else "Pós-fixado"
        name = f"LF {emissor} {label} {date_pt}".strip()
        return name, json_type, emissor, indexer, y, idx_pct, maturity_iso
    if type_up in ("CRI", "CRA"):
        emissor = _devedor_canonical(issuer_raw)
        label = "Pré-fixado" if indexer == "PRE" else "Pós-fixado"
        name = f"{type_up} {emissor} {label} {date_pt}".strip()
        return name, json_type, emissor, indexer, y, idx_pct, maturity_iso
    if type_up == "DEB":
        emissor = _devedor_canonical(issuer_raw)
        label = "Pré-fixado" if indexer == "PRE" else "Pós-fixado"
        rate_fmt = _format_rate_for_name(indexer, y, idx_pct)
        # checa infraestrutura via descrição/ticker
        is_infra = _is_infra_debenture(
            f"{issuer_raw} {item.get('Ticker','')} {item.get('IssuerType','')}"
        )
        json_type_final = "infrastructureDebenture" if is_infra else "debenture"
        name = f"Debênture {emissor} {rate_fmt} {label} {date_pt}".strip()
        name = re.sub(r"\s+", " ", name)
        return name, json_type_final, emissor, indexer, y, idx_pct, maturity_iso
    if json_type == "publicBond":
        # TPF — usar Ticker como nome (LFT/LTN/NTN-B/NTN-F já vêm no ticker)
        ticker = item.get("Ticker") or ""
        return ticker or f"TPF {date_pt}", "publicBond", "Tesouro Nacional", indexer, y, idx_pct, maturity_iso
    # fallback genérico
    emissor = _title_case_emissor(issuer_raw)
    rate_fmt = _format_rate_for_name(indexer, y, idx_pct) if indexer else ""
    name = f"{type_up} {emissor} {rate_fmt} {date_pt}".strip()
    return re.sub(r"\s+", " ", name), json_type, emissor, indexer, y, idx_pct, maturity_iso


def _build_fund_name(item):
    fund = item.get("Fund") or {}
    name = _smart_title_name(fund.get("FundName") or "")
    return name, "fund", fund.get("FundCNPJCode"), fund.get("SecurityCode")


def _build_coe_name(item):
    # FixedIncomeStructuredNote
    issuer = item.get("Issuer") or ""
    fantasy = _smart_title_name(item.get("FantasyName") or "")
    maturity_iso = _to_iso(item.get("MaturityDate"))
    date_pt = _date_to_pt_abbr(maturity_iso)
    rate_info = item.get("ReferenceIndexValue") or ""
    indexer, y, idx_pct = _parse_rate(rate_info)
    base = fantasy or _short_emissor(issuer) or "COE"
    return f"COE {base} {date_pt}".strip(), "coe", indexer, y, idx_pct, maturity_iso


def _build_pension_name(item, pos):
    fname = _smart_title_name(pos.get("FundName") or "")
    susep = pos.get("SusepCode") or item.get("SusepCode") or ""
    fund_type = pos.get("FundType") or item.get("FundType") or ""
    base = fname or f"{fund_type} {susep}".strip()
    return base, "pension", pos.get("FundCode"), pos.get("FundCGECode")


def _build_crypto_name(item):
    a = item.get("Asset") or {}
    name = _smart_title_name(a.get("Name") or "")
    code = a.get("Code") or ""
    return name or f"Crypto {code}", "crypto"


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    coll = db["rawBTGPosition"]
    q = {"companyId": COMPANY_ID, "consumeDate": CONSUME_DATE}
    docs = list(coll.find(q))
    print(f"docs encontrados: {len(docs)}")

    # rows[key] = {payload + wallets[]}
    rows = OrderedDict()

    def add(key, payload, wallet):
        if not key:
            return
        if key not in rows:
            rows[key] = {"_wallets": set(), **payload}
        rows[key]["_wallets"].add(wallet)

    for d in docs:
        wallet = d.get("walletCode") or ""
        p = d.get("position") or {}

        for fi in (p.get("FixedIncome") or []):
            if not isinstance(fi, dict):
                continue
            name, jt, emissor, indexer, y, idx_pct, m_iso = _build_fi_name(fi)
            key = (
                "FI",
                fi.get("SecurityCode") or fi.get("Ticker") or name,
                jt,
            )
            add(key, {
                "category": "FixedIncome",
                "beehusName": name,
                "securityType": "bond",
                "type": jt,
                "ticker": fi.get("Ticker") or "",
                "cetipCode": fi.get("CetipCode") or "",
                "selicCode": fi.get("SelicCode") or "",
                "isin": fi.get("ISIN") or "",
                "securityCode": fi.get("SecurityCode") or "",
                "issuerRaw": fi.get("Issuer") or "",
                "issuerShort": emissor,
                "issuerCgeCode": fi.get("IssuerCGECode") or "",
                "issueDate": _to_iso(fi.get("IssueDate")),
                "maturityDate": m_iso,
                "indexer": indexer,
                "indexerPercentual": idx_pct,
                "yield": y,
                "rawRate": fi.get("IndexYieldRate") or fi.get("ReferenceIndexValue") or "",
                "isLiquidity": fi.get("IsLiquidity") or "",
                "isRepo": fi.get("IsRepo") or "",
                "accountingGroupCode": fi.get("AccountingGroupCode") or "",
                "issuerType": fi.get("IssuerType") or "",
                "taxFree": fi.get("TaxFree") or "",
                "currency": "BRL",
                "country": "BR",
            }, wallet)

        for fnd in (p.get("InvestmentFund") or []):
            if not isinstance(fnd, dict):
                continue
            name, jt, cnpj, sec_code = _build_fund_name(fnd)
            fund = fnd.get("Fund") or {}
            key = ("FUND", sec_code or cnpj or name, jt)
            add(key, {
                "category": "InvestmentFund",
                "beehusName": name,
                "securityType": "fund",
                "type": jt,
                "ticker": "",
                "cetipCode": "",
                "isin": "",
                "selicCode": "",
                "securityCode": sec_code or "",
                "taxId": cnpj or "",
                "managerName": fund.get("ManagerName") or "",
                "benchmark": fund.get("BenchMark") or "",
                "fundLiquidityDays": fund.get("FundLiquidity") or "",
                "tipoCvm": fund.get("TipoCvm") or "",
                "issuerRaw": fund.get("FundName") or "",
                "issuerShort": fund.get("FundName") or "",
                "issueDate": "",
                "maturityDate": "",
                "indexer": None,
                "indexerPercentual": None,
                "yield": None,
                "rawRate": "",
                "currency": "BRL",
                "country": "BR",
            }, wallet)

        for coe in (p.get("FixedIncomeStructuredNote") or []):
            if not isinstance(coe, dict):
                continue
            name, jt, indexer, y, idx_pct, m_iso = _build_coe_name(coe)
            key = ("COE", coe.get("SecurityCode") or coe.get("Ticker") or name, jt)
            add(key, {
                "category": "FixedIncomeStructuredNote",
                "beehusName": name,
                "securityType": "structuredNote",
                "type": jt,
                "ticker": coe.get("Ticker") or "",
                "cetipCode": coe.get("CetipCode") or "",
                "selicCode": "",
                "isin": "",
                "securityCode": coe.get("SecurityCode") or "",
                "issuerRaw": coe.get("Issuer") or "",
                "issuerShort": _short_emissor(coe.get("Issuer") or ""),
                "issueDate": _to_iso(coe.get("IssueDate")),
                "maturityDate": m_iso,
                "indexer": indexer,
                "indexerPercentual": idx_pct,
                "yield": y,
                "rawRate": coe.get("ReferenceIndexValue") or "",
                "fantasyName": coe.get("FantasyName") or "",
                "currency": "BRL",
                "country": "BR",
            }, wallet)

        for pen in (p.get("PensionInformations") or []):
            if not isinstance(pen, dict):
                continue
            for pos in (pen.get("Positions") or []):
                if not isinstance(pos, dict):
                    continue
                name, jt, fcode, fcge = _build_pension_name(pen, pos)
                key = ("PEN", fcode or fcge or name, jt)
                add(key, {
                    "category": "PensionInformations",
                    "beehusName": name,
                    "securityType": "pension",
                    "type": jt,
                    "ticker": "",
                    "cetipCode": "",
                    "isin": "",
                    "selicCode": "",
                    "securityCode": fcode or "",
                    "fundCgeCode": fcge or "",
                    "taxId": pos.get("PensionCnpjCode") or "",
                    "susepCode": pos.get("SusepCode") or pen.get("SusepCode") or "",
                    "fundType": pos.get("FundType") or pen.get("FundType") or "",
                    "taxRegime": pos.get("TaxRegime") or pen.get("TaxRegime") or "",
                    "issuerRaw": pos.get("FundName") or "",
                    "issuerShort": pos.get("FundName") or "",
                    "issueDate": "",
                    "maturityDate": "",
                    "indexer": None,
                    "indexerPercentual": None,
                    "yield": None,
                    "rawRate": "",
                    "currency": "BRL",
                    "country": "BR",
                }, wallet)

        for cr in (p.get("CryptoCoin") or []):
            if not isinstance(cr, dict):
                continue
            name, jt = _build_crypto_name(cr)
            asset = cr.get("Asset") or {}
            key = ("CRYPTO", asset.get("Code") or name, jt)
            add(key, {
                "category": "CryptoCoin",
                "beehusName": name,
                "securityType": "crypto",
                "type": jt,
                "ticker": asset.get("Name") or "",
                "cetipCode": "",
                "isin": "",
                "selicCode": "",
                "securityCode": asset.get("Code") or "",
                "issuerRaw": asset.get("Name") or "",
                "issuerShort": asset.get("Name") or "",
                "issueDate": "",
                "maturityDate": "",
                "indexer": None,
                "indexerPercentual": None,
                "yield": None,
                "rawRate": "",
                "currency": "BRL",
                "country": "BR",
            }, wallet)

    # ── Match contra securities (mesma lógica do securityMapping) ─────────
    from security_matcher import get_cache
    from btg_existing_matcher import find_existing
    cache = get_cache()
    cache.ensure_loaded(db)
    print(f"securities cache: {cache.count} ativos (date={cache.loaded_date})")

    found = 0
    for r in rows.values():
        sec_id, beehus_name, matched_on = find_existing(r, cache)
        if sec_id:
            r["existingSecurityId"] = sec_id
            r["existingBeehusName"] = beehus_name
            r["matchedOn"] = matched_on
            found += 1
        else:
            r["existingSecurityId"] = None
            r["existingBeehusName"] = None
            r["matchedOn"] = None
    print(f"já cadastrados: {found} / {len(rows)} ({found*100/len(rows):.1f}%)")

    # ── Outputs ────────────────────────────────────────────────────────────
    out_dir = Path(__file__).resolve().parent.parent / "data" / "temp"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 1) MARKDOWN table
    md_path = out_dir / f"btg_registration_{COMPANY_ID}_{CONSUME_DATE}.md"
    by_cat = OrderedDict()
    for k, v in rows.items():
        by_cat.setdefault(v["category"], []).append(v)

    lines = [
        f"# rawBTGPosition — cadastro de ativos",
        f"",
        f"- companyId: `{COMPANY_ID}`",
        f"- consumeDate: `{CONSUME_DATE}`",
        f"- Ativos distintos: **{len(rows)}**",
        f"- Padrão de nome: `templates/controlpanel.html` (modal _Cadastrar ativos_)",
        f"",
    ]
    counts = ", ".join(f"{k}={len(v)}" for k, v in by_cat.items())
    lines.append(f"Por categoria: {counts}")
    lines.append("")
    lines.append(f"**Já existem na collection `securities`: {found} / {len(rows)} "
                 f"({found*100/len(rows):.1f}%)** — coluna `securityId` preenchida quando há match.")
    lines.append("")

    def _exist_cells(r):
        sid = r.get("existingSecurityId") or ""
        nm = r.get("existingBeehusName") or ""
        return sid, nm

    # FixedIncome table
    if "FixedIncome" in by_cat:
        lines.append("## FixedIncome (CDB/LCA/LCI/Debênture/CRI/CRA)")
        lines.append("")
        lines.append("| # | securityId (existente) | beehusName existente | beehusName (padrão BTG) | type | ticker | ISIN | CetipCode | Issuer (raw) | Emissor curto | indexer | idxPct | yield | maturityDate | accGroup | wallets |")
        lines.append("|--:|---|---|---|---|---|---|---|---|---|---|--:|--:|---|---|--:|")
        for i, r in enumerate(by_cat["FixedIncome"], 1):
            sid, nm = _exist_cells(r)
            lines.append(
                f"| {i} | {sid} | {nm} | {r['beehusName']} | {r['type']} | {r['ticker']} | "
                f"{r.get('isin','')} | {r.get('cetipCode','')} | {r['issuerRaw']} | {r['issuerShort']} | "
                f"{r.get('indexer','') or ''} | {r.get('indexerPercentual','') or ''} | "
                f"{r.get('yield','') if r.get('yield') is not None else ''} | "
                f"{r.get('maturityDate','')} | {r.get('accountingGroupCode','')} | {len(r['_wallets'])} |"
            )
        lines.append("")

    if "InvestmentFund" in by_cat:
        lines.append("## InvestmentFund")
        lines.append("")
        lines.append("| # | securityId (existente) | beehusName existente | beehusName (padrão BTG) | type | CNPJ | SecurityCode | Manager | Benchmark | wallets |")
        lines.append("|--:|---|---|---|---|---|---|---|---|--:|")
        for i, r in enumerate(by_cat["InvestmentFund"], 1):
            sid, nm = _exist_cells(r)
            lines.append(
                f"| {i} | {sid} | {nm} | {r['beehusName']} | {r['type']} | {r.get('taxId','')} | "
                f"{r.get('securityCode','')} | {r.get('managerName','')} | "
                f"{r.get('benchmark','')} | {len(r['_wallets'])} |"
            )
        lines.append("")

    if "FixedIncomeStructuredNote" in by_cat:
        lines.append("## FixedIncomeStructuredNote (COE)")
        lines.append("")
        lines.append("| # | securityId (existente) | beehusName existente | beehusName (padrão BTG) | ticker | CetipCode | Issuer | maturityDate | fantasyName | wallets |")
        lines.append("|--:|---|---|---|---|---|---|---|---|--:|")
        for i, r in enumerate(by_cat["FixedIncomeStructuredNote"], 1):
            sid, nm = _exist_cells(r)
            lines.append(
                f"| {i} | {sid} | {nm} | {r['beehusName']} | {r['ticker']} | {r.get('cetipCode','')} | "
                f"{r.get('issuerRaw','')} | {r.get('maturityDate','')} | "
                f"{r.get('fantasyName','')} | {len(r['_wallets'])} |"
            )
        lines.append("")

    if "PensionInformations" in by_cat:
        lines.append("## PensionInformations (VGBL/PGBL)")
        lines.append("")
        lines.append("| # | securityId (existente) | beehusName existente | beehusName (padrão BTG) | type | FundCode | SusepCode | CNPJ | FundType | TaxRegime | wallets |")
        lines.append("|--:|---|---|---|---|---|---|---|---|---|--:|")
        for i, r in enumerate(by_cat["PensionInformations"], 1):
            sid, nm = _exist_cells(r)
            lines.append(
                f"| {i} | {sid} | {nm} | {r['beehusName']} | {r['type']} | {r.get('securityCode','')} | "
                f"{r.get('susepCode','')} | {r.get('taxId','')} | "
                f"{r.get('fundType','')} | {r.get('taxRegime','')} | {len(r['_wallets'])} |"
            )
        lines.append("")

    if "CryptoCoin" in by_cat:
        lines.append("## CryptoCoin")
        lines.append("")
        lines.append("| # | securityId (existente) | beehusName existente | beehusName (padrão BTG) | SecurityCode | wallets |")
        lines.append("|--:|---|---|---|---|--:|")
        for i, r in enumerate(by_cat["CryptoCoin"], 1):
            sid, nm = _exist_cells(r)
            lines.append(
                f"| {i} | {sid} | {nm} | {r['beehusName']} | {r.get('securityCode','')} | "
                f"{len(r['_wallets'])} |"
            )
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Markdown: {md_path}")

    # 2) JSON payload no mesmo formato que `_generateRegistrationJSON()` gera.
    payload = []
    for v in rows.values():
        indexer_up = (v.get("indexer") or "").upper().replace("IPC-A", "IPCA")
        entry = {
            "beehusName": v["beehusName"],
            "securityType": v["securityType"],
            "type": v["type"],
            "subscriptionSettlementDays": 0,
            "subscriptionNAVDays": 0,
            "redemptionNAVDays": 0,
            "redemptionSettlementDays": 0,
            "currency": v.get("currency") or "BRL",
            "country": v.get("country") or "BR",
            "maturityDate": v.get("maturityDate") or "",
        }
        if indexer_up:
            entry["indexer"] = indexer_up
        if v.get("indexerPercentual") is not None:
            entry["indexerPercentual"] = v["indexerPercentual"]
        if v.get("yield") is not None:
            entry["yield"] = v["yield"]
        if v.get("ticker"):
            entry["ticker"] = v["ticker"]
        if v.get("isin"):
            entry["isIn"] = v["isin"]
        if v.get("cetipCode"):
            entry["cetipCode"] = v["cetipCode"]
        if v.get("selicCode"):
            entry["selicCode"] = v["selicCode"]
        if v.get("taxId"):
            entry["taxId"] = v["taxId"]
        if v.get("securityCode"):
            entry["sourceSecurityCode"] = v["securityCode"]
        # Match com securities collection — útil para distinguir cadastros
        # novos (sem existingSecurityId) de re-mapeamentos.
        if v.get("existingSecurityId"):
            entry["existingSecurityId"] = v["existingSecurityId"]
            entry["existingBeehusName"] = v.get("existingBeehusName") or ""
            entry["matchedOn"] = v.get("matchedOn") or ""
        entry["walletIds"] = []
        entry["companyIds"] = []
        entry["feederIds"] = []
        payload.append(entry)

    json_path = out_dir / f"btg_registration_{COMPANY_ID}_{CONSUME_DATE}.json"
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"JSON:     {json_path}")
    print(f"Total ativos distintos: {len(rows)}")


if __name__ == "__main__":
    main()
