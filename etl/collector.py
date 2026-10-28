"""Polls the DataMall Bus Arrival API and stores one snapshot per stop per round.

    python -m etl.collector             # run until Ctrl+C
    python -m etl.collector --once      # one round, e.g. from a scheduler

Watches data/collector_stops.txt (one code per line) if present, otherwise the
COLLECTOR_STOP_COUNT stops served by the most services. Keep this running for weeks
before the demo: the reliability page needs the history.
"""
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

from app.config import Config
from app.db import mongo_db, mysql_connect
from app.services.arrivals import snapshot_from_api
from etl.datamall import DataMall

STOP_FILE = Path(__file__).resolve().parent.parent / "data" / "collector_stops.txt"
PAUSE_BETWEEN_CALLS = 0.2  # seconds; stay well inside DataMall's rate limit


def watched_stops():
    if STOP_FILE.exists():
        return [line.strip() for line in STOP_FILE.read_text().splitlines() if line.strip()]
    conn = mysql_connect("ro")
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT bus_stop_code FROM v_stop_service_count ORDER BY service_count DESC LIMIT %s",
                        (Config.COLLECTOR_STOP_COUNT,))
            return [r["bus_stop_code"] for r in cur.fetchall()]
    finally:
        conn.close()


def poll_round(client, stops):
    ts = datetime.now(timezone.utc)
    docs, failures = [], 0
    for code in stops:
        try:
            docs.append(snapshot_from_api(code, client.bus_arrival(code), ts))
        except Exception as exc:
            failures += 1
            print(f"  {code}: {exc}")
        time.sleep(PAUSE_BETWEEN_CALLS)
    if docs:
        mongo_db()["arrival_snapshots"].insert_many(docs, ordered=False)
    return len(docs), failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()

    client = DataMall()
    stops = watched_stops()
    print(f"Watching {len(stops)} stops every {Config.COLLECTOR_INTERVAL}s")
    while True:
        started = time.monotonic()
        saved, failed = poll_round(client, stops)
        print(f"{datetime.now():%Y-%m-%d %H:%M:%S} saved {saved} snapshots, {failed} failed")
        if args.once:
            break
        time.sleep(max(0.0, Config.COLLECTOR_INTERVAL - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped.")
