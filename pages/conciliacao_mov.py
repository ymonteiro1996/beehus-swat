"""Conciliação (Movimentação).

Projeta a posição de uma carteira de uma data ORIGEM para uma data ALVO
(D+1 dia útil, ajustável) e calcula NAV / navPerShare / GAP **simulados**.

A tela inicial (Step 1) replica a grade da Conciliação (Não Proc.) **só até a
coluna GAP**; o botão "Movimentar" roda a projeção nas carteiras selecionadas e
preenche as colunas de NAV/cota/GAP simulados. O resultado pode ser baixado
(.xlsx), enviado ao Beehus (unprocessed da data-alvo) e tem um modal de
diagnóstico comparando a posição calculada com a unprocessed real do alvo.

Regras (spec), todas calculadas NESTE projeto (não no sistema origem):
  • BASE: unprocessedSecurityPositions da ORIGEM (preProcessingData), agregada
    por securityId (reusa conciliacao_unprocessed._aggregate_positions).
  • QUANTIDADE: transações na janela (origem, alvo] mudam a qtd — buySell soma
    `quantity`; quando `quantity` é nulo (≈100% desta base) infere `q=-balance/PU`
    (compra balance<0 → qtd↑; venda balance>0 → qtd↓). As linhas saem de
    source ∪ securityIds transacionados (ativo comprado na janela entra com
    former=0). split/inplit aplicam `factor` de securityEvents. Vencimento
    (`maturityDate <= alvo`) → qtd 0, balance 0, PU=executionPrice (ou PU da
    origem se executionPrice 0); o ativo PERMANECE na posição.
  • CAIXA: caixa da origem + Σ transações; ativo vencido SEM transação de
    maturity ainda impacta o caixa pelo seu saldo de origem.
  • PU: FONTE PRIMÁRIA = PU OFICIAL da processed-position do ALVO (autoritativo, quando o
    alvo já está processado); forward (alvo não processado) → PU da unprocessed-ALVO
    (snapshot, cobre C3) → B1/B2/C1/C2 via securityPrices → repetir PU da origem (ajustado
    por eventos de split/inplit). Cupom/amortização entram no caixa/contribuição.
  • PROVISÕES: (a) das transações buySell com offset de liquidação != 0 (régua de
    datas do diagnóstico — _prov_dates); (b) de DIVIDENDO/JCP de securityEvents
    (recebível = qtd-ORIGEM × dividendo-por-cota; datas = operationDate→liquidationDate
    do evento). Ambas entram no NAV simulado (a (b) pareia o rendimento do dia-ex).
  • NAV: navPerShare = (nav − inAndOutFlows) / formerAmount(origem);
    returnContribution = Σ contribuições / formerNav; GAP = rnps − rc.
  • DIAGNÓSTICO: compara posição calculada × unprocessed real do alvo
    (nav/cota/GAP + ativos divergentes + caixa).
"""
from flask import Blueprint, render_template, jsonify, request, Response
from db import (get_biz_dates, get_company_filter, company_visible,
                get_company_names, resolve_wallet)
import beehus_catalog
from pages.conciliacao_unprocessed import (_aggregate_positions, _position_name_hints,
                                           _group_unprocessed, _mapping_from_position)
from pages.conciliacao_shared import _diff_threshold_decimal
from pages.diagnostic_engine import _prov_dates
from beehus_api import (security_events as _api_security_events,
                        upload_unprocessed_security_positions_file as _api_upload_unprocessed,
                        create_provision as _api_create_provision,
                        create_transaction as _api_create_transaction,
                        create_execution_price as _api_create_execution_price,
                        update_execution_price as _api_update_execution_price,
                        BeehusAPIError, BeehusAuthError)

from datetime import date as _date, timedelta
import concurrent.futures
import io, math, logging, threading, time, copy

_log = logging.getLogger(__name__)

bp = Blueprint("conciliacao_mov", __name__)

_NUM_DATES = 10
_FLOW_TYPES = {"withdrawalDeposit", "withdrawalDepositAdjustment", "securityTransfer", "taxes"}
_EVENT_TYPES = {"coupon", "amortization"}
_QTY_TXN_TYPES = {"buySell"}                 # somam quantity (aditivo)
_SPLIT_EVENT_TYPES = {"split", "inplit", "grouping", "reverseSplit"}
# securityEvents com caixa POR COTA (dividendo/JCP). Alimentam a contribuição
# (div_ps×fq) E geram a provisão de recebível do Passo 6.5. provisionType usa os
# mesmos códigos de beehusTransactionType: cashDividend→"dividend", interestOnEquity→idem.
_DIVIDEND_EVENT_TYPES = ("cashDividend", "interestOnEquity")
# provisionType das provisões de dividendo/JCP (códigos beehusTransactionType) — p/ o
# guardrail anti-duplicação contra provisões OFICIAIS já existentes no sistema.
_DIVIDEND_PROV_TYPES = ("dividend", "interestOnEquity")
# Transações de P&L em NÍVEL DE CARTEIRA (ganhos/despesas/TAXAS): entram no
# `total_contribution` (returnContribution) E movimentam o caixa/NAV (como qualquer
# transação), mas NÃO são capital (não entram nos `inAndOutFlows`). TAXAS
# (managementFee = taxa de consultoria/gestão; otherFee; brokerageFee = corretagem)
# são CUSTOS que reduzem o retorno do investidor — igual a `gainsExpenses`/`rebate` —,
# não retiradas de capital: sem estar aqui, uma taxa derrubava a cota
# (returnNavPerShare) mas ficava fora do returnContribution, abrindo um GAP do tamanho
# exato da taxa (caso real 6a0f558b 15→16/jun: managementFee −4.751,32 = GAP −4.751,28).
_WALLET_CONTRIB_TYPES = {"gainsExpenses", "rebate", "managementFee", "otherFee", "brokerageFee"}
# Tipos de transação cujo CAIXA POSITIVO p/ um ativo é resgate de PRINCIPAL — o guard
# de vencimento soma só o RESÍDUO (former_bal − já lançado), evitando dupla contagem
# (resgate lançado como maturity/buySell/amortization/securityTransfer/withdrawalDeposit)
# E o sub-conto (amortização PARCIAL devolve o resto no vencimento). Cupom/taxes/dividendo
# NÃO retornam principal → não reduzem o resíduo. `maturity` é o tipo DIRETO do resgate no
# vencimento; sem ele o principal era contado 2× (txn maturity em all_cash + matured_cash).
_MATURITY_REDEMPT_TYPES = {"maturity", "buySell", "amortization", "securityTransfer", "withdrawalDeposit"}
# Ajustes de contribuição/retorno (não financeiros): entram na CONTRIBUIÇÃO
# (returnContribution, via wallet_contrib) mas NÃO no caixa projetado (`all_cash`) — o
# cashAccounts oficial não os reflete. São o PAR contábil do P&L de vencimento que entra
# no caixa via txns `maturity` mas não na contribuição (a marcação no vencimento usa
# former_pu). Continuam visíveis no detalhamento.
_CASH_EXCLUDED_TYPES = {"securityContributionAdjustment", "contributionAdjustment"}
# Security sintética da B3 p/ a liquidação CONSOLIDADA de stockETF: a B3 junta todas
# as operações de stockETF que liquidam na mesma data numa única transação buySell.
_B3_LIQ_SECURITY = "6a3fd49986ea629551686213"

# ── Tolerâncias do diagnóstico (centralizadas) ───────────────────────────────────
# (O caixa-âncora foi ELIMINADO — não há mais tolerância de "caixa bate". O resíduo de
#  caixa é só informativo; a divergência de qtd vira provisão de ajuste independente dele.)
# IRRF "coberto": |Σ taxes lançados − IRRF calculado| ≤ max(piso R$, |IRRF|×rel).
# Banda ESTREITA (1%) sobre a SOMA dos taxes — antes era 10% e por-transação, o que
# engolia discrepância real e duplicava IRRF lançado em parcelas (ver docs).
_IRRF_ABS_TOL = 1.0
_IRRF_REL_TOL = 0.01          # 1%
# Preço de execução: só vira FIX (PATCH/criar) se o execPrice DERIVADO diferir do PU
# por mais que isto — evita sobrescrever um execPrice real que por acaso ≈ PU e fixes
# inócuos (derivado ≈ PU → nada de intraday a acrescentar).
_EXECPRICE_REL_TOL = 1e-4     # 0,01%
# Divergência por ativo no diff: qtd é ABSOLUTA (sem escala monetária); saldo usa um
# piso absoluto (centavos) E o threshold relativo CONFIGURÁVEL (`_diff_threshold_decimal`
# de conciliacao_config.json) — alinha com o resto do sistema. Config 0 → só o piso.
_QTY_DIFF_TOL = 1e-6
_BALANCE_ABS_TOL = 0.01
# Casamento provisão-que-liquida ↔ transação de caixa: uma provisão de liquidação FUTURA,
# ao liquidar, tem que ser igualada por uma liquidação de caixa (mesmo sinal) p/ equilibrar
# o NAV. "Valor semelhante" = min/max dos saldos ≥ este ratio (90% → dentro de ±10%).
_LIQ_MATCH_RATIO = 0.90

# ── Cache TTL LOCAL desta página p/ a projeção em LOTE ────────────────────────────
# Cada projeção faz ~8 leituras pesadas e NÃO cacheadas na API (unprocessed×2,
# transações, preços, eventos, 2 envelopes processed-position, NAV). Na projeção em
# LOTE (`/movimentar-batch`) o `_prefetch_batch` busca tudo de uma vez e PRÉ-POPULA
# este cache; cada carteira lê com `use_cache=True` SEM I/O. `_cget` memoiza por chave
# com TTL e só entrega o valor cacheado quando `use_cache`; a projeção individual passa
# use_cache=False → relê FRESCO e re-aquece o cache. A leitura cacheada devolve uma
# cópia isolada p/ leituras repetidas nunca aliasarem o cache.
_PROJ_CACHE_TTL = 300.0
# Cap de espera do /prewarm quando `wait` (padrão): se o warm demorar mais que isto,
# o request volta com done:false e o daemon segue em background (evita prender o
# cliente se o upstream travar). O warm típico é ~2-3s (índice securities).
_PREWARM_WAIT_CAP = 20.0
_proj_cache = {}   # key(tuple) -> (inserted_monotonic, value)
# Flask roda threaded (1 thread por request): MUTAÇÕES do _proj_cache (escrita e o
# purge, que ITERA o dict) são serializadas por este lock — sem ele, um purge em
# iteração + um _cput de outro request = RuntimeError('dict changed size').
# Leituras pontuais (`dict.get`) são atômicas sob o GIL → ficam sem lock.
_proj_lock = threading.Lock()
# OVERLAY por-request (thread-local): o prewarm da rota single grava aqui o snapshot
# que acabou de buscar, e o `_cget` o consulta ANTES do cache global — a projeção lê
# EXATAMENTE o snapshot do próprio request, imune a purge/overwrite de requests
# concorrentes da mesma carteira (mistura de txns t0 com events t1) e ao shape
# reduzido de "nav" que o prefetch em lote grava sob a mesma chave.
_tl = threading.local()


def _cget(use_cache, key, loader):
    """Memo TTL local. `use_cache=True` relê do overlay do request (snapshot do
    prewarm) ou do cache global (se fresco); senão recarrega. SEMPRE re-aquece o
    cache (mesmo com use_cache=False)."""
    if use_cache:
        ov = getattr(_tl, "overlay", None)
        if ov is not None and key in ov:
            return copy.deepcopy(ov[key])
        hit = _proj_cache.get(key)
        if hit is not None and (time.monotonic() - hit[0]) < _PROJ_CACHE_TTL:
            return copy.deepcopy(hit[1])
    value = loader()
    _cput(key, value)   # mesma escrita do prefetch em lote (única fonte da linha)
    # CONTRATO: os HITS (overlay/global) devolvem deepcopy, mas o miss devolve o
    # PRÓPRIO objeto que também foi cacheado (e, no prewarm, compartilhado com o
    # overlay). Os consumidores NÃO podem mutar o retorno de `_cget` IN-PLACE —
    # copiam antes (ex.: `price_hp = dict(price_hp)` antes do único write). Mutar
    # aqui corromperia o cache e abriria corrida de leitura durante o deepcopy.
    return value


# ── number helpers ───────────────────────────────────────────────────────────

def _num(v, default=0.0):
    try:
        f = float(v)
        return default if (math.isinf(f) or math.isnan(f)) else f
    except (TypeError, ValueError):
        return default


def _safe_num(v):
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return None
    return v


def _pu_close(a, b):
    """'preço ≈ PU' (tolerância relativa 1e-5 + piso 1e-6) — detecta executionPrice
    PLACEHOLDER (apenas repetiu o PU; sem informação intraday). Único ponto da regra."""
    a, b = _num(a), _num(b)
    return abs(a - b) <= max(abs(a), abs(b)) * 1e-5 + 1e-6


def _sanitize(obj):
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(v) for v in obj]
    return _safe_num(obj)


def _next_biz_day(d_iso):
    """Primeiro dia útil (seg–sex) estritamente após `d_iso`."""
    try:
        d = _date.fromisoformat(d_iso)
    except (TypeError, ValueError):
        return d_iso
    cur = d + timedelta(days=1)
    while cur.weekday() >= 5:
        cur += timedelta(days=1)
    return cur.isoformat()


def _security_contribution(fq, fp, q, p, coupon_amort=0.0, dividend_ps=0.0, exec_price=None):
    """Fórmulas 4–7 de docs/CONCILIACAO_RECALCULO.md:
        daily    = formerQty × (PU − formerPU)
        intraday = (qty − formerQty) × (PU − execPrice)
        event    = couponAmort + dividendPS × formerQty
    """
    fq = _num(fq); fp = _num(fp); q = _num(q); p = _num(p)
    ep = _num(exec_price) if exec_price is not None else p
    daily = fq * (p - fp)
    intraday = (q - fq) * (p - ep)
    event = _num(coupon_amort) + _num(dividend_ps) * fq
    return round(daily + intraday + event, 2)


def _lots_by_sid(secs):
    """`{sid: [{unprocessedId, quantity, balance}]}` — quebra por LOTE (unprocessedId)
    ANTES da agregação por securityId. Vários unprocessedId podem resolver ao MESMO
    securityId (ex.: dois lotes/aliases do mesmo fundo — um com ISIN real, outro com
    ISIN placeholder BR0000000000). A projeção agrega por securityId (p/ NAV/diff/PU),
    mas o UPLOAD deve preservar as linhas SEPARADAS (senão o `Ativo` vira a concatenação
    dos unprocessedId). Este mapa guarda a origem p/ dividir a projeção de volta."""
    m = _mapping_from_position(secs)
    by_uid = _group_unprocessed(secs)
    out = {}
    for uid, d in by_uid.items():
        sid = m.get(uid)
        if sid:
            out.setdefault(sid, []).append({
                "unprocessedId": uid,
                "quantity": _num(d.get("quantity")),
                "balance": _num(d.get("balance")),
            })
    return out


def _project_lots(origin_lots, proj_qty, pu, proj_balance):
    """Divide a qtd/saldo PROJETADOS de um securityId de volta nos seus lotes de
    origem, proporcional à qtd de cada lote — EXATO quando não houve movimento
    (proj_qty == Σ origem → cada lote mantém a própria qtd). O PU é o mesmo (é do
    securityId). O ÚLTIMO lote recebe o resíduo p/ a soma bater EXATAMENTE com a
    linha agregada. Retorna None quando há ≤1 lote (nada a separar)."""
    lots = list(origin_lots or [])
    if len(lots) <= 1:
        return None
    o_tot = sum(_num(l.get("quantity")) for l in lots)
    n = len(lots)
    out, acc_q, acc_b = [], 0.0, 0.0
    for i, l in enumerate(lots):
        if i == n - 1:                       # último = resíduo (fecha a soma)
            lot_q = round(_num(proj_qty) - acc_q, 6)
            lot_b = round(_num(proj_balance) - acc_b, 2)
        else:
            frac = (_num(l.get("quantity")) / o_tot) if o_tot else (1.0 / n)
            lot_q = round(_num(proj_qty) * frac, 6)
            lot_b = round(_num(pu) * lot_q, 2)
            acc_q += lot_q
            acc_b += lot_b
        out.append({"unprocessedId": l.get("unprocessedId"),
                    "quantity": _safe_num(lot_q),
                    "pu": _safe_num(round(_num(pu), 6)),
                    "balance": _safe_num(lot_b)})
    return out


def _history_pu_on_date(history_price, date_str):
    """PU em `date_str` a partir de um `historyPrice[]` (entradas
    `{date, value}`). Casamento ESTRITO por data (sem carry-forward). None se
    não houver."""
    d = str(date_str)[:10]
    for hp in (history_price or []):
        if str(hp.get("date") or "")[:10] == d:
            v = _num(hp.get("value"), None) if hp.get("value") is not None else None
            return v
    return None


def _pricing_from_unproc(doc):
    """`{sid: pricingType}` da unprocessed (alvo), lido de `preProcessingData.pricingType`.
    O PU por securityId é derivado das linhas de `_aggregate_positions` no chamador
    (evita reagregar o mesmo doc duas vezes)."""
    pricing = {}
    for s in (doc or {}).get("securities", []) or []:
        pp = s.get("preProcessingData") or {}
        sid = str(pp.get("securityId") or "")
        pt = pp.get("pricingType")
        if sid and pt and sid not in pricing:
            pricing[sid] = pt
    return pricing


def _uid_to_pp(*docs):
    """`{unprocessedId: preProcessingData}` a partir das securities RESOLVIDAS (com
    securityId) de um ou mais docs unprocessed. Primeiro doc a resolver um uid vence
    (não sobrescreve). Base do enriquecimento bidirecional origem↔alvo."""
    out = {}
    for doc in docs:
        for s in (doc or {}).get("securities", []) or []:
            uid = s.get("unprocessedId")
            pp = s.get("preProcessingData") or {}
            if uid and pp.get("securityId") and uid not in out:
                out[uid] = pp
    return out


def _enrich_secs(secs, uid_pp):
    """Preenche securities NÃO resolvidas (sem securityId) a partir do mapa
    `{uid: preProcessingData}`.

    O pré-processamento de UMA das datas (origem OU alvo) às vezes vem presente porém
    **não resolvido** (securityId/pricingType/beehusName nulos — a posição foi importada
    mas ainda não pré-processada naquela data). Sem securityId, TODO ativo desse lado cai
    em 'não mapeado': na ORIGEM o motor só mostra o `unprocessedId`; no ALVO o diff perde
    a contraparte real e aponta TUDO como onlyCalc/onlyReal (revisão manual em massa).
    Como o `unprocessedId` é ESTÁVEL por ativo entre datas, a data resolvida doa
    securityId/pricingType/beehusName/mainId/pricingId à não resolvida.

    Não muta o doc cacheado (devolve cópias rasas só dos ativos tocados). Retorna
    `(securities, n_enriq)`."""
    if not uid_pp:
        return list(secs or []), 0
    out, n = [], 0
    for s in secs or []:
        pp = s.get("preProcessingData") or {}
        if not pp.get("securityId"):
            tpp = uid_pp.get(s.get("unprocessedId"))
            if tpp:
                pp2 = dict(pp)
                for k in ("securityId", "pricingType", "beehusName", "mainId", "pricingId"):
                    if not pp2.get(k) and tpp.get(k) is not None:
                        pp2[k] = tpp.get(k)
                s = {**s, "preProcessingData": pp2}
                n += 1
        out.append(s)
    return out, n


# ── PU/qtd oficiais da processed-position (do envelope já buscado) ────────────

def _pu_by_sid_from_env(env):
    """`{sid: pu}` da `position` de um envelope processed-position já buscado (PU
    OFICIAL/autoritativo). `{}` quando não há posição processada (env None — cenário
    forward) ou sem PU. Só inclui PU != 0/None. Puro (sem I/O)."""
    out = {}
    for s in ((env.get("position") or {}).get("securities") or []) if env else []:
        sid = str(s.get("securityId") or "")
        pu = _num(s.get("pu"))
        if sid and pu:
            out[sid] = pu
    return out


def _qty_by_sid_from_env(env):
    """`{sid: quantity}` da `position` de um envelope processed-position já buscado,
    ou None quando não há posição processada (env None). Usado p/ a qtd OFICIAL do alvo
    (filtro de posição fechada / adoção do alvo). Puro (sem I/O)."""
    if not env:
        return None
    secs = {}
    for s in ((env.get("position") or {}).get("securities") or []):
        sid = str(s.get("securityId") or "")
        if sid:
            secs[sid] = _num(s.get("quantity"))
    return secs


def _rows_from_env(env):
    """`tgt_rows`-shaped `{sid: {securityId, unprocessedIds, quantity, balance, pu}}` a
    partir da `position` da processed-position. FALLBACK do diff quando a unprocessed-alvo
    não está pré-processada (sem securityId): a processed traz a contraparte OFICIAL.
    `balance = pu×qty` quando ausente. Puro (sem I/O)."""
    out = {}
    for s in ((env.get("position") or {}).get("securities") or []) if env else []:
        sid = str(s.get("securityId") or "")
        if not sid:
            continue
        q = _num(s.get("quantity"))
        pu = _num(s.get("pu"))
        bal = _num(s.get("balance")) if s.get("balance") is not None else round(pu * q, 2)
        out.setdefault(sid, {"securityId": sid, "unprocessedIds": [],
                             "quantity": q, "balance": bal, "pu": pu})
    return out


# ── Estágios PUROS do motor (sem I/O) ────────────────────────────────────────
# O motor é organizado nos MESMOS blocos da "Memória de cálculo" da UI:
#   B1 projeção (posição-origem + transações)  → _aggregate_window_txns + _project_rows
#   B2 amountDifference (adoção da qtd-alvo)   → _adopt_target_qty
#   B3 caixa projetado                          → inline no orquestrador (3 linhas)
#   B4 outros (IRRF/recon/stockETF/diff)        → _diagnose (já puro)
#   B5 NAV e GAPs                               → _official_prov_in_nav_total + _compute_nav_and_gaps
# Todo I/O (fetch de unprocessed/txns/preços/envelopes/navPackage) fica no
# orquestrador `_build_movement_for_wallet`; os estágios recebem insumos prontos.

def _aggregate_window_txns(txns):
    """Agregados das transações da janela (B1/B3). Puro.

    Retorna `(txn_by_sid, all_cash, inflows, wallet_contrib, redempt_cash_by_sid)`:
      • all_cash    — Σ balance de TODAS que movem caixa (fora `_CASH_EXCLUDED_TYPES`:
                      ajustes de contribuição não refletem no cashAccounts oficial);
      • inflows     — Σ dos FLUXOS DE CAPITAL (`_FLOW_TYPES`) — mudam o nº de cotas,
                      saem do NAV/cota (não são P&L);
      • wallet_contrib — P&L de CARTEIRA: gainsExpenses/rebate (movem caixa) + ajustes
                      de contribuição (não movem caixa; é o PAR do termo excluído do
                      all_cash — sem isso o P&L de vencimento entraria no caixa/NAV sem
                      contrapartida na contribuição → GAP; caso real 6a0f558b 08→11/mai,
                      GAP$ 341,09 = Σ ajustes);
      • redempt_cash_by_sid — Σ caixa POSITIVO de resgate de principal por ativo
                      (`_MATURITY_REDEMPT_TYPES`) p/ o guard de vencimento somar só o
                      RESÍDUO (former_bal − já lançado). Cupom/taxes/dividendo ficam fora."""
    txn_by_sid = {}
    all_cash = inflows = wallet_contrib = 0.0
    redempt_cash_by_sid = {}
    for t in txns:
        sid = str(t.get("securityId") or "")
        typ = t.get("beehusTransactionType") or ""
        bal = _num(t.get("balance"))
        if typ not in _CASH_EXCLUDED_TYPES:
            all_cash += bal
        if typ in _FLOW_TYPES:
            inflows += bal
        if typ in _WALLET_CONTRIB_TYPES or typ in _CASH_EXCLUDED_TYPES:
            wallet_contrib += bal
        if sid:
            txn_by_sid.setdefault(sid, []).append(t)
            if typ in _MATURITY_REDEMPT_TYPES and bal > 0:
                redempt_cash_by_sid[sid] = redempt_cash_by_sid.get(sid, 0.0) + bal
    return txn_by_sid, all_cash, inflows, wallet_contrib, redempt_cash_by_sid


