# -*- coding: utf-8 -*-
"""wallet_scope.py — escopo de carteiras por requisição (painéis Template Carteiras).

[2026-09-24, pedido do usuário] Dois painéis novos derivados do Painel de
Controle:

  • "Painel de Controle - Template Carteiras" (modo `template`): o universo
    de carteiras é o TemplateCarteiras.xlsx do ControleCargas (ver
    template_carteiras.py) — todas as empresas ou uma só.
  • "Painel de Controle - Template Carteiras (Somente SLA)" (modo
    `template_sla`): além de estar no Template, cada carteira só é
    considerada na SUA data de SLA = data de execução − Defasagem (dias
    úteis ANBIMA). Ex.: carteira D-1 rodando hoje -> só a posição de ontem.
    Defasagem "M" (mensal) conta como D-1 (decisão do usuário).

"Todos os fluxos desses painéis seguem o Template" — Transações, Processar,
NAV, Publicação, drill-downs... Em vez de duplicar cada rota, o front dos
painéis manda o escopo em CABEÇALHOS em toda chamada /api/ (ver o wrapper
de fetch em templates/base.html, que também cobre as ferramentas abertas em
iframe dentro do painel), e as rotas consultam este módulo para recortar as
carteiras/agrupamentos. Sem cabeçalho = sem escopo = comportamento original
intacto (o Painel de Controle de sempre não muda nada).

Cabeçalhos (ou query params equivalentes, para links diretos/debug):
  X-Swat-Scope          template | template_sla            (?_scope=)
  X-Swat-Run-Date       YYYY-MM-DD, data de execução (SLA)  (?_runDate=)
  X-Swat-Scope-Company  companyId opcional (recorte extra)  (?_scopeCompany=)
"""
import json
import logging
import os
import re
import threading
from datetime import date as _date, timedelta

from flask import g, has_request_context, request

import beehus_catalog
import template_carteiras
from db import atomic_write_json, company_visible, get_grouping_index, today_in_brt

logger = logging.getLogger(__name__)

MODO_TEMPLATE = "template"
MODO_SLA = "template_sla"
MODOS = (MODO_TEMPLATE, MODO_SLA)

HEADER_MODO = "X-Swat-Scope"
HEADER_DATA_EXECUCAO = "X-Swat-Run-Date"
HEADER_EMPRESA = "X-Swat-Scope-Company"

_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ── Calendário (dias úteis ANBIMA, mesmo do ControleCargas) ──────────────────

_calendario = {"carregado": False, "cal": None}
# (data, n) -> data deslocada. Só há um punhado de combinações por data de
# execução (uma por defasagem distinta), mas deslocar_du roda para cada uma
# das ~1.500 carteiras do Template — e o offset do bizdays custa ~1 ms.
_deslocamentos = {}


def _cal():
    """bizdays.Calendar("ANBIMA") carregado uma vez; None se o pacote não
    estiver instalado (cai para seg-sex sem feriados, como o resto do SWAT)."""
    if not _calendario["carregado"]:
        try:
            from bizdays import Calendar
            _calendario["cal"] = Calendar.load("ANBIMA")
        except Exception as exc:  # noqa: BLE001
            logger.warning("bizdays ANBIMA indisponível (%s) — SLA usa seg-sex sem feriados.", exc)
            _calendario["cal"] = None
        _calendario["carregado"] = True
    return _calendario["cal"]


def deslocar_du(data_iso, n):
    """Desloca `data_iso` em `n` dias úteis (n < 0 = para trás). Retorna ISO."""
    chave = (data_iso, n)
    if chave in _deslocamentos:
        return _deslocamentos[chave]
    cal = _cal()
    if cal is not None:
        res = cal.offset(data_iso, n)
        out = res.isoformat() if hasattr(res, "isoformat") else str(res)[:10]
    else:
        cur = _date.fromisoformat(data_iso)
        passo = 1 if n > 0 else -1
        restante = abs(n)
        while restante > 0:
            cur += timedelta(days=passo)
            if cur.weekday() < 5:
                restante -= 1
        out = cur.isoformat()
    _deslocamentos[chave] = out
    return out


# ── Escopo da requisição ─────────────────────────────────────────────────────

class Escopo:
    """Escopo ativo: modo, data de execução (ISO) e empresa opcional."""

    __slots__ = ("modo", "data_execucao", "empresa")

    def __init__(self, modo, data_execucao=None, empresa=""):
        self.modo = modo
        self.data_execucao = data_execucao or today_in_brt().isoformat()
        self.empresa = empresa or ""

    @property
    def sla(self):
        return self.modo == MODO_SLA


