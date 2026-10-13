"""Hand-offs between the traveler app and the insurer console: one claim id end to end, second review, asking the
traveler for information, decisions landing in the traveler's Messages, and payouts that cannot repeat."""
import os
import tempfile
import time

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ.setdefault("CALMY_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, live  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def setup_function():
    with client:
        client.post("/api/demo/reset")


def _trip_under_the_rule():
    """A traveler with an account and delay cover, whose flight was 2 hours late: no payout, so they can ask for a re-check."""
    q = client.post("/api/policies", json={"name": "Mali Test", "flight": "OA 204"}).json()
    pid, cid = q["policy"], q["customer"]
    assert client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": True, "ack": True}).status_code == 200
    assert client.post("/api/demo/delay", json={"flight": "OA 204", "minutes": 120}).json()["results"] == []
    with db.conn() as c:  # the booking row is what puts the trip on the traveler's home screen
        c.execute("UPDATE flights SET sched_ts=? WHERE code='OA 204'", (time.time() - 6 * 3600,))  # the flight has left, like the Jakarta trip
        pnr = db.one(c, "SELECT pnr FROM policies WHERE id=?", (pid,))["pnr"]
        c.execute("INSERT INTO bookings (ref,leg,passenger,flight,trip,channel,customer_id) VALUES (?,1,?,?,'One way','Airline site',?)",
                  (pnr, "Mali Test", "OA 204", cid))
    return pid, {"x-customer": cid}, cid


def _leg(h, pid):
    return next(g for t in h["trips"] for g in t["legs"] if g["policy"] == pid)


def _claim(pid):
    with db.conn() as c:
        return db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (pid,))


def _chat(cid):
    with db.conn() as c:
        ch = db.one(c, "SELECT * FROM chats WHERE customer_id=? AND status='open'", (cid,))
        return ch, db.rows(c, "SELECT * FROM chat_messages WHERE chat_id=? ORDER BY ts, rowid", (ch["id"],)) if ch else []


def test_recheck_reaches_the_queue_with_the_same_claim_id():
    with client:
        pid, H, _ = _trip_under_the_rule()
        r = client.post("/api/app/recheck", json={"policy": pid, "note": "Airline app said 3h20"}, headers=H)
        assert r.status_code == 200
        did = r.json()["id"]
        q = {x["id"]: x for x in client.get("/api/console/review").json()}
        assert q[did]["source"] == "recheck" and q[did]["policy"]["id"] == pid and "3h20" in q[did]["note"]


def test_declined_claim_gets_one_second_review_then_is_final():
    with client:
        pid, H, _ = _trip_under_the_rule()
        did = client.post("/api/app/recheck", json={"policy": pid, "note": "first"}, headers=H).json()["id"]
        assert client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "airline says 2h"}).json()["status"] == "declined"
        assert "second review" in _claim(pid)["message"]

        # The button the traveler sees on a declined claim sends them through the same re-check endpoint.
        r = client.post("/api/app/recheck", json={"policy": pid, "note": "I have the airline email"}, headers=H)
        assert r.status_code == 200 and r.json() == {"id": did, "status": "held"}  # same claim, back with a person
        q = {x["id"]: x for x in client.get("/api/console/review").json()}
        assert q[did]["second_review"] and "airline email" in q[did]["note"] and "airline says 2h" in q[did]["note"]

        assert client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "still 2h"}).status_code == 200
        assert "final" in _claim(pid)["message"]
        r = client.post("/api/app/recheck", json={"policy": pid, "note": "again"}, headers=H)
        assert r.status_code == 400 and "already had a second review" in r.json()["detail"]
        # The legacy endpoint follows the same rule.
        assert client.post(f"/api/traveler/{pid}/review-request").status_code == 400


