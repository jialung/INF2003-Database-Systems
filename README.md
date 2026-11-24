# Smart City Transport Tracker

INF2003 Database Systems project, Group 083 (Singapore Institute of Technology).

A web app for Singapore bus commuters and network admins, built on **two databases**:

- **MySQL 8.4** holds the structured transport network and users: stops, services, routes, train lines, favourites and the audit log.
- **MongoDB 7.0** holds the high-volume, nested and time-stamped data: live arrival snapshots, passenger volumes, crowd reports and map points.

Several pages use both at once, and the project measures the two engines against each other on the same data.

## Features

| Page | What it does |
|---|---|
| Search | Find stops and services |
| Stop | Next buses, services, open reports, hourly passengers, save as favourite |
| Service | Route map and stop list in both directions |
| Journey | Direct and one-transfer bus options between two stops |
| Nearby | Stops and MRT exits within a radius, on either engine |
| Reports | Create, filter, vote on and resolve crowding, delay and breakdown reports |
| Analytics | Busiest stops, operators, longest routes, interchanges, top origin–destination pairs |
| Reliability | Scheduled frequency vs. observed gaps between buses |
| Admin | Stop and route editing, frequencies, users and roles, audit log |

Every page has a **Show queries** button that lists the SQL and MongoDB commands it ran, with timings.

## Database features

| | MySQL | MongoDB |
|---|---|---|
| Schema | 3NF, composite keys, FKs, CHECK constraints, generated spatial column | `$jsonSchema` validators, bucket pattern, TTL index |
| Queries | Views, recursive CTE, window functions (`LAG`, `RANK`, `ROW_NUMBER`), `WITH ROLLUP`, nested subqueries | `$facet`, `$bucket`, `$unwind`/`$group`, `$percentile`, `$merge`, `$geoNear` |
| Logic | Triggers (audit log, favourite limit), stored procedures with transactions and `SIGNAL` | Atomic `$addToSet` votes, scheduled roll-up |
| Indexes | Composite, SPATIAL (R-tree) | Compound, 2dsphere, text, partial, unique |
| Security | Least-privilege accounts, column-level grants | Schema validation rejects bad documents |

The app adds bcrypt password hashes, CSRF tokens on every POST, parameterised queries and local-only redirects.

## Data

| Dataset | Source | Stored in |
|---|---|---|
| Bus stops, services, routes | [LTA DataMall](https://datamall.lta.gov.sg) | MySQL, with a copy of stops in MongoDB `places` |
| Train stations and MRT exits | [data.gov.sg](https://data.gov.sg) | MySQL; MRT exits also in MongoDB `places` |
| Live bus arrivals | LTA DataMall API | MongoDB `arrival_snapshots`, rolled up into `headway_daily` |
| Passenger volumes (bus, train, origin–destination) | LTA DataMall | MongoDB, plus MySQL copies for the benchmark |
| Users, favourites, reports | Generated | MySQL and MongoDB |

The repo ships everything needed to run without a DataMall key:
- `data/static/`: cached stops, services, routes, stations and MRT exits.
- `data/sample/`: October 2026 passenger volumes, the same data used for the benchmark.

A key is only needed for live arrivals or newer months.

## Quick start

You need Python 3 and Docker.

```bash
cp .env.example .env                 # optional: set DATAMALL_KEY
docker compose up -d                 # MySQL on 3306, MongoDB on 27017

python -m venv .venv
.venv\Scripts\activate               # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

python -m etl.init_mysql
python -m etl.init_mongo
python -m etl.load_static --cached
python -m etl.load_train
python -m etl.sync_places
python -m etl.seed_users

python run.py                        # http://127.0.0.1:5000
```

Log in as `admin` / `admin12345`, or `commuter001` / `password123`.

To fill the analytics page and run the benchmark, load the sample volumes:

```bash
python -m etl.load_volume --dataset bus   --file data/sample/bus_202610.zip
python -m etl.load_volume --dataset train --file data/sample/train_202610.zip
python -m etl.load_volume --dataset odbus --file data/sample/odbus_202610_1M.zip
```

The [user manual](docs/USER_MANUAL.md) has the full steps, the live arrival collector, a feature tour and troubleshooting.

## SQL vs NoSQL benchmark

```bash
python -m bench.bench                # writes bench/results/latency.csv and throughput.csv
```

Four tasks, each run 30 times on both engines, with and without secondary indexes. Median latency in ms, from `bench/results/latency.csv`:

| Task | MySQL, no index | MySQL, indexed | MongoDB, no index | MongoDB, indexed |
|---|---|---|---|---|
| Point lookup | 222.5 | 5.8 | 657.6 | **2.6** |
| Top origin–destination pairs | 308.4 | **88.8** | 1026.1 | 247.3 |
| 24-hour stop profile | 62.0 | 2.9 | 1.2 | **1.0** |
| Stops within 500 m | 2.6 | 2.6 | 1.8 | **1.5** |

MySQL wins full scans and grouping. MongoDB wins indexed lookups, and its bucket pattern returns a stop's whole day as one document. Throughput peaks near 870 ops/s for MySQL and 1,190 ops/s for MongoDB. Test machine: Ryzen AI 9 H 365, 20 GB RAM, NVMe SSD, Windows 11, both databases in Docker.

## Tests

```bash
pytest                               # 50 tests on throwaway transport_test databases
```

The tests cover constraints, procedures, triggers, privileges, validators, indexes, the roll-up and every page.

## Project layout

```
app/          Flask app: routes/ (main, user, admin, analytics), services/, templates/, static/
sql/          01 schema, 02 views, 03 triggers, 04 procedures, 05 benchmark tables, 06 accounts
etl/          schema setup, data loaders, live arrival collector, headway roll-up, DataMall client
bench/        SQL vs NoSQL benchmark and its results
tests/        pytest suite
data/         static/ cached network data, sample/ passenger volumes
docs/         USER_MANUAL.md, report/ (ER diagram and report build scripts)
```

## Design notes

- **MySQL owns bus stops.** Routes, favourites and services reference them by foreign key. MongoDB `places` is a read-only copy for map search. Admin edits commit to MySQL first, then update the copy. `etl.sync_places` repairs it if that second step fails.
- **Services are keyed by `(service_no, direction)`,** because LTA lists each direction with its own frequencies.
- **Arrival snapshots expire after 30 days.** `etl.rollup_headway` first rolls them into `headway_daily` with `$merge`, so the reliability history is kept.
- **Passenger volumes use the bucket pattern:** 24 hourly counts in one document per stop and day type, about 24 times fewer documents.

## Team

| Member | Scope |
|---|---|
| Koh Jia Lung | Relational DB |
| Christian Khee Jia Heng | Relational DB |
| Ryan Teo Kah Hao | NoSQL DB |
| Cheng Lui Khee | NoSQL DB |
| Chua Jia Jun | Application and cross-DB |
| Lai Jian Yi | Application and cross-DB |
