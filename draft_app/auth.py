import os
from functools import wraps
from flask import Blueprint, render_template, request, redirect, url_for, flash, session, current_app
from .config import AUTH_FILE
from .services import load_json

bp = Blueprint("auth", __name__)

def load_auth_users():
    s3_key = os.getenv("DRAFT_S3_AUTH_KEY", os.path.basename(AUTH_FILE))
    data = load_json(AUTH_FILE, default={'users': []}, s3_key=s3_key)
    users = {}
    for u in data.get('users', []):
        if 'id' in u and u['id'] is not None:
            users[str(u['id'])] = u
        else:
            print(f"Warning: Skipping user with missing or null 'id': {u}")
    return users

def require_auth(f):
    """Decorator to require authentication for routes"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login', next=request.url))
        
        # Add user info to request for easy access
        users = current_app.config.get("AUTH_USERS", {})
        user_id = session.get('user_id')
        user = users.get(user_id, {})
        request.user = user
        
        return f(*args, **kwargs)
    return decorated_function

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
