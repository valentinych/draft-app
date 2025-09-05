import os
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from .config import AUTH_FILE
from .services import load_json

bp = Blueprint("auth", __name__)

def load_auth_users():
    s3_key = os.getenv("DRAFT_S3_AUTH_KEY", os.path.basename(AUTH_FILE))
    data = load_json(AUTH_FILE, default={'users': []}, s3_key=s3_key)
    return {str(u['id']): u for u in data.get('users', [])}

@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html")
    uid = request.form.get('id', '').strip()
    pwd = request.form.get('password', '').strip()
    users = current_app.config.get("AUTH_USERS", {})
    user = users.get(uid)
    if user and user.get('password') == pwd:
        session.clear()
        session['user_id'] = uid
        session['user_name'] = user.get('name')
        session['godmode'] = bool(user.get('godmode', False))
        return redirect(request.args.get('next') or url_for('home.index'))
    flash('Неверный ID или пароль.', 'error')
    return render_template('login.html')

@bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))
