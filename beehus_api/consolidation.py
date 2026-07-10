"""High-level functions for /beehus/consolidation/*.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def list_company_variables(*, timeout: int = 60) -> list:
    """GET /beehus/consolidation/company-variables — READ crua, SEM filtro.

    IMPORTANTE: o endpoint IGNORA o parâmetro `companyId` — ele sempre
    devolve a árvore de classificação de ativos de TODAS as empresas
    (confirmado em produção: a mesma lista completa volta não importa o
    `companyId` enviado). Devolve a lista crua (1 doc por empresa, cada um
    com `companyId` + `hierarchicalVariables[]`); o filtro pela empresa certa
    é responsabilidade do chamador (ver `get_company_variables`).
    """
    out = request(
        "GET",
        "/beehus/consolidation/company-variables",
        params={"companyId": ""},
        timeout=timeout,
    )
    return out if isinstance(out, list) else []


def get_company_variables(*, company_id: str, timeout: int = 60) -> dict | None:
    """Árvore de classificação de ativos (`hierarchicalVariables[]`) de UMA
    empresa — filtra `list_company_variables()` pelo `companyId` certo, já
    que o próprio endpoint upstream não filtra (ver aviso lá). Não existe
    (ainda) endpoint de escrita conhecido pra vincular um ativo a um desses
    nós — gerar JSON manual enquanto isso.
    """
    for doc in list_company_variables(timeout=timeout):
        if isinstance(doc, dict) and str(doc.get("companyId") or "") == str(company_id):
            return doc
    return None


def get_nav_contribution(
    *,
    entity_id: str,
    company_id: str,
    scope: str,
    initial_date: str = "2000-01-01",
    final_date: str,
    timeout: int = 60,
) -> list:
    """GET /beehus/consolidation/nav-contribution-calculation — READ da série.

    Devolve a lista de pacotes NAV (um por `positionDate`) de uma carteira
    (`scope='wallet'`, `entity_id`=walletId) ou agrupamento (`scope='grouping'`,
    `entity_id`=groupingId). Mesmos campos do navPackage (nav, navPerShare,
    amount, formerAmount, inAndOutFlows, returnNavPerShare, returnContribution,
    published, trashed, positionDate). Não vem ordenado.
    """
    out = request(
        "GET",
        "/beehus/consolidation/nav-contribution-calculation",
        params={"id": entity_id, "companyId": company_id, "type": scope,
                "initialDate": initial_date, "finalDate": final_date},
        timeout=timeout,
    )
    return out if isinstance(out, list) else []


def get_nav_results(
    *,
    company_id: str,
    position_date: str,
    timeout: int = 60,
) -> dict:
    """GET /beehus/consolidation/nav-contribution-calculation/results — CONSOLIDADO
    por empresa+data, numa única chamada.

    Devolve um dict com (validado 1:1 contra navPackages):
      - `walletsWithNav` (int), `totalGroupings` (int), `publishedGroupings` (int)
      - `walletsWithNavDetailed`: [{walletId, walletName, groupingId, groupingName,
            nav, navPerShare, amount, returnNavPerShare, returnContribution,
            returnDifference (= |rnps-rc|), financialValueReturnDifference}]
      - `groupingsDetailed`: idem por agrupamento + `published`, `publishedAt`.

    Substitui o aquecimento por-entidade (N chamadas) para as telas consolidadas
    (Painel, Conciliação grade, Console publicação): 1 chamada por empresa+data,
    ao vivo (sem cache → sempre fresco). `returnDifference` e a divergência são
    pré-calculadas no servidor.
    """
    out = request(
        "GET",
        "/beehus/consolidation/nav-contribution-calculation/results",
        params={"positionDate": position_date, "companyId": company_id},
        timeout=timeout,
    )
    return out if isinstance(out, dict) else {}


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
