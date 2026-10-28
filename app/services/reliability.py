"""Service reliability: scheduled frequency (MySQL) against observed headways (MongoDB).

headway_daily is built by etl/rollup_headway.py from the arrival snapshots. The arrival
API does not say which direction a bus is travelling, so the schedule is aggregated per
service, taking the most lenient frequency across its directions.
"""
from datetime import datetime, timedelta, timezone

from app.db import mongo_aggregate, query

SGT = timezone(timedelta(hours=8))

# LTA's frequency bands: AM peak 06:30-08:30, AM off-peak 08:31-16:59,
# PM peak 17:00-19:00, PM off-peak after 19:00. Rounded to whole hours here.
BANDS = ("am_peak", "am_offpeak", "pm_peak", "pm_offpeak")


def band_for_hour(hour):
    if 6 <= hour <= 8:
        return "am_peak"
    if 9 <= hour <= 16:
        return "am_offpeak"
    if 17 <= hour <= 18:
        return "pm_peak"
    return "pm_offpeak"


def score(avg_gap, scheduled_max):
    """1.0 when buses come at least as often as promised, falling as gaps stretch."""
    if avg_gap is None or not scheduled_max:
        return None
    return 1.0 if avg_gap <= scheduled_max else round(scheduled_max / avg_gap, 3)


def _schedules(service_no=None):
    columns = ", ".join(f"MIN({b}_min) AS {b}_min, MAX({b}_max) AS {b}_max" for b in BANDS)
    sql = f"SELECT service_no, MIN(operator_code) AS operator_code, {columns} FROM bus_service"
    params = None
    if service_no:
        sql += " WHERE service_no = %s"
        params = (service_no,)
    sql += " GROUP BY service_no"
    return {r["service_no"]: r for r in query(sql, params, account="ro")}


def _observed(days, service_no=None, bus_stop_code=None):
    since = (datetime.now(SGT) - timedelta(days=days)).strftime("%Y-%m-%d")
    match = {"date": {"$gte": since}}
    if service_no:
        match["service_no"] = service_no
    if bus_stop_code:
        match["bus_stop_code"] = bus_stop_code
    return mongo_aggregate("headway_daily", [
        {"$match": match},
        {"$group": {
            "_id": {"service_no": "$service_no", "hour": "$hour"},
            "avg_gap": {"$avg": "$avg_gap_min"},
            "p90_gap": {"$max": "$p90_gap_min"},
            "samples": {"$sum": "$samples"},
        }},
        {"$sort": {"_id.service_no": 1, "_id.hour": 1}},
    ])


def hourly(service_no, days=7, bus_stop_code=None):
    """Per-hour observed gap against the scheduled band for one service."""
    schedule = _schedules(service_no).get(service_no)
    if not schedule:
        return []
    rows = []
    for r in _observed(days, service_no, bus_stop_code):
        band = band_for_hour(r["_id"]["hour"])
        rows.append({
            "hour": r["_id"]["hour"],
            "band": band,
            "scheduled_min": schedule[f"{band}_min"],
            "scheduled_max": schedule[f"{band}_max"],
            "avg_gap": round(r["avg_gap"], 1),
            "p90_gap": r["p90_gap"],
            "samples": r["samples"],
            "score": score(r["avg_gap"], schedule[f"{band}_max"]),
        })
    return rows


def ranking(days=7):
    """One row per service with its sample-weighted score, least reliable first."""
    schedules = _schedules()
    totals = {}
    for r in _observed(days):
        service = r["_id"]["service_no"]
        schedule = schedules.get(service)
        if not schedule:
            continue
        band = band_for_hour(r["_id"]["hour"])
        s = score(r["avg_gap"], schedule[f"{band}_max"])
        if s is None:
            continue
        t = totals.setdefault(service, {"service_no": service, "operator_code": schedule["operator_code"],
                                        "weighted": 0.0, "samples": 0, "worst_hour": None, "worst_score": 2})
        t["weighted"] += s * r["samples"]
        t["samples"] += r["samples"]
        if s < t["worst_score"]:
            t["worst_score"], t["worst_hour"] = s, r["_id"]["hour"]
    result = []
    for t in totals.values():
        t["score"] = round(t.pop("weighted") / t["samples"], 3)
        result.append(t)
    return sorted(result, key=lambda t: t["score"])
