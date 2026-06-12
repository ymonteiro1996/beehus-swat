"""Tabela de soma de GrossValue (position.Cash[].CashInvested[]) por data.

Para o walletCode informado, percorre rawBTGPosition e, para cada consumeDate,
soma o GrossValue de TODOS os itens presentes no array Cash[].CashInvested[].
Imprime a tabela em formato pt-BR e grava um CSV (UTF-8 BOM, separador ';').
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

WALLET_CODE = "008356152"


def to_float(v):
    """GrossValue vem como string ('910.96'). Converte com segurança."""
    if v is None:
        return 0.0
    try:
        return float(str(v).strip())
    except (ValueError, TypeError):
        return 0.0


def br(n):
    """Formata número no padrão pt-BR: 1.234.567,89."""
    return f"{n:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def main():
    if client is None:
        print("client is None — verifique data/user_connections.json", file=sys.stderr)
        return 1

    coll = db["rawBTGPosition"]
    rows = []  # (consumeDate, n_itens, soma_grossvalue)

    for d in coll.find({"walletCode": WALLET_CODE}).sort("consumeDate", 1):
        consume_date = d.get("consumeDate")
        pos = d.get("position") or {}
        cash = pos.get("Cash")
        total = 0.0
        n_itens = 0
        if isinstance(cash, list):
            for bloco in cash:
                if not isinstance(bloco, dict):
                    continue
                invested = bloco.get("CashInvested") or []
                if isinstance(invested, list):
                    for item in invested:
                        if isinstance(item, dict):
                            total += to_float(item.get("GrossValue"))
                            n_itens += 1
        rows.append((consume_date, n_itens, total))

    # ── Tabela no console ─────────────────────────────────────────────────
    print(f"\nwalletCode: {WALLET_CODE}   |   documentos: {len(rows)}\n")
    print(f"{'Data (consumeDate)':<22}{'Qtd itens':>12}{'Soma GrossValue':>22}")
    print("-" * 56)
    soma_geral = 0.0
    for consume_date, n_itens, total in rows:
        soma_geral += total
        print(f"{str(consume_date):<22}{n_itens:>12}{br(total):>22}")
    print("-" * 56)
    print(f"{'TOTAL GERAL':<22}{'':>12}{br(soma_geral):>22}")

    # ── CSV (Excel pt-BR: BOM + separador ';') ────────────────────────────
    out = Path(__file__).parent / f"cash_grossvalue_{WALLET_CODE}.csv"
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["Data (consumeDate)", "Qtd itens", "Soma GrossValue"])
        for consume_date, n_itens, total in rows:
            w.writerow([consume_date, n_itens, br(total)])
        w.writerow(["TOTAL GERAL", "", br(soma_geral)])
    print(f"\nCSV gravado em: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
