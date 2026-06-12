"""Fundos — upload Anbima posicao-de-fundo XMLs and forward to Beehus.

Accepts both ANBIMA layouts:

  * **Layout 4.01** — `<arquivoposicao_4_01>` with a `<fundo>` block that
    contains a `<header>` (fund metadata + dtposicao + valorcota) and N
    asset blocks: `<titpublico>`, `<cotas>`, `<opcoesderiv>`, `<acoes>`,
    `<debenture>`, `<futuro>`, `<termo>`, `<aluguel>`, `<exterior>`,
    `<swap>`, plus `<caixa>` (cash) and `<provisao>` (forwarded to
    `POST /beehus/provisions`).
  * **Layout 5.0 / ISO 20022** — `<PosicaoAtivosCarteira>` with the
    `semt.003.001.04` schema. Fund identification lives in
    `BalForAcct/FinInstrmId` and positions in
    `SubAcctDtls/BalForSubAcct[]`. The BTG variant carries neither a
    cash row nor provisions inside the envelope.

The flow mirrors `pages/carteira.py`'s apply pipeline:

  1. Operator picks a `companyId`.
  2. Uploads N XML files. Each file is parsed server-side and a preview
     payload is returned with the proposed asset names, quantities, PUs,
     and provisões.
  3. Operator confirms (after picking a wallet per file — pre-selected
     automatically when the XML's fund CNPJ matches a wallet's
     `consumptionIdentifiers[].consumptionId`). Backend materialises a
     separate `unprocessedSecurityPositions` Excel per fund and posts
     it to `/beehus/financial/positions/...file`. Each `<provisao>`
     from layout 4.0 is created via `POST /beehus/provisions` after
     the upload succeeds.

Asset-naming convention — adopted from the existing
`unprocessedSecurityPositions` snapshots of the reference wallets
(`680a9ce43b2296d8612711e0` for 4.0, `680a9ce43b2296d8612711ee` for
5.0) so that fresh uploads land on the same `unprocessedId` keys the
operator already has in production. The exhaustive table lives in
`docs/FUNDOS.md`; below is a one-line summary per type.

Layout 4.0 — VALIDATED (snapshot 680a9ce43b2296d8612711e0):

  * titpublico        → `<isin>_<cusip>_<dtemissao>_<dtvencimento>`
                        (the `<compromisso>` child is metadata only and
                        does NOT spawn a separate row)
  * cotas             → `<isin>_<cnpjfundo>`
  * opcoesderiv       → `<isin>_<ativo>_<serie>_<callput>_<strike>_<dtvencimento>`
                        (qty + PU signed by `classeoperacao`: V ⇒ negative)
  * caixa             → cash row (`Caixa=Sim`) with the wallet's
                        `cashAccounts.unprocessedId` (fallback "Caixa")

Layout 4.0 — BEST-EFFORT (analogy with the validated builders, confirm
against a snapshot when a sample arrives):

  * acoes             → `<isin>_<codativo>`
  * debenture         → `<isin>_<codativo>_<dtemissao>_<dtvencimento>`
  * futuro            → `<isin>_<ativo>_<serie>_<dtvencimento>`
  * termo             → `<isin>_<codativo>_<dtoperacao>_<dtvencimento>`
  * aluguel           → `<isin>_<codativo>` (qty negative when
                        `tipotomadordoador=T`)
  * exterior          → `<isincomp>_<descrcomp>`
  * swap              → `swap_<identificador>_<dtoperacao>_<dtvencimento>`
                        (qty=0, balance = `valorcurva` ∨ `valorbase`)

Layout 5.0 — VALIDATED (snapshot 680a9ce43b2296d8612711ee), dispatched
by `OthrId[Tp.Prtry='TABELA NIVEL 1'].Id`:

  * EQUI / FUTU       → `<isin>_<desc>`
  * SHAR              → `<isin>_<cnpj>_<desc>` (generic ISIN
                        BR0000000000 is preserved)
  * GOVE              → `<isin>_<desc>_<isseDt>_<mtrtyDt>`

Layout 5.0 — BEST-EFFORT:

  * OPCO / OPTI       → `<isin>_<desc>_<C|P>_<strike>_<mtrtyDt>`
  * TERM              → `<isin>_<desc>_<mtrtyDt>`
  * DEBE / CDB / LCI / LCA / LF / LCRE / AGRO → `<isin>_<desc>_<isseDt>_<mtrtyDt>`
  * SWAP              → `swap_<isin>_<desc>_<mtrtyDt>`
  * REPO / COMP       → `<isin>_<desc>_<mtrtyDt>`
  * unknown class     → `<isin>_<desc>` (or `<isin>_<cnpj>_<desc>`
                        when a CNPJ is present, so the row stays
                        unique across funds with similar Desc)

Short positions (5.0 `ShrtLngInd=SHOR`, 4.0 `classeoperacao=V`) keep
the same `Ativo` name as their long counterparts — the upstream parser
accepts multiple rows under the same `Ativo` within a
`(Data, Carteira)` key, and the snapshot for
`680a9ce43b2296d8612711ee` shows LFT REF materialised 6 times (1 long
+ 5 short) under one name.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, jsonify, render_template, request
from openpyxl import Workbook

from beehus_api import (
    BeehusAPIError,
    BeehusAuthError,
    create_provision,
    upload_unprocessed_security_positions_file,
)
from db import company_visible, db, get_company_names, get_wallet_names

bp = Blueprint("fundos", __name__)

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")

# ──────────────────────────────────────────────────────────────────────────────
# XML namespaces for layout 5.0
# ──────────────────────────────────────────────────────────────────────────────

_NS_ANBIMA = "http://www.anbima.com.br/SchemaPosicaoAtivos"
_NS_HEADER = "urn:iso:std:iso:20022:tech:xsd:head.001.001.01"
_NS_ISO    = "urn:iso:std:iso:20022:tech:xsd:semt.003.001.04"

_NS_5 = {
    "a": _NS_ANBIMA,
    "h": _NS_HEADER,
    "i": _NS_ISO,
}

# Generic ISIN placeholder used by some custodians when the security
# has no real ISIN — treat as "no ISIN" for naming purposes.
_GENERIC_ISIN = "BR0000000000"

# Upload caps for `POST /api/fundos/parse`. The Anbima XMLs we've seen
# from BTG sit under 500 KB; we leave plenty of room (5 MB/file, 50
# files/request) before refusing the upload. These cap *parsing input*,
# not the eventual XLSX upload — the latter is bounded by the upstream
# Beehus endpoint.
_MAX_XML_BYTES_PER_FILE = 5 * 1024 * 1024  # 5 MB
_MAX_XML_FILES_PER_BATCH = 50

# Best-effort mapping for layout 4.0 `<codprov>`. Codes that fall through
# the map are forwarded with provisionType="other" and the code embedded
# in the description. The operator can edit the type in the preview UI
# before confirming.
_PROVISAO_TYPE_MAP = {
    "2":  "dividend",
    "12": "interestOnEquity",
    "13": "managementFee",
    "14": "couponInterest",
    "16": "adjustment",
    "34": "other",
}


def _safe(s):
    if not isinstance(s, str) or not _SAFE_ID_RE.match(s):
        raise ValueError(f"invalid id: {s!r}")
    return s


def _to_oid(v):
    try:
        return ObjectId(str(v))
    except (InvalidId, TypeError):
        return None


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _strip_ns(tag: str) -> str:
    """Return the local-name of `tag`, dropping any `{ns}` prefix."""
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _fmt_date_yyyymmdd(s: str | None) -> str:
    """Convert layout-4.0 dates ("YYYYMMDD") to ISO ("YYYY-MM-DD"). Returns
    empty string when input is missing or malformed — the caller decides
    whether that's a fatal error or just a "no maturity" signal."""
    if not s or not isinstance(s, str):
        return ""
    s = s.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    # 5.0 already uses ISO dates
    if len(s) == 10 and s[4] == "-" and s[7] == "-":
        return s
    return ""


