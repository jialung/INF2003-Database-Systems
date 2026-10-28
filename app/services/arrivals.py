"""Bus arrival snapshots: one MongoDB document per stop per poll, services embedded."""
from datetime import datetime, timedelta, timezone

from flask import current_app

from app.db import mongo_find, mongo_write

FRESH_FOR = timedelta(seconds=120)


def _next_bus(raw):
    if not raw or not raw.get("EstimatedArrival"):
        return None
    return {
        "eta": datetime.fromisoformat(raw["EstimatedArrival"]),
        "load": raw.get("Load") or None,
        "type": raw.get("Type") or None,
        "feature": raw.get("Feature") or None,
        "monitored": raw.get("Monitored") == 1,
        "lat": float(raw["Latitude"]) if raw.get("Latitude") not in (None, "", "0.0") else None,
        "lon": float(raw["Longitude"]) if raw.get("Longitude") not in (None, "", "0.0") else None,
    }


def snapshot_from_api(bus_stop_code, payload, ts=None):
    services = []
    for svc in payload.get("Services", []):
        upcoming = [b for b in (_next_bus(svc.get(k)) for k in ("NextBus", "NextBus2", "NextBus3")) if b]
        if upcoming:
            services.append({"service_no": svc["ServiceNo"], "operator": svc.get("Operator"), "next": upcoming})
    return {"bus_stop_code": bus_stop_code, "ts": ts or datetime.now(timezone.utc), "services": services}


def latest(bus_stop_code):
    """The newest snapshot; polls DataMall first when it is stale and a key is configured."""
    found = mongo_find("arrival_snapshots", {"bus_stop_code": bus_stop_code}, sort=[("ts", -1)], limit=1)
    snapshot = found[0] if found else None
    stale = snapshot is None or datetime.now(timezone.utc) - snapshot["ts"] > FRESH_FOR
    if stale and current_app.config.get("DATAMALL_KEY"):
        from etl.datamall import DataMall

        try:
            snapshot = snapshot_from_api(bus_stop_code, DataMall().bus_arrival(bus_stop_code))
        except Exception as exc:  # the page still works from the stored snapshot
            current_app.logger.warning("Live arrival fetch failed for %s: %s", bus_stop_code, exc)
        else:
            mongo_write("arrival_snapshots", "insert_one", snapshot)
    return snapshot
