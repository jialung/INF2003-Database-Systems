"""Logged-in commuter features: favourite stops (MySQL) and crowd reports (MongoDB)."""
from datetime import datetime, timezone

import pymysql
from bson import ObjectId
from bson.errors import InvalidId
from flask import Blueprint, abort, flash, redirect, render_template, request, session, url_for
from pymongo.errors import WriteError

from app.auth import login_required, safe_redirect
from app.db import execute, mongo_find, mongo_write, query
from app.errors import db_error_message

bp = Blueprint("user", __name__)

REPORT_TYPES = ("crowding", "delay", "breakdown", "other")
CROWD_LEVELS = {"SEA": "Seats available", "SDA": "Standing available", "LSD": "Limited standing"}


# ---------------------------------------------------------------- favourites (MySQL)

@bp.route("/favourites")
@login_required
def favourites():
    rows = query("""
        SELECT f.bus_stop_code, f.label, f.added_at, s.description, s.road_name
        FROM favourite_stop f JOIN bus_stop s ON s.bus_stop_code = f.bus_stop_code
        WHERE f.user_id = %s ORDER BY f.added_at DESC""", (session["user_id"],))
    return render_template("favourites.html", favourites=rows)


@bp.route("/favourites/add", methods=["POST"])
@login_required
def add_favourite():
    code = request.form.get("bus_stop_code", "")
    try:
        execute("INSERT INTO favourite_stop (user_id, bus_stop_code, label) VALUES (%s, %s, %s)",
                (session["user_id"], code, request.form.get("label") or None))
        flash("Saved to favourites.", "success")
    except pymysql.MySQLError as exc:
        flash(db_error_message(exc), "danger")
    return safe_redirect(request.form.get("back"), url_for("user.favourites"))


@bp.route("/favourites/<code>/label", methods=["POST"])
@login_required
def label_favourite(code):
    try:
        execute("UPDATE favourite_stop SET label = %s WHERE user_id = %s AND bus_stop_code = %s",
                (request.form.get("label") or None, session["user_id"], code))
    except pymysql.MySQLError as exc:
        flash(db_error_message(exc), "danger")
    return redirect(url_for("user.favourites"))


@bp.route("/favourites/<code>/delete", methods=["POST"])
@login_required
def delete_favourite(code):
    execute("DELETE FROM favourite_stop WHERE user_id = %s AND bus_stop_code = %s", (session["user_id"], code))
    flash("Removed from favourites.", "success")
    return safe_redirect(request.form.get("back"), url_for("user.favourites"))


# ---------------------------------------------------------------- crowd reports (MongoDB)

def _object_id(report_id):
    try:
        return ObjectId(report_id)
    except InvalidId:
        abort(404)


def _owned_report(report_id):
    report = mongo_find("commuter_reports", {"_id": _object_id(report_id)}, limit=1)
    if not report:
        abort(404)
    if report[0]["user_id"] != session["user_id"] and session.get("role") != "admin":
        abort(403)
    return report[0]


@bp.route("/reports")
def reports():
    filter_ = {}
    status = request.args.get("status", "open")
    if status in ("open", "resolved"):
        filter_["status"] = status
    if request.args.get("type") in REPORT_TYPES:
        filter_["type"] = request.args["type"]
    if request.args.get("stop"):
        filter_["bus_stop_code"] = request.args["stop"].strip()
    if request.args.get("text"):
        filter_["$text"] = {"$search": request.args["text"]}
    rows = mongo_find("commuter_reports", filter_, sort=[("created_at", -1)], limit=100)
    return render_template("reports.html", reports=rows, types=REPORT_TYPES, crowd_levels=CROWD_LEVELS,
                           args=request.args, status=status)


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@bp.route("/reports/new", methods=["GET", "POST"])
@login_required
def new_report():
    form = request.form if request.method == "POST" else request.args
    if request.method == "POST":
        code = form.get("bus_stop_code", "").strip()
        stop = query("SELECT latitude, longitude FROM bus_stop WHERE bus_stop_code = %s", (code,), one=True)
        if not stop:
            flash("Unknown bus stop code.", "danger")
            return render_template("report_form.html", form=form, types=REPORT_TYPES, crowd_levels=CROWD_LEVELS)
        doc = {
            "user_id": session["user_id"],
            "username": session["username"],
            "bus_stop_code": code,
            "type": form.get("type"),
            "severity": _int(form.get("severity")),
            "status": "open",
            "votes": 0,
            "voters": [],
            # Location comes from MySQL, so reports can be found with geospatial queries too.
            "location": {"type": "Point", "coordinates": [float(stop["longitude"]), float(stop["latitude"])]},
            "created_at": datetime.now(timezone.utc),
        }
        # Each type stores only the fields that make sense for it; the validator enforces them.
        if form.get("service_no"):
            doc["service_no"] = form["service_no"].strip().upper()
        if form.get("comment"):
            doc["comment"] = form["comment"].strip()
        if doc["type"] == "crowding" and form.get("crowd_level"):
            doc["crowd_level"] = form["crowd_level"]
        if doc["type"] == "delay" and _int(form.get("delay_min")) is not None:
            doc["delay_min"] = _int(form["delay_min"])
        try:
            mongo_write("commuter_reports", "insert_one", doc)
        except WriteError as exc:
            flash(db_error_message(exc), "danger")
            return render_template("report_form.html", form=form, types=REPORT_TYPES, crowd_levels=CROWD_LEVELS)
        flash("Thanks, your report is live.", "success")
        return redirect(url_for("main.stop", code=code))
    return render_template("report_form.html", form=form, types=REPORT_TYPES, crowd_levels=CROWD_LEVELS)


@bp.route("/reports/<report_id>/vote", methods=["POST"])
@login_required
def vote(report_id):
    # The voters filter makes the vote idempotent: one atomic update, no read-then-write race.
    result = mongo_write("commuter_reports", "update_one",
                         {"_id": _object_id(report_id), "voters": {"$ne": session["user_id"]}},
                         {"$addToSet": {"voters": session["user_id"]}, "$inc": {"votes": 1}})
    if not result.modified_count:
        flash("You have already confirmed this report.", "info")
    return safe_redirect(request.form.get("back"), url_for("user.reports"))


@bp.route("/reports/<report_id>/resolve", methods=["POST"])
@login_required
def resolve(report_id):
    report = _owned_report(report_id)
    mongo_write("commuter_reports", "update_one", {"_id": report["_id"]},
                {"$set": {"status": "resolved", "resolved_at": datetime.now(timezone.utc)}})
    flash("Report marked as resolved.", "success")
    return safe_redirect(request.form.get("back"), url_for("user.reports"))


@bp.route("/reports/<report_id>/delete", methods=["POST"])
@login_required
def delete_report(report_id):
    report = _owned_report(report_id)
    mongo_write("commuter_reports", "delete_one", {"_id": report["_id"]})
    flash("Report deleted.", "success")
    return safe_redirect(request.form.get("back"), url_for("user.reports"))
