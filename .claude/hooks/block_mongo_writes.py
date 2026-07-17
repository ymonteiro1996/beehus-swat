"""Contexto:
Hook de PreToolUse do Claude Code. Roda ANTES das ferramentas Write, Edit e Bash
e bloqueia qualquer tentativa de ESCRITA direta no MongoDB (regra crítica do
CLAUDE.md: alterações de dados só via rotas homologadas da API Beehus; o Mongo é
somente leitura). Lê o JSON do hook pela stdin e devolve, também em JSON, a decisão
de permissão para o Claude Code.

Pseudocodigo:
  1. Le o JSON da stdin (tool_name + tool_input).
  2. Junta o texto relevante conforme a ferramenta:
       - Write -> conteudo do arquivo; Edit -> texto novo; Bash -> comando.
  3. Procura por chamadas de escrita do pymongo (snake_case), do shell/mongosh
     (camelCase) e por utilitarios que gravam (mongoimport/mongorestore).
  4. Se achar, responde permissionDecision=deny com uma explicacao clara.
  5. Se nao achar, sai em silencio (exit 0) e o fluxo normal segue.
"""
import json
import re
import sys

# Metodos de ESCRITA do pymongo (Python). Sao inequivocos — nao ha uso legitimo
# desses nomes fora de uma operacao de escrita no Mongo neste projeto.
_PYMONGO_WRITES = (
    "insert_one", "insert_many",
    "update_one", "update_many", "replace_one",
    "delete_one", "delete_many",
    "find_one_and_update", "find_one_and_replace", "find_one_and_delete",
    "bulk_write",
    "drop_collection", "drop_database", "drop_index", "drop_indexes",
    "create_index", "create_indexes", "create_collection",
    "rename_collection",
)

# Equivalentes do shell do Mongo (mongosh / --eval / drivers JS).
_SHELL_WRITES = (
    "insertOne", "insertMany",
    "updateOne", "updateMany", "replaceOne",
    "deleteOne", "deleteMany", "findOneAndUpdate", "findOneAndReplace",
    "findOneAndDelete", "bulkWrite",
    "dropDatabase", "createIndex", "createCollection", "renameCollection",
)

# Utilitarios de linha de comando que GRAVAM no banco.
_CLI_WRITE_TOOLS = ("mongoimport", "mongorestore")

_PY_RE = re.compile(r"\.(?:" + "|".join(_PYMONGO_WRITES) + r")\s*\(")
_SHELL_RE = re.compile(r"\.(?:" + "|".join(_SHELL_WRITES) + r")\s*\(")
_CLI_RE = re.compile(r"\b(?:" + "|".join(_CLI_WRITE_TOOLS) + r")\b")

_REASON = (
    "BLOQUEADO pela Regra 2 do CLAUDE.md: escrita direta no MongoDB nao e "
    "permitida. O Mongo e SOMENTE LEITURA (apenas como fallback). Toda alteracao "
    "de dados (criar/editar/remover) deve passar pelas rotas homologadas da API "
    "Beehus (modulos em beehus_api/). Se a operacao nao existir na API, PARE e "
    "confirme com o desenvolvedor antes de prosseguir — nao grave direto no banco."
)


def _collect_text(tool_name, tool_input):
    """Contexto:
    Extrai, de acordo com a ferramenta interceptada, o texto que deve ser
    inspecionado. Retorna uma string (vazia se nao houver o que inspecionar).

    Pseudocodigo:
      1. Write  -> devolve o conteudo que sera escrito.
      2. Edit   -> devolve o texto novo que sera inserido.
      3. Bash   -> devolve o comando que sera executado.
      4. Outras -> string vazia.
    """
    if tool_name == "Write":
        return tool_input.get("content", "") or ""
    if tool_name == "Edit":
        return tool_input.get("new_string", "") or ""
    if tool_name == "Bash":
        return tool_input.get("command", "") or ""
    return ""


def _is_mongo_write(tool_name, text):
    """Contexto:
    Decide se o texto contem uma operacao de escrita no Mongo. Retorna True/False.

    Pseudocodigo:
      1. Sempre checa os metodos de escrita do pymongo (snake_case).
      2. Se for comando de Bash, checa tambem os metodos do shell (camelCase) e os
         utilitarios que gravam (mongoimport/mongorestore).
    """
    if _PY_RE.search(text):
        return True
    if tool_name == "Bash" and (_SHELL_RE.search(text) or _CLI_RE.search(text)):
        return True
    return False


def main():
    """Contexto:
    Ponto de entrada do hook. Le o payload, decide e emite a resposta JSON.

    Pseudocodigo:
      1. Le e faz parse do JSON da stdin (falha silenciosa = libera).
      2. Coleta o texto e testa se ha escrita no Mongo.
      3. Se houver, imprime deny; senao, sai em silencio.
    """
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # Sem payload valido nao ha o que bloquear — nao atrapalha o fluxo.
        return

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input", {}) or {}
    text = _collect_text(tool_name, tool_input)

    if text and _is_mongo_write(tool_name, text):
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _REASON,
            }
        }))


if __name__ == "__main__":
    main()
