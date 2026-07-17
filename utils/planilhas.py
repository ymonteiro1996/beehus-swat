"""Geração de planilhas .xlsx no formato de upload do Beehus.

Contexto:
Monta o workbook de posições no schema aceito pelo endpoint de upstream de
`unprocessedSecurityPositions`. Centralizado aqui porque o MESMO schema
(Data/Carteira/Ativo/Quant/PU/SaldoBruto/Caixa/Moeda) é usado por várias telas
(Carteira, Exceções, Repetir Posições) — regra do CLAUDE.md de reaproveitar em
utils/ em vez de duplicar.
"""
import io

from openpyxl import Workbook

# Cabeçalho fixo esperado pelo parser de upstream (ordem importa).
_HEADER = ["Data", "Carteira", "Ativo", "Quant", "PU", "SaldoBruto", "Caixa", "Moeda"]


def build_positions_xlsx(target_date, wallet_id, rows, cash=None,
                         currency_id="", cash_unprocessed_id="Caixa"):
    """Contexto:
    Gera os bytes de um .xlsx de posições para UMA (carteira, data). A linha de
    caixa só é incluída quando `cash` é informado — `cash=None` significa "não
    mexer no caixa", não "zerar". Retorna `bytes` prontos para upload.

    Parâmetros:
      target_date          — data ISO 'YYYY-MM-DD' da posição.
      wallet_id            — id da carteira.
      rows                 — lista de dicts {ativo, quantity, pu, balance}.
      cash                 — valor de caixa (ou None p/ omitir a linha).
      currency_id          — moeda (coluna Moeda).
      cash_unprocessed_id  — rótulo Ativo da linha de caixa (default "Caixa").

    Pseudocódigo:
      1. Cria o workbook e escreve o cabeçalho fixo.
      2. Para cada linha de ativo, escreve (data, carteira, ativo, qtd, pu,
         saldo, "Não", moeda).
      3. Se `cash` foi informado, escreve a linha de caixa (Ativo =
         cash_unprocessed_id, coluna Caixa = "Sim").
      4. Salva em memória e devolve os bytes.
    """
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Posicoes"
    sheet.append(list(_HEADER))
    for row in rows:
        sheet.append([
            target_date,
            wallet_id,
            row.get("ativo") or "",
            row.get("quantity") or 0,
            row.get("pu") or 0,
            row.get("balance") or 0,
            "Não",
            currency_id or "",
        ])
    if cash is not None:
        sheet.append([
            target_date,
            wallet_id,
            (cash_unprocessed_id or "Caixa"),
            0,
            0,
            cash,
            "Sim",
            currency_id or "",
        ])
    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer.getvalue()
