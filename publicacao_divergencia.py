# -*- coding: utf-8 -*-
"""publicacao_divergencia.py — trava de divergência da Publicação (SWAT-05).

[2026-09-25, pedido do usuário: "BUG Publicação, permitindo publicar carteiras
com divergência maior do que o selecionado" + "precisa de uma verificação
melhor por data e diferença em cada data"]

Antes a trava de |Δ| (|returnNavPerShare − returnContribution|) vivia só na
tela: o seletor escondia os agrupamentos acima do limite, mas com "Selecionadas"
vazia o run() publicava TODOS os elegíveis de cada dia, o |Δ| só era carregado
para a data inicial da faixa, e o número mostrado era o do documento do
agrupamento, não o da pior carteira. O servidor não checava nada.

Agora a checagem é do SERVIDOR (`nav_publish` em pages/beehus_console.py), dia
a dia e carteira a carteira, usando os resultados NAV daquela data:
  • [pedido do usuário, 25/09: "dois campos, um de limite por carteira e
    outro de limite por agrupamento", ambos 0,02% por padrão] o |Δ| do próprio
    agrupamento é comparado com o LIMITE DO AGRUPAMENTO e o de cada carteira
    dele com o LIMITE DA CARTEIRA;
  • bloqueia se qualquer um não passar (régua `passa_no_limite`: |Δ| < limite
    ou |Δ| exatamente 0 — é só isso que o limite 0 publica) ou se alguém não
    tiver Δ calculado (decisão do usuário: "sem Δ calculado não publica").

Carteiras de um agrupamento numa data = união de
  (a) as que o /results lista sob aquele groupingId, e
  (b) as membras do cadastro ativas na data (initialDateOnGrouping ..
      finalDateOnGrouping) — cobre a carteira que devia ter NAV e não tem.

Funções puras (sem Flask): recebem o dict de `get_nav_results` e o índice de
agrupamentos (`db.get_grouping_index()`). O mesmo cálculo alimenta o seletor
(`filter_grouping_return_deltas`), para a tela mostrar o número que o servidor
vai usar.
"""
import json
import logging
import math
import os

import beehus_catalog

logger = logging.getLogger(__name__)

_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "data", "publicacao_config.json")
# Mesmo padrão dos campos "Limite |Δ| agrupamento/carteira (%)" da tela (0.02).
_LIMITE_PADRAO_PCT = 0.02

ENTIDADE_AGRUPAMENTO = "agrupamento"
ENTIDADE_CARTEIRA = "carteira"
_CHAVE_CONFIG = {ENTIDADE_AGRUPAMENTO: "limitePadraoDeltaAgrupamentoPct",
                 ENTIDADE_CARTEIRA: "limitePadraoDeltaCarteiraPct"}

MOTIVO_ACIMA_LIMITE = "acima_limite"
MOTIVO_SEM_DELTA = "sem_delta"


# ── Limite ────────────────────────────────────────────────────────────────────

def carregar_limite_padrao_decimal(entidade):
    """Contexto:
    Limite |Δ| padrão de um tipo de entidade ("agrupamento" ou "carteira"),
    usado quando a requisição de publicação não traz o limite daquele tipo (ex.:
    o atalho "Publicar" do drill-down do Painel). Retorna decimal (0,02% ->
    0.0002).

    Pseudocódigo:
      1. Lê de data/publicacao_config.json a chave do tipo
         (`limitePadraoDeltaAgrupamentoPct` / `limitePadraoDeltaCarteiraPct`,
         em %); na falta dela, a antiga `limitePadraoDeltaPct`.
      2. Arquivo ausente, ilegível ou valor negativo -> usa 0,02% (0 vale:
         só publica divergência zero).
      3. Converte % em decimal e retorna.
    """
    percentual = _LIMITE_PADRAO_PCT
    try:
        with open(_CONFIG_FILE, encoding="utf-8") as arquivo:
            config = json.load(arquivo)
        percentual = float(config.get(_CHAVE_CONFIG[entidade],
                                      config.get("limitePadraoDeltaPct", _LIMITE_PADRAO_PCT)))
    except (OSError, ValueError, TypeError, AttributeError):
        percentual = _LIMITE_PADRAO_PCT
    if math.isnan(percentual) or math.isinf(percentual) or percentual < 0:
        percentual = _LIMITE_PADRAO_PCT
    return percentual / 100.0