def _txn_effects(t):
    """Efeitos (buckets do cálculo) que UMA transação da janela alimentou — para o
    detalhamento mostrar, ao lado de cada transação, O QUE o motor fez com ela. Puro.

    Espelha EXATAMENTE os mesmos conjuntos de tipo consumidos em `_aggregate_window_txns`
    (caixa/fluxo/contribuição/resgate de principal), em `_project_rows` (quantidade, via
    `bs_txns`∈`_QTY_TXN_TYPES`) e no builder de eventos da janela (cupom/amort ∈ `_EVENT_TYPES`).
    Um mesmo tipo pode alimentar VÁRIOS buckets (ex.: `securityTransfer` = caixa + fluxo +
    resgate de principal; venda `buySell` = caixa + quantidade + resgate de principal), e a
    lista reflete isso fielmente. Retorna `[{code,label,title}]` (0+, ordem estável: do mais
    distintivo — o que identifica a transação — ao menos — o caixa, que quase todas movem)."""
    typ = t.get("beehusTransactionType") or ""
    bal = _num(t.get("balance"))
    sid = beehus_catalog.id_str(t.get("securityId")) if t.get("securityId") else ""
    eff = []

    def _add(code, label, title):
        eff.append({"code": code, "label": label, "title": title})

    if sid == _B3_LIQ_SECURITY:
        _add("b3", "liquidação B3",
             'Ativo genérico "Liquidação B3": a B3 consolida numa única transação os '
             "stockETF que liquidam na data. Alimenta o resíduo de custo de execução.")
    if typ in _QTY_TXN_TYPES:
        _add("qty", "quantidade",
             "Compra/venda: soma quantidade na projeção da carteira (quando a cota já existe "
             "na data-alvo; navDate futuro fica de fora e vira provisão de liquidação futura).")
    if typ in _EVENT_TYPES:
        _add("event", "cupom/amort.",
             "Cupom/amortização da janela: o caixa do evento entra na contribuição do ativo.")
    if typ in _FLOW_TYPES:
        _add("flow", "fluxo de capital",
             "Fluxo de capital (aporte/resgate/transferência/taxas): muda o nº de cotas e sai "
             "do NAV por cota — não é P&L (entra em inAndOutFlows).")
    if typ in _WALLET_CONTRIB_TYPES or typ in _CASH_EXCLUDED_TYPES:
        _add("contrib", "contribuição",
             "P&L de carteira (ganho/despesa, taxa ou ajuste): entra no returnContribution."
             + ("" if typ not in _CASH_EXCLUDED_TYPES
                else " Ajuste NÃO move o caixa projetado (par contábil do P&L de vencimento)."))
    if typ in _MATURITY_REDEMPT_TYPES and bal > 0:
        _add("principal", "resgate de principal",
             "Caixa positivo de resgate de principal: reduz o resíduo do guard de vencimento "
             "(não conta o principal 2×).")
    if typ not in _CASH_EXCLUDED_TYPES:
        _add("cash", "caixa",
             "Entrou no caixa projetado (Σ balance das transações da janela que movem caixa).")
    return eff


def _project_rows(*, src_rows, txn_by_sid, redempt_cash_by_sid,
                  coupon_amort_by_sid, split_by_sid, dividend_by_sid, dividend_events_by_sid,
                  price_hp, exec_price_by_sid,
                  tgt_pricing, tgt_pu, tgt_proc_pu, tgt_proc_qty, tgt_rows,
                  src_proc_pu, sec_meta, name_hints,
                  src_lots_by_sid, tgt_lots_by_sid, target_date):
    """B1b — PROJEÇÃO por ativo (Passos 2–4, 6, 6.5 + adoção do alvo + sort). Puro.

    Constrói as linhas projetadas da carteira na data-alvo a partir da posição-origem
    agregada + transações da janela + preços/eventos, e as provisões DERIVADAS
    (liquidação futura do Passo 6; dividendo/JCP do Passo 6.5). Inclui a adoção de
    ativos presentes SÓ no alvo (confirmados na processed-alvo — `adoptedFromTarget`).
    Todos os insumos chegam prontos (sem I/O). Retorna
    `(rows, provisions, exec_prices_view, total_contribution, matured_cash)`."""
    # ── Passos 2–4 + 6: construir linhas projetadas + provisões ─────────────────
    # Projeta os ativos do source UNIÃO os ativos só-transacionados na janela
    # (comprados/aportados DEPOIS da origem). Os "novos" entram com former=0 e a
    # quantidade é inferida de -balance/PU_alvo (executionPrice é nulo nesta base).
    src_covered = {r.get("securityId") for r in src_rows.values() if r.get("securityId")}
    new_sids = [s for s in txn_by_sid if s and s not in src_covered]
    proj_items = list(src_rows.items()) + [
        (s, {"securityId": s, "unprocessedIds": [], "quantity": 0.0, "balance": 0.0, "pu": 0.0})
        for s in new_sids]
    rows = []
    provisions = []
    exec_prices_view = []   # TODOS os preços de execução (p/ o bloco); isFix = a corrigir
    total_contribution = 0.0
    matured_cash = 0.0
    for _key, r in proj_items:
        sid = r.get("securityId")
        # "Liquidação B3" (_B3_LIQ_SECURITY) é um ativo GENÉRICO de liquidação/ajuste — suas
        # transações (buySell da liquidação consolidada, contributionAdjustment) são
        # SETTLEMENTS/ajustes, NÃO posições de mercado, então NÃO viram linha da carteira. O
        # caixa dessas transações já foi tratado no laço de transações acima e o resumo
        # stockETF/B3 é apurado em _diagnose — ambos independem desta
        # linha. Pertence aos blocos de Transações/Provisões, não à tabela de posições.
        if sid and beehus_catalog.id_str(sid) == _B3_LIQ_SECURITY:
            continue
        uid_label = ", ".join(r.get("unprocessedIds") or [])
        former_qty = _num(r.get("quantity"))
        former_pu = _num(r.get("pu"))
        former_bal = _num(r.get("balance"))
        # PU OFICIAL da processed-position da ORIGEM sobrepõe o `formerPu` — alinha a
        # contribuição diária (`formerQty×(PU−formerPU)`) ao oficial. Forward/origem não
        # processada → mantém o formerPu da unprocessed-origem. (Espelho da fonte do PU-alvo.)
        # NÃO recompõe `former_bal`: ele é o snapshot REAL da unprocessed-origem (usado no
        # principal de vencimento `matured_cash`, no filtro de posição-zerada e no display/diff)
        # e a contribuição NÃO o usa (só `former_qty`/`former_pu`). Recompô-lo deslocaria o
        # caixa de vencimento sem necessidade (verificação adversarial, jun/2026).
        _pp_src = src_proc_pu.get(sid) if sid else None
        if _pp_src:
            former_pu = _num(_pp_src)
        sec_name = name_hints.get(sid, uid_label) if sid else uid_label
        meta = sec_meta.get(sid or "", {}) if sid else {}

        # buySell da janela deste ativo — filtrado UMA vez (reusado no Passo 2/qty e no
        # Passo 6/provisão de liquidação futura; evita re-filtrar txn_by_sid 3× por linha).
        bs_txns = [t for t in txn_by_sid.get(sid, [])
                   if (t.get("beehusTransactionType") or "") in _QTY_TXN_TYPES] if sid else []
        # Split por navDate (data da COTA = operationDate + navDays úteis): só as
        # "position-effective" (navDate ≤ alvo) somam QUANTIDADE. As de navDate POSTERIOR ao
        # alvo (ex.: subscrição com subscriptionNAVDays>0) — a cota ainda não está na posição,
        # mas o caixa já saiu (liquidationDate ≤ alvo) — NÃO viram/somam linha; a compra fica
        # como PROVISÃO de subscrição em trânsito (Passo 6b, após o laço).
        bs_pos = [t for t in bs_txns if _txn_nav_date(t) <= target_date]
        has_qty_txn = bool(bs_pos)
        # Filtro de origem: ativo já ZERADO na origem (vencido/liquidado em data
        # anterior — a posição-origem mostra a linha mas com qtd/saldo 0) e SEM
        # transação que o reabra → descartado da projeção (não vira linha). Espelha
        # a regra da Repetir Posições; mantém ativos NOVOS (só transacionados).
        if abs(former_qty) < 1e-9 and abs(former_bal) < 0.005 and not has_qty_txn:
            continue
        # Filtro de ALVO: posição OFICIALMENTE FECHADA na data-alvo. O ativo SUMIU da
        # unprocessed-alvo, NÃO há transação de quantidade na janela que o movesse, NÃO
        # está vencido (vencimento tem passo próprio) E a processed-position do alvo
        # (autoritativa) o lista com qtd ≈ 0 → a posição foi encerrada pelo sistema.
        # Projetar a qtd da ORIGEM aqui fabricaria um `onlyCalc` falso (ex.: futuro
        # liquidado/ajustado sem `buySell` na janela). Só dropa COM confirmação oficial
        # (processed-alvo lista o ativo a 0); em forward (sem processed-alvo) mantém +
        # sinaliza a divergência normalmente. Caso real: "Futuro Mini Dólar - Jun/2026"
        # (69d6a0da…) origem −30 → some do alvo, processed-alvo qtd 0, 26→27/mai.
        _mat = str(meta.get("maturityDate") or "")[:10]
        _tpq = tgt_proc_qty or {}
        if (sid and sid not in tgt_rows and not has_qty_txn
                and not (_mat and _mat <= target_date)
                and sid in _tpq and abs(_num(_tpq.get(sid))) < 1e-9):
            continue

        coupon_amort = coupon_amort_by_sid.get(sid, 0.0) if sid else 0.0
        div_ps = dividend_by_sid.get(sid, 0.0) if sid else 0.0
        exec_price = _exec_price(txn_by_sid.get(sid, [])) if sid else None
        is_fund = bool(sid) and (meta.get("securityType") or "") == "brazilianFund"

        # ── Passo 2 — QUANTIDADE ─────────────────────────────────────────────────────
        # qty PROJETADA = qty da ORIGEM + Σ quantidade MOVIMENTADA por transação de qtd
        # (buySell). Regra ÚNICA p/ TODO ativo (inclusive FUNDO — não há mais via
        # "qtd-do-alvo" nem `amountDifference` no cálculo da quantidade; o amountDifference
        # só cria PROVISÃO quando não há transação associada). Quantidade movimentada de
        # cada transação, por PRIORIDADE:
        #   1. `quantity` da transação (quando vier preenchida)
        #   2. −balance / executionPrice CAPTURADO — SÓ quando difere do PU (= o usuário
        #      inputou um execPrice real; quando ≈ PU é placeholder → ignora, cai no 3)
        #   3. −balance / PU da POSIÇÃO ATUAL (processed-alvo → unprocessed-alvo)
        #   4. −balance / PU do securityPrices na data-alvo (se houver securityId)
        #   5. −balance / former_pu (PU da origem)
        #   6. −balance / 1 (assume PU = 1) — último recurso
        # Sinal: balance<0 (compra/aplicação) → qtd↑; balance>0 (venda/resgate) → qtd↓.
        # Em brazilianFund o divisor (PU) sobre o balance LÍQUIDO de IRRF deixa um resíduo =
        # IRRF na quantidade (esperado; o bloco de IRRF o explica).
        _cap_ep = (exec_price_by_sid.get(sid) or {}).get("price") if sid else None
        _pu_atual = (tgt_proc_pu.get(sid) if sid else None) or (tgt_pu.get(sid) if sid else None)
        _pu_sp = _history_pu_on_date(price_hp.get(sid), target_date) if sid else None
        # execPrice capturado só vale se INPUTADO (difere do PU); ≈ PU = placeholder → ignora.
        _cap_real = None
        if _cap_ep and (not _pu_atual or not _pu_close(_cap_ep, _pu_atual)):
            _cap_real = _num(_cap_ep)
        qty_div = (_cap_real                                       # 2 (execPrice ≠ PU)
                   or (_num(_pu_atual) if _pu_atual else None)     # 3
                   or (_num(_pu_sp) if _pu_sp else None)           # 4
                   or (former_pu if former_pu else None)           # 5
                   or 1.0)                                         # 6
        bs_delta = bs_balance = 0.0   # Δqty e Σbalance do buySell POSITION-EFFECTIVE (deriva o execPrice da contribuição)
        for t in bs_pos:
            b = _num(t.get("balance"))
            bs_balance += b
            q = t.get("quantity")
            bs_delta += _num(q) if q is not None else (-b / qty_div)   # 1 senão 2-6
        qty = former_qty + bs_delta
        # Passo 2b — split/inplit via factor de securityEvents (multiplicativo).
        split_factor = split_by_sid.get(sid) if sid else None
        if split_factor:
            qty *= split_factor

        # Passo 3 — vencimento (maturityDate <= alvo).
        maturity = str(meta.get("maturityDate") or "")[:10] or None
        matured = bool(maturity) and maturity <= target_date
        if matured:
            pu_final = exec_price if (exec_price and exec_price != 0) else former_pu
            qty = 0.0
            balance = 0.0
            pricing_used = "MATURITY"
            # Caixa: o vencimento devolve o principal (former_bal). Soma só o RESÍDUO
            # ainda NÃO lançado = former_bal − Σ(caixa de resgate já lançado p/ o ativo:
            # buySell/amortization/securityTransfer/withdrawalDeposit positivos). Assim
            # resgate lançado por QUALQUER desses tipos NÃO duplica; amortização
            # PARCIAL devolve o resto; cupom/taxes/dividendo (não-principal) não reduzem.
            if sid:
                matured_cash += max(0.0, former_bal - redempt_cash_by_sid.get(sid, 0.0))
        else:
            # PU resolvido pela cadeia normal (processed-position → snapshot → securityPrices
            # → repete origem). O fundo TOTALMENTE resgatado (qty 0) também passa por aqui: a
            # cota da data-alvo vem da processed-position (se houver) ou do securityPrices, e a
            # valorização do dia entra no `daily` (sem derivar execPrice — fundo não tem).
            pu_final, pricing_used = _resolve_pu(
                tgt_pricing.get(sid), tgt_pu.get(sid), price_hp.get(sid),
                target_date, former_pu, split_factor, tgt_proc_pu.get(sid))
            balance = round(pu_final * qty, 2)

        # Preço de execução p/ a contribuição INTRADAY — vale para o trade comum E
        # para o VENCIMENTO (resgate = "venda" de formerQty ao preço de execução do
        # dia → intraday = (qty − formerQty) × (PU − execPrice), que no vencimento
        # = formerQty × (execPrice − formerPU)). PRIMÁRIO = capturado do sistema
        # (endpoint execution-prices); quando ausente OU == PU (placeholder, sem
        # informação intraday) deriva-se de -Σbalance/Δqty (se houver buySell) e
        # registra-se um FIX p/ subir o preço calculado ao sistema (PATCH/criar).
        cap = (exec_price_by_sid.get(sid) or {}) if sid else {}
        captured = cap.get("price")
        contrib_exec = captured if captured is not None else exec_price
        # placeholder = sem informação intraday real: capturado AUSENTE ou ≈ PU
        # (apenas repetiu o PU). Nesse caso deriva-se o execPrice de -Σbalance/Δqty.
        placeholder = (contrib_exec is None or _pu_close(contrib_exec, pu_final))
        derived = round(-bs_balance / bs_delta, 8) if abs(bs_delta) > 1e-9 else None
        # Fundo (brazilianFund) NÃO tem execPrice → nunca deriva/usa preço de execução: o
        # resgate líquido vira `daily` (cota) + IRRF (passo de IRRF), não intraday.
        if placeholder and derived is not None and not is_fund:
            contrib_exec = derived
        # Visão de preços de execução — TODO ativo com record capturado OU trade na
        # janela (não só os placeholder). Os a corrigir (placeholder/ausente
        # deriváveis) recebem isFix=True; os reais (execPrice != PU) ficam "ok".
        # FUNDO é EXCLUÍDO da visão: não tem execPrice (o resíduo é IRRF, não preço).
        if sid and not is_fund and (captured is not None or derived is not None):
            # só é FIX se o execPrice DERIVADO difere MATERIALMENTE do PU — senão
            # não há informação intraday a acrescentar (overwrite inócuo) e protege um
            # execPrice REAL que por acaso ≈ PU (aí o derivado também ≈ PU → não marca).
            is_fix = bool(placeholder and derived is not None
                          and abs(derived - pu_final) > max(abs(pu_final), abs(derived)) * _EXECPRICE_REL_TOL)
            # Impacto na CONTRIBUIÇÃO (termo intraday): (qty − formerQty) × (PU − execUsado).
            # `contrib_exec` é o preço EM EFEITO (derivado nos placeholder/ausente com fix;
            # capturado real nos "ok"). Num placeholder ≈PU o intraday era ~0 → este valor é
            # exatamente o que a correção do execPrice ACRESCENTA ao returnContribution (já
            # refletido no `simReturnContribution`, pois o derivado já entra em `contribution`).
            intraday_contrib = round((_num(qty) - _num(former_qty)) * (_num(pu_final) - _num(contrib_exec)), 2)
            exec_prices_view.append({
                "securityId": sid, "securityName": sec_name,
                "recordId": cap.get("id"),     # PATCH se existir record; senão criar
                "positionDate": cap.get("positionDate") or target_date,
                "capturedPrice": _safe_num(captured),   # ==PU (placeholder), real, ou None (ausente)
                "pu": _safe_num(pu_final),
                "calculatedPrice": _safe_num(derived),
                "intradayContribution": _safe_num(intraday_contrib),
                "status": ("placeholder" if (captured is not None and placeholder)
                           else "ausente" if captured is None else "ok"),
                "action": (("update" if cap.get("id") else "create") if is_fix else None),
                "isFix": is_fix,
            })

        contribution = _security_contribution(
            former_qty, former_pu, qty, pu_final, coupon_amort, div_ps, contrib_exec)
        total_contribution += contribution

        # Passo 6 — provisão de LIQUIDAÇÃO FUTURA p/ buySell cujo caixa settla DEPOIS do alvo.
        if sid and not matured:
            for t in bs_txns:
                # O `liquidationDate` da transação É o dia do SETTLEMENT (o caixa entra/sai
                # nesse dia; o navDate fica `offset` dias ANTES). A provisão só faz sentido
                # quando o settlement é FUTURO em relação ao alvo. As transações são buscadas
                # por liquidationDate ≤ alvo → o caixa já liquidou na janela (entra no
                # `all_cash`) e NÃO há liquidação futura a provisionar; provisionar dobraria o
                # caixa. (Caso real RIZA LOTUS, resgate liq.=alvo, navDate antes: a provisão
                # `target+offset` cancelava o caixa real e abria GAP de −200k.)
                t_liq = str(t.get("liquidationDate") or "")[:10]
                if t_liq <= target_date:
                    continue
                offset = _settlement_offset(t)
                if offset:
                    init_d, liq_d = _prov_dates(target_date, offset)
                    pbal = -_num(t.get("balance"))   # caixa pendente (sinal invertido)
                    provisions.append({
                        "securityId": sid, "securityName": sec_name,
                        "initialDate": init_d, "liquidationDate": liq_d,
                        "provisionType": "buySell", "balance": round(pbal, 2),
                        "offset": offset,
                        "description": f"Provisão de liquidação futura — {sec_name}",
                    })

        # Passo 6.5 — provisão de DIVIDENDO/JCP a partir de securityEvents.
        # O evento credita rendimento na contribuição (div_ps·fq, Passo 7) mas o
        # caixa só entra na liquidação (transação futura). A provisão de RECEBÍVEL
        # (balance = qtd-ORIGEM × dividendo-por-cota, sinal + = a receber) entra no
        # NAV simulado e PAREIA esse rendimento → fecha o GAP do dia-ex (senão
        # sobraria GAP = −div_ps·fq, pois o PU já caiu ex-dividendo no daily).
        # Datas do próprio evento: initialDate = operationDate (dia-ex),
        # liquidationDate = liquidationDate (pagamento). NÃO inverte o sinal (não há
        # caixa lançado a estornar, ao contrário do Passo 6).
        if sid and abs(former_qty) > 1e-9:
            for ev in dividend_events_by_sid.get(sid, []):
                ev_bal = _num(ev.get("balance"))
                pbal = round(former_qty * ev_bal, 2)
                if abs(pbal) < 0.005:
                    continue
                ptype = ("interestOnEquity" if ev.get("eventType") == "interestOnEquity"
                         else "dividend")
                provisions.append({
                    "securityId": sid, "securityName": sec_name,
                    "initialDate": ev.get("operationDate") or target_date,
                    "liquidationDate": ev.get("liquidationDate") or target_date,
                    "provisionType": ptype, "provisionSource": "corporate-actions",
                    "balance": pbal,
                    "description": f"Provisão de {ptype} — {sec_name}",
                })

        # Lotes separados p/ o upload (quando >1 unprocessedId no mesmo securityId).
        _row_lots = _project_lots(src_lots_by_sid.get(sid), qty, pu_final,
                                  round(balance, 2)) if sid else None
        _row = {
            "securityId": sid, "unprocessedId": uid_label,
            "securityName": sec_name, "mapped": bool(sid),
            "pricingType": pricing_used,
            "formerQuantity": _safe_num(round(former_qty, 6)),
            "formerPu": _safe_num(round(former_pu, 6)),
            "formerBalance": _safe_num(round(former_bal, 2)),
            "quantity": _safe_num(round(qty, 6)),
            "pu": _safe_num(round(pu_final, 6)),
            "balance": _safe_num(round(balance, 2)),
            "matured": matured,
            "couponAmort": _safe_num(round(coupon_amort, 2)) if coupon_amort else None,
            "contribution": _safe_num(contribution),
        }
        if _row_lots:
            _row["lots"] = _row_lots
        rows.append(_row)

    # ── Passo 6b — PROVISÃO de SUBSCRIÇÃO EM TRÂNSITO (independe da linha) ──────────
    # buySell cuja COTA (navDate = operationDate + navDays úteis) é POSTERIOR ao alvo: as
    # cotas ainda NÃO estão na posição (não somaram qtd/linha, ver `bs_pos` acima), mas o
    # caixa já saiu (liquidationDate ≤ alvo → no `all_cash`). A compra fica representada por
    # PROVISÃO (+ = a receber), NÃO por posição — senão a projeção mostra cotas que o alvo
    # não tem (onlyCalc + GAP). Roda FORA do laço p/ cobrir também o ativo NOVO cuja única
    # transação é future-nav (a linha nem é criada). O diagnóstico marca `duplicatesOfficial`
    # (valor 2-casas + tipo) quando já há provisão idêntica no sistema → a UI a pré-ignora
    # (nasce ignorada), evitando dobrar com a provisão oficial de compra.
    for _sid2, _ts in (txn_by_sid or {}).items():
        if not _sid2 or beehus_catalog.id_str(_sid2) == _B3_LIQ_SECURITY:
            continue
        _m2 = str((sec_meta.get(_sid2, {}) or {}).get("maturityDate") or "")[:10]
        if _m2 and _m2 <= target_date:
            continue   # vencido tem passo próprio
        for t in _ts:
            if (t.get("beehusTransactionType") or "") not in _QTY_TXN_TYPES:
                continue
            if _txn_nav_date(t) <= target_date:
                continue   # cota já na posição
            pbal = round(-_num(t.get("balance")), 2)
            if abs(pbal) < 0.005:
                continue
            _snm = name_hints.get(_sid2, _sid2)
            provisions.append({
                "securityId": _sid2, "securityName": _snm,
                "initialDate": target_date, "liquidationDate": _txn_nav_date(t),
                "provisionType": "buySell", "provisionSource": "subscription",
                "balance": pbal,
                "description": f"Compra {_snm} (cota após o alvo — navDays)",
            })

    # ── Adoção de ativos OFICIALMENTE PRESENTES no alvo, AUSENTES da projeção ──────
    # Espelho INVERTIDO do filtro de posição fechada: a processed-position do alvo
    # (autoritativa) confirma o ativo com **qtd ≠ 0** (POSITIVA ou NEGATIVA — futuros/
    # vendidos a descoberto têm qtd<0), mas ele NÃO veio da origem nem de transação da
    # janela (entrou por securityTransfer/reestruturação que o motor não deriva). Copia a
    # posição do ALVO p/ a projeção — senão sumia (onlyReal) e o /apply a perderia. Regra
    # de qtd UNIFICADA com o filtro de fechamento: só fica de fora quando a qtd oficial é
    # 0; qualquer qtd ≠ 0 é adotada. Forward (sem processed-alvo) → não adota → segue
    # onlyReal p/ revisão. Adotado = SEM P&L (contribuição 0; entrou por transferência);
    # entra no sim_nav pelo saldo. Caso real 69b99c5a 12→13/mai: Investo ETF (200) e
    # CDB BS2 (50) só no alvo, confirmados na processed-alvo.
    _proj_sids = {r.get("securityId") for r in rows if r.get("securityId")}
    for _sid, _tr in (tgt_rows or {}).items():
        if not _sid or _sid in _proj_sids:
            continue
        _pq = (tgt_proc_qty or {}).get(_sid)
        if _pq is None or abs(_num(_pq)) < 1e-9:
            continue   # sem confirmação oficial OU qtd oficial 0 → não adota
        _aq = _num(_tr.get("quantity"))
        _apu = _num(_tr.get("pu")) or _num((tgt_proc_pu or {}).get(_sid))
        _abal = _num(_tr.get("balance")) if _tr.get("balance") is not None else round(_apu * _aq, 2)
        _adopt_lots = _project_lots(tgt_lots_by_sid.get(_sid), _aq, _apu, round(_abal, 2))
        _arow = {
            "securityId": _sid, "unprocessedId": ", ".join(_tr.get("unprocessedIds") or []),
            "securityName": name_hints.get(_sid, _sid), "mapped": True,
            "pricingType": tgt_pricing.get(_sid) or "ALVO", "adoptedFromTarget": True,
            "formerQuantity": 0.0, "formerPu": 0.0, "formerBalance": 0.0,
            "quantity": _safe_num(round(_aq, 6)), "pu": _safe_num(round(_apu, 6)),
            "balance": _safe_num(round(_abal, 2)), "matured": False,
            "couponAmort": None, "contribution": 0.0,
        }
        if _adopt_lots:
            _arow["lots"] = _adopt_lots
        rows.append(_arow)
        _proj_sids.add(_sid)

    rows.sort(key=lambda x: (0 if not x["mapped"] else 1, x["securityName"] or ""))
    return rows, provisions, exec_prices_view, total_contribution, matured_cash


