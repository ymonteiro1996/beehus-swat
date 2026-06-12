"""High-level functions for /beehus/consolidation/*.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def calculate_nav_wallets(
    *,
    company_id: str,
    position_date: str,
    wallets: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """POST /beehus/consolidation/nav-contribution-calculation/wallets.

    Triggers NAV-contribution recalculation for the given company on
    `position_date` (ISO YYYY-MM-DD) for the listed wallet ids. The
    upstream payload field is `wallets` (matching the /positions/process
    sibling, not `walletIds`). Empty list means "all wallets in the
    company" per the upstream contract. Calculation can take a while,
    so the default timeout is wider than the API client default.
    """
    payload = {
        "companyId": company_id,
        "positionDate": position_date,
        "wallets": list(wallets or []),
    }
    return request(
        "POST",
        "/beehus/consolidation/nav-contribution-calculation/wallets",
        json=payload,
        timeout=timeout,
    )


def calculate_nav_groupings(
    *,
    company_id: str,
    position_date: str,
    groupings: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """POST /beehus/consolidation/nav-contribution-calculation/groupings.

    Triggers NAV-contribution recalculation at the grouping level for the
    given company on `position_date` (ISO YYYY-MM-DD). The upstream
    payload field is `groupings` (array of grouping ids). Empty list
    means "all groupings of the company" per the upstream contract.
    """
    payload = {
        "companyId": company_id,
        "positionDate": position_date,
        "groupings": list(groupings or []),
    }
    return request(
        "POST",
        "/beehus/consolidation/nav-contribution-calculation/groupings",
        json=payload,
        timeout=timeout,
    )


def proportion_explosion(
    *,
    company_id: str,
    position_date: str,
    groupings: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """POST /beehus/consolidation/nav-contribution-calculation/explosion-proportions.

    Recalculates the proportion of each "exploded" component contribution
    for the listed groupings of `company_id` on `position_date`. Same
    payload shape as `calculate_nav_groupings` (`companyId`, `groupings`,
    `positionDate`). Empty list means "all groupings of the company".
    """
    payload = {
        "companyId": company_id,
        "positionDate": position_date,
        "groupings": list(groupings or []),
    }
    return request(
        "POST",
        "/beehus/consolidation/nav-contribution-calculation/explosion-proportions",
        json=payload,
        timeout=timeout,
    )


def publish_nav(
    *,
    company_id: str,
    position_date: str,
    grouping_ids: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """PATCH /beehus/consolidation/nav-contribution-calculation/publish.

    Publishes the calculated NAV results for the listed groupings of
    `company_id` on `position_date`. Payload is a JSON body with
    `groupingIds` as a real array:

        {"companyId": "...", "positionDate": "YYYY-MM-DD",
         "groupingIds": ["id1", "id2", ...]}

    Empty `grouping_ids` sends an empty array — the upstream contract
    treats it as "all groupings of the company".
    """
    payload = {
        "companyId":    company_id,
        "positionDate": position_date,
        "groupingIds":  list(grouping_ids or []),
    }
    return request(
        "PATCH",
        "/beehus/consolidation/nav-contribution-calculation/publish",
        json=payload,
        timeout=timeout,
    )


def unpublish_nav(
    *,
    company_id: str,
    position_date: str,
    grouping_ids: list[str] | None = None,
    timeout: int = 300,
) -> dict | None:
    """PATCH /beehus/consolidation/nav-contribution-calculation/unpublish.

    Inverse of `publish_nav` — same JSON body shape, different path.
    """
    payload = {
        "companyId":    company_id,
        "positionDate": position_date,
        "groupingIds":  list(grouping_ids or []),
    }
    return request(
        "PATCH",
        "/beehus/consolidation/nav-contribution-calculation/unpublish",
        json=payload,
        timeout=timeout,
    )


def run_heuristics(
    *,
    company_id: str,
    current: str,
    entity_id: str,
    timeout: int = 120,
) -> dict | None:
    """POST /data-science/heuristics — run transaction-identification heuristics.

    Args:
        company_id: Company identifier.
        current:    Position date (ISO YYYY-MM-DD).
        entity_id:  Wallet entity ID (wallet.entityId field).
    """
    payload = {
        "company_id": company_id,
        "current":    current,
        "entity_id":  entity_id,
    }
    return request(
        "POST",
        "/data-science/heuristics",
        json=payload,
        timeout=timeout,
    )