def _to_float(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def _round(v, digits=8) -> float:
    try:
        return round(float(v), digits)
    except (TypeError, ValueError):
        return 0.0


def _text(el) -> str:
    """`(el.text or "").strip()` with `None`-safety for "missing element"."""
    if el is None:
        return ""
    return (el.text or "").strip()


# ──────────────────────────────────────────────────────────────────────────────
# Layout 4.0 parser
# ──────────────────────────────────────────────────────────────────────────────


def _parse_v40(root: ET.Element) -> dict:
    """Parse `<arquivoposicao_4_01>` into a normalised preview dict.

    Returns:
        {
          "format":       "4.0",
          "fundName":     str,
          "fundCnpj":     str,
          "positionDate": "YYYY-MM-DD",
          "currencyId":   "BRL",
          "rows":         [{name, quantity, pu, balance, isCash, sourceKind}, ...],
          "provisions":   [{codprov, credeb, liquidationDate, valor,
                            suggestedType, description}, ...],
        }
    """
    fundo = root.find("fundo")
    if fundo is None:
        # tolerate a flatter shape — root itself acts as the fundo
        fundo = root
    header = fundo.find("header")
    if header is None:
        raise ValueError("XML 4.0 sem bloco <header>")

    fund_name = _text(header.find("nome")) or _text(header.find("nomefundo"))
    fund_cnpj = _text(header.find("cnpj")) or _text(header.find("cnpjfundo"))
    pos_date  = _fmt_date_yyyymmdd(_text(header.find("dtposicao")))

    rows: list[dict] = []

    # titpublico — públicos (LFT/NTN-B/etc). The `<compromisso>` element
    # is intentionally *not* turned into a separate row: the production
    # snapshot for wallet 680a9ce43b2296d8612711e0 keeps the host line
    # alone (qty = qtdisponivel + qtgarantia, pu = puposicao) and the
    # compromisso metadata sits in the operação history, not in the
    # position list.
    for tp in fundo.findall("titpublico"):
        isin   = _text(tp.find("isin"))
        cusip  = _text(tp.find("cusip"))
        emi    = _fmt_date_yyyymmdd(_text(tp.find("dtemissao")))
        venc   = _fmt_date_yyyymmdd(_text(tp.find("dtvencimento")))
        pu     = _to_float(_text(tp.find("puposicao")))
        qty_d  = _to_float(_text(tp.find("qtdisponivel")))
        qty_g  = _to_float(_text(tp.find("qtgarantia")))
        qty    = qty_d + qty_g
        balance = _to_float(_text(tp.find("valorfindisp"))) \
                + _to_float(_text(tp.find("valorfinemgar")))
        rows.append({
            "name":       _bond_name(isin=isin, cusip=cusip,
                                      emi=emi, venc=venc),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(balance, 2),
            "isCash":     False,
            "sourceKind": "titpublico",
        })

    # cotas — `<isin>_<cnpjfundo>` (no descriptive name in the XML, so
    # ISIN+CNPJ alone is the snapshot's identity).
    for c in fundo.findall("cotas"):
        isin    = _text(c.find("isin"))
        cnpj_fd = _text(c.find("cnpjfundo"))
        pu      = _to_float(_text(c.find("puposicao")))
        qty_d   = _to_float(_text(c.find("qtdisponivel")))
        qty_g   = _to_float(_text(c.find("qtgarantia")))
        qty     = qty_d + qty_g
        rows.append({
            "name":       _cota_name(isin=isin, cnpj=cnpj_fd),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(qty * pu, 2),
            "isCash":     False,
            "sourceKind": "cotas",
        })

    # opcoesderiv — both qty *and* PU carry the sign of `classeoperacao`
    # so qty*pu equals the (positive) `<valorfinanceiro>`. The snapshot
    # for wallet ...11e0 shows e.g. NVD1 P (V) with qty=-1100, pu=-3.798
    # and balance=+4178.05 — that is the convention we follow here.
    for o in fundo.findall("opcoesderiv"):
        isin    = _text(o.find("isin"))
        ativo   = _text(o.find("ativo"))
        serie   = _text(o.find("serie"))
        cp      = _text(o.find("callput"))     # C / P
        strike  = _text(o.find("precoexercicio"))
        venc    = _fmt_date_yyyymmdd(_text(o.find("dtvencimento")))
        classe  = _text(o.find("classeoperacao"))  # C / V
        qty_raw = _to_float(_text(o.find("quantidade")))
        valorfin = _to_float(_text(o.find("valorfinanceiro")))
        sign    = -1 if classe.upper() == "V" else 1
        qty     = qty_raw * sign
        # PU computed as valorfin / qty so qty*pu reconciles to valorfin.
        pu      = (valorfin / qty) if qty else 0.0
        rows.append({
            "name":       _opcoes_name(isin=isin, ativo=ativo, serie=serie,
                                        cp=cp, strike=strike, venc=venc),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(valorfin, 2),
            "isCash":     False,
            "sourceKind": "opcoesderiv",
        })

    # acoes — ações listadas. By analogy with 5.0 EQUI (`<isin>_<desc>`),
    # the 4.0 equivalent is `<isin>_<codativo>` where codativo is the
    # B3 ticker (PETR4, VALE3, etc.). EDUCATED GUESS — confirm against a
    # production snapshot when an `<acoes>` XML lands.
    for a in fundo.findall("acoes"):
        isin     = _text(a.find("isin"))
        ticker   = _text(a.find("codativo"))
        pu       = _to_float(_text(a.find("puposicao")))
        qty_d    = _to_float(_text(a.find("qtdisponivel")))
        qty_g    = _to_float(_text(a.find("qtgarantia")))
        qty      = qty_d + qty_g
        balance  = _to_float(_text(a.find("valorfindisp"))) \
                 + _to_float(_text(a.find("valorfinemgar")))
        rows.append({
            "name":       _join_us([isin, ticker]),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(balance, 2),
            "isCash":     False,
            "sourceKind": "acoes",
        })

    # debenture — same shape as titpublico (codativo is the issuer's
    # ticker, dtemissao + dtvencimento distinguish series). Pattern
    # `<isin>_<codativo>_<dtemissao>_<dtvencimento>`.
    for d in fundo.findall("debenture"):
        isin   = _text(d.find("isin"))
        cod    = _text(d.find("codativo"))
        emi    = _fmt_date_yyyymmdd(_text(d.find("dtemissao")))
        venc   = _fmt_date_yyyymmdd(_text(d.find("dtvencimento")))
        pu     = _to_float(_text(d.find("puposicao")))
        qty_d  = _to_float(_text(d.find("qtdisponivel")))
        qty_g  = _to_float(_text(d.find("qtgarantia")))
        qty    = qty_d + qty_g
        balance = _to_float(_text(d.find("valorfindisp"))) \
                + _to_float(_text(d.find("valorfinemgar")))
        rows.append({
            "name":       _join_us([isin, cod, emi, venc]),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(balance, 2),
            "isCash":     False,
            "sourceKind": "debenture",
        })

    # futuro — futures (DI1, DOL, IND…). Mirrors `opcoesderiv` minus
    # strike/callput. Pattern `<isin>_<ativo>_<serie>_<dtvencimento>`.
    # Sign-flips qty when `classeoperacao=V`, then derives PU as
    # `valorfinanceiro/qty` so the balance reconciles.
    for f in fundo.findall("futuro"):
        isin     = _text(f.find("isin"))
        ativo    = _text(f.find("ativo"))
        serie    = _text(f.find("serie"))
        venc     = _fmt_date_yyyymmdd(_text(f.find("dtvencimento")))
        classe   = _text(f.find("classeoperacao"))
        qty_raw  = _to_float(_text(f.find("quantidade")))
        valorfin = _to_float(_text(f.find("valorfinanceiro")))
        sign     = -1 if classe.upper() == "V" else 1
        qty      = qty_raw * sign
        pu       = (valorfin / qty) if qty else _to_float(_text(f.find("puposicao")))
        rows.append({
            "name":       _join_us([isin, ativo, serie, venc]),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(valorfin, 2),
            "isCash":     False,
            "sourceKind": "futuro",
        })

    # termo — forward on equities. Pattern
    # `<isin>_<codativo>_<dtoperacao>_<dtvencimento>` — same idea as
    # debenture but anchored on the trade date rather than emission.
    for t in fundo.findall("termo"):
        isin     = _text(t.find("isin"))
        cod      = _text(t.find("codativo"))
        op       = _fmt_date_yyyymmdd(_text(t.find("dtoperacao")))
        venc     = _fmt_date_yyyymmdd(_text(t.find("dtvencimento")))
        classe   = _text(t.find("classeoperacao"))
        qty_raw  = _to_float(_text(t.find("quantidade")) or _text(t.find("qtdisponivel")))
        valorfin = _to_float(_text(t.find("valorfinanceiro")))
        sign     = -1 if classe.upper() == "V" else 1
        qty      = qty_raw * sign
        pu       = (valorfin / qty) if qty else _to_float(_text(t.find("puposicao")))
        rows.append({
            "name":       _join_us([isin, cod, op, venc]),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(valorfin, 2),
            "isCash":     False,
            "sourceKind": "termo",
        })

    # aluguel — stock loan. The leg is identified by ticker; the sign
    # depends on `tipotomadordoador` (`D`oador → long, `T`omador →
    # short). Pattern `<isin>_<codativo>` to match `acoes`.
    for al in fundo.findall("aluguel"):
        isin     = _text(al.find("isin"))
        cod      = _text(al.find("codativo"))
        td       = _text(al.find("tipotomadordoador")).upper()  # D / T
        qty_raw  = _to_float(_text(al.find("qtdisponivel")))
        pu       = _to_float(_text(al.find("puposicao")))
        sign     = -1 if td == "T" else 1
        qty      = qty_raw * sign
        rows.append({
            "name":       _join_us([isin, cod]),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(qty * pu, 2),
            "isCash":     False,
            "sourceKind": "aluguel",
        })

    # exterior — foreign investments. Pattern `<isincomp>_<descrcomp>`,
    # mirroring 5.0 EQUI's `<isin>_<desc>`.
    for ex in fundo.findall("exterior"):
        isin    = _text(ex.find("isincomp")) or _text(ex.find("isin"))
        desc    = _text(ex.find("descrcomp")) or _text(ex.find("descricao"))
        pu      = _to_float(_text(ex.find("puposicao")))
        qty     = _to_float(_text(ex.find("qtdisponivel")))
        balance = _to_float(_text(ex.find("valorfinanceiro")))
        rows.append({
            "name":       _join_us([isin, desc]),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(balance or (qty * pu), 2),
            "isCash":     False,
            "sourceKind": "exterior",
        })

    # swap — OTC derivative. No ISIN; the Anbima 4.0 schema gives an
    # `<identificador>` (free-form contract id) plus dates. Pattern
    # `swap_<identificador>_<dtoperacao>_<dtvencimento>` — the "swap"
    # prefix keeps these visually distinct in the operator's preview.
    for sw in fundo.findall("swap"):
        ident   = _text(sw.find("identificador")) or _text(sw.find("codswap"))
        op      = _fmt_date_yyyymmdd(_text(sw.find("dtoperacao")))
        venc    = _fmt_date_yyyymmdd(_text(sw.find("dtvencimento")))
        # Balance: prefer `valorcurva` (mark-to-market); fall back to
        # `valorbase` if curva isn't reported.
        balance = _to_float(_text(sw.find("valorcurva"))) \
               or _to_float(_text(sw.find("valorbase")))
        rows.append({
            "name":       _join_us(["swap", ident, op, venc]),
            "quantity":   0,
            "pu":         0,
            "balance":    _round(balance, 2),
            "isCash":     False,
            "sourceKind": "swap",
        })

    # caixa — cash balance
    cash_total = 0.0
    has_cash = False
    for cx in fundo.findall("caixa"):
        has_cash = True
        cash_total += _to_float(_text(cx.find("saldo")))
    if has_cash:
        rows.append({
            "name":       "Caixa",
            "quantity":   0,
            "pu":         0,
            "balance":    _round(cash_total, 2),
            "isCash":     True,
            "sourceKind": "caixa",
        })

    # provisao
    provisions: list[dict] = []
    for p in fundo.findall("provisao"):
        codprov = _text(p.find("codprov"))
        credeb  = _text(p.find("credeb")).upper()
        dt      = _fmt_date_yyyymmdd(_text(p.find("dt")))
        valor   = _to_float(_text(p.find("valor")))
        signed  = -valor if credeb == "D" else valor
        provisions.append({
            "codprov":       codprov,
            "credeb":        credeb,
            "liquidationDate": dt,
            "valor":         _round(signed, 2),
            "suggestedType": _PROVISAO_TYPE_MAP.get(codprov, "other"),
            "description":   f"Anbima 4.0 codprov={codprov} credeb={credeb}",
        })

    return {
        "format":       "4.0",
        "fundName":     fund_name,
        "fundCnpj":     fund_cnpj,
        "positionDate": pos_date,
        "currencyId":   "BRL",
        "rows":         rows,
        "provisions":   provisions,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Layout 5.0 parser
# ──────────────────────────────────────────────────────────────────────────────


def _parse_v50(root: ET.Element) -> dict:
    """Parse the ISO 20022 / Anbima 5.0 envelope into the same dict shape
    as `_parse_v40`. The 5.0 sample carries no `<caixa>` and no
    `<provisao>`, so those lists are empty unless the file in question
    deviates from the BTG variant we sourced the schema from."""
    rep = root.find(".//i:SctiesBalAcctgRpt", _NS_5)
    if rep is None:
        raise ValueError("XML 5.0 sem <SctiesBalAcctgRpt>")

    # Statement date — header is more authoritative than the receipt date.
    stmt_dt = rep.find("./i:StmtGnlDtls/i:StmtDtTm/i:Dt", _NS_5)
    pos_date = _text(stmt_dt) or ""

    # Fund header lives in BalForAcct (without SfkpgAcct/SubAcct context).
    bfa = rep.find("./i:BalForAcct", _NS_5)
    if bfa is None:
        raise ValueError("XML 5.0 sem <BalForAcct> (header do fundo)")
    fund_name = _text(bfa.find("./i:FinInstrmId/i:Desc", _NS_5))
    # FinInstrmId.OthrId[Tp=CNPJ] holds the fund's CNPJ.
    fund_cnpj = ""
    for othr in bfa.findall("./i:FinInstrmId/i:OthrId", _NS_5):
        cd = _text(othr.find("./i:Tp/i:Cd", _NS_5))
        if cd == "CNPJ":
            fund_cnpj = _text(othr.find("./i:Id", _NS_5))
            break

    rows: list[dict] = []

    # BalForSubAcct[] — one row per security/derivative position.
    # The name format follows the existing snapshot for wallet
    # 680a9ce43b2296d8612711ee, which keys off the OthrId[Tp.Prtry=
    # 'TABELA NIVEL 1'].Id ("EQUI", "SHAR", "GOVE", "FUTU").
    for sub in rep.findall("./i:SubAcctDtls/i:BalForSubAcct", _NS_5):
        fii = sub.find("./i:FinInstrmId", _NS_5)
        if fii is None:
            continue
        desc = _text(fii.find("./i:Desc", _NS_5))
        isin = _text(fii.find("./i:ISIN", _NS_5))

        cnpj = ""
        cls  = ""
        for othr in fii.findall("./i:OthrId", _NS_5):
            cd      = _text(othr.find("./i:Tp/i:Cd", _NS_5))
            cd_prty = _text(othr.find("./i:Tp/i:Prtry", _NS_5))
            oid     = _text(othr.find("./i:Id", _NS_5))
            if cd == "CNPJ":
                cnpj = oid
            elif cd_prty == "TABELA NIVEL 1":
                # The Prtry-typed entry holds the asset-class tag (EQUI /
                # SHAR / GOVE / FUTU). When both EQUI and CNPJ entries
                # appear, the class wins and CNPJ is just metadata.
                cls = oid

        # Maturity + issuance — populated for GOVE/DEBE/CDB-like rows.
        emi  = _text(sub.find("./i:FinInstrmAttrbts/i:IsseDt",  _NS_5))
        venc = _text(sub.find("./i:FinInstrmAttrbts/i:MtrtyDt", _NS_5))

        # Option-only attributes — `ConvsPric` is the strike (under
        # `FinInstrmAttrbts/ConvsPric/Val/Amt`); call/put can show up
        # under a few legacy paths. Both stay empty for non-OPCO rows
        # so the dispatcher still degrades to `<isin>_<desc>`.
        strike = _text(sub.find(
            "./i:FinInstrmAttrbts/i:ConvsPric/i:Val/i:Amt", _NS_5))
        cp = (
            _text(sub.find("./i:FinInstrmAttrbts/i:OptnTp/i:Cd", _NS_5))
            or _text(sub.find("./i:FinInstrmAttrbts/i:PutOrCallInd",
                              _NS_5))
            or _text(sub.find("./i:FinInstrmAttrbts/i:CallOrPut",
                              _NS_5))
        )
        # Normalise CALL/PUT → C/P so the name matches the 4.0
        # opcoesderiv convention.
        if cp:
            cp = cp.strip().upper()[:1]

        # AggtBal.Qty.Qty.Qty.{Unit|FaceAmt|AmtsdVal} — `Qty` is a
        # `<choice>` in the semt.003 schema. `Unit` is what BTG emits
        # for the wallets we've seen; `FaceAmt` (face value, common for
        # fixed-income/derivatives) and `AmtsdVal` (amortised value)
        # are the documented alternatives. Short positions flip qty
        # sign — PU stays positive (production snapshot for LFT REF
        # SHOR rows keeps pu=18990.67072 on negative-qty rows).
        side = _text(sub.find("./i:AggtBal/i:ShrtLngInd", _NS_5))
        qty_path = "./i:AggtBal/i:Qty/i:Qty/i:Qty"
        qty_node = sub.find(qty_path, _NS_5)
        qty = 0.0
        if qty_node is not None:
            for leaf in ("i:Unit", "i:FaceAmt", "i:AmtsdVal"):
                txt = _text(qty_node.find(leaf, _NS_5))
                if txt:
                    qty = _to_float(txt)
                    break
        if side.upper() == "SHOR":
            qty = -qty

        # PricDtls.Val.Amt — instrument PU (unsigned in the wire format).
        pu = _to_float(_text(sub.find("./i:PricDtls/i:Val/i:Amt", _NS_5)))

        # AcctBaseCcyAmts.HldgVal — `Sgn=false` ⇒ negative balance.
        hold_amt = _to_float(_text(sub.find(
            "./i:AcctBaseCcyAmts/i:HldgVal/i:Amt", _NS_5)))
        hold_sgn = _text(sub.find(
            "./i:AcctBaseCcyAmts/i:HldgVal/i:Sgn", _NS_5)).lower()
        if hold_sgn == "false":
            hold_amt = -hold_amt

        rows.append({
            "name":       _v50_security_name(
                            cls=cls, desc=desc, isin=isin, cnpj=cnpj,
                            emi=emi, venc=venc, strike=strike, cp=cp),
            "quantity":   _round(qty, 8),
            "pu":         _round(pu, 8),
            "balance":    _round(hold_amt, 2),
            "isCash":     False,
            "sourceKind": (cls or "balforsubacct").lower(),
        })

    return {
        "format":       "5.0",
        "fundName":     fund_name,
        "fundCnpj":     fund_cnpj,
        "positionDate": pos_date,
        "currencyId":   "BRL",
        "rows":         rows,
        "provisions":   [],
    }


# ──────────────────────────────────────────────────────────────────────────────
# Asset name builders
# ──────────────────────────────────────────────────────────────────────────────


# ── Naming conventions ────────────────────────────────────────────────
# All four 4.0 builders + the 5.0 dispatcher mirror the production
# snapshot for wallets 680a9ce43b2296d8612711e0 (4.0) and
# 680a9ce43b2296d8612711ee (5.0). Whenever a part is missing in the XML
# we keep the underscore separator empty rather than collapsing it, so
# parsers downstream that split on "_" find a consistent column count
# inside an asset class.


def _join_us(parts: list[str]) -> str:
    """Underscore-join, dropping `None`/empty parts. Matches the
    `_`-separated style used in the production snapshot."""
    return "_".join(p for p in parts if p)


def _bond_name(*, isin: str, cusip: str, emi: str, venc: str) -> str:
    """4.0 títulos públicos — `<isin>_<cusip>_<dtemissao>_<dtvencimento>`.

    Matches the production snapshot, e.g.
    `BRSTNCLF1RK7_STNCLF1RK_2022-04-06_2028-09-01`."""
    return _join_us([isin, cusip, emi, venc])


def _cota_name(*, isin: str, cnpj: str) -> str:
    """4.0 `<cotas>` — `<isin>_<cnpjfundo>`. Generic ISIN is kept
    because the snapshot includes it verbatim (and the (ISIN, CNPJ)
    pair is unique even when ISIN is the placeholder)."""
    return _join_us([isin, cnpj])


def _opcoes_name(*, isin: str, ativo: str, serie: str, cp: str,
                  strike: str, venc: str) -> str:
    """4.0 `<opcoesderiv>` —
    `<isin>_<ativo>_<serie>_<callput>_<precoexercicio>_<dtvencimento>`,
    matching the snapshot e.g. `BRBMEFVDR563_DOL_NVD1_P_4500_2026-07-01`.

    Strike is normalised via `_norm_strike` so `4500.0` collapses to
    `4500` — the production snapshot stores integer strikes verbatim
    and a trailing `.0` would create a duplicate `unprocessedId`."""
    return _join_us([isin, ativo, serie, (cp or "").upper(),
                     _norm_strike(strike), venc])


# Asset-class shapes for 5.0 BalForSubAcct rows. The first four codes
# (EQUI, SHAR, GOVE, FUTU) are validated against production snapshot
# of wallet 680a9ce43b2296d8612711ee. The remaining codes follow the
# same shape conventions by analogy with the 4.0 parser — when a real
# XML lands, the operator should verify the name lines up with the
# wallet's existing snapshot.
def _v50_security_name(*, cls: str, desc: str, isin: str, cnpj: str,
                        emi: str, venc: str, strike: str = "",
                        cp: str = "") -> str:
    """5.0 BalForSubAcct dispatcher. The asset-class tag is the
    `OthrId[Tp.Prtry='TABELA NIVEL 1'].Id` value extracted by the
    parser. Validated mappings:

      * `EQUI` (ações/ETFs)         → `<isin>_<desc>`
      * `SHAR` (cotas de fundos)    → `<isin>_<cnpj>_<desc>`
      * `GOVE` (títulos públicos)   → `<isin>_<desc>_<isseDt>_<mtrtyDt>`
      * `FUTU` (futuros)            → `<isin>_<desc>`

    Best-effort mappings (mirror the 4.0 builders for the analogous
    asset type — confirm against a snapshot when a sample arrives):

      * `OPCO` / `OPTI` (opções)     → `<isin>_<desc>_<C|P>_<strike>_<mtrtyDt>`
      * `TERM` (termo)               → `<isin>_<desc>_<mtrtyDt>`
      * `DEBE` (debêntures)          → `<isin>_<desc>_<isseDt>_<mtrtyDt>`
      * `CDB`/`LCI`/`LCA`/`LF`/`LCRE`/`AGRO` (renda fixa privada) →
                                       `<isin>_<desc>_<isseDt>_<mtrtyDt>`
      * `SWAP`                       → `swap_<isin>_<desc>_<mtrtyDt>`
      * `REPO` / `COMP` (compromissadas) → `<isin>_<desc>_<mtrtyDt>`
      * outros / sem tag             → `<isin>_<desc>` (safe fallback)

    SHAR keeps the generic ISIN (BR0000000000) when present, because
    the snapshot shows funds without a real ISIN still keyed off the
    placeholder + CNPJ + descritivo."""
    c = (cls or "").upper()
    # Validated mappings ────────────────────────────────────────────
    if c == "SHAR":
        return _join_us([isin, cnpj, desc])
    if c == "GOVE":
        return _join_us([isin, desc, emi, venc])
    if c in ("EQUI", "FUTU"):
        return _join_us([isin, desc])
    # Best-effort mappings ───────────────────────────────────────────
    if c in ("OPCO", "OPTI"):
        return _join_us([isin, desc, (cp or "").upper(),
                         _norm_strike(strike), venc])
    if c == "TERM":
        return _join_us([isin, desc, venc])
    if c in ("DEBE", "CDB", "LCI", "LCA", "LF", "LCRE", "AGRO"):
        return _join_us([isin, desc, emi, venc])
    if c == "SWAP":
        return _join_us(["swap", isin, desc, venc])
    if c in ("REPO", "COMP"):
        return _join_us([isin, desc, venc])
    # Unknown class — preserve as much identity as the XML gave us. If
    # a CNPJ tagged the row, include it: two unrelated funds can share
    # the same Desc (`BARAUNA IV FIP CL A` vs `CL B`) and the CNPJ is
    # what keeps them apart in the SHAR shape. Falls back to the
    # EQUI/FUTU shape when no CNPJ is around.
    if cnpj:
        return _join_us([isin, cnpj, desc])
    return _join_us([isin, desc])


def _norm_strike(s: str) -> str:
    """Trim a trailing `.0` so option strikes like `4500.0` collapse to
    `4500` — matches the production snapshot for 4.0 opcoesderiv.
    Non-numeric values pass through unchanged."""
    s = (s or "").strip()
    if not s:
        return s
    try:
        f = float(s)
    except (TypeError, ValueError):
        return s
    return str(int(f)) if f == int(f) else s


# ──────────────────────────────────────────────────────────────────────────────
# XLSX builder
# ──────────────────────────────────────────────────────────────────────────────


def _build_xlsx(*, target_date: str, wallet_id: str, rows: list[dict],
                currency_id: str, cash_unprocessed_id: str = "Caixa") -> bytes:
    """Build the upstream-shaped workbook (same schema as carteira)."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(["Data", "Carteira", "Ativo", "Quant", "PU",
               "SaldoBruto", "Caixa", "Moeda"])
    for r in rows:
        if r.get("isCash"):
            ws.append([
                target_date,
                wallet_id,
                cash_unprocessed_id or "Caixa",
                0,
                0,
                r.get("balance") or 0,
                "Sim",
                currency_id or "BRL",
            ])
        else:
            ws.append([
                target_date,
                wallet_id,
                r.get("name") or "",
                r.get("quantity") or 0,
                r.get("pu") or 0,
                r.get("balance") or 0,
                "Não",
                currency_id or "BRL",
            ])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


# ──────────────────────────────────────────────────────────────────────────────
# Page + filter routes
# ──────────────────────────────────────────────────────────────────────────────


@bp.route("/fundos")
def index():
    return render_template("fundos.html")


@bp.route("/api/fundos/filters/companies")
def list_companies():
    """Mirror of the /carteira and /excecoes company listings — same
    `company_filter` settings.json gate, so the dropdown matches the
    favourites bar."""
    from db import get_company_filter
    cf = get_company_filter()
    out = []
    for cid, name in get_company_names().items():
        if cf and cid not in cf:
            continue
        out.append({"id": cid, "name": name or cid})
    out.sort(key=lambda c: (c["name"] or "").lower())
    return jsonify(out)


@bp.route("/api/fundos/filters/wallets")
def list_wallets():
    """List wallets for `companyId`. Returns each wallet's name, currency,
    and the CNPJ list pulled from `consumptionIdentifiers[].consumptionId`.

    The CNPJs feed the per-file auto-match on the page: a fund XML's
    header `<cnpj>` (4.0) or `OthrId[Tp.Cd=CNPJ]` (5.0) is matched against
    these values to pre-select the destination wallet without operator
    intervention. The field is an *array* in the upstream schema because
    a wallet can carry multiple identifiers (`exclusive-funds`,
    `exclusive-funds-v5`, etc.) — we collect them all and compare on
    digits-only to dodge formatting differences (`58.123...` vs
    `58123...`)."""
    company_id = (request.args.get("companyId") or "").strip()
    if not company_visible(company_id):
        return jsonify([])
    wallet_names = get_wallet_names()
    items = []
    for w in db.wallets.find(
        {"companyId": company_id},
        {"name": 1, "currencyId": 1, "consumptionIdentifiers": 1},
    ):
        wid = str(w["_id"])
        # Pull every consumption id and reduce to digits-only so the
        # frontend can do an exact-string match against the digits-only
        # CNPJ it extracts from the XML.
        cnpjs = []
        for ident in (w.get("consumptionIdentifiers") or []):
            raw = (ident or {}).get("consumptionId")
            if not raw:
                continue
            digits = re.sub(r"\D", "", str(raw))
            if digits:
                cnpjs.append(digits)
        items.append({
            "id":         wid,
            "name":       w.get("name") or wallet_names.get(wid, wid),
            "currencyId": w.get("currencyId") or "BRL",
            "cnpjs":      cnpjs,
        })
    items.sort(key=lambda x: (x["name"] or "").lower())
    return jsonify(items)


# ──────────────────────────────────────────────────────────────────────────────
# Parse endpoint
# ──────────────────────────────────────────────────────────────────────────────


@bp.route("/api/fundos/parse", methods=["POST"])
def parse_xmls():
    """Accept multipart `files[]` of XMLs. Return a per-file preview
    payload. No upstream writes happen here — this is a read-only
    interpretation pass that feeds the confirm modal."""
    company_id = (request.form.get("companyId") or "").strip()
    if not company_id or not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    files = request.files.getlist("files") or []
    if not files:
        return jsonify({"error": "nenhum arquivo enviado"}), 400
    if len(files) > _MAX_XML_FILES_PER_BATCH:
        return jsonify({
            "error": f"máximo de {_MAX_XML_FILES_PER_BATCH} arquivos "
                     f"por upload (recebido: {len(files)})",
        }), 413

    out = []
    for f in files:
        filename = f.filename or "fundo.xml"
        try:
            raw = f.read()
            if not raw:
                raise ValueError("arquivo vazio")
            if len(raw) > _MAX_XML_BYTES_PER_FILE:
                raise ValueError(
                    f"arquivo excede {_MAX_XML_BYTES_PER_FILE // (1024 * 1024)} MB "
                    f"({len(raw)} bytes)")
            # ElementTree handles XML declarations including encoding=
            # attributes — no need to pre-decode the bytes.
            root = ET.fromstring(raw)
        except (ET.ParseError, ValueError) as e:
            out.append({
                "filename": filename,
                "error":    f"XML inválido: {e}",
            })
            continue

        local = _strip_ns(root.tag)
        try:
            if local == "arquivoposicao_4_01":
                parsed = _parse_v40(root)
            elif local == "PosicaoAtivosCarteira":
                parsed = _parse_v50(root)
            else:
                raise ValueError(
                    f"layout não reconhecido (raiz <{local}>)")
        except (ValueError, KeyError, AttributeError) as e:
            out.append({"filename": filename, "error": str(e)})
            continue

        # Coverage summary for the operator (so they can sanity-check
        # before confirming an upload).
        n_assets = sum(1 for r in parsed["rows"] if not r.get("isCash"))
        n_cash   = sum(1 for r in parsed["rows"] if r.get("isCash"))
        total_balance = round(sum(_to_float(r.get("balance")) for r in parsed["rows"]), 2)

        out.append({
            "filename":     filename,
            "format":       parsed["format"],
            "fundName":     parsed["fundName"],
            "fundCnpj":     parsed["fundCnpj"],
            "positionDate": parsed["positionDate"],
            "currencyId":   parsed["currencyId"],
            "rows":         parsed["rows"],
            "provisions":   parsed["provisions"],
            "summary": {
                "nAssets":      n_assets,
                "nCash":        n_cash,
                "nProvisions":  len(parsed["provisions"]),
                "totalBalance": total_balance,
            },
        })

    return jsonify({"files": out})


# ──────────────────────────────────────────────────────────────────────────────
# Apply endpoint
# ──────────────────────────────────────────────────────────────────────────────


@bp.route("/api/fundos/apply", methods=["POST"])
def apply_files():
    """Confirm-and-send. Each entry in `files[]` is uploaded as its own
    `unprocessedSecurityPositions` snapshot for `(walletId, positionDate)`
    — same upstream endpoint as /carteira's apply. After a successful
    upload, the file's `provisions[]` (4.0 only) are created via
    `POST /beehus/provisions`, one at a time.

    Body:
        {
          "companyId": str,
          "files": [
            {
              "filename":     str,
              "walletId":     str,
              "positionDate": "YYYY-MM-DD",
              "currencyId":   str,
              "rows":         [{name, quantity, pu, balance, isCash}, ...],
              "provisions":   [{liquidationDate, valor, suggestedType, description}, ...]
            },
            ...
          ]
        }
    """
    body = request.get_json(silent=True) or {}
    company_id = (body.get("companyId") or "").strip()
    files = body.get("files") or []

    if not company_id or not company_visible(company_id):
        return jsonify({"error": "forbidden"}), 403
    try:
        _safe(company_id)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    if not files:
        return jsonify({"error": "files[] vazio"}), 400

    # Pre-build the wallet → currencyId / cashAccount.unprocessedId index
    # in a single pass so the per-file loop doesn't fire N queries.
    wanted_oids = []
    for f in files:
        oid = _to_oid((f.get("walletId") or "").strip())
        if oid is not None:
            wanted_oids.append(oid)
    w_index: dict[str, dict] = {}
    if wanted_oids:
        for w in db.wallets.find(
            {"_id": {"$in": wanted_oids}, "companyId": company_id},
            {"currencyId": 1, "name": 1},
        ):
            w_index[str(w["_id"])] = {
                "currencyId": w.get("currencyId") or "BRL",
                "name":       w.get("name") or "",
            }
    cash_uid_index: dict[str, str] = {}
    if wanted_oids:
        for ca in db.cashAccounts.find(
            {"walletId": {"$in": [str(o) for o in wanted_oids]},
             "trashed":  {"$ne": True}},
            {"walletId": 1, "unprocessedId": 1},
        ):
            wid = str(ca.get("walletId") or "")
            uid = (ca.get("unprocessedId") or "").strip()
            if wid and uid and wid not in cash_uid_index:
                cash_uid_index[wid] = uid

    results = []
    for f in files:
        filename     = (f.get("filename") or "").strip() or "fundo.xml"
        wallet_id    = (f.get("walletId") or "").strip()
        position_dt  = (f.get("positionDate") or "").strip()
        currency_id  = (f.get("currencyId") or "").strip()
        rows         = f.get("rows") or []
        provisions   = f.get("provisions") or []

        # Per-file validation — a single bad file shouldn't abort the
        # batch. We collect a "skipped" result so the operator can re-fix
        # and re-submit without losing the work that did upload.
        if not wallet_id or not position_dt:
            results.append({
                "filename": filename,
                "status":   "skipped",
                "error":    "walletId/positionDate ausentes",
            })
            continue
        w_meta = w_index.get(wallet_id)
        if not w_meta:
            results.append({
                "filename": filename,
                "status":   "skipped",
                "error":    "wallet não encontrada nesta empresa",
            })
            continue
        currency_id = currency_id or w_meta["currencyId"] or "BRL"
        cash_uid    = cash_uid_index.get(wallet_id, "Caixa")

        # Drop rows the operator unchecked (empty name only). Duplicate
        # names are passed through unchanged — the production snapshot
        # for wallet 680a9ce43b2296d8612711ee shows LFT REF 6 rows under
        # the same `unprocessedId` (1 long + 5 short legs), so the
        # upstream parser is fine with same-named siblings within a
        # (Data, Carteira) bucket.
        norm_rows: list[dict] = []
        for r in rows:
            if not isinstance(r, dict):
                continue
            name = (r.get("name") or "").strip()
            if not r.get("isCash") and not name:
                continue
            norm_rows.append({
                "name":     name,
                "quantity": _round(_to_float(r.get("quantity")), 8),
                "pu":       _round(_to_float(r.get("pu")), 8),
                "balance":  _round(_to_float(r.get("balance")), 2),
                "isCash":   bool(r.get("isCash")),
            })

        if not norm_rows:
            results.append({
                "filename": filename,
                "status":   "skipped",
                "error":    "nenhuma linha para enviar",
            })
            continue

        xlsx = _build_xlsx(
            target_date=position_dt,
            wallet_id=wallet_id,
            rows=norm_rows,
            currency_id=currency_id,
            cash_unprocessed_id=cash_uid,
        )
        upstream_name = (
            f"fundos_{company_id}_{wallet_id}_{position_dt}_"
            f"{datetime.utcnow().strftime('%Y%m%d%H%M%S')}.xlsx"
        )
        try:
            upstream = upload_unprocessed_security_positions_file(
                company_id=company_id,
                file_bytes=xlsx,
                filename=upstream_name,
            )
        except BeehusAuthError as e:
            results.append({
                "filename":         filename,
                "status":           "error",
                "error":            str(e),
                "upstream_status":  getattr(e, "status", None),
                "upstream_body":    getattr(e, "body", None),
            })
            continue
        except BeehusAPIError as e:
            results.append({
                "filename":         filename,
                "status":           "error",
                "error":            str(e),
                "upstream_status":  getattr(e, "status", None),
                "upstream_body":    getattr(e, "body", None),
            })
            continue

        # Provisions — one POST each. We don't abort the file on the first
        # failure (the upload already succeeded); failures are reported
        # individually so the operator can re-issue just the broken ones.
        prov_results = []
        for p in provisions:
            if not isinstance(p, dict):
                continue
            liq = (p.get("liquidationDate") or "").strip()
            val = _to_float(p.get("valor"))
            if not liq or val == 0:
                continue
            prov_type   = (p.get("suggestedType") or "other").strip()
            description = (p.get("description") or "").strip()
            try:
                created = create_provision(
                    company_id=company_id,
                    wallet_id=wallet_id,
                    balance=val,
                    initial_date=position_dt,
                    liquidation_date=liq,
                    provision_type=prov_type,
                    currency_id=currency_id,
                    description=description,
                )
                prov_results.append({
                    "ok":          True,
                    "description": description,
                    "balance":     val,
                    "liquidationDate": liq,
                    "provisionId": (created or {}).get("_id"),
                })
            except (BeehusAuthError, BeehusAPIError) as e:
                prov_results.append({
                    "ok":          False,
                    "description": description,
                    "error":       str(e),
                })

        results.append({
            "filename":      filename,
            "status":        "ok",
            "uploadedRows":  len(norm_rows),
            "provisions":    prov_results,
            "upstream":      upstream,
        })

    return jsonify({"results": results})
