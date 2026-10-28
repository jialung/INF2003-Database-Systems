"""Admin pages: network maintenance, route editing, audit log, user roles."""
import pymysql
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from pymongo.errors import PyMongoError

from app.auth import admin_required
from app.db import callproc, execute, mongo_write, query
from app.errors import db_error_message
from app.services.places import stop_id, stop_place

bp = Blueprint("admin", __name__, url_prefix="/admin")

BANDS = ("am_peak", "am_offpeak", "pm_peak", "pm_offpeak")


def _sync_place(code):
    """MySQL has committed; mirror the stop into MongoDB. If this fails, the stop is still
    correct in MySQL and `python -m etl.sync_places` repairs the copy."""
    row = query("SELECT bus_stop_code, description, road_name, latitude, longitude FROM bus_stop "
                "WHERE bus_stop_code = %s", (code,), one=True)
    try:
        if row:
            mongo_write("places", "replace_one", {"_id": stop_id(code)}, stop_place(row), upsert=True)
        else:
            mongo_write("places", "delete_one", {"_id": stop_id(code)})
    except PyMongoError as exc:
        flash(f"Saved in MySQL, but the map copy was not updated ({exc}). Run etl.sync_places.", "warning")


# ---------------------------------------------------------------- bus stops

@bp.route("/stops")
@admin_required
def stops():
    q = request.args.get("q", "").strip()
    like = f"%{q}%"
    rows = query("""
        SELECT bus_stop_code, description, road_name, latitude, longitude, service_count
        FROM v_stop_service_count
        WHERE %s = '' OR bus_stop_code = %s OR description LIKE %s OR road_name LIKE %s
        ORDER BY bus_stop_code LIMIT 100""", (q, q, like, like))
    return render_template("admin/stops.html", stops=rows, q=q)


def _stop_values(form):
    return (form.get("road_name", "").strip(), form.get("description", "").strip(),
            form.get("latitude") or None, form.get("longitude") or None)


@bp.route("/stops/new", methods=["GET", "POST"])
@admin_required
def new_stop():
    if request.method == "POST":
        code = request.form.get("bus_stop_code", "").strip()
        try:
            execute("INSERT INTO bus_stop (bus_stop_code, road_name, description, latitude, longitude) "
                    "VALUES (%s, %s, %s, %s, %s)", (code, *_stop_values(request.form)))
        except pymysql.MySQLError as exc:
            flash(db_error_message(exc), "danger")
            return render_template("admin/stop_form.html", stop=request.form, new=True)
        _sync_place(code)
        flash(f"Stop {code} created.", "success")
        return redirect(url_for("admin.stops", q=code))
    return render_template("admin/stop_form.html", stop={}, new=True)


@bp.route("/stops/<code>/edit", methods=["GET", "POST"])
@admin_required
def edit_stop(code):
    stop = query("SELECT * FROM bus_stop WHERE bus_stop_code = %s", (code,), one=True)
    if not stop:
        abort(404)
    if request.method == "POST":
        try:
            execute("UPDATE bus_stop SET road_name = %s, description = %s, latitude = %s, longitude = %s "
                    "WHERE bus_stop_code = %s", (*_stop_values(request.form), code))
        except pymysql.MySQLError as exc:
            flash(db_error_message(exc), "danger")
            return render_template("admin/stop_form.html", stop={**stop, **request.form.to_dict()}, new=False)
        _sync_place(code)
        flash(f"Stop {code} updated.", "success")
        return redirect(url_for("admin.stops", q=code))
    return render_template("admin/stop_form.html", stop=stop, new=False)


@bp.route("/stops/<code>/delete", methods=["POST"])
@admin_required
def delete_stop(code):
    try:
        execute("DELETE FROM bus_stop WHERE bus_stop_code = %s", (code,))
    except pymysql.MySQLError as exc:
        flash(db_error_message(exc), "danger")
    else:
        _sync_place(code)
        flash(f"Stop {code} deleted.", "success")
    return redirect(url_for("admin.stops"))


# ---------------------------------------------------------------- services and routes

