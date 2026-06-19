"""Lista issues security_unmapped por walletId e por todas as wallets da entity."""
from __future__ import annotations

import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402

WALLET_ID = "6a233027666abaefd3c806b9"
ENTITY_ID = "67c0a6ca71f5e8c88f76044f"
DATE = "2026-05-21"


def main():
    # Todas as wallets da entity
    print(f"=== Wallets da entity {ENTITY_ID} ===")
    wmap = {}
    for w in db.wallets.find({"entityId": ENTITY_ID},
                             {"name": 1, "accountCode": 1}):
        wid = str(w["_id"])
        wmap[wid] = (w.get("name"), w.get("accountCode"))
        print(f"  {wid}  acct={w.get('accountCode')}  {w.get('name')}")

    # Issues do walletId principal
    print(f"\n=== Issues security_unmapped walletId={WALLET_ID} date={DATE} ===")
    for it in db.issues.find({"walletId": WALLET_ID, "type": "security_unmapped",
                              "date": DATE}).sort("status", 1):
        print(f"  [{it.get('status'):7s}] {it.get('unprocessedSecurityId')}")

    # Issues de TODAS as wallets da entity nessa data
    print(f"\n=== Issues security_unmapped de TODAS as wallets da entity ({DATE}) ===")
    wids = list(wmap.keys())
    for it in db.issues.find({"walletId": {"$in": wids}, "type": "security_unmapped",
                              "date": DATE}).sort("walletId", 1):
        wid = it.get("walletId")
        nm = wmap.get(wid, ("?", "?"))
        print(f"  [{it.get('status'):7s}] acct={nm[1]} ({nm[0]}) :: {it.get('unprocessedSecurityId')}")


if __name__ == "__main__":
    main()