def interpretar_limite(valor):
    """Contexto:
    Valida um limite |Δ| (decimal) que a tela manda no POST de publicação
    (`maxDeltaAbsAgrupamento` / `maxDeltaAbsCarteira`).
    Retorna float >= 0, ou None se o valor for inválido.

    Pseudocódigo:
      1. Recusa booleano (True viraria 1.0).
      2. Converte para float; falha -> None.
      3. Aceita número finito e >= 0. [decisão do usuário, 25/09] Limite 0 é
         válido e significa "só publica o que tem divergência ZERO" (antes, na
         tela, 0 desligava o filtro — isso não existe mais).
    """
    if isinstance(valor, bool):
        return None
    try:
        limite = float(valor)
    except (TypeError, ValueError):
        return None
    if math.isnan(limite) or math.isinf(limite) or limite < 0:
        return None
    return limite


def passa_no_limite(delta, limite):
    """Contexto:
    Régua única da trava (servidor e, espelhada, a tela): um |Δ| passa se for
    menor que o limite ou exatamente zero. Retorna boolean.

    Pseudocódigo:
      1. |Δ| == 0 -> passa (é o que "limite 0" deixa publicar).
      2. Senão, passa só se |Δ| < limite (mesma régua do seletor, que só
         mostra |Δ| < limite; |Δ| igual ao limite bloqueia).
    """
    return delta == 0 or delta < limite


# ── Δ por entidade ────────────────────────────────────────────────────────────

def delta_abs(entrada):
    """Contexto:
    |returnNavPerShare − returnContribution| de uma linha do /results (carteira
    ou agrupamento). Retorna float, ou None quando o Δ não está calculado.

    Pseudocódigo:
      1. Sem linha -> None.
      2. Os dois retornos precisam ser números (bool não conta).
      3. Retorna o módulo da diferença (NaN vira None).
    """
    if not entrada:
        return None
    rnps = entrada.get("returnNavPerShare")
    rc = entrada.get("returnContribution")
    if isinstance(rnps, bool) or isinstance(rc, bool):
        return None
    if not isinstance(rnps, (int, float)) or not isinstance(rc, (int, float)):
        return None
    diferenca = abs(float(rnps) - float(rc))
    return None if math.isnan(diferenca) else diferenca


def indexar_resultados(resultados):
    """Contexto:
    Organiza o dict de `get_nav_results` para consulta por id. Chamado 1x por
    data. Retorna {"carteiras": {wid: linha}, "agrupamentos": {gid: linha},
    "carteirasPorAgrupamento": {gid: [wid, ...]}}.

    Pseudocódigo:
      1. Para cada linha de walletsWithNavDetailed: guarda por walletId e
         anota o walletId sob o groupingId da linha.
      2. Para cada linha de groupingsDetailed: guarda por groupingId.
    """
    carteiras, agrupamentos, carteiras_por_agrupamento = {}, {}, {}
    for linha in (resultados or {}).get("walletsWithNavDetailed") or []:
        if not isinstance(linha, dict):
            continue
        wallet_id = beehus_catalog.id_str(linha.get("walletId"))
        if not wallet_id:
            continue
        carteiras.setdefault(wallet_id, linha)
        grouping_id = beehus_catalog.id_str(linha.get("groupingId"))
        if grouping_id:
            lista = carteiras_por_agrupamento.setdefault(grouping_id, [])
            if wallet_id not in lista:
                lista.append(wallet_id)
    for linha in (resultados or {}).get("groupingsDetailed") or []:
        if not isinstance(linha, dict):
            continue
        grouping_id = beehus_catalog.id_str(linha.get("groupingId"))
        if grouping_id:
            agrupamentos[grouping_id] = linha
    return {"carteiras": carteiras, "agrupamentos": agrupamentos,
            "carteirasPorAgrupamento": carteiras_por_agrupamento}


def _membro_ativo_na_data(membro, data):
    """Contexto:
    Diz se um membro do cadastro do agrupamento está ativo em `data`
    (AAAA-MM-DD). Datas ausentes = sem limite daquele lado.

    Pseudocódigo:
      1. Entrou depois da data -> False.
      2. Saiu antes da data -> False.
      3. Senão -> True.
    """
    inicio = str(membro.get("ini") or "")[:10]
    fim = str(membro.get("fim") or "")[:10]
    if inicio and inicio > data:
        return False
    if fim and fim < data:
        return False
    return True


