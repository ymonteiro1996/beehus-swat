"""High-level functions for /beehus/provisions/*.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def create_provision(
    *,
    company_id: str,
    wallet_id: str,
    balance: float,
    initial_date: str,
    liquidation_date: str,
    provision_type: str,
    provision_source: str = "adjustments",
    currency_id: str = "BRL",
    description: str = "",
    security_id: str | None = None,
) -> dict:
    """POST /beehus/provisions — create a provision.

    Dates are ISO strings (YYYY-MM-DD). `balance` is the cash amount in the
    wallet's currency. Returns the created provision document including
    the server-assigned `_id`.
    """
    payload = {
        "companyId":        company_id,
        "walletId":         wallet_id,
        "balance":          balance,
        "currencyId":       currency_id,
        "initialDate":      initial_date,
        "liquidationDate":  liquidation_date,
        "provisionType":    provision_type,
        "provisionSource":  provision_source,
        "description":      description,
    }
    if security_id:
        payload["securityId"] = security_id
    return request("POST", "/beehus/provisions", json=payload)


def delete_provision(provision_id: str) -> dict | None:
    """DELETE /beehus/provisions/{id}.

    Returns whatever the API returns (often empty body). Raises BeehusAPIError
    on non-2xx so callers can collect successes/failures per id.
    """
    if not provision_id:
        raise ValueError("provision_id is required")
    return request("DELETE", f"/beehus/provisions/{provision_id}")


_PATCHABLE_FIELDS = {
    "balance",
    "currencyId",
    "description",
    "initialDate",
    "liquidationDate",
    "provisionSource",
    "provisionType",
    "securityId",
    # walletId is patchable so class_strip can migrate a provision from a
    # source wallet to the routed target without delete+create (which would
    # lose the upstream id and historical metadata).
    "walletId",
}


def update_provision(provision_id: str, patch: dict) -> dict | None:
    """PATCH /beehus/provisions/{id} with a partial body.

    Only known fields are forwarded; unknown keys are silently dropped to
    avoid the upstream rejecting the whole request because of a typo. The
    caller is expected to pre-validate values (no coercion happens here).
    """
    if not provision_id:
        raise ValueError("provision_id is required")
    if not isinstance(patch, dict) or not patch:
        raise ValueError("patch must be a non-empty dict")
    payload = {k: v for k, v in patch.items() if k in _PATCHABLE_FIELDS}
    if not payload:
        raise ValueError("patch contains no patchable fields")
    return request("PATCH", f"/beehus/provisions/{provision_id}", json=payload)
