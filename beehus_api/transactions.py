"""High-level functions for /beehus/financial/transactions.

One Python function per logical operation. Returns parsed dicts.
Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def create_transaction(
    *,
    company_id: str,
    entity_id: str,
    wallet_id: str,
    balance: float,
    operation_date: str,
    liquidation_date: str,
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
    wallet's currency. Returns the created transaction document including
    the server-assigned `_id`.
    """
    payload = {
        "companyId": company_id,
        "entityId": entity_id,
        "walletId": wallet_id,
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
    if security_id is not None:
        payload["securityId"] = security_id
    if quantity is not None:
        payload["quantity"] = quantity
    if price is not None:
        payload["price"] = price

    return request("POST", "/beehus/financial/transactions", json=payload)


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
