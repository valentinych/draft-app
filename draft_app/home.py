from flask import Blueprint, render_template
from .auth import load_auth_users
from .state import init_ucl, init_epl
from .config import BASE_DIR

bp = Blueprint("home", __name__)

@bp.route("/")
def index():
    return render_template("home.html")

# Плейсхолдер для Top-4
@bp.route("/top4")
def top4():
    return render_template("empty_draft.html", draft_name="Топ-4 Драфт")