def test_traveler_home_offers_second_review_only_once():
    with client:
        pid, H, _ = _trip_under_the_rule()
        g = _leg(client.get("/api/app/home", headers=H).json(), pid)
        assert g["state"] == "not_covered" and "recheck" in g["can"]
        did = client.post("/api/app/recheck", json={"policy": pid, "note": ""}, headers=H).json()["id"]
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "Airline record shows 2h"})
        g = _leg(client.get("/api/app/home", headers=H).json(), pid)
        assert g["state"] == "declined" and "review" in g["can"]
        client.post("/api/app/recheck", json={"policy": pid, "note": "The airline letter says 3h20"}, headers=H)
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "Letter checked, still 2h"})
        g = _leg(client.get("/api/app/home", headers=H).json(), pid)
        assert g["state"] == "declined" and "review" not in g["can"]


def test_approval_pays_once_and_lands_in_the_travelers_messages():
    with client:
        pid, H, cid = _trip_under_the_rule()
        did = client.post("/api/app/recheck", json={"policy": pid, "note": "3h20"}, headers=H).json()["id"]
        d = client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "airline record 3h20", "verified_minutes": 200}).json()
        assert d["status"] == "paid" and d["decided_by"]
        assert client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "again", "verified_minutes": 200}).status_code == 400  # double click
        _, msgs = _chat(cid)
        staff = [m for m in msgs if m["role"] == "staff"]
        assert len(staff) == 1 and did in staff[0]["text"] and staff[0]["author"] == d["decided_by"]
        with db.conn() as c:
            earn = db.rows(c, "SELECT * FROM wallet_tx WHERE customer_id=? AND kind='earn'", (cid,))
        assert len(earn) == 1 and earn[0]["id"] == "earn:" + did


def test_reviewer_can_ask_the_traveler_and_the_claim_stays_held():
    with client:
        pid, H, cid = _trip_under_the_rule()
        did = client.post("/api/app/recheck", json={"policy": pid, "note": ""}, headers=H).json()["id"]
        r = client.post(f"/api/console/review/{did}/ask", json={"text": "Could you send the airline's delay email?"})
        assert r.status_code == 200
        ch, msgs = _chat(cid)
        assert ch["mode"] == "human"  # the traveler's reply goes to a person, not the assistant
        asked = [m for m in msgs if m["role"] == "staff"]
        assert asked and did in asked[-1]["text"] and "delay email" in asked[-1]["text"]
        d = _claim(pid)
        assert d["status"] == "held" and "delay email" in d["message"]
        q = {x["id"]: x for x in client.get("/api/console/review").json()}
        assert q[did]["asked"]["text"].startswith("Could you send")
        # Asking twice does not stack two "joined" notices.
        client.post(f"/api/console/review/{did}/ask", json={"text": "And the boarding pass, please."})
        _, msgs = _chat(cid)
        assert sum(1 for m in msgs if m["role"] == "system" and "joined" in m["text"]) == 1
        # Only a held claim can be asked about.
        client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "Email confirms 3h20", "verified_minutes": 200})
        assert client.post(f"/api/console/review/{did}/ask", json={"text": "One more thing?"}).status_code == 400


def test_live_replay_never_pays_a_claim_twice(monkeypatch):
    with client:
        pid, H, cid = _trip_under_the_rule()
        client.post("/api/demo/delay", json={"flight": "OA 204", "minutes": 200})
        before = _claim(pid)
        assert before and before["status"] in ("paid", "held")
        monkeypatch.setattr(live, "lookup", lambda code: (_ for _ in ()).throw(AssertionError("no lookup expected")))
        r = client.post("/api/demo/live", json={"policy": pid, "flight": "OA204"})
        assert r.status_code == 400 and "already has a claim" in r.json()["detail"]
        assert _claim(pid)["id"] == before["id"]


def test_demo_tools_are_off_outside_demo_mode(monkeypatch):
    monkeypatch.setenv("KODA_DEMO_CODES", "off")
    with client:
        assert client.post("/api/demo/reset").status_code == 404
        assert client.post("/api/demo/delay", json={"flight": "OA 204", "minutes": 200}).status_code == 404


