# Smart City Transport Tracker (INF2003 Group 083)

A Flask web app over **MySQL** (the transport network and users) and **MongoDB** (live arrivals, passenger volumes, crowd reports and map data). Setup and run instructions are in [docs/USER_MANUAL.md](docs/USER_MANUAL.md).

```
sql/        01 schema · 02 views · 03 triggers · 04 procedures · 05 benchmark tables · 06 accounts
etl/        init_mysql, init_mongo, load_static, load_train, sync_places, seed_users,
            load_volume, collector, rollup_headway, datamall (API client)
app/        Flask app: routes/ (main, user, admin, analytics), services/ (arrivals, places, reliability)
bench/      SQL-vs-NoSQL latency and throughput study
tests/      pytest suite (runs against throwaway transport_test databases)
data/static DataMall / data.gov.sg files that ship with the submission
```

## Design decisions to explain in the report

- **Bus stops live in MySQL.** Routes, favourites and services all reference them through foreign keys. MongoDB `places` is a read-only copy for geospatial queries. Admin edits commit to MySQL, then update the copy, and `etl.sync_places` rebuilds it. This gives eventual consistency, a trade-off worth discussing.
- **`bus_service` key is `(service_no, direction)`.** LTA publishes one row per direction, and frequencies differ by direction. Frequencies are stored as integer min/max columns so CHECK constraints and the reliability maths work.
- **Snapshots expire after 30 days (TTL index).** `etl.rollup_headway` first rolls them up into `headway_daily` with `$merge`, so the reliability history survives.
- **Headway definition:** the gap between consecutive predicted arrivals in each snapshot. The arrival API doesn't report direction, so the schedule is compared per service.
- **Security:**
  - two MySQL accounts: `app_rw` cannot alter `audit_log`, and `app_ro` cannot read password hashes (column-level grant)
  - bcrypt password hashes
  - CSRF tokens on every POST
  - parameterised queries throughout
  - local-only redirects

## Quick start

```bash
cp .env.example .env              # add DATAMALL_KEY; change MYSQL_PORT / MONGO_PORT if those ports are taken
docker compose up -d
python -m venv venv && source venv/bin/activate && pip install -r requirements.txt
python -m etl.init_mysql && python -m etl.init_mongo
python -m etl.load_static --cached && python -m etl.load_train && python -m etl.sync_places && python -m etl.seed_users
flask --app run run               # http://127.0.0.1:5000 · admin / admin12345
```

The full steps, the feature tour and troubleshooting are in the [user manual](docs/USER_MANUAL.md).

## Status (Oct 28, week 9)

Done and tested (`pytest`: 50 passing):
- schema with all constraints, views, triggers and procedures
- loaders for every dataset
- arrival collector and headway roll-up
- every page listed in the manual
- benchmark harness

Running on the Ubuntu server:
- the real LTA network is loaded: 5,211 stops, 804 service directions, 26,948 route stops
- demo users and reports are seeded
- the collector runs continuously, and cron runs `rollup_headway` hourly

To do:
1. Load one recent month of passenger volumes, then run `python -m bench.bench` for the report.
2. Week 13: final report, slides and video.
