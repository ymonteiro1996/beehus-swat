"""Procura ativos especificos em rawBTGPosition (consumeDate fixo) por todos os
walletCodes, para localizar ativos ausentes do walletCode principal."""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402

CONSUME_DATE = "2026-05-21"
COMPANY_ID = "58454495000109"

# Termos a procurar (case-insensitive) em Issuer/Ticker/FundName
NEEDLES = ["NU FINANCEIRA", "TENTOS", "LCA-25A02638302", "CDB925", "25A02638302"]


def hay(item):
    parts = []
    for k in ("Issuer", "Ticker", "CetipCode", "SecurityCode", "ISIN"):
        v = item.get(k)
        if v:
            parts.append(str(v))
    return " | ".join(parts).upper()


def main():
    coll = db["rawBTGPosition"]

    # 1) Quais walletCodes existem para essa company nessa data?
    same_co = list(coll.find(
        {"companyId": COMPANY_ID, "consumeDate": CONSUME_DATE},
        {"walletCode": 1}))
    print(f"companyId={COMPANY_ID} consumeDate={CONSUME_DATE}: {len(same_co)} doc(s)")
    print("walletCodes:", sorted(set(d.get("walletCode") for d in same_co)))

    # 2) Varre TODOS os docs da data e procura os termos no FixedIncome
    print(f"\n--- Procurando {NEEDLES} em toda a data {CONSUME_DATE} ---")
    cursor = coll.find({"consumeDate": CONSUME_DATE},
                       {"walletCode": 1, "companyId": 1, "position.FixedIncome": 1})
    hits = 0
    scanned = 0
    for d in cursor:
        scanned += 1
        p = d.get("position") or {}
        for fi in (p.get("FixedIncome") or []):
            if not isinstance(fi, dict):
                continue
            h = hay(fi)
            for needle in NEEDLES:
                if needle.upper() in h:
                    hits += 1
                    print(f"  HIT wallet={d.get('walletCode')} company={d.get('companyId')} "
                          f"[{needle}] -> {fi.get('AccountingGroupCode')} | {fi.get('Issuer')} | "
                          f"{fi.get('Ticker')} | {fi.get('IndexYieldRate')} | "
                          f"mat={str(fi.get('MaturityDate'))[:10]}")
                    break
    print(f"\nDocs varridos: {scanned}, hits: {hits}")


if __name__ == "__main__":
    main()
