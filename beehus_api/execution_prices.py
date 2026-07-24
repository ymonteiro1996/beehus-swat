"""High-level functions for /beehus/financial/execution-prices.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def list_execution_prices(
    *,
    company_id: str,
    initial_date: str,
    final_date: str,
    wallet_id: str | None = None,
    timeout: int = 60,
) -> list:
    """GET /beehus/financial/execution-prices — READ execution prices in a range.

    Filtra por `companyId` + [`initialDate`, `finalDate`] (por `positionDate`) e,
    opcionalmente, `walletId`. Devolve uma LISTA de docs, cada um com:

        securityId     — POPULADO (dict; normalizar p/ _id no cliente)
        executionPrice — número (o preço de execução do trade na data)
        positionDate   — "YYYY-MM-DD" (data a que o preço se refere)
        walletId / companyId — POPULADOS · currency · trashed

    Retorna `[]` se a resposta não for lista.
    """
    params = {"companyId": company_id, "initialDate": initial_date, "finalDate": final_date}
    if wallet_id:
        params["walletId"] = wallet_id
    out = request("GET", "/beehus/financial/execution-prices", params=params, timeout=timeout)
    return out if isinstance(out, list) else []


def create_execution_price(
    *,
    company_id: str,
    wallet_id: str,
    security_id: str,
    position_date: str,
    execution_price: float,
) -> dict:
    """POST /beehus/financial/execution-prices — create an execution price.

    `position_date` is an ISO string (YYYY-MM-DD). `execution_price` is the
    numeric price for the security on that date. Returns the created
    document (server-assigned `_id` included).
    """
    payload = {
        "companyId":      company_id,
        "walletId":       wallet_id,
        "securityId":     security_id,
        "positionDate":   position_date,
        "executionPrice": execution_price,
    }
    return request("POST", "/beehus/financial/execution-prices", json=payload)


def update_execution_price(execution_price_id: str, execution_price: float) -> dict:
    """PATCH /beehus/financial/execution-prices/{id} — atualiza o `executionPrice`
    de um record existente.

    Usado para CORRIGIR um record placeholder (cujo `executionPrice` apenas repetia
    o PU, sem ter sido inputado/calculado). `execution_price_id` é o `_id` do record
    (obtido no GET). Só o `executionPrice` é enviado.
    """
    if not execution_price_id:
        raise ValueError("execution_price_id is required")
    return request("PATCH", f"/beehus/financial/execution-prices/{execution_price_id}",
                   json={"executionPrice": execution_price})


def delete_execution_price(execution_price_id: str) -> dict | None:
    """DELETE /beehus/financial/execution-prices/{id}.

    Returns whatever the API returns (often empty body). Raises BeehusAPIError
    on non-2xx so callers can surface the failure.
    """
    if not execution_price_id:
        raise ValueError("execution_price_id is required")
    return request("DELETE", f"/beehus/financial/execution-prices/{execution_price_id}")
