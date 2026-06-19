"""Upload the 10 unidentified buy/sell transactions to the Beehus API.

Mirrors the per-row submit pattern used by Correções > Transações > Enviar
selecionadas via API: one HTTP call per row, sequential, 401 short-circuits
the rest. `beehusTransactionType` is hardcoded to "buySell" (per operator
instruction); `securityId` is omitted because the source rows had it empty.

This script bypasses the local Flask + SWAT cookie auth and calls the
upstream `POST /beehus/financial/transactions` directly, using a JWT token
read from the BEEHUS_TOKEN env var. Designed for the operator to run
manually so the agent harness never has to handle the credential.

Usage (PowerShell):
    $env:BEEHUS_TOKEN = "<paste JWT here>"
    python scripts/upload_intraday_buysell.py            # dry run — prints what would be sent
    python scripts/upload_intraday_buysell.py --send     # actually POST
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from beehus_api import (  # noqa: E402
    BeehusAPIError,
    BeehusAuthError,
    create_transaction,
    set_token,
)


# ── The 10 transactions from the operator's payload ────────────────────────
# Resolved companyId (23313334000110) and entityId (67c0a6fb71f5e8c88f760455)
# come from db.wallets — all three wallets belong to the same company.
COMPANY_ID = "23313334000110"

ROWS = [
    {"balance": -8222302.5,  "currencyId": "BRL", "description": "Venda Opcao - DI1FF35",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-29",
     "operationDate": "2026-04-29", "price": 32889.21, "quantity": -250,
     "walletId": "680a9ce43b2296d8612711ee"},
    {"balance":  8306052.5,  "currencyId": "BRL", "description": "Compra Opcao - DI1FF35",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-28",
     "operationDate": "2026-04-28", "price": 33224.21, "quantity":  250,
     "walletId": "680a9ce43b2296d8612711ee"},
    {"balance": -8283527.5,  "currencyId": "BRL", "description": "Venda Opcao - DI1FF35",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-27",
     "operationDate": "2026-04-27", "price": 33134.11, "quantity": -250,
     "walletId": "680a9ce43b2296d8612711ee"},
    {"balance":   358208.02, "currencyId": "BRL", "description": "Venda LFT - VENDA DE LFT",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-24",
     "operationDate": "2026-04-24", "price": 1, "quantity":  358208.02,
     "walletId": "680a9ce43b2296d8612711e0"},
    {"balance": -3329568,    "currencyId": "BRL", "description": "Venda Opcao - DI1FF35",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-15",
     "operationDate": "2026-04-15", "price": 33295.68, "quantity": -100,
     "walletId": "680a9ce43b2296d8612711ee"},
    {"balance":  -281981,    "currencyId": "BRL", "description": "Venda Futuro - WINFM26",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-10",
     "operationDate": "2026-04-10", "price": 40283, "quantity": -7,
     "walletId": "680a9ce43b2296d8612711f5"},
    {"balance": -2013960,    "currencyId": "BRL", "description": "Venda Futuro - INDFM26",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-10",
     "operationDate": "2026-04-10", "price": 201396, "quantity": -10,
     "walletId": "680a9ce43b2296d8612711f5"},
    {"balance":  -443113,    "currencyId": "BRL", "description": "Venda Futuro - WINFM26",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-10",
     "operationDate": "2026-04-10", "price": 40283, "quantity": -11,
     "walletId": "680a9ce43b2296d8612711ee"},
    {"balance":   433796,    "currencyId": "BRL", "description": "Compra Futuro - WINFJ26",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-10",
     "operationDate": "2026-04-10", "price": 39436, "quantity":  11,
     "walletId": "680a9ce43b2296d8612711ee"},
    {"balance":  3339248,    "currencyId": "BRL", "description": "Compra Opcao - DI1FF35",
     "entityId": "67c0a6fb71f5e8c88f760455", "liquidationDate": "2026-04-10",
     "operationDate": "2026-04-10", "price": 33392.48, "quantity":  100,
     "walletId": "680a9ce43b2296d8612711ee"},
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--send", action="store_true",
                   help="Actually POST to the Beehus API. Without this it's a dry run.")
    args = p.parse_args()

    if args.send:
        token = (os.environ.get("BEEHUS_TOKEN") or "").strip()
        if not token:
            print("ERR: set BEEHUS_TOKEN env var to the JWT before running with --send")
            return 2
        set_token(token)

    print(f"{'POSTING' if args.send else 'DRY RUN'} {len(ROWS)} transaction(s) — "
          f"companyId={COMPANY_ID}, beehusTransactionType=buySell, securityId omitted")
    print()

    ok = 0
    errors = []
    for i, r in enumerate(ROWS, start=1):
        label = f"[{i:>2}/{len(ROWS)}] {r['description']:<32} {r['liquidationDate']} " \
                f"wallet={r['walletId'][-6:]} balance={r['balance']:>14,.2f}"
        if not args.send:
            print(label, "  (dry run)")
            continue
        try:
            result = create_transaction(
                company_id=COMPANY_ID,
                entity_id=r["entityId"],
                wallet_id=r["walletId"],
                balance=r["balance"],
                operation_date=r["operationDate"],
                liquidation_date=r["liquidationDate"],
                currency_id=r["currencyId"],
                transaction_type="buySell",
                description=r["description"],
                quantity=r.get("quantity"),
                price=r.get("price"),
            )
            created_id = (result or {}).get("_id") or (result or {}).get("id") or "?"
            print(label, f"  ✓ created _id={created_id}")
            ok += 1
        except BeehusAuthError as e:
            print(label, f"  ✗ AUTH error — stopping. {e}")
            errors.append((i, "auth_error", str(e)))
            break
        except BeehusAPIError as e:
            body = getattr(e, "body", None)
            body_short = (json.dumps(body, ensure_ascii=False)[:200]
                          if isinstance(body, (dict, list)) else str(body)[:200])
            print(label, f"  ✗ {getattr(e, 'status', '?')} — {body_short}")
            errors.append((i, getattr(e, "status", None), str(e)))
        except Exception as e:  # network / unexpected
            print(label, f"  ✗ {type(e).__name__}: {e}")
            errors.append((i, "exception", str(e)))

    print()
    if not args.send:
        print(f"Dry run complete. Re-run with --send to actually POST.")
        return 0
    print(f"Done. {ok} ok, {len(errors)} error(s).")
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