def _adopt_target_qty(rows, diag):
    """B2 — ADOÇÃO da qtd-ALVO (amountDifference → provisão). Puro (sem I/O), mas MUTA em
    lugar: `rows` (quantity/balance das linhas adotadas), as entries de
    `diag["diff"]["diverged"]` (flag `adoptedTargetQty`) e cada `reconProvisions`
    (`intradayContribution`). Retorna `(base_rows_balance, recon_prov_in_nav, adoption_intraday)`:
      • base_rows_balance — Σ saldos das linhas ANTES da adoção (base da etapa "mov"
        dos GAPs por etapa);
      • recon_prov_in_nav — Σ saldos das provisões de ajuste (−execPrice×Δqty) somados
        ao sim_nav p/ casar as cotas adotadas;
      • adoption_intraday — Σ P&L INTRADAY das adoções = Σ variação de NAV de cada adoção
        (Δcotas + provisão) = Σ Δqty×(PU−execPrice). Somado ao returnContribution pelo
        chamador p/ PAREAR o mesmo valor no sim_nav → o GAP não muda (gap_amdiff ≈ 0)."""
    # ── Adotar a qtd-ALVO nos ativos com PROVISÃO DE AJUSTE (amountDifference) ──────
    # A divergência de qtd (que não seja vencimento) vira PROVISÃO de ajuste (liquidação
    # FUTURA, sem transaction na janela) — a cota já foi apurada no alvo; só o caixa está
    # pendente. Então a posição projetada ADOTA a quantidade do ALVO e a
    # provisão de ajuste (o caixa a liquidar) entra no NAV simulado. As cotas adotadas
    # entram ao PU (+Δqty×PU) e a provisão ao execPrice (−execPrice×Δqty). Quando
    # execPrice = PU (fallback pu-alvo) isso se cancela → NAV-NEUTRO (o GAP não muda; só a
    # projetada passa a IGUALAR o alvo em qtd). Quando execPrice ≠ PU (executionPrice real)
    # a adoção soma Δqty×(PU−execPrice) ao NAV = P&L INTRADAY do trade adotado; esse mesmo
    # valor é devolvido em `adoption_intraday` p/ o chamador somá-lo ao returnContribution
    # → NAV e contribuição sobem juntos e o GAP volta a não mudar (gap_amdiff ≈ 0).
    # Vale para os DOIS caminhos da liquidação futura: (a) a provisão de ajuste CRIADA
    # agora (reconProvisions, caixa bate) e (b) a divergência COBERTA por provisão OFICIAL
    # já existente (coveredByProvision) — mesmo caso econômico, só muda a origem da provisão.
    # Saldo das linhas ANTES da adoção amDiff — base da etapa "mov" (GAPs por etapa, ver Passo 7).
    base_rows_balance = round(sum(_num(r["balance"]) for r in rows), 2)
    recon_prov_in_nav = 0.0
    adoption_intraday = 0.0   # Σ P&L intraday das adoções → devolvido p/ a contribuição
    _rows_by_sid = {r["securityId"]: r for r in rows if r.get("securityId")}
    _adopted_sids = set()
    # (a) provisões de ajuste CRIADAS nesta corrida (reconProvisions). O offset é o próprio
    # saldo da provisão (−execPrice×Δqty) calculado no diagnóstico; o intraday da adoção é a
    # variação líquida de NAV (Δcotas ao PU + provisão ao execPrice) = Δqty×(PU−execPrice).
    for _rp in diag["reconProvisions"]:
        # TODOS os recon (inclusive `coveredByOfficial`/`duplicatesOfficial`) são adotados: entram
        # no sim_nav pela adoção da qtd-alvo. Os que duplicam uma oficial nascem pré-ignorados na
        # UI (o what-if desconta o saldo → NAV/GAP corretos) mas seguem no sim_nav CRU — como
        # qualquer `duplicatesOfficial`; assim o toggle "↩ considerar" volta a somá-los.
        _row = _rows_by_sid.get(_rp.get("securityId"))
        if _row is None:
            continue
        _old_bal = _num(_row.get("balance"))
        _real_q = round(_num(_row.get("quantity")) + _num(_rp.get("amountDifference")), 6)
        _row["quantity"] = _safe_num(_real_q)
        _new_bal = round(_real_q * _num(_row.get("pu")), 2)
        _row["balance"] = _safe_num(_new_bal)
        recon_prov_in_nav += _num(_rp.get("balance"))
        _intraday = round((_new_bal - _old_bal) + _num(_rp.get("balance")), 2)
        _rp["intradayContribution"] = _safe_num(_intraday)
        adoption_intraday += _intraday
        _adopted_sids.add(_rp.get("securityId"))
    # (b) divergência de qtd COBERTA por provisão OFICIAL já existente (sem recon criado —
    # o guardrail do diagnóstico não duplica). A provisão oficial fica FORA do sim_nav, então
    # SINTETIZAMOS o offset NAV-neutro (−Δqty×PU, com o PU da própria linha) p/ casar a cota
    # adotada → o GAP não muda; a projetada passa a bater o alvo em qtd.
    for _dvg in diag["diff"].get("diverged", []):
        _sid = _dvg.get("securityId")
        # NÃO adotar a qtd-alvo em VENCIMENTO: o ativo vencido é zerado na projeção e
        # resolvido por TRANSAÇÃO de resgate (regra) — adotar ressuscitaria o ativo.
        # (Defesa: o vencimento já não recebe `coveredByProvision` no _diagnose.)
        if not (_dvg.get("qtyDiverged") and _dvg.get("coveredByProvision")) or not _sid \
                or _dvg.get("matured"):
            continue
        _row = _rows_by_sid.get(_sid)
        if _row is None or _sid in _adopted_sids:
            continue
        _pu = _num(_row.get("pu"))
        _target_q = round(_num(_dvg.get("realQuantity")), 6)
        _delta = round(_target_q - _num(_row.get("quantity")), 6)
        _old_bal = _num(_row.get("balance"))
        _row["quantity"] = _safe_num(_target_q)
        _new_bal = round(_target_q * _pu, 2)
        _row["balance"] = _safe_num(_new_bal)
        _off = round(-_delta * _pu, 2)   # offset sintetizado ao PU da linha → NAV-neutro
        recon_prov_in_nav += _off
        adoption_intraday += round((_new_bal - _old_bal) + _off, 2)   # ≈ 0 (offset ao PU)
        _adopted_sids.add(_sid)
    recon_prov_in_nav = round(recon_prov_in_nav, 2)
    adoption_intraday = round(adoption_intraday, 2)
    # Marca a divergência (não-destrutivo) como resolvida pela provisão de ajuste — a
    # projetada agora bate o alvo; a entry do diff explica a origem (provisão criada/oficial).
    for _dvg in diag["diff"].get("diverged", []):
        if _dvg.get("securityId") in _adopted_sids:
            _dvg["adoptedTargetQty"] = True
    return base_rows_balance, recon_prov_in_nav, adoption_intraday


def _official_prov_in_nav_total(provisions, off_provs, diverged):
    """B5a — Σ provisões OFICIAIS pendentes no alvo que entram no sim_nav. Puro.
    Dedup: exclui as já representadas por provisão do MOTOR (mesmo securityId em
    `provisions`) e as que cobrem divergência de qtd (`coveredByProvision` — offset
    NAV-neutro sintetizado na adoção B2). Retorna o total (float, 2 casas)."""
    # ── Provisões OFICIAIS pendentes no alvo que entram no NAV simulado ──────────
    # O NAV oficial do alvo inclui as provisões ATIVAS na data (off_provs); o sim_nav
    # precisa das MESMAS p/ reconciliar. O motor já cobre dois subconjuntos: (a) as que
    # ele DERIVA da janela (Passo 6/6.5, em `prov_total`) e (b) as que cobrem divergência
    # de qtd (`coveredByProvision`, sintetizadas NAV-neutro em `recon_prov_in_nav`).
    # FALTAVAM as DEMAIS — provisões que o sistema tem mas o motor não deriva da janela
    # (ex.: settlement que liquida DEPOIS do alvo → sem transação na janela; sem
    # securityId). Sem elas, o sim_nav fica ACIMA do oficial pelo exato valor da provisão
    # (caso real 69b99c5a 26→27/mai: provisão buySell −197.841,77 = GAP). Dedup: exclui as
    # já representadas por engine-provision ou por coveredByProvision (recon). As que liquidam
    # NA data-alvo já não estão em off_provs (envelope traz só `initialDate <= data < liquidationDate`).
    # A dedup engine↔oficial casa por (securityId, provisionType) — NÃO só por securityId: um
    # ativo pode ter uma provisão de DIVIDENDO (Passo 6.5) E uma oficial buySell (ex.: compra
    # pendente coberta) no MESMO securityId; casar só por sid excluía a buySell indevidamente,
    # e como o recon coberto (que precisa da oficial NO NAV como âncora do born-ignored) é
    # descontado, o passivo ficava contado ZERO vezes → GAP falso (revisão adversarial jul/2026).
    # Só provisões-engine ATIVAS (não `duplicatesOfficial`) excluem a oficial equivalente. Uma
    # provisão-engine `duplicatesOfficial` (ex.: subscrição em trânsito do Passo 6b com oficial
    # de MESMO securityId) será DESCONTADA no born-ignored da UI — se ela também excluísse a
    # oficial, a compra ficaria contada ZERO vezes (engine descontada + oficial excluída) → NAV
    # baixo demais / GAP falso. Mantendo a oficial no NAV, o desconto do duplicata deixa a oficial
    # contada 1×. (Bug real exposto pelo usuário: Kapitalo NW3 −180k; revisão jul/2026.)
    _engine_prov_keys = {(beehus_catalog.id_str(p.get("securityId")), p.get("provisionType") or "")
                         for p in provisions if p.get("securityId") and not p.get("duplicatesOfficial")}
    _covered_sids = {d.get("securityId") for d in (diverged or [])
                     if d.get("coveredByProvision")}
    official_prov_in_nav = round(sum(
        _num(p.get("balance")) for p in (off_provs or [])
        if (beehus_catalog.id_str(p.get("securityId")), p.get("provisionType") or "") not in _engine_prov_keys
        and beehus_catalog.id_str(p.get("securityId")) not in _covered_sids), 2)
    return official_prov_in_nav


def _compute_nav_and_gaps(*, rows, new_cash, prov_total, recon_prov_in_nav,
                          official_prov_in_nav, inflows, total_contribution,
                          former_nav, former_nps, shares,
                          base_rows_balance, cash_residual, adoption_intraday=0.0):
    """B5b — NAV / cota / retornos / GAP simulados + GAPs POR ETAPA. Puro.

    `cash_residual` = resíduo de caixa (INFORMATIVO) já computado no `_diagnose`
    (`diff.cashResidual`). `adoption_intraday` = P&L intraday das adoções da qtd-alvo
    (já incluído em `total_contribution`); a etapa "mov" o SUBTRAI p/ medir o GAP
    pré-adoção. Retorna dict com sim_nav, sim_nps, ret_nps, ret_contrib, gap_pct,
    gap_cash e gap_stages (mov/amDiff/cash)."""
    # ── Passo 7: NAV / navPerShare / GAP simulados (após a adoção da qtd-alvo acima).
    # `recon_prov_in_nav` = Σ provisões de ajuste adotadas, somadas ao NAV p/ casar a
    # qtd adotada (NAV-neutro). `official_prov_in_nav` = Σ provisões oficiais pendentes
    # (acima). As demais reconProvisions/reconTransactions seguem write-only (fora do NAV).
    sim_nav = round(sum(_num(r["balance"]) for r in rows) + new_cash + prov_total
                    + recon_prov_in_nav + official_prov_in_nav, 2)
    sim_nps = round((sim_nav - inflows) / shares, 8) if shares else None
    ret_nps = round(sim_nps / former_nps - 1, 8) if (sim_nps is not None and former_nps) else None
    ret_contrib = round(total_contribution / former_nav, 8) if former_nav else None
    gap_pct = round(ret_nps - ret_contrib, 8) if (ret_nps is not None and ret_contrib is not None) else None
    gap_cash = round(gap_pct * former_nav, 2) if (gap_pct is not None and former_nav) else None

    # ── GAPs POR ETAPA (verificação de "cada etapa é auto-contida") ───────────────────────
    # Identidade: GAP$ = simNav − inflows − formerNav − contribuição (linear em simNav). Mede
    # o GAP em checkpoints cumulativos do simNav p/ localizar de ONDE vem o GAP:
    #   • mov    = projeção (posição-origem + movimentos) + caixa projetado + provisões
    #              (derivadas/oficiais), ANTES da adoção amDiff. É o resíduo da projeção em si.
    #   • amDiff = incremento do GAP causado pela adoção da qtd-alvo (reconProvisions /
    #              coveredByProvision). Deve ser ≈ 0 — a adoção soma Δqty×(PU−execPrice) ao
    #              sim_nav (cota ao PU + provisão ao execPrice) E o MESMO valor à contribuição
    #              (`adoption_intraday`, P&L intraday), então NAV e contribuição sobem juntos
    #              e o GAP não muda; ≠ 0 só se essa neutralidade for quebrada (guard de regressão).
    #   • cash   = caixa PROJETADO − caixa OFICIAL (INFORMATIVO). NÃO é um GAP de retorno
    #              (o caixa não se calcula isolado) e NÃO classifica mais nada — o caixa-âncora
    #              foi eliminado; é só o resíduo de caixa exibido. None sem caixa oficial (forward).
    def _gap_cash_for(nav, contrib):
        if nav is None or not shares or not former_nps or contrib is None or not former_nav:
            return None
        _nps = round((nav - inflows) / shares, 8)
        _rnps = round(_nps / former_nps - 1, 8)
        _rc = round(contrib / former_nav, 8)
        return round((_rnps - _rc) * former_nav, 2)
    # etapa "mov" = projeção PRÉ-adoção: NAV sem a adoção E contribuição sem o P&L intraday
    # dela (subtrai `adoption_intraday`). Assim o incremento de NAV da adoção (recon) e o
    # incremento de contribuição (intraday) entram JUNTOS na etapa amDiff e se cancelam.
    mov_contrib = round(total_contribution - adoption_intraday, 2)
    sim_nav_mov = round(base_rows_balance + new_cash + prov_total + official_prov_in_nav, 2)
    gap_mov = _gap_cash_for(sim_nav_mov, mov_contrib)
    gap_amdiff = (round(gap_cash - gap_mov, 2)
                  if (gap_cash is not None and gap_mov is not None) else None)
    # caixa PROJETADO − caixa OFICIAL: reusa o resíduo já computado em `_diagnose`.
    gap_stages = {"mov": _safe_num(gap_mov), "amDiff": _safe_num(gap_amdiff),
                  "cash": _safe_num(cash_residual)}
    return {"sim_nav": sim_nav, "sim_nps": sim_nps, "ret_nps": ret_nps,
            "ret_contrib": ret_contrib, "gap_pct": gap_pct, "gap_cash": gap_cash,
            "gap_stages": gap_stages}


# ── Motor de projeção ────────────────────────────────────────────────────────

