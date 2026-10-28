"""Parsing of DataMall records (the records below follow DataMall's field names)."""
from datetime import datetime, timedelta, timezone

import pytest

from app.services.arrivals import snapshot_from_api
from etl.load_static import parse_freq, parse_time, route_rows, service_rows, stop_rows


@pytest.mark.parametrize("raw, expected", [
    ("08-12", (8, 12)), ("10", (10, 10)), ("12-08", (8, 12)), ("-", (None, None)), ("", (None, None)),
    ("00-00", (None, None)),
])
def test_parse_freq(raw, expected):
    assert parse_freq(raw) == expected


@pytest.mark.parametrize("raw, expected", [("0530", "05:30:00"), ("2359", "23:59:00"), ("-", None), ("", None)])
def test_parse_time(raw, expected):
    assert parse_time(raw) == expected


def test_rows_skip_records_that_would_break_constraints():
    stops, skipped = stop_rows([
        {"BusStopCode": "01012", "RoadName": "Victoria St", "Description": "Hotel Grand Pacific",
         "Latitude": 1.29685, "Longitude": 103.85254},
        {"BusStopCode": "99999", "RoadName": "Nowhere", "Description": "Bad", "Latitude": 0, "Longitude": 0},
    ])
    assert [s[0] for s in stops] == ["01012"] and skipped == 1

    services = service_rows([{"ServiceNo": "7", "Operator": "SBST", "Direction": 1, "Category": "TRUNK",
                              "OriginCode": "01012", "DestinationCode": "77009", "AM_Peak_Freq": "08-12",
                              "AM_Offpeak_Freq": "10-14", "PM_Peak_Freq": "-", "PM_Offpeak_Freq": "12",
                              "LoopDesc": ""}], {"01012"})
    assert services[0][:6] == ("7", 1, "SBST", "TRUNK", "01012", None)  # unknown destination -> NULL
    assert services[0][6:14] == (8, 12, 10, 14, None, None, 12, 12)

    route = {"ServiceNo": "7", "Operator": "SBST", "Direction": 1, "StopSequence": 1, "BusStopCode": "01012",
             "Distance": 0, "WD_FirstBus": "0530", "WD_LastBus": "2330", "SAT_FirstBus": "0530",
             "SAT_LastBus": "2330", "SUN_FirstBus": "-", "SUN_LastBus": "-"}
    rows, skipped = route_rows([route, route, {**route, "StopSequence": 2, "BusStopCode": "55555"}],
                               {"01012"}, {("7", 1)})
    assert len(rows) == 1 and skipped == 2  # duplicate key and unknown stop
    assert rows[0][5:] == ("05:30:00", "23:30:00", "05:30:00", "23:30:00", None, None)


def test_snapshot_from_bus_arrival_payload():
    eta = (datetime.now(timezone(timedelta(hours=8))) + timedelta(minutes=3)).isoformat(timespec="seconds")
    payload = {"BusStopCode": "83139", "Services": [
        {"ServiceNo": "15", "Operator": "GAS",
         "NextBus": {"EstimatedArrival": eta, "Monitored": 1, "Latitude": "1.3154", "Longitude": "103.9059",
                     "Load": "SEA", "Feature": "WAB", "Type": "SD"},
         "NextBus2": {"EstimatedArrival": "", "Latitude": "", "Longitude": "", "Load": "", "Type": ""},
         "NextBus3": {"EstimatedArrival": ""}},
        {"ServiceNo": "150", "Operator": "SBST", "NextBus": {"EstimatedArrival": ""}},
    ]}
    snap = snapshot_from_api("83139", payload)
    assert [s["service_no"] for s in snap["services"]] == ["15"]  # services with no bus coming are dropped
    bus = snap["services"][0]["next"][0]
    assert bus["load"] == "SEA" and bus["monitored"] and bus["eta"].tzinfo is not None
