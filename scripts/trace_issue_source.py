"""Rastreia a origem dos ativos da issue securityMapping para um walletId/data."""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db  # noqa: E402
from bson import ObjectId  # noqa: E402

WALLET_ID = "6a233027666abaefd3c806b9"
POS_DATE = "2026-05-21"


def stringify(v):
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


def show_keys(label, d, skip=()):
    print(f"\n--- {label} top-level keys ---")
    for k, v in d.items():
        if k in skip:
            continue
        if isinstance(v, list):
            print(f"  {k}: list[{len(v)}]")
        elif isinstance(v, dict):
            print(f"  {k}: dict{list(v.keys())[:8]}")
        else:
            s = str(stringify(v))
            print(f"  {k}: {s[:90]}")


def main():
    # 1) unprocessedSecurityPositions para o walletId+date
    coll = db.unprocessedSecurityPositions
    for date_field in ("positionDate",):
        for q in ({"walletId": WALLET_ID, date_field: POS_DATE},
                  {"walletId": ObjectId(WALLET_ID), date_field: POS_DATE}):
            docs = list(coll.find(q))
            if docs:
                print(f"unprocessedSecurityPositions match {q}: {len(docs)} doc(s)")
                show_keys("unprocessedSecurityPositions[0]", docs[0], skip=())
                # Procura array de securities/positions dentro
                for key in ("securities", "positions", "securityPositions", "items", "data"):
                    arr = docs[0].get(key)
                    if isinstance(arr, list) and arr:
                        print(f"\n  {key}[0]:")
                        print(json.dumps(stringify(arr[0]), indent=2, ensure_ascii=False)[:1500])
                        break
                out = Path(__file__).parent / "trace_unproc.json"
                out.write_text(json.dumps([stringify(d) for d in docs], indent=2, ensure_ascii=False), encoding="utf-8")
                print(f"  dump -> {out}")
                break

    # 2) issues do tipo securityMapping para essa wallet
    print("\n=== issues (securityMapping) ===")
    iq = {"walletId": WALLET_ID}
    n = db.issues.count_documents(iq)
    print(f"issues walletId={WALLET_ID} (str): {n}")
    if n == 0:
        iq = {"walletId": ObjectId(WALLET_ID)}
        n = db.issues.count_documents(iq)
        print(f"issues walletId (ObjectId): {n}")
    for it in db.issues.find(iq).limit(5):
        print(f"\n  issue type={it.get('type')} status={it.get('status')} date={it.get('date')}")
        show_keys("issue", it)

    # 3) wallet dona do accountCode 013988951 (onde está o TENTOS LCA)
    print("\n=== wallet com accountCode 013988951 ===")
    for m in db.wallets.find({"accountCode": "013988951"},
                             {"name": 1, "companyId": 1, "entityId": 1, "accountCode": 1}):
        print(f"  {stringify(m)}")


if __name__ == "__main__":
    main()
