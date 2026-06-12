"""Consolida TODOS os meses já gerados (EMR_*_positions.json / _transactions.json)
num ÚNICO workbook, em vez de um arquivo por data.

Saída: <out> (default data/.tmp/EMR_consolidado.xlsx) com 2 abas:
  - Posicoes   : formato de upload da Beehus (8 colunas exatas), todos os meses
                 empilhados (coluna Data distingue cada mês). Continua sendo um
                 upload válido — Beehus indexa por companyId+walletId+positionDate.
  - Transacoes : todas as transações, uma linha por lançamento.

Reexecute após gerar/atualizar meses (inclusive os criptografados via mb_batch
--password) para incorporá-los.

Uso:
  python scripts/mb_consolidate.py "data/.tmp"
"""
import sys, os, glob, json, argparse, tempfile
from openpyxl import Workbook
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mb_to_beehus import HEADERS as POS_HEADERS, row_to_beehus

TX_COLS = ["liquidationDate", "operationDate", "walletId", "entityId", "securityId",
           "balance", "description", "beehusTransactionType", "currencyId",
           "inputType", "hide", "comment", "companyId"]


def _month(path):
    # .../EMR_202507_positions.json -> "202507"
    b = os.path.basename(path)
    return b.split("_")[1] if "_" in b else b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    skip = lambda f: "consolidado" in os.path.basename(f)   # não recolher o próprio output
    pos_files = sorted(f for f in glob.glob(os.path.join(args.folder, "EMR_*_positions.json")) if not skip(f))
    tx_files = sorted(f for f in glob.glob(os.path.join(args.folder, "EMR_*_transactions.json")) if not skip(f))

    wb = Workbook()

    # ── Posicoes (formato Beehus, todos os meses) ──
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(POS_HEADERS)
    pos_total, months = 0, []
    for f in pos_files:
        data = json.load(open(f, encoding="utf-8"))
        rows = data.get("unprocessedSecurities") or []
        for r in rows:
            ws.append(row_to_beehus(r))
        pos_total += len(rows)
        months.append((_month(f), len(rows)))

    # ── Transacoes (todos os meses) — aba de revisão + JSON de upload Beehus ──
    ws = wb.create_sheet("Transacoes")
    ws.append(TX_COLS)
    all_tx = []
    company_id = None
    for f in tx_files:
        data = json.load(open(f, encoding="utf-8"))
        company_id = company_id or data.get("companyId")
        for t in (data.get("transactions") or []):
            ws.append([t.get(c, "") for c in TX_COLS])
            all_tx.append(t)

    out = args.out or os.path.join(args.folder, "EMR_consolidado.xlsx")
    wb.save(out)

    # JSON único no formato de upload da Beehus (FILE_GENERATION #1 / create_transaction)
    tx_json = os.path.splitext(out)[0] + "_transactions.json"
    payload = {"companyId": company_id, "transactions": all_tx}
    fd, tmp = tempfile.mkstemp(suffix=".json", dir=os.path.dirname(out) or ".")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    os.replace(tmp, tx_json)

    print(f"Meses incluídos: {[m for m, _ in months]}")
    print(f"Posicoes: {pos_total} linhas | Transacoes: {len(all_tx)} linhas")
    print("Por mês (posições):", {m: n for m, n in months})
    print("->", out)
    print("->", tx_json)


if __name__ == "__main__":
    main()
