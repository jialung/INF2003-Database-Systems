"""Database access for MySQL and MongoDB.

Every query that goes through these helpers is timed and recorded in g.queries, and
the "Show queries" panel in base.html displays them, so the demo shows exactly which
SQL or aggregation pipeline produced each page.
"""
import json
import time

import pymysql
from flask import g, has_request_context, session
from pymongo import MongoClient

from .config import Config

_ACCOUNTS = {
    "rw": (Config.MYSQL_RW_USER, Config.MYSQL_RW_PASSWORD),
    "ro": (Config.MYSQL_RO_USER, Config.MYSQL_RO_PASSWORD),
    "root": ("root", Config.MYSQL_ROOT_PASSWORD),
}

_mongo_client = None


def mysql_connect(account="rw", database=True):
    user, password = _ACCOUNTS[account]
    return pymysql.connect(
        host=Config.MYSQL_HOST,
        port=Config.MYSQL_PORT,
        user=user,
        password=password,
        database=Config.MYSQL_DB if database else None,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True,
    )


def mongo_db():
    global _mongo_client
    if _mongo_client is None:
        _mongo_client = MongoClient(Config.MONGO_URI, tz_aware=True, serverSelectionTimeoutMS=3000)
    return _mongo_client[Config.MONGO_DB]


# ---------------------------------------------------------------- request-scoped MySQL

def get_mysql(account="rw"):
    """One connection per account per request, tagged with the logged-in user for the audit log."""
    conns = g.setdefault("mysql_conns", {})
    if account not in conns:
        conn = mysql_connect(account)
        if account == "rw":
            with conn.cursor() as cur:
                cur.execute("SET @app_user = %s", (session.get("username"),))
        conns[account] = conn
    return conns[account]


def close_connections(_exc=None):
    for conn in g.pop("mysql_conns", {}).values():
        conn.close()


def _record(db, text, started):
    if has_request_context():
        g.setdefault("queries", []).append(
            {"db": db, "text": text, "ms": round((time.perf_counter() - started) * 1000, 2)}
        )


def query(sql, params=None, one=False, account="rw"):
    conn = get_mysql(account)
    started = time.perf_counter()
    with conn.cursor() as cur:
        text = cur.mogrify(sql, params)
        cur.execute(sql, params)
        rows = cur.fetchall()
    _record("MySQL", text, started)
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql, params=None):
    """Runs a write and returns (rowcount, lastrowid)."""
    conn = get_mysql("rw")
    started = time.perf_counter()
    with conn.cursor() as cur:
        text = cur.mogrify(sql, params)
        cur.execute(sql, params)
        result = (cur.rowcount, cur.lastrowid)
    _record("MySQL", text, started)
    return result


def callproc(name, args):
    conn = get_mysql("rw")
    started = time.perf_counter()
    with conn.cursor() as cur:
        cur.callproc(name, args)
    _record("MySQL", f"CALL {name}{tuple(args)}", started)


# ---------------------------------------------------------------- logged MongoDB helpers

def _show(obj):
    return json.dumps(obj, default=str, indent=1)


def mongo_aggregate(collection, pipeline):
    started = time.perf_counter()
    rows = list(mongo_db()[collection].aggregate(pipeline))
    _record("MongoDB", f"db.{collection}.aggregate({_show(pipeline)})", started)
    return rows


def mongo_find(collection, filter_, sort=None, limit=0, projection=None):
    started = time.perf_counter()
    cursor = mongo_db()[collection].find(filter_, projection)
    if sort:
        cursor = cursor.sort(sort)
    rows = list(cursor.limit(limit))
    text = f"db.{collection}.find({_show(filter_)})"
    if sort:
        text += f".sort({_show(dict(sort))})"
    if limit:
        text += f".limit({limit})"
    _record("MongoDB", text, started)
    return rows


def mongo_write(collection, operation, *args, **kwargs):
    """Calls a write method (insert_one, update_one, delete_one, ...) and records it."""
    started = time.perf_counter()
    result = getattr(mongo_db()[collection], operation)(*args, **kwargs)
    shown = [_show(a) for a in args] + [f"{k}={_show(v)}" for k, v in kwargs.items()]
    _record("MongoDB", f"db.{collection}.{operation}({', '.join(shown)})", started)
    return result
