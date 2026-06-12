"""High-level functions for /beehus/financial/execution-prices.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


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
