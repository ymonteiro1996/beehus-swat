"""High-level functions for /beehus/provisions/*.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request


def list_provisions(
    *,
    company_id: str,
    initial_date: str,
    final_date: str,
    wallet_id: str | None = None,
    timeout: int = 60,
) -> list:
    """GET /beehus/provisions — READ das provisões por empresa + faixa de datas.

    Devolve a lista de provisões (`_id`, `walletId`, `companyId`, `securityId`,
    `balance`, `initialDate`, `liquidationDate`, `provisionType`,
    `provisionSource`, `description`, `currencyId`, `trashed`, …). NÃO há campo
    `amount` (provisões usam `balance`).

    ⚠️ SEMÂNTICA DA JANELA (verificada em produção, jun/2026): o upstream filtra
    por **`initialDate` DENTRO de `[initial_date, final_date]`** — ou seja,
    provisões **iniciadas** no intervalo —, NÃO por overlap/atividade. Prova:
    uma provisão ativa em `2026-03-02` mas iniciada em `2025-12-01` NÃO retorna
    para a query `[2026-03-02, 2026-03-02]`. Portanto, para obter "provisões
    ativas em D" é preciso buscar de uma data-piso bem atrás (ex.: `2000-01-01`)
    e filtrar a atividade no cliente — é o que `beehus_catalog.provisions_active`
    / `provisions_overlapping` fazem. (Documentação anterior afirmava "ativas no
    intervalo" — estava incorreta.) Alternativa quando há posição processada na
    data: o envelope de `processed-position` já traz, em `provisions`, exatamente
    as provisões ativas naquela data — ver `beehus_catalog.carteira_position_bundle`.

    `wallet_id` (singular `walletId`) **filtra no servidor** por carteira
    (confirmado em produção: devolve exatamente as provisões da carteira). NÃO
    confundir com `walletIds` (plural), que o endpoint **não aceita** (400).
    Retorna `[]` se a resposta não for uma lista.
    """
    params = {
        "companyId":   company_id,
        "initialDate": initial_date,
        "finalDate":   final_date,
    }
    if wallet_id:
        params["walletId"] = wallet_id
    out = request("GET", "/beehus/provisions", params=params, timeout=timeout)
    return out if isinstance(out, list) else []


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
