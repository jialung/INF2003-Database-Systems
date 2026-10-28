"""Accounts, sessions, role checks and CSRF protection."""
import re
import secrets
from functools import wraps

import bcrypt
import pymysql
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for

from .db import execute, query
from .errors import db_error_message

bp = Blueprint("auth", __name__)

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,30}$")


def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password, password_hash):
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def safe_redirect(target, default):
    """Redirects only to local paths, never to another site."""
    if target and target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(default)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if session.get("role") != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def init_app(app):
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def check_csrf():
        if request.method == "POST":
            sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token")
            if not sent or not secrets.compare_digest(sent, session.get("csrf", "")):
                abort(400, "Missing or invalid CSRF token")


@bp.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        email = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        if not USERNAME_RE.match(username):
            flash("Username must be 3-30 letters, digits or underscores.", "danger")
        elif len(password) < 8:
            flash("Password must be at least 8 characters.", "danger")
        else:
            try:
                execute("INSERT INTO user_account (username, email, password_hash) VALUES (%s, %s, %s)",
                        (username, email, hash_password(password)))
            except pymysql.MySQLError as exc:
                flash(db_error_message(exc), "danger")
            else:
                flash("Account created. Please log in.", "success")
                return redirect(url_for("auth.login"))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = query("SELECT user_id, username, password_hash, role FROM user_account WHERE username = %s",
                     (request.form.get("username", "").strip(),), one=True)
        if user and check_password(request.form.get("password", ""), user["password_hash"]):
            session.clear()
            session.update(user_id=user["user_id"], username=user["username"], role=user["role"])
            return safe_redirect(request.args.get("next"), url_for("main.index"))
        flash("Wrong username or password.", "danger")
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.index"))
