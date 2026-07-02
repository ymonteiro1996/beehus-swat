"""High-level functions for /beehus/financial/positions.

One Python function per logical operation. Returns parsed dicts.
Reusable from any blueprint — does not depend on Flask.
"""
from .client import request, request_multipart


def _csv(v) -> str:
    """Join a list/tuple/set of ids into a comma-separated query value.
    A bare string passes through; None/empty becomes ''."""
    if isinstance(v, (list, tuple, set)):
        return ",".join(str(x) for x in v if x)
    return str(v or "")


# Servidores limitam o tamanho da request-line/URL (~8KB → HTTP 414). Um CSV de
# `walletIds` da empresa inteira (centenas de ids de 24 chars) estoura fácil, então
# dividimos os ids em blocos URL-safe, fazemos 1 GET por bloco e concatenamos.
# Blocos são conjuntos DISJUNTOS de carteiras → não há docs duplicados p/ dedupe.
_MAX_WALLET_IDS_PER_REQUEST = 150


def _id_list(v):
    """Normaliza wallet_ids (escalar/iterável) para list[str], ou None."""
    if v is None:
        return None
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if x]
    return [str(v)] if v else []


def _get_positions_chunked(path, base_params, wallet_ids, timeout):
    """GET `path` dividindo um `walletIds` grande em blocos URL-safe e
    concatenando os resultados (lista). Evita HTTP 414. Erro em qualquer bloco
    propaga (o caller trata falha parcial como falha total)."""
    ids = _id_list(wallet_ids)

    def _one(chunk):
        params = dict(base_params)
        params["walletIds"] = _csv(chunk)
        out = request("GET", path, params=params, timeout=timeout)
        return out if isinstance(out, list) else []

    if ids and len(ids) > _MAX_WALLET_IDS_PER_REQUEST:
        merged = []
        for i in range(0, len(ids), _MAX_WALLET_IDS_PER_REQUEST):
            merged.extend(_one(ids[i:i + _MAX_WALLET_IDS_PER_REQUEST]))
        return merged
    return _one(ids)


# ── READ endpoints (substituem leituras diretas do Mongo) ───────────────────

def get_processed_position(
    *,
    company_id: str,
    date: str,
    wallet_ids=None,
    timeout: int = 60,
) -> list:
    """GET /beehus/financial/positions/processed-position — READ.

    Posições processadas da empresa numa `date` (ISO YYYY-MM-DD), opcionalmente
    restritas a `wallet_ids` (lista/str → CSV em `walletIds`). A resposta é uma
    LISTA de envelopes por carteira, cada um com TRÊS blocos (confirmado em
    produção):

        {
          "position":     {"_id", "positionDate", "walletId"{populado},
                            "companyId", "trashed", "securities": [...],
                            "totalKlassContribution"/"...Balances", ...},
          "provisions":   [...],
          "cashAccounts": [{"_id", "unprocessedId", "walletId", "currency",
                            "values": [{"date", "value"}]}]  # values = histórico
                            completo, independente da `date` consultada
        }

    IMPORTANTE: os ativos ficam em `item["position"]["securities"]` (NÃO no topo).
    `cashAccounts[].values` traz o histórico completo de caixa (mesmo shape do
    Mongo) — usado por `beehus_catalog.cash_sums_by_dates`. Retorna `[]` se a
    resposta não for uma lista.

    Nota: o endpoint recebe uma data única. Para "posição anterior" / "mais
    recente ≤ D" / várias datas, o chamador faz N chamadas ou agrega no cliente.

    `wallet_ids` grandes são divididos em blocos URL-safe (evita HTTP 414).
    """
    return _get_positions_chunked(
        "/beehus/financial/positions/processed-position",
        {"companyId": company_id, "date": date},
        wallet_ids, timeout,
    )


