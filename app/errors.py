"""Turns database errors into messages a user can act on."""
import pymysql
from pymongo.errors import WriteError

MYSQL_MESSAGES = {
    1062: "That value already exists.",
    1451: "This record is still used elsewhere (for example by a route), so it cannot be deleted.",
    1452: "This refers to a record that does not exist.",
    1406: "A value is too long.",
}


def db_error_message(exc):
    if isinstance(exc, pymysql.MySQLError) and exc.args:
        code, text = exc.args[0], exc.args[1] if len(exc.args) > 1 else ""
        if code == 3819:  # CHECK constraint violated; the text names the constraint
            return f"Rejected by a database rule: {text}"
        if code == 1644:  # SIGNAL from a trigger or stored procedure
            return text
        return MYSQL_MESSAGES.get(code, f"Database error {code}: {text}")
    if isinstance(exc, WriteError) and exc.code == 121:
        return "Rejected by the collection's schema validator. Check the required fields for this report type."
    return str(exc)