def _ler_da_requisicao():
    """Escopo a partir dos cabeçalhos/params da requisição, ou None."""
    modo = (request.headers.get(HEADER_MODO) or request.args.get("_scope") or "").strip()
    if modo not in MODOS:
        return None
    data = (request.headers.get(HEADER_DATA_EXECUCAO) or request.args.get("_runDate") or "").strip()
    if not _ISO.match(data):
        data = None
    empresa = (request.headers.get(HEADER_EMPRESA) or request.args.get("_scopeCompany") or "").strip()
    return Escopo(modo, data, empresa)


def install(app):
    """Registra o before_request que lê o escopo. Chamar DEPOIS de auth.install
    (o gate de autenticação precisa ser o primeiro before_request)."""
    @app.before_request
    def _capturar_escopo():
        g._swat_escopo = _ler_da_requisicao()


def atual():
    """Escopo ativo na requisição corrente, ou None (painel normal)."""
    if not has_request_context():
        return None
    return getattr(g, "_swat_escopo", None)


def ativo():
    return atual() is not None


# ── Universo de carteiras do escopo ──────────────────────────────────────────

# walletId -> companyId da última vez que a carteira apareceu no índice.
# [2026-09-24, achado em teste] O índice global de carteiras é montado com
# fan-out por empresa e, sob 429 da API (rajada do painel normal), sai PARCIAL
# e fica 5 min em cache — sem esta memória, metade do Template virava "órfã" e
# sumia do painel. Carteira não troca de empresa, então o último valor
# conhecido é seguro; carteira trashed sai da memória quando é vista assim.
# Persistida em data/ (cache local, fora do git) para valer também no 1º
# acesso depois de reiniciar — quando o shell abre o painel normal junto e a
# rajada dele pode derrubar metade do fan-out.
_ARQUIVO_MEMORIA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data", "template_wallet_companies.json")
_memoria_lock = threading.Lock()


def _carregar_memoria():
    try:
        with open(_ARQUIVO_MEMORIA, "r", encoding="utf-8") as f:
            dados = json.load(f)
        return {str(k): str(v) for k, v in dados.items()} if isinstance(dados, dict) else {}
    except (OSError, ValueError):
        return {}


_empresa_conhecida = _carregar_memoria()


def _carteiras_do_template():
    """(carteiras, orfas): {walletId: info+companyId} das carteiras do Template
    que existem no índice de carteiras da API (não-trashed, empresa visível), e
    a lista de walletIds do Template que não foram achados. Memorizado por
    requisição (g) — várias rotas chamam mais de uma vez."""
    if has_request_context() and hasattr(g, "_swat_template_join"):
        return g._swat_template_join
    tpl = template_carteiras.carteiras_template()
    idx = beehus_catalog.wallets_index()
    carteiras, orfas = {}, []
    mudou = False
    for wid, info in tpl.items():
        doc = idx.get(wid)
        if doc is not None:
            cid = beehus_catalog.id_str(doc.get("companyId"))
            if doc.get("trashed") or not cid:
                mudou |= _empresa_conhecida.pop(wid, None) is not None
                orfas.append(wid)
                continue
            if _empresa_conhecida.get(wid) != cid:
                _empresa_conhecida[wid] = cid
                mudou = True
        else:
            # Ausente do índice: pode ser índice parcial (429) — usa a empresa
            # já conhecida; só é órfã se nunca foi vista.
            cid = _empresa_conhecida.get(wid, "")
            if not cid:
                orfas.append(wid)
                continue
        if not company_visible(cid):
            continue
        carteiras[wid] = {**info, "companyId": cid}
    if mudou:
        with _memoria_lock:
            try:
                atomic_write_json(_ARQUIVO_MEMORIA, dict(_empresa_conhecida))
            except Exception:  # noqa: BLE001 — cache local, nunca derruba a tela
                logger.warning("Não foi possível gravar %s.", _ARQUIVO_MEMORIA)
    resultado = (carteiras, orfas)
    # Índice vazio (token ainda não pronto) não é memorizado — a próxima
    # chamada da mesma requisição tenta de novo.
    if has_request_context() and idx:
        g._swat_template_join = resultado
    return resultado


