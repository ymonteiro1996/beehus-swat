"""Cadastro de ativos automatizado — orquestração Tier 1 / Tier 2 / Pendente.

Given an unprocessed position (`unprocessedId` + the `securityType` already
classified by `SecurityTypeClassifier` earlier in the same Mapeamento pass —
see `pages/controlpanel.py`'s `/api/controlpanel/match`), decides how to
register it as a new Beehus security with NO LLM call at runtime:

  Tier 1 — `beehus_api.onboarding` (market-data-collector, Anbima/B3/CVM
           derivation) for CRI, CRA, Debênture, LF (when a B3/CETIP code was
           extracted), stockEtf, brazilianFund, futures. The Onboarding
           response is NOT a ready-to-create payload (confirmed live
           2026-07-30: `exchange`/`country` use different conventions than
           `/beehus/securities` expects) — `_normalize_onboarding_*` bridges
           the gap for the field(s) empirically verified so far.
  Tier 2 — `asset_registration_rules` deterministic formulas (source of truth:
           `reference/beehusName-regra-final.md` / `beehusName-regras.md`,
           gap-filled by REGRAS_CAMPOS_CADASTRO.md/REGRAS_BEEHUSNAME.md, see
           that module's docstring) for CDB/LCA/LCI/LCD/CCB/CDCA/LIG (same
           formula), and as a FALLBACK (when Onboarding doesn't resolve the
           code) for LF, CRI, CRA, Debênture. `CD`/`LC`/`NP` have builder
           functions ready in `asset_registration_rules.py` but are never
           dispatched here yet — `security_matcher._BOND_INSTRUMENT_RE`
           doesn't extract those instrument tokens today, so real volume for
           them is unknown; wire them up if/when Pendente volume shows they
           matter (Pareto, not a guess).
  Tier 3 — Pendente: every other type/instrument, or any Tier 1/2 attempt
           that couldn't confirm a required field. Never guesses — the human
           either fixes the field in the review modal or hands the item to
           the `/securities-cadastro` skill.

Every proposal (Tier 1 or 2) is checked for near-duplicates via
`beehus_api.check_similar_securities` — the same fuzzy issuer/type/indexer/
name match already proven live by `scripts/create_securities.py` — so the
human reviewing the batch sees "isso parece existir" warnings before
creating. `create_registration` re-runs that same check immediately before
`create_security_raw`, mirroring `scripts/create_securities.py:57-110`
exactly: only an EXACT identifier (ticker/isIn/taxId) match blocks creation
as a real duplicate; a fuzzy-only match is surfaced as a warning, not a block
(the human already saw it in the preview).
"""
import security_matcher
from beehus_api.exceptions import BeehusAPIError, BeehusAuthError
from beehus_api import onboarding as ob
from beehus_api import securities as sec_api
import asset_registration_rules as rules

# ── Tier 1 dispatch tables (instrument token -> onboarding call) ────────────
# Tokens come from `security_matcher._BOND_INSTRUMENT_RE` — only the ones
# actually emitted by that regex are reachable here.
_ONBOARD_CRI_CRA = {"CRI", "CRA"}
_ONBOARD_DEBENTURE = {"DEB", "DEBENTURE"}
_ONBOARD_LF = {"LF"}

# ── Tier 2 dispatch tables (v1 scope only) ──────────────────────────────────
# CDB/LCA/LCI/LCD/CCB/LIG all share the "sem taxa unificada" formula
# (confirmed live with the user — supersedes the older, taxed formula
# REGRAS_BEEHUSNAME.md §4.1/§4.3 still documents).
_RULES_RENDA_FIXA_SIMPLES = {"CDB", "LCA", "LCI", "LCD", "CCB", "LIG", "CDCA"}
_RULES_LF_SUB = {"LFS", "LFSN"}


class RegistrationProposal:
    """Result of *proposing* (not creating) a registration for one unprocessedId."""

    def __init__(self, unprocessed_id, *, tier=None, beehus_name=None,
                 payload=None, reason=None, warnings=None):
        self.unprocessed_id = unprocessed_id
        self.tier = tier  # "onboarding" | "rules" | "pendente"
        self.beehus_name = beehus_name
        self.payload = payload
        self.reason = reason
        self.warnings = list(warnings or [])

    def to_dict(self):
        return {
            "unprocessedId": self.unprocessed_id,
            "tier": self.tier,
            "beehusName": self.beehus_name,
            "payload": self.payload,
            "reason": self.reason,
            "warnings": self.warnings,
        }


