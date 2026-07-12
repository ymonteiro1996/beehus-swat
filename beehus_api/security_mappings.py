"""High-level functions for /beehus/financial/security-mappings.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def get_security_mappings(*, company_id: str | None = None, timeout: int = 60):
    """GET /beehus/financial/security-mappings — READ.

    Devolve o(s) documento(s) `securityMappings` (1 por empresa): cada um com
    `_id`, `companyId` e `mappings[]` (`{from: unprocessedId, to: securityId}`).
    Quando `company_id` é informado, vai como filtro `companyId` na query (a
    API pode também devolver tudo e o chamador filtra). O `_id` retornado é o
    que o PATCH de `update_security_mappings` exige. Retorna o corpo cru.
    """
    params = {"companyId": company_id} if company_id else None
    return request(
        "GET",
        "/beehus/financial/security-mappings",
        params=params,
        timeout=timeout,
    )


def update_security_mappings(
    security_mapping_id: str,
    *,
    mappings_to_include: list[dict] | None = None,
    mappings_to_exclude: list[dict] | None = None,
    timeout: int = 60,
) -> dict | None:
    """PATCH /beehus/financial/security-mappings/{id}.

    Adds and/or removes entries on the company's `securityMappings` document
    identified by `security_mapping_id` (the Mongo `_id`, not the companyId).
    Each entry is `{"from": <unprocessedSecurityId>, "to": <securityId>}`.
    """
    if not security_mapping_id:
        raise ValueError("security_mapping_id is required")
    payload = {
        "mappingsToInclude": list(mappings_to_include or []),
        "mappingsToExclude": list(mappings_to_exclude or []),
    }
    return request(
        "PATCH",
        f"/beehus/financial/security-mappings/{security_mapping_id}",
        json=payload,
        timeout=timeout,
    )
