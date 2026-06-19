import logging
import os
import re
import subprocess

from flask import Flask, jsonify, redirect, request, render_template
from pymongo.errors import PyMongoError
from pages.config         import bp as config_bp
from pages.nav            import bp as nav_bp
from pages.conciliacao    import bp as conciliacao_bp
from pages.setup          import bp as setup_bp
from pages.stubs          import bp as stubs_bp
from pages.caixa         import bp as caixa_bp
from pages.correcoes     import bp as correcoes_bp
from pages.beehus_console import bp as beehus_console_bp
from pages.controlpanel   import bp as controlpanel_bp
from pages.excecoes       import bp as excecoes_bp
from pages.carteira       import bp as carteira_bp
from pages.repetir_posicoes import bp as repetir_posicoes_bp
from pages.precificacao   import bp as precificacao_bp
import db as db_module
import db_profiler
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
db_profiler.install(app)
# auth.install MUST run before any other before_request hook below: Flask
# fires before_request handlers in registration order, and the auth gate
# whitelists /setup, /api/setup, /static, /bootstrap, /healthz, /favicon.ico.
# If a new blueprint registers its own before_request before this line, an
# unauthenticated user hitting /setup gets a 401 instead of the setup page.
auth.install(app)
app.register_blueprint(config_bp)
app.register_blueprint(nav_bp)
app.register_blueprint(conciliacao_bp)
app.register_blueprint(setup_bp)
app.register_blueprint(stubs_bp)
app.register_blueprint(caixa_bp)
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


@app.errorhandler(PyMongoError)
def handle_mongo_error(err):
    """Render a friendly page when MongoDB is unreachable instead of a 500 traceback.
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


@app.before_request
def require_registration():
    """Redirect unregistered users to /setup before any other route is served."""
    if db_module.db._ready():
        return  # connected — proceed normally
    # Allow setup routes, auth bootstrap, healthcheck, and static files to pass through
    p = request.path
    if (p.startswith("/setup") or p.startswith("/api/setup") or p.startswith("/static")
            or p.startswith("/bootstrap") or p == "/healthz" or p == "/favicon.ico"):
        return
    return redirect("/setup")


_BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# POST (not GET): this runs `git pull`, a state change, so it must flow through
# the Sec-Fetch-Site CSRF gate in auth.py (which only checks non-GET methods).
# A GET here would also be triggerable by prefetchers/link scanners.
@app.route("/api/update", methods=["POST"])
def api_update():
    try:
        result = subprocess.run(
            ["git", "pull"],
            capture_output=True, text=True, cwd=_BASE_DIR, timeout=30
        )
        already_latest = "Already up to date" in result.stdout or "Já está atualizado" in result.stdout
        if result.returncode != 0:
            # Log details server-side; don't return raw git stderr to the client
            # (it can leak the absolute repo path, remote URL, credential hints).
            app.logger.error("git pull failed (rc=%s): %s", result.returncode, result.stderr)
            return jsonify({"status": "error", "message": "Falha ao atualizar — veja os logs do servidor."})
        if already_latest:
            return jsonify({"status": "up_to_date", "message": "Já está na versão mais recente."})
        return jsonify({"status": "updated", "message": "Código atualizado!"})
    except Exception:
        app.logger.exception("git pull error")
        return jsonify({"status": "error", "message": "Falha ao atualizar — veja os logs do servidor."})


if __name__ == "__main__":
    # use_reloader=False: project lives on OneDrive with spaces in path,
    # which breaks Werkzeug's reloader on Windows (WinError 10038).
    # host=127.0.0.1: auth binds the session to this Windows user via a
    # token file in %LOCALAPPDATA% — don't expose to the LAN.
    token = auth.get_or_create_token()
    print(f"\n[SWAT] Bootstrap URL: http://127.0.0.1:5000/bootstrap?token={token}\n")
    app.run(debug=False, host="127.0.0.1", port=5000, use_reloader=False)
