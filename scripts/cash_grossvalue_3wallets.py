"""Tabela por data das 3 wallets de histórico completo, sinalizando quando
NÃO há registro de Cash.

Para cada (wallet, consumeDate) classifica:
  - 'SEM DOC'        : não existe documento rawBTGPosition nessa data
  - 'SEM CASH'       : doc existe mas position.Cash vazio/ausente
  - 'SEM CASHINVEST' : Cash existe mas nenhum item em CashInvested
  - <valor>          : soma de GrossValue dos itens (pode ser 0,00)
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
WALLETS = ["008121162", "008356152", "013988951"]
# Prefixo de consumeDate (string ISO YYYY-MM-DD). "" = todas as datas.
MONTH_PREFIX = "2026-06"


def to_float(v):
    if v is None:
        return 0.0
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return 0.0


def br(n):
    return f"{n:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def classify(doc):
    """Retorna (status, soma, n_itens) para um documento (ou None)."""
    if doc is None:
        return "SEM DOC", None, 0
    pos = doc.get("position") or {}
    cash = pos.get("Cash")
    if not isinstance(cash, list) or not cash:
        return "SEM CASH", None, 0
    total = 0.0
    n = 0
    for bloco in cash:
        if not isinstance(bloco, dict):
            continue
        invested = bloco.get("CashInvested") or []
        if isinstance(invested, list):
            for item in invested:
                if isinstance(item, dict):
                    total += to_float(item.get("GrossValue"))
                    n += 1
    if n == 0:
        return "SEM CASHINVEST", None, 0
    return "OK", total, n


def main():
    if client is None:
        print("client is None — verifique data/user_connections.json", file=sys.stderr)
        return 1

    coll = db["rawBTGPosition"]

    # carrega docs das 3 wallets, indexados por (wallet, date)
    q = {"companyId": COMPANY_ID, "walletCode": {"$in": WALLETS}}
    if MONTH_PREFIX:
        q["consumeDate"] = {"$regex": f"^{MONTH_PREFIX}"}
    by_key = {}
    dates = set()
    for d in coll.find(q, {"walletCode": 1, "consumeDate": 1, "position.Cash": 1}):
        by_key[(d.get("walletCode"), d.get("consumeDate"))] = d
        dates.add(d.get("consumeDate"))
    dates_sorted = sorted(x for x in dates if x is not None)

    def cell(wal, date):
        status, total, n = classify(by_key.get((wal, date)))
        return (br(total) if status == "OK" else status), n, status

    # ── Console ───────────────────────────────────────────────────────────
    print(f"\ncompanyId: {COMPANY_ID}   |   wallets: {', '.join(WALLETS)}   |   datas: {len(dates_sorted)}")
    print("(célula = soma GrossValue OU motivo de não haver registro)\n")
    header = f"{'Data':<12}" + "".join(f"{w:>22}" for w in WALLETS)
    print(header)
    print("-" * len(header))
    for date in dates_sorted:
        line = f"{str(date):<12}"
        for wal in WALLETS:
            txt, _, _ = cell(wal, date)
            line += f"{txt:>22}"
        print(line)

    # contagem de datas sem registro por wallet
    print("\n--- Datas SEM registro de Cash, por wallet ---")
    for wal in WALLETS:
        faltas = []
        for date in dates_sorted:
            _, _, status = cell(wal, date)
            if status != "OK":
                faltas.append(f"{date} ({status})")
        print(f"\n{wal}: {len(faltas)} data(s) sem registro")
        for f in faltas:
            print(f"    {f}")

    # ── CSV ─────────────────────────────────────────────────────────────
    suffix = f"_{MONTH_PREFIX}" if MONTH_PREFIX else ""
    out = Path(__file__).parent / f"cash_grossvalue_3wallets{suffix}.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Data (consumeDate)"] + WALLETS)
        for date in dates_sorted:
            row = [date]
            for wal in WALLETS:
                txt, _, _ = cell(wal, date)
                row.append(txt)
            w.writerow(row)
    print(f"\nCSV: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