@bp.route("/services/<service_no>/<int:direction>", methods=["GET", "POST"])
@admin_required
def edit_service(service_no, direction):
    service = query("SELECT * FROM bus_service WHERE service_no = %s AND direction = %s",
                    (service_no, direction), one=True)
    if not service:
        abort(404)
    if request.method == "POST":
        values = [request.form.get(f"{band}_{end}") or None for band in BANDS for end in ("min", "max")]
        sets = ", ".join(f"{band}_{end} = %s" for band in BANDS for end in ("min", "max"))
        try:
            execute(f"UPDATE bus_service SET {sets} WHERE service_no = %s AND direction = %s",
                    (*values, service_no, direction))
            flash("Frequencies updated.", "success")
        except pymysql.MySQLError as exc:
            flash(db_error_message(exc), "danger")
        return redirect(url_for("admin.edit_service", service_no=service_no, direction=direction))
    stops = query("""
        SELECT r.stop_sequence, r.bus_stop_code, s.description, r.distance_km
        FROM route_stop r JOIN bus_stop s ON s.bus_stop_code = r.bus_stop_code
        WHERE r.service_no = %s AND r.direction = %s ORDER BY r.stop_sequence""", (service_no, direction))
    return render_template("admin/service.html", service=service, stops=stops, bands=BANDS)


@bp.route("/services/<service_no>/<int:direction>/stops", methods=["POST"])
@admin_required
def insert_route_stop(service_no, direction):
    try:
        callproc("sp_insert_route_stop", (service_no, direction, int(request.form["stop_sequence"]),
                                          request.form["bus_stop_code"].strip(), request.form["distance_km"]))
        flash("Stop inserted; later stops renumbered in the same transaction.", "success")
    except (pymysql.MySQLError, ValueError) as exc:
        flash(db_error_message(exc), "danger")
    return redirect(url_for("admin.edit_service", service_no=service_no, direction=direction))


@bp.route("/services/<service_no>/<int:direction>/stops/<int:seq>/delete", methods=["POST"])
@admin_required
def delete_route_stop(service_no, direction, seq):
    try:
        callproc("sp_delete_route_stop", (service_no, direction, seq))
        flash(f"Stop {seq} removed and the route renumbered.", "success")
    except pymysql.MySQLError as exc:
        flash(db_error_message(exc), "danger")
    return redirect(url_for("admin.edit_service", service_no=service_no, direction=direction))


# ---------------------------------------------------------------- audit log and users

@bp.route("/audit")
@admin_required
def audit():
    table = request.args.get("table", "")
    rows = query("""
        SELECT log_id, table_name, action, row_key, old_data, new_data, changed_by, changed_at
        FROM audit_log WHERE %s = '' OR table_name = %s
        ORDER BY log_id DESC LIMIT 200""", (table, table))
    return render_template("admin/audit.html", rows=rows, table=table)


@bp.route("/users")
@admin_required
def users():
    rows = query("""
        SELECT u.user_id, u.username, u.email, u.role, u.created_at, COUNT(f.bus_stop_code) AS favourites
        FROM user_account u LEFT JOIN favourite_stop f ON f.user_id = u.user_id
        GROUP BY u.user_id, u.username, u.email, u.role, u.created_at
        ORDER BY u.user_id""")
    return render_template("admin/users.html", users=rows)


@bp.route("/users/<int:user_id>/role", methods=["POST"])
@admin_required
def set_role(user_id):
    if user_id == session["user_id"]:
        flash("You cannot change your own role.", "warning")
    else:
        try:
            execute("UPDATE user_account SET role = %s WHERE user_id = %s", (request.form.get("role"), user_id))
            flash("Role updated.", "success")
        except pymysql.MySQLError as exc:
            flash(db_error_message(exc), "danger")
    return redirect(url_for("admin.users"))


@bp.route("/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def delete_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot delete yourself.", "warning")
    else:
        execute("DELETE FROM user_account WHERE user_id = %s", (user_id,))
        # No foreign keys across databases: keep their reports for the statistics, but anonymise them.
        mongo_write("commuter_reports", "update_many", {"user_id": user_id}, {"$set": {"username": "[deleted]"}})
        flash("User deleted. Favourites were removed by ON DELETE CASCADE; their reports were anonymised.",
              "success")
    return redirect(url_for("admin.users"))