def _build_movement_for_wallet(company_id, wallet_id, source_date, target_date,
                               use_cache=False,
                               shared_price_records=None, shared_events=None):
    """Projeta a posição da carteira de `source_date` para `target_date`.

    ORQUESTRADOR: concentra TODO o I/O (unprocessed, txns, preços, eventos,
    envelopes, navPackages) e delega os cálculos aos ESTÁGIOS PUROS, na ordem:
      Insumos I/O → B1 `_aggregate_window_txns` + `_project_rows` (projeção)
      → B3 caixa projetado (inline, 3 linhas) → B4 `_diagnose` (diagnóstico)
      → B2 `_adopt_target_qty` (adoção amountDifference, NAV-neutra)
      → B5 `_official_prov_in_nav_total` + `_compute_nav_and_gaps` (NAV/GAPs).
    (B* = blocos da "Memória de cálculo" da UI; a ordem de CÁLCULO difere da de
    exibição porque o B4 precisa do caixa e o B5 precisa da adoção do B2.)

    Retorna um dict com `rows[]`, `cash`, NAV/nps/GAP simulados, `provisions[]` e
    `diff` (comparação com a unprocessed real do alvo). Erros de escopo são
    sinalizados em `error`."""
    wallet = resolve_wallet(wallet_id, {"entityId": 1, "currencyId": 1, "name": 1, "companyId": 1},
                            company_id=company_id)
    if not wallet:
        return {"walletId": wallet_id, "error": "Carteira não encontrada."}
    company_id = company_id or str(wallet.get("companyId") or "")
    entity_id = str(wallet.get("entityId") or "")
    currency_id = str(wallet.get("currencyId") or "BRL")
    wallet_name = wallet.get("name") or wallet_id

    # ── Insumos I/O (Passo 0): base = unprocessed da ORIGEM (agregada por sid) ────
    src_doc = _cget(use_cache, ("unproc", company_id, wallet_id, source_date),
                    lambda: beehus_catalog.unprocessed_doc(wallet_id, source_date, company_id))
    if not src_doc:
        return {"walletId": wallet_id, "walletName": wallet_name,
                "error": "Sem posição não processada na data origem."}
    # unprocessed do ALVO — usada no preço/C3, no diff E p/ enriquecer a ORIGEM:
    # quando o pré-processamento da origem vem sem securityId resolvido, reaproveita
    # a resolução do alvo (mesmo unprocessedId) p/ os ativos não caírem em
    # 'não mapeado' (mostrando só o unprocessedId).
    tgt_doc = _cget(use_cache, ("unproc", company_id, wallet_id, target_date),
                    lambda: beehus_catalog.unprocessed_doc(wallet_id, target_date, company_id))
    # Enriquecimento BIDIRECIONAL origem↔alvo: o `preProcessingData` de UMA das datas pode
    # vir não resolvido (securityId nulo em TODOS os ativos — importado mas não pré-processado).
    # Como o `unprocessedId` é estável por ativo entre datas, a data RESOLVIDA doa a resolução
    # à não resolvida. Sem o lado ALVO deste enriquecimento, um alvo não pré-processado fazia o
    # diff apontar TODA a carteira como onlyCalc/onlyReal (revisão manual em massa) — bug
    # recorrente. Ver `_enrich_secs` e o guard `targetUnresolved` adiante.
    _uid_pp = _uid_to_pp(src_doc, tgt_doc)
    src_secs, _ = _enrich_secs(src_doc.get("securities", []) or [], _uid_pp)
    if tgt_doc:
        _tgt_secs, _n_tgt = _enrich_secs(tgt_doc.get("securities", []) or [], _uid_pp)
        if _n_tgt:
            tgt_doc = {**tgt_doc, "securities": _tgt_secs}
    src_rows = _aggregate_positions(src_secs)
    # Quebra por lote (unprocessedId) da ORIGEM — p/ o upload preservar linhas separadas
    # quando >1 unprocessedId cai no mesmo securityId (ex.: dois lotes do mesmo fundo).
    src_lots_by_sid = _lots_by_sid(src_secs)
    name_hints = _position_name_hints({"securities": src_secs})

    # ── Insumos I/O (Passo 1): txns, preços, eventos, exec-prices, envelopes ──────
    # Transações na janela (origem, alvo] por liquidationDate.
    _raw_txns = _cget(use_cache, ("txns", company_id, wallet_id, source_date, target_date),
                      lambda: beehus_catalog.transactions_search(
                          company_id, initial_date=source_date, final_date=target_date,
                          wallet_ids=[wallet_id]))
    txns = [t for t in _raw_txns
            if not t.get("trashed") and str(t.get("liquidationDate") or "")[:10] > source_date]

    sids = {r["securityId"] for r in src_rows.values() if r["securityId"]}
    sids |= {str(t.get("securityId") or "") for t in txns if t.get("securityId")}
    sids.discard("")
    sec_meta = beehus_catalog.securities_by_ids(list(sids)) if sids else {}

    # unprocessed do ALVO já buscada no Passo 0 (reuso) — pricingType / C3 e diff.
    tgt_pricing = _pricing_from_unproc(tgt_doc)
    tgt_rows = _aggregate_positions((tgt_doc or {}).get("securities", []) or []) if tgt_doc else {}
    tgt_lots_by_sid = _lots_by_sid((tgt_doc or {}).get("securities", []) or []) if tgt_doc else {}
    # PU por securityId derivado das próprias linhas agregadas do alvo (sem reagregar o doc).
    tgt_pu = {r["securityId"]: r.get("pu") for r in tgt_rows.values() if r.get("securityId")}
    # Nomes de ativos NOVOS (só transacionados na janela) vêm da unprocessed-alvo
    # ou do beehusName da própria transação — a fonte source não os conhece.
    for _sid, _nm in _position_name_hints(tgt_doc).items():
        name_hints.setdefault(_sid, _nm)
    for _t in txns:
        _sid, _nm = str(_t.get("securityId") or ""), _t.get("securityBeehusName")
        if _sid and _nm:
            name_hints.setdefault(_sid, _nm)

    # Preços resolvidos por escopo (C3→C2→C1→B2→B1) e eventos do dia-alvo. Em projeção
    # de LOTE, `shared_price_records`/`shared_events` (união de todas as carteiras, 1 fetch)
    # são injetados → a resolução é client-side, SEM I/O por carteira (resultado idêntico).
    _sids_key = tuple(sorted(sids))   # chave estável dos caches por conjunto de ativos
    price_hp = _cget(use_cache, ("prices", company_id, wallet_id, _sids_key),
                     lambda: beehus_catalog.security_prices_resolved(
                         list(sids), company_id=company_id, entity_id=entity_id,
                         wallet_id=wallet_id, records=shared_price_records)) if sids else {}
    # `source_date` fica na CHAVE (as txns dependem da janela), mas _events_window não a usa.
    coupon_amort_by_sid, split_by_sid, dividend_by_sid, dividend_events_by_sid = _cget(
        use_cache, ("events", wallet_id, source_date, target_date, _sids_key),
        lambda: _events_window(list(sids), txns, target_date, events=shared_events))
    # Preço de EXECUÇÃO capturado do sistema (endpoint execution-prices), por ativo —
    # SOMENTE da DATA-ALVO. O intraday do dia-alvo só pode usar o execPrice do alvo; um
    # record de OUTRA data (a busca antiga era [origem, alvo] e pegava o positionDate
    # MAIS RECENTE, que podia ser < alvo) entraria na contribuição intraday com preço de
    # outro dia → GAP. Quando ausente ou == PU, é derivado de -Σbalance/amountDifference.
    exec_price_by_sid = _cget(
        use_cache, ("execprices", company_id, wallet_id, target_date),
        lambda: beehus_catalog.execution_prices_by_sid(company_id, wallet_id, target_date, target_date))
    # Envelope processed-position do ALVO e da ORIGEM — UMA leitura por data que serve o
    # PU OFICIAL (autoritativo), a quantidade (amountDifference) E, adiante, o caixa
    # oficial + provisões. Antes eram chamadas SEPARADAS ao MESMO endpoint (processed_doc
    # p/ PU + wallet_cash_and_provisions p/ caixa/provisões → 2 leituras por data); agora
    # 1 por data. Cacheado sob `procenv` p/ reprojetar em lote sem I/O.
    tgt_env = _cget(use_cache, ("procenv", company_id, wallet_id, target_date),
                    lambda: beehus_catalog.processed_envelope(wallet_id, target_date, company_id))
    src_env = _cget(use_cache, ("procenv", company_id, wallet_id, source_date),
                    lambda: beehus_catalog.processed_envelope(wallet_id, source_date, company_id))
    # PU OFICIAL do ALVO: 1ª fonte de PU no `_resolve_pu` — alinha os preços ao oficial (a
    # unprocessed-alvo NÃO carrega o PU de B1/B2/C1/C2, que cairiam no securityPrices e
    # divergiriam levemente do processado). Forward (alvo não processado → env None) → {} →
    # cai na cadeia normal (unprocessed-alvo → securityPrices → repete origem).
    tgt_proc_pu = _pu_by_sid_from_env(tgt_env)
    # PU OFICIAL da ORIGEM → vira o `formerPu` (PU inicial da janela) quando a origem está
    # processada. Alinha a CONTRIBUIÇÃO DIÁRIA (`formerQty×(PU−formerPU)`) ao oficial — a
    # unprocessed-origem traz o PU de B1/etc. levemente diferente. Forward/origem não
    # processada → {} → mantém o formerPu da unprocessed-origem.
    src_proc_pu = _pu_by_sid_from_env(src_env)
    # Quantidade OFICIAL da processed-position do ALVO ({sid: qty}; None em forward, sem
    # processed-alvo). Usada p/ detectar posição OFICIALMENTE FECHADA (qtd 0) que sumiu da
    # unprocessed-alvo sem transação — não se projeta a qtd da origem nesse caso (ver filtro).
    tgt_proc_qty = _qty_by_sid_from_env(tgt_env)

    # SAFETY NET (bug recorrente): a unprocessed-alvo veio importada mas NÃO pré-processada
    # (NENHUM securityId resolvido) e o enriquecimento bidirecional não alcançou (a origem
    # também não resolve esses uids). Sem isto o diff compara contra um alvo VAZIO e aponta
    # TODA a carteira como onlyCalc/onlyReal (revisão em massa). Quando a processed-position
    # do alvo existe, ela é a contraparte OFICIAL (securityId+qtd) → vira a base do diff.
    # `targetSource` registra a procedência p/ a UI sinalizar. (No caso comum o enriquecimento
    # já resolveu a unprocessed-alvo e este ramo NÃO dispara.)
    target_source = "unprocessed"
    if tgt_doc and tgt_rows and not any(r.get("securityId") for r in tgt_rows.values()):
        _env_rows = _rows_from_env(tgt_env)   # só construído quando o alvo está 100% não resolvido
        if _env_rows:
            tgt_rows = _env_rows
            if not tgt_pu:
                tgt_pu = {sid: r["pu"] for sid, r in tgt_rows.items() if r.get("pu")}
            target_source = "processed-fallback"

    # Fundo VENDIDO POR COMPLETO (saiu da unprocessed-alvo) e SEM PU na processed-alvo
    # (pu=0): a única fonte do PU bruto é a COTA do securityPrices na data-alvo. O fetch em
    # LOTE (todos os sids) pode deixá-lo de fora (limite por requisição), então busca esses
    # poucos ativos DIRETO (lista curta) e completa o `price_hp` — assim o `_resolve_pu` cai
    # na cota (passo 2). Não substitui nada já presente (processed/snapshot seguem primeiro).
    _redeemed_funds = [s for s in sids
                       if (sec_meta.get(s, {}).get("securityType") or "") == "brazilianFund"
                       and not _num(tgt_pu.get(s)) and not tgt_proc_pu.get(s) and not price_hp.get(s)]
    if _redeemed_funds:
        # Em lote, resolve dos records já buscados (a união do prefetch é completada por
        # re-fetch de omissões → sem I/O extra aqui). Single-wallet (shared=None) → fetch direto.
        _extra_hp = _cget(use_cache, ("prices1", company_id, wallet_id, tuple(sorted(_redeemed_funds))),
                          lambda: beehus_catalog.security_prices_resolved(
                              _redeemed_funds, company_id=company_id, entity_id=entity_id,
                              wallet_id=wallet_id, records=shared_price_records))
        price_hp = dict(price_hp)   # não muta o objeto cacheado do fetch em lote
        for _s, _hp in (_extra_hp or {}).items():
            if _hp and not price_hp.get(_s):
                price_hp[_s] = _hp

    # ── B1a: transações por securityId + agregados de caixa/fluxo (puro) ─────────
    (txn_by_sid, all_cash, inflows, wallet_contrib,
     redempt_cash_by_sid) = _aggregate_window_txns(txns)

    # ── B1b: linhas projetadas + provisões derivadas (puro) — Passos 2–4, 6, 6.5 ──
    (rows, provisions, exec_prices_view, total_contribution,
     matured_cash) = _project_rows(
        src_rows=src_rows, txn_by_sid=txn_by_sid, redempt_cash_by_sid=redempt_cash_by_sid,
        coupon_amort_by_sid=coupon_amort_by_sid, split_by_sid=split_by_sid,
        dividend_by_sid=dividend_by_sid, dividend_events_by_sid=dividend_events_by_sid,
        price_hp=price_hp, exec_price_by_sid=exec_price_by_sid,
        tgt_pricing=tgt_pricing, tgt_pu=tgt_pu, tgt_proc_pu=tgt_proc_pu,
        tgt_proc_qty=tgt_proc_qty, tgt_rows=tgt_rows,
        src_proc_pu=src_proc_pu, sec_meta=sec_meta, name_hints=name_hints,
        src_lots_by_sid=src_lots_by_sid, tgt_lots_by_sid=tgt_lots_by_sid,
        target_date=target_date)

    # Contribuição em NÍVEL DE CARTEIRA (gainsExpenses/rebate): P&L que mexe o caixa
    # mas não é de um ativo específico → entra no returnContribution p/ acompanhar o
    # returnNavPerShare (senão sobra um GAP simulado falso do tamanho desse valor).
    total_contribution += wallet_contrib

    # ── B3: caixa projetado = caixa-origem + Σ transações + resíduo de vencimento ─
    # `src_provs` = provisões ATIVAS no envelope da ORIGEM — usadas p/ achar as que
    # LIQUIDAM na data-alvo (no envelope do alvo elas já saíram). Caixa + provisões da
    # ORIGEM vêm do `src_env` JÁ buscado (mesma resposta processed-position do PU) —
    # SEM 2ª chamada ao endpoint (era um `wallet_cash_and_provisions` separado).
    cash_by_date, src_provs = beehus_catalog.cash_and_provisions_from_envelope(
        src_env, [source_date], wallet_id)
    former_cash = cash_by_date.get(source_date)
    fc = _num(former_cash) if former_cash is not None else 0.0
    new_cash = round(fc + all_cash + matured_cash, 2)

    prov_total = round(sum(_num(p["balance"]) for p in provisions), 2)

    # ── Insumo I/O p/ B5: navPackage da ORIGEM (NAV/cota/cotas oficiais) ──────────
    # Base do retorno; ausente (forward sem navPackage) → cota/retornos/GAP ficam None.
    # O CÁLCULO de NAV/cota/GAP fica ADIANTE (B5) — depois do `_diagnose` (B4), porque a
    # posição projetada pode ADOTAR a qtd-alvo (B2) antes de somar o NAV.
    src_nav_pkg = _cget(use_cache, ("nav", company_id, wallet_id, source_date),
                        lambda: beehus_catalog.nav_doc_for_entity_date(wallet_id, source_date, company_id)) or {}
    former_nav = _num(src_nav_pkg.get("nav"), None) if src_nav_pkg.get("nav") is not None else None
    former_nps = _num(src_nav_pkg.get("navPerShare"), None) if src_nav_pkg.get("navPerShare") is not None else None
    shares = _num(src_nav_pkg.get("amount"), None) if src_nav_pkg.get("amount") is not None else None

    # ── Insumo I/O p/ B3/B4: caixa oficial no ALVO (cashAccounts) ─────────────────
    # Usado só p/ o RESÍDUO de caixa INFORMATIVO (projetado − oficial) exibido no detalhe —
    # NÃO classifica mais a divergência (caixa-âncora eliminado: caixa de carteira não isola
    # por ativo com várias transações/provisões simultâneas). Caixa + provisões do ALVO vêm
    # do `tgt_env` JÁ buscado (mesma resposta processed-position do PU) — sem 2ª chamada.
    cash_tgt_by, off_provs = beehus_catalog.cash_and_provisions_from_envelope(
        tgt_env, [target_date], wallet_id)
    official_cash = cash_tgt_by.get(target_date)

    # Meta (securityType) das securities das provisões — p/ marcar stockETF. As sids
    # das provisões podem não estar no sec_meta (ativo vendido/sai da posição), então
    # buscamos as faltantes (seam cacheado). É I/O → fica AQUI (fetch), entregue pronto
    # ao diagnóstico puro.
    _prov_sids = {beehus_catalog.id_str(p.get("securityId"))
                  for p in ((off_provs or []) + (src_provs or [])) if p.get("securityId")}
    _missing = [s for s in _prov_sids if s and s not in sec_meta]
    _prov_meta = beehus_catalog.securities_by_ids(_missing) if _missing else {}

    # Threshold de saldo configurável (config — fora do diagnóstico puro); 0 = só piso.
    balance_tol_pct = _diff_threshold_decimal()
    # NAV/cota/GAP reais do alvo (navPackage) — PRÉ-BUSCADO aqui (cacheado p/ reprojeção)
    # e entregue pronto ao diagnóstico (que assim não faz I/O).
    tgt_nav = _cget(use_cache, ("nav", company_id, wallet_id, target_date),
                    lambda: beehus_catalog.nav_doc_for_entity_date(wallet_id, target_date, company_id)) or {}

    # ── B4: diagnóstico PURO — IRRF, caixa-âncora, provisões oficiais/liquidando,
    # liquidação stockETF/B3, diff vs unprocessed-alvo, correções (recon) e visão das
    # transações. Todos os insumos chegam JÁ buscados → sem I/O, testável isolado.
    diag = _diagnose(
        rows=rows, provisions=provisions, txns=txns, txn_by_sid=txn_by_sid,
        src_rows=src_rows, tgt_rows=tgt_rows, tgt_doc=tgt_doc, tgt_pu=tgt_pu,
        sec_meta=sec_meta, prov_meta=_prov_meta, name_hints=name_hints,
        new_cash=new_cash, official_cash=official_cash, off_provs=off_provs,
        src_provs=src_provs, tgt_nav=tgt_nav, balance_tol_pct=balance_tol_pct,
        exec_price_by_sid=exec_price_by_sid, target_date=target_date)

    # ── B2: adoção da qtd-alvo (amountDifference) — muta rows/diff ────────────────
    base_rows_balance, recon_prov_in_nav, adoption_intraday = _adopt_target_qty(rows, diag)
    # P&L INTRADAY das adoções (cota ao PU × provisão ao executionPrice) → entra no
    # returnContribution p/ PAREAR o mesmo valor somado ao sim_nav (recon) → a adoção fica
    # NAV-neutra NO GAP (gap_amdiff ≈ 0). No fallback pu-alvo (execPrice=PU) é 0.
    total_contribution = round(total_contribution + adoption_intraday, 2)

    # ── B5-pre: correção SUGERIDA da liquidação stockETF (Opção 1) na CONTRIBUIÇÃO ──
    # O resíduo AINDA NÃO lançado (custo de execução: Σprov stockETF − liq. B3, líquido do
    # que já foi lançado) entra no returnContribution — a transação sugerida é um
    # `contributionAdjustment` no ativo "Liquidação B3" (cash-neutro). Assim o GAP já
    # reflete a correção no PREVIEW, antes de o usuário aprovar (botão no bloco de transações).
    # Sinal: a transação a criar tem balance = −resíduo → soma −resíduo à contribuição.
    # Idempotente: depois de criada, o resíduo zera (b3AdjustTotal) → nada extra é somado, e
    # a transação real entra na contribuição pela via normal (_CASH_EXCLUDED_TYPES).
    _se_diag = diag.get("stockEtfLiquidation") or {}
    suggested_b3_contrib = round(-_num(_se_diag.get("residual")), 2)
    if suggested_b3_contrib:
        wallet_contrib = round(wallet_contrib + suggested_b3_contrib, 2)
        total_contribution = round(total_contribution + suggested_b3_contrib, 2)

    # Detalhamento do walletContribution POR TIPO — p/ a "Memória de cálculo" detalhar
    # o somatório das contribuições (QUAIS itens de nível-carteira entraram no
    # returnContribution: taxas managementFee/otherFee/brokerageFee, rebate,
    # gainsExpenses, ajustes). Espelha EXATAMENTE o que `_aggregate_window_txns` somou em
    # `wallet_contrib` (tipos em _WALLET_CONTRIB_TYPES ∪ _CASH_EXCLUDED_TYPES) + o ajuste
    # sugerido da liquidação stockETF (b3), quando houver → Σ desta lista == walletContribution.
    _wc_by_type = {}
    for _t in txns:
        _typ = _t.get("beehusTransactionType") or ""
        if _typ in _WALLET_CONTRIB_TYPES or _typ in _CASH_EXCLUDED_TYPES:
            _wc_by_type[_typ] = round(_wc_by_type.get(_typ, 0.0) + _num(_t.get("balance")), 2)
    if suggested_b3_contrib:
        _wc_by_type["stockEtfLiquidation"] = round(
            _wc_by_type.get("stockEtfLiquidation", 0.0) + suggested_b3_contrib, 2)
    wallet_contribution_breakdown = [{"type": k, "balance": v}
                                     for k, v in _wc_by_type.items() if abs(v) >= 0.005]

    # ── B5a: provisões oficiais pendentes que entram no NAV (puro) ────────────────
    official_prov_in_nav = _official_prov_in_nav_total(
        provisions, off_provs, diag["diff"].get("diverged", []))

    # ── B5b: NAV / cota / GAPs simulados (puro) ───────────────────────────────────
    _nav = _compute_nav_and_gaps(
        rows=rows, new_cash=new_cash, prov_total=prov_total,
        recon_prov_in_nav=recon_prov_in_nav, official_prov_in_nav=official_prov_in_nav,
        inflows=inflows, total_contribution=total_contribution,
        former_nav=former_nav, former_nps=former_nps, shares=shares,
        base_rows_balance=base_rows_balance, adoption_intraday=adoption_intraday,
        cash_residual=diag["diff"].get("cashResidual"))
    sim_nav, sim_nps = _nav["sim_nav"], _nav["sim_nps"]
    ret_nps, ret_contrib = _nav["ret_nps"], _nav["ret_contrib"]
    gap_pct, gap_cash, gap_stages = _nav["gap_pct"], _nav["gap_cash"], _nav["gap_stages"]

    return _sanitize({
        "walletId": wallet_id, "walletName": wallet_name,
        "entityId": entity_id,   # p/ as rotas de escrita (transação de carteira exige entityId)
        "sourceDate": source_date, "targetDate": target_date,
        "currencyId": currency_id,
        "rows": rows, "transactions": diag["transactions"],
        "cash": {"former": _safe_num(former_cash), "delta": round(all_cash, 2),
                 "maturedDelta": round(matured_cash, 2), "new": new_cash},
        "inflows": round(inflows, 2),
        "walletContribution": _safe_num(round(wallet_contrib, 2)),
        "walletContributionBreakdown": wallet_contribution_breakdown,
        "adoptionIntradayContribution": _safe_num(adoption_intraday),
        "provisions": provisions, "provisionsTotal": prov_total,
        "officialProvisions": diag["officialProvisions"],
        "officialProvisionsInNav": _safe_num(official_prov_in_nav),
        "liquidatingProvisions": diag["liquidatingProvisions"],
        "stockEtfLiquidation": diag["stockEtfLiquidation"],
        "reconProvisions": diag["reconProvisions"], "reconProvisionsTotal": diag["reconProvisionsTotal"],
        "reconTransactions": diag["reconTransactions"], "reconTransactionsTotal": diag["reconTransactionsTotal"],
        "irrf": diag["irrf"], "irrfMissingTotal": diag["irrfMissingTotal"],
        "executionPrices": exec_prices_view,
        "executionPriceFixes": [e for e in exec_prices_view if e.get("isFix")],
        "formerNav": _safe_num(former_nav), "formerNavPerShare": _safe_num(former_nps),
        "shares": _safe_num(shares),
        "simNav": _safe_num(sim_nav), "simNavPerShare": _safe_num(sim_nps),
        "simReturnNavPerShare": _safe_num(ret_nps), "simReturnContribution": _safe_num(ret_contrib),
        "simGapPct": _safe_num(gap_pct), "simGapCash": _safe_num(gap_cash),
        "gapStages": gap_stages,
        "diff": diag["diff"],
        "targetSource": target_source,
    })


def _exec_price(txns):
    """Preço de execução do ativo a partir das transações (campo `executionPrice`
    ou `price`). None se nenhuma transação trouxer preço."""
    for t in txns or []:
        ep = t.get("executionPrice")
        if ep not in (None, "", 0):
            return _num(ep)
    for t in txns or []:
        p = t.get("price")
        if p not in (None, "", 0):
            return _num(p)
    return None


def _settlement_offset(txn):
    """Offset de liquidação (settlementDays − navDays) capturado do
    `securitySecInfo` da transação (casing correto `*NAVDays`). 0 quando ausente.
    Compra (balance<0) usa subscrição; venda usa resgate."""
    info = txn.get("securitySecInfo") or {}
    bal = _num(txn.get("balance"))
    if bal < 0:
        settle = info.get("subscriptionSettlementDays") or 0
        nav = info.get("subscriptionNAVDays") or 0
    else:
        settle = info.get("redemptionSettlementDays") or 0
        nav = info.get("redemptionNAVDays") or 0
    try:
        return int(settle) - int(nav)
    except (TypeError, ValueError):
        return 0


def _txn_nav_date(txn):
    """Data da COTA/posição do trade = `operationDate` + navDays (dias ÚTEIS). É quando as
    cotas passam a EXISTIR na posição — pode ser POSTERIOR ao `liquidationDate` (settlement
    do caixa). Compra (balance<0) usa `subscriptionNAVDays`; venda usa `redemptionNAVDays`.
    SEM operationDate → devolve "" (o caller trata como POSITION-EFFECTIVE, navDate ≤ alvo):
    não usa o `liquidationDate` como proxy porque ele é o SETTLEMENT (pode ser futuro sem que
    a cota seja futura — ex.: compra à vista com settlementDays>0 e navDays=0)."""
    info = txn.get("securitySecInfo") or {}
    nav = (info.get("subscriptionNAVDays") if _num(txn.get("balance")) < 0
           else info.get("redemptionNAVDays"))
    try:
        nav = int(nav or 0)
    except (TypeError, ValueError):
        nav = 0
    d = str(txn.get("operationDate") or "")[:10]
    if not d:
        return ""
    for _ in range(max(0, nav)):
        d = _next_biz_day(d)
    return d


def _resolve_pu(pricing_type, tgt_pu, history_price, target_date, former_pu, split_factor,
                proc_pu=None):
    """Resolve o PU da data-alvo. Retorna `(pu, pricing_used)`.

    FONTE PRIMÁRIA: o **PU OFICIAL da processed-position do ALVO** (`proc_pu`), quando o
    alvo já está processado — é o preço autoritativo (a unprocessed-alvo NÃO carrega o PU
    de B1/B2/C1/C2). No cenário **forward** (alvo não processado) `proc_pu` vem vazio e o
    sistema segue para as alternativas, nesta ordem:
      1. PU da **unprocessed da data-alvo** (snapshot — cobre o C3);
      2. pricingType B1/B2/C1/C2 → securityPrices (historyPrice na data-alvo);
      3. repetir o PU da origem (ajustado pelo fator de split/inplit, p/ preservar o saldo)."""
    pt = (pricing_type or "").upper()
    # 0) MAIS AUTORITATIVO — PU da processed-position do ALVO. Vazio no forward → cai abaixo.
    pp = _num(proc_pu)
    if pp:
        return pp, (pt + "·proc" if pt else "PROC")
    # 1) PU da unprocessed do alvo. "Não existe" = ausente OU zero/None.
    tp = _num(tgt_pu)
    if tp:
        return tp, (pt + "·alvo" if pt else "ALVO")
    # 2) securityPrices (historyPrice na data-alvo). É o primário p/ B1/B2/C1/C2 E o
    #    fallback quando NÃO HÁ processed-position nem snapshot — ex.: ativo VENDIDO POR
    #    COMPLETO (fundo totalmente resgatado): saiu da unprocessed-alvo e a processed-alvo
    #    tem PU 0, então a COTA da data-alvo (securityPrices) é a única fonte do PU bruto.
    #    Vem DEPOIS da processed-position (tier 0): regra "processed primeiro; securityPrices
    #    só sem processed". Casamento ESTRITO por data → só dispara com cota EXATA na data.
    pu = _history_pu_on_date(history_price, target_date)
    if pu is not None:
        # Rótulo EXPLÍCITO p/ o operador ver a procedência do PU: "<pricing> · securityPrices
        # dd/mm" (ex.: "B1 · securityPrices 30/06"). Deixa claro que o PU veio da COTA da
        # curva capturada NA data-alvo — não é repetição da origem nem oficial do processado.
        _dm = (f"{target_date[8:10]}/{target_date[5:7]}" if len(str(target_date)) >= 10
               else str(target_date))
        return _num(pu), (f"{pt} · securityPrices {_dm}" if pt else f"securityPrices {_dm}")
    # 3) Fallback: repetir PU da origem. Split/inplit: PU inverso ao fator de qtd.
    # Rótulo "<pricing> · repetido origem" (ex.: "B1 · repetido origem") — o securityPrices
    # NÃO tinha cota na data-alvo, então o PU foi repetido da origem (fallback do B1).
    pu = _num(former_pu)
    if split_factor:
        pu = pu / split_factor if split_factor else pu
    return pu, (f"{pt} · repetido origem" if pt else "REPETIDO")


