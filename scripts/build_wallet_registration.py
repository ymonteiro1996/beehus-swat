"""Tabela de cadastro para um walletCode especifico (reusa build_btg_asset_registration)."""
from __future__ import annotations

import io
import json
import sys
from collections import OrderedDict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402
import build_btg_asset_registration as B  # noqa: E402

CONSUME_DATE = "2026-05-21"
# (walletCode, [opcional] filtro de tickers/fundCodes a manter)
TARGETS = [
    ("008121162", None),                       # Cliente 3 — todos
    ("013988951", {"LCA-25A02638302"}),        # Cliente 1 — só o TENTOS LCA
]


def fi_row(fi):
    name, jt, emissor, indexer, y, idx_pct, m_iso = B._build_fi_name(fi)
    return {
        "category": "FixedIncome", "beehusName": name, "securityType": "bond",
        "type": jt, "ticker": fi.get("Ticker") or "", "cetipCode": fi.get("CetipCode") or "",
        "selicCode": fi.get("SelicCode") or "", "isin": fi.get("ISIN") or "",
        "securityCode": fi.get("SecurityCode") or "", "issuerRaw": fi.get("Issuer") or "",
        "issuerShort": emissor, "issueDate": B._to_iso(fi.get("IssueDate")),
        "maturityDate": m_iso, "indexer": indexer, "indexerPercentual": idx_pct, "yield": y,
        "rawRate": fi.get("IndexYieldRate") or fi.get("ReferenceIndexValue") or "",
        "accountingGroupCode": fi.get("AccountingGroupCode") or "",
        "taxFree": fi.get("TaxFree") or "", "currency": "BRL", "country": "BR",
    }


def fund_row(fnd):
    name, jt, cnpj, sec_code = B._build_fund_name(fnd)
    fund = fnd.get("Fund") or {}
    return {
        "category": "InvestmentFund", "beehusName": name, "securityType": "fund", "type": jt,
        "securityCode": sec_code or "", "taxId": cnpj or "",
        "managerName": fund.get("ManagerName") or "", "benchmark": fund.get("BenchMark") or "",
        "ticker": "", "cetipCode": "", "isin": "", "selicCode": "",
        "issuerRaw": fund.get("FundName") or "", "issuerShort": fund.get("FundName") or "",
        "maturityDate": "", "indexer": None, "indexerPercentual": None, "yield": None,
        "rawRate": "", "currency": "BRL", "country": "BR",
    }


def pen_row(pen, pos):
    name, jt, fcode, fcge = B._build_pension_name(pen, pos)
    return {
        "category": "PensionInformations", "beehusName": name, "securityType": "pension",
        "type": jt, "securityCode": fcode or "", "fundCgeCode": fcge or "",
        "taxId": pos.get("PensionCnpjCode") or "",
        "susepCode": pos.get("SusepCode") or pen.get("SusepCode") or "",
        "fundType": pos.get("FundType") or pen.get("FundType") or "",
        "taxRegime": pos.get("TaxRegime") or pen.get("TaxRegime") or "",
        "ticker": "", "cetipCode": "", "isin": "", "selicCode": "",
        "issuerRaw": pos.get("FundName") or "", "issuerShort": pos.get("FundName") or "",
        "maturityDate": "", "indexer": None, "indexerPercentual": None, "yield": None,
        "rawRate": "", "currency": "BRL", "country": "BR",
    }


def main():
    coll = db["rawBTGPosition"]
    rows = []
    for wallet_code, keep in TARGETS:
        d = coll.find_one({"walletCode": wallet_code, "consumeDate": CONSUME_DATE})
        if not d:
            print(f"!! sem doc para {wallet_code}")
            continue
        p = d.get("position") or {}
        for fi in (p.get("FixedIncome") or []):
            if not isinstance(fi, dict):
                continue
            if keep and (fi.get("Ticker") not in keep):
                continue
            r = fi_row(fi); r["_wallet"] = wallet_code; rows.append(r)
        if keep:
            continue  # para Cliente 1 só queremos o LCA
        for fnd in (p.get("InvestmentFund") or []):
            if isinstance(fnd, dict):
                r = fund_row(fnd); r["_wallet"] = wallet_code; rows.append(r)
        for pen in (p.get("PensionInformations") or []):
            if not isinstance(pen, dict):
                continue
            poss = pen.get("Positions")
            if isinstance(poss, list) and poss:
                for pos in poss:
                    if isinstance(pos, dict):
                        r = pen_row(pen, pos); r["_wallet"] = wallet_code; rows.append(r)
            else:
                r = pen_row(pen, pen); r["_wallet"] = wallet_code; rows.append(r)

    # match com securities existentes
    from security_matcher import get_cache
    from btg_existing_matcher import find_existing
    cache = get_cache(); cache.ensure_loaded(db)
    print(f"securities cache: {cache.count} ativos (date={cache.loaded_date})\n")

    for r in rows:
        r["_wallets"] = {r["_wallet"]}
        sid, bn, mo = find_existing(r, cache)
        r["existingSecurityId"] = sid
        r["existingBeehusName"] = bn
        r["matchedOn"] = mo

    # imprime
    print(f"{'CAT':6} {'EXISTE?':10} {'beehusName (sugerido)':52} {'type':14} {'ticker/cnpj':20} mat")
    print("-" * 130)
    for r in rows:
        exist = (r["existingSecurityId"] or "")[:8] if r["existingSecurityId"] else "NOVO"
        ident = r.get("ticker") or r.get("taxId") or r.get("securityCode") or ""
        print(f"{r['category'][:6]:6} {exist:10} {r['beehusName'][:52]:52} {r['type'][:14]:14} "
              f"{ident[:20]:20} {r.get('maturityDate','')}")

    out = Path(__file__).parent / f"wallet_registration_{CONSUME_DATE}.json"
    dump = []
    for r in rows:
        rr = {k: (list(v) if isinstance(v, set) else v) for k, v in r.items()}
        dump.append(rr)
    out.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDump -> {out}")


if __name__ == "__main__":
    main()