def carteiras_do_escopo(esc=None):
    """{walletId: info} do escopo: Template ∩ empresa do escopo (se houver),
    cada info com `dataSla` (data de SLA para a data de execução do escopo).
    {} quando não há escopo ativo."""
    esc = esc or atual()
    if esc is None:
        return {}
    chave = ("_swat_escopo_cart", esc.modo, esc.data_execucao, esc.empresa)
    cache = getattr(g, "_swat_cache", None) if has_request_context() else None
    if cache is not None and chave in cache:
        return cache[chave]
    base, _ = _carteiras_do_template()
    out = {}
    for wid, info in base.items():
        if esc.empresa and info["companyId"] != esc.empresa:
            continue
        out[wid] = {**info,
                    "dataSla": deslocar_du(esc.data_execucao, -info["defasagemDu"])}
    if has_request_context() and base:
        if cache is None:
            cache = g._swat_cache = {}
        cache[chave] = out
    return out


def carteiras(company_id=None, data=None, data_final=None):
    """Set de walletIds permitidos, ou None quando não há escopo (sem recorte).

    - `company_id`: restringe à empresa.
    - `data` (e opcional `data_final`): só vale no modo SLA — mantém as
      carteiras cuja data de SLA é `data` (ou cai em [data, data_final]).
      No modo Template a data não recorta nada.
    """
    esc = atual()
    if esc is None:
        return None
    cid = beehus_catalog.id_str(company_id) if company_id else ""
    ini = str(data or "")[:10]
    fim = str(data_final or "")[:10]
    out = set()
    for wid, info in carteiras_do_escopo(esc).items():
        if cid and info["companyId"] != cid:
            continue
        if esc.sla and ini:
            d = info["dataSla"]
            if fim:
                if not (ini <= d <= fim):
                    continue
            elif d != ini:
                continue
        out.add(wid)
    return out


def agrupamentos(company_id=None, data=None, data_final=None):
    """Set de groupingIds permitidos, ou None sem escopo. = agrupamentos
    listados na coluna "Agrupamentos Indexados" do Template para as carteiras
    permitidas (mesma fonte do ControleCargas), só os não-trashed que existem
    no índice (e da empresa, quando `company_id` é dado)."""
    wids = carteiras(company_id, data, data_final)
    if wids is None:
        return None
    base = carteiras_do_escopo()
    gindex = get_grouping_index()
    cid = beehus_catalog.id_str(company_id) if company_id else ""
    out = set()
    for wid in wids:
        for gid in base[wid]["agrupamentos"]:
            gi = gindex.get(gid)
            if not gi or gi.get("trashed"):
                continue
            if cid and gi.get("companyId") and gi.get("companyId") != cid:
                continue
            out.add(gid)
    return out


def empresas():
    """Set de companyIds com carteira no escopo, ou None sem escopo."""
    if not ativo():
        return None
    return {info["companyId"] for info in carteiras_do_escopo().values()}


def pares(data=None):
    """Unidades de trabalho do grid: [(companyId, data_alvo, {walletIds})].

    - Template: 1 par por empresa do escopo, com a `data` pedida.
    - SLA: 1 par por (empresa, data de SLA) distinta — a data de execução é a
      do escopo; `data` é ignorada.
    [] sem escopo."""
    esc = atual()
    if esc is None:
        return []
    grupos = {}
    for wid, info in carteiras_do_escopo(esc).items():
        alvo = info["dataSla"] if esc.sla else str(data or esc.data_execucao)[:10]
        grupos.setdefault((info["companyId"], alvo), set()).add(wid)
    return [(cid, alvo, wids) for (cid, alvo), wids in sorted(grupos.items())]


def rotulo_defasagens(wids):
    """"D-1", "D-1 · D-3"... — defasagens (como estão no Template) das carteiras."""
    base = carteiras_do_escopo()
    rotulos = set()
    for wid in wids:
        info = base.get(wid)
        if info:
            rotulos.add(info["defasagem"] or "M")
    return " · ".join(sorted(rotulos, key=lambda r: (r == "M", len(r), r)))


def filtrar(ids, permitidos):
    """Lista `ids` sem os que estão fora de `permitidos` (None = sem recorte)."""
    if permitidos is None:
        return list(ids)
    return [i for i in ids if i in permitidos]


def resumo(modo, data_execucao=None):
    """Resumo para a tela do painel (fora de requisição com escopo): total de
    carteiras do Template, por empresa, órfãs e arquivo-fonte."""
    base, orfas = _carteiras_do_template()
    por_empresa = {}
    for info in base.values():
        por_empresa[info["companyId"]] = por_empresa.get(info["companyId"], 0) + 1
    arquivo = template_carteiras.info_arquivo()
    return {
        "modo": modo,
        "dataExecucao": data_execucao,
        "totalCarteiras": len(base),
        "porEmpresa": por_empresa,
        "orfas": len(orfas),
        "arquivo": arquivo.get("caminho"),
        "atualizadoEm": arquivo.get("mtime"),
    }
