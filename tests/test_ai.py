"""AI value features: forecast, points and partner restaurants, adverse-selection check, exposure, copilot, rights."""
import os
import tempfile

os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "a.db")
os.environ.pop("AVIATIONSTACK_KEY", None)
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
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
    return client.post("/api/webhook/flight-event", json={"flight": flight, "event": event, "minutes": minutes}).json()


def test_forecast_rises_with_storm_and_alerts_only_covered_traveler():
    with client:
        _buy("CL-44910")
        calm = client.get("/api/traveler/CL-44910").json()
        assert calm["forecast"]["probability"] < 40 and calm["alert"] is None
        _hook("storm")
        hot = client.get("/api/traveler/CL-44910").json()
        assert hot["forecast"]["probability"] >= 55 and hot["alert"]["title"].startswith("A delay is likely")
        assert any("Thunderstorm" in t for t, _ in hot["forecast"]["drivers"])
        _hook("clear")
        assert client.get("/api/traveler/CL-44910").json()["alert"] is None


def test_buying_into_a_storm_is_flagged_and_held():
    with client:
        _hook("storm")
        _buy("CL-44910")
        r = _hook("delayed", 200)["results"][0]
        assert r["status"] == "held"
        d = client.get("/api/traveler/CL-44910").json()["delay_decision"]
        assert any("forecast" in t for t, _ in d["breakdown"])


def test_points_payout_credits_bonus_and_redeems_only_at_partners():
    with client:
        q = client.post("/api/policies", json={"name": "Pim Wong", "flight": "OA 108", "last4": "1111", "rail": "KODA points"}).json()
        pid = q["policy"]
        _buy(pid)
        _hook("delayed", 200)
        s = client.get(f"/api/traveler/{pid}").json()
        assert s["points"]["balance"] == 5000 and "points" in s["delay_decision"]["message"]  # 40 USD x 100 x 1.25
        lst = client.get(f"/api/airport/BKK/partners?pid={pid}").json()
        assert lst["partners"] and lst["not_partner"] and lst["pick"]
        assert all(p["accepts"] for p in lst["partners"])
        bad = next(p for p in client.get("/api/airport/BKK/partners").json()["not_partner"])
        with client:
            from app import db
            with db.conn() as c:
                no = db.one(c, "SELECT id FROM partners WHERE airport='BKK' AND accepts=0")["id"]
        assert client.post(f"/api/traveler/{pid}/redeem", json={"partner": no, "points": 500}).status_code == 400
        ok = client.post(f"/api/traveler/{pid}/redeem", json={"partner": lst["pick"], "points": 1500})
        assert ok.status_code == 200 and ok.json()["balance"] == 3500 and ok.json()["code"].startswith("KP-")
        assert client.post(f"/api/traveler/{pid}/redeem", json={"partner": lst["pick"], "points": 999999}).status_code == 400


def test_rights_card_depends_on_market_and_event():
    with client:
        _buy("CL-44910")
        assert client.get("/api/traveler/CL-44910").json()["rights"] is None
        _hook("delayed", 200)
        r = client.get("/api/traveler/CL-44910").json()["rights"]
        assert r and any(i["status"] == "owed" for i in r["items"]) and "not legal advice" in r["disclaimer"]


def test_exposure_and_price_follow_the_storm():
    with client:
        _buy("CL-44910")
        a = client.get("/api/console/exposure").json()
        _hook("storm")
        b = client.get("/api/console/exposure").json()
        assert b["expected_usd"] > a["expected_usd"] and b["reserve_usd"] >= b["expected_usd"]
        f = next(x for x in b["flights"] if x["flight"] == "OA 108")
        assert f["price_suggested"] >= f["price_now"] and f["covered"] == 1


def test_copilot_recommends_escalate_for_shared_account_and_never_declines():
    with client:
        _buy("CL-45102")
        r = _hook("delayed", 200)["results"][0]
        assert r["status"] == "held"
        rec = client.get(f"/api/console/copilot/{r['decision']}").json()
        assert rec["action"] == "escalate" and rec["confidence"] > 50 and "Only a person" in rec["never_declines"]
        assert "OA 108" in rec["draft"]
        a = client.post("/api/console/copilot", json={"decision": r["decision"], "question": "Why was it held?"}).json()
        assert "signals" in a["answer"]