def _events_window(security_ids, txns, target_date, events=None):
    """`(coupon_amort_by_sid, split_factor_by_sid, dividend_ps_by_sid, dividend_events_by_sid)`.

    coupon/amortização vêm das TRANSAÇÕES da janela (lado caixa). split/inplit e
    dividendos vêm de securityEvents com `operationDate == target_date`.
    `dividend_events_by_sid` = `{sid: [{operationDate, liquidationDate, balance,
    eventType}]}` guarda os eventos de dividendo/JCP CRUS (com datas) para o Passo
    6.5 gerar a provisão de recebível (qtd-origem × dividendo-por-cota).

    `events`: lista de securityEvents JÁ buscada (ex.: união de várias carteiras numa
    projeção em lote) — quando fornecida, NÃO chama a API; processa só os eventos cujo
    `securityId` está em `security_ids` (escopo da carteira), idêntico ao fetch
    por-carteira (que já vinha filtrado pelos ids do chunk)."""
    coupon_amort = {}
    for t in txns or []:
        if (t.get("beehusTransactionType") or "") in _EVENT_TYPES:
            sid = str(t.get("securityId") or "")
            if sid:
                coupon_amort[sid] = coupon_amort.get(sid, 0.0) + _num(t.get("balance"))
    split_factor, dividend_ps, dividend_events = {}, {}, {}
    sids = list(security_ids or [])
    _want = set(sids)
    # INJETADO (lote): processa a união já buscada, escopada aos sids da carteira.
    # Caso normal: chunk + fetch por chunk (cada resposta já vem só com os ids pedidos).
    chunks = ([events] if events is not None
              else None)
    if chunks is None:
        chunks = []
        for i in range(0, len(sids), 150):   # chunk p/ evitar 414 (csv de securities)
            chunk = sids[i:i + 150]
            try:
                chunks.append(_api_security_events(security_ids=chunk) or [])
            except (BeehusAPIError, BeehusAuthError, Exception):  # noqa: BLE001
                chunks.append([])
    for evts in chunks:
        for e in (evts or []):
            if not isinstance(e, dict):
                continue
            if str(e.get("operationDate") or "")[:10] != target_date:
                continue
            sid = beehus_catalog.id_str(e.get("securityId"))
            if events is not None and sid not in _want:
                continue   # escopa a união injetada aos ativos desta carteira
            et = e.get("eventType")
            if et in _SPLIT_EVENT_TYPES and e.get("factor"):
                split_factor[sid] = _num(e.get("factor"), 1.0) or 1.0
            elif et in _DIVIDEND_EVENT_TYPES:
                dividend_ps[sid] = dividend_ps.get(sid, 0.0) + _num(e.get("balance"))
                dividend_events.setdefault(sid, []).append({
                    "operationDate": str(e.get("operationDate") or "")[:10],
                    "liquidationDate": str(e.get("liquidationDate") or "")[:10],
                    "balance": _num(e.get("balance")),    # dividendo POR COTA
                    "eventType": et,
                })
    return coupon_amort, split_factor, dividend_ps, dividend_events


def _balance_similar(a, b, ratio=_LIQ_MATCH_RATIO):
    """`a` e `b` são "semelhantes": MESMO sinal e min/max ≥ ratio (90% → ±10%).
    Zero só casa zero (evita 0 casar com qualquer valor por divisão degenerada)."""
    a, b = _num(a), _num(b)
    if a == 0.0 or b == 0.0:
        return a == b
    if (a > 0.0) != (b > 0.0):
        return False   # sinais opostos nunca casam (item 1: mesmo sinal)
    return (min(abs(a), abs(b)) / max(abs(a), abs(b))) >= ratio


def _match_liquidating_provisions(liq_provs, txns, etf_liq_total=0.0, b3_balance=0.0):
    """Casa cada provisão que LIQUIDA na data-alvo com uma **liquidação de caixa** da
    janela (item 1 — ver CONCILIACAO_MOV.md). Anota EM LUGAR cada entrada de `liq_provs`:
      • `matchStatus`  = "green" | "yellow" | "red"
      • `matchTxnId` / `matchBalance` / `matchBecause`

    Regra: candidata = transação de **mesmo sinal** e **valor semelhante** (`_balance_similar`,
    ≥90%). QUALQUER `beehusTransactionType` conta.
      • 🟢 green  = candidata com `securityId` **igual** ao da provisão (provisões de
                    buySell/dividend/interestOnEquity carregam securityId);
      • 🟡 yellow = candidata só por VALOR (securityId difere, ou a provisão afeta a
                    wallet sem ativo → o balance é só indício de liquidação);
      • 🔴 red    = nenhuma candidata de mesmo sinal dentro dos 90%.
    Casamento **1:1 guloso** (melhor proximidade primeiro): a transação casada é consumida
    e não reaproveitada por outra provisão. As transações contra o ativo genérico
    "Liquidação B3" ficam FORA do 1:1 — as provisões **stockETF** liquidam de forma
    CONSOLIDADA (Σ provisões × Σ buySell B3); se a soma casa (≥90%), as stockETF que
    ficaram vermelhas viram amarelas ("liquidação consolidada B3"). Futures fora de escopo."""
    # Pool p/ 1:1 = transações da janela EXCETO as consolidadas da B3 (tratadas no Passo 3).
    pool = [t for t in txns
            if beehus_catalog.id_str(t.get("securityId")) != _B3_LIQ_SECURITY]
    used = set()   # índices de `pool` já consumidos

    def _best(pbal, same_sid=None):
        """Índice da transação livre mais próxima (por valor) de `pbal`; se `same_sid`,
        exige `securityId` igual. None se nenhuma casar."""
        best_i, best_ratio = None, -1.0
        for i, t in enumerate(pool):
            if i in used:
                continue
            if same_sid is not None and beehus_catalog.id_str(t.get("securityId")) != same_sid:
                continue
            tb = _num(t.get("balance"))
            if not _balance_similar(pbal, tb):
                continue
            ratio = min(abs(pbal), abs(tb)) / max(abs(pbal), abs(tb))
            if ratio > best_ratio:
                best_i, best_ratio = i, ratio
        return best_i

    def _assign(p, i, status, because):
        used.add(i)
        t = pool[i]
        p["matchStatus"] = status
        p["matchTxnId"] = beehus_catalog.id_str(t.get("_id"))
        p["matchBalance"] = _safe_num(round(_num(t.get("balance")), 2))
        p["matchBecause"] = because

    for p in liq_provs:
        p["matchStatus"] = "red"; p["matchTxnId"] = None
        p["matchBalance"] = None; p["matchBecause"] = "Nenhuma liquidação de caixa de valor semelhante na janela."

    # Passo 1 — 🟢 GREEN: provisão COM securityId × transação do MESMO ativo + valor.
    for p in liq_provs:
        sid = beehus_catalog.id_str(p.get("securityId"))
        pbal = _num(p.get("balance"))
        if not (sid and pbal):
            continue
        i = _best(pbal, same_sid=sid)
        if i is not None:
            _assign(p, i, "green", "Transação do mesmo ativo e valor semelhante (≥90%) — liquidação casada.")

    # Passo 2 — 🟡 YELLOW: restantes × transação por VALOR (qualquer/sem securityId).
    for p in liq_provs:
        if p["matchStatus"] != "red":
            continue
        pbal = _num(p.get("balance"))
        if not pbal:
            continue
        i = _best(pbal)
        if i is not None:
            _assign(p, i, "yellow",
                    "Transação de valor semelhante (≥90%), mas o ativo não bate (ou a "
                    "provisão não tem ativo) — provável liquidação, revisar.")

    # Passo 3 — stockETF vermelhas: resgate CONSOLIDADO da B3 (Σ provisões ≈ Σ buySell B3).
    if _balance_similar(etf_liq_total, b3_balance):
        for p in liq_provs:
            if p.get("isStockEtf") and p["matchStatus"] == "red":
                p["matchStatus"] = "yellow"
                p["matchBecause"] = ("stockETF liquidado de forma CONSOLIDADA na B3 "
                                     "(Σ provisões ≈ Σ buySell B3, ≥90%) — casada no agregado.")


