"""Loads train lines, stations and station codes into MySQL.

    python -m etl.load_train

Inputs (both public, no API key needed):
  data/static/train_station_codes.csv  DataMall "Train Station Codes and Chinese Names" (.xls saved as CSV)
  data/static/mrt_exits.geojson        data.gov.sg "LTA MRT Station Exit (GEOJSON)"
A station's coordinates are the average of its exits.
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

from app.db import mysql_connect
from app.services.places import station_key

STATIC_DIR = Path(__file__).resolve().parent.parent / "data" / "static"

LINE_CODES = {
    "North-South Line": "NSL",
    "East-West Line": "EWL",
    "Changi Airport Branch Line": "CGL",
    "North East Line": "NEL",
    "Circle Line": "CCL",
    "Circle Line Extension": "CEL",
    "Downtown Line": "DTL",
    "Thomson-East Coast Line": "TEL",
    "Bukit Panjang LRT": "BPL",
    "Sengkang LRT": "SKL",
    "Punggol LRT": "PGL",
}


def exit_centroids():
    features = json.loads((STATIC_DIR / "mrt_exits.geojson").read_text(encoding="utf-8"))["features"]
    points = defaultdict(list)
    for feature in features:
        lon, lat = feature["geometry"]["coordinates"][:2]
        points[station_key(feature["properties"]["STATION_NA"])].append((lat, lon))
    return {key: (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
            for key, pts in points.items()}


def main():
    with open(STATIC_DIR / "train_station_codes.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    unknown = {r["mrt_line_english"] for r in rows} - LINE_CODES.keys()
    if unknown:
        raise SystemExit(f"Add a line code for: {sorted(unknown)}")
    centroids = exit_centroids()

    conn = mysql_connect("root")
    try:
        conn.begin()
        with conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO train_line (line_code, line_name, line_type) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE line_name = VALUES(line_name), line_type = VALUES(line_type)",
                [(code, name, "LRT" if "LRT" in name else "MRT") for name, code in LINE_CODES.items()],
            )
            names = sorted({r["mrt_station_english"].strip() for r in rows})
            cur.executemany(
                "INSERT INTO train_station (station_name, latitude, longitude) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE latitude = VALUES(latitude), longitude = VALUES(longitude)",
                [(name, *centroids.get(station_key(name), (None, None))) for name in names],
            )
            cur.execute("SELECT station_id, station_name FROM train_station")
            ids = {r["station_name"]: r["station_id"] for r in cur.fetchall()}
            cur.executemany(
                "INSERT INTO station_line (station_code, station_id, line_code) VALUES (%s, %s, %s) "
                "ON DUPLICATE KEY UPDATE station_id = VALUES(station_id), line_code = VALUES(line_code)",
                [(r["stn_code"].strip(), ids[r["mrt_station_english"].strip()], LINE_CODES[r["mrt_line_english"]])
                 for r in rows],
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    located = sum(1 for n in names if station_key(n) in centroids)
    print(f"train_line: {len(LINE_CODES)}, train_station: {len(names)} ({located} located), station_line: {len(rows)}")


if __name__ == "__main__":
    main()
