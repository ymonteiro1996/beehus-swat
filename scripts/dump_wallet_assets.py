"""Dump completo dos ativos de rawBTGPosition para um walletCode+consumeDate.

Uso pontual: localizar os ativos apontados numa issue de securityMapping e
extrair todos os campos brutos do BTG para fazer o cadastro.

Saída: imprime no console + grava scripts/dump_wallet_assets_<wallet>_<date>.json
"""
from __future__ import annotations

import io
import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402

WALLET_CODE = "008121162"
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


def main():
    coll = db["rawBTGPosition"]
    q = {"walletCode": WALLET_CODE, "consumeDate": CONSUME_DATE}
    docs = list(coll.find(q))
    print(f"Query: {q}")
    print(f"Docs encontrados: {len(docs)}")

    if not docs:
        # Diagnostico: o walletCode existe em alguma data?
        sample = list(coll.find({"walletCode": WALLET_CODE},
                                {"consumeDate": 1, "companyId": 1}).limit(50))
        if sample:
            dates = Counter(s.get("consumeDate") for s in sample)
            print(f"  walletCode existe noutras datas: {dict(dates)}")
            print(f"  companyId(s): {set(s.get('companyId') for s in sample)}")
        else:
            print("  walletCode NAO encontrado em nenhuma data.")
        return

    out = {}
    for d in docs:
        wallet = d.get("walletCode")
        company = d.get("companyId")
        print(f"\n=== doc walletCode={wallet} companyId={company} consumeDate={d.get('consumeDate')} ===")
        p = d.get("position") or {}

        # Coletores planos: categoria -> lista de itens brutos
        buckets = {}

        def grab(label, items):
            if not items:
                return
            if isinstance(items, dict):
                items = [items]
            if isinstance(items, list):
                lst = buckets.setdefault(label, [])
                for x in items:
                    if isinstance(x, dict):
                        lst.append(stringify(x))

        grab("FixedIncome", p.get("FixedIncome"))
        grab("InvestmentFund", p.get("InvestmentFund"))
        grab("InvestmentFundCotaCetipada", p.get("InvestmentFundCotaCetipada"))
        grab("FixedIncomeStructuredNote", p.get("FixedIncomeStructuredNote"))

        # PensionInformations -> achatamos Positions
        for pen in (p.get("PensionInformations") or []):
            if not isinstance(pen, dict):
                continue
            base = {k: v for k, v in pen.items() if k != "Positions"}
            positions = pen.get("Positions") or []
            if not positions:
                grab("PensionInformations", pen)
            for pos in positions:
                if isinstance(pos, dict):
                    grab("PensionInformations", {**base, **pos})

        eq = p.get("Equities") or {}
        if isinstance(eq, dict):
            for k in ("StockPositions", "ForwardPositions", "OptionPositions",
                      "StockLendingPositions", "CollateralPositions",
                      "StructuredProducts", "CetipOptionPosition",
                      "PortfolioInvestments"):
                grab(f"Equities.{k}", eq.get(k))

        for k in ("Commodity", "CryptoCoin", "Credits", "Precatories",
                  "PrecatoriesCR"):
            grab(k, p.get(k))

        # Resumo
        print("  Categorias com itens:")
        for label, items in buckets.items():
            print(f"    {label:35s} {len(items)} item(s)")

        out[f"{wallet}"] = buckets

    out_path = Path(__file__).parent / f"dump_wallet_assets_{WALLET_CODE}_{CONSUME_DATE}.json"
    out_path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nDump completo gravado em: {out_path}")


if __name__ == "__main__":
    main()
