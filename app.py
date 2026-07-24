import logging
import os
import re
import subprocess

from flask import Flask, jsonify, request, render_template
from pages.config         import bp as config_bp
from pages.conciliacao    import bp as conciliacao_bp
# conciliacao_unprocessed agora puxa os helpers de pages.conciliacao_shared
# (API-only, sem Mongo), então não depende mais da ordem de import.
from pages.conciliacao_unprocessed import bp as conciliacao_unprocessed_bp
from pages.conciliacao_mov import bp as conciliacao_mov_bp
from pages.stubs          import bp as stubs_bp
# pages.correcoes: page removed from the sidebar, but the blueprint stays
# registered — Conciliação calls its /api/correcoes/* routes and imports its
# helper functions (load_corrections_for_wallet, etc.).
from pages.correcoes     import bp as correcoes_bp
from pages.beehus_console import bp as beehus_console_bp
from pages.controlpanel   import bp as controlpanel_bp
from pages.excecoes       import bp as excecoes_bp
from pages.carteira       import bp as carteira_bp
from pages.repetir_posicoes import bp as repetir_posicoes_bp
from pages.precificacao   import bp as precificacao_bp
import db as db_module
import auth

_log = logging.getLogger(__name__)

# Redact credentials from any Mongo URI embedded in an exception message before
# it reaches the browser. PyMongo exceptions can carry the full
# "mongodb+srv://user:password@host/..." string verbatim.
_URI_CRED_RE = re.compile(r"(mongodb(?:\+srv)?://)[^@\s/]+@", re.IGNORECASE)

def _scrub(msg):
    return _URI_CRED_RE.sub(r"\1***:***@", str(msg))

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
# The Mongo query profiler only matters when this instance talks to Mongo; skip
# the import (and its pymongo dependency) entirely when disabled.
if db_module.IDENTIFICAR_ENABLED:
    import db_profiler
    db_profiler.install(app)
# auth.install MUST run before any other before_request hook: Flask fires
# before_request handlers in registration order, and the auth gate whitelists
# /static, /bootstrap, /healthz, /favicon.ico for unauthenticated access.
auth.install(app)
app.register_blueprint(config_bp)
app.register_blueprint(conciliacao_bp)
app.register_blueprint(conciliacao_unprocessed_bp)
app.register_blueprint(conciliacao_mov_bp)
app.register_blueprint(stubs_bp)
app.register_blueprint(correcoes_bp)
app.register_blueprint(beehus_console_bp)
app.register_blueprint(controlpanel_bp)
app.register_blueprint(excecoes_bp)
app.register_blueprint(carteira_bp)
app.register_blueprint(repetir_posicoes_bp)
app.register_blueprint(precificacao_bp)


@app.route("/")
def index():
    return render_template("shell.html")


@app.after_request
def _no_store_html(resp):
    """Nunca cachear HTML. As páginas rodam dentro de um <iframe> criado por JS
    (shell.html → `?_frame=1`); como esse iframe é requisitado DEPOIS do load, um
    Ctrl+Shift+R no shell NÃO invalida o cache dele, e o Chrome servia o iframe do
    disk cache → rodava JS ANTIGO (ReferenceError `tbl`/TDZ `_scopeState` após uma
    edição de template). Sem cabeçalho de cache o browser aplicava freshness
    heurística. App local/single-user → HTML nunca deve ser cacheado. Assets
    estáticos (mimetype != text/html) seguem cacheáveis pelo handler do Flask."""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# Only reachable when this instance uses Mongo; register the handler (and import
# pymongo.errors) lazily so a Mongo-free instance never loads pymongo. The
# RuntimeError handler below still covers the "db not initialized" case.
if db_module.IDENTIFICAR_ENABLED:
    from pymongo.errors import PyMongoError

    @app.errorhandler(PyMongoError)
    def handle_mongo_error(err):
        """Friendly page when MongoDB is unreachable instead of a 500 traceback.
        The scrubbed error stays in the server log; the user-facing page does not
        leak Atlas hosts or SRV record names."""
        _log.exception("PyMongoError surfaced to user: %s", _scrub(err))
        return render_template("db_unreachable.html"), 503