# ── Onboarding response normalization (Tier 1 only) ─────────────────────────
# Confirmed live 2026-07-30 (POST .../onboarding/equities {"ticker":"PETR4"}):
# the response uses `exchange: "SAO"` (not the MIC code "BVMF"), a raw-feed
# `beehusName` with listing-segment noise (e.g. "PETROBRAS   PN  ATZ N2"), and
# a `country`/`feederIds` shape that beehusName-regra-final.md §4.5 says the
# real production stockEtf schema doesn't use at all (empirically: 0/1243
# real records have `country` or any feeder field populated). So this
# normalizes the TECHNICAL fields onboarding is authoritative for (ticker,
# exchange, currency, settlement/NAV days) but drops `country`/`feederIds`
# entirely and REBUILDS `beehusName` from the already-cleaned name
# `security_matcher._extract_stock_etf` derived from the raw uid (see
# `asset_registration_rules.build_stock_etf_name_br`), not from Onboarding's
# noisier raw-feed name.
_EXCHANGE_MAP = {"SAO": "BVMF"}


def _normalize_equity_payload(raw: dict, ticker: str, features: dict):
    payload = dict(raw)
    warnings = []
    payload.pop("country", None)
    payload.pop("feederIds", None)
    exch = str(payload.get("exchange") or "").upper()
    if exch in _EXCHANGE_MAP:
        payload["exchange"] = _EXCHANGE_MAP[exch]
        clean_name = features.get("name")
        if clean_name:
            payload["beehusName"] = rules.build_stock_etf_name_br(ticker, clean_name)
        else:
            warnings.append("nome limpo não extraído do texto bruto — beehusName usa o nome cru do Onboarding, revisar")
    elif exch:
        warnings.append(f"exchange '{exch}' do Onboarding não mapeado (BR/BVMF é o único caso validado; "
                        "formato de beehusName para bolsas internacionais ainda não implementado) — revisar antes de cadastrar")
    return payload, warnings


def _tier1_bond(uid, instrument, features):
    code = features.get("cetip_code") or features.get("internal_code")
    if not code:
        return None  # sem código -> deixa o chamador tentar Tier 2 (só LF tem fórmula)
    try:
        if instrument in _ONBOARD_CRI_CRA:
            raw = ob.onboard_cri_cra(code)
        elif instrument in _ONBOARD_DEBENTURE:
            raw = ob.onboard_debenture(code)
        elif instrument in _ONBOARD_LF:
            raw = ob.onboard_lf(code)
        else:
            return None
    except (BeehusAPIError, BeehusAuthError) as exc:
        return RegistrationProposal(uid, tier="pendente",
            reason=f"Onboarding API ({instrument} código {code}) falhou: {exc}")
    if not isinstance(raw, dict) or not raw.get("beehusName"):
        return None  # não encontrado -> deixa o chamador tentar Tier 2 / Pendente
    warnings = [f"payload de Onboarding para {instrument} ainda não teve o shape "
                "validado empiricamente (só stockEtf foi confirmado ao vivo) — revisar antes de cadastrar"]
    return RegistrationProposal(uid, tier="onboarding", beehus_name=raw.get("beehusName"),
                                payload=raw, warnings=warnings)


