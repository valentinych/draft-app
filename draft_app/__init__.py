# draft_app/__init__.py
import os
from flask import Flask
from .config import SECRET_KEY, BASE_DIR
from .auth import bp as auth_bp, load_auth_users
from .home import bp as home_bp
from .ucl import bp as ucl_bp
from .epl import bp as epl_bp
from .stats import bp as stats_bp
from .state import init_ucl, init_epl
from .wishlist import bp as wishlist_bp
from .status import bp as status_bp

def create_app():
    app = Flask(__name__, template_folder=os.path.join(BASE_DIR, "templates"))
    app.secret_key = SECRET_KEY

    app.config["AUTH_USERS"] = load_auth_users()

    # Инициализация данных
    init_ucl(app)
    init_epl(app)

    # Регистрация блюпринтов
    app.register_blueprint(auth_bp)
    app.register_blueprint(home_bp)
    app.register_blueprint(ucl_bp)
    app.register_blueprint(epl_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(wishlist_bp)
    app.register_blueprint(status_bp)

    return app
