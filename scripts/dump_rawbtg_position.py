"""Dump structure of `position` field for rawBTGPosition docs.

Goal: find all distinct asset types/keys in `position` so we can enumerate
the ativos to register.
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


def main():
    coll = db["rawBTGPosition"]
    q = {"companyId": COMPANY_ID, "consumeDate": CONSUME_DATE}
    cursor = coll.find(q)

    top_keys = Counter()
    pos_keys = Counter()
    sample_per_key = {}

    docs = list(cursor)
    print(f"Total docs: {len(docs)}")

    for d in docs:
        pos = d.get("position") or {}
        for k in pos.keys():
            pos_keys[k] += 1
            if k not in sample_per_key:
                v = pos[k]
                if isinstance(v, list) and v:
                    sample_per_key[k] = v[0]
                else:
                    sample_per_key[k] = v

    print("\n=== position.<key> distribution ===")
    for k, c in pos_keys.most_common():
        sample = sample_per_key.get(k)
        kind = type(sample).__name__
        extra = ""
        if isinstance(sample, list):
            extra = f" [list len from first occurrence]"
        elif isinstance(sample, dict):
            extra = f" dict_keys={list(sample.keys())[:8]}"
        print(f"  {k:40s} count={c:5d}  type={kind}{extra}")

    # Dump first non-trivial sample for each key
    out_path = Path(__file__).parent / "rawbtg_position_samples.json"
    serializable = {k: stringify(v) for k, v in sample_per_key.items()}
    out_path.write_text(json.dumps(serializable, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nSamples gravados em: {out_path}")

    # Inspect known array-typed keys (asset containers per BTG schema)
    print("\n=== Inspecting common asset-list keys ===")
    list_keys = [k for k, v in sample_per_key.items() if isinstance(v, (dict, list))]
    print(f"Keys with dict/list samples: {list_keys}")


if __name__ == "__main__":
    main()