def _diagnose(rows, provisions, txns, txn_by_sid, src_rows, tgt_rows, tgt_doc,
              tgt_pu, sec_meta, prov_meta, name_hints, new_cash, official_cash,
              off_provs, src_provs, tgt_nav, balance_tol_pct, target_date,
              exec_price_by_sid=None):
    """Diagnóstico PURO (sem I/O) da Conciliação (mov.) — extraído de
    `_build_movement_for_wallet`.

    Recebe os insumos JÁ buscados (resultado da projeção + leituras da API) e devolve
    o bloco de diagnóstico: IRRF de resgate de fundo, resíduo de caixa INFORMATIVO
    (`cashResidual`, sem caixa-âncora), provisões oficiais/liquidando, liquidação
    stockETF/B3, `diff` vs unprocessed-alvo, correções (`reconProvisions` p/ divergência
    de qtd / `reconTransactions` só p/ vencimento) e a visão das transações da janela.

    NÃO faz I/O nem cache: `tgt_nav`, `prov_meta` e `balance_tol_pct` chegam prontos
    (buscados pelo chamador), o que torna o diagnóstico testável isoladamente e
    recomputável sem refetch. Anota EM LUGAR a lista `provisions` (`coveredByOfficial`)
    e as divergências do `diff` (`coveredByProvision`) — os mesmos objetos seguem no
    payload."""
    # ── IRRF de resgate de FUNDO (brazilianFund) ────────────────────────────────
    # Fundo não tem executionPrice; o `balance` do resgate é LÍQUIDO (bruto −
    # IRRF), então o resíduo entre o valor BRUTO das cotas e o caixa líquido é o
    # IRRF. amountDifference bruto vem da reconciliação com o alvo real
    # (qty_alvo − qty_origem). IRRF = Σbalance + amountDifference × PU_alvo
    # (negativo = imposto). Se NÃO houver `taxes`/`bzFundTaxes` cobrindo ~esse
    # valor, o IRRF está AUSENTE → o sistema propõe criá-la (transação `taxes`,
    # rota /irrf). O IRRF ausente NÃO é somado ao `sim_nav` (é write-only; explica
    # parte do resíduo do GAP mov). Um IRRF já lançado já está no caixa (all_cash).
    existing_tax = {}
    for t in txns:
        if (t.get("beehusTransactionType") or "") in ("taxes", "bzFundTaxes"):
            s = str(t.get("securityId") or "")
            if s:
                existing_tax.setdefault(s, []).append(_num(t.get("balance")))
    irrf_entries = []
    irrf_missing_total = 0.0
    rows_by_sid = {r.get("securityId"): r for r in rows if r.get("securityId")}
    for sid, ts in txn_by_sid.items():
        if (sec_meta.get(sid, {}).get("securityType") or "") != "brazilianFund":
            continue
        bs = [t for t in ts if (t.get("beehusTransactionType") or "") in _QTY_TXN_TYPES]
        if not bs:
            continue
        # PU BRUTO da cota na data-alvo + qtd-alvo, conforme o tipo de resgate:
        #   • PARCIAL (fundo SEGUE na unprocessed-alvo): cota do snapshot-alvo, qtd-alvo real.
        #   • TOTAL (fundo SAIU do alvo): cota = PU RESOLVIDO da linha (securityPrices na
        #     data-alvo, ver Passo 4), qtd-alvo = 0. Sem este ramo o resgate total caía no
        #     fluxo de execPrice (que fundo não tem) em vez de virar IRRF.
        if sid in tgt_rows:
            pu = tgt_pu.get(sid)
            if pu is None:
                continue   # precisa da cota bruta do alvo
            tgt_qty = _num(tgt_rows[sid].get("quantity"))
        else:
            pu = _num((rows_by_sid.get(sid) or {}).get("pu")) or None
            if pu is None:
                continue
            tgt_qty = 0.0
        bal_sum = sum(_num(t.get("balance")) for t in bs)
        if bal_sum <= 0:
            continue   # IRRF só em RESGATE (caixa ENTRA); aplicação não é tributada
        amt_diff = tgt_qty - _num(src_rows.get(sid, {}).get("quantity") or 0.0)
        irrf = round(bal_sum + amt_diff * _num(pu), 2)
        # IRRF é SEMPRE um débito (< 0). Valor ≥ 0 é SEM SENTIDO — tipicamente artefato
        # de VÁRIOS resgates com PUs diferentes na janela agregados a um PU único. NÃO
        # propor (senão a rota /irrf criaria um `taxes` POSITIVO = "restituição" sobre
        # parcelas de IRRF já corretas).
        if irrf >= -0.01:
            continue
        # Janela com mais de uma DATA de resgate → IRRF agregado (PU único) é pouco
        # confiável: mantém visível, mas marca p/ revisão (alerta antes de criar).
        multi_event = len({str(t.get("liquidationDate") or "")[:10] for t in bs}) > 1
        # Coberto = a SOMA dos taxes/bzFundTaxes já lançados p/ o ativo bate o IRRF
        # calculado dentro de uma banda ESTREITA. (Antes: 10% e comparado por-transação
        # — discrepância grande passava "coberta" e IRRF em 2 parcelas era duplicado.)
        # Limitação conhecida: janela com VÁRIOS resgates em datas/PUs diferentes ainda
        # usa um IRRF agregado (não por-evento).
        tax_list = existing_tax.get(sid, [])
        tax_sum = round(sum(tax_list), 2)
        covered = bool(tax_list) and abs(tax_sum - irrf) <= max(_IRRF_ABS_TOL, abs(irrf) * _IRRF_REL_TOL)
        if covered:
            because = "IRRF já coberto pelos taxes lançados (dentro da tolerância)."
        elif tax_list:
            because = "Há taxes lançados, mas a soma diverge do IRRF calculado — revisar/ajustar."
        else:
            because = "Nenhum taxes lançado p/ o ativo — IRRF ausente, propor criação."
        if multi_event:
            because = ("⚠ Múltiplos resgates na janela (datas/PUs diferentes) — IRRF agregado "
                       "pouco confiável; revisar antes de criar. ") + because
        # Data do IRRF = a do RESGATE (a transação de buySell), não a do alvo —
        # senão o imposto cairia na data errada numa janela longa.
        red_liq = max((str(t.get("liquidationDate") or "")[:10] for t in bs), default=target_date) or target_date
        red_op = next((str(t.get("operationDate") or "")[:10] for t in bs
                       if str(t.get("liquidationDate") or "")[:10] == red_liq and t.get("operationDate")), red_liq)
        irrf_entries.append({
            "securityId": sid, "securityName": name_hints.get(sid, sid),
            "amountDifference": _safe_num(round(amt_diff, 6)), "pu": _safe_num(round(_num(pu), 6)),
            "netBalance": _safe_num(round(bal_sum, 2)), "irrf": _safe_num(irrf),
            "covered": covered, "taxSum": _safe_num(tax_sum), "because": because,
            "multiEvent": multi_event,
            "operationDate": red_op, "liquidationDate": red_liq,
            "description": f"IRRF resgate de fundo — {name_hints.get(sid, sid)}",
        })
        if not covered:
            irrf_missing_total += irrf
    irrf_missing_total = round(irrf_missing_total, 2)

    # Guardrail anti-duplicação das provisões de DIVIDENDO/JCP (Passo 6.5): se o
    # sistema origem JÁ tem uma provisão OFICIAL de dividend/interestOnEquity para o
    # mesmo ativo (envelope do alvo), a nossa NÃO deve ser enviada (duplicaria) — fica
    # SÓ no NAV simulado (diagnóstico, fecha o GAP do dia-ex) e é marcada
    # `coveredByOfficial`, que o envio (/provisions) pula e a UI pode exibir.
    _off_div_sids = {beehus_catalog.id_str(p.get("securityId"))
                     for p in (off_provs or [])
                     if p.get("securityId") and (p.get("provisionType") or "") in _DIVIDEND_PROV_TYPES}
    for _p in provisions:
        if (_p.get("provisionType") or "") in _DIVIDEND_PROV_TYPES and _p.get("securityId") in _off_div_sids:
            _p["coveredByOfficial"] = True

    # Resíduo de caixa (INFORMATIVO) = caixa PROJETADO − caixa OFICIAL do alvo. Mantido só
    # para EXIBIÇÃO (etapa "caixa" dos GAPs por etapa + linha Caixa da tabela do detalhe);
    # NÃO classifica mais nada. A VERIFICAÇÃO contra o caixa foi ELIMINADA: o caixa é de
    # CARTEIRA e não isola por ativo quando há várias transações/provisões simultâneas.
    # Regra atual (sem caixa-âncora): divergência de QTD → PROVISÃO de ajuste (adota a
    # qtd-alvo, considerando o offset de liquidação), independente do caixa; o VENCIMENTO
    # continua TRANSAÇÃO de resgate. None quando não há caixa oficial no alvo (forward).
    cash_residual = (round(new_cash - _num(official_cash), 2)
                     if official_cash is not None else None)

    # Meta (securityType) das securities das provisões — p/ marcar stockETF. `prov_meta`
    # chega pronto do chamador (fetch das sids faltantes no sec_meta).
    def _is_stocketf(sid):
        return ((sec_meta.get(sid, {}) or prov_meta.get(sid, {})).get("securityType") or "") == "stockEtf"

    def _prov_entry(p):
        sid = beehus_catalog.id_str(p.get("securityId"))
        return {
            # `id` = _id ORIGINAL da provisão (estável entre reprojeções) — a rota de
            # deslocamento (/shift-provisions) casa por ele as provisões selecionadas.
            "id": beehus_catalog.id_str(p.get("_id")),
            "securityId": sid,
            "securityName": name_hints.get(sid, sid or "Provisão"),
            "provisionType": p.get("provisionType") or "",
            "provisionSource": p.get("provisionSource") or "",
            # `description` OFICIAL do sistema (ex.: "TED ... APLICAÇÃO FUNDOS ...") — a UI
            # mostra na coluna Descrição. Sem isto, o front caía no provisionSource
            # ("adjustments") e exibia o rótulo interno em vez do texto real da provisão.
            "description": p.get("description") or "",
            "initialDate": str(p.get("initialDate") or "")[:10],
            "liquidationDate": str(p.get("liquidationDate") or "")[:10],
            "balance": _safe_num(p.get("balance")),
            "isStockEtf": _is_stocketf(sid),
        }

    # Provisões OFICIAIS já existentes no sistema (envelope do ALVO — ativas na data).
    # Servem de contexto + guardrail p/ não duplicar recon.
    official_provisions = [_prov_entry(p) for p in (off_provs or [])]

    # (Item 1) Provisões que LIQUIDAM na data-alvo — vêm do envelope da ORIGEM (no
    # alvo já liquidaram → saem do NAV; o saldo virou caixa). Exibidas em amarelo só
    # como contexto/reconciliação; NÃO entram no NAV simulado.
    liquidating_provisions = [_prov_entry(p) for p in (src_provs or [])
                              if str(p.get("liquidationDate") or "")[:10] == target_date]

    # (Item 2) Resumo stockETF liquidando na data × liquidação CONSOLIDADA da B3:
    # Σ provisões stockETF que liquidam vs Σ buySell da security "Liquidação B3".
    etf_liq_total = round(sum(_num(p["balance"]) for p in liquidating_provisions if p["isStockEtf"]), 2)
    b3_balance = round(sum(_num(t.get("balance")) for t in txns
                           if beehus_catalog.id_str(t.get("securityId")) == _B3_LIQ_SECURITY
                           and (t.get("beehusTransactionType") or "") == "buySell"), 2)
    # Custo de execução JÁ lançado na janela (Σ balance) — usado p/ o RESIDUAL: a Opção 1
    # só precisa criar o que falta. residual = (Σprov − B3) + Σ gainsExpenses + Σ ajustes-B3;
    # ~0 = já resolvido (não oferecer o botão / não duplicar).
    gains_expenses_total = round(sum(_num(t.get("balance")) for t in txns
                                     if (t.get("beehusTransactionType") or "") == "gainsExpenses"), 2)
    # Ajustes de contribuição JÁ lançados NO ATIVO "Liquidação B3" — a Opção 1 cria um
    # `contributionAdjustment` NESSE ativo (versões antigas criavam `securityContributionAdjustment`;
    # ambos contam, daí `in _CASH_EXCLUDED_TYPES`). Filtrar pelo securityId do B3 é o que torna a
    # detecção idempotente SEM conflitar com ajustes de VENCIMENTO (mesmos tipos, em OUTROS ativos):
    # depois de criar o ajuste, ele entra aqui, o resíduo zera e o botão some.
    b3_adjust_total = round(sum(_num(t.get("balance")) for t in txns
                                if (t.get("beehusTransactionType") or "") in _CASH_EXCLUDED_TYPES
                                and beehus_catalog.id_str(t.get("securityId")) == _B3_LIQ_SECURITY), 2)
    diff = round(etf_liq_total - b3_balance, 2)
    stocketf_liquidation = ({
        "provisionsTotal": _safe_num(etf_liq_total),
        "b3Balance": _safe_num(b3_balance),
        "diff": _safe_num(diff),
        "gainsExpensesTotal": _safe_num(gains_expenses_total),
        "b3AdjustTotal": _safe_num(b3_adjust_total),
        "residual": _safe_num(round(diff + gains_expenses_total + b3_adjust_total, 2)),
    } if (any(p["isStockEtf"] for p in liquidating_provisions) or b3_balance) else None)

    # (Item 1) Casa cada provisão que LIQUIDA na data com uma liquidação de caixa da
    # janela (mesmo sinal, valor ≥90%) → matchStatus green/yellow/red em cada entrada.
    # Passa etf/B3 p/ o resgate CONSOLIDADO de stockETF não sair falso-vermelho.
    _match_liquidating_provisions(liquidating_provisions, txns, etf_liq_total, b3_balance)

    # ── Passo 8: diagnóstico/diff vs unprocessed do alvo ────────────────────────
    # `tgt_nav` PRÉ-BUSCADO entregue ao `_build_diff` (mantém o diagnóstico sem I/O).
    diff = _build_diff(rows, tgt_rows, tgt_doc, new_cash, official_cash, name_hints=name_hints,
                       cash_residual=cash_residual,
                       balance_tol_pct=balance_tol_pct, tgt_nav=tgt_nav)

    # ── Passo (3): correções p/ eliminar o GAP de cada divergência de QTD ────────
    # SEM caixa-âncora (verificação contra o caixa ELIMINADA): a divergência de QTD vira
    # sempre PROVISÃO de ajuste (adota a qtd-alvo, NAV-neutro), CONSIDERANDO o offset de
    # liquidação do trade (securitySecInfo) — não mais um +1 fixo nem a decisão "caixa bate".
    # ÚNICA exceção: VENCIMENTO → TRANSAÇÃO de resgate (o alvo ainda carrega o ativo vencido;
    # o principal ENTRA no caixa). Balance da provisão/transação = -executionPrice × Δqty
    # (Δqty = qtd unprocessed do alvo − movimentada; mesma fórmula da Repetir). As
    # reconProvisions o CHAMADOR usa p/ ADOTAR a qtd-alvo na linha + dobrar a provisão no
    # `sim_nav` (NAV-neutro); as reconTransactions (só vencimento) ficam write-only fora do
    # NAV. Aqui (`_diagnose`) só são CONSTRUÍDAS. Guardrails (passo 5): só ativo MAPEADO;
    # pular se já há provisão oficial OU provisão do Passo 6 cobrindo o ativo. A divergência
    # coberta por provisão OFICIAL é marcada `coveredByProvision` — o CHAMADOR também ADOTA a
    # qtd-alvo nela (mesmo caso de liquidação futura), sintetizando o offset NAV-neutro (a
    # provisão oficial não está no sim_nav). Gera recon inclusive no forward (sem caixa
    # oficial): a classificação não depende mais do caixa.
    recon_provisions, recon_txns = [], []
    if diff.get("hasTarget"):
        # Só as provisões de offset (buySell) cobrem uma divergência de QUANTIDADE;
        # as de dividendo/JCP (Passo 6.5) não — não devem suprimir o recon de qtd.
        passo6_sids = {p.get("securityId") for p in provisions
                       if (p.get("provisionType") or "") == "buySell"}
        off_prov_sids = {beehus_catalog.id_str(p.get("securityId"))
                         for p in (off_provs or []) if p.get("securityId")}
        # Provisões buySell que LIQUIDAM na data-alvo (envelope da ORIGEM). Elas NÃO estão em
        # `off_provs` (o envelope do alvo traz só `initialDate <= data < liquidationDate`, e a que
        # liquida NA data tem `liquidationDate == data`), então o guardrail `off_prov_sids` acima
        # nunca as pegava → o recon de ajuste do MESMO ativo nascia ATIVO e dobrava a provisão que
        # já liquida (caso real More Crédito FICFIDC 18→19/jun: recon −155.000 = GAP −154.971;
        # born-ignored → GAP +28). Elas cobrem a mesma divergência de qtd que uma oficial pendente,
        # então entram no mesmo conjunto `covered_by_official` (recon nasce IGNORADO, não é enviado).
        # Só buySell: dividendo/JCP não explica divergência de quantidade (mesma regra do passo6_sids).
        liq_prov_sids = {beehus_catalog.id_str(p.get("securityId"))
                         for p in (src_provs or [])
                         if p.get("securityId")
                         and str(p.get("liquidationDate") or "")[:10] == target_date
                         and (p.get("provisionType") or "") == "buySell"}
        # Ativos com IRRF apurado (resgate de brazilianFund): o `balance` do resgate é LÍQUIDO
        # de IRRF, então o resíduo de qtd (projetada × alvo) é o próprio IRRF/PU — NÃO um trade
        # ausente. O bloco de IRRF já resolve; provisionar/adotar a qtd-alvo aqui DUPLICARIA o
        # IRRF (e sumiria com o resíduo que o IRRF explica). Por isso o fundo com IRRF é pulado.
        irrf_sids = {e.get("securityId") for e in irrf_entries if e.get("securityId")}
        # Offset de liquidação POR ATIVO (settlementDays − navDays), capturado do buySell da
        # janela. A provisão de ajuste usa esse offset p/ datar a liquidação (via _prov_dates),
        # em vez de um +1 fixo. Ausente / offset 0 → cai no +1 dia útil (piso de _prov_dates).
        offset_by_sid = {}
        for _sid, _ts in (txn_by_sid or {}).items():
            for _t in (_ts or []):
                if (_t.get("beehusTransactionType") or "") in _QTY_TXN_TYPES:
                    offset_by_sid[_sid] = _settlement_offset(_t)
                    break
        for dvg in diff.get("diverged", []):
            sid = dvg.get("securityId")
            if not dvg.get("qtyDiverged") or not sid:
                continue
            if sid in irrf_sids:
                # Divergência de qtd de FUNDO explicada pela apuração de IRRF (resíduo = IRRF/PU):
                # não gera provisão de ajuste NEM adota a qtd-alvo (mantém a qtd projetada, que
                # carrega o IRRF). O bloco de IRRF é quem resolve.
                dvg["explainedByIrrf"] = True
                dvg["suggestedAction"] = None
                continue
            # Δqty = alvo − projetada. A regra genérica lê Δ>0 (alvo tem MAIS) como
            # COMPRA perdida → caixa a pagar (negativo).
            delta = round(_num(dvg.get("realQuantity")) - _num(dvg.get("calcQuantity")), 6)
            # PU do ajuste: executionPrice CAPTURADO (endpoint execution-prices, data-alvo)
            # PRIMEIRO; fallback = PU-alvo (realPu → calcPu), o comportamento anterior. O
            # executionPrice é o preço real do trade → balance de caixa mais fiel que a marcação.
            _cap_ep = _num(((exec_price_by_sid or {}).get(sid) or {}).get("price"))
            if _cap_ep:
                ep, price_source = _cap_ep, "executionPrice"
            else:
                ep, price_source = (_num(dvg.get("realPu")) or _num(dvg.get("calcPu")) or 0.0), "pu-alvo"
            if abs(delta) < 1e-6 or not ep:
                continue
            sec_name = dvg.get("securityName") or sid
            if bool(dvg.get("matured")):
                # VENCIMENTO → TRANSAÇÃO de resgate (regra), verificado ANTES dos guardrails de
                # provisão: uma provisão oficial / do Passo 6 NÃO substitui o resgate ausente (o
                # ALVO ainda carrega o ativo vencido; a projeção já o zerou). Por isso o vencimento
                # NÃO recebe `coveredByProvision` → o chamador NÃO adota a qtd-alvo (a linha
                # permanece zerada, como manda o vencimento — senão ressuscitaria o ativo vencido).
                # O principal ENTRA no caixa (balance > 0): inverte o Δ (sistema vai real→calc =
                # resgate); senão a regra "alvo>proj = compra perdida" daria o sinal trocado.
                t_delta = -delta
                recon_txns.append({
                    "securityId": sid, "securityName": sec_name,
                    "beehusTransactionType": "buySell",
                    "direction": "subscription" if t_delta > 0 else "redemption",
                    "quantity": _safe_num(t_delta), "price": _safe_num(round(ep, 6)),
                    "priceSource": price_source, "matured": True,
                    "balance": round(-ep * t_delta, 2),
                    "operationDate": target_date, "liquidationDate": target_date,
                    "confidence": dvg.get("confidence"), "because": dvg.get("because"),
                    "description": f"Transação de resgate no vencimento do ativo {sec_name}",
                })
                continue
            # NÃO vencido. Passo 6 (o motor JÁ derivou a provisão da janela p/ este ativo) → pula,
            # não duplica; se TAMBÉM há oficial, mantém `coveredByProvision` (adoção NAV-neutra).
            if sid in passo6_sids:
                if sid in off_prov_sids:
                    dvg["coveredByProvision"] = True
                continue
            # Provisão OFICIAL já no sistema cobrindo a divergência de qtd (MESMO ativo): o recon
            # de ajuste É gerado, mas nasce IGNORADO e é tratado como DUPLICATA da oficial (mesma
            # mecânica de `duplicatesOfficial`, jul/2026): a adoção da qtd-alvo o coloca no `sim_nav`
            # e a UI o **pré-marca IGNORADO** descontando o saldo → NAV/GAP corretos por construção,
            # MAS **toggleável**: o usuário reativa em "↩ considerar" (what-if que passa a CONTAR a
            # provisão sobre a oficial). `coveredByOfficial` faz o envio (`/provisions`) PULAR — a
            # oficial já existe, não duplicar no Beehus. Dois casos, mesma economia (só muda a
            # origem da provisão oficial):
            #   • `off_prov_sids`  — provisão oficial ATIVA na data-alvo (pendente). Ela também entra
            #     no `official_prov_in_nav` (NÃO marcamos `coveredByProvision`, então não é excluída),
            #     servindo de ÂNCORA do born-ignored.
            #   • `liq_prov_sids`  — provisão buySell que LIQUIDA na data-alvo. Já saiu do NAV do alvo
            #     (o saldo virou caixa/qtd), então NÃO está em `official_prov_in_nav`; ainda assim o
            #     recon nasce IGNORADO (o trade já é representado pela provisão que liquida — o ajuste
            #     dobraria). Validado real (More Crédito FICFIDC): GAP −154.971 → +28.
            covered_by_active = sid in off_prov_sids
            covered_by_official = covered_by_active or sid in liq_prov_sids
            if covered_by_active:
                # Coberto por provisão oficial ATIVA (âncora em `official_prov_in_nav`): valora ao
                # PU-alvo (NÃO ao execPrice capturado). A adoção da qtd-alvo então gera intraday =
                # Δqty×(PU−execPrice) = 0 → o desconto do what-if born-ignored reproduz EXATAMENTE o
                # GAP "sem o recon" (robusto a execPrice≠PU; senão o intraday somado à contribuição
                # não seria desfeito pelo drop, e como a ÂNCORA oficial fica no NAV, o GAP derivaria).
                # A linha é informacional / não enviada, então o PU basta.
                ep = _num(dvg.get("realPu")) or _num(dvg.get("calcPu")) or ep
                price_source = "pu-alvo"
            # Coberto SÓ por provisão que LIQUIDA na data (sem âncora em `official_prov_in_nav`):
            # MANTÉM o executionPrice capturado. Não há oficial no NAV p/ duplicar, então o P&L
            # intraday do trade (Δqty×(PU−execPrice)) é REAL e deve seguir na contribuição — o drop
            # do born-ignored tira só o saldo do recon do NAV, a cota adotada fica marcada ao PU e o
            # intraday explica a variação → GAP≈0 (real More Crédito: −558 com PU-alvo → +28 c/ execPrice).
            # Divergência de qtd → PROVISÃO de ajuste. Mantém o Δ original — inverter aqui
            # corromperia a adoção (amountDifference vira a qtd a somar na linha). Datas pelo OFFSET
            # do trade (settlementDays − navDays). Só offset POSITIVO desloca (liquidação FUTURA);
            # offset ≤ 0 (trade já liquidado / navDays ≥ settlementDays) cai no +1 dia útil (piso) —
            # evita initialDate ANTES do alvo, que reprocessaria a origem já reconciliada.
            offset = offset_by_sid.get(sid) or 0
            offset = offset if offset > 0 else 0
            init_d, liq_d = _prov_dates(target_date, offset if offset else 1)
            recon_provisions.append({
                "securityId": sid, "securityName": sec_name,
                "initialDate": init_d, "liquidationDate": liq_d,
                "provisionType": "buySell", "provisionSource": "amountDifference",
                "balance": round(-ep * delta, 2), "amountDifference": _safe_num(delta),
                "executionPrice": _safe_num(round(ep, 6)), "priceSource": price_source,
                "offset": offset,
                "direction": "subscription" if delta > 0 else "redemption",
                "coveredByOfficial": covered_by_official,    # envio /provisions PULA (oficial já existe)
                "duplicatesOfficial": covered_by_official,   # nasce IGNORADA no NAV/GAP, toggleável
                "confidence": dvg.get("confidence"), "because": dvg.get("because"),
                "description": f"Provisão de ajuste por diferença na quantidade do ativo {sec_name}",
            })
    # Marca provisões CRIADAS que DUPLICAM uma provisão OFICIAL já no sistema (mesmo VALOR
    # financeiro a 2 casas + mesmo TIPO). Cobre (a) `reconProvisions` (ajuste amountDifference)
    # e (b) `provisions` de SUBSCRIÇÃO EM TRÂNSITO (Passo 6b, `provisionSource="subscription"`).
    # O guardrail por securityId NÃO as pega quando a oficial vem SEM securityId (ex.:
    # provisão genérica de "adjustments"/"Compra <fundo>"). Elas continuam sendo GERADAS,
    # mas ganham `duplicatesOfficial` p/ a UI pré-marcá-las como IGNORAR no NAV/GAP (senão a
    # mesma provisão é contada 2×: a oficial em `official_prov_in_nav` e a criada em
    # `prov_total`/`recon_prov_in_nav`). Casamento por CONTAGEM (consome uma oficial por
    # provisão) → não marca mais que o nº de oficiais equivalentes.
    # As oficiais JÁ reivindicadas por um recon `coveredByOfficial` (dedup DETERMINÍSTICO por
    # securityId) NÃO entram na contagem por valor — senão a mesma oficial seria consumida 2× e
    # marcaria indevidamente uma provisão IRMÃ de mesmo (valor, tipo) — ex.: uma subscrição em
    # trânsito do Passo 6b sem oficial própria (bug confirmado por revisão adversarial jul/2026).
    _covered_recon_sids = {beehus_catalog.id_str(p.get("securityId"))
                           for p in recon_provisions
                           if p.get("coveredByOfficial") and p.get("securityId")}
    _off_counts = {}
    for _op in official_provisions:
        if beehus_catalog.id_str(_op.get("securityId")) in _covered_recon_sids:
            continue
        _ok = (round(_num(_op.get("balance")), 2), _op.get("provisionType") or "")
        _off_counts[_ok] = _off_counts.get(_ok, 0) + 1
    # Exclui os recon `coveredByOfficial` do casamento por VALOR: eles já recebem
    # `duplicatesOfficial=True` DETERMINISTICAMENTE (por securityId, na criação) — deixá-los aqui
    # CONSUMIRIA de novo a contagem da oficial equivalente (`_off_counts`) e poderia marcar
    # indevidamente OUTRA provisão como duplicata.
    _dup_candidates = [p for p in provisions
                       if (p.get("provisionSource") or "") == "subscription"] \
        + [p for p in recon_provisions if not p.get("coveredByOfficial")]
    for _cp in _dup_candidates:
        _ck = (round(_num(_cp.get("balance")), 2), _cp.get("provisionType") or "")
        if _off_counts.get(_ck, 0) > 0:
            _cp["duplicatesOfficial"] = True
            _off_counts[_ck] -= 1
    recon_prov_total = round(sum(_num(p["balance"]) for p in recon_provisions), 2)
    recon_txn_total = round(sum(_num(t["balance"]) for t in recon_txns), 2)

    # Transações da janela (para o detalhamento) — visão amigável.
    txn_view = [{
        "id": beehus_catalog.id_str(t.get("_id")),
        "securityId": str(t.get("securityId") or ""),
        "securityName": name_hints.get(str(t.get("securityId") or ""),
                                       t.get("securityBeehusName") or "Caixa/sem ativo"),
        "type": t.get("beehusTransactionType") or "",
        "operationDate": str(t.get("operationDate") or "")[:10],
        "liquidationDate": str(t.get("liquidationDate") or "")[:10],
        "balance": _safe_num(round(_num(t.get("balance")), 2)),
        "quantity": _safe_num(t.get("quantity")),
        "description": t.get("description") or "",
        "effects": _txn_effects(t),   # o que o motor fez com esta transação (badges na UI)
    } for t in sorted(txns, key=lambda x: str(x.get("liquidationDate") or ""))]

    return {
        "irrf": irrf_entries, "irrfMissingTotal": irrf_missing_total,
        "officialProvisions": official_provisions,
        "liquidatingProvisions": liquidating_provisions,
        "stockEtfLiquidation": stocketf_liquidation,
        "reconProvisions": recon_provisions, "reconProvisionsTotal": recon_prov_total,
        "reconTransactions": recon_txns, "reconTransactionsTotal": recon_txn_total,
        "transactions": txn_view,
        "diff": diff,
    }


def _build_diff(rows, tgt_rows, tgt_doc, sim_cash, official_cash=None, name_hints=None,
                cash_residual=None, balance_tol_pct=0.0, tgt_nav=None):
    """Compara a posição CALCULADA (movimentada) com a **unprocessed do alvo**.
    Aponta ativos divergentes (qtd/pu/balance) e faltantes de cada lado.

    SEM caixa-âncora (verificação contra o caixa ELIMINADA) → `suggestedAction` por
    divergência de QUANTIDADE:
      • VENCIMENTO → "transaction" (o alvo ainda carrega o ativo vencido; falta o resgate);
      • demais → "provision" (adota a qtd-alvo, NAV-neutro, considerando o offset).
    A classificação NÃO depende mais do caixa — o resíduo de caixa (`cashResidual`) é só
    informativo (etapa "caixa" dos GAPs por etapa / linha Caixa do detalhe). Divergência de
    saldo com qtd igual (raro, só PU muito alto) é marcada p/ revisão manual, sem ação de
    posição aqui. A GERAÇÃO (valor/datas/envio) é o passo (3).
    """
    name_hints = name_hints or {}
    if not tgt_doc:
        return {"hasTarget": False,
                "cashResidual": _safe_num(cash_residual), "officialCash": _safe_num(official_cash),
                "note": "Sem unprocessed do alvo para comparar."}
    # "Liquidação B3" (_B3_LIQ_SECURITY) é ativo genérico de liquidação/ajuste, não posição:
    # fora do diff dos DOIS lados (não vira diverged/onlyCalc/onlyReal nem entra nos counts).
    # Já não vira linha da projeção (laço de `rows`); aqui cobre o caso de ele existir na
    # unprocessed-alvo (senão apareceria como "só no alvo").
    calc_by_sid = {r["securityId"]: r for r in rows
                   if r.get("securityId") and beehus_catalog.id_str(r["securityId"]) != _B3_LIQ_SECURITY}
    real_by_sid = {r["securityId"]: r for r in tgt_rows.values()
                   if r.get("securityId") and beehus_catalog.id_str(r["securityId"]) != _B3_LIQ_SECURITY}
    diverged, only_calc, only_real = [], [], []
    for sid, c in calc_by_sid.items():
        real = real_by_sid.get(sid)
        if not real:
            # Ativo na MOVIMENTADA, ausente no alvo. Classificação FLAG-ONLY:
            # hipótese + confiança BAIXA + revisão manual; NÃO gera recon (este caso
            # nunca entra no laço de recon — só `diverged` entra) e precisa validação
            # com dado real do que significa cada situação antes de qualquer escrita.
            only_calc.append({"securityId": sid, "securityName": c["securityName"],
                              "quantity": c["quantity"], "balance": c["balance"],
                              "suggestedAction": "review", "confidence": "low",
                              "needsManualReview": True,
                              "because": ("Ativo na carteira MOVIMENTADA mas AUSENTE na unprocessed do "
                                          "alvo. Hipóteses: venda/resgate não capturado na janela, ou "
                                          "baixa/transferência no alvo."
                                          " ⚠ Requer revisão manual — nenhuma escrita é gerada automaticamente.")})
            continue
        # PU-ALVO := PU EM USO (oficial da processed quando o alvo está processado; a
        # unprocessed-alvo NÃO carrega o PU de B1/B2/C1/C2). O `diff` confronta só a
        # QUANTIDADE; divergência só-de-PU (qtd igual) não existe mais. Ver _resolve_pu
        # (tier 0 = processed-position) e Opção A/B.
        rqty = _num(real.get("quantity"))
        used_pu = _num(c["pu"])
        dq = round(_num(c["quantity"]) - rqty, 6)
        # A divergência de SALDO é DERIVADA da de quantidade ao PU em uso (db = PU × Δqty),
        # NÃO a subtração de dois saldos arredondados de forma independente. Senão o
        # arredondamento de PU×qtd (PU herdado da processed com >6 casas × quantidade grande)
        # gera um resíduo espúrio de centavos mesmo com qtd E PU iguais (caso real 69cc1d08:
        # 0,03 com qtd/PU iguais → "saldo diverge" falso). Assim db = 0 EXATO quando a qtd
        # bate, e o saldo-alvo (`rbal`) fica CONSISTENTE com o projetado (== projetado em Δqty=0).
        db = round(used_pu * dq, 2)
        rbal = round(_num(c["balance"]) - db, 2)
        # Divergência SÓ quando o IMPACTO EM $ é material: `db = PU_em_uso × Δqty` (o saldo é
        # DERIVADO da qtd — divergência só-de-PU não existe). Uma diferença de qtd ÍNFIMA cujo
        # valor financeiro |Δqty × PU| é < R$ 0,01 (ex.: 0,000329 cota × PU 2,11 = R$ 0,0007 →
        # `db` arredonda a 0,00) é ARREDONDAMENTO da inferência −saldo/PU, NÃO uma divergência
        # real → IGNORADA (fora de `diverged`, logo sem recon transação/provisão de ajuste).
        # Piso ABSOLUTO (`_BALANCE_ABS_TOL` = R$ 0,01): a qtd tem tolerância absoluta e o
        # threshold % de saldo (`balance_tol_pct`) NÃO a suprime (Scenario P1) — como o único
        # tipo de divergência é o de qtd (db = PU×Δqty), o caminho relativo não é aplicado à
        # detecção. Antes o ramo `abs(dq) > _QTY_DIFF_TOL` marcava qualquer Δqty > 1e-6, mesmo
        # sem impacto em caixa (ex.: 0,0003 cota → transação de ajuste espúria de saldo 0,00).
        if abs(db) > _BALANCE_ABS_TOL:
            qty_diverged = abs(dq) > _QTY_DIFF_TOL
            diverged.append({
                "securityId": sid, "securityName": c["securityName"],
                "calcQuantity": c["quantity"], "realQuantity": _safe_num(round(rqty, 6)),
                # realPu == calcPu (PU em uso): o alvo é mostrado ao MESMO PU da projeção.
                "calcPu": c["pu"], "realPu": _safe_num(round(used_pu, 6)),
                "calcBalance": c["balance"], "realBalance": _safe_num(round(rbal, 2)),
                "qtyDiff": _safe_num(dq), "balanceDiff": _safe_num(db),
                "qtyDiverged": qty_diverged,
                # Vencido: a projeção zerou o ativo (maturityDate ≤ alvo) e o alvo ainda
                # o carrega (não processou o vencimento). A correção é um RESGATE (caixa
                # ENTRA), não uma compra — o passo 3 inverte o Δ p/ acertar o sinal.
                "matured": bool(c.get("matured")),
            })
    for sid, real in real_by_sid.items():
        if sid not in calc_by_sid:
            # Ativo no ALVO, ausente na movimentada. Classificação FLAG-ONLY —
            # mesma lógica do onlyCalc: hipótese + confiança baixa + revisão manual,
            # sem geração de escrita (não entra no recon).
            only_real.append({"securityId": sid,
                              "securityName": name_hints.get(sid, sid),
                              "quantity": _safe_num(real.get("quantity")),
                              "balance": _safe_num(real.get("balance")),
                              "suggestedAction": "review", "confidence": "low",
                              "needsManualReview": True,
                              "because": ("Ativo na unprocessed do ALVO mas AUSENTE na carteira "
                                          "movimentada. Hipóteses: compra/aporte não capturado na "
                                          "janela, ou posição de origem não mapeada (sem securityId)."
                                          " ⚠ Requer revisão manual — nenhuma escrita é gerada automaticamente.")})
    # Passo (2): classifica cada divergência de QTD. SEM caixa-âncora: VENCIMENTO →
    # "transaction" (resgate); demais → "provision" (adota a qtd-alvo, considerando o
    # offset). A classificação é determinística (não depende do caixa) → confiança ALTA;
    # o resgate no vencimento fica MÉDIA (o alvo pode estar só atrasado).
    qty_div = [d for d in diverged if d.get("qtyDiverged")]
    n_qty = len(qty_div)
    for d in diverged:
        if d.get("qtyDiverged"):
            if d.get("matured"):
                d["suggestedAction"] = "transaction"
                d["confidence"] = "medium"
                d["because"] = ("Ativo VENCIDO — a projeção o zerou (vencimento ≤ alvo) e o alvo "
                                "ainda o carrega. Falta a TRANSAÇÃO de resgate no vencimento (caixa entra).")
            else:
                d["suggestedAction"] = "provision"
                d["confidence"] = "high"
                d["because"] = ("Divergência de quantidade → PROVISÃO de ajuste (adota a qtd-alvo, "
                                "NAV-neutro) considerando o offset de liquidação. Não é analisada contra o caixa.")
        else:
            # divergência só de PU/saldo (qtd igual) — flag + confiança baixa +
            # revisão manual. SEM ação de posição (o tratamento é via preço de execução,
            # bloco/rota próprios); NÃO gera recon (qtyDiverged=False é pulado no laço).
            d["suggestedAction"] = None
            d["confidence"] = "low"
            d["needsManualReview"] = True
            d["because"] = ("Divergência só de PU/saldo (qtd igual) — verificar fonte de preço / "
                            "preço de execução. ⚠ Requer revisão manual (sem ajuste de posição automático).")
    classification_conf = (None if not qty_div
                           else "medium" if any(d.get("matured") for d in qty_div) else "high")
    # NAV/cota/GAP reais do alvo (navPackage) — chega PRÉ-BUSCADO de `_diagnose`.
    tgt_nav = tgt_nav or {}
    # Σ SALDOS da posição-ALVO (os "dados da posição alvo"): soma o balance das linhas do
    # alvo (exclui a "Liquidação B3", que não é posição). É a contrapartida do "Σ saldos"
    # projetado na Composição do NAV — a UI mostra os dois lado a lado + Δ.
    tgt_sec_balance = round(sum(_num(rr.get("balance")) for rr in tgt_rows.values()
                                if beehus_catalog.id_str(rr.get("securityId")) != _B3_LIQ_SECURITY), 2) if tgt_rows else None
    return {
        "hasTarget": True,
        "diverged": diverged, "onlyCalc": only_calc, "onlyReal": only_real,
        "counts": {"diverged": len(diverged), "onlyCalc": len(only_calc),
                   "onlyReal": len(only_real), "qtyDiverged": n_qty,
                   # total de achados marcados p/ revisão manual (onlyCalc +
                   # onlyReal + divergência de saldo c/ qtd igual) — chip de resumo na UI.
                   "manualReview": len(only_calc) + len(only_real)
                   + sum(1 for d in diverged if d.get("needsManualReview"))},
        # Resíduo de caixa (INFORMATIVO) — não classifica mais nada (caixa-âncora eliminado).
        "cashResidual": _safe_num(cash_residual), "officialCash": _safe_num(official_cash),
        "movedCash": _safe_num(sim_cash),
        # Ação predominante da carteira: provisão se houver qualquer divergência não-vencida;
        # senão transação (só vencimentos); None sem divergência de qtd.
        "suggestedAction": ("provision" if any(not d.get("matured") for d in qty_div)
                            else "transaction" if qty_div else None),
        "classificationConfidence": classification_conf,
        "real": {
            "nav": _safe_num(tgt_nav.get("nav")),
            "navPerShare": _safe_num(tgt_nav.get("navPerShare")),
            "inAndOutFlows": _safe_num(tgt_nav.get("inAndOutFlows")),
            "returnNavPerShare": _safe_num(tgt_nav.get("returnNavPerShare")),
            "returnContribution": _safe_num(tgt_nav.get("returnContribution")),
            "securitiesBalance": _safe_num(tgt_sec_balance),
        },
    }


