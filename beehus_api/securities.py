"""High-level functions for /beehus/securities.

One Python function per logical operation. Returns parsed dicts.
Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def list_securities(*, timeout: int = 60) -> list:
    """GET /beehus/securities — READ do catálogo completo de ativos.

    Devolve a lista de securities (cada item com `_id`, `beehusName`, `mainId`,
    `ticker`, `taxId`, `isIn`, `selicCode`, `securityType`, `type`, `currency`,
    `maturityDate`, `emissionDate`, `issuer`, `indexer`, `indexerPercentual`,
    `yield`, `companyIds`, e os dias de subscrição/resgate). Coleção pequena e
    estável → ideal cachear no cliente (ver `beehus_catalog.py`) e resolver
    ids/mainId/busca em memória, substituindo os full scans e os
    `find({_id:$in})` espalhados pelas páginas. Retorna `[]` se não for lista.
    """
    out = request("GET", "/beehus/securities", timeout=timeout)
    return out if isinstance(out, list) else []


def get_security(*, security_id: str, timeout: int = 60) -> dict | None:
    """GET /beehus/securities/{id} — READ de UM ativo pelo `_id`.

    Usado quando o catálogo cacheado (`beehus_catalog.security_doc`) não tem o
    campo necessário (ex.: `correspondingWallet` de um ativo de explosão) e
    vale a pena um GET pontual em vez de esperar o próximo refresh do catálogo
    inteiro. Retorna o doc cru, ou None se `security_id` for vazio.
    """
    if not security_id:
        return None
    return request("GET", f"/beehus/securities/{security_id}", timeout=timeout)


def create_security(
    *,
    beehus_name: str,
    ticker: str,
    security_type: str = "poc",
    currency: str = "BRL",
    subscription_nav_days: int = 0,
    subscription_settlement_days: int = 0,
    redemption_nav_days: int = 0,
    redemption_settlement_days: int = 0,
    wallet_ids: list | None = None,
    company_ids: list | None = None,
    user_ids: list | None = None,
    feeder_ids: list | None = None,
    immediate_price_consume: bool = False,
) -> dict:
    """POST /beehus/securities — register a security (ativo).

    Mirrors the payload the controlpanel "Cadastrar ativos" modal sends.
    `beehus_name` and `ticker` are the only required identifiers. Returns the
    created security document including the server-assigned `_id`. Raises
    BeehusAPIError on non-2xx so callers can collect successes/failures.
    """
    payload = {
        "securityType": security_type,
        "beehusName": beehus_name,
        "currency": currency,
        "subscriptionNAVDays": subscription_nav_days,
        "subscriptionSettlementDays": subscription_settlement_days,
        "redemptionNAVDays": redemption_nav_days,
        "redemptionSettlementDays": redemption_settlement_days,
        "ticker": ticker,
        "walletIds": wallet_ids or [],
        "companyIds": company_ids or [],
        "userIds": user_ids or [],
        "feederIds": feeder_ids or [],
        "immediatePriceConsume": immediate_price_consume,
    }
    return request("POST", "/beehus/securities", json=payload)


_PRICING_TYPES = ("B1", "B2", "C1", "C2", "C3")


def filtered_security_price(
    *,
    security_ids,
    pricing_types=_PRICING_TYPES,
    timeout: int = 60,
) -> list:
    """GET /beehus/security-prices/filtered-security-price — READ.

    Preços (`securityPrices`) dos `security_ids` nos `pricing_types` pedidos.
    **`pricingType` é OBRIGATÓRIO** (400 sem ele). Devolve uma LISTA de docs no
    mesmo shape do Mongo, cada um com:

        type          — o pricingType (B1/B2/C1/C2/C3) = ESCOPO do preço
        securityId     — POPULADO (dict; normalizar p/ _id no cliente)
        companyId / entityId / walletId — escopo, POPULADOS (ou null)
        status         — ex. "approved"
        trashed        — bool
        historyPrice[] — {date, value, adjustedQuantity}

    O endpoint devolve **TODOS** os records dos tipos pedidos (NÃO pré-resolve):
    um ativo com preço por carteira retorna N records C3, um por wallet. O
    **cliente** escolhe o mais específico para o contexto `(company, entity,
    wallet)` na ordem C3→C2→C1→B2→B1 (ver `beehus_catalog.security_prices_resolved`).

    Escopos do `type`: B1=securityId; B2=+entityId; C1=+companyId;
    C2=+companyId+entityId; C3=+companyId+walletId.

    Retorna `[]` se `security_ids` for vazio ou a resposta não for lista.
    """
    if isinstance(security_ids, (list, tuple, set)):
        ids = ",".join(str(x) for x in security_ids if x)
    else:
        ids = str(security_ids or "")
    if not ids:
        return []
    if isinstance(pricing_types, (list, tuple, set)):
        pt = ",".join(str(x) for x in pricing_types if x)
    else:
        pt = str(pricing_types or "")
    out = request(
        "GET",
        "/beehus/security-prices/filtered-security-price",
        params={"securityIds": ids, "pricingType": pt},
        timeout=timeout,
    )
    return out if isinstance(out, list) else []


def security_events(*, security_ids, timeout: int = 60) -> list:
    """GET /beehus/security-events — READ de eventos (corporate actions) por ativo.

    Filtra pelo parâmetro **`securities`** (securityId; aceita CSV p/ vários).
    Devolve uma LISTA crua de eventos, cada um com (confirmado em produção):

        _id, securityId (string, NÃO populado), eventType
        (`cashDividend`/`interestOnEquity`/`interest`/`coupon`/`amortization`/…),
        operationDate ("YYYY-MM-DD"), liquidationDate, balance (número —
        dividendo POR COTA), newSecurityId, factor, createdAt/updatedAt.

    NÃO há campo `trashed` na resposta (a API já exclui trashed). O cliente
    filtra por `operationDate`/`eventType` e agrega (ver
    `beehus_catalog.dividend_events_by_sid`). Retorna `[]` se `security_ids`
    for vazio ou a resposta não for lista.
    """
    if isinstance(security_ids, (list, tuple, set)):
        ids = ",".join(str(x) for x in security_ids if x)
    else:
        ids = str(security_ids or "")
    if not ids:
        return []
    out = request("GET", "/beehus/security-events",
                  params={"securities": ids}, timeout=timeout)
    return out if isinstance(out, list) else []
