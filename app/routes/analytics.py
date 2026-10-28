"""Analytics: advanced SQL on the network, aggregation pipelines on volumes and reports,
and the cross-database reliability view. Read-only work uses the app_ro account."""
from flask import Blueprint, render_template, request

from app.db import mongo_aggregate, mongo_find, query
from app.services import reliability as rel

bp = Blueprint("analytics", __name__, url_prefix="/analytics")


def _sql_panels():
    return {
        # Nested query: compare each stop with the network-wide average.
        "above_average": query("""
            SELECT bus_stop_code, description, road_name, service_count,
                   (SELECT ROUND(AVG(service_count), 2) FROM v_stop_service_count) AS network_avg
            FROM v_stop_service_count
            WHERE service_count > (SELECT AVG(service_count) FROM v_stop_service_count)
            ORDER BY service_count DESC LIMIT 15""", account="ro"),
        # WITH ROLLUP adds the all-operators total row.
        "operators": query("""
            SELECT COALESCE(operator_name, 'All operators') AS operator_name,
                   COUNT(DISTINCT service_no) AS services, COUNT(*) AS directions,
                   ROUND(AVG(route_km), 1) AS avg_route_km, MAX(route_km) AS longest_km,
                   SUM(stop_count) AS stop_calls
            FROM v_service_summary
            GROUP BY operator_name WITH ROLLUP""", account="ro"),
        # Window function: top three longest routes within each operator.
        "longest": query("""
            SELECT operator_name, service_no, direction, route_km, stop_count, rnk
            FROM (SELECT operator_name, service_no, direction, route_km, stop_count,
                         RANK() OVER (PARTITION BY operator_name ORDER BY route_km DESC) AS rnk
                  FROM v_service_summary) ranked
            WHERE rnk <= 3
            ORDER BY operator_name, rnk""", account="ro"),
        "interchanges": query("SELECT * FROM v_interchange_station ORDER BY line_count DESC, station_name",
                              account="ro"),
    }


def _latest_period(collection):
    latest = mongo_find(collection, {}, sort=[("period", -1)], limit=1, projection={"period": 1})
    return latest[0]["period"] if latest else None


def _volume_panels():
    period = _latest_period("pv_bus_stop")
    if not period:
        return {"period": None, "profile": [], "busiest": []}
    profile = mongo_aggregate("pv_bus_stop", [
        {"$match": {"period": period}},
        {"$unwind": "$hours"},
        {"$group": {"_id": {"day_type": "$day_type", "hour": "$hours.hour"},
                    "tap_in": {"$sum": "$hours.tap_in"}, "tap_out": {"$sum": "$hours.tap_out"}}},
        {"$sort": {"_id.day_type": 1, "_id.hour": 1}},
    ])
    busiest = mongo_aggregate("pv_bus_stop", [
        {"$match": {"period": period}},
        {"$group": {"_id": "$bus_stop_code",
                    "tap_in": {"$sum": {"$sum": "$hours.tap_in"}},
                    "tap_out": {"$sum": {"$sum": "$hours.tap_out"}}}},
        {"$sort": {"tap_in": -1}},
        {"$limit": 10},
    ])
    # Cross-database: volumes come from MongoDB, the stop names from MySQL.
    if busiest:
        codes = [b["_id"] for b in busiest]
        marks = ", ".join(["%s"] * len(codes))
        names = {r["bus_stop_code"]: r["description"] for r in query(
            f"SELECT bus_stop_code, description FROM bus_stop WHERE bus_stop_code IN ({marks})", codes, account="ro")}
        for b in busiest:
            b["description"] = names.get(b["_id"], "(not in bus_stop)")
    return {"period": period, "profile": profile, "busiest": busiest}


def _od_panel(day_type, hour):
    return mongo_aggregate("od_volume", [
        {"$match": {"day_type": day_type, "hour": hour}},
        {"$group": {"_id": {"origin": "$origin_code", "destination": "$destination_code"},
                    "trips": {"$sum": "$trips"}}},
        {"$sort": {"trips": -1}},
        {"$limit": 10},
    ])


def _report_panels():
    result = mongo_aggregate("commuter_reports", [
        {"$facet": {
            "by_type": [{"$group": {"_id": "$type", "n": {"$sum": 1}}}, {"$sort": {"n": -1}}],
            "by_status": [{"$group": {"_id": "$status", "n": {"$sum": 1}}}],
            "hot_stops": [
                {"$match": {"status": "open"}},
                {"$group": {"_id": "$bus_stop_code", "reports": {"$sum": 1}, "votes": {"$sum": "$votes"},
                            "types": {"$addToSet": "$type"}}},
                {"$sort": {"reports": -1, "votes": -1}},
                {"$limit": 5},
            ],
            "by_hour": [
                {"$group": {"_id": {"$hour": {"date": "$created_at", "timezone": "Asia/Singapore"}},
                            "n": {"$sum": 1}}},
                {"$sort": {"_id": 1}},
            ],
            "severity": [
                {"$bucket": {"groupBy": "$severity", "boundaries": [1, 3, 5, 6], "default": "other",
                             "output": {"n": {"$sum": 1}}}},
            ],
        }},
    ])
    return result[0] if result else {}


@bp.route("/")
def index():
    day_type = request.args.get("day_type", "WEEKDAY")
    hour = int(request.args.get("hour", 8)) if request.args.get("hour", "8").isdigit() else 8
    od = _od_panel(day_type, hour) if request.args.get("od") else None
    return render_template("analytics.html", sql=_sql_panels(), volume=_volume_panels(),
                           reports=_report_panels(), od=od, day_type=day_type, hour=hour)


@bp.route("/reliability")
def reliability():
    days = int(request.args.get("days", 7)) if request.args.get("days", "7").isdigit() else 7
    service_no = request.args.get("service", "").strip().upper()
    ranking = rel.ranking(days)
    detail = rel.hourly(service_no, days) if service_no else []
    return render_template("reliability.html", ranking=ranking, detail=detail, service_no=service_no, days=days)
