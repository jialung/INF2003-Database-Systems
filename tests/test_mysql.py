"""Constraints, triggers, procedures and privileges in MySQL."""
import pymysql
import pytest

from app.db import mysql_connect
from tests.conftest import root_execute


def error_code(callable_, *args):
    with pytest.raises(pymysql.MySQLError) as info:
        callable_(*args)
    return info.value.args[0]


def insert_stop(code, lat=1.3, lon=103.8):
    root_execute("INSERT INTO bus_stop (bus_stop_code, road_name, description, latitude, longitude) "
                 "VALUES (%s, 'R', 'D', %s, %s)", (code, lat, lon))


def route(service="T1"):
    return root_execute("SELECT stop_sequence, bus_stop_code, distance_km FROM route_stop "
                        "WHERE service_no = %s AND direction = 1 ORDER BY stop_sequence", (service,))


@pytest.mark.parametrize("code, lat, lon", [
    ("ABCDE", 1.30, 103.80),   # chk_bus_stop_code
    ("99901", 5.00, 103.80),   # chk_bus_stop_area: latitude
    ("99902", 1.30, 100.00),   # chk_bus_stop_area: longitude
])
def test_bus_stop_check_constraints(code, lat, lon):
    assert error_code(insert_stop, code, lat, lon) == 3819


def test_frequency_check_constraint():
    code = error_code(root_execute, "UPDATE bus_service SET am_peak_min = 20, am_peak_max = 10 "
                                    "WHERE service_no = 'T1' AND direction = 1")
    assert code == 3819


def test_foreign_keys():
    assert error_code(root_execute, "INSERT INTO route_stop (service_no, direction, stop_sequence, bus_stop_code, "
                                    "distance_km) VALUES ('T1', 1, 99, '12345', 9)") == 1452
    assert error_code(root_execute, "DELETE FROM bus_stop WHERE bus_stop_code = '99001'") == 1451


def test_spatial_column_follows_coordinates():
    row = root_execute("SELECT ST_Latitude(location) AS lat, ST_Longitude(location) AS lon "
                       "FROM bus_stop WHERE bus_stop_code = '99003'")[0]
    assert (round(row["lat"], 4), round(row["lon"], 4)) == (1.302, 103.802)


def test_insert_route_stop_shifts_sequence_in_one_transaction():
    root_execute("CALL sp_insert_route_stop('T1', 1, 2, '99005', 0.5)")
    assert [r["bus_stop_code"] for r in route()] == ["99001", "99005", "99002", "99003", "99004"]
    assert [r["stop_sequence"] for r in route()] == [1, 2, 3, 4, 5]
    root_execute("CALL sp_delete_route_stop('T1', 1, 2)")
    assert [r["bus_stop_code"] for r in route()] == ["99001", "99002", "99003", "99004"]


@pytest.mark.parametrize("args, message", [
    ("('T1', 1, 2, '99005', 9.9)", "Distance must lie between"),   # 9.9 km is past the next stop
    ("('T1', 1, 9, '99005', 9.9)", "outside the route"),
    ("('ZZ', 1, 1, '99005', 0)", "Unknown service"),
])
def test_insert_route_stop_rejects_and_rolls_back(args, message):
    before = route()
    with pytest.raises(pymysql.MySQLError) as info:
        root_execute(f"CALL sp_insert_route_stop{args}")
    assert info.value.args[0] == 1644 and message in info.value.args[1]
    assert route() == before


def test_audit_trigger_records_app_user():
    conn = mysql_connect("rw")
    try:
        with conn.cursor() as cur:
            cur.execute("SET @app_user = 'tester'")
            cur.execute("UPDATE bus_stop SET description = 'Stop F renamed' WHERE bus_stop_code = '99006'")
            cur.execute("SELECT action, changed_by, old_data->>'$.description' AS old_desc, "
                        "new_data->>'$.description' AS new_desc FROM audit_log "
                        "WHERE table_name = 'bus_stop' AND row_key = '99006' ORDER BY log_id DESC LIMIT 1")
            row = cur.fetchone()
            cur.execute("UPDATE bus_stop SET description = 'Stop F' WHERE bus_stop_code = '99006'")
    finally:
        conn.close()
    assert row == {"action": "UPDATE", "changed_by": "tester", "old_desc": "Stop F", "new_desc": "Stop F renamed"}


def test_favourite_limit_trigger():
    user_id = root_execute("SELECT user_id FROM user_account WHERE username = 'bob'")[0]["user_id"]
    codes = [f"98{i:03d}" for i in range(21)]
    for code in codes:
        insert_stop(code)
    try:
        root_execute("INSERT INTO favourite_stop (user_id, bus_stop_code) VALUES (%s, %s)",
                     [(user_id, c) for c in codes[:20]], many=True)
        code = error_code(root_execute, "INSERT INTO favourite_stop (user_id, bus_stop_code) VALUES (%s, %s)",
                          (user_id, codes[20]))
        assert code == 1644
    finally:
        root_execute("DELETE FROM bus_stop WHERE bus_stop_code LIKE '98%'")  # cascades to favourites


def test_least_privilege_accounts():
    ro = mysql_connect("ro")
    rw = mysql_connect("rw")
    try:
        with ro.cursor() as cur:
            cur.execute("SELECT username FROM user_account LIMIT 1")  # allowed column
            assert error_code(cur.execute, "SELECT password_hash FROM user_account") == 1143
            assert error_code(cur.execute, "DELETE FROM bus_stop WHERE bus_stop_code = '99001'") == 1142
        with rw.cursor() as cur:
            assert error_code(cur.execute, "DELETE FROM audit_log") == 1142
    finally:
        ro.close()
        rw.close()


def test_views():
    counts = {r["bus_stop_code"]: r["service_count"] for r in root_execute(
        "SELECT bus_stop_code, service_count FROM v_stop_service_count WHERE bus_stop_code LIKE '990%'")}
    assert counts["99002"] == 2 and counts["99003"] == 2 and counts["99006"] == 1
    summary = root_execute("SELECT stop_count, route_km FROM v_service_summary WHERE service_no = 'T1'")[0]
    assert summary["stop_count"] == 4 and float(summary["route_km"]) == 3.0
    interchanges = {r["station_name"] for r in root_execute("SELECT station_name FROM v_interchange_station")}
    assert {"Dhoby Ghaut", "Outram Park", "Marina Bay"} <= interchanges
