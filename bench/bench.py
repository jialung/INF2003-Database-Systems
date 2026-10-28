"""SQL vs NoSQL performance study on the same passenger-volume data.

    python -m bench.bench                       # latency, with and without indexes, plus throughput
    python -m bench.bench --runs 50 --threads 1 4 16 32 --seconds 15

Needs od_volume and pv_bus_stop loaded into both databases (etl.load_volume) and bus
stops synced to places. Results go to bench/results/*.csv; report median and p95.
Run with nothing else busy on the machine, and record its specs in the report.
"""
import argparse
import csv
import random
import statistics
import threading
import time
from pathlib import Path

from app.db import mongo_db, mysql_connect

RESULTS = Path(__file__).resolve().parent / "results"

MYSQL_INDEXES = {
    "idx_od_origin": "CREATE INDEX idx_od_origin ON od_volume (origin_code)",
    "idx_od_day_hour": "CREATE INDEX idx_od_day_hour ON od_volume (day_type, hour)",
    "idx_pv_stop": "CREATE INDEX idx_pv_stop ON pv_bus_stop (bus_stop_code)",
}
MONGO_INDEXES = {
    "od_volume": [[("origin_code", 1)], [("day_type", 1), ("hour", 1)]],
}  # pv_bus_stop already has its unique (bus_stop_code, period, day_type) index


# ---------------------------------------------------------------- the queries, one pair per task

def q_point_sql(cur, p):
    cur.execute("SELECT destination_code, hour, trips FROM od_volume WHERE origin_code = %s", (p["origin"],))
    return cur.fetchall()


def q_point_mongo(db, p):
    return list(db.od_volume.find({"origin_code": p["origin"]}, {"_id": 0, "destination_code": 1, "hour": 1, "trips": 1}))


def q_top_od_sql(cur, p):
    cur.execute("""SELECT origin_code, destination_code, SUM(trips) AS trips FROM od_volume
                   WHERE day_type = %s AND hour = %s GROUP BY origin_code, destination_code
                   ORDER BY trips DESC LIMIT 10""", (p["day_type"], p["hour"]))
    return cur.fetchall()


def q_top_od_mongo(db, p):
    return list(db.od_volume.aggregate([
        {"$match": {"day_type": p["day_type"], "hour": p["hour"]}},
        {"$group": {"_id": {"o": "$origin_code", "d": "$destination_code"}, "trips": {"$sum": "$trips"}}},
        {"$sort": {"trips": -1}}, {"$limit": 10},
    ]))


def q_profile_sql(cur, p):
    cur.execute("SELECT day_type, hour, tap_in, tap_out FROM pv_bus_stop WHERE bus_stop_code = %s "
                "ORDER BY day_type, hour", (p["stop"],))
    return cur.fetchall()


def q_profile_mongo(db, p):
    # Bucket pattern: the whole 24-hour profile is one document per day type.
    return list(db.pv_bus_stop.find({"bus_stop_code": p["stop"]}, {"_id": 0, "day_type": 1, "hours": 1}))


def q_nearby_sql(cur, p):
    point = f"POINT({p['lon']} {p['lat']})"
    cur.execute("""SELECT bus_stop_code FROM bus_stop
                   WHERE MBRContains(ST_Buffer(ST_GeomFromText(%s, 4326, 'axis-order=long-lat'), 500), location)
                     AND ST_Distance_Sphere(location, ST_GeomFromText(%s, 4326, 'axis-order=long-lat')) <= 500""",
                (point, point))
    return cur.fetchall()


def q_nearby_mongo(db, p):
    return list(db.places.find({"kind": "bus_stop", "location": {"$nearSphere": {
        "$geometry": {"type": "Point", "coordinates": [p["lon"], p["lat"]]}, "$maxDistance": 500}}}))


TASKS = {
    "point_lookup": (q_point_sql, q_point_mongo),
    "top_od_pairs": (q_top_od_sql, q_top_od_mongo),
    "stop_profile": (q_profile_sql, q_profile_mongo),
    "nearby_500m": (q_nearby_sql, q_nearby_mongo),
}


# ---------------------------------------------------------------- harness

