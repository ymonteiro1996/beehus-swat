"""Upload transactions from chunk JSON to Beehus financial API."""
import json
import os
import sys
import time
from pathlib import Path

import requests

URL = "https://controladoria.beehus.com.br/beehus/financial/transactions"
TOKEN = os.environ.get("BEEHUS_TOKEN", "").strip()
if not TOKEN:
    sys.exit(
        "ERROR: BEEHUS_TOKEN environment variable is not set.\n"
        "Set it before running, e.g. (PowerShell): $env:BEEHUS_TOKEN = '<jwt>'\n"
        "or (bash): export BEEHUS_TOKEN='<jwt>'"
    )
HEADERS = {
    "Authorization": f"Bearer {TOKEN}",
    "Content-Type": "application/json",
}

CHUNK_PATH = Path(
    r"c:\Users\gyamaguti\AppData\Local\Packages\5319275A.WhatsAppDesktop_cv1g1gvanyjgm"
    r"\LocalState\sessions\F653C3C1D1DE241AAB4D887FDD0850A1EF037115\transfers\2026-17"
    r"\aggregated_cota_green_pl_split_chunk_1.json"
)
DOWNLOADS_DIR = Path(r"c:\Users\gyamaguti\Downloads")


def build_payload(tx: dict) -> dict:
    """Force inputType=sheets and hide=True per user instruction."""
    payload = {
        "balance": tx["balance"],
        "beehusTransactionType": tx["beehusTransactionType"],
        "comment": tx.get("comment", ""),
        "companyId": tx["companyId"],
        "currencyId": tx["currencyId"],
        "description": tx["description"],
        "groupingId": tx["groupingId"],
        "hide": True,
        "inputType": "sheets",
        "liquidationDate": tx["liquidationDate"],
        "operationDate": tx["operationDate"],
    }
    return payload


def resolve_paths(path_args: list[str]) -> list[Path]:
    """Resolve --path / --chunk / --all arguments to a list of chunk files."""
    paths: list[Path] = []
    for arg in path_args:
        if arg == "--all":
            found = sorted(
                DOWNLOADS_DIR.glob("aggregated_cota_green_pl_split_chunk_*.json"),
                key=lambda p: int(p.stem.rsplit("_", 1)[-1]),
            )
            paths.extend(found)
        elif arg.startswith("--chunk="):
            n = int(arg.split("=", 1)[1])
            paths.append(DOWNLOADS_DIR / f"aggregated_cota_green_pl_split_chunk_{n}.json")
        elif arg.startswith("--path="):
            paths.append(Path(arg.split("=", 1)[1]))
    return paths or [CHUNK_PATH]


def main(
    dry_run: bool = False,
    limit: int | None = None,
    offset: int = 0,
    chunk_paths: list[Path] | None = None,
) -> int:
    chunk_paths = chunk_paths or [CHUNK_PATH]
    transactions: list[dict] = []
    for cp in chunk_paths:
        data = json.loads(cp.read_text(encoding="utf-8"))
        chunk_txs = data["transactions"]
        print(f"[load] {cp.name}: {len(chunk_txs)} transações")
        transactions.extend(chunk_txs)
    if offset:
        transactions = transactions[offset:]
    if limit is not None:
        transactions = transactions[:limit]

    total = len(transactions)
    print(f"[info] {total} transações a enviar (dry_run={dry_run})")

    ok = 0
    fail = 0
    failures: list[tuple[int, int, str]] = []

    for idx, tx in enumerate(transactions, start=1):
        payload = build_payload(tx)
        if dry_run:
            print(f"[dry] {idx}/{total} {payload['operationDate']} {payload['balance']:.4f}")
            ok += 1
            continue

        try:
            resp = requests.post(URL, headers=HEADERS, json=payload, timeout=180)
        except requests.RequestException as exc:
            fail += 1
            failures.append((idx, -1, repr(exc)))
            print(f"[err] {idx}/{total} request error: {exc}")
            continue

        if 200 <= resp.status_code < 300:
            ok += 1
            print(f"[ok ] {idx}/{total} {resp.status_code} {payload['operationDate']} {payload['balance']:.4f}")
        else:
            fail += 1
            body = resp.text[:500]
            failures.append((idx, resp.status_code, body))
            print(f"[err] {idx}/{total} {resp.status_code} {body}")

        time.sleep(0.05)

    print(f"\n[summary] ok={ok} fail={fail} total={total}")
    if failures:
        print("[failures]")
        for idx, status, body in failures:
            print(f"  #{idx} status={status} body={body}")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    dry = "--dry-run" in args
    lim = None
    off = 0
    for a in args:
        if a.startswith("--limit="):
            lim = int(a.split("=", 1)[1])
        elif a.startswith("--offset="):
            off = int(a.split("=", 1)[1])
    paths = resolve_paths(args)
    sys.exit(main(dry_run=dry, limit=lim, offset=off, chunk_paths=paths))