def propose_registration(uid: str, security_type: str) -> RegistrationProposal:
    """Tier 1 -> Tier 2 -> Pendente for one unprocessed position. Never
    raises — `RegistrationPending` from the rules layer is converted into a
    `tier="pendente"` proposal."""
    features = security_matcher.extract_features(uid, security_type)
    instrument = features.get("instrument")

    try:
        if security_type == "bond" and instrument in (
                _ONBOARD_CRI_CRA | _ONBOARD_DEBENTURE | _ONBOARD_LF):
            proposal = _tier1_bond(uid, instrument, features)
            if proposal is not None:
                return _attach_duplicate_check(proposal)
            # Onboarding não resolveu (sem código, ou código não encontrado):
            # cai para a fórmula manual (beehusName-regra-final.md §4.2-4.4 —
            # mesmo algoritmo de CDB, com canonicalização de devedor para
            # CRI/CRA/Debênture). Só existe fallback manual para ESTES tipos
            # porque o algoritmo já está formalmente especificado; não damos
            # o mesmo tratamento a outros tipos Tier 1 sem fórmula documentada.
            beehus_name, payload = rules.build_renda_fixa_simples(instrument, uid, features)
            return _attach_duplicate_check(RegistrationProposal(
                uid, tier="rules", beehus_name=beehus_name, payload=payload))

        if security_type == "bond" and instrument in _RULES_LF_SUB:
            beehus_name, payload = rules.build_lf(uid, features, subordinada=True)
            return _attach_duplicate_check(RegistrationProposal(
                uid, tier="rules", beehus_name=beehus_name, payload=payload))

        if security_type == "stockEtf":
            ticker = features.get("ticker")
            if not ticker:
                return RegistrationProposal(uid, tier="pendente",
                    reason="ticker não identificado no texto bruto para onboarding de ações/BDR/ETF")
            raw = ob.onboard_equity(ticker)
            if not isinstance(raw, dict) or not raw.get("beehusName"):
                return RegistrationProposal(uid, tier="pendente",
                    reason=f"Onboarding não encontrou o ticker '{ticker}'")
            payload, warnings = _normalize_equity_payload(raw, ticker, features)
            return _attach_duplicate_check(RegistrationProposal(
                uid, tier="onboarding", beehus_name=payload.get("beehusName"),
                payload=payload, warnings=warnings))

        if security_type == "brazilianFund":
            cnpj = features.get("cnpj")
            if not cnpj:
                return RegistrationProposal(uid, tier="pendente",
                    reason="CNPJ não identificado no texto bruto para onboarding de fundo brasileiro")
            raw = ob.onboard_brazilian_fund(cnpj)
            if not isinstance(raw, dict) or not raw.get("beehusName"):
                return RegistrationProposal(uid, tier="pendente",
                    reason=f"Onboarding não encontrou o CNPJ '{cnpj}'")
            warnings = ["payload de Onboarding para brazilianFund ainda não teve o shape "
                        "validado empiricamente (só stockEtf foi confirmado ao vivo) — revisar antes de cadastrar"]
            return _attach_duplicate_check(RegistrationProposal(
                uid, tier="onboarding", beehus_name=raw.get("beehusName"), payload=raw, warnings=warnings))

        if security_type == "futures":
            ticker = features.get("ticker")
            if not ticker:
                return RegistrationProposal(uid, tier="pendente",
                    reason="ticker não identificado no texto bruto para onboarding de futuros")
            raw = ob.onboard_future(ticker)
            if not isinstance(raw, dict) or not raw.get("beehusName"):
                return RegistrationProposal(uid, tier="pendente",
                    reason=f"Onboarding não encontrou o futuro '{ticker}'")
            warnings = ["payload de Onboarding para futures ainda não teve o shape "
                        "validado empiricamente (só stockEtf foi confirmado ao vivo) — revisar antes de cadastrar"]
            return _attach_duplicate_check(RegistrationProposal(
                uid, tier="onboarding", beehus_name=raw.get("beehusName"), payload=raw, warnings=warnings))

        if security_type == "bond" and instrument in _RULES_RENDA_FIXA_SIMPLES:
            beehus_name, payload = rules.build_renda_fixa_simples(instrument, uid, features)
            return _attach_duplicate_check(RegistrationProposal(
                uid, tier="rules", beehus_name=beehus_name, payload=payload))

        # beehusName-regra-final.md §4.9: compromissadas are classified under
        # securityType="otc" (not a standalone "brazilianRepo"), so we can't
        # dispatch on security_type alone — looks_like_compromissada checks
        # the raw text for the "Comp*"/"COMPROMISSADA" signal.
        if security_type in ("brazilianRepo", "otc") and rules.looks_like_compromissada(uid):
            beehus_name, payload = rules.build_compromissada(uid, features)
            return _attach_duplicate_check(RegistrationProposal(
                uid, tier="rules", beehus_name=beehus_name, payload=payload))

    except rules.RegistrationPending as exc:
        return RegistrationProposal(uid, tier="pendente", reason=exc.reason)
    except (BeehusAPIError, BeehusAuthError) as exc:
        return RegistrationProposal(uid, tier="pendente", reason=f"erro ao consultar Beehus: {exc}")

    return RegistrationProposal(uid, tier="pendente",
        reason=f"tipo '{security_type}' / instrumento '{instrument}' ainda não coberto pela "
                "automação v1 (onboarding: CRI/CRA/Debênture/LF/stockEtf/brazilianFund/futures; "
                "regras: CDB/LCA/LCI/LCD/CCB, LIG, LF-sub, Compromissada)")


