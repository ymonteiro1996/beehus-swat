"""Aviso de token Beehus vencido para a tela (TRV-01).

[2026-09-25, pedido do usuário: "Quando tentar algo e estourar o token, já mostrar na tela o pop
up de colar o token"] O erro de token nem sempre chegava à tela como 401: o `beehus_catalog`
engole `BeehusAuthError` em ~30 lugares, ~39 rotas devolvem 502 ou 200 com o erro no corpo (ex.: `/api/conciliacao-mov/rows` devolvia 200
`{"rows": []}` com o token vencido) e threads de fundo (prewarm) não têm resposta nenhuma. Olhar
só o status HTTP não resolve.

Por isso o sinal vai num CABEÇALHO "grudento": toda resposta `/api/*` (menos a própria rota de
token) sai com `X-Beehus-Token: expired` enquanto o cliente Beehus estiver sem token, com token
vencido pelo `exp` ou com o último 401 do upstream não "limpo" por um 2xx (`token_status()`).
O front (`static/js/utils/beehus_token_guard.js`, carregado no base.html e no shell.html) lê esse cabeçalho em QUALQUER fetch e abre o
pop-up de colar token. O 401 do login LOCAL (sem cookie de sessão) não ganha o cabeçalho — nele
o problema não é o token do Beehus.
"""
import logging

from flask import jsonify, request

import auth
from beehus_api import BeehusAuthError, token_status

_log = logging.getLogger(__name__)

CABECALHO = "X-Beehus-Token"
VALOR_VENCIDO = "expired"
CODIGO_ERRO = "BEEHUS_TOKEN_EXPIRED"
_ROTA_TOKEN = "/api/beehus/token"


def token_indisponivel():
    """Contexto:
    Diz se o cliente Beehus deste processo está sem token utilizável. Usado pelo hook
    `marcar_resposta` e pelo errorhandler. Retorna boolean.

    Pseudocódigo:
      1. Lê token_status() do cliente (não chama a API).
      2. Sem token, vencido pelo `exp` ou rejeitado no último 401 -> True.
    """
    estado = token_status() or {}
    return (not estado.get("loaded")) or bool(estado.get("expired")) or bool(estado.get("rejected"))


def marcar_resposta(resposta):
    """Contexto:
    Hook `after_request`: acrescenta `X-Beehus-Token: expired` às respostas `/api/*` quando o
    token não está utilizável. Retorna a própria resposta.

    Pseudocódigo:
      1. Fora de /api/, na rota de token ou sem sessão local válida -> não marca.
      2. Token indisponível -> marca o cabeçalho.
    """
    caminho = request.path or ""
    if not caminho.startswith("/api/") or caminho.startswith(_ROTA_TOKEN):
        return resposta
    if not auth._is_authenticated():
        return resposta
    if token_indisponivel():
        resposta.headers[CABECALHO] = VALOR_VENCIDO
    return resposta


def resposta_token_vencido(erro):
    """Contexto:
    Rede de segurança (`errorhandler(BeehusAuthError)`): um erro de token que escapou de toda rota
    vira 401 JSON com `error_code`, em vez de um 500. Retorna (payload, 401).

    Pseudocódigo:
      1. Monta {error, error_code: BEEHUS_TOKEN_EXPIRED}.
      2. O after_request põe o cabeçalho (a flag `rejected` já está ligada).
    """
    _log.warning("[beehus] token rejeitado/ausente (errorhandler): %s", erro)
    return jsonify({"error": str(erro), "error_code": CODIGO_ERRO}), 401


def instalar(app):
    """Contexto:
    Liga o aviso de token vencido num app Flask. Chamado 1x no app.py. Não retorna nada.

    Pseudocódigo:
      1. Registra o after_request que marca o cabeçalho.
      2. Registra o errorhandler de BeehusAuthError.
    """
    app.after_request(marcar_resposta)
    app.register_error_handler(BeehusAuthError, resposta_token_vencido)
