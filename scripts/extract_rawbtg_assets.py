"""Extrai ativos distintos de rawBTGPosition para a companyId/consumeDate.

Para cada categoria (FixedIncome, InvestmentFund, etc) lista um exemplo da
estrutura e tabula as chaves disponíveis para gerar a tabela de cadastro.
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402

COMPANY_ID = "58454495000109"
CONSUME_DATE = "2026-05-21"


def stringify(v):
    from bson import ObjectId
    from datetime import date, datetime
    if isinstance(v, ObjectId):
        return f"ObjectId({v})"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: stringify(val) for k, val in v.items()}
    if isinstance(v, list):
        return [stringify(x) for x in v]
    return v


def collect(coll, q):
    docs = list(coll.find(q))
    print(f"Total docs: {len(docs)}")

    # Aggregators
    cats = {
        "FixedIncome": [],
        "InvestmentFund": [],
        "InvestmentFundCotaCetipada": [],
        "PensionInformations": [],
        "Equities.StockPositions": [],
        "Equities.ForwardPositions": [],
        "Equities.OptionPositions": [],
        "Equities.StockLendingPositions": [],
        "Equities.CollateralPositions": [],
        "Equities.StructuredProducts": [],
        "Equities.CetipOptionPosition": [],
        "Equities.PortfolioInvestments": [],
        "Derivative.NDFPosition": [],
        "Derivative.BMFFuturePosition": [],
        "Derivative.BMFOptionPosition": [],
        "Derivative.CetipOptionPosition": [],
        "Derivative.SwapPosition": [],
        "Cash.CashInvested": [],
        "FixedIncomeStructuredNote": [],
        "Commodity": [],
        "CryptoCoin": [],
        "Credits": [],
        "Precatories": [],
        "PrecatoriesCR": [],
        "PayableReceivables.Credit": [],
    }

    for d in docs:
        p = d.get("position") or {}
        wallet = d.get("walletCode") or ""

        def add(key, item, source_key):
            if item is None:
                return
            if isinstance(item, list):
                for x in item:
                    if x:
                        cats[key].append({"_wallet": wallet, **x} if isinstance(x, dict) else {"_wallet": wallet, "_value": x})
            elif isinstance(item, dict):
                cats[key].append({"_wallet": wallet, **item})

        add("FixedIncome", p.get("FixedIncome"), "FixedIncome")
        add("InvestmentFund", p.get("InvestmentFund"), "InvestmentFund")
        add("InvestmentFundCotaCetipada", p.get("InvestmentFundCotaCetipada"), "InvestmentFundCotaCetipada")
        add("PensionInformations", p.get("PensionInformations"), "PensionInformations")

        eq = p.get("Equities") or {}
        if isinstance(eq, dict):
            for k in ("StockPositions", "ForwardPositions", "OptionPositions",
                      "StockLendingPositions", "CollateralPositions",
                      "StructuredProducts", "CetipOptionPosition", "PortfolioInvestments"):
                add(f"Equities.{k}", eq.get(k), f"Equities.{k}")

        dv = p.get("Derivative") or {}
        if isinstance(dv, dict):
            for k in ("NDFPosition", "BMFFuturePosition", "BMFOptionPosition",
                      "CetipOptionPosition", "SwapPosition"):
                add(f"Derivative.{k}", dv.get(k), f"Derivative.{k}")

        cash = p.get("Cash") or {}
        if isinstance(cash, dict):
            add("Cash.CashInvested", cash.get("CashInvested"), "Cash.CashInvested")

        for k in ("FixedIncomeStructuredNote", "Commodity", "CryptoCoin", "Credits",
                  "Precatories", "PrecatoriesCR"):
            add(k, p.get(k), k)

        pr = p.get("PayableReceivables") or {}
        if isinstance(pr, dict):
            add("PayableReceivables.Credit", pr.get("Credit"), "PayableReceivables.Credit")

    print("\n=== Categorias preenchidas ===")
    for k, v in cats.items():
        if v:
            print(f"  {k:45s} items={len(v)}  sample_keys={list(v[0].keys())[:12]}")

    # Dump full sample (first item per non-empty cat)
    samples = {k: stringify(v[0]) for k, v in cats.items() if v}
    out = Path(__file__).parent / "rawbtg_assets_samples.json"
    out.write_text(json.dumps(samples, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSamples por categoria: {out}")

    # Also dump all items for the non-empty categories
    out2 = Path(__file__).parent / "rawbtg_assets_full.json"
    full = {k: stringify(v) for k, v in cats.items() if v}
    out2.write_text(json.dumps(full, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Full dump: {out2}")


def main():
    coll = db["rawBTGPosition"]
    q = {"companyId": COMPANY_ID, "consumeDate": CONSUME_DATE}
    collect(coll, q)


if __name__ == "__main__":
    main()