def _attach_duplicate_check(proposal: RegistrationProposal) -> RegistrationProposal:
    """Best-effort: attaches a warning when `check_similar_securities` finds a
    fuzzy match. Never fails the proposal — a network hiccup here just means
    one fewer warning shown; the real gate runs again in `create_registration`."""
    if not proposal.payload:
        return proposal
    try:
        similar = sec_api.check_similar_securities(proposal.payload)
    except (BeehusAPIError, BeehusAuthError):
        return proposal
    if similar:
        names = [s.get("beehusName") or s.get("mainId") for s in similar]
        proposal.warnings.append(f"{len(similar)} ativo(s) parecido(s) já existem: {', '.join(map(str, names))}")
    return proposal


def _identifier(payload: dict):
    return payload.get("ticker") or payload.get("isIn") or payload.get("taxId") or None


def create_registration(proposal: RegistrationProposal) -> dict:
    """Creates one approved proposal in Beehus. Mirrors
    `scripts/create_securities.py:57-110` exactly: only an EXACT identifier
    match against `check_similar_securities` results is treated as a real
    duplicate (skip); a fuzzy-only match doesn't block (the human already saw
    it as a warning in the preview). Returns `{"status": "created"|"skipped"|
    "failed", ...}` — never raises."""
    label = proposal.beehus_name or proposal.unprocessed_id
    payload = proposal.payload
    if not payload:
        return {"unprocessedId": proposal.unprocessed_id, "status": "failed",
                "beehusName": label, "error": "sem payload (proposta não é Tier 1/2)"}

    try:
        similar = sec_api.check_similar_securities(payload)
    except (BeehusAPIError, BeehusAuthError) as exc:
        return {"unprocessedId": proposal.unprocessed_id, "status": "failed",
                "beehusName": label, "error": f"erro ao checar similares: {exc}"}

    ident = _identifier(payload)
    exact = [s for s in similar if ident and _identifier(s) == ident]
    if exact:
        names = [s.get("beehusName") or s.get("mainId") for s in exact]
        return {"unprocessedId": proposal.unprocessed_id, "status": "skipped",
                "beehusName": label, "reason": "identificador já existe", "similar": names}

    try:
        result = sec_api.create_security_raw(payload)
    except (BeehusAPIError, BeehusAuthError) as exc:
        body = getattr(exc, "body", "") or ""
        if "mesmo securityType e mainId" in body:
            return {"unprocessedId": proposal.unprocessed_id, "status": "skipped",
                    "beehusName": label, "reason": "mainId já existe na base"}
        return {"unprocessedId": proposal.unprocessed_id, "status": "failed",
                "beehusName": label, "error": str(exc)}

    return {"unprocessedId": proposal.unprocessed_id, "status": "created",
            "beehusName": label, "securityId": result.get("_id"), "mainId": result.get("mainId")}


def create_registration_payload(unprocessed_id: str, payload: dict) -> dict:
    """Entry point for the "Cadastrar no sistema" button: the modal already
    built `payload` client-side (same shape `_generateRegistrationJSON`
    downloads today, possibly hand-edited by the operator) — wraps it as a
    `RegistrationProposal` and reuses `create_registration`'s dedupe+create
    logic verbatim, so the manually-reviewed path and the Tier 1/2 automated
    path share the exact same creation gate."""
    proposal = RegistrationProposal(unprocessed_id, payload=payload,
                                    beehus_name=payload.get("beehusName"))
    return create_registration(proposal)
