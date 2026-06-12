"""Inspeciona a wallet Beehus e descobre como o walletCode BTG se liga a ela."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db, resolve_wallet  # noqa: E402

WALLET_ID = "6a233027666abaefd3c806b9"
WALLET_CODES = ["008121162", "013988951"]


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
    w = resolve_wallet(WALLET_ID)
    print(f"=== wallets._id = {WALLET_ID} ===")
    if not w:
        print("  NAO encontrada")
    else:
        print(json.dumps(stringify(w), indent=2, ensure_ascii=False))

    # Procura quais wallets referenciam os walletCodes BTG (campo desconhecido)
    print("\n=== Procurando walletCodes em qualquer campo de wallets ===")
    for code in WALLET_CODES:
        matches = list(db.wallets.find(
            {"$or": [
                {"walletCode": code},
                {"accountNumber": code},
                {"btgAccount": code},
                {"account": code},
                {"consumptionIdentifiers": code},
                {"code": code},
            ]},
            {"name": 1, "companyId": 1, "entityId": 1}))
        print(f"  walletCode {code}: {len(matches)} match(es)")
        for m in matches:
            print(f"    {stringify(m)}")

    # Em rawBTGPosition: que walletId (Beehus) está gravado junto a esses codes?
    print("\n=== rawBTGPosition: campos de identificacao para os walletCodes ===")
    for code in WALLET_CODES:
        d = db["rawBTGPosition"].find_one(
            {"walletCode": code, "consumeDate": "2026-05-21"})
        if not d:
            print(f"  {code}: sem doc em 2026-05-21")
            continue
        top = {k: stringify(v) for k, v in d.items() if k != "position"}
        print(f"  {code}: {json.dumps(top, ensure_ascii=False)}")


if __name__ == "__main__":
    main()