def test_webhook_secret_is_enforced_when_set(monkeypatch):
    monkeypatch.setenv("KODA_WEBHOOK_SECRET", "s3cret")
    with client:
        ev = {"flight": "OA 204", "event": "on_time"}
        assert client.post("/api/webhook/flight-event", json=ev).status_code == 401
        assert client.post("/api/webhook/flight-event", json=ev, headers={"x-webhook-secret": "wrong"}).status_code == 401
        assert client.post("/api/webhook/flight-event", json=ev, headers={"x-webhook-secret": "s3cret"}).status_code == 200


def test_both_apps_read_the_same_case_history():
    with client:
        pid, H, _ = _trip_under_the_rule()
        did = client.post("/api/app/recheck", json={"policy": pid, "note": "3h20 on the airline app"}, headers=H).json()["id"]
        client.post(f"/api/console/review/{did}/ask", json={"text": "Could you send the airline's email?"})
        client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "email confirms 3h20", "verified_minutes": 200})
        mine = [x["type"] for x in client.get(f"/api/app/history/{pid}", headers=H).json()["history"]]
        desk = client.get(f"/api/console/decision/{pid}").json()["history"]
        assert mine == [x["type"] for x in desk]  # same steps, same order
        assert mine[:2] == ["policy_active", "delay_detected"] and mine[-4:] == ["recheck_requested", "info_requested", "evidence_verified", "review_paid"]
        traveler_text = " ".join(x["what"] for x in client.get(f"/api/app/history/{pid}", headers=H).json()["history"])
        assert "risk score" not in traveler_text and "email confirms" not in traveler_text  # staff-only details stay on the desk
        assert any("email confirms" in x["what"] for x in desk)
        other = client.post("/api/policies", json={"name": "Someone Else", "flight": "OA 108"}).json()["customer"]
        assert client.get(f"/api/app/history/{pid}", headers={"x-customer": other}).status_code == 404


def test_add_a_booking_bought_elsewhere_joins_the_account_once():
    with client:
        q = client.post("/api/policies", json={"name": "Nok Lee", "flight": "OA 204"}).json()
        me, other = q["customer"], client.post("/api/policies", json={"name": "Ploy Lee", "flight": "OA 108"}).json()["customer"]
        with db.conn() as c:
            c.execute("INSERT INTO bookings (ref,leg,passenger,flight,trip,channel,customer_id) VALUES ('AGD123',1,'Nok Lee','OA 204','One way','Agoda',NULL)")
            c.execute("INSERT INTO bookings (ref,leg,passenger,flight,trip,channel,customer_id) VALUES ('THEIRS',1,'Ploy Lee','OA 108','One way','Agoda',?)", (other,))
        H = {"x-customer": me}
        assert client.post("/api/app/link-booking", json={"ref": "AGD123", "surname": "Wrong"}, headers=H).status_code == 404
        r = client.post("/api/app/link-booking", json={"ref": "agd-123", "surname": "Lee"}, headers=H).json()
        assert r["added"] and r["customer_id"] == me and r["legs"][0]["missing_addable"] == ["delay", "baggage"]
        assert client.post("/api/app/link-booking", json={"ref": "AGD123", "surname": "Lee"}, headers=H).json()["added"] is False
        # Someone else's booking looks exactly like a wrong reference.
        theirs = client.post("/api/app/link-booking", json={"ref": "THEIRS", "surname": "Lee"}, headers=H)
        wrong = client.post("/api/app/link-booking", json={"ref": "NOPE99", "surname": "Lee"}, headers=H)
        assert theirs.status_code == wrong.status_code == 404 and theirs.json() == wrong.json()
        # Once linked, cover can be added through the normal add-cover path.
        a = client.post("/api/app/add-cover", json={"ref": "AGD123", "flight": "OA 204", "delay": True, "baggage": False, "ack": True}, headers=H)
        assert a.status_code == 200