@app.errorhandler(RuntimeError)
def handle_runtime_error(err):
    """_DbProxy raises RuntimeError when db is not initialized — surface the same friendly page."""
    if "Database not initialized" in str(err):
        _log.warning("db not initialized: %s", _scrub(err))
        return render_template("db_unreachable.html"), 503
    # Don't `raise err` here: with debug=True the propagated traceback would
    # render the full source/locals to the browser. Log + return a generic 500.
    _log.exception("Unhandled RuntimeError: %s", _scrub(err))
    return render_template("db_unreachable.html"), 500


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


@app.route("/api/cache/refresh", methods=["POST"])
def api_cache_refresh():
    """Invalida os caches em processo da camada API. Backing do modal "Atualizar"
    do sidebar. Tudo é invalidação (instantâneo) — o re-warm é preguiçoso na
    próxima leitura. As telas consolidadas (Painel/Conciliação/Console) leem NAV
    AO VIVO via /results, então dependem apenas do cache de REFERÊNCIA.

    `scope` (JSON body ou query param):
      - "reference": só securities/mappings/companies/entities
      - "nav":       invalida o cache navPackages de todas as empresas
      - "company":   invalida referência + navPackages de UMA empresa (`companyId`)
      - "all" (default): invalida referência + navPackages de TODAS as empresas"""
    body = request.get_json(silent=True) or {}
    scope = str(body.get("scope") or request.args.get("scope") or "all").lower()
    company_id = (body.get("companyId") or request.args.get("companyId") or "").strip()
    try:
        import beehus_catalog
        if scope == "reference":
            beehus_catalog.invalidate()
            return jsonify({"ok": True, "scope": scope})
        if scope == "nav":
            beehus_catalog.invalidate_nav()
            return jsonify({"ok": True, "scope": scope})
        if scope == "company" and company_id:
            beehus_catalog.invalidate()
            beehus_catalog.invalidate_nav(company_id)
            return jsonify({"ok": True, "scope": scope, "companyId": company_id})
        # all: limpa referência + nav de todas as empresas
        scope = "all"
        beehus_catalog.refresh()
        return jsonify({"ok": True, "scope": scope})
    except Exception as e:
        return jsonify({"ok": False, "error": _scrub(e)}), 500


@app.route("/api/cache/companies")
def api_cache_companies():
    """Lista de empresas (id+nome) para o seletor do modal "Atualizar". Usa
    company_names (API com fallback Mongo)."""
    try:
        import beehus_catalog
        names = beehus_catalog.company_names()
        companies = sorted(
            [{"id": cid, "name": nm or cid} for cid, nm in names.items()],
            key=lambda c: c["name"].lower(),
        )
        return jsonify({"companies": companies})
    except Exception as e:
        return jsonify({"companies": [], "error": _scrub(e)}), 500


@app.route("/api/update")
def api_update():
    try:
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True, text=True, cwd=_BASE_DIR, timeout=30
        )
        already_latest = "Already up to date" in result.stdout or "Já está atualizado" in result.stdout
        if result.returncode != 0:
            return jsonify({"status": "error", "message": result.stderr or "Falha no git pull"})
        if already_latest:
            return jsonify({"status": "up_to_date", "message": "Já está na versão mais recente."})
        return jsonify({"status": "updated", "message": "Código atualizado!"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


if __name__ == "__main__":
    # use_reloader=False: project lives on OneDrive with spaces in path,
    # which breaks Werkzeug's reloader on Windows (WinError 10038).
    # host=127.0.0.1: auth binds the session to this Windows user via a
    # token file in %LOCALAPPDATA% — don't expose to the LAN.
    token = auth.get_or_create_token()
    print(f"\n[SWAT] Bootstrap URL: http://127.0.0.1:5000/bootstrap?token={token}\n")
    app.run(debug=False, host="127.0.0.1", port=5000, use_reloader=False)
