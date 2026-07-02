"""High-level functions for /beehus/partner-info.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def list_companies() -> list:
    """GET /beehus/partners/companies — empresas que o token enxerga.

    Cada item traz `_id` (companyId, normalmente o CNPJ), `name`, `currencyId`,
    etc. Retorna `[]` se a resposta não for uma lista.
    """
    out = request("GET", "/beehus/partners/companies")
    return out if isinstance(out, list) else []


def list_entities() -> list:
    """GET /beehus/entities — entidades (carteiras-mãe) que o token enxerga.

    Cada item traz `_id` (entityId) e `name`. Substitui `db.entities.find` /
    `db.get_entity_names`. Retorna `[]` se a resposta não for uma lista.
    """
    out = request("GET", "/beehus/entities")
    return out if isinstance(out, list) else []


def partner_wallets(company_id: str) -> list:
    """GET /beehus/partner-info/{companyId}/wallets — carteiras da empresa.

    Cada item traz `_id`, `name`, `accountCode`, `currency`, `trashed` e os
    campos POPULADOS `entityId` ({_id, name, …}) e `companyId` ({_id, name,
    currencyId}). Retorna `[]` se a resposta não for uma lista.
    """
    out = request("GET", f"/beehus/partner-info/{company_id}/wallets")
    return out if isinstance(out, list) else []
