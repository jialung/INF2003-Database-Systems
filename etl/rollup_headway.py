"""Rolls arrival snapshots up into hourly headways per stop and service.

    python -m etl.rollup_headway            # last 2 days (run hourly)
    python -m etl.rollup_headway --days 29  # rebuild everything still in the snapshots
                                            # (not 30: the oldest day is already part-expired)

Snapshots expire after 30 days (TTL index); headway_daily keeps the summary for good.
Each snapshot lists the next three predicted arrivals of a service, so the gaps between
them are the headway the operator is currently running. Polling every ~90 s samples
each hour many times, so the averages are time-weighted.
"""
import argparse
from datetime import datetime, timedelta, timezone

from app.db import mongo_db

TZ = "Asia/Singapore"
MAX_GAP_MIN = 120  # ignore gaps that span the overnight break


def pipeline(since):
    return [
        {"$match": {"ts": {"$gte": since}}},
        {"$unwind": "$services"},
        {"$project": {
            "bus_stop_code": 1,
            "ts": 1,
            "service_no": "$services.service_no",
            "etas": "$services.next.eta",
        }},
        {"$match": {"etas.1": {"$exists": True}}},
        # Gaps between consecutive predicted buses, in minutes.
        {"$project": {
            "bus_stop_code": 1, "ts": 1, "service_no": 1,
            "gaps": {"$map": {
                "input": {"$range": [0, {"$subtract": [{"$size": "$etas"}, 1]}]},
                "as": "i",
                "in": {"$divide": [
                    {"$subtract": [{"$arrayElemAt": ["$etas", {"$add": ["$$i", 1]}]},
                                   {"$arrayElemAt": ["$etas", "$$i"]}]},
                    60000,
                ]},
            }},
        }},
        {"$unwind": "$gaps"},
        {"$match": {"gaps": {"$gt": 0, "$lt": MAX_GAP_MIN}}},
        {"$group": {
            "_id": {
                "bus_stop_code": "$bus_stop_code",
                "service_no": "$service_no",
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$ts", "timezone": TZ}},
                "hour": {"$hour": {"date": "$ts", "timezone": TZ}},
            },
            "avg_gap": {"$avg": "$gaps"},
            "p90_gap": {"$percentile": {"input": "$gaps", "p": [0.9], "method": "approximate"}},
            "samples": {"$sum": 1},
        }},
        {"$project": {
            "_id": 0,
            "bus_stop_code": "$_id.bus_stop_code",
            "service_no": "$_id.service_no",
            "date": "$_id.date",
            "hour": "$_id.hour",
            "avg_gap_min": {"$round": ["$avg_gap", 1]},
            "p90_gap_min": {"$round": [{"$arrayElemAt": ["$p90_gap", 0]}, 1]},
            "samples": 1,
            "updated_at": "$$NOW",
        }},
        # Upsert into the summary collection (an on-demand materialised view).
        {"$merge": {
            "into": "headway_daily",
            "on": ["bus_stop_code", "service_no", "date", "hour"],
            "whenMatched": "replace",
            "whenNotMatched": "insert",
        }},
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=2)
    args = parser.parse_args()
    # Start at midnight SGT so every (date, hour) group is complete before $merge replaces it.
    sgt = timezone(timedelta(hours=8))
    since = (datetime.now(sgt) - timedelta(days=args.days)).replace(hour=0, minute=0, second=0, microsecond=0)
    db = mongo_db()
    db["arrival_snapshots"].aggregate(pipeline(since))
    print(f"headway_daily now holds {db['headway_daily'].estimated_document_count()} rows")


if __name__ == "__main__":
    main()