def carteiras_do_agrupamento(grouping_id, resultados_indexados, indice_agrupamentos, data):
    """Contexto:
    Carteiras que contam para a trava de um agrupamento numa data. Retorna a
    lista de walletIds, sem repetição, na ordem em que foram achadas.

    Pseudocódigo:
      1. Começa pelas carteiras que o /results lista sob o agrupamento.
      2. Soma as membras do cadastro ativas na data (a que devia ter NAV e não
         tem entra aqui e vira "sem Δ").
    """
    carteiras = list(resultados_indexados["carteirasPorAgrupamento"].get(grouping_id) or [])
    cadastro = indice_agrupamentos.get(grouping_id) or {}
    for membro in cadastro.get("members") or []:
        wallet_id = membro.get("walletId")
        if wallet_id and wallet_id not in carteiras and _membro_ativo_na_data(membro, data):
            carteiras.append(wallet_id)
    return carteiras


def pior_divergencia_do_agrupamento(grouping_id, resultados_indexados, indice_agrupamentos,
                                    data, nomes_carteiras=None):
    """Contexto:
    Resume a divergência de um agrupamento numa data olhando o próprio
    agrupamento e cada carteira dele. Usado pela trava do servidor e pelo
    seletor da tela. Retorna {deltaAgrupamento, deltaPiorCarteira,
    piorCarteiraWalletId, piorCarteira, deltaAbs, returnNavPerShare,
    returnContribution, walletId, carteira, semDelta, semDeltaWalletId,
    semDeltaCarteira}.

    Pseudocódigo:
      1. Lista as entidades: o agrupamento + as carteiras dele na data.
      2. Para cada uma, calcula o |Δ| pela linha do /results (ausente = None).
      3. deltaAgrupamento = |Δ| do próprio agrupamento; deltaPiorCarteira =
         maior |Δ| entre as carteiras (e de quem é) — cada um comparado com o
         seu limite pela trava.
      4. deltaAbs = o maior dos dois (e de quem é: walletId None = o próprio
         agrupamento), para exibição.
      5. semDelta = alguma entidade sem Δ; guarda a primeira delas.
    """
    nomes_carteiras = nomes_carteiras or {}
    linha_agrupamento = resultados_indexados["agrupamentos"].get(grouping_id)
    entidades = [(None, linha_agrupamento)]
    for wallet_id in carteiras_do_agrupamento(grouping_id, resultados_indexados,
                                              indice_agrupamentos, data):
        entidades.append((wallet_id, resultados_indexados["carteiras"].get(wallet_id)))

    resumo = {"deltaAgrupamento": delta_abs(linha_agrupamento), "deltaPiorCarteira": None,
              "piorCarteiraWalletId": None, "piorCarteira": None,
              "deltaAbs": None, "returnNavPerShare": None, "returnContribution": None,
              "walletId": None, "carteira": None, "semDelta": False,
              "semDeltaWalletId": None, "semDeltaCarteira": None}
    for wallet_id, linha in entidades:
        delta = delta_abs(linha)
        nome = _nome_da_carteira(wallet_id, linha, nomes_carteiras)
        if delta is None:
            if not resumo["semDelta"]:
                resumo.update(semDelta=True, semDeltaWalletId=wallet_id, semDeltaCarteira=nome)
            continue
        if wallet_id is not None and (resumo["deltaPiorCarteira"] is None
                                      or delta > resumo["deltaPiorCarteira"]):
            resumo.update(deltaPiorCarteira=delta, piorCarteiraWalletId=wallet_id, piorCarteira=nome)
        if resumo["deltaAbs"] is None or delta > resumo["deltaAbs"]:
            resumo.update(deltaAbs=delta, walletId=wallet_id, carteira=nome,
                          returnNavPerShare=linha.get("returnNavPerShare"),
                          returnContribution=linha.get("returnContribution"))
    return resumo


def _nome_da_carteira(wallet_id, linha, nomes_carteiras):
    """Contexto:
    Nome legível de uma entidade para o log de bloqueio. Retorna None para o
    próprio agrupamento (walletId None).

    Pseudocódigo:
      1. Agrupamento -> None.
      2. Prefere walletName da linha do /results; senão o índice de nomes;
         senão o próprio id.
    """
    if wallet_id is None:
        return None
    return (linha or {}).get("walletName") or nomes_carteiras.get(wallet_id) or wallet_id


# ── Trava ─────────────────────────────────────────────────────────────────────

