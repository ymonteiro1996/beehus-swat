from flask import Blueprint, render_template

bp = Blueprint("stubs", __name__)

_PAGES = [
    ("/risco",            "risco",            "Relatórios de Risco"),
    ("/taxa-gestao",      "taxa_gestao",      "Taxa de Gestão"),
    ("/taxa-performance", "taxa_performance",  "Taxa de Performance"),
    ("/analytics",        "analytics",        "Dashboard"),
    ("/billing",          "billing",          "Billing"),
]

for _route, _active, _title in _PAGES:
    # Use default-argument capture to avoid closure over loop variables
    def _view(active=_active, title=_title):
        return render_template("stub.html", active=active, title=title)
    _view.__name__ = f"stub_{_active}"
    bp.add_url_rule(_route, view_func=_view)
