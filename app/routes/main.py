"""Commuter pages: search, stop and service details, journey planner, nearby map."""
import time

from flask import Blueprint, abort, g, jsonify, render_template, request, session

from app.db import mongo_aggregate, mongo_find, query
from app.services import arrivals

bp = Blueprint("main", __name__)

MAX_RADIUS_M = 2000


@bp.route("/")
def index():
    q = request.args.get("q", "").strip()
    stops, services = [], []
    if q:
        like = f"%{q}%"
        stops = query("""
            SELECT bus_stop_code, description, road_name, service_count
            FROM v_stop_service_count
            WHERE bus_stop_code = %s OR description LIKE %s OR road_name LIKE %s
            ORDER BY service_count DESC, description
            LIMIT 30""", (q, like, like))
        services = query("""
            SELECT service_no, direction, operator_name, category, origin_desc, destination_desc, stop_count, route_km
            FROM v_service_summary WHERE service_no = %s ORDER BY direction""", (q.upper(),))
    return render_template("index.html", q=q, stops=stops, services=services)


@bp.route("/stops/<code>")
def stop(code):
    stop_row = query("SELECT * FROM bus_stop WHERE bus_stop_code = %s", (code,), one=True)
    if not stop_row:
        abort(404)
    services = query("""
        SELECT service_no, direction, operator_code, category, stop_sequence, distance_km, wd_first_bus, wd_last_bus
        FROM v_stop_services WHERE bus_stop_code = %s
        ORDER BY CAST(service_no AS UNSIGNED), service_no, direction""", (code,))
    # Recursive CTE: how much of the network is reachable from here with one bus, or with one transfer.
    reach = query("""
        WITH RECURSIVE reach (bus_stop_code, buses) AS (
            SELECT CAST(%s AS CHAR(5)), 0
            UNION DISTINCT
            SELECT b.bus_stop_code, r.buses + 1
            FROM reach r
            JOIN route_stop a ON a.bus_stop_code = r.bus_stop_code
            JOIN route_stop b ON b.service_no = a.service_no AND b.direction = a.direction
                             AND b.stop_sequence > a.stop_sequence
            WHERE r.buses < 2
        )
        SELECT buses, COUNT(*) AS stops
        FROM (SELECT bus_stop_code, MIN(buses) AS buses FROM reach GROUP BY bus_stop_code) first_reached
        WHERE buses > 0
        GROUP BY buses ORDER BY buses""", (code,))
    snapshot = arrivals.latest(code)
    reports = mongo_find("commuter_reports", {"bus_stop_code": code, "status": "open"},
                         sort=[("created_at", -1)], limit=10)
    profile = mongo_aggregate("pv_bus_stop", [
        {"$match": {"bus_stop_code": code}},
        {"$sort": {"period": -1}},
        {"$group": {"_id": "$day_type", "period": {"$first": "$period"}, "hours": {"$first": "$hours"}}},
    ])
    is_favourite = bool(session.get("user_id")) and bool(query(
        "SELECT 1 FROM favourite_stop WHERE user_id = %s AND bus_stop_code = %s",
        (session["user_id"], code), one=True))
    return render_template("stop.html", stop=stop_row, services=services, reach=reach, snapshot=snapshot,
                           reports=reports, profile=profile, is_favourite=is_favourite)


@bp.route("/services/<service_no>")
def service(service_no):
    summary = query("SELECT * FROM v_service_summary WHERE service_no = %s ORDER BY direction", (service_no,))
    if not summary:
        abort(404)
    # LAG() gives the length of each leg; the running total is LTA's cumulative distance.
    stops = query("""
        SELECT r.direction, r.stop_sequence, r.bus_stop_code, s.description, s.road_name,
               s.latitude, s.longitude, r.distance_km, r.wd_first_bus, r.wd_last_bus,
               ROUND(r.distance_km - LAG(r.distance_km) OVER w, 1) AS leg_km
        FROM route_stop r
        JOIN bus_stop s ON s.bus_stop_code = r.bus_stop_code
        WHERE r.service_no = %s
        WINDOW w AS (PARTITION BY r.direction ORDER BY r.stop_sequence)
        ORDER BY r.direction, r.stop_sequence""", (service_no,))
    return render_template("service.html", service_no=service_no, summary=summary, stops=stops)


DIRECT_SQL = """
    WITH direct AS (
        SELECT a.service_no, a.direction, a.stop_sequence AS board_seq, b.stop_sequence AS alight_seq,
               b.stop_sequence - a.stop_sequence AS stops, b.distance_km - a.distance_km AS km
        FROM route_stop a
        JOIN route_stop b ON b.service_no = a.service_no AND b.direction = a.direction
                         AND b.stop_sequence > a.stop_sequence
        WHERE a.bus_stop_code = %(origin)s AND b.bus_stop_code = %(destination)s
    ),
    ranked AS (
        SELECT d.*, ROW_NUMBER() OVER (PARTITION BY d.service_no ORDER BY d.km) AS rn FROM direct d
    )
    SELECT r.service_no, r.direction, r.stops, ROUND(r.km, 1) AS km, bs.operator_code
    FROM ranked r
    JOIN bus_service bs ON bs.service_no = r.service_no AND bs.direction = r.direction
    WHERE r.rn = 1
    ORDER BY r.km
    LIMIT 20"""

