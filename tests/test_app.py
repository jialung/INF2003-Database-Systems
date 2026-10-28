"""End-to-end requests through the Flask app."""
from datetime import datetime, timedelta, timezone

from app.db import mongo_db
from tests.conftest import CSRF, root_execute


def test_search_finds_stops_and_services(client):
    page = client.get("/?q=Test Ave").get_data(as_text=True)
    assert "99005" in page and "99006" in page
    page = client.get("/?q=t1").get_data(as_text=True)
    assert "Stop A → Stop D" in page


def test_stop_page_combines_both_databases(client):
    page = client.get("/stops/99002").get_data(as_text=True)
    assert "Stop B" in page and "T1" in page and "T3" in page
    assert "Reachable with 1 bus: <strong>2</strong>" in page    # C and D (T1, T3)
    assert "Reachable with 2 buses: <strong>2</strong>" in page  # E and F (change to T2 at C)
    assert "No arrival data yet" in page
    assert client.get("/stops/00000").status_code == 404


def test_service_page_window_function(client):
    page = client.get("/services/T2").get_data(as_text=True)
    assert "Stop C → Stop F" in page and "<td>1.5</td>" in page  # leg km from LAG()


def test_journey_direct_and_transfer(client):
    page = client.get("/journey?from=99001&to=99004").get_data(as_text=True)
    assert "No direct bus" not in page and ">T1<" in page
    page = client.get("/journey?from=99001&to=99006").get_data(as_text=True)
    assert "No direct bus" in page
    assert "<td>T1</td>" in page and "Stop C" in page and "<td>T2</td>" in page


def test_nearby_mongo_and_mysql_agree(client):
    for engine in ("mongo", "mysql"):
        data = client.get(f"/api/nearby?lat=1.3000&lon=103.8000&radius=300&engine={engine}").get_json()
        stops = [p for p in data["places"] if p["kind"] == "bus_stop"]
        assert [s["ref_code"] for s in stops][:2] == ["99001", "99002"]
        assert stops[0]["services"] == "T1"
        assert data["queries"]
    assert client.get("/api/nearby?lat=x&lon=1").status_code == 400


def test_post_without_csrf_is_rejected(client):
    assert client.post("/login", data={"username": "alice", "password": "alicepass1"}).status_code == 400


def test_register_and_login(client):
    response = client.post("/register", data={"username": "newbie", "email": "newbie@example.com",
                                              "password": "longenough", "csrf": CSRF})
    assert response.status_code == 302
    page = client.post("/register", data={"username": "newbie", "email": "other@example.com",
                                          "password": "longenough", "csrf": CSRF}).get_data(as_text=True)
    assert "already exists" in page
    assert client.post("/login", data={"username": "newbie", "password": "wrong-one", "csrf": CSRF}).status_code == 200
    assert client.post("/login", data={"username": "newbie", "password": "longenough", "csrf": CSRF}).status_code == 302


def test_login_redirect_stays_on_site(client):
    response = client.post("/login?next=//evil.example", data={"username": "alice", "password": "alicepass1",
                                                               "csrf": CSRF})
    assert response.headers["Location"] == "/"


def test_favourites_crud(alice):
    alice.post("/favourites/add", data={"bus_stop_code": "99003", "csrf": CSRF})
    alice.post("/favourites/99003/label", data={"label": "Work", "csrf": CSRF})
    page = alice.get("/favourites").get_data(as_text=True)
    assert "Stop C" in page and 'value="Work"' in page
    page = alice.post("/favourites/add", data={"bus_stop_code": "99003", "csrf": CSRF},
                      follow_redirects=True).get_data(as_text=True)
    assert "already exists" in page
    alice.post("/favourites/99003/delete", data={"csrf": CSRF})
    assert "Stop C" not in alice.get("/favourites").get_data(as_text=True)


