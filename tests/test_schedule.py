"""Saved real schedule: dates up to a month ahead, offline fallback, and booking a later day."""
import json
import os
import tempfile
from datetime import date, timedelta

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "s.db")
os.environ.pop("AVIATIONSTACK_KEY", None)
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
os.environ["KODA_NO_WEATHER"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import schedule  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
SNAP = {"generated_at": "2026-10-12T05:00:00+00:00", "flights": [
    {"code": "TG 102", "airline": "Thai Airways", "origin": "BKK", "origin_city": "Suvarnabhumi", "dest": "CNX", "dest_city": "Chiang Mai",
     "time": "09:30", "tz": "Asia/Bangkok", "market": "TH"},
    {"code": "FD 117", "airline": "Thai AirAsia", "origin": "BKK", "origin_city": "Suvarnabhumi", "dest": "HKT", "dest_city": "Phuket",
     "time": "07:10", "tz": "Asia/Bangkok", "market": "TH"},
]}


@pytest.fixture(autouse=True)
def snapshot(tmp_path):
    f = tmp_path / "snap.json"
    f.write_text(json.dumps(SNAP))
    old = schedule.PATH
    schedule.PATH = str(f)
    schedule._cache.update(mtime=None, data=None)
    with client:
        client.post("/api/demo/reset")
    yield
    schedule.PATH = old
    schedule._cache.update(mtime=None, data=None)


def _day(n):
    return (date.today() + timedelta(days=n)).isoformat()


def test_a_later_day_lists_saved_flights_earliest_first():
    with client:
        r = client.get(f"/api/live/schedule?airport=BKK&date={_day(5)}")
        assert r.status_code == 200
        codes = [f["code"] for f in r.json()]
        assert codes == ["FD 117", "TG 102"] and r.json()[0]["flight_date"] == _day(5)


def test_dates_outside_the_month_are_refused():
    with client:
        assert client.get(f"/api/live/schedule?airport=BKK&date={_day(45)}").status_code == 400
        assert client.get(f"/api/live/schedule?airport=BKK&date={_day(-5)}").status_code == 400


def test_status_is_ready_with_a_snapshot_and_no_live_key(monkeypatch):
    monkeypatch.delenv("AVIATIONSTACK_KEY", raising=False)
    with client:
        s = client.get("/api/live/status").json()
        assert s["enabled"] is False and s["ready"] is True and s["snapshot"]["flights"] == 2 and "BKK" in s["snapshot"]["airports"]


def test_book_a_later_day_and_buy_cover():
    with client:
        r = client.post("/api/policies", json={"name": "Lena Fischer", "last4": "1234", "flight": "TG 102", "date": _day(10), "channel": "Trip.com"})
        assert r.status_code == 200, r.text
        pid = r.json()["policy"]
        s = client.get(f"/api/traveler/{pid}").json()
        assert s["flight"]["flight_date"] == _day(10) and not s["flight"]["fetched_at"]
        assert client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": False, "ack": True}).status_code == 200


def test_unknown_flight_on_a_later_day_is_a_clear_error():
    with client:
        r = client.post("/api/policies", json={"name": "Lena Fischer", "last4": "1234", "flight": "XX 999", "date": _day(3)})
        assert r.status_code == 400 and "saved schedule" in r.json()["detail"]


def test_later_day_flight_is_not_refreshed_from_todays_live_status():
    from app import live, track
    with client:
        pid = client.post("/api/policies", json={"name": "Lena Fischer", "last4": "1234", "flight": "TG 102", "date": _day(2)}).json()["policy"]
        code = client.get(f"/api/traveler/{pid}").json()["flight"]["code"]
        old = live.lookup
        live.lookup = lambda c, d=None: {"code": code, "flight_date": _day(0), "delay_minutes": 300, "origin": "BKK", "status": "active"}
        try:
            out = track.refresh_flight(code)
        finally:
            live.lookup = old
        assert out["results"] == [] and "closer to the day" in out["note"]


def _book(ref=None, day=5):
    body = {"name": "Lena Fischer", "last4": "1234", "flight": "TG 102", "date": _day(day), "channel": "Trip.com"}
    if ref is not None:
        body["booking_ref"] = ref
    return client.post("/api/policies", json=body)


def test_the_travelers_own_booking_reference_is_stored():
    with client:
        r = _book("k7m2qd")
        assert r.status_code == 200
        pid = r.json()["policy"]
        assert client.get(f"/api/traveler/{pid}").json()["policy"]["pnr"] == "K7M2QD"  # kept, upper-cased


def test_a_malformed_booking_reference_is_refused():
    with client:
        assert _book("ab").status_code == 422
        assert _book("AB-12-34").status_code == 422


def test_a_lost_bag_cannot_be_reported_before_the_flight_leaves():
    with client:
        pid = _book("K7M2QD").json()["policy"]
        client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": True, "ack": True})
        r = client.post(f"/api/traveler/{pid}/baggage", json={"report_id": "PIR12345", "bag_tag": "TG123456", "items": [{"name": "Shirt", "amount": 20}]})
        assert r.status_code == 400 and "once your flight has left" in r.json()["detail"]