TRANSFER_SQL = """
    WITH first_leg AS (
        SELECT a.service_no AS s1, b.bus_stop_code AS transfer_stop,
               b.distance_km - a.distance_km AS km1, b.stop_sequence - a.stop_sequence AS n1
        FROM route_stop a
        JOIN route_stop b ON b.service_no = a.service_no AND b.direction = a.direction
                         AND b.stop_sequence > a.stop_sequence
        WHERE a.bus_stop_code = %(origin)s
    ),
    second_leg AS (
        SELECT c.service_no AS s2, c.bus_stop_code AS transfer_stop,
               d.distance_km - c.distance_km AS km2, d.stop_sequence - c.stop_sequence AS n2
        FROM route_stop c
        JOIN route_stop d ON d.service_no = c.service_no AND d.direction = c.direction
                         AND d.stop_sequence > c.stop_sequence
        WHERE d.bus_stop_code = %(destination)s
    ),
    joined AS (
        SELECT f.s1, f.transfer_stop, s.s2, f.km1 + s.km2 AS total_km, f.n1 + s.n2 AS total_stops,
               ROW_NUMBER() OVER (PARTITION BY f.s1, s.s2 ORDER BY f.km1 + s.km2) AS rn
        FROM first_leg f
        JOIN second_leg s ON s.transfer_stop = f.transfer_stop AND s.s2 <> f.s1
    )
    SELECT j.s1, j.transfer_stop, t.description AS transfer_desc, j.s2,
           ROUND(j.total_km, 1) AS total_km, j.total_stops
    FROM joined j
    JOIN bus_stop t ON t.bus_stop_code = j.transfer_stop
    WHERE j.rn = 1
    ORDER BY j.total_km
    LIMIT 10"""


@bp.route("/journey")
def journey():
    origin = request.args.get("from", "").strip()
    destination = request.args.get("to", "").strip()
    ends, direct, transfers = {}, [], []
    if origin and destination:
        for row in query("SELECT bus_stop_code, description FROM bus_stop WHERE bus_stop_code IN (%s, %s)",
                         (origin, destination)):
            ends[row["bus_stop_code"]] = row["description"]
        if origin in ends and destination in ends:
            params = {"origin": origin, "destination": destination}
            direct = query(DIRECT_SQL, params)
            transfers = query(TRANSFER_SQL, params)
    return render_template("journey.html", origin=origin, destination=destination, ends=ends,
                           direct=direct, transfers=transfers)


@bp.route("/nearby")
def nearby():
    return render_template("nearby.html")


def _nearby_mongo(lat, lon, radius):
    return mongo_aggregate("places", [
        {"$geoNear": {"near": {"type": "Point", "coordinates": [lon, lat]}, "distanceField": "distance_m",
                      "maxDistance": radius, "spherical": True}},
        {"$limit": 40},
        {"$project": {"_id": 0, "kind": 1, "ref_code": 1, "name": 1,
                      "distance_m": {"$round": ["$distance_m", 0]}, "location": 1}},
    ])


def _nearby_mysql(lat, lon, radius):
    # MBRContains on a buffer lets the SPATIAL index prune; ST_Distance_Sphere gives the exact cut.
    return query("""
        SELECT bus_stop_code AS ref_code, description AS name, latitude, longitude,
               ROUND(ST_Distance_Sphere(location, ST_GeomFromText(%(p)s, 4326, 'axis-order=long-lat'))) AS distance_m
        FROM bus_stop
        WHERE MBRContains(ST_Buffer(ST_GeomFromText(%(p)s, 4326, 'axis-order=long-lat'), %(r)s), location)
          AND ST_Distance_Sphere(location, ST_GeomFromText(%(p)s, 4326, 'axis-order=long-lat')) <= %(r)s
        ORDER BY distance_m
        LIMIT 40""", {"p": f"POINT({lon} {lat})", "r": radius}, account="ro")


@bp.route("/api/nearby")
def api_nearby():
    try:
        lat, lon = float(request.args["lat"]), float(request.args["lon"])
        radius = min(float(request.args.get("radius", 500)), MAX_RADIUS_M)
    except (KeyError, ValueError):
        abort(400, "lat, lon and radius must be numbers")
    engine = request.args.get("engine", "mongo")

    started = time.perf_counter()
    if engine == "mysql":
        places = [{"kind": "bus_stop", **row, "latitude": float(row["latitude"]),
                   "longitude": float(row["longitude"]), "distance_m": float(row["distance_m"])}
                  for row in _nearby_mysql(lat, lon, radius)]
    else:
        places = []
        for p in _nearby_mongo(lat, lon, radius):
            lon_p, lat_p = p.pop("location")["coordinates"]
            places.append({**p, "latitude": lat_p, "longitude": lon_p})
    geo_ms = round((time.perf_counter() - started) * 1000, 2)

    # Cross-database step: MongoDB found the stops, MySQL knows which services call there.
    codes = [p["ref_code"] for p in places if p["kind"] == "bus_stop"]
    if codes:
        marks = ", ".join(["%s"] * len(codes))
        rows = query(f"""
            SELECT bus_stop_code, GROUP_CONCAT(DISTINCT service_no ORDER BY service_no SEPARATOR ' ') AS services
            FROM route_stop WHERE bus_stop_code IN ({marks}) GROUP BY bus_stop_code""", codes)
        services = {r["bus_stop_code"]: r["services"] for r in rows}
        for p in places:
            p["services"] = services.get(p["ref_code"], "")
    return jsonify(engine=engine, geo_ms=geo_ms, places=places, queries=g.get("queries", []))