# ── Rotas ────────────────────────────────────────────────────────────────────

@bp.route("/conciliacao-mov")
def index():
    companies = sorted(
        [{"id": cid, "name": name or cid} for cid, name in get_company_names().items()],
        key=lambda c: c["name"],
    )
    cf = get_company_filter()
    if cf:
        companies = [c for c in companies if c["id"] in cf]
    return render_template("conciliacao_mov.html", companies=companies)


@bp.route("/api/conciliacao-mov/dates")
def get_dates():
    company_id = request.args.get("companyId", "")
    end_date = request.args.get("endDate") or None
    if not company_id or not company_visible(company_id):
        return jsonify({"cards": []})
    return jsonify({"cards": [{"date": d} for d in get_biz_dates(_NUM_DATES, end_date)]})


@bp.route("/api/conciliacao-mov/rows")
def get_rows():
    """Grade do Step 1 — carteiras com posição não-processada na ORIGEM (projetáveis)
    e navPackage do **SISTEMA na data-ALVO**. NÃO analisa dados da origem: as
    colunas são o navPackage oficial do alvo (nav/cota/gap$/gap%). O filtro por
    divergência é client-side (limiar opcional)."""
    try:
        company_id = request.args.get("companyId", "")
        date = request.args.get("date", "")            # origem (projetável)
        target_date = request.args.get("targetDate", "")  # alvo (navPackage do sistema)
        if not company_id or not company_visible(company_id) or not date:
            return jsonify({"rows": [], "date": date})
        wallets = beehus_catalog.wallets_for_company(company_id)
        wallet_ids = list(wallets.keys())
        if not wallet_ids:
            return jsonify({"rows": [], "date": date})
        unproc_wids = beehus_catalog.unprocessed_existing_wallets(company_id, date, wallet_ids)
        if not unproc_wids:
            return jsonify({"rows": [], "date": date})
        # navPackage do sistema na data-ALVO (nav/cota/gap). Sem alvo → grade sem
        # números do sistema (só lista as carteiras projetáveis).
        res = beehus_catalog.nav_results(company_id, target_date) if target_date else {}
        sys_by_wid = {beehus_catalog.id_str(w.get("walletId")): w
                      for w in res.get("walletsWithNavDetailed", [])}
        rows = []
        for wid in unproc_wids:
            w = sys_by_wid.get(wid) or {}
            rnps, rc = w.get("returnNavPerShare"), w.get("returnContribution")
            gap_pct = (_num(rnps) - _num(rc)) if (rnps is not None and rc is not None) else None
            rows.append({
                "walletId": wid,
                "walletName": w.get("walletName") or wallets.get(wid, wid),
                # navPackage do SISTEMA na data-alvo:
                "sysNav": _safe_num(w.get("nav")),
                "sysNavPerShare": _safe_num(w.get("navPerShare")),
                "sysGapCash": _safe_num(w.get("financialValueReturnDifference")),
                "sysGapPct": _safe_num(gap_pct),
                "hasSys": bool(w),
            })
        rows.sort(key=lambda x: x["walletName"] or "")
        return jsonify(_sanitize({"rows": rows, "date": date, "targetDate": target_date}))
    except Exception:
        import traceback
        traceback.print_exc()
        _log.exception("conciliacao-mov /rows failed")
        return jsonify({"error": "falha ao processar"}), 500


def _resolve_dates(data):
    """`(source_date, target_date, err)` do corpo da requisição."""
    source_date = str(data.get("sourceDate") or "")[:10]
    target_date = str(data.get("targetDate") or "")[:10] or _next_biz_day(source_date)
    if not source_date:
        return None, None, "sourceDate obrigatório"
    if not (target_date > source_date):
        return None, None, "targetDate deve ser posterior à sourceDate"
    return source_date, target_date, None


def _cput(key, value):
    """Insere `value` no cache local da página (mesmo formato de `_cget`), com
    timestamp fresco — o prefetch em lote pré-popula o cache p/ cada carteira ler
    SEM I/O via `use_cache=True`."""
    with _proj_lock:
        _proj_cache[key] = (time.monotonic(), value)


def _proj_purge(wallet_ids):
    """Remove do `_proj_cache`, sob lock e iterando um SNAPSHOT (`list(...)`), toda
    chave de qualquer carteira em `wallet_ids` — garante que a projeção subsequente
    (use_cache=True) só leia o que ESTE request buscar. De carona, EXPULSA entradas
    além do TTL (o TTL só era checado na leitura → o dict crescia sem teto com o
    uptime, alargando a janela de iteração)."""
    wids = set(wallet_ids)
    now = time.monotonic()
    with _proj_lock:
        for k in list(_proj_cache):
            entry = _proj_cache.get(k)
            if (any(w in k for w in wids)
                    or (entry is not None and now - entry[0] >= _PROJ_CACHE_TTL)):
                _proj_cache.pop(k, None)


def _run_parallel(tasks, workers=8):
    """Executa `{chave: loader}` em PARALELO (threads; o cliente HTTP tem pool 20 e o
    catálogo já usa o mesmo padrão em `_NAV_WARM_WORKERS`). Retorna `(ok, errs)`:
    `ok[chave]=valor` dos que deram certo, `errs[chave]=exceção` dos que falharam —
    o chamador decide se propaga (lote) ou segue fail-open (prewarm single)."""
    ok, errs = {}, {}
    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(workers, max(1, len(tasks)))) as ex:
        futs = {ex.submit(fn): key for key, fn in tasks.items()}
        for f in concurrent.futures.as_completed(futs):
            key = futs[f]
            try:
                ok[key] = f.result()
            except Exception as e:   # noqa: BLE001 — o chamador decide o destino
                errs[key] = e
    return ok, errs


def _prewarm_single(company_id, wallet_id, source_date, target_date):
    """Aquece o `_proj_cache` p/ UMA carteira disparando em PARALELO as mesmas leituras
    que a projeção individual faria em SÉRIE (~10 round-trips sequenciais → 2 ondas).
    As chaves/loaders REPLICAM os `_cget` do orquestrador — divergência de chave vira
    cache MISS (refetch sequencial), nunca dado errado. Fail-open: exceção numa leitura
    só pula o warm dela (o motor refaz e SURFACEIA o erro no fluxo normal).

    Antes de aquecer, PURGA do cache toda chave da carteira: a projeção subsequente
    (`use_cache=True`) lê APENAS o que esta request acabou de buscar — mesmo frescor
    do antigo `use_cache=False`, sem a latência serial. Chaves condicionais que o
    prewarm não cobre (ex.: "prices1" de fundo resgatado) ficam purgadas → o motor
    busca fresco na hora, como antes."""
    cid, wid = company_id, wallet_id
    _tl.overlay = None   # threads são REUSADAS entre requests — nunca herdar snapshot
    _proj_purge([wid])
    # PIPELINE (não barreira): a Onda B (preços/eventos, as leituras mais caras) só
    # depende de unproc-src/tgt + txns + wallet — dispara ASSIM QUE essas 4 chegam,
    # sobrepondo com NAV/envelopes/exec-prices ainda em voo.
    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as ex:
        # ── Onda A: leituras independentes (só precisam de empresa/carteira/datas) ──
        futs = {
            ("unproc", cid, wid, source_date):
                ex.submit(beehus_catalog.unprocessed_doc, wid, source_date, cid),
            ("unproc", cid, wid, target_date):
                ex.submit(beehus_catalog.unprocessed_doc, wid, target_date, cid),
            ("txns", cid, wid, source_date, target_date):
                ex.submit(lambda: beehus_catalog.transactions_search(
                    cid, initial_date=source_date, final_date=target_date, wallet_ids=[wid])),
            ("execprices", cid, wid, target_date):
                ex.submit(beehus_catalog.execution_prices_by_sid, cid, wid, target_date, target_date),
            ("procenv", cid, wid, target_date):
                ex.submit(beehus_catalog.processed_envelope, wid, target_date, cid),
            ("procenv", cid, wid, source_date):
                ex.submit(beehus_catalog.processed_envelope, wid, source_date, cid),
            ("nav", cid, wid, source_date):
                ex.submit(beehus_catalog.nav_doc_for_entity_date, wid, source_date, cid),
            ("nav", cid, wid, target_date):
                ex.submit(beehus_catalog.nav_doc_for_entity_date, wid, target_date, cid),
            # resolve_wallet é wrapper puro do catálogo (caches RLock) — warm do índice
            # e fonte do entityId da Onda B. O motor chama de novo (hit em memória).
            "wallet": ex.submit(resolve_wallet, wid,
                                {"entityId": 1, "currencyId": 1, "name": 1, "companyId": 1},
                                company_id=cid),
            # warm do ÍNDICE GLOBAL de securities (TTL do catálogo): no processo frio é a
            # leitura mais cara de todas (~2s) e o motor a dispara SEQUENCIAL via
            # securities_by_ids. Aquecida aqui, vira hit em memória.
            "secindex": ex.submit(beehus_catalog.securities_index),
        }

        def _res(key):
            """Resultado do future `key`, fail-open (None em erro — o motor refaz)."""
            try:
                return futs[key].result()
            except Exception:   # noqa: BLE001 — o motor surfaceia no fluxo normal
                return None

        # ── Onda B: espera SÓ as 4 dependências do escopo e dispara preços/eventos.
        # Réplica da derivação do orquestrador (src_rows/sids) — manter em sincronia.
        wallet = _res("wallet")
        src_doc = _res(("unproc", cid, wid, source_date))
        tgt_doc = _res(("unproc", cid, wid, target_date))
        raw_txns = _res(("txns", cid, wid, source_date, target_date))
        futs_b = {}
        if src_doc and wallet is not None and raw_txns is not None:
            _uid_pp = _uid_to_pp(src_doc, tgt_doc)
            src_secs, _ = _enrich_secs(src_doc.get("securities", []) or [], _uid_pp)
            src_rows = _aggregate_positions(src_secs)
            txns = [t for t in raw_txns
                    if not t.get("trashed") and str(t.get("liquidationDate") or "")[:10] > source_date]
            sids = {r["securityId"] for r in src_rows.values() if r["securityId"]}
            sids |= {str(t.get("securityId") or "") for t in txns if t.get("securityId")}
            sids.discard("")
            if sids:
                _sids_key = tuple(sorted(sids))
                entity_id = str(wallet.get("entityId") or "")
                futs_b = {
                    ("prices", cid, wid, _sids_key):
                        ex.submit(lambda: beehus_catalog.security_prices_resolved(
                            list(sids), company_id=cid, entity_id=entity_id,
                            wallet_id=wid, records=None)),
                    ("events", wid, source_date, target_date, _sids_key):
                        ex.submit(_events_window, list(sids), txns, target_date),
                }

        # ── Colheita: grava no cache global E no OVERLAY do request (snapshot que a
        # projeção deste request lê, imune a purge/overwrite concorrente). Erro =
        # pula o warm (motor refaz) — EXCETO auth: token rejeitado condena TODAS as
        # leituras, então re-levanta já (502 imediato, sem ~10 refetches fadados).
        ov = {}
        n_err, auth_err = 0, None
        for key, fut in list(futs.items()) + list(futs_b.items()):
            if key in ("wallet", "secindex"):   # warms de índice, não são chaves do cache
                continue
            try:
                value = fut.result()
            except BeehusAuthError as e:
                auth_err = auth_err or e
                n_err += 1
                continue
            except Exception:   # noqa: BLE001
                n_err += 1
                continue
            _cput(key, value)
            ov[key] = value
        if auth_err is not None:
            raise auth_err
        if n_err:
            _log.info("conciliacao-mov prewarm %s: %d leitura(s) falharam (motor refaz)",
                      wid, n_err)
        _tl.overlay = ov


def _prefetch_batch(company_id, wallet_ids, source_date, target_date):
    """Pré-busca, EM LOTE (1 chamada por endpoint p/ TODO o conjunto), tudo que a
    projeção lê por carteira/data e PRÉ-POPULA o `_proj_cache` com as chaves EXATAS que
    `_build_movement_for_wallet(use_cache=True)` espera. Devolve
    `(shared_price_records, shared_events)` — a UNIÃO dos preços/eventos (resolução é
    client-side por carteira, então a injeção é idêntica ao fetch por-carteira).

    Colapsa ~10·N round-trips em ~9 fixos:
      • unprocessed (range origem..alvo, walletIds)        → 1
      • processed-position (walletIds), 1 por data          → 2
      • transactions (walletIds, range)                      → 1
      • execution-prices (empresa/data, sem walletId)        → 1
      • nav_results (consolidado), 1 por data                → 2
      • security-prices (união) + re-fetch de omissões       → 1(+)
      • security-events (união)                              → 1
    `securities`/`wallets` saem de índices warm em memória (sem custo por carteira)."""
    cid = company_id
    wids = [str(w) for w in wallet_ids if w]
    _tl.overlay = None   # defesa: thread reusada não pode herdar snapshot de /movimentar

    # ── Onda A: as 8 leituras de lote são INDEPENDENTES entre si → paralelas.
    # (Antes rodavam em série: ~8 round-trips somados viravam o wall-time do prefetch.)
    # Qualquer falha ABORTA o prefetch como antes (a rota devolve 502/500).
    got, errs = _run_parallel({
        # warm dos índices compartilhados (nomes de carteira; resolve_wallet/
        # securities_by_ids leem daqui sem round-trip por carteira). No processo
        # frio o índice de securities é a leitura mais cara (~2s) — aquecido aqui
        # em paralelo, o loop de projeção lê da memória.
        "wallets": lambda: beehus_catalog.wallets_for_company(cid),
        "secindex": lambda: beehus_catalog.securities_index(),
        # 1) unprocessed origem+alvo (1 chamada cobre as duas datas via range, walletIds).
        "unproc": lambda: beehus_catalog.unprocessed_docs_map(
            cid, wids, [source_date, target_date]),
        # 2) processed-position (envelope) — 1 chamada por data, walletIds.
        "env_src": lambda: beehus_catalog.processed_envelopes_map(cid, source_date, wids),
        "env_tgt": lambda: beehus_catalog.processed_envelopes_map(cid, target_date, wids),
        # 3) transações da janela (1 chamada, walletIds; RAW — o motor filtra).
        "txns": lambda: beehus_catalog.transactions_search(
            cid, initial_date=source_date, final_date=target_date, wallet_ids=wids),
        # 4) execution-prices (1 chamada empresa/data-alvo; cada doc traz walletId).
        "execprices": lambda: beehus_catalog.execution_prices_by_wallet_sid(
            cid, target_date, target_date),
        # 5) NAV (nav_results consolidado, 1 por data).
        "nav_src": lambda: beehus_catalog.nav_results(cid, source_date),
        "nav_tgt": lambda: beehus_catalog.nav_results(cid, target_date),
    })
    errs.pop("secindex", None)   # warm-only: falha aqui não aborta (motor busca na hora)
    if errs:
        raise next(iter(errs.values()))

    # PURGE por carteira ANTES de re-popular: os _cputs abaixo sobrescrevem as chaves
    # principais, mas as DERIVADAS ("events"/"prices"/"prices1", keyed por sids) de um
    # request ≤TTL anterior sobreviveriam e VENCERIAM os blobs recém-buscados no _cget
    # (hit curto-circuita o loader) — events de txns antigas com txns novas (pré-existente).
    _proj_purge(wids)

    unproc_map = got.get("unproc") or {}
    for wid in wids:
        for dd in (source_date, target_date):
            _cput(("unproc", cid, wid, dd), unproc_map.get((wid, dd)))

    for dd, ek in ((source_date, "env_src"), (target_date, "env_tgt")):
        env_map = got.get(ek) or {}
        for wid in wids:
            _cput(("procenv", cid, wid, dd), env_map.get(wid))

    # transações agrupadas por carteira (RAW; o motor aplica trashed+liq>origem).
    all_txns = got.get("txns") or []
    txns_by_wid = {wid: [] for wid in wids}
    for t in all_txns:
        w = beehus_catalog.id_str(t.get("walletId"))
        if w in txns_by_wid:
            txns_by_wid[w].append(t)
    for wid in wids:
        _cput(("txns", cid, wid, source_date, target_date), txns_by_wid.get(wid, []))

    ep_by_wid = got.get("execprices") or {}
    for wid in wids:
        _cput(("execprices", cid, wid, target_date), ep_by_wid.get(wid, {}))

    # navPackage por carteira a partir de `walletsWithNavDetailed`. `inAndOutFlows`
    # não vem no consolidado (só exibição no card "alvo real") → None.
    for dd, nk in ((source_date, "nav_src"), (target_date, "nav_tgt")):
        res = got.get(nk) or {}
        by_wid = {beehus_catalog.id_str(w.get("walletId")): w
                  for w in (res.get("walletsWithNavDetailed") or [])}
        for wid in wids:
            w = by_wid.get(wid)
            _cput(("nav", cid, wid, dd), ({
                "nav": w.get("nav"), "navPerShare": w.get("navPerShare"),
                "amount": w.get("amount"), "inAndOutFlows": None,
                "returnNavPerShare": w.get("returnNavPerShare"),
                "returnContribution": w.get("returnContribution"),
            } if w else {}))

    # ── Onda B: UNIÃO de securityIds (superconjunto: todos os ativos de TODA
    # unprocessed + todas as transações; o motor resolve seu subconjunto client-side,
    # então um superconjunto é seguro). Preços e eventos são independentes → paralelos.
    union = set()
    for doc in unproc_map.values():
        for s in (doc or {}).get("securities", []) or []:
            sid = beehus_catalog.id_str((s.get("preProcessingData") or {}).get("securityId") or "")
            if sid:
                union.add(sid)
    for t in all_txns:
        sid = beehus_catalog.id_str(t.get("securityId") or "")
        if sid:
            union.add(sid)
    union.discard("")
    shared_price_records, shared_events = [], []
    if union:
        ul = sorted(union)

        def _fetch_prices():
            recs = beehus_catalog.security_price_records(ul) or []
            have = {beehus_catalog.id_str(r.get("securityId")) for r in recs}
            missing = [s for s in ul if s not in have]
            if missing:   # backend pode OMITIR ativos numa resposta grande → re-busca
                recs = list(recs) + (beehus_catalog.security_price_records(missing) or [])
            return recs

        def _fetch_events():
            # chunks p/ 414, em PARALELO (união grande de sids); `map` preserva a
            # ordem e o engolimento é POR CHUNK (mesma semântica do loop antigo).
            def _one(i):
                try:
                    return _api_security_events(security_ids=ul[i:i + 150]) or []
                except (BeehusAuthError, BeehusAPIError, Exception):  # noqa: BLE001
                    return []
            starts = list(range(0, len(ul), 150))
            evs = []
            with concurrent.futures.ThreadPoolExecutor(
                    max_workers=min(5, max(1, len(starts)))) as ex:
                for out in ex.map(_one, starts):
                    evs += out
            return evs

        got_b, errs_b = _run_parallel({"prices": _fetch_prices, "events": _fetch_events})
        if errs_b:   # só prices pode falhar (events engole por chunk) — aborta como antes
            raise next(iter(errs_b.values()))
        shared_price_records = got_b.get("prices") or []
        shared_events = got_b.get("events") or []
    return shared_price_records, shared_events


@bp.route("/api/conciliacao-mov/movimentar", methods=["POST"])
def movimentar():
    """Projeta UMA carteira — usado pelo DETALHE (recarga completa ao abrir um
    resultado `_lite` do lote e re-projeções pós-ação); o grid usa `/movimentar-batch`.
    Requests concorrentes (detalhe + lote, 2 abas) são seguros: purge/_cput sob
    `_proj_lock` e a projeção lê o snapshot do próprio request (overlay)."""
    data = request.get_json() or {}
    company_id = str(data.get("companyId") or "")
    wallet_id = str(data.get("walletId") or "")
    if not company_id or not company_visible(company_id):
        return jsonify({"error": "acesso negado"}), 403
    if not wallet_id:
        return jsonify({"error": "walletId obrigatório"}), 400
    source_date, target_date, err = _resolve_dates(data)
    if err:
        return jsonify({"error": err}), 400
    try:
        # Prewarm PARALELO (2 ondas) + leitura do SNAPSHOT do request (overlay
        # thread-local; fallback cache global recém-aquecido): mesmos dados, mesmo
        # frescor do antigo use_cache=False (o prewarm purga e re-busca AGORA), sem
        # os ~10 round-trips sequenciais. Falha no prewarm → o motor refaz a leitura
        # que faltou e surfaceia o erro aqui, como antes (auth falha já no prewarm).
        _prewarm_single(company_id, wallet_id, source_date, target_date)
        result = _build_movement_for_wallet(company_id, wallet_id, source_date, target_date,
                                            use_cache=True)
    except (BeehusAuthError, BeehusAPIError) as e:
        return jsonify({"error": str(e), "upstream_status": getattr(e, "status", None)}), 502
    except Exception:
        _log.exception("conciliacao-mov /movimentar failed %s", wallet_id)
        return jsonify({"error": "falha ao projetar"}), 500
    finally:
        _tl.overlay = None   # thread é reusada pelo próximo request — nunca vazar snapshot
    return jsonify(result)


