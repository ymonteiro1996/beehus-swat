"""Single-user Windows-bound authentication.

Threat model: the app binds to 127.0.0.1 only. The session token lives in
`~/.swat/session.token` (i.e. `%USERPROFILE%\\.swat\\` on Windows) — readable
only by the current Windows user by default. Any request without a cookie
carrying that token is rejected. SameSite=Strict on the cookie + a
Sec-Fetch-Site check on mutating requests handle CSRF from browser tabs on
other sites. Another Windows user on the same box cannot read the token
file; malware running as the same user already has full access and is out
of scope.

We avoid `%LOCALAPPDATA%\\SWAT\\` because Windows Store Python silently
redirects writes there into the UWP sandbox, which breaks the launcher
script's ability to read back the token.

The token is *not* rotated on each bootstrap — one token per install, reused
across server restarts, browser restarts, and cookie expiries. Delete the
file to force re-bootstrap.
"""
import secrets
from pathlib import Path

from flask import request, redirect, make_response, Response


_COOKIE_NAME = "swat_session"
_COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days
_WHITELIST_PREFIXES = ("/bootstrap", "/static/", "/healthz", "/favicon.ico")

_TOKEN_CACHE: str | None = None


def _token_path() -> Path:
    d = Path.home() / ".swat"
    d.mkdir(parents=True, exist_ok=True)
    return d / "session.token"


def get_or_create_token() -> str:
    """Load the existing token file or generate a new one. Result is cached in memory."""
    global _TOKEN_CACHE
    if _TOKEN_CACHE:
        return _TOKEN_CACHE
    p = _token_path()
    if p.exists():
        value = p.read_text(encoding="utf-8").strip()
        if value:
            _TOKEN_CACHE = value
            return value
    value = secrets.token_urlsafe(32)
    # Write with 0600-equivalent behavior on Windows: the containing
    # %LOCALAPPDATA%\SWAT is already restricted to the current user by default.
    p.write_text(value, encoding="utf-8")
    _TOKEN_CACHE = value
    return value


def _is_authenticated() -> bool:
    got = request.cookies.get(_COOKIE_NAME, "")
    if not got:
        return False
    return secrets.compare_digest(got, get_or_create_token())


def _set_session_cookie(resp: Response) -> Response:
    resp.set_cookie(
        _COOKIE_NAME,
        get_or_create_token(),
        max_age=_COOKIE_MAX_AGE,
        httponly=True,
        samesite="Strict",
        secure=False,  # localhost http
        path="/",
    )
    return resp


def _before_request_auth():
    """Flask before_request hook. Returns a response to short-circuit, or None."""
    path = request.path or "/"
    if any(path.startswith(p) for p in _WHITELIST_PREFIXES):
        return None

    # Defense-in-depth CSRF: reject cross-site mutating requests outright.
    # Modern browsers send Sec-Fetch-Site; absent header => non-browser client
    # (curl, another local process), which still needs the cookie below.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        sfs = request.headers.get("Sec-Fetch-Site")
        if sfs and sfs not in ("same-origin", "same-site", "none"):
            return ("forbidden: cross-site request", 403)

    if _is_authenticated():
        return None

    # Unauthenticated: redirect HTML navigations to /bootstrap, 401 everything else.
    if request.method == "GET" and request.accept_mimetypes.accept_html and not request.is_json:
        return redirect("/bootstrap")
    return ("unauthorized", 401)


_BOOTSTRAP_PAGE_HEAD = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<title>SWAT - Autenticacao</title>
<style>
  body { font-family: system-ui, -apple-system, Segoe UI, sans-serif; max-width: 640px;
         margin: 4em auto; padding: 1em; color: #111; }
  h1 { font-size: 1.4em; }
  code { background: #f3f4f6; padding: 0.1em 0.4em; border-radius: 4px; }
  input { width: 100%; padding: 0.6em; font-family: ui-monospace, Consolas, monospace;
          font-size: 0.9em; border: 1px solid #d1d5db; border-radius: 6px; box-sizing: border-box; }
  button { margin-top: 1em; padding: 0.6em 1.2em; background: #111827; color: white;
           border: 0; border-radius: 6px; cursor: pointer; font-size: 1em; }
  .err { color: #b91c1c; margin: 1em 0; }
</style>
</head><body>
<h1>Autenticação necessária</h1>
<p>Abra o SWAT pelo atalho <code>start.ps1</code>. Se já estiver aberto,
feche todas as abas e inicie novamente pelo atalho.</p>
<p>Se precisar entrar manualmente, cole o conteúdo do arquivo
<code>%USERPROFILE%\\.swat\\session.token</code> abaixo:</p>
"""

_BOOTSTRAP_PAGE_TAIL = """<form method="get" action="/bootstrap">
  <input name="token" autocomplete="off" autofocus>
  <button type="submit">Entrar</button>
</form>
</body></html>
"""


def _bootstrap_page(error_html: str = "") -> str:
    return _BOOTSTRAP_PAGE_HEAD + error_html + _BOOTSTRAP_PAGE_TAIL


def install_bootstrap_route(app):
    @app.route("/bootstrap", methods=["GET"])
    def bootstrap():
        supplied = (request.args.get("token") or "").strip()
        if supplied:
            if secrets.compare_digest(supplied, get_or_create_token()):
                return _set_session_cookie(make_response(redirect("/")))
            return _bootstrap_page('<p class="err">Token inválido.</p>'), 401
        return _bootstrap_page(), 200

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return "ok", 200


def install(app):
    """Install auth: before_request hook + /bootstrap + /healthz routes."""
    # Pre-warm the token so the file is created at startup, before any browser arrives.
    get_or_create_token()
    app.before_request(_before_request_auth)
    install_bootstrap_route(app)