def get_unprocessed_security_positions(
    *,
    company_id: str,
    initial_date: str,
    final_date: str,
    wallet_ids=None,
    timeout: int = 60,
) -> list:
    """GET /beehus/financial/positions/unprocessed-security-positions — READ.

    Devolve as posições brutas (`unprocessedSecurityPositions`) da empresa na
    janela `initial_date`..`final_date` (ISO), opcionalmente restritas a
    `wallet_ids`. Campos: `walletId`, `companyId`, `positionDate`,
    `securities[]` (`unprocessedId`/`pu`/`quantity`). Diferente do endpoint de
    posição processada, este aceita **faixa** de datas. Retorna `[]` se a
    resposta não for uma lista.

    `wallet_ids` grandes são divididos em blocos URL-safe (evita HTTP 414). O
    endpoint NÃO trata `walletIds` vazio como "empresa toda" (devolve 0) — passe
    a lista explícita de carteiras.
    """
    return _get_positions_chunked(
        "/beehus/financial/positions/unprocessed-security-positions",
        {"companyId": company_id, "initialDate": initial_date, "finalDate": final_date},
        wallet_ids, timeout,
    )


def get_preprocessing_status(
    *,
    company_id: str,
    position_date: str,
    timeout: int = 120,
) -> dict | list | None:
    """GET /beehus/financial/positions/processed-position/pre-processing — READ.

    Status de pré-processamento por carteira da empresa em `position_date`
    (ISO) — o que alimenta a grade inicial do Painel de Controle: quais
    carteiras já têm posição processada, quais têm posição bruta e os
    bloqueios (issues) pendentes. Retorna o corpo cru da API (shape a
    confirmar com o backend).
    """
    params = {
        "positionDate": position_date,
        "companyId":    company_id,
    }
    return request(
        "GET",
        "/beehus/financial/positions/processed-position/pre-processing",
        params=params,
        timeout=timeout,
    )


def process_processed_position(
    *,
    company_id: str,
    position_date: str,
    wallets: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """POST /beehus/financial/positions/processed-position/process.

    Triggers the server-side processing of positions for the given company
    on `position_date` (ISO YYYY-MM-DD). When `wallets` is empty/None the
    server processes all wallets in the company; otherwise only the listed
    wallet ids are processed. Position processing can take a while, so the
    default timeout is wider than the API client default.
    """
    payload = {
        "companyId": company_id,
        "positionDate": position_date,
        "wallets": list(wallets or []),
    }
    return request(
        "POST",
        "/beehus/financial/positions/processed-position/process",
        json=payload,
        timeout=timeout,
    )


def delete_processed_position(
    *,
    company_id: str,
    position_date: str,
    wallet_ids: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """DELETE /beehus/financial/positions/processed-position/delete.

    Deletes processed positions for the given company on `position_date`.
    When `wallet_ids` is empty/None the upstream API treats it as "all
    wallets in the company". The upstream payload field is `walletIds`
    (camelCase, plural with Ids) — note the spelling difference vs. the
    `wallets` field used by the sibling /process endpoint.
    """
    payload = {
        "companyId": company_id,
        "positionDate": position_date,
        "walletIds": list(wallet_ids or []),
    }
    return request(
        "DELETE",
        "/beehus/financial/positions/processed-position/delete",
        json=payload,
        timeout=timeout,
    )


def upload_unprocessed_security_positions_file(
    *,
    company_id: str,
    file_bytes: bytes,
    filename: str = "unprocessed_security_positions.xlsx",
    timeout: int = 120,
) -> dict | None:
    """POST /beehus/financial/positions/unprocessed-security-positions/file.

    Multipart upload of an Excel workbook with the wallet's unprocessed
    security positions. The upstream endpoint expects the columns:
    `Data, Carteira, Ativo, Quant, PU, SaldoBruto, Caixa, Moeda` plus a
    form field `companyId`.
    """
    files = {
        "file": (
            filename,
            file_bytes,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
    }
    data = {"companyId": company_id}
    return request_multipart(
        "POST",
        "/beehus/financial/positions/unprocessed-security-positions/file",
        files=files,
        data=data,
        timeout=timeout,
    )
