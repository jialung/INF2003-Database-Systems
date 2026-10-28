"""Loads bus stops, services and routes from LTA DataMall into MySQL.

    python -m etl.load_static              # download from DataMall, cache in data/static/
    python -m etl.load_static --cached     # reuse data/static/*.json (no API key needed)

Rows that would break a constraint (stops outside the service area, routes that point
at unknown stops) are skipped and counted rather than aborting the load.
"""
import argparse
import json
from pathlib import Path

from app.db import mysql_connect

STATIC_DIR = Path(__file__).resolve().parent.parent / "data" / "static"
DATASETS = ("BusStops", "BusServices", "BusRoutes")
LOAD_USER = "etl-load"
BATCH = 2000


def fetch(cached):
    data = {}
    if not cached:
        from etl.datamall import DataMall

        client = DataMall()
        STATIC_DIR.mkdir(parents=True, exist_ok=True)
    for name in DATASETS:
        path = STATIC_DIR / f"{name}.json"
        if cached:
            if not path.exists():
                raise SystemExit(f"{path} is missing: run once without --cached (needs DATAMALL_KEY)")
            data[name] = json.loads(path.read_text(encoding="utf-8"))
        else:
            data[name] = client.get_all(name)
            path.write_text(json.dumps(data[name]), encoding="utf-8")
        print(f"{name}: {len(data[name])} records")
    return data


def parse_freq(value):
    """'08-12' -> (8, 12); '10' -> (10, 10); '-' or '' -> (None, None)."""
    value = (value or "").strip()
    if not value or value == "-":
        return None, None
    low, _, high = value.partition("-")
    low, high = int(low), int(high or low)
    if low <= 0 or high <= 0:
        return None, None
    return min(low, high), max(low, high)


def parse_time(value):
    """'0530' -> '05:30:00'; '-' -> None."""
    value = (value or "").strip()
    if len(value) != 4 or not value.isdigit():
        return None
    return f"{value[:2]}:{value[2:]}:00"


def in_service_area(lat, lon):
    return 1.15 <= lat <= 1.60 and 103.55 <= lon <= 104.15


def stop_rows(records):
    rows, skipped = [], 0
    for r in records:
        lat, lon = float(r["Latitude"]), float(r["Longitude"])
        if not in_service_area(lat, lon):
            skipped += 1
            continue
        rows.append((r["BusStopCode"], r["RoadName"][:60], r["Description"][:80], lat, lon))
    return rows, skipped


def service_rows(records, stop_codes):
    rows = []
    for r in records:
        freqs = [parse_freq(r[k]) for k in ("AM_Peak_Freq", "AM_Offpeak_Freq", "PM_Peak_Freq", "PM_Offpeak_Freq")]
        origin = r["OriginCode"] if r["OriginCode"] in stop_codes else None
        destination = r["DestinationCode"] if r["DestinationCode"] in stop_codes else None
        rows.append((r["ServiceNo"], int(r["Direction"]), r["Operator"], r["Category"][:20], origin, destination,
                     *[v for pair in freqs for v in pair], (r.get("LoopDesc") or None)))
    return rows


def route_rows(records, stop_codes, service_keys):
    rows, seen, skipped = [], set(), 0
    for r in records:
        key = (r["ServiceNo"], int(r["Direction"]), int(r["StopSequence"]))
        if key in seen or key[:2] not in service_keys or r["BusStopCode"] not in stop_codes:
            skipped += 1
            continue
        seen.add(key)
        rows.append((*key, r["BusStopCode"], float(r["Distance"] or 0),
                     parse_time(r["WD_FirstBus"]), parse_time(r["WD_LastBus"]),
                     parse_time(r["SAT_FirstBus"]), parse_time(r["SAT_LastBus"]),
                     parse_time(r["SUN_FirstBus"]), parse_time(r["SUN_LastBus"])))
    return rows, skipped


def insert_many(cur, sql, rows):
    for i in range(0, len(rows), BATCH):
        cur.executemany(sql, rows[i:i + BATCH])


def load(data):
    stops, bad_stops = stop_rows(data["BusStops"])
    stop_codes = {row[0] for row in stops}
    services = service_rows(data["BusServices"], stop_codes)
    service_keys = {(row[0], row[1]) for row in services}
    routes, bad_routes = route_rows(data["BusRoutes"], stop_codes, service_keys)
    operators = {row[2] for row in services}

    conn = mysql_connect("root")
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.execute("SET @app_user = %s", (LOAD_USER,))
            cur.executemany(
                "INSERT IGNORE INTO operator (operator_code, operator_name) VALUES (%s, %s)",
                [(code, code) for code in operators],
            )
            insert_many(cur, """
                INSERT INTO bus_stop (bus_stop_code, road_name, description, latitude, longitude)
                VALUES (%s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE road_name = VALUES(road_name), description = VALUES(description),
                    latitude = VALUES(latitude), longitude = VALUES(longitude)""", stops)
            insert_many(cur, """
                INSERT INTO bus_service (service_no, direction, operator_code, category, origin_code,
                    destination_code, am_peak_min, am_peak_max, am_offpeak_min, am_offpeak_max,
                    pm_peak_min, pm_peak_max, pm_offpeak_min, pm_offpeak_max, loop_desc)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE operator_code = VALUES(operator_code), category = VALUES(category),
                    origin_code = VALUES(origin_code), destination_code = VALUES(destination_code),
                    am_peak_min = VALUES(am_peak_min), am_peak_max = VALUES(am_peak_max),
                    am_offpeak_min = VALUES(am_offpeak_min), am_offpeak_max = VALUES(am_offpeak_max),
                    pm_peak_min = VALUES(pm_peak_min), pm_peak_max = VALUES(pm_peak_max),
                    pm_offpeak_min = VALUES(pm_offpeak_min), pm_offpeak_max = VALUES(pm_offpeak_max),
                    loop_desc = VALUES(loop_desc)""", services)
            insert_many(cur, """
                INSERT INTO route_stop (service_no, direction, stop_sequence, bus_stop_code, distance_km,
                    wd_first_bus, wd_last_bus, sat_first_bus, sat_last_bus, sun_first_bus, sun_last_bus)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE bus_stop_code = VALUES(bus_stop_code), distance_km = VALUES(distance_km),
                    wd_first_bus = VALUES(wd_first_bus), wd_last_bus = VALUES(wd_last_bus),
                    sat_first_bus = VALUES(sat_first_bus), sat_last_bus = VALUES(sat_last_bus),
                    sun_first_bus = VALUES(sun_first_bus), sun_last_bus = VALUES(sun_last_bus)""", routes)
            # A bulk load is not an edit worth auditing; keep the log for real changes.
            cur.execute("DELETE FROM audit_log WHERE changed_by = %s", (LOAD_USER,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"bus_stop: {len(stops)} loaded, {bad_stops} skipped (outside service area)")
    print(f"bus_service: {len(services)} loaded")
    print(f"route_stop: {len(routes)} loaded, {bad_routes} skipped (duplicate or unknown stop/service)")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cached", action="store_true", help="use data/static/*.json instead of the API")
    load(fetch(parser.parse_args().cached))


if __name__ == "__main__":
    main()