@bp.route("/api/conciliacao-mov/prewarm", methods=["POST"])
def prewarm():
    """Aquece, em BACKGROUND, os índices de catálogo caros que o 1º cálculo da
    sessão pagaria em SÉRIE: `securities_index` (~2s no frio — o custo fixo
    dominante) e, quando há empresa, `wallets_for_company`. São caches TTL (5min)
    COMPARTILHADOS por todas as telas: nenhum dado novo, nenhuma escrita, resultado
    idêntico ao caminho normal — só antecipa a leitura. É OPT-IN (o front só chama
    com o toggle "Pré-aquecer" ligado, decisão do usuário ao abrir o app).

    O trabalho roda SEMPRE numa thread daemon (sobrevive a desconexão do cliente e
    ao cap de espera abaixo). Best-effort: token ausente/expirado ou falha faz o
    warm silenciar; o fluxo normal re-busca e sinaliza o erro como sempre. Sem
    walletIds → NÃO faz o prefetch pesado por-carteira (isso é do /movimentar[-batch]);
    aqui só os 2 índices, cujo custo NÃO cresce com o tamanho da seleção.

    Contrato de resposta:
    - `wait` != false (padrão): o request AGUARDA o warm terminar (cap de segurança
      `_PREWARM_WAIT_CAP`) e devolve 200 `{warming, done}` — assim o front sabe QUANDO
      o aquecimento realmente acabou e pode esconder o spinner no tempo real (não num
      atraso chutado). Se estourar o cap, devolve `done:false` e o daemon segue.
    - `wait` == false: devolve 202 IMEDIATO (fire-and-forget), como antes."""
    data = request.get_json() or {}
    company_id = str(data.get("companyId") or "")
    if company_id and not company_visible(company_id):
        return jsonify({"error": "acesso negado"}), 403
    wait = data.get("wait", True)

    done = threading.Event()

    def _warm():
        try:
            try:
                beehus_catalog.securities_index()
            except Exception:   # noqa: BLE001 — warm é best-effort; não propaga
                pass
            if company_id:
                try:
                    beehus_catalog.wallets_for_company(company_id)
                except Exception:   # noqa: BLE001
                    pass
        finally:
            done.set()   # sinaliza o fim mesmo em falha → o cliente nunca fica preso

    threading.Thread(target=_warm, daemon=True).start()
    if not wait:
        return jsonify({"warming": True, "done": False}), 202
    finished = done.wait(timeout=_PREWARM_WAIT_CAP)
    return jsonify({"warming": True, "done": bool(finished)}), 200


def _batch_prologue(data):
    """Validação (empresa/carteiras/datas) + prefetch EM LOTE — o prólogo comum das
    rotas de lote. Retorna `(company_id, wallet_ids, source, target, shared_records,
    shared_events, err_response)`; se `err_response` != None, é um `(jsonify, status)`
    pronto p/ devolver (os 6 primeiros vêm None). Ver `_prefetch_batch`."""
    company_id = str(data.get("companyId") or "")
    wallet_ids = [str(w) for w in (data.get("walletIds") or []) if w]
    if not company_id or not company_visible(company_id):
        return (None,) * 6 + ((jsonify({"error": "acesso negado"}), 403),)
    if not wallet_ids:
        return (None,) * 6 + ((jsonify({"error": "walletIds obrigatório"}), 400),)
    source_date, target_date, err = _resolve_dates(data)
    if err:
        return (None,) * 6 + ((jsonify({"error": err}), 400),)
    try:
        shared_records, shared_events = _prefetch_batch(
            company_id, wallet_ids, source_date, target_date)
    except (BeehusAuthError, BeehusAPIError) as e:
        return (None,) * 6 + ((jsonify({"error": str(e),
                                        "upstream_status": getattr(e, "status", None)}), 502),)
    except Exception:
        _log.exception("conciliacao-mov batch prefetch failed")
        return (None,) * 6 + ((jsonify({"error": "falha no prefetch do lote"}), 500),)
    return company_id, wallet_ids, source_date, target_date, shared_records, shared_events, None


@bp.route("/api/conciliacao-mov/movimentar-batch", methods=["POST"])
def movimentar_batch():
    """Projeta VÁRIAS carteiras numa só requisição. Faz o prefetch EM LOTE (1 chamada
    por endpoint p/ todo o conjunto, ver `_prefetch_batch`) e roda a MESMA projeção por
    carteira lendo do cache (`use_cache=True`) + preços/eventos injetados → ZERO I/O por
    carteira. Resultado idêntico ao `/movimentar` carteira a carteira (mesma função),
    mas ~10·N round-trips viram ~9 fixos. Devolve `{results: [...]}` (1 por carteira, na
    ordem pedida; erros por carteira vêm como `{walletId, error}` sem derrubar o lote)."""
    (company_id, wallet_ids, source_date, target_date,
     shared_records, shared_events, err) = _batch_prologue(request.get_json() or {})
    if err:
        return err
    results = []
    try:
        for wid in wallet_ids:
            try:
                results.append(_build_movement_for_wallet(
                    company_id, wid, source_date, target_date, use_cache=True,
                    shared_price_records=shared_records, shared_events=shared_events))
            except (BeehusAuthError, BeehusAPIError) as e:
                results.append({"walletId": wid, "error": str(e),
                                "upstream_status": getattr(e, "status", None)})
            except Exception:
                _log.exception("conciliacao-mov /movimentar-batch wallet %s", wid)
                results.append({"walletId": wid, "error": "falha ao projetar"})
    finally:
        # simetria com /movimentar: o lote lê com overlay=None (injeta blobs); garante
        # que a thread reusada não vaze snapshot mesmo se um dia o loop passar a setá-lo.
        _tl.overlay = None
    return jsonify({"results": results})


def _xlsx_rows_for(result):
    """Linhas no formato do upload (Data/Carteira/Ativo/Quant/PU/SaldoBruto/Caixa/
    Moeda) a partir de um movement dict: securities + 1 linha de caixa."""
    out = []
    wid = result.get("walletId")
    td = result.get("targetDate")
    cur = result.get("currencyId") or "BRL"
    for r in result.get("rows", []):
        lots = r.get("lots")
        if lots and len(lots) > 1:
            # >1 unprocessedId no mesmo securityId → MANTÉM as linhas SEPARADAS no
            # upload (não concatena): uma linha por lote, com a qtd/saldo já divididos.
            for l in lots:
                out.append({"date": td, "walletId": wid,
                            "ativo": l.get("unprocessedId") or r.get("securityName") or "",
                            "quantity": l.get("quantity") or 0, "pu": l.get("pu") or 0,
                            "balance": l.get("balance") or 0, "caixa": False, "currencyId": cur})
            continue
        out.append({"date": td, "walletId": wid,
                    "ativo": r.get("unprocessedId") or r.get("securityName") or "",
                    "quantity": r.get("quantity") or 0, "pu": r.get("pu") or 0,
                    "balance": r.get("balance") or 0, "caixa": False, "currencyId": cur})
    out.append({"date": td, "walletId": wid, "ativo": "Caixa", "quantity": 0, "pu": 0,
                "balance": (result.get("cash") or {}).get("new") or 0, "caixa": True,
                "currencyId": cur})
    return out


def _build_xlsx(rows):
    """Workbook .xlsx com as colunas do upstream (mesmo formato de Repetir/Carteira)."""
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Posicoes"
    ws.append(["Data", "Carteira", "Ativo", "Quant", "PU", "SaldoBruto", "Caixa", "Moeda"])
    for r in rows:
        is_cash = bool(r.get("caixa"))
        ws.append([r.get("date") or "", r.get("walletId") or "",
                   ("Caixa" if is_cash else (r.get("ativo") or "")),
                   (0 if is_cash else (r.get("quantity") or 0)),
                   (0 if is_cash else (r.get("pu") or 0)),
                   r.get("balance") or 0, "Sim" if is_cash else "Não",
                   r.get("currencyId") or ""])
    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _projection_batch(data):
    """`(company_id, source, target, [movement...], err_response)` p/ as rotas em lote
    (xlsx/apply/envios). Faz o prefetch EM LOTE (como `/movimentar-batch`) e reprojeta
    cada carteira lendo do cache FRESCO (`use_cache=True`) → ~9 round-trips fixos em vez
    de ~10·N. O prefetch é SEMPRE fresco (não reusa projeção anterior), então enxerga
    escritas já feitas (IRRF/provisões/preços/recon) — idempotente. Reusa o prólogo
    (validação + prefetch) do `_batch_prologue`; só o LOOP difere do `/movimentar-batch`
    (aqui DESCARTA carteiras com erro; lá as expõe como `{walletId, error}`)."""
    (company_id, wallet_ids, source_date, target_date,
     shared_records, shared_events, err) = _batch_prologue(data)
    if err:
        return None, None, None, None, err
    results = []
    try:
        for wid in wallet_ids:
            try:
                res = _build_movement_for_wallet(
                    company_id, wid, source_date, target_date, use_cache=True,
                    shared_price_records=shared_records, shared_events=shared_events)
                if not res.get("error"):
                    results.append(res)
            except Exception:
                _log.exception("conciliacao-mov batch projection failed %s", wid)
    finally:
        _tl.overlay = None   # simetria defensiva (alimenta rotas de ESCRITA: xlsx/apply)
    return company_id, source_date, target_date, results, None


@bp.route("/api/conciliacao-mov/xlsx", methods=["POST"])
def download_xlsx():
    """Baixa um .xlsx com a posição projetada das carteiras selecionadas."""
    data = request.get_json() or {}
    company_id, _sd, target_date, results, err = _projection_batch(data)
    if err:
        return err
    rows = [x for res in results for x in _xlsx_rows_for(res)]
    if not rows:
        return jsonify({"error": "nada a exportar"}), 400
    content = _build_xlsx(rows)
    fname = f"conciliacao_mov_{company_id}_{target_date}.xlsx"
    return Response(
        content,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@bp.route("/api/conciliacao-mov/apply", methods=["POST"])
def apply_upload():
    """Envia a unprocessed projetada (data-alvo) ao Beehus (upload de arquivo).
    AÇÃO DESTRUTIVA no sistema origem — o front pede confirmação."""
    data = request.get_json() or {}
    company_id, _sd, target_date, results, err = _projection_batch(data)
    if err:
        return err
    rows = [x for res in results for x in _xlsx_rows_for(res)]
    if not rows:
        return jsonify({"error": "nada a enviar"}), 400
    content = _build_xlsx(rows)
    try:
        upstream = _api_upload_unprocessed(
            company_id=company_id, file_bytes=content,
            filename=f"conciliacao_mov_{company_id}_{target_date}.xlsx")
    except (BeehusAuthError, BeehusAPIError) as e:
        return jsonify({"error": str(e), "upstream_status": getattr(e, "status", None),
                        "upstream_body": getattr(e, "body", None)}), 502
    return jsonify(_sanitize({"ok": True, "wallets": len(results), "rows": len(rows),
                              "upstream": upstream}))


@bp.route("/api/conciliacao-mov/provisions", methods=["POST"])
def send_provisions():
    """Envia ao Beehus as provisões calculadas das carteiras selecionadas
    (`create_provision`) — tanto as do Passo 6 (buySell-offset in-window) quanto
    as de AJUSTE por divergência de quantidade (passo 3, `amountDifference`).
    AÇÃO DESTRUTIVA — o front pede confirmação."""
    data = request.get_json() or {}
    company_id, _sd, _td, results, err = _projection_batch(data)
    if err:
        return err
    created, failed, skipped, errors = 0, 0, 0, []
    for res in results:
        wid = res.get("walletId")
        currency_id = res.get("currencyId") or "BRL"
        for p in (res.get("provisions", []) + res.get("reconProvisions", [])):
            # Guardrail anti-duplicação: provisão de dividendo/JCP (Passo 6.5) já
            # coberta por uma provisão OFICIAL do sistema NÃO é reenviada (duplicaria);
            # ela segue só no NAV simulado (diagnóstico).
            if p.get("coveredByOfficial"):
                skipped += 1
                continue
            try:
                _api_create_provision(
                    company_id=company_id, wallet_id=wid,
                    balance=_num(p.get("balance")),
                    initial_date=p["initialDate"], liquidation_date=p["liquidationDate"],
                    provision_type=p.get("provisionType") or "buySell",
                    provision_source=_valid_prov_source(p.get("provisionSource")),
                    currency_id=currency_id,
                    description=p.get("description") or "",
                    security_id=p.get("securityId") or None)
                created += 1
            except (BeehusAuthError, BeehusAPIError) as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(str(e))
    return jsonify({"ok": failed == 0, "created": created, "skipped": skipped,
                    "failed": failed, "errors": errors})


# provisionSource ACEITOS pela API ao criar provisão (senão 400 "valor inválido").
# Fontes internas/importadas fora desta lista (ex.: `xml-5`, `corporate-actions`) NÃO
# podem ser reenviadas verbatim.
_VALID_PROVISION_SOURCES = {"adjustments", "performanceFee", "managementFee",
                            "amountDifference", "transaction", "xml"}


def _valid_prov_source(src):
    """Normaliza `provisionSource` p/ um valor ACEITO pela API. Fora da lista →
    `adjustments` (a provisão criada aqui é um ajuste feito pelo operador, não a
    original importada). Ex.: `xml-5`/`corporate-actions` → `adjustments`."""
    s = (src or "").strip()
    return s if s in _VALID_PROVISION_SOURCES else "adjustments"


@bp.route("/api/conciliacao-mov/shift-provisions", methods=["POST"])
def shift_provisions():
    """Desloca para a DATA-ALVO as provisões que LIQUIDAM na data-alvo
    (`liquidatingProvisions`), SELECIONADAS pelo usuário no modal. Para cada
    provisão escolhida, cria uma CÓPIA (a original é mantida) com:
      • initialDate      = data-alvo
      • liquidationDate  = data-alvo + 1 dia útil
      • demais campos (balance, provisionType, provisionSource, securityId,
        description) idênticos à original.

    `selected` = {walletId: [provisionId, ...]} — os ids são os `_id` originais
    (campo `id` das entradas de `liquidatingProvisions`). Reprojeta no servidor e
    casa por id (idempotência do resto do fluxo). Guardrail anti-duplicação: pula
    quando já existe uma provisão OFICIAL no alvo com mesmo ativo/tipo, initialDate
    na data-alvo e saldo semelhante (uma cópia já criada num run anterior).
    AÇÃO DESTRUTIVA — o front pede confirmação."""
    data = request.get_json() or {}
    company_id, _sd, target_date, results, err = _projection_batch(data)
    if err:
        return err
    selected = data.get("selected") or {}
    liq_date = _next_biz_day(target_date)
    created, skipped, failed, errors = 0, 0, 0, []
    for res in results:
        wid = res.get("walletId")
        want = {str(x) for x in (selected.get(wid) or []) if x}
        if not want:
            continue
        currency_id = res.get("currencyId") or "BRL"
        # Provisões OFICIAIS já na data-alvo — guardrail contra recriar a cópia.
        off = res.get("officialProvisions", []) or []

        def _already(sid, ptype, bal):
            for o in off:
                if (str(o.get("initialDate") or "")[:10] == target_date
                        and str(o.get("securityId") or "") == str(sid or "")
                        and (o.get("provisionType") or "") == (ptype or "")
                        and abs(_num(o.get("balance")) - _num(bal)) < 0.01):
                    return True
            return False

        for p in res.get("liquidatingProvisions", []) or []:
            if str(p.get("id") or "") not in want:
                continue
            sid = p.get("securityId") or None
            ptype = p.get("provisionType") or "buySell"
            bal = _num(p.get("balance"))
            if _already(sid, ptype, bal):
                skipped += 1
                continue
            try:
                _api_create_provision(
                    company_id=company_id, wallet_id=wid, balance=bal,
                    initial_date=target_date, liquidation_date=liq_date,
                    provision_type=ptype,
                    provision_source=_valid_prov_source(p.get("provisionSource")),
                    currency_id=currency_id,
                    description=p.get("description") or "",
                    security_id=sid)
                created += 1
            except (BeehusAuthError, BeehusAPIError) as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(str(e))
    return jsonify({"ok": failed == 0, "created": created, "skipped": skipped,
                    "failed": failed, "errors": errors, "liquidationDate": liq_date})


@bp.route("/api/conciliacao-mov/reconcile-txn", methods=["POST"])
def send_recon_txns():
    """Cria no Beehus as TRANSAÇÕES de ajuste (buySell) sugeridas pela regra
    caixa-âncora quando o caixa NÃO bate (transação ausente/errada). AÇÃO
    DESTRUTIVA — o front pede confirmação."""
    data = request.get_json() or {}
    company_id, _sd, _td, results, err = _projection_batch(data)
    if err:
        return err
    created, failed, errors = 0, 0, []
    for res in results:
        wid = res.get("walletId")
        currency_id = res.get("currencyId") or "BRL"
        entity_id = res.get("entityId") or None   # transação de carteira exige entityId (senão 502)
        for t in res.get("reconTransactions", []):
            try:
                _api_create_transaction(
                    company_id=company_id, wallet_id=wid, entity_id=entity_id,
                    balance=_num(t.get("balance")),
                    operation_date=t.get("operationDate"), liquidation_date=t.get("liquidationDate"),
                    transaction_type=t.get("beehusTransactionType") or "buySell",
                    currency_id=currency_id, description=t.get("description") or "",
                    security_id=t.get("securityId") or None,
                    quantity=_num(t.get("quantity")), price=_num(t.get("price")))
                created += 1
            except (BeehusAuthError, BeehusAPIError) as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(str(e))
    return jsonify({"ok": failed == 0, "created": created, "failed": failed, "errors": errors})


@bp.route("/api/conciliacao-mov/irrf", methods=["POST"])
def send_irrf():
    """Cria no Beehus as transações de IRRF AUSENTES (tipo `taxes`) dos resgates
    de fundo das carteiras selecionadas. Só envia as `not covered` (sem `taxes`/
    `bzFundTaxes` existente). AÇÃO DESTRUTIVA — o front pede confirmação."""
    data = request.get_json() or {}
    company_id, _sd, target_date, results, err = _projection_batch(data)
    if err:
        return err
    created, failed, errors = 0, 0, []
    for res in results:
        wid = res.get("walletId")
        currency_id = res.get("currencyId") or "BRL"
        entity_id = res.get("entityId") or None   # transação de carteira exige entityId (senão 502)
        for e in res.get("irrf", []):
            if e.get("covered"):
                continue   # já existe taxes/bzFundTaxes cobrindo — não duplicar
            # Defesa: IRRF é débito (< 0). Nunca criar um `taxes` ≥ 0 (seria
            # "restituição"); valor não-negativo só aparece por agregação inconsistente.
            if _num(e.get("irrf")) >= 0:
                continue
            try:
                _api_create_transaction(
                    company_id=company_id, wallet_id=wid, entity_id=entity_id,
                    balance=_num(e.get("irrf")),
                    operation_date=e.get("operationDate") or target_date,
                    liquidation_date=e.get("liquidationDate") or target_date,
                    transaction_type="taxes", currency_id=currency_id,
                    description=e.get("description") or "IRRF resgate de fundo",
                    security_id=e.get("securityId") or None)
                created += 1
            except (BeehusAuthError, BeehusAPIError) as ex:
                failed += 1
                if len(errors) < 5:
                    errors.append(str(ex))
    return jsonify({"ok": failed == 0, "created": created, "failed": failed, "errors": errors})


@bp.route("/api/conciliacao-mov/execution-prices", methods=["POST"])
def send_execution_prices():
    """Sobe ao Beehus os preços de execução CALCULADOS dos ativos cujo
    `executionPrice` no sistema apenas repetia o PU (placeholder) ou estava ausente.
    PATCH no record existente (corrige no lugar); cria se não houver. AÇÃO
    DESTRUTIVA — o front pede confirmação."""
    data = request.get_json() or {}
    company_id, _sd, target_date, results, err = _projection_batch(data)
    if err:
        return err
    updated, created, failed, errors = 0, 0, 0, []
    for res in results:
        wid = res.get("walletId")
        for f in res.get("executionPriceFixes", []):
            price = _num(f.get("calculatedPrice"))
            try:
                if f.get("recordId"):
                    _api_update_execution_price(f["recordId"], price)
                    updated += 1
                else:
                    _api_create_execution_price(
                        company_id=company_id, wallet_id=wid,
                        security_id=f.get("securityId"),
                        position_date=f.get("positionDate") or target_date,
                        execution_price=price)
                    created += 1
            except (BeehusAuthError, BeehusAPIError) as e:
                failed += 1
                if len(errors) < 5:
                    errors.append(str(e))
    return jsonify({"ok": failed == 0, "updated": updated, "created": created,
                    "failed": failed, "errors": errors})


@bp.route("/api/conciliacao-mov/gains-expenses", methods=["POST"])
def create_gains_expenses():
    """Opção 1 da liquidação stockETF/B3: cria UMA transação de AJUSTE DE CONTRIBUIÇÃO
    (`contributionAdjustment`) na data-alvo p/ a diferença entre as provisões e a
    liquidação consolidada da B3. Esse tipo é CASH-NEUTRO (está em `_CASH_EXCLUDED_TYPES`):
    NÃO entra no caixa projetado, só na contribuição — então não há divergência de caixa a
    omitir (o caixa oficial já reflete só o líquido da B3). A transação carrega o securityId
    do ativo genérico **"Liquidação B3"** (`_B3_LIQ_SECURITY`) — é por esse ativo que a
    provisão (stockETF) casa com a transação. AÇÃO DESTRUTIVA — confirm() no front.
    (A rota mantém o path `/gains-expenses` por compat; o tipo criado mudou.)
    Devolve `transactionId`."""
    data = request.get_json() or {}
    company_id = str(data.get("companyId") or "")
    wallet_id = str(data.get("walletId") or "")
    if not company_id or not company_visible(company_id):
        return jsonify({"error": "acesso negado"}), 403
    if not wallet_id:
        return jsonify({"error": "walletId obrigatório"}), 400
    balance = _num(data.get("balance"))
    date = str(data.get("date") or "")[:10]
    if not date or abs(balance) < 0.005:
        return jsonify({"error": "balance/data inválidos"}), 400
    # A transação de CARTEIRA exige `entityId` (o Beehus rejeita entityId nulo → 502).
    # Resolve server-side (não confia no corpo) — mesma fonte da projeção. currencyId idem.
    _w = resolve_wallet(wallet_id, {"entityId": 1, "currencyId": 1}, company_id=company_id) or {}
    entity_id = str(_w.get("entityId") or "")
    currency_id = data.get("currencyId") or str(_w.get("currencyId") or "") or "BRL"
    if not entity_id:
        return jsonify({"error": "entityId da carteira não resolvido"}), 400
    try:
        created = _api_create_transaction(
            company_id=company_id, wallet_id=wallet_id, entity_id=entity_id, balance=balance,
            operation_date=date, liquidation_date=date,
            transaction_type="contributionAdjustment", currency_id=currency_id,
            security_id=_B3_LIQ_SECURITY,   # ativo genérico "Liquidação B3" — casa provisão ↔ transação
            description=data.get("description") or "Ajuste de contribuição — custo de execução stockETF (B3)")
    except (BeehusAuthError, BeehusAPIError) as e:
        return jsonify({"error": str(e), "upstream_status": getattr(e, "status", None)}), 502
    created_id = beehus_catalog.id_str((created or {}).get("_id")) if isinstance(created, dict) else ""
    return jsonify({"ok": True, "transactionId": created_id})
