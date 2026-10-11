"""Edge cases, tier top-up, rule validation and the live flight adapter (mocked, no network)."""
import os
import tempfile
import time

os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "x.db")
os.environ.pop("AVIATIONSTACK_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402

from app import db, live  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def setup_function():
    live._cache.clear()
    os.environ.pop("AVIATIONSTACK_KEY", None)
    with client:
        client.post("/api/demo/reset")
    with db.conn() as c:
        c.execute("DELETE FROM api_usage")  # reset keeps the monthly count on purpose


def _buy(pid, delay=True, baggage=True):
    return client.post(f"/api/traveler/{pid}/checkout", json={"delay": delay, "baggage": baggage})


def _delay(m, flight="OA 108"):
    return client.post("/api/demo/delay", json={"flight": flight, "minutes": m}).json()


def test_tier_upgrade_tops_up_to_3000():
    with client:
        _buy("CL-44910")
        assert _delay(192)["results"][0]["status"] == "paid"
        r = _delay(370)["results"][0]
        assert r["status"] == "upgraded" and r["extra"] == 1500
        d = client.get("/api/traveler/CL-44910").json()["delay_decision"]
        assert d["amount"] == 3000 and d["delay_minutes"] == 370
        assert _delay(380)["results"] == []  # no third payment


def test_checkout_blocked_after_delay_known():
    with client:
        _delay(200)
        r = _buy("CL-44910")
        assert r.status_code == 400 and "already delayed" in r.json()["detail"]


def test_delay_without_cover_reports_zero_covered():
    with client:
        r = _delay(200)
        assert r["covered"] == 0 and r["results"] == []


def test_delay_validation():
    with client:
        assert client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": -5}).status_code == 422
        assert client.post("/api/demo/delay", json={"flight": "ZZ 1", "minutes": 200}).status_code == 404


def test_held_count_matches_queue_and_ignores_baggage():
    with client:
        _buy("CL-45102")
        _delay(192)
        client.post("/api/traveler/CL-45102/baggage", json={})
        ov = client.get("/api/console/overview").json()
        q = client.get("/api/console/review").json()
        assert ov["stats"]["held"] == len(q) == 1
        assert ov["stats"]["bag_waiting"] == 1


def test_rules_validation():
    with client:
        bad = [{"delay_tier1_minutes": 500}, {"risk_threshold": 0}, {"nope": 1}, {"delay_tier2_payout": 10}, {"delay_premium": -1}]
        for v in bad:
            assert client.put("/api/console/rules", json={"values": v}).status_code == 400, v
        ok = client.put("/api/console/rules", json={"values": {"delay_tier1_payout": 1800}})
        assert ok.status_code == 200 and ok.json()["delay_tier1_payout"] == 1800


def test_decline_review_keeps_money_unpaid():
    with client:
        _buy("CL-45102")
        _delay(192)
        did = client.get("/api/console/review").json()[0]["id"]
        r = client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "<b>x</b>"})
        assert r.json()["status"] == "declined"
        assert client.post(f"/api/console/review/{did}", json={"action": "approve"}).status_code == 400


# ------------------------------------------------------------- live adapter (mocked)

def _rec(iata="TG102", sched="2026-10-11T10:00:00+00:00", actual="2026-10-11T13:30:00+00:00", reported=5):
    return {
        "flight_date": "2026-10-11", "flight_status": "active",
        "departure": {"iata": "BKK", "airport": "Suvarnabhumi International Airport", "scheduled": sched, "actual": actual, "estimated": None, "delay": reported},
        "arrival": {"iata": "CNX", "airport": "Chiang Mai International Airport"},
        "airline": {"name": "Sample Air"}, "flight": {"iata": iata},
    }


def test_delay_uses_timestamps_not_reported_field():
    f = live.normalize(_rec())
    assert f["delay_minutes"] == 210 and f["code"] == "TG 102"


def test_live_disabled_without_key():
    with client:
        assert client.get("/api/live/status").json()["enabled"] is False
        assert client.get("/api/live/lookup", params={"flight": "TG102"}).status_code == 502


def test_live_replay_pays_and_labels_source(monkeypatch):
    os.environ["AVIATIONSTACK_KEY"] = "test"
    calls = []
    monkeypatch.setattr(live, "_http_get", lambda p: calls.append(p) or {"data": [_rec()]})
    with client:
        assert client.post("/api/demo/live", json={"policy": "CL-44910", "flight": "TG102"}).status_code == 400  # no cover yet
        _buy("CL-44910")
        r = client.post("/api/demo/live", json={"policy": "CL-44910", "flight": "TG102"}).json()
        assert r["paid"] and r["flight"]["delay_minutes"] == 210
        t = client.get("/api/traveler/CL-44910").json()
        assert t["flight"]["source"] == "live" and t["delay_decision"]["amount"] == 1500
        assert client.get("/api/live/status").json()["used"] == 1
        client.post("/api/demo/live", json={"policy": "CL-44910", "flight": "TG102"})
        assert len(calls) == 1  # second lookup came from the cache


def test_live_budget_guard(monkeypatch):
    os.environ["AVIATIONSTACK_KEY"] = "test"
    monkeypatch.setattr(live, "MONTHLY_BUDGET", 1)
    monkeypatch.setattr(live, "_http_get", lambda p: {"data": [_rec()]})
    with client:
        assert client.get("/api/live/lookup", params={"flight": "TG102"}).status_code == 200
        r = client.get("/api/live/lookup", params={"flight": "TG103"})
        assert r.status_code == 502 and "used up" in r.json()["detail"]


def test_live_short_delay_no_payout(monkeypatch):
    os.environ["AVIATIONSTACK_KEY"] = "test"
    monkeypatch.setattr(live, "_http_get", lambda p: {"data": [_rec(actual="2026-10-11T10:40:00+00:00")]})
    with client:
        _buy("CL-44910")
        r = client.post("/api/demo/live", json={"policy": "CL-44910", "flight": "TG102"}).json()
        assert r["paid"] is False and r["flight"]["delay_minutes"] == 40


def test_live_api_error_is_readable(monkeypatch):
    os.environ["AVIATIONSTACK_KEY"] = "test"
    monkeypatch.setattr(live, "_http_get", lambda p: {"error": {"message": "invalid key"}})
    with client:
        r = client.get("/api/live/lookup", params={"flight": "TG102"})
        assert r.status_code == 502 and "invalid key" in r.json()["detail"]
