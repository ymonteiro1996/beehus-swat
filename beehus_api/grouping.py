"""High-level functions for /beehus/grouping.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def list_groupings(company_id: str) -> list:
    """GET /beehus/grouping?companyId=… — agrupamentos da empresa.

    Cada item traz `_id`, `name`, `companyId`, `currencyId`, `trashed` e
    `wallets`: lista de `{walletId: {_id, name, currency}, initialDateOnGrouping,
    finalDateOnGrouping}` (o `walletId` vem POPULADO como objeto, não como id
    cru). Retorna `[]` se a resposta não for uma lista.
    """
    out = request("GET", "/beehus/grouping", params={"companyId": company_id})
    return out if isinstance(out, list) else []
