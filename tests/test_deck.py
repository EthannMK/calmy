"""Cancellation, webhook, caring message, PNR and archive: the flows named in the submission deck."""
import os
import tempfile

os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "d.db")
os.environ.pop("AVIATIONSTACK_KEY", None)
os.environ["KODA_NO_WEATHER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def setup_function():
    with client:
        client.post("/api/demo/reset")


def _buy(pid):
    return client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": True, "ack": True})


def _hook(event, minutes=0, flight="OA 108"):
    return client.post("/api/webhook/flight-event", json={"flight": flight, "event": event, "minutes": minutes})


def test_cancellation_pays_at_once_with_message_and_five_steps():
    with client:
        _buy("CL-44910")
        r = _hook("cancelled").json()["results"][0]
        assert r["status"] == "paid"
        d = client.get("/api/traveler/CL-44910").json()["delay_decision"]
        assert d["amount"] == 80 and d["trigger"] == "cancel" and d["clause"].startswith("Clause 3.2")
        assert "was cancelled" in d["message"] and "80 USD" in d["message"]
        assert [s["label"] for s in d["timeline"]][-2:] == ["Paid", "Traveler notified"]
        assert d["context"]["source"].startswith("Simulated") and d["elapsed_ms"] >= 1


def test_cancelled_flight_blocks_new_cover_and_second_cancel_does_not_double_pay():
    with client:
        _buy("CL-44910")
        _hook("cancelled")
        assert _hook("cancelled").json()["results"] == []
        q = client.post("/api/policies", json={"name": "Late Buyer", "flight": "OA 108", "last4": "4321"}).json()
        assert _buy(q["policy"]).status_code == 400


def test_on_time_event_archives_quietly():
    with client:
        _buy("CL-44910")
        r = _hook("landed").json()
        assert r["archived"] == 1 and r["results"] == []
        assert client.get("/api/traveler/CL-44910").json()["policy"]["status"] == "archived"
        assert client.get("/api/traveler/CL-44910").json()["delay_decision"] is None


def test_risky_cancellation_is_held_with_a_kind_message():
    with client:
        _buy("CL-45102")
        r = _hook("cancelled").json()["results"][0]
        assert r["status"] == "held"
        d = client.get("/api/traveler/CL-45102").json()["delay_decision"]
        assert "specialist" in d["message"]


def test_policy_has_pnr_and_overview_reports_performance():
    with client:
        q = client.post("/api/policies", json={"name": "Pat", "flight": "OA 204", "last4": "1111"}).json()
        assert len(client.get(f"/api/traveler/{q['policy']}").json()["policy"]["pnr"]) == 6
        _buy("CL-44910")
        _hook("delayed", 200)
        perf = client.get("/api/console/overview").json()["performance"]
        assert perf["decisions"] == 1 and perf["documents_asked"] == 0 and perf["avg_decision_ms"] >= 1


def test_live_weather_is_used_when_cached(monkeypatch):
    from app import context
    context._cache["BKK"] = (__import__("time").time(), {
        "airport": "BKK", "weather": "Thunderstorm", "code": 95, "temp_c": 29, "wind_kmh": 55, "rain_mm": 4,
        "visibility_km": 0.8, "severe": True, "source": "Live weather, Open-Meteo"})
    try:
        with client:
            _buy("CL-44910")
            _hook("cancelled")
            d = client.get("/api/traveler/CL-44910").json()["delay_decision"]
            assert d["context"]["source"].startswith("Live weather") and "29 C" in d["context"]["text"]
            assert client.get("/api/traveler/CL-44910").json()["weather"]["severe"] is True
    finally:
        context._cache.clear()
