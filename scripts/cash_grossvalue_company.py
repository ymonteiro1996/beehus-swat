"""Tabela de soma de GrossValue (position.Cash[].CashInvested[]) por data,
SEPARADA por wallet, para TODAS as wallets de uma company em rawBTGPosition.
NÃO soma entre wallets — cada wallet mantém seus próprios valores.

- Console: resumo por wallet (qtd datas c/ dado, qtd itens, soma do período).
- CSV pivot: linhas = consumeDate, colunas = walletCode (cada wallet separada),
  + linha TOTAL por wallet (soma da própria wallet no período).
- CSV long: uma linha por (walletCode, consumeDate, qtd itens, soma GrossValue).
Ambos CSV em UTF-8 BOM, separador ';' (Excel pt-BR).
"""
from __future__ import annotations

import csv
import io
import sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from db import db, client  # noqa: E402

COMPANY_ID = "58454495000109"


def to_float(v):
    if v is None:
        return 0.0
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return 0.0


def br(n):
    """Formata número no padrão pt-BR: 1.234.567,89."""
    return f"{n:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def sum_cash(pos):
    """(soma_grossvalue, qtd_itens) para position.Cash[].CashInvested[]."""
    total = 0.0
    n = 0
    cash = (pos or {}).get("Cash")
    if isinstance(cash, list):
        for bloco in cash:
            if not isinstance(bloco, dict):
                continue
            invested = bloco.get("CashInvested") or []
            if isinstance(invested, list):
                for item in invested:
                    if isinstance(item, dict):
                        total += to_float(item.get("GrossValue"))
                        n += 1
    return total, n


def main():
    if client is None:
        print("client is None — verifique data/user_connections.json", file=sys.stderr)
        return 1

    coll = db["rawBTGPosition"]
    q = {"companyId": COMPANY_ID}
    proj = {"walletCode": 1, "consumeDate": 1, "position.Cash": 1}

    # matrix[date][wallet] = soma  /  items[date][wallet] = qtd itens
    matrix = {}           # date -> {wallet: soma}
    items = {}            # date -> {wallet: qtd itens}
    long_rows = []        # (wallet, date, n_itens, soma)
    wallets = set()
    dates = set()
    wallet_total = {}     # wallet -> soma do período
    wallet_dates = {}     # wallet -> nº datas com dado
    wallet_items = {}     # wallet -> qtd itens no período

    for d in coll.find(q, proj):
        wallet = d.get("walletCode")
        date = d.get("consumeDate")
        total, n = sum_cash(d.get("position"))
        wallets.add(wallet)
        dates.add(date)
        matrix.setdefault(date, {})[wallet] = total
        items.setdefault(date, {})[wallet] = n
        long_rows.append((wallet, date, n, total))
        wallet_total[wallet] = wallet_total.get(wallet, 0.0) + total
        wallet_items[wallet] = wallet_items.get(wallet, 0) + n
        if total:
            wallet_dates[wallet] = wallet_dates.get(wallet, 0) + 1

    wallets_sorted = sorted(w for w in wallets if w is not None)
    dates_sorted = sorted(d for d in dates if d is not None)

    # ── Console: resumo POR WALLET (sem somar entre wallets) ──────────────
    print(f"\ncompanyId: {COMPANY_ID}   |   wallets: {len(wallets_sorted)}   |   datas: {len(dates_sorted)}\n")
    print(f"{'walletCode':<14}{'Datas c/ dado':>16}{'Qtd itens':>12}{'Soma GrossValue (período)':>28}")
    print("-" * 70)
    for wal in wallets_sorted:
        print(f"{wal:<14}{wallet_dates.get(wal, 0):>16}{wallet_items.get(wal, 0):>12}{br(wallet_total.get(wal, 0.0)):>28}")

    # ── CSV pivot: datas × wallets (cada wallet separada) ─────────────────
    out_pivot = Path(__file__).parent / f"cash_grossvalue_company_{COMPANY_ID}_pivot.csv"
    with open(out_pivot, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Data (consumeDate)"] + wallets_sorted)
        for date in dates_sorted:
            row = matrix.get(date, {})
            w.writerow([date] + [br(row.get(wal, 0.0)) for wal in wallets_sorted])
        # linha TOTAL: soma de cada wallet no período (NÃO mistura wallets)
        w.writerow(["TOTAL (período por wallet)"] + [br(wallet_total.get(wal, 0.0)) for wal in wallets_sorted])
    print(f"\nCSV pivot (datas × wallets): {out_pivot}")

    # ── CSV long ──────────────────────────────────────────────────────────
    out_long = Path(__file__).parent / f"cash_grossvalue_company_{COMPANY_ID}_long.csv"
    with open(out_long, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["walletCode", "Data (consumeDate)", "Qtd itens", "Soma GrossValue"])
        for wallet, date, n, total in sorted(long_rows, key=lambda r: (r[0] or "", r[1] or "")):
            w.writerow([wallet, date, n, br(total)])
    print(f"CSV long (1 linha por wallet+data): {out_long}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
