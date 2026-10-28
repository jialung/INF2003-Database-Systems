"""Validators, indexes and pipelines in MongoDB."""
from datetime import datetime, timedelta, timezone

import pytest
from pymongo.errors import WriteError

from app.db import mongo_db
from etl.rollup_headway import pipeline


def report(**overrides):
    doc = {"user_id": 1, "username": "alice", "bus_stop_code": "99001", "type": "crowding", "severity": 3,
           "crowd_level": "LSD", "status": "open", "votes": 0, "created_at": datetime.now(timezone.utc)}
    doc.update(overrides)
    return {k: v for k, v in doc.items() if v is not None}  # None means "leave the field out"


@pytest.mark.parametrize("overrides", [
    {"crowd_level": None},                        # crowding needs a crowd level
    {"type": "delay", "service_no": "T1"},        # delay needs delay_min
    {"type": "breakdown"},                        # breakdown needs a service
    {"bus_stop_code": "1234"},                    # pattern ^[0-9]{5}$
    {"severity": 9},                              # maximum 5
    {"status": "maybe"},                          # enum
])
def test_report_validator_rejects(overrides):
    with pytest.raises(WriteError) as info:
        mongo_db()["commuter_reports"].insert_one(report(**overrides))
    assert info.value.code == 121


def test_report_validator_accepts_each_type():
    reports = mongo_db()["commuter_reports"]
    ids = reports.insert_many([
        report(),
        report(type="delay", service_no="T1", delay_min=12, crowd_level=None),
        report(type="breakdown", service_no="T2", crowd_level=None),
        report(type="other", comment="Shelter roof leaking", crowd_level=None),
    ]).inserted_ids
    reports.delete_many({"_id": {"$in": ids}})


def test_open_reports_query_uses_partial_index():
    plan = mongo_db()["commuter_reports"].find({"bus_stop_code": "99001", "status": "open"}).explain()
    assert "open_by_stop" in str(plan["queryPlanner"]["winningPlan"])


def test_places_contain_stops_and_exits_with_geo_query():
    places = mongo_db()["places"]
    assert places.count_documents({"kind": "bus_stop", "ref_code": "99001"}) == 1
    assert places.count_documents({"kind": "mrt_exit"}) > 500
    near = places.aggregate([
        {"$geoNear": {"near": {"type": "Point", "coordinates": [103.8000, 1.3000]},
                      "distanceField": "d", "maxDistance": 200, "spherical": True}},
    ])
    assert [p["ref_code"] for p in near][0] == "99001"


def test_headway_rollup_merges_hourly_gaps():
    snapshots = mongo_db()["arrival_snapshots"]
    # 10:00 SGT, two polls; buses 8 and 10 minutes apart.
    base = datetime.now(timezone(timedelta(hours=8))).replace(hour=10, minute=0, second=0, microsecond=0)
    base = base.astimezone(timezone.utc) - timedelta(days=1)
    docs = []
    for poll in range(2):
        ts = base + timedelta(minutes=poll)
        etas = [ts + timedelta(minutes=m) for m in (2, 10, 20)]
        docs.append({"bus_stop_code": "99001", "ts": ts,
                     "services": [{"service_no": "T1", "operator": "SBST", "next": [{"eta": e} for e in etas]}]})
    snapshots.insert_many(docs)

    snapshots.aggregate(pipeline(base - timedelta(hours=1)))
    row = mongo_db()["headway_daily"].find_one({"bus_stop_code": "99001", "service_no": "T1"})
    assert row["hour"] == 10
    assert row["samples"] == 4 and row["avg_gap_min"] == 9.0

    # Re-running replaces rather than duplicates.
    snapshots.aggregate(pipeline(base - timedelta(hours=1)))
    assert mongo_db()["headway_daily"].count_documents({"bus_stop_code": "99001", "service_no": "T1"}) == 1
