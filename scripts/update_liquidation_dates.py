"""
Atualiza liquidationDate de 2026-04-01 até 2026-04-29 para 2026-04-30
nas transactions dos wallets especificados.

Usa MongoDB local para buscar IDs e a API Beehus para atualizar.
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

import requests

# ── Config ────────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("BEEHUS_TOKEN", "").strip()
if not TOKEN:
    sys.exit(
        "ERROR: BEEHUS_TOKEN environment variable is not set.\n"
        "Set it before running, e.g. (PowerShell): $env:BEEHUS_TOKEN = '<jwt>'\n"
        "or (bash): export BEEHUS_TOKEN='<jwt>'"
    )
HEADERS = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
BASE_URL = "https://controladoria.beehus.com.br/beehus/financial/transactions"

TARGET_WALLET_NAMES = [
    "JER PF BTG BRL",
    "LAP JER II PF BTG BRL",
    "JUL PF BTG BRL",
    "JUL PF BTG BRL II",
    "SQP PF BTG BRL",
    "LAP SOP II PF BTG BRL",
    "AMN PF BTG BRL",
    "LAP AMN PF BTG BRL II",
]

DATE_FROM = "2026-04-01"
DATE_TO   = "2026-04-29"
NEW_DATE  = "2026-04-30"


def main():
    # Offline maintenance script: connect to Mongo explicitly (the web app no
    # longer connects at import). URI from $SWAT_MONGO_URI or user_connections.json.
    from db import db, connect_for_cli
    connect_for_cli()

    # ── 1. Find wallet IDs ────────────────────────────────────────────────────
    print("=== Buscando wallets no MongoDB ===")
    wallets = list(db.wallets.find({"name": {"$in": TARGET_WALLET_NAMES}}, {"name": 1}))
    found_names = {w["name"]: str(w["_id"]) for w in wallets}

    print(f"Wallets encontrados ({len(found_names)}/{len(TARGET_WALLET_NAMES)}):")
    for name in TARGET_WALLET_NAMES:
        status = found_names.get(name, "NAO ENCONTRADO")
        print(f"  {name!r}: {status}")

    missing = [n for n in TARGET_WALLET_NAMES if n not in found_names]
    if missing:
        print(f"\nWARNING – wallets não encontrados: {missing}")

    wallet_id_strs = list(found_names.values())
    if not wallet_id_strs:
        print("Nenhum wallet encontrado. Abortando.")
        return

    # ── 2. Query transactions ─────────────────────────────────────────────────
    print(f"\n=== Buscando transactions (liquidationDate {DATE_FROM} a {DATE_TO}) ===")
    query = {
        "walletId": {"$in": wallet_id_strs},
        "liquidationDate": {"$gte": DATE_FROM, "$lte": DATE_TO},
        "trashed": {"$ne": True},
    }
    txs = list(db.transactions.find(query, {"_id": 1, "walletId": 1, "liquidationDate": 1, "description": 1}))
    print(f"Transactions encontradas: {len(txs)}")

    if not txs:
        print("Nenhuma transaction encontrada. Nada a fazer.")
        return

    # ── 3. Group by wallet for display ───────────────────────────────────────
    wallet_id_to_name = {v: k for k, v in found_names.items()}
    from collections import defaultdict
    by_wallet = defaultdict(list)
    for tx in txs:
        wname = wallet_id_to_name.get(str(tx["walletId"]), str(tx["walletId"]))
        by_wallet[wname].append(tx)

    for wname, wtxs in sorted(by_wallet.items()):
        print(f"  {wname}: {len(wtxs)} transactions")

    # ── 4. Update via API ─────────────────────────────────────────────────────
    print(f"\n=== Atualizando liquidationDate para {NEW_DATE} ===")
    total_ok  = 0
    total_err = 0

    for tx in txs:
        tx_id  = str(tx["_id"])
        liq_dt = tx.get("liquidationDate", "")
        desc   = (tx.get("description") or "")[:60]
        wname  = wallet_id_to_name.get(str(tx["walletId"]), "?")

        try:
            r = requests.patch(
                f"{BASE_URL}/{tx_id}",
                headers=HEADERS,
                json={"liquidationDate": NEW_DATE},
                timeout=20,
            )
            if 200 <= r.status_code < 300:
                print(f"  OK  [{wname}] {tx_id} | {liq_dt} -> {NEW_DATE} | {desc}")
                total_ok += 1
            else:
                print(f"  ERR [{wname}] {tx_id} | status={r.status_code} | {r.text[:150]}")
                total_err += 1
        except Exception as e:
            print(f"  EXC [{wname}] {tx_id} | {e}")
            total_err += 1

    print(f"\n=== RESUMO ===")
    print(f"Transactions encontradas : {len(txs)}")
    print(f"Atualizadas com sucesso  : {total_ok}")
    print(f"Erros                    : {total_err}")


if __name__ == "__main__":
    main()
