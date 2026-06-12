"""Converte o positions JSON (unprocessedSecurities, gerado por mb_generate.py)
para o XLSX de **upload da Beehus** — colunas idênticas a
pages/excecoes._build_xlsx (sheet 'Posicoes'):

    Data | Carteira | Ativo | Quant | PU | SaldoBruto | Caixa | Moeda

O `companyId` NÃO é coluna — vai no form do multipart
(POST /beehus/financial/positions/unprocessed-security-positions/file).

Para VALIDAÇÃO gera um único workbook com todas as carteiras empilhadas
(distinguidas pela coluna Carteira). No upload real a Beehus recebe
1 arquivo por carteira. Use --split para emitir 1 XLSX por carteira.

Uso:
  python scripts/mb_to_beehus.py data/.tmp/EMR_202507_positions.json
  python scripts/mb_to_beehus.py data/.tmp/EMR_202507_positions.json --split
"""
import sys, os, json, argparse, re
from openpyxl import Workbook

HEADERS = ["Data", "Carteira", "Ativo", "Quant", "PU", "SaldoBruto", "Caixa", "Moeda"]


def row_to_beehus(r):
    return [
        r.get("date") or "",
        r.get("walletId") or "",        # PLACEHOLDER (rótulo) até trocar pelo walletId real
        r.get("security") or "",        # vira unprocessedId no upstream
        r.get("quantity") or 0,         # não existe na DataWM -> 0
        r.get("pu") or 0,               # não existe na DataWM -> 0
        r.get("balance") or 0,
        "Sim" if r.get("cashAccount") == "Sim" else "Não",
        r.get("currencyId") or "",
    ]


def write_wb(path, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(HEADERS)
    for r in rows:
        ws.append(row_to_beehus(r))
    wb.save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("positions_json")
    ap.add_argument("--split", action="store_true",
                    help="1 XLSX por carteira (como no upload real)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    data = json.load(open(args.positions_json, encoding="utf-8"))
    rows = data.get("unprocessedSecurities") or []
    company_id = data.get("companyId", "")

    base = os.path.splitext(args.positions_json)[0]  # .../EMR_202507_positions

    if args.split:
        groups = {}
        for r in rows:
            groups.setdefault(r.get("walletId") or "", []).append(r)
        outdir = args.out or (base + "_beehus")
        os.makedirs(outdir, exist_ok=True)
        for wallet, grp in groups.items():
            safe = re.sub(r"[^A-Za-z0-9_-]+", "_", wallet) or "sem_carteira"
            p = os.path.join(outdir, f"{safe}.xlsx")
            write_wb(p, grp)
            print(f"  {wallet:12} {len(grp):3} linhas -> {p}")
        print(f"\ncompanyId (form do multipart): {company_id}")
    else:
        out = args.out or (base + "_beehus.xlsx")
        write_wb(out, rows)
        print(f"{len(rows)} linhas -> {out}")
        print(f"companyId (form do multipart): {company_id}")


if __name__ == "__main__":
    main()
