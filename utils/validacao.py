"""Validações e conversões genéricas de identificadores e datas.

Contexto:
Helpers usados por qualquer tela que receba ids ou datas vindos do cliente.
Centralizados aqui para evitar duplicação entre blueprints (regra do CLAUDE.md:
funções de uso comum vão para utils/).
"""
import re

from bson import ObjectId
from bson.errors import InvalidId

# Identificadores aceitos: letras, dígitos, ponto, hífen e underscore.
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_.\-]+$")
# Datas ISO no formato YYYY-MM-DD.
_SAFE_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def safe_id(value):
    """Contexto:
    Valida um identificador vindo do cliente (companyId, walletId, groupingId…)
    antes de repassá-lo a consultas/serviços. Levanta ValueError se inválido;
    devolve o próprio id quando ok.

    Pseudocódigo:
      1. Confere que é string e casa com [A-Za-z0-9_.-]+.
      2. Se não casar, levanta ValueError.
      3. Caso contrário, retorna o id intacto.
    """
    if not isinstance(value, str) or not _SAFE_ID_RE.match(value):
        raise ValueError(f"invalid id: {value!r}")
    return value


def safe_date(value):
    """Contexto:
    Valida uma data ISO 'YYYY-MM-DD' vinda do cliente antes de montar faixas de
    datas / consultas. Levanta ValueError se inválida; devolve a data quando ok.

    Pseudocódigo:
      1. Confere que é string no formato YYYY-MM-DD.
      2. Se não casar, levanta ValueError.
      3. Caso contrário, retorna a data intacta.
    """
    if not isinstance(value, str) or not _SAFE_DATE_RE.match(value):
        raise ValueError(f"invalid date: {value!r}")
    return value


def to_object_id(value):
    """Contexto:
    Converte um valor em `bson.ObjectId` de forma tolerante, para consultar
    documentos por `_id`. Retorna None quando o valor não é um ObjectId válido
    (nunca levanta).

    Pseudocódigo:
      1. Tenta construir ObjectId(str(value)).
      2. Em erro de formato/tipo, retorna None.
    """
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


def to_object_ids(values):
    """Contexto:
    Converte uma coleção de valores em lista de ObjectId, descartando os
    inválidos. Útil para filtros do tipo `{$in: [...]}`. Retorna lista
    (possivelmente vazia); nunca levanta.

    Pseudocódigo:
      1. Para cada valor da entrada (ou lista vazia):
         a. Converte via to_object_id.
         b. Mantém apenas os resultados não-None.
      2. Retorna a lista acumulada.
    """
    result = []
    for value in values or []:
        oid = to_object_id(value)
        if oid is not None:
            result.append(oid)
    return result