def sample_params(cur, n, seed=7):
    rng = random.Random(seed)
    cur.execute("SELECT DISTINCT origin_code FROM od_volume LIMIT 5000")
    origins = [r["origin_code"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT day_type FROM od_volume")
    day_types = [r["day_type"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT bus_stop_code FROM pv_bus_stop LIMIT 5000")
    stops = [r["bus_stop_code"] for r in cur.fetchall()]
    if not (origins and stops):
        raise SystemExit("Load passenger volumes first: python -m etl.load_volume --dataset odbus/bus ...")
    return [{"origin": rng.choice(origins), "day_type": rng.choice(day_types), "hour": rng.randint(6, 22),
             "stop": rng.choice(stops), "lat": rng.uniform(1.28, 1.42), "lon": rng.uniform(103.70, 103.95)}
            for _ in range(n)]


def timed(fn, target, params, warmup=3):
    for p in params[:warmup]:
        fn(target, p)
    samples = []
    for p in params:
        started = time.perf_counter()
        fn(target, p)
        samples.append((time.perf_counter() - started) * 1000)
    samples.sort()
    return statistics.median(samples), samples[int(len(samples) * 0.95) - 1]


def set_indexes(cur, db, enabled):
    cur.execute("SELECT index_name AS index_name FROM information_schema.statistics WHERE table_schema = DATABASE() "
                "AND table_name IN ('od_volume', 'pv_bus_stop')")
    existing = {r["index_name"] for r in cur.fetchall()}
    for name, ddl in MYSQL_INDEXES.items():
        table = "pv_bus_stop" if name == "idx_pv_stop" else "od_volume"
        if enabled and name not in existing:
            cur.execute(ddl)
        elif not enabled and name in existing:
            cur.execute(f"DROP INDEX {name} ON {table}")
    for collection, specs in MONGO_INDEXES.items():
        for keys in specs:
            name = "_".join(f"{k}_{d}" for k, d in keys)
            if enabled:
                db[collection].create_index(keys, name=name)
            elif name in db[collection].index_information():
                db[collection].drop_index(name)


def latency(runs):
    conn = mysql_connect("root")
    db = mongo_db()
    rows = []
    try:
        with conn.cursor() as cur:
            params = sample_params(cur, runs)
            for indexed in (False, True):
                set_indexes(cur, db, indexed)
                for task, (sql_fn, mongo_fn) in TASKS.items():
                    for engine, fn, target in (("MySQL", sql_fn, cur), ("MongoDB", mongo_fn, db)):
                        median, p95 = timed(fn, target, params)
                        rows.append({"task": task, "engine": engine, "indexed": indexed,
                                     "median_ms": round(median, 2), "p95_ms": round(p95, 2)})
                        print(f"{task:14} {engine:8} indexed={indexed!s:5} median {median:9.2f} ms  p95 {p95:9.2f} ms")
    finally:
        conn.close()
    return rows


def throughput(thread_counts, seconds, task="point_lookup"):
    sql_fn, mongo_fn = TASKS[task]
    conn = mysql_connect("root")
    with conn.cursor() as cur:
        params = sample_params(cur, 500)
    conn.close()
    rows = []
    for engine in ("MySQL", "MongoDB"):
        for threads in thread_counts:
            counts = [0] * threads
            stop = threading.Event()

            def worker(i):
                rng = random.Random(i)
                if engine == "MySQL":
                    c = mysql_connect("root")
                    cur, fn = c.cursor(), sql_fn
                else:
                    c, cur, fn = None, mongo_db(), mongo_fn  # MongoClient pools connections itself
                while not stop.is_set():
                    fn(cur, rng.choice(params))
                    counts[i] += 1
                if c:
                    c.close()

            workers = [threading.Thread(target=worker, args=(i,)) for i in range(threads)]
            for w in workers:
                w.start()
            time.sleep(seconds)
            stop.set()
            for w in workers:
                w.join()
            ops = sum(counts) / seconds
            rows.append({"task": task, "engine": engine, "threads": threads, "ops_per_s": round(ops, 1)})
            print(f"{task} {engine:8} threads={threads:3} {ops:10.1f} ops/s")
    return rows


def write_csv(name, rows):
    RESULTS.mkdir(exist_ok=True)
    with open(RESULTS / name, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=30)
    parser.add_argument("--threads", type=int, nargs="+", default=[1, 4, 16, 32])
    parser.add_argument("--seconds", type=int, default=10)
    args = parser.parse_args()

    write_csv("latency.csv", latency(args.runs))
    write_csv("throughput.csv", throughput(args.threads, args.seconds))
    print(f"Results written to {RESULTS}")


if __name__ == "__main__":
    main()
