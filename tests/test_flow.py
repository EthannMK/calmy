"""End to end checks of the money path against the demo numbers."""
import os
import tempfile

os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "t.db")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)

BAG = {"report_id": "PIR-BKK-90412", "bag_tag": "OA482103", "items": [
    {"name": "Hard-shell suitcase", "amount": 600}, {"name": "Essential clothing", "amount": 350},
    {"name": "Toiletries kit", "amount": 150}, {"name": "Chargers and accessories", "amount": 150}]}



def setup_function():
    with client:
        client.post("/api/demo/reset")


def _activate(pid, delay=True, baggage=True):
    r = client.post(f"/api/traveler/{pid}/checkout", json={"delay": delay, "baggage": baggage, "ack": True})
    assert r.status_code == 200
    return r.json()


def test_checkout_total_is_208():
    with client:
        assert _activate("CL-44910")["total"] == 208


def test_clean_profile_paid_1500_on_192_minutes():
    with client:
        _activate("CL-44910")
        _activate("CL-45102")
        res = client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": 192}).json()["results"]
        by = {r["policy"]: r for r in res}
        assert by["CL-44910"]["status"] == "paid" and by["CL-44910"]["score"] == 12
        assert by["CL-45102"]["status"] == "held" and by["CL-45102"]["score"] == 91
        d = client.get("/api/traveler/CL-44910").json()["delay_decision"]
        assert d["amount"] == 1500


def test_under_threshold_pays_nothing():
    with client:
        _activate("CL-44910")
        res = client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": 150}).json()["results"]
        assert res == []


def test_six_hours_pays_3000():
    with client:
        _activate("CL-44910")
        client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": 370})
        assert client.get("/api/traveler/CL-44910").json()["delay_decision"]["amount"] == 3000


def test_no_double_claim():
    with client:
        _activate("CL-44910")
        client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": 192})
        again = client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": 200}).json()["results"]
        assert again == []


def test_held_case_can_be_approved_once():
    with client:
        _activate("CL-45102")
        client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": 185})
        q = client.get("/api/console/review").json()
        assert len(q) == 1 and q[0]["delay_minutes"] == 185
        ok = client.post(f"/api/console/review/{q[0]['id']}", json={"action": "approve", "note": "ok"})
        assert ok.json()["status"] == "paid"
        again = client.post(f"/api/console/review/{q[0]['id']}", json={"action": "decline"})
        assert again.status_code == 400


def test_rules_change_applies_to_next_trigger():
    with client:
        client.put("/api/console/rules", json={"values": {"delay_tier1_minutes": 120, "delay_tier1_payout": 800}})
        _activate("CL-44910")
        client.post("/api/demo/delay", json={"flight": "OA 108", "minutes": 130})
        assert client.get("/api/traveler/CL-44910").json()["delay_decision"]["amount"] == 800


def test_lost_bag_claim_is_1250_with_score_18():
    with client:
        _activate("CL-44910")
        b = client.post("/api/traveler/CL-44910/baggage", json=BAG).json()
        assert b["total"] == 1250
        d = client.get("/api/console/baggage").json()[0]["decision"]
        assert d["amount"] == 1250 and d["risk_score"] == 18 and d["status"] == "pending"
        done = client.post(f"/api/console/baggage/{b['id']}", json={"action": "approve"}).json()
        assert done["status"] == "paid"


def test_baggage_requires_cover():
    with client:
        _activate("CL-44910", baggage=False)
        assert client.post("/api/traveler/CL-44910/baggage", json=BAG).status_code == 400
