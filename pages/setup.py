import ipaddress
import logging
import re

from flask import Blueprint, render_template, request, jsonify
from pymongo import MongoClient
from pymongo.uri_parser import parse_uri
import certifi
import db as db_module

bp = Blueprint("setup", __name__)
_log = logging.getLogger(__name__)

# SSRF hardening: only accept standard mongodb URIs, and reject any host that
# resolves to a literal private / link-local / loopback IP. We cannot block
# SRV records that resolve to private IPs without DNS work, but we do block
# literal-IP attempts which are the common probe shape.
_ALLOWED_SCHEMES = ("mongodb://", "mongodb+srv://")
_PRIVATE_HOSTNAME_RE = re.compile(
    r"^(localhost|.*\.local|.*\.internal)$", re.IGNORECASE
)


def _validate_mongo_uri(uri: str) -> str | None:
    """Return None if the URI passes checks; otherwise a user-safe error string."""
    if not uri:
        return "URI não pode ser vazia."
    low = uri.lower()
    if not any(low.startswith(s) for s in _ALLOWED_SCHEMES):
        return "URI inválida: use mongodb:// ou mongodb+srv://."
    try:
        parsed = parse_uri(uri)
    except Exception:
        return "URI inválida: formato não reconhecido."
    nodelist = parsed.get("nodelist") or []
    if not nodelist:
        return "URI inválida: nenhum host informado."
    for host, _port in nodelist:
        if not host:
            return "URI inválida: host vazio."
        if _PRIVATE_HOSTNAME_RE.match(host):
            return "URI inválida: host não permitido."
        # Block literal private/loopback/link-local IPs
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            continue  # hostname, not an IP — allowed through
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            return "URI inválida: endereço IP não permitido."
    return None


@bp.route("/setup")
def setup_page():
    return render_template(
        "setup.html",
        windows_user=db_module.get_windows_user(),
        already_registered=db_module.db._ready(),
    )


@bp.route("/api/setup/test-connection", methods=["POST"])
def test_connection():
    uri = (request.get_json(force=True) or {}).get("uri", "").strip()
    err = _validate_mongo_uri(uri)
    if err:
        return jsonify({"ok": False, "error": err})
    try:
        c = MongoClient(uri, serverSelectionTimeoutMS=5000, tlsCAFile=certifi.where())
        c.server_info()
        c.close()
        return jsonify({"ok": True})
    except Exception:
        _log.exception("test-connection failed")
        return jsonify({"ok": False, "error": "Conexão falhou."})


@bp.route("/api/setup/save-connection", methods=["POST"])
def save_connection():
    uri = (request.get_json(force=True) or {}).get("uri", "").strip()
    err = _validate_mongo_uri(uri)
    if err:
        return jsonify({"ok": False, "error": err})

    try:
        new_client = MongoClient(uri, serverSelectionTimeoutMS=5000, tlsCAFile=certifi.where())
        new_client.server_info()
    except Exception:
        _log.exception("save-connection: initial probe failed")
        return jsonify({"ok": False, "error": "Conexão falhou."})

    try:
        conns = db_module.load_user_connections()
        conns[db_module.get_windows_user()] = uri
        db_module.save_user_connections(conns)
        # swap_mongo_client locks the swap and closes the previous client so
        # rapid double-clicks on "Conectar" don't leak Mongo handles.
        db_module.swap_mongo_client(new_client, db_module.DB_NAME)
    except Exception:
        _log.exception("save-connection: persistence failed")
        return jsonify({"ok": False, "error": "Falha ao salvar conexão."})

    return jsonify({"ok": True})
