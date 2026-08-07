"""High-level functions for /market-data-collector/onboarding.

Derives security fields from an authoritative code/ticker/CNPJ (Anbima/B3/CVM
lookups happen server-side). Confirmed live against production (2026-07-30,
`POST .../onboarding/equities {"ticker": "PETR4", "download": true}`) that
`download=true` returns the derived payload WITHOUT persisting anything (no
`_id` in the response) — so every function here hardcodes `download: True`.
Persistence is always a separate, explicit step via `securities.create_security_raw`
after `securities.check_similar_securities`, never done by this module. This
also means the (unverified) `download=false` persist-directly behavior is
simply never exercised by this codebase.

The returned payload uses field conventions that do NOT always match what
`/beehus/securities` expects (e.g. `exchange: "SAO"` / `country: "Brazil"` from
equities onboarding, vs the `"BVMF"` MIC / `"BR"` ISO code `/beehus/securities`
wants) — callers must run the response through
`asset_registration_rules.normalize_onboarding_payload()` before creating.

Reusable from any blueprint — does not depend on Flask.
"""
from .client import request

_PREFIX = "/market-data-collector/onboarding"


def onboard_cri_cra(codigo: str, *, timeout: int = 30) -> dict:
    """POST /onboarding/bonds/cri-cra — derive fields for a single CRI/CRA
    from its B3/CETIP code."""
    return request("POST", f"{_PREFIX}/bonds/cri-cra",
                   json={"codigo": codigo, "download": True}, timeout=timeout)


def onboard_cri_cra_bulk(codigos: list[str], *, timeout: int = 60) -> dict:
    """POST /onboarding/bonds/cri-cra/bulk — same as `onboard_cri_cra`, batched."""
    return request("POST", f"{_PREFIX}/bonds/cri-cra/bulk",
                   json={"codes": list(codigos), "download": True}, timeout=timeout)


def onboard_debenture(codigo: str, *, timeout: int = 30) -> dict:
    """POST /onboarding/bonds/debentures — derive fields for a single
    debenture from its B3 code."""
    return request("POST", f"{_PREFIX}/bonds/debentures",
                   json={"codigo": codigo, "download": True}, timeout=timeout)


def onboard_debenture_bulk(codigos: list[str], *, timeout: int = 60) -> dict:
    """POST /onboarding/bonds/debentures/bulk — same as `onboard_debenture`, batched."""
    return request("POST", f"{_PREFIX}/bonds/debentures/bulk",
                   json={"codes": list(codigos), "download": True}, timeout=timeout)


def onboard_lf(codigo: str, *, timeout: int = 30) -> dict:
    """POST /onboarding/bonds/lf — derive fields for a single Letra
    Financeira from its B3/CETIP code."""
    return request("POST", f"{_PREFIX}/bonds/lf",
                   json={"codigo": codigo, "download": True}, timeout=timeout)


def onboard_lf_bulk(codigos: list[str], *, timeout: int = 60) -> dict:
    """POST /onboarding/bonds/lf/bulk — same as `onboard_lf`, batched."""
    return request("POST", f"{_PREFIX}/bonds/lf/bulk",
                   json={"codes": list(codigos), "download": True}, timeout=timeout)


def onboard_equity(ticker: str, *, timeout: int = 30) -> dict:
    """POST /onboarding/equities — derive fields for a single ação/BDR/ETF
    from its ticker. Confirmed live shape (PETR4):
    `{beehusName, securityType:"stockEtf", ticker, exchange, currency,
    country, klass, subscriptionNAVDays, subscriptionSettlementDays,
    redemptionNAVDays, redemptionSettlementDays, mainId}` — `exchange`/
    `country` need normalizing before create (see module docstring)."""
    return request("POST", f"{_PREFIX}/equities",
                   json={"ticker": ticker, "download": True}, timeout=timeout)


def onboard_equity_bulk(tickers: list[str], *, timeout: int = 60) -> dict:
    """POST /onboarding/equities/bulk — same as `onboard_equity`, batched."""
    return request("POST", f"{_PREFIX}/equities/bulk",
                   json={"tickers": list(tickers), "download": True}, timeout=timeout)


def onboard_brazilian_fund(cnpj: str, *, timeout: int = 30) -> dict:
    """POST /onboarding/funds/brazilianFund — derive fields for a single
    Brazilian quota fund from its CNPJ (punctuated or digits-only)."""
    return request("POST", f"{_PREFIX}/funds/brazilianFund",
                   json={"cnpj": cnpj, "download": True}, timeout=timeout)


def onboard_brazilian_fund_bulk(cnpjs: list[str], *, timeout: int = 60) -> dict:
    """POST /onboarding/funds/brazilianFund/bulk — same as
    `onboard_brazilian_fund`, batched."""
    return request("POST", f"{_PREFIX}/funds/brazilianFund/bulk",
                   json={"cnpjs": list(cnpjs), "download": True}, timeout=timeout)


def onboard_future(ticker: str, *, date: str = "", timeout: int = 30) -> dict:
    """POST /onboarding/futures — derive fields for a single futuro B3."""
    return request("POST", f"{_PREFIX}/futures",
                   json={"ticker": ticker, "date": date, "download": True}, timeout=timeout)


def onboard_future_bulk(tickers: list[str], *, date: str = "", timeout: int = 60) -> dict:
    """POST /onboarding/futures/bulk — same as `onboard_future`, batched."""
    return request("POST", f"{_PREFIX}/futures/bulk",
                   json={"tickers": list(tickers), "date": date, "download": True}, timeout=timeout)


def onboard_option(ticker: str, *, date: str = "", timeout: int = 30) -> dict:
    """POST /onboarding/options — derive fields for a single opção B3."""
    return request("POST", f"{_PREFIX}/options",
                   json={"ticker": ticker, "date": date, "download": True}, timeout=timeout)


def onboard_option_bulk(tickers: list[str], *, date: str = "", timeout: int = 60) -> dict:
    """POST /onboarding/options/bulk — same as `onboard_option`, batched."""
    return request("POST", f"{_PREFIX}/options/bulk",
                   json={"tickers": list(tickers), "date": date, "download": True}, timeout=timeout)