def test_lost_bag_can_be_claimed_after_the_flight_lands():
    with client:
        q = client.post("/api/policies", json={"name": "Bag Landed", "flight": "OA 204"}).json()
        client.post(f"/api/traveler/{q['policy']}/checkout", json={"delay": True, "baggage": True, "ack": True})
        q2 = client.post("/api/policies", json={"name": "Delay Only", "flight": "OA 204"}).json()
        client.post(f"/api/traveler/{q2['policy']}/checkout", json={"delay": True, "baggage": False, "ack": True})
        with db.conn() as c:
            c.execute("UPDATE flights SET sched_ts=? WHERE code='OA 204'", (time.time() - 4 * 3600,))
        assert client.post("/api/webhook/flight-event", json={"flight": "OA 204", "event": "landed"}).json()["archived"] >= 1
        r = client.post(f"/api/traveler/{q['policy']}/baggage", json={"report_id": "PIR123", "bag_tag": "OA1234", "items": [{"name": "Suitcase", "amount": 30}]})
        assert r.status_code == 200
        # A delay-only trip still says so plainly.
        r = client.post(f"/api/traveler/{q2['policy']}/baggage", json={"report_id": "PIR124", "bag_tag": "OA1235", "items": [{"name": "Suitcase", "amount": 30}]})
        assert r.status_code == 400 and "no baggage cover" in r.json()["detail"]


def test_delay_after_trip_closed_goes_to_review_once_and_pays_nothing_by_itself():
    with client:
        q = client.post("/api/policies", json={"name": "Closed Early", "flight": "OA 204"}).json()
        client.post(f"/api/traveler/{q['policy']}/checkout", json={"delay": True, "baggage": False, "ack": True})
        client.post("/api/webhook/flight-event", json={"flight": "OA 204", "event": "on_time"})
        for _ in range(2):  # the same late report twice
            client.post("/api/webhook/flight-event", json={"flight": "OA 204", "event": "delayed", "minutes": 250})
        info = client.get(f"/api/console/decision/{q['policy']}").json()
        d = info["decision"]
        assert d["status"] == "held" and d["trigger"] == "late_report" and len(d["evidence"]["reports"]) == 1
        assert d["evidence"]["previous"]["status"].startswith("on time")
        q2 = [x for x in client.get("/api/console/review").json() if x["policy_id"] == q["policy"]]
        assert len(q2) == 1  # one review task, not two
        with db.conn() as c:
            assert not db.rows(c, "SELECT * FROM payments WHERE decision_id=?", (d["id"],))  # nothing sent


def test_auto_paid_claim_lists_only_checks_that_ran():
    with client:
        q = client.post("/api/policies", json={"name": "Evidence", "flight": "OA 204"}).json()
        client.post(f"/api/traveler/{q['policy']}/checkout", json={"delay": True, "baggage": False, "ack": True})
        client.post("/api/demo/delay", json={"flight": "OA 204", "minutes": 200})
        d = client.get(f"/api/console/decision/{q['policy']}").json()["decision"]
        assert d["status"] == "paid" and not any("PromptPay" in r for r in d["reasons"])
        assert any("meets the" in r for r in d["reasons"]) and any("under the auto-pay limit" in r for r in d["reasons"])


def test_model_rewrites_cannot_add_numbers_or_decisions():
    from app import copilot
    base = "A delay of 180 minutes or more pays S$40."
    assert copilot.safe_rewrite(base, "Good news, a 180 minute delay pays S$40.") is not None
    assert copilot.safe_rewrite(base, "Your claim is approved: S$900 today.") is None
    assert copilot.safe_rewrite(base, "We guarantee a delay of 180 minutes pays S$40.") is None
    assert copilot.safe_rewrite(base, None) is None
