"""Test fixtures. Runs against separate databases (transport_test) and separate MySQL
accounts on the same servers, so the real data is never touched.
Needs the docker compose services running."""
import os

os.environ.update({
    "MYSQL_DB": "transport_test",
    "MONGO_DB": "transport_test",
    "MYSQL_RW_USER": "test_rw",
    "MYSQL_RW_PASSWORD": "test_rw_pass",
    "MYSQL_RO_USER": "test_ro",
    "MYSQL_RO_PASSWORD": "test_ro_pass",
    "DATAMALL_KEY": "",  # never call the live API from tests
})

import pytest  # noqa: E402

from app import create_app  # noqa: E402
from app.auth import hash_password  # noqa: E402
from app.db import mongo_db, mysql_connect  # noqa: E402
from etl import init_mongo, init_mysql, load_train, sync_places  # noqa: E402

# A small made-up network (codes 99xxx do not exist in LTA's data):
#   T1: A -> B -> C -> D      T2: C -> E -> F      T3: B -> D
STOPS = [
    ("99001", "Test Rd", "Stop A", 1.3000, 103.8000),
    ("99002", "Test Rd", "Stop B", 1.3010, 103.8010),
    ("99003", "Test Rd", "Stop C", 1.3020, 103.8020),
    ("99004", "Test Rd", "Stop D", 1.3030, 103.8030),
    ("99005", "Test Ave", "Stop E", 1.3040, 103.8040),
    ("99006", "Test Ave", "Stop F", 1.3050, 103.8050),
]
SERVICES = [
    ("T1", 1, "SBST", "TRUNK", "99001", "99004", 8, 12, 10, 15, 8, 12, 12, 18),
    ("T2", 1, "SMRT", "TRUNK", "99003", "99006", 10, 14, 12, 20, 10, 14, 15, 20),
    ("T3", 1, "GAS", "FEEDER", "99002", "99004", 15, 20, 15, 20, 15, 20, 15, 20),
]
ROUTES = [
    ("T1", 1, 1, "99001", 0.0), ("T1", 1, 2, "99002", 1.0), ("T1", 1, 3, "99003", 2.0), ("T1", 1, 4, "99004", 3.0),
    ("T2", 1, 1, "99003", 0.0), ("T2", 1, 2, "99005", 1.5), ("T2", 1, 3, "99006", 3.0),
    ("T3", 1, 1, "99002", 0.0), ("T3", 1, 2, "99004", 2.0),
]
CSRF = "test-csrf-token"


def root_execute(sql, params=None, many=False):
    conn = mysql_connect("root")
    try:
        with conn.cursor() as cur:
            (cur.executemany if many else cur.execute)(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


@pytest.fixture(scope="session", autouse=True)
def databases():
    init_mysql.main()
    init_mongo.setup(reset=True)
    load_train.main()
    root_execute("INSERT INTO bus_stop (bus_stop_code, road_name, description, latitude, longitude) "
                 "VALUES (%s, %s, %s, %s, %s)", STOPS, many=True)
    root_execute("INSERT INTO bus_service (service_no, direction, operator_code, category, origin_code, "
                 "destination_code, am_peak_min, am_peak_max, am_offpeak_min, am_offpeak_max, pm_peak_min, "
                 "pm_peak_max, pm_offpeak_min, pm_offpeak_max) VALUES "
                 "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", SERVICES, many=True)
    root_execute("INSERT INTO route_stop (service_no, direction, stop_sequence, bus_stop_code, distance_km) "
                 "VALUES (%s, %s, %s, %s, %s)", ROUTES, many=True)
    root_execute("DELETE FROM audit_log")
    root_execute("INSERT INTO user_account (username, email, password_hash, role) VALUES (%s, %s, %s, %s)",
                 [("alice", "alice@example.com", hash_password("alicepass1"), "commuter"),
                  ("bob", "bob@example.com", hash_password("bobpass12"), "commuter"),
                  ("root_admin", "admin@example.com", hash_password("adminpass1"), "admin")], many=True)
    sync_places.main()
    yield
    root_execute("DROP USER IF EXISTS 'test_rw'@'%', 'test_ro'@'%'")
    root_execute("DROP DATABASE transport_test")
    mongo_db().client.drop_database("transport_test")


@pytest.fixture
def app():
    app = create_app()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as s:
        s["csrf"] = CSRF
    return client


def login(client, username, password):
    response = client.post("/login", data={"username": username, "password": password, "csrf": CSRF})
    assert response.status_code == 302, "login failed"
    with client.session_transaction() as s:  # login clears the session, so restore the token
        s["csrf"] = CSRF
    return client


@pytest.fixture
def alice(client):
    return login(client, "alice", "alicepass1")


@pytest.fixture
def admin(client):
    return login(client, "root_admin", "adminpass1")
