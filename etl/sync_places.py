"""Rebuilds the MongoDB `places` collection from MySQL bus stops and the MRT exit GeoJSON.

    python -m etl.sync_places

MySQL is the system of record for bus stops. The app updates `places` after each admin
edit; this script is the full rebuild that also repairs any drift.
"""
import json
from pathlib import Path

from pymongo import ReplaceOne

from app.db import mongo_db, mysql_connect
from app.services.places import exit_place, stop_place

EXITS = Path(__file__).resolve().parent.parent / "data" / "static" / "mrt_exits.geojson"


def main():
    conn = mysql_connect("ro")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bus_stop_code, description, road_name, latitude, longitude FROM bus_stop")
            stops = [stop_place(row) for row in cur.fetchall()]
    finally:
        conn.close()
    exits = [exit_place(f) for f in json.loads(EXITS.read_text(encoding="utf-8"))["features"]]

    places = mongo_db()["places"]
    docs = stops + exits
    if docs:
        places.bulk_write([ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs], ordered=False)
    removed = places.delete_many({"_id": {"$nin": [d["_id"] for d in docs]}}).deleted_count
    print(f"places: {len(stops)} bus stops, {len(exits)} MRT exits, {removed} stale removed")


if __name__ == "__main__":
    main()