def test_report_lifecycle(alice):
    response = alice.post("/reports/new", data={"bus_stop_code": "99004", "type": "delay", "severity": "4",
                                                "service_no": "t3", "delay_min": "15", "csrf": CSRF})
    assert response.status_code == 302
    doc = mongo_db()["commuter_reports"].find_one({"bus_stop_code": "99004", "type": "delay"})
    assert doc["service_no"] == "T3" and doc["location"]["coordinates"] == [103.803, 1.303]

    # Missing delay_min: the server sends it on, MongoDB's validator refuses it.
    page = alice.post("/reports/new", data={"bus_stop_code": "99004", "type": "delay", "severity": "4",
                                            "service_no": "T3", "csrf": CSRF}).get_data(as_text=True)
    assert "schema validator" in page

    rid = str(doc["_id"])
    alice.post(f"/reports/{rid}/vote", data={"csrf": CSRF})
    alice.post(f"/reports/{rid}/vote", data={"csrf": CSRF})
    assert mongo_db()["commuter_reports"].find_one({"_id": doc["_id"]})["votes"] == 1

    alice.post(f"/reports/{rid}/resolve", data={"csrf": CSRF})
    assert mongo_db()["commuter_reports"].find_one({"_id": doc["_id"]})["status"] == "resolved"
    alice.post(f"/reports/{rid}/delete", data={"csrf": CSRF})
    assert mongo_db()["commuter_reports"].find_one({"_id": doc["_id"]}) is None


def test_admin_pages_need_admin_role(client, alice):
    assert alice.get("/admin/stops").status_code == 403


def test_admin_stop_crud_keeps_places_in_sync(admin):
    data = {"bus_stop_code": "99050", "description": "New Stop", "road_name": "New Rd",
            "latitude": "1.31", "longitude": "103.81", "csrf": CSRF}
    admin.post("/admin/stops/new", data=data)
    place = mongo_db()["places"].find_one({"_id": "BS:99050"})
    assert place["location"]["coordinates"] == [103.81, 1.31]

    page = admin.post("/admin/stops/99050/edit", data={**data, "latitude": "9.99"},
                      follow_redirects=True).get_data(as_text=True)
    assert "chk_bus_stop_area" in page

    admin.post("/admin/stops/99050/edit", data={**data, "description": "Renamed Stop"})
    assert mongo_db()["places"].find_one({"_id": "BS:99050"})["name"] == "Renamed Stop"
    audit = root_execute("SELECT changed_by FROM audit_log WHERE row_key = '99050' ORDER BY log_id DESC LIMIT 1")
    assert audit[0]["changed_by"] == "root_admin"

    admin.post("/admin/stops/99050/delete", data={"csrf": CSRF})
    assert mongo_db()["places"].find_one({"_id": "BS:99050"}) is None
    page = admin.post("/admin/stops/99001/delete", data={"csrf": CSRF}, follow_redirects=True).get_data(as_text=True)
    assert "still used elsewhere" in page


def test_admin_route_edit_reports_procedure_errors(admin):
    page = admin.post("/admin/services/T2/1/stops", data={"stop_sequence": "2", "bus_stop_code": "99001",
                                                          "distance_km": "7", "csrf": CSRF},
                      follow_redirects=True).get_data(as_text=True)
    assert "Distance must lie between" in page


def test_analytics_and_reliability(client):
    assert client.get("/analytics/").status_code == 200
    today = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")
    mongo_db()["headway_daily"].insert_many([
        {"bus_stop_code": "99001", "service_no": "T2", "date": today, "hour": 8, "avg_gap_min": 21.0,
         "p90_gap_min": 25.0, "samples": 10},   # AM peak, scheduled 10-14: late
        {"bus_stop_code": "99001", "service_no": "T2", "date": today, "hour": 12, "avg_gap_min": 15.0,
         "p90_gap_min": 18.0, "samples": 10},   # AM off-peak, scheduled 12-20: fine
    ])
    page = client.get("/analytics/reliability?service=T2&days=1").get_data(as_text=True)
    assert "<td>0.667</td>" in page and "<td>1.0</td>" in page
