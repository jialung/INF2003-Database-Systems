from datetime import datetime, timedelta, timezone

from flask import Flask

from .config import Config
from .db import close_connections

SGT = timezone(timedelta(hours=8))


def sgt(value, fmt="%d %b %Y %H:%M"):
    """Formats a datetime in Singapore time; naive values are taken as UTC."""
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(SGT).strftime(fmt)


def minutes_until(value):
    minutes = int((value - datetime.now(timezone.utc)).total_seconds() // 60)
    return "Arr" if minutes <= 0 else f"{minutes} min"


def create_app(config=Config):
    app = Flask(__name__)
    app.config.from_object(config)
    app.jinja_env.filters["sgt"] = sgt
    app.jinja_env.filters["minutes_until"] = minutes_until

    from . import auth
    from .routes import admin, analytics, main, user

    auth.init_app(app)
    app.register_blueprint(auth.bp)
    app.register_blueprint(main.bp)
    app.register_blueprint(user.bp)
    app.register_blueprint(admin.bp)
    app.register_blueprint(analytics.bp)
    app.teardown_appcontext(close_connections)
    return app
