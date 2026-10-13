"""Edge cases, tier top-up, rule validation and the live flight adapter (mocked, no network)."""
import os
import tempfile
import time

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ["KODA_DB"] = os.path.join(tempfile.mkdtemp(), "x.db")
os.environ.pop("AVIATIONSTACK_KEY", None)
os.environ["KODA_NO_WEATHER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app import db, live  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)

BAG = {"report_id": "PIR-BKK-90412", "bag_tag": "OA482103", "items": [
    {"name": "Hard-shell suitcase", "amount": 600}, {"name": "Essential clothing", "amount": 350},
    {"name": "Toiletries kit", "amount": 150}, {"name": "Chargers and accessories", "amount": 150}]}



def setup_function():
    live._cache.clear()
    os.environ.pop("AVIATIONSTACK_KEY", None)
    with client:
        client.post("/api/demo/reset")
    with db.conn() as c:
        c.execute("DELETE FROM api_usage")  # reset keeps the monthly count on purpose


def _buy(pid, delay=True, baggage=True):
    return client.post(f"/api/traveler/{pid}/checkout", json={"delay": delay, "baggage": baggage, "ack": True})


def _delay(m, flight="OA 108"):
    return client.post("/api/demo/delay", json={"flight": flight, "minutes": m}).json()


def test_tier_upgrade_tops_up_to_80():
    with client:
        _buy("CL-44910")
        assert _delay(192)["results"][0]["status"] == "paid"
        r = _delay(370)["results"][0]
        assert r["status"] == "upgraded" and r["extra"] == 40
        d = client.get("/api/traveler/CL-44910").json()["delay_decision"]
        assert d["amount"] == 80 and d["delay_minutes"] == 370
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
        client.post("/api/traveler/CL-45102/baggage", json=BAG)
        ov = client.get("/api/console/overview").json()
        q = client.get("/api/console/review").json()
        assert ov["stats"]["held"] == len(q) == 1
        assert ov["stats"]["bag_waiting"] == 1


def test_rules_validation():
    with client:
        bad = [{"delay_tier1_minutes": 500}, {"risk_threshold": 0}, {"nope": 1}, {"delay_tier2_payout": 10}, {"delay_premium": -1}]
        for v in bad:
            assert client.put("/api/console/rules", json={"values": v}).status_code == 400, v
        ok = client.put("/api/console/rules", json={"values": {"delay_tier1_payout": 50}})
        assert ok.status_code == 200 and ok.json()["delay_tier1_payout"] == 50


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
        assert t["flight"]["source"] == "live" and t["delay_decision"]["amount"] == 40
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


# ------------------------------------------------------------- markets and global behaviour

def _quote(name, last4, flight, rail=None):
    body = {"name": name, "last4": last4, "flight": flight}
    if rail:
        body["rail"] = rail
    return client.post("/api/policies", json=body)


def test_market_sets_currency_rails_and_rules():
    with client:
        r = _quote("Lena Fischer", "1234", "OA 420").json()  # Frankfurt to Madrid, EU
        assert r["market"]["currency"] == "SGD"
        pid = r["policy"]
        t = client.get(f"/api/traveler/{pid}").json()
        assert t["policy"]["payout_account"] == "SEPA Instant ending 1234"
        assert t["rules"]["delay_tier1_payout"] == 40 and t["market"]["statutory"].startswith("EU Regulation 261")


def test_wrong_rail_and_bad_input_rejected():
    with client:
        assert _quote("Lena Fischer", "1234", "OA 420", rail="PromptPay").status_code == 400
        assert _quote("L", "1234", "OA 420").status_code == 422
        assert _quote("Lena Fischer", "12a4", "OA 420").status_code == 422
        assert _quote("Lena Fischer", "1234", "ZZ 999").status_code == 503  # not a sample flight and no live key


def test_checkout_needs_acknowledgement():
    with client:
        pid = _quote("Ken Ito", "5555", "OA 310").json()["policy"]
        r = client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": False})
        assert r.status_code == 400 and "key facts" in r.json()["detail"]
        ok = client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": False, "ack": True}).json()
        assert ok["total"] == 5 and ok["currency"] == "SGD"  # UK price, shown in demo currency
        again = client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": False, "ack": True})
        assert again.status_code == 400


def test_payout_uses_market_currency_and_amounts():
    with client:
        pid = _quote("Ana Souza", "9876", "OA 970").json()["policy"]  # Brazil
        client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": True, "ack": True})
        r = client.post("/api/demo/delay", json={"flight": "OA 970", "minutes": 200}).json()["results"][0]
        assert r["status"] == "paid"
        d = client.get(f"/api/traveler/{pid}").json()["delay_decision"]
        assert d["amount"] == 45 and "S$45" in d["explanation"]


def test_rules_are_per_market():
    with client:
        assert client.put("/api/console/rules", params={"market": "EU"}, json={"values": {"delay_tier1_payout": 55}}).status_code == 200
        eu = {r["key"]: r["value"] for r in client.get("/api/console/rules", params={"market": "EU"}).json()}
        th = {r["key"]: r["value"] for r in client.get("/api/console/rules", params={"market": "TH"}).json()}
        assert eu["delay_tier1_payout"] == 55 and th["delay_tier1_payout"] == 40
        assert client.get("/api/console/rules", params={"market": "XX"}).status_code == 404


def test_risk_signals_are_computed_from_real_data():
    """Two people sharing one payout account, plus several covers in 2 hours, raise the score without any preset flags."""
    with client:
        ids = [_quote(n, "7777", "OA 530").json()["policy"] for n in ("Dan Cole", "Eve Park", "Fay Roe", "Gus Lim")]
        for pid in ids:
            client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": False, "ack": True})
        res = client.post("/api/demo/delay", json={"flight": "OA 530", "minutes": 200}).json()["results"]
        assert res and all(r["status"] == "held" for r in res)  # shared account + several covers in 2 hours


def test_declined_claim_can_be_reviewed_again():
    with client:
        pid = _quote("Hal Wu", "4444", "OA 750").json()["policy"]
        client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": False, "ack": True})
        # make it held by sharing the account across names
        other = _quote("Ivy Tan", "4444", "OA 750").json()["policy"]
        client.post(f"/api/traveler/{other}/checkout", json={"delay": True, "baggage": False, "ack": True})
        client.post("/api/demo/delay", json={"flight": "OA 750", "minutes": 200})
        q = client.get("/api/console/review").json()
        did = q[0]["id"]
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "x"})
        pol = q[0]["policy_id"]
        assert client.post(f"/api/traveler/{pol}/review-request").status_code == 400  # a second review needs a reason
        r = client.post(f"/api/traveler/{pol}/review-request", json={"note": "The airline letter says 3h20"})
        assert r.status_code == 200 and r.json()["status"] == "held"
        assert client.post(f"/api/traveler/{pol}/review-request", json={"note": "The airline letter says 3h20"}).status_code == 400  # nothing declined now


