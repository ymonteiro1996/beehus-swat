"""Inspeciona rawBTGPosition para companyId/consumeDate fornecidos.

Lista collections candidatas (case-insensitive), conta documentos,
e imprime estrutura/exemplo de campos para entender o schema.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from collections import Counter

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db, client  # noqa: E402


COMPANY_ID = "58454495000109"
CONSUME_DATE = "2026-05-21"


def _stringify(v):
    from bson import ObjectId
    from datetime import date, datetime
    if isinstance(v, ObjectId):
        return f"ObjectId({v})"
    if isinstance(v, (date, datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: _stringify(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_stringify(x) for x in v]
    return v


def main() -> int:
    if client is None:
        print("client is None — verifique data/user_connections.json", file=sys.stderr)
        return 1

    # 1. Find candidate collections
    all_colls = sorted(db.list_collection_names())
    candidates = [c for c in all_colls if "btg" in c.lower() and "position" in c.lower()]
    print("Collections com 'btg' + 'position':")
    for c in candidates:
        print(f"  - {c}")
    if not candidates:
        # broader: any "btg" coll
        broader = [c for c in all_colls if "btg" in c.lower()]
        print("Sem candidatas. Collections com 'btg':")
        for c in broader:
            print(f"  - {c}")
        # broader: any "raw" coll
        broader2 = [c for c in all_colls if "raw" in c.lower()]
        print("Collections com 'raw':")
        for c in broader2:
            print(f"  - {c}")
        return 0

    for coll_name in candidates:
        coll = db[coll_name]
        print(f"\n=== {coll_name} ===")
        total = coll.estimated_document_count()
        print(f"Total estimado de docs: {total}")

        # Tenta vários campos possíveis para companyId
        company_variants = [
            {"companyId": COMPANY_ID},
            {"companyId": int(COMPANY_ID) if COMPANY_ID.isdigit() else COMPANY_ID},
            {"company_id": COMPANY_ID},
            {"taxId": COMPANY_ID},
        ]
        date_variants = [
            {"consumeDate": CONSUME_DATE},
            {"consume_date": CONSUME_DATE},
        ]

        for cq in company_variants:
            n = coll.count_documents(cq, limit=1)
            if n:
                print(f"  match (sem date): {cq} -> >=1 doc")
                break
        else:
            print(f"  nenhum filtro companyId casou: {company_variants}")

        # Combina
        full_q = {"companyId": COMPANY_ID, "consumeDate": CONSUME_DATE}
        n_full = coll.count_documents(full_q)
        print(f"  count({full_q}) = {n_full}")

        if n_full == 0:
            # tenta achar algum doc dessa company e mostrar consumeDates disponíveis
            sample = list(coll.find({"companyId": COMPANY_ID}, {"consumeDate": 1}).limit(5))
            if sample:
                dates = Counter()
                for d in coll.find({"companyId": COMPANY_ID}, {"consumeDate": 1}).limit(2000):
                    dates[d.get("consumeDate")] += 1
                print(f"  consumeDates disponíveis para companyId={COMPANY_ID} (top 10):")
                for dt, c in sorted(dates.items(), key=lambda x: -x[1])[:10]:
                    print(f"    {dt}: {c}")

        # Mostra 1 doc completo (truncado)
        first = coll.find_one(full_q)
        if first:
            print("\n  --- doc[0] (chaves de topo): ---")
            for k in first.keys():
                v = first[k]
                tname = type(v).__name__
                if isinstance(v, list):
                    print(f"    {k}: list[{tname}] len={len(v)}")
                else:
                    s = str(v)
                    if len(s) > 100:
                        s = s[:100] + "…"
                    print(f"    {k} ({tname}): {s}")

            # Procura array com posições/securities
            for key in ("positions", "securities", "items", "data", "assets"):
                if isinstance(first.get(key), list) and first[key]:
                    print(f"\n  --- {key}[0]: ---")
                    print(json.dumps(_stringify(first[key][0]), indent=2, ensure_ascii=False)[:2500])
                    break

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
