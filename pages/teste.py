from flask import Blueprint, render_template

bp = Blueprint("teste", __name__)


@bp.route("/teste")
def index():
    return render_template("teste.html")
