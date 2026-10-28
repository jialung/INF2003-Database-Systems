"""Loads LTA passenger-volume CSVs into MongoDB (and MySQL, for the benchmark).

    python -m etl.load_volume --dataset bus   --period 202409
    python -m etl.load_volume --dataset odbus --period 202409 --limit 1000000
    python -m etl.load_volume --dataset train --file data/volume/train_202409.zip

Datasets (DataMall "Passenger Volume by ..."):
  bus    tap-in/out per bus stop per hour   -> Mongo pv_bus_stop (24 hours bucketed per doc) + MySQL pv_bus_stop
  train  tap-in/out per station per hour    -> Mongo pv_train
  odbus  trips per origin-destination stop  -> Mongo od_volume (flat) + MySQL od_volume
--period downloads through the API (needs DATAMALL_KEY); --file reads a downloaded .zip or .csv.
Reloading a period replaces it.
"""
import argparse
import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path

import requests
from pymongo import ReplaceOne

from app.db import mongo_db, mysql_connect

VOLUME_DIR = Path(__file__).resolve().parent.parent / "data" / "volume"
API_DATASETS = {"bus": "PV/Bus", "train": "PV/Train", "odbus": "PV/ODBus"}
BATCH = 10000


def download(dataset, period):
    from etl.datamall import DataMall

    VOLUME_DIR.mkdir(parents=True, exist_ok=True)
    path = VOLUME_DIR / f"{dataset}_{period}.zip"
    if not path.exists():
        link = DataMall().passenger_volume_link(API_DATASETS[dataset], period)
        with requests.get(link, stream=True, timeout=120) as response:
            response.raise_for_status()
            with open(path, "wb") as f:
                for chunk in response.iter_content(1 << 20):
                    f.write(chunk)
    return path


def read_rows(path, limit=None):
    path = Path(path)
    if path.suffix == ".zip":
        archive = zipfile.ZipFile(path)
        member = next(n for n in archive.namelist() if n.lower().endswith(".csv"))
        stream = io.TextIOWrapper(archive.open(member), encoding="utf-8-sig", newline="")
    else:
        stream = open(path, encoding="utf-8-sig", newline="")
    with stream:
        for i, row in enumerate(csv.DictReader(stream)):
            if limit and i >= limit:
                return
            yield row


def load_tap_volume(rows, collection, code_field, mysql):
    """Groups hourly rows into one document per place, period and day type (bucket pattern)."""
    buckets = defaultdict(list)
    flat = []
    for r in rows:
        code = r["PT_CODE"].strip()
        if code_field == "bus_stop_code":
            code = code.zfill(5)
        hour, tap_in, tap_out = int(r["TIME_PER_HOUR"]), int(r["TOTAL_TAP_IN_VOLUME"]), int(r["TOTAL_TAP_OUT_VOLUME"])
        buckets[(r["YEAR_MONTH"], r["DAY_TYPE"], code)].append({"hour": hour, "tap_in": tap_in, "tap_out": tap_out})
        flat.append((r["YEAR_MONTH"], r["DAY_TYPE"], hour, code, tap_in, tap_out))

    ops = [ReplaceOne({"period": p, "day_type": d, code_field: c},
                      {"period": p, "day_type": d, code_field: c, "hours": sorted(h, key=lambda x: x["hour"])},
                      upsert=True)
           for (p, d, c), h in buckets.items()]
    for i in range(0, len(ops), BATCH):
        mongo_db()[collection].bulk_write(ops[i:i + BATCH], ordered=False)
    print(f"{collection}: {len(ops)} documents from {len(flat)} rows")

    if mysql:
        periods = sorted({row[0] for row in flat})
        _mysql_replace("pv_bus_stop", periods, """
            INSERT INTO pv_bus_stop (period, day_type, hour, bus_stop_code, tap_in, tap_out)
            VALUES (%s, %s, %s, %s, %s, %s)""", flat)


OD_FIELDS = ("period", "day_type", "hour", "origin_code", "destination_code", "trips")


def load_od(rows, mysql):
    """Streams in batches: a month of OD data is several million rows."""
    collection = mongo_db()["od_volume"]
    conn = mysql_connect("root") if mysql else None
    batch, periods, total = [], set(), 0

    def flush():
        if not batch:
            return
        if conn:
            with conn.cursor() as cur:
                cur.executemany(f"INSERT INTO od_volume ({', '.join(OD_FIELDS)}) VALUES (%s, %s, %s, %s, %s, %s)",
                                [tuple(d[k] for k in OD_FIELDS) for d in batch])
        collection.insert_many(batch, ordered=False)  # adds _id to each doc, so after the MySQL copy
        batch.clear()

    try:
        for r in rows:
            doc = {"period": r["YEAR_MONTH"], "day_type": r["DAY_TYPE"], "hour": int(r["TIME_PER_HOUR"]),
                   "origin_code": r["ORIGIN_PT_CODE"].strip().zfill(5),
                   "destination_code": r["DESTINATION_PT_CODE"].strip().zfill(5), "trips": int(r["TOTAL_TRIPS"])}
            if doc["period"] not in periods:  # a reload of the same period replaces it
                periods.add(doc["period"])
                collection.delete_many({"period": doc["period"]})
                if conn:
                    with conn.cursor() as cur:
                        cur.execute("DELETE FROM od_volume WHERE period = %s", (doc["period"],))
            batch.append(doc)
            total += 1
            if len(batch) >= BATCH:
                flush()
        flush()
    finally:
        if conn:
            conn.close()
    print(f"od_volume: {total} rows into MongoDB{' and MySQL' if mysql else ''}")


def _mysql_replace(table, periods, insert_sql, rows):
    conn = mysql_connect("root")
    try:
        with conn.cursor() as cur:
            for period in periods:
                cur.execute(f"DELETE FROM {table} WHERE period = %s", (period,))
            for i in range(0, len(rows), BATCH):
                cur.executemany(insert_sql, rows[i:i + BATCH])
    finally:
        conn.close()
    print(f"MySQL {table}: {len(rows)} rows")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", choices=sorted(API_DATASETS), required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--period", help="YYYYMM, downloaded through the DataMall API")
    source.add_argument("--file", help="a downloaded .zip or .csv")
    parser.add_argument("--limit", type=int, help="load only the first N rows (benchmark scaling)")
    parser.add_argument("--no-mysql", action="store_true", help="skip the MySQL benchmark copy")
    args = parser.parse_args()

    path = args.file or download(args.dataset, args.period)
    rows = read_rows(path, args.limit)
    if args.dataset == "odbus":
        load_od(rows, mysql=not args.no_mysql)
    elif args.dataset == "bus":
        load_tap_volume(rows, "pv_bus_stop", "bus_stop_code", mysql=not args.no_mysql)
    else:
        load_tap_volume(rows, "pv_train", "station_code", mysql=False)


if __name__ == "__main__":
    main()