def avaliar_publicacao(grouping_ids, resultados, indice_agrupamentos, data,
                       limite_agrupamento, limite_carteira, nomes_carteiras=None):
    """Contexto:
    Decide, para UMA data, quais agrupamentos podem ser publicados. Chamado por
    `nav_publish` antes de mandar qualquer coisa ao Beehus. Retorna
    (liberados: [gid], bloqueados: [{groupingId, nome, walletId, carteira,
    delta, limite, entidade, motivo}]).

    Pseudocódigo:
      1. Indexa os resultados NAV da data.
      2. Para cada agrupamento pedido, resume a divergência (agrupamento e
         pior carteira).
      3. Alguém sem Δ -> bloqueia com motivo "sem_delta".
      4. |Δ| do agrupamento não passa no LIMITE DO AGRUPAMENTO -> bloqueia
         ("acima_limite", entidade "agrupamento").
      5. |Δ| da pior carteira não passa no LIMITE DA CARTEIRA -> bloqueia
         ("acima_limite", entidade "carteira", com a carteira).
      6. Senão, libera. (Agrupamento sem nenhuma carteira na data só passa
         pelo limite do agrupamento.)
    """
    resultados_indexados = indexar_resultados(resultados)
    liberados, bloqueados = [], []
    for grouping_id in grouping_ids:
        resumo = pior_divergencia_do_agrupamento(grouping_id, resultados_indexados,
                                                 indice_agrupamentos, data, nomes_carteiras)
        bloqueio = _motivo_de_bloqueio(resumo, limite_agrupamento, limite_carteira)
        if bloqueio:
            nome = _nome_do_agrupamento(grouping_id, resultados_indexados, indice_agrupamentos)
            bloqueados.append({"groupingId": grouping_id, "nome": nome, **bloqueio})
        else:
            liberados.append(grouping_id)
    return liberados, bloqueados


def _motivo_de_bloqueio(resumo, limite_agrupamento, limite_carteira):
    """Contexto:
    Aplica os dois limites ao resumo de UM agrupamento. Usado por
    `avaliar_publicacao`. Retorna None (passa) ou {walletId, carteira, delta,
    limite, entidade, motivo}.

    Pseudocódigo:
      1. Sem Δ em alguém -> "sem_delta" (entidade = agrupamento ou carteira).
      2. |Δ| do agrupamento não passa no limite do agrupamento -> bloqueia.
      3. |Δ| da pior carteira não passa no limite da carteira -> bloqueia.
      4. Senão -> None.
    """
    if resumo["semDelta"]:
        entidade = ENTIDADE_AGRUPAMENTO if resumo["semDeltaWalletId"] is None else ENTIDADE_CARTEIRA
        return {"walletId": resumo["semDeltaWalletId"], "carteira": resumo["semDeltaCarteira"],
                "delta": None, "limite": None, "entidade": entidade, "motivo": MOTIVO_SEM_DELTA}
    if not passa_no_limite(resumo["deltaAgrupamento"], limite_agrupamento):
        return {"walletId": None, "carteira": None, "delta": resumo["deltaAgrupamento"],
                "limite": limite_agrupamento, "entidade": ENTIDADE_AGRUPAMENTO,
                "motivo": MOTIVO_ACIMA_LIMITE}
    if resumo["deltaPiorCarteira"] is not None and not passa_no_limite(resumo["deltaPiorCarteira"], limite_carteira):
        return {"walletId": resumo["piorCarteiraWalletId"], "carteira": resumo["piorCarteira"],
                "delta": resumo["deltaPiorCarteira"], "limite": limite_carteira,
                "entidade": ENTIDADE_CARTEIRA, "motivo": MOTIVO_ACIMA_LIMITE}
    return None


def _nome_do_agrupamento(grouping_id, resultados_indexados, indice_agrupamentos):
    """Contexto:
    Nome do agrupamento para o log. Retorna string (o id, na falta de nome).

    Pseudocódigo:
      1. Tenta o nome do cadastro; depois o groupingName do /results; senão o id.
    """
    cadastro = indice_agrupamentos.get(grouping_id) or {}
    linha = resultados_indexados["agrupamentos"].get(grouping_id) or {}
    return cadastro.get("name") or linha.get("groupingName") or grouping_id


def agrupamentos_nao_publicados(resultados):
    """Contexto:
    Agrupamentos com NAV calculado e ainda não publicados na data. Usado quando
    o POST de publicação chega com `groupingIds` vazio — no contrato upstream,
    lista vazia = "todas da empresa", e isso nunca pode ir adiante sem passar
    pela trava. Retorna [gid] na ordem do /results.

    Pseudocódigo:
      1. Percorre groupingsDetailed.
      2. Mantém só published == False (estrito; ausente não conta).
    """
    ids = []
    for linha in (resultados or {}).get("groupingsDetailed") or []:
        if not isinstance(linha, dict) or linha.get("published") is not False:
            continue
        grouping_id = beehus_catalog.id_str(linha.get("groupingId"))
        if grouping_id and grouping_id not in ids:
            ids.append(grouping_id)
    return ids
