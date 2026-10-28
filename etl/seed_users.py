"""Generates synthetic users, favourite stops and commuter reports.

    python -m etl.seed_users                      # 1 admin, 50 commuters, 400 reports
    python -m etl.seed_users --users 200 --reports 2000

No public dataset has user accounts or crowd reports (FAQ Q2 allows generating them).
A fixed random seed keeps runs reproducible. Needs bus stops loaded first.
Demo logins: admin / admin12345 and commuter001 / password123.
"""
import argparse
import random
from datetime import datetime, timedelta, timezone

from app.auth import hash_password
from app.db import mongo_db, mysql_connect

COMMENTS = {
    "crowding": ["Packed after school ended", "Could not board, bus full", "Long queue at the stop",
                 "Crowded because of event at stadium", "Standing room only"],
    "delay": ["Waited much longer than shown", "Bus skipped the stop", "Arrival time kept jumping",
              "Heavy traffic on the expressway", "Two buses came together after a long gap"],
    "breakdown": ["Bus broke down, everyone alighted", "Air-con not working", "Door fault, bus withdrawn"],
    "other": ["Shelter roof leaking", "Display board not working", "Stop moved due to roadworks",
              "Wheelchair ramp blocked by parked car"],
}
LOADS = ["SEA", "SDA", "LSD"]
PEAK_HOURS = [7, 8, 8, 9, 12, 17, 18, 18, 19, 21]


def random_time(rng, now):
    day = now - timedelta(days=rng.randint(0, 13))
    hour = rng.choice(PEAK_HOURS) if rng.random() < 0.7 else rng.randint(6, 23)
    local = day.astimezone(timezone(timedelta(hours=8))).replace(hour=hour, minute=rng.randint(0, 59))
    return min(local.astimezone(timezone.utc), now)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--users", type=int, default=50)
    parser.add_argument("--reports", type=int, default=400)
    parser.add_argument("--seed", type=int, default=2003)
    args = parser.parse_args()
    rng = random.Random(args.seed)

    conn = mysql_connect("root")
    try:
        with conn.cursor() as cur:
            cur.execute("SET @app_user = 'etl-seed'")
            cur.execute("SELECT bus_stop_code, latitude, longitude FROM v_stop_service_count "
                        "ORDER BY service_count DESC LIMIT 300")
            stops = cur.fetchall()
            if not stops:
                raise SystemExit("No bus stops loaded; run etl.load_static first.")
            cur.execute("SELECT service_no, bus_stop_code FROM route_stop WHERE bus_stop_code IN (%s)"
                        % ", ".join(["%s"] * len(stops)), [s["bus_stop_code"] for s in stops])
            services_at = {}
            for r in cur.fetchall():
                services_at.setdefault(r["bus_stop_code"], set()).add(r["service_no"])

            cur.execute("DELETE FROM user_account WHERE username = 'admin' OR username LIKE 'commuter%'")
            cur.execute("INSERT INTO user_account (username, email, password_hash, role) VALUES (%s, %s, %s, 'admin')",
                        ("admin", "admin@example.com", hash_password("admin12345")))
            commuter_hash = hash_password("password123")  # one hash is enough for synthetic accounts
            cur.executemany(
                "INSERT INTO user_account (username, email, password_hash, created_at) VALUES (%s, %s, %s, %s)",
                [(f"commuter{i:03d}", f"commuter{i:03d}@example.com", commuter_hash,
                  datetime.now() - timedelta(days=rng.randint(1, 60))) for i in range(1, args.users + 1)])
            cur.execute("SELECT user_id, username FROM user_account WHERE username LIKE 'commuter%'")
            users = cur.fetchall()

            favourites = []
            for u in users:
                for s in rng.sample(stops, rng.randint(0, 8)):
                    favourites.append((u["user_id"], s["bus_stop_code"], rng.choice([None, "Home", "Work", "School", "Gym"])))
            cur.executemany("INSERT INTO favourite_stop (user_id, bus_stop_code, label) VALUES (%s, %s, %s)", favourites)
    finally:
        conn.close()

    now = datetime.now(timezone.utc)
    hot_stops = stops[:100]
    reports = []
    for _ in range(args.reports):
        stop = rng.choice(hot_stops)
        user = rng.choice(users)
        kind = rng.choices(list(COMMENTS), weights=[5, 4, 1, 1])[0]
        created = random_time(rng, now)
        doc = {
            "user_id": user["user_id"], "username": user["username"], "bus_stop_code": stop["bus_stop_code"],
            "type": kind, "severity": rng.randint(1, 5), "votes": 0, "voters": [],
            "location": {"type": "Point", "coordinates": [float(stop["longitude"]), float(stop["latitude"])]},
            "created_at": created,
        }
        services = sorted(services_at.get(stop["bus_stop_code"], []))
        if kind in ("delay", "breakdown") or (kind == "crowding" and rng.random() < 0.6):
            if services:
                doc["service_no"] = rng.choice(services)
            elif kind != "crowding":
                kind = doc["type"] = "other"
        if kind == "crowding":
            doc["crowd_level"] = rng.choices(LOADS, weights=[1, 3, 4])[0]
        if kind == "delay":
            doc["delay_min"] = rng.randint(3, 40)
        if kind == "other" or rng.random() < 0.7:
            doc["comment"] = rng.choice(COMMENTS[kind])
        voters = rng.sample(users, rng.randint(0, 6))
        doc["voters"] = [v["user_id"] for v in voters if v["user_id"] != user["user_id"]]
        doc["votes"] = len(doc["voters"])
        # Older reports are mostly resolved.
        if now - created > timedelta(days=2) and rng.random() < 0.8:
            doc["status"] = "resolved"
            doc["resolved_at"] = created + timedelta(minutes=rng.randint(10, 300))
        else:
            doc["status"] = "open"
        reports.append(doc)

    collection = mongo_db()["commuter_reports"]
    collection.delete_many({"username": {"$regex": "^commuter"}})
    if reports:
        collection.insert_many(reports)
    print(f"1 admin, {len(users)} commuters, {len(favourites)} favourites, {len(reports)} reports")


if __name__ == "__main__":
    main()
