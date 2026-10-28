"""Creates the MySQL schema, views, triggers, procedures and app accounts.

    python -m etl.init_mysql

Drops and recreates every table, so run it before loading data, not after.
Handles DELIMITER lines so the same .sql files also work in the mysql client.
"""
import re
from pathlib import Path

from app.config import Config
from app.db import mysql_connect

SQL_DIR = Path(__file__).resolve().parent.parent / "sql"


def split_statements(script):
    delimiter = ";"
    buffer = []
    for line in script.splitlines():
        stripped = line.strip()
        if stripped.upper().startswith("DELIMITER "):
            delimiter = stripped.split(None, 1)[1]
            continue
        if not buffer and (not stripped or stripped.startswith("--")):
            continue
        buffer.append(line)
        if stripped.endswith(delimiter):
            statement = "\n".join(buffer).rstrip()[: -len(delimiter)].strip()
            buffer = []
            if statement:
                yield statement
    if "".join(buffer).strip():
        yield "\n".join(buffer)


def render(script):
    # The scripts name the database "transport"; tests point MYSQL_DB at a separate one.
    script = re.sub(r"\btransport\b", Config.MYSQL_DB, script)
    return (
        script.replace("{{RW_USER}}", Config.MYSQL_RW_USER)
        .replace("{{RW_PASSWORD}}", Config.MYSQL_RW_PASSWORD)
        .replace("{{RO_USER}}", Config.MYSQL_RO_USER)
        .replace("{{RO_PASSWORD}}", Config.MYSQL_RO_PASSWORD)
    )


def main():
    conn = mysql_connect("root", database=False)
    try:
        with conn.cursor() as cur:
            for path in sorted(SQL_DIR.glob("*.sql")):
                statements = list(split_statements(render(path.read_text(encoding="utf-8"))))
                for statement in statements:
                    cur.execute(statement)
                print(f"{path.name}: {len(statements)} statements")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
