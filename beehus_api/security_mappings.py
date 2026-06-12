"""High-level functions for /beehus/financial/security-mappings.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


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
