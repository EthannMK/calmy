"""The investigation agent uses its tools, advises only, and answers traveler questions."""
import os
import tempfile

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ["KODA_DB"] = os.path.join(tempfile.mkdtemp(), "g.db")
os.environ.pop("GOOGLE_CLOUD_PROJECT", None)
os.environ["KODA_NO_WEATHER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


def _held():
    client.post("/api/demo/reset")
    for pid, name in (("CL-44910", "A"), ("CL-44911", "B")):
        client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": True, "ack": True})
    client.post("/api/webhook/flight-event", json={"flight": "OA 108", "event": "delayed", "minutes": 200})
    return client.get("/api/console/review").json()


def test_investigation_runs_every_tool_and_never_declines():
    with client:
        held = _held()
        did = held[0]["id"] if held else client.get("/api/console/overview").json()["decisions"][0]["id"]
        r = client.get(f"/api/console/investigate/{did}").json()
    assert [s["tool"] for s in r["steps"]] == ["claim", "flight_status", "delay_forecast", "conditions", "policy_terms", "traveler_history"]
    assert r["recommendation"]["action"] in ("approve", "ask_proof", "escalate") and "decline" not in r["recommendation"]["action"]
    assert r["summary"] and r["ai"] is False


def test_unknown_claim_is_404():
    with client:
        assert client.get("/api/console/investigate/NOPE").status_code == 404


def test_traveler_question_is_answered_from_the_policy():
    with client:
        client.post("/api/demo/reset")
        r = client.post("/api/traveler/CL-44910/ask", json={"question": "When will I be paid?"}).json()
    assert "automatically" in r["answer"] and r["ai"] is False
