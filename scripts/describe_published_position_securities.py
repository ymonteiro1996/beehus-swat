"""Schema discovery for any MongoDB collection.

Reads a *small sample* of documents (default 100) and prints **only field
names + types + nullability**. No values are included in the output, so
nothing identifying leaves the database.

Usage:
    python scripts/describe_published_position_securities.py [collection] [sample_size]
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import db as db_module  # noqa: E402
from db import db        # noqa: E402


def type_name(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, int):
        return "int"
    if isinstance(v, float):
        return "float"
    if isinstance(v, str):
        return "string"
    if isinstance(v, list):
        if not v:
            return "array<empty>"
        inner = {type_name(x) for x in v}
        return f"array<{'|'.join(sorted(inner))}>"
    if isinstance(v, dict):
        return f"object<{','.join(sorted(v.keys()))}>"
    cls = type(v).__name__
    if cls == "ObjectId":
        return "ObjectId"
    if cls == "datetime":
        return "datetime"
    if cls == "Decimal128":
        return "Decimal128"
    return cls


def main(collection_name: str = "publishedPositionSecurities", n: int = 100) -> None:
    if not db._ready():
        print("ERROR: DB not initialized — register a connection at /setup", file=sys.stderr)
        sys.exit(1)

    coll = db[collection_name]
    cursor = coll.find().limit(n)
    docs = list(cursor)
    if not docs:
        print(f"Collection '{collection_name}' is empty.")
        return

    print(f"=== {collection_name} ===")
    print(f"Sampled {len(docs)} document(s).\n")

    field_types: dict[str, set[str]] = {}
    field_seen: dict[str, int] = {}

    for doc in docs:
        for k, v in doc.items():
            field_types.setdefault(k, set()).add(type_name(v))
            field_seen[k] = field_seen.get(k, 0) + 1

    print(f"{'Field':<32} {'Seen':>6}  Types")
    print("-" * 80)
    for k in sorted(field_types):
        seen   = field_seen[k]
        nullable = "yes" if "null" in field_types[k] or seen < len(docs) else "no"
        types  = "|".join(sorted(t for t in field_types[k] if t != "null"))
        print(f"{k:<32} {seen:>6}  ({nullable:<3}) {types}")


if __name__ == "__main__":
    coll = sys.argv[1] if len(sys.argv) > 1 else "publishedPositionSecurities"
    n    = int(sys.argv[2]) if len(sys.argv) > 2 else 100
    main(coll, n)
