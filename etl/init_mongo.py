"""Creates the MongoDB collections, validators and indexes.

    python -m etl.init_mongo           # create or update, keeps data
    python -m etl.init_mongo --reset   # drop everything first
"""
import argparse

from pymongo import ASCENDING, DESCENDING, GEOSPHERE, TEXT

from app.db import mongo_db

SNAPSHOT_TTL_DAYS = 30

# Reports share a core shape, but each type carries its own fields; the anyOf branch
# enforces those per-type fields while leaving room for new types later.
REPORT_SCHEMA = {
    "$jsonSchema": {
        "bsonType": "object",
        "required": ["user_id", "username", "bus_stop_code", "type", "severity", "status", "votes", "created_at"],
        "properties": {
            "user_id": {"bsonType": "int"},
            "username": {"bsonType": "string"},
            "bus_stop_code": {"bsonType": "string", "pattern": "^[0-9]{5}$"},
            "service_no": {"bsonType": "string", "maxLength": 5},
            "type": {"enum": ["crowding", "delay", "breakdown", "other"]},
            "severity": {"bsonType": "int", "minimum": 1, "maximum": 5},
            "crowd_level": {"enum": ["SEA", "SDA", "LSD"]},
            "delay_min": {"bsonType": "int", "minimum": 1, "maximum": 180},
            "comment": {"bsonType": "string", "maxLength": 500},
            "status": {"enum": ["open", "resolved"]},
            "votes": {"bsonType": "int", "minimum": 0},
            "voters": {"bsonType": "array", "items": {"bsonType": "int"}},
            "location": {
                "bsonType": "object",
                "required": ["type", "coordinates"],
                "properties": {"type": {"enum": ["Point"]}, "coordinates": {"bsonType": "array"}},
            },
            "created_at": {"bsonType": "date"},
            "resolved_at": {"bsonType": "date"},
        },
        "anyOf": [
            {"properties": {"type": {"enum": ["crowding"]}}, "required": ["crowd_level"]},
            {"properties": {"type": {"enum": ["delay"]}}, "required": ["service_no", "delay_min"]},
            {"properties": {"type": {"enum": ["breakdown"]}}, "required": ["service_no"]},
            {"properties": {"type": {"enum": ["other"]}}, "required": ["comment"]},
        ],
    }
}

VALIDATORS = {"commuter_reports": REPORT_SCHEMA}

INDEXES = {
    "arrival_snapshots": [
        ([("bus_stop_code", ASCENDING), ("ts", DESCENDING)], {"name": "stop_ts"}),
        ([("ts", ASCENDING)], {"name": "ttl_ts", "expireAfterSeconds": SNAPSHOT_TTL_DAYS * 86400}),
    ],
    # $merge in etl/rollup_headway.py matches on these fields, which requires a unique index.
    "headway_daily": [
        ([("bus_stop_code", ASCENDING), ("service_no", ASCENDING), ("date", ASCENDING), ("hour", ASCENDING)],
         {"name": "stop_service_date_hour", "unique": True}),
        ([("service_no", ASCENDING), ("date", ASCENDING)], {"name": "service_date"}),
    ],
    "pv_bus_stop": [
        ([("bus_stop_code", ASCENDING), ("period", ASCENDING), ("day_type", ASCENDING)],
         {"name": "stop_period_day", "unique": True}),
    ],
    "pv_train": [
        ([("station_code", ASCENDING), ("period", ASCENDING), ("day_type", ASCENDING)],
         {"name": "station_period_day", "unique": True}),
    ],
    "commuter_reports": [
        # Partial index: the app almost always asks for open reports at a stop.
        ([("bus_stop_code", ASCENDING), ("created_at", DESCENDING)],
         {"name": "open_by_stop", "partialFilterExpression": {"status": "open"}}),
        ([("user_id", ASCENDING), ("created_at", DESCENDING)], {"name": "by_user"}),
        ([("comment", TEXT)], {"name": "comment_text"}),
        ([("location", GEOSPHERE)], {"name": "location_2dsphere"}),
    ],
    "places": [
        ([("location", GEOSPHERE)], {"name": "location_2dsphere"}),
        ([("kind", ASCENDING), ("ref_code", ASCENDING)], {"name": "kind_ref"}),
    ],
    # od_volume is left without secondary indexes; bench/ adds and drops them.
    "od_volume": [],
}


def setup(reset=False):
    db = mongo_db()
    if reset:
        for name in INDEXES:
            db.drop_collection(name)

    existing = set(db.list_collection_names())
    for name, indexes in INDEXES.items():
        validator = VALIDATORS.get(name)
        if name not in existing:
            db.create_collection(name, **({"validator": validator} if validator else {}))
        elif validator:
            db.command("collMod", name, validator=validator)
        for keys, options in indexes:
            db[name].create_index(keys, **options)
        print(f"{name}: {len(indexes)} indexes")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="drop all collections first")
    setup(parser.parse_args().reset)


if __name__ == "__main__":
    main()
