# Smart City Transport Tracker: User Manual

INF2003 Database Systems, Group 083.

This manual explains how to set up, configure and run the application from a fresh copy of the source code.

## 1. What you need

| Software | Version tested | Notes |
|---|---|---|
| Docker Desktop | 29.x | Runs MySQL 8.4 and MongoDB 7.0. You can use local installs of the same versions instead. |
| Python | 3.13 (3.11 or newer works) | |
| LTA DataMall account key | — | Free from https://datamall.lta.gov.sg. Needed only for live bus arrivals and fresh downloads. The submitted copy includes cached static data, so you can run the app without a key. |

The browser must be able to reach the internet. Bootstrap, Leaflet and Chart.js load from CDNs, and the map tiles come from OpenStreetMap.

## 2. Set up

Run all commands from the project folder (the folder that contains `run.py`).

```bash
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env          # Windows: copy .env.example .env
# Edit .env and set DATAMALL_KEY if you have one. Leave the other values as they are.

docker compose up -d          # starts MySQL on port 3306 and MongoDB on port 27017
```

Wait until `docker compose ps` shows MySQL as `healthy`. This takes about 30 seconds the first time.

## 3. Build the databases and load the data

Run the steps in this order:

```bash
python -m etl.init_mysql            # schema, views, triggers, procedures, app accounts
python -m etl.init_mongo            # collections, $jsonSchema validator, indexes

python -m etl.load_static --cached  # bus stops, services, routes from data/static/*.json
                                    # (drop --cached to download fresh copies; needs DATAMALL_KEY)
python -m etl.load_train            # MRT/LRT lines and stations
python -m etl.sync_places           # copy stops and MRT exits into MongoDB for map queries
python -m etl.seed_users            # demo users, favourites and commuter reports
```

> **Warning:** `init_mysql` drops and recreates every table. Run it only on a fresh setup.

Each step prints row counts. With the full LTA data, expect roughly 5,000 bus stops and 25,000+ route stops, plus 171 stations, 613 MRT exits, 51 users and 400 reports.

### Optional: passenger volumes (analytics and benchmark)

The submitted copy includes the October 2026 volumes used in the report, in `data/sample/`. They load without a DataMall key:

```bash
python -m etl.load_volume --dataset bus   --file data/sample/bus_202610.zip       # 203,638 rows
python -m etl.load_volume --dataset train --file data/sample/train_202610.zip     # 7,380 rows
python -m etl.load_volume --dataset odbus --file data/sample/odbus_202610_1M.zip  # first 1,000,000 rows
```

To download a newer month instead (needs `DATAMALL_KEY`; DataMall only keeps the **last three months**):

```bash
python -m etl.load_volume --dataset bus   --period YYYYMM
python -m etl.load_volume --dataset train --period YYYYMM
python -m etl.load_volume --dataset odbus --period YYYYMM --limit 1000000   # large; --limit keeps it manageable
```

### Optional: live arrival history (reliability page)

```bash
python -m etl.collector             # polls about 100 busy stops every 90 s; leave it running
python -m etl.rollup_headway        # run hourly (Task Scheduler / cron) to update headway_daily
```

## 4. Run the application

```bash
python run.py
```

Open http://127.0.0.1:5000.

| Login | Password | Role |
|---|---|---|
| `admin` | `admin12345` | admin |
| `commuter001` to `commuter050` | `password123` | commuter |

You can also register a new commuter account.

Every page has a **Show queries** button at the bottom. It lists each SQL statement or MongoDB command the page ran, with its time in milliseconds.

## 5. Feature tour

| Page | What it shows | Database features |
|---|---|---|
| Search (`/`) | Stops and services | Views `v_stop_service_count`, `v_service_summary` |
| Stop (`/stops/<code>`) | Next buses, services, connectivity, open reports, hourly passengers, save as favourite | Recursive CTE; latest snapshot from `arrival_snapshots`; partial index on open reports; bucketed `pv_bus_stop` |
| Service (`/services/<no>`) | Route map and stop list in both directions | `LAG()` window for leg distance |
| Journey (`/journey`) | Direct buses and one-transfer options between two stops | Self-joins on `route_stop`; CTEs; `ROW_NUMBER()` |
| Nearby (`/nearby`) | Stops and MRT exits around a point. Switch between engines to compare them. | MongoDB `$geoNear` (2dsphere) vs. MySQL `ST_Distance_Sphere` (SPATIAL index); services added from MySQL |
| Reports (`/reports`) | Create, filter, confirm, resolve and delete crowding, delay or breakdown reports | `$jsonSchema` with per-type rules; text index; atomic `$addToSet` vote |
| Favourites | Add, label and remove saved stops | Composite primary key; trigger that limits a user to 20 favourites |
| Analytics | Busiest stops, operator summary, longest routes, interchanges, passenger profile, top origin–destination pairs, report statistics | Nested subquery; `WITH ROLLUP`; `RANK()`; views; `$unwind`/`$group`, `$facet`, `$bucket` |
| Reliability | Scheduled frequency vs. observed bus gaps, per service and hour | MySQL schedule joined with MongoDB `headway_daily` (built with `$percentile` and `$merge`) |
| Admin → Bus stops | Create, edit and delete stops. Bad codes or coordinates are rejected. | CHECK constraints; FK blocks deleting a stop that routes still use; MongoDB `places` updated after the commit |
| Admin → Service | Edit frequencies; insert or remove a stop in a route | CHECK (min ≤ max); stored procedures with transactions and `SIGNAL` |
| Admin → Audit log | Every change to stops, services and routes, and who made it | AFTER triggers; the app account can read the log but not change it |
| Admin → Users | Change roles; delete users | `ON DELETE CASCADE` for favourites; reports anonymised in MongoDB |

## 6. Tests and benchmark

```bash
pytest                      # 50 tests; creates and removes its own transport_test databases
python -m bench.bench       # needs passenger volumes loaded; writes bench/results/*.csv
```

The benchmark runs the same tasks on both databases, with and without secondary indexes:
- point lookup
- top origin–destination pairs
- hourly profile of a stop
- stops within 500 m

It reports the median and p95 latency, then throughput at 1, 4, 16 and 32 concurrent clients.

## 7. Troubleshooting

| Problem | Fix |
|---|---|
| `Can't connect to MySQL server`, or port 3306 or 27017 already in use | Stop the local server, or pick free ports in `.env`: set `MYSQL_PORT`, and set `MONGO_PORT` together with the port in `MONGO_URI`. `docker compose` reads both from `.env`. |
| `Access denied for user 'app_rw'` | Run `python -m etl.init_mysql` again; it creates the app accounts. |
| `DATAMALL_KEY is not set` | Add the key to `.env`, or use `--cached` / `--file`. |
| DataMall returns 401 | The key is wrong or not yet activated. |
| No arrivals or empty reliability page | Needs the collector, or a DataMall key for live arrivals on the stop page. |
| Map is blank | The browser cannot reach the CDN or OpenStreetMap. |

To start over, run `docker compose down -v`. This deletes both databases. Then repeat sections 2 and 3.
