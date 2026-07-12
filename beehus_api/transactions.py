"""High-level functions for /beehus/financial/transactions.

One Python function per logical operation. Returns parsed dicts.
Reusable from any blueprint — does not depend on Flask.
"""
import concurrent.futures

from .client import request


def create_transaction(
    *,
    company_id: str,
    balance: float,
    operation_date: str,
    liquidation_date: str,
    entity_id: str | None = None,
    wallet_id: str | None = None,
    grouping_id: str | None = None,
    currency_id: str = "BRL",
    transaction_type: str = "withdrawalDeposit",
    description: str = "",
    comment: str = "",
    hide: bool = False,
    input_type: str = "web",
    security_id: str | None = None,
    quantity: float | None = None,
    price: float | None = None,
) -> dict:
    """POST /beehus/financial/transactions — create a financial transaction.

    Dates are ISO strings (YYYY-MM-DD). `balance` is the cash amount in the
    target's currency. Returns the created transaction document including
    the server-assigned `_id`.

    Scope: pass `wallet_id` (+ `entity_id`) for a wallet-level transaction, OR
    `grouping_id` for a grouping-level (consolidation) transaction — the latter
    is booked with `groupingId` and no `walletId`/`entityId` (matches how the
    upstream stores grouping adjustments).
    """
    payload = {
        "companyId": company_id,
        "balance": balance,
        "currencyId": currency_id,
        "beehusTransactionType": transaction_type,
        "operationDate": operation_date,
        "liquidationDate": liquidation_date,
        "description": description,
        "comment": comment,
        "hide": hide,
        "inputType": input_type,
    }
    if grouping_id:
        payload["groupingId"] = grouping_id
    else:
        payload["entityId"] = entity_id
        payload["walletId"] = wallet_id
    if security_id is not None:
        payload["securityId"] = security_id
    if quantity is not None:
        payload["quantity"] = quantity
    if price is not None:
        payload["price"] = price

    return request("POST", "/beehus/financial/transactions", json=payload)


def _csv(v) -> str:
    if isinstance(v, (list, tuple, set)):
        return ",".join(str(x) for x in v if x)
    return str(v or "")


def _id_list(v):
    """Normaliza um filtro de ids (escalar/iterável) para list[str], ou None."""
    if v is None:
        return None
    if isinstance(v, (list, tuple, set)):
        return [str(x) for x in v if x]
    return [str(v)] if v else []


# Servidores limitam o tamanho da request-line/URL (~8KB → HTTP 414, ou o host
# fecha a conexão). Um CSV de `walletIds` da empresa inteira (centenas/milhares
# de ids de 24 chars) estoura fácil, então dividimos os ids em blocos URL-safe,
# fazemos 1 GET por bloco e concatenamos. Blocos são conjuntos DISJUNTOS de
# carteiras → não há docs duplicados p/ dedupe. Espelha
# `beehus_api.positions._get_positions_chunked`.
_MAX_WALLET_IDS_PER_REQUEST = 150


def list_transactions(
    *,
    company_id: str,
    initial_date: str,
    final_date: str,
    wallet_ids=None,
    grouping_ids=None,
    entity_ids=None,
    security_ids=None,
    date_type: str = "liquidation",
    timeout: int = 60,
) -> list:
    """GET /beehus/financial/transactions — READ de transações por filtro.

    Filtra por empresa + (carteiras/agrupamentos/entidades/securities, todos
    opcionais e aceitando lista) e por janela de datas (`date_type` em
    `liquidation`/`operation`). Devolve a lista de transações (campos
    populados: `walletId`, `entityId`, `companyId` vêm como objetos com `_id`).
    Retorna `[]` se a resposta não for uma lista.

    `wallet_ids` grandes são divididos em blocos URL-safe (evita HTTP 414 / o
    host fechar a conexão): 1 GET por bloco disjunto de carteiras, resultados
    concatenados. Os demais filtros (grouping/entity/security) repetem em cada
    bloco. Erro em qualquer bloco propaga (falha parcial = falha total).
    """
    base_params = {
        "companyId":    company_id,
        "groupingIds":  _csv(grouping_ids),
        "entityIds":    _csv(entity_ids),
        "securityIds":  _csv(security_ids),
        "dateType":     date_type,
        "initialDate":  initial_date,
        "finalDate":    final_date,
    }

    def _one(chunk):
        params = dict(base_params)
        params["walletIds"] = _csv(chunk)
        out = request("GET", "/beehus/financial/transactions", params=params, timeout=timeout)
        return out if isinstance(out, list) else []

    ids = _id_list(wallet_ids)
    if ids and len(ids) > _MAX_WALLET_IDS_PER_REQUEST:
        # Blocos em PARALELO (threads; Session pool 20) — `ex.map` preserva a
        # ordem de concatenação e re-levanta a 1ª exceção na ordem dos blocos,
        # mesma semântica do loop sequencial (GETs sem efeito colateral).
        chunks = [ids[i:i + _MAX_WALLET_IDS_PER_REQUEST]
                  for i in range(0, len(ids), _MAX_WALLET_IDS_PER_REQUEST)]
        merged = []
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=min(5, len(chunks))) as ex:
            for out in ex.map(_one, chunks):
                merged.extend(out)
        return merged
    return _one(ids)


def delete_transaction(transaction_id: str) -> dict | None:
    """DELETE /beehus/financial/transactions/{id}.

    Returns whatever the API returns (often empty body). Raises BeehusAPIError
    on non-2xx so callers can collect successes/failures per id."""
    if not transaction_id:
        raise ValueError("transaction_id is required")
    return request("DELETE", f"/beehus/financial/transactions/{transaction_id}")


_PATCHABLE_FIELDS = {
    "balance",
    "beehusTransactionType",
    "currencyId",
    "description",
    "entityId",
    "liquidationDate",
    "operationDate",
    # `price` (PU) is patchable so the Exceções routine can convert a
    # migrated transaction's unit price into the destination wallet's
    # currency alongside `balance`/`currencyId`.
    "price",
    "securityId",
    # `walletId` is patchable so the Exceções routine can migrate a
    # transaction from the stripped wallet to the new wallet without having
    # to delete + recreate (which would lose the upstream `_id`).
    "walletId",
}


def update_transaction(transaction_id: str, patch: dict) -> dict | None:
    """PATCH /beehus/financial/transactions/{id} with a partial body.

    Only known fields are forwarded; unknown keys are silently dropped to
    avoid the upstream rejecting the whole request because of a typo. The
    caller is expected to pre-validate values (no coercion happens here).
    """
    if not transaction_id:
        raise ValueError("transaction_id is required")
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch must be a non-empty dict")
    payload = {k: v for k, v in patch.items() if k in _PATCHABLE_FIELDS}
    if not payload:
        raise ValueError("patch contains no patchable fields")
    return request(
        "PATCH",
        f"/beehus/financial/transactions/{transaction_id}",
        json=payload,
    )
