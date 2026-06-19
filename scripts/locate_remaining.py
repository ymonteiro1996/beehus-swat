"""Localiza TENTOS LCA (detalhe completo) e os CDB NU FINANCEIRA (onde estiverem)."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402


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
    raw = db["rawBTGPosition"]

    # 1) TENTOS LCA detalhe completo (wallet 013988951, 2026-05-21)
    print("=== TENTOS LCA-25A02638302 @ walletCode 013988951 / 2026-05-21 ===")
    d = raw.find_one({"walletCode": "013988951", "consumeDate": "2026-05-21"},
                     {"position.FixedIncome": 1})
    tentos = None
    for fi in ((d or {}).get("position", {}).get("FixedIncome") or []):
        if "TENTOS" in str(fi.get("Issuer", "")).upper():
            tentos = fi
            break
    if tentos:
        slim = {k: stringify(v) for k, v in tentos.items() if k != "Acquisitions"}
        print(json.dumps(slim, indent=2, ensure_ascii=False))
    else:
        print("  NAO encontrado")

    # 2) CDB NU FINANCEIRA — busca global em issues (qualquer wallet/data)
    print("\n=== Issues com 'NU FINANCEIRA' (qualquer wallet, qualquer data) ===")
    n = 0
    for it in db.issues.find({"unprocessedSecurityId": {"$regex": "NU FINANCEIRA", "$options": "i"}}).limit(40):
        n += 1
        print(f"  [{it.get('status'):7s}] date={it.get('date')} wallet={it.get('walletId')} "
              f":: {it.get('unprocessedSecurityId')}")
    print(f"  total: {n}")

    # 3) CDB NU FINANCEIRA — busca em rawBTGPosition (qualquer data) FixedIncome
    print("\n=== rawBTGPosition FixedIncome com Issuer ~ 'NU FINANCEIRA' e CDB ===")
    cur = raw.find(
        {"position.FixedIncome.Issuer": {"$regex": "NU FINANCEIRA", "$options": "i"}},
        {"walletCode": 1, "companyId": 1, "consumeDate": 1, "position.FixedIncome": 1})
    seen = 0
    for doc in cur.limit(200):
        for fi in (doc.get("position", {}).get("FixedIncome") or []):
            if "NU FINANCEIRA" in str(fi.get("Issuer", "")).upper():
                seen += 1
                print(f"  {doc.get('consumeDate')} w={doc.get('walletCode')} co={doc.get('companyId')} "
                      f":: {fi.get('AccountingGroupCode')} | {fi.get('Ticker')} | "
                      f"{fi.get('IndexYieldRate')} | mat={str(fi.get('MaturityDate'))[:10]}")
    print(f"  ocorrencias NU FINANCEIRA (qualquer tipo): {seen}")


if __name__ == "__main__":
    main()