def test_bag_claim_needs_real_input():
    with client:
        _buy("CL-44910")
        assert client.post("/api/traveler/CL-44910/baggage", json={}).status_code == 422
        bad = {"report_id": "PIR-1", "bag_tag": "ABC123", "items": [{"name": "Bag", "amount": -5}]}
        assert client.post("/api/traveler/CL-44910/baggage", json=bad).status_code == 422


def test_timezone_picks_market():
    from app import markets
    assert markets.market_for_timezone("Europe/Paris") == "EU"
    assert markets.market_for_timezone("Europe/London") == "UK"
    assert markets.market_for_timezone("America/Chicago") == "US"
    assert markets.market_for_timezone("Asia/Bangkok") == "TH"
    assert markets.market_for_timezone(None) == "GLOBAL"


def test_real_flight_end_to_end_with_refresh(monkeypatch):
    """Pick a real flight, buy cover, then a later real status shows a long delay and the payout fires."""
    os.environ["AVIATIONSTACK_KEY"] = "test"
    future = time.time() + 3 * 3600
    from datetime import datetime, timezone
    sched = datetime.fromtimestamp(future, timezone.utc).isoformat()
    rec = _rec(sched=sched, actual=None)
    rec["departure"]["timezone"] = "Europe/Paris"
    rec["departure"]["estimated"] = None
    state = {"rec": rec}
    monkeypatch.setattr(live, "_http_get", lambda p: {"data": [state["rec"]]})
    with client:
        r = _quote("Mia Roy", "2468", "TG102").json()
        assert r["market"]["code"] == "EU"
        pid = r["policy"]
        client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": False, "ack": True})
        # later: the real record now shows an actual departure 4 hours late
        late = _rec(sched=sched, actual=datetime.fromtimestamp(future + 4 * 3600, timezone.utc).isoformat())
        late["departure"]["timezone"] = "Europe/Paris"
        state["rec"] = late
        live._cache.clear()
        out = client.post("/api/flights/TG 102/refresh").json()
        assert out["flight"]["delay_minutes"] == 240 and out["results"][0]["status"] == "paid"
        d = client.get(f"/api/traveler/{pid}").json()
        assert d["delay_decision"]["amount"] == 40 and d["flight"]["source"] == "live"


def test_departed_flight_cannot_be_quoted(monkeypatch):
    os.environ["AVIATIONSTACK_KEY"] = "test"
    monkeypatch.setattr(live, "_http_get", lambda p: {"data": [_rec(sched="2020-01-01T10:00:00+00:00", actual=None)]})
    with client:
        r = _quote("Old Trip", "1111", "TG102")
        assert r.status_code == 400 and "already left" in r.json()["detail"]
