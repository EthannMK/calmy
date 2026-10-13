"""The five rule decisions, as implemented (see docs/INSURANCE_RULES.md):

A. A delayed flight that is later cancelled: the cancellation raises the total, only the difference is sent.
B. A delay reported after the trip was closed as on time: a person reviews it; nothing is paid or refused by itself.
C. Live data pays on the actual departure time; an estimate is a forecast.
D. A re-check is paid from the verified delay through the same tiers; a reviewer cannot type an amount.
E. One second review per declined claim, with a reason; beyond that only an admin can reopen, with a reason.
"""
import os
import tempfile
import threading

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ.setdefault("KODA_DB", os.path.join(tempfile.mkdtemp(), "t.db"))

from fastapi.testclient import TestClient  # noqa: E402

from app import db, engine, live, payments, track  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
FL = "OA 204"   # Thai market sample flight: tier 1 from 180 min pays 40, tier 2 from 360 min pays 80, cancelled pays 80


def setup_function():
    payments.FAIL_NEXT = 0
    with client:
        client.post("/api/demo/reset")


def _cover(name, delay=True, bag=False, card=False):
    body = {"name": name, "flight": FL}
    if card:  # an older checkout path that pays to a card-style rail instead of points
        body.update(rail="PromptPay", last4="4921")
    q = client.post("/api/policies", json=body).json()
    client.post(f"/api/traveler/{q['policy']}/checkout", json={"delay": delay, "baggage": bag, "ack": True})
    return q["policy"]


def _delay(minutes, cancelled=False):
    return client.post("/api/demo/delay", json={"flight": FL, "minutes": minutes, "cancelled": cancelled}).json()


def _claim(pid):
    with db.conn() as c:
        return db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (pid,))


def _pays(did):
    with db.conn() as c:
        return db.rows(c, "SELECT seq, amount, status FROM payments WHERE decision_id=? ORDER BY seq", (did,))


def _rules():
    with db.conn() as c:
        return engine.get_rules(c, "TH")


# ------------------------------------------------------------------ A. delay, then cancellation

def test_a_cancellation_after_delay_payout_tops_up_to_the_total_once():
    with client:
        pid = _cover("Ana Top")
        _delay(200)
        d = _claim(pid)
        assert d["status"] == "paid" and d["amount"] == 40 and [p["amount"] for p in _pays(d["id"])] == [40]
        _delay(0, cancelled=True)
        d = _claim(pid)
        assert d["amount"] == 80 and [(p["amount"], p["status"]) for p in _pays(d["id"])] == [(40, "succeeded"), (40, "succeeded")]
        for _ in range(3):  # duplicate cancellation events
            _delay(0, cancelled=True)
        assert [p["amount"] for p in _pays(d["id"])] == [40, 40]
        hist = [h["what"] for h in client.get(f"/api/console/decision/{pid}").json()["history"]]
        assert any("Flight cancelled after the delay payout" in h for h in hist)  # the original payment and the top-up are both on record


def test_a_cancellation_before_any_delay_pays_the_cancellation_amount():
    with client:
        pid = _cover("Ben Cancel")
        _delay(0, cancelled=True)
        d = _claim(pid)
        assert d["amount"] == 80 and [p["amount"] for p in _pays(d["id"])] == [80]


def test_a_amount_already_paid_is_not_counted_twice():
    with client:
        pid = _cover("Cat Twice")
        _delay(400)  # tier 2 pays 80, the same as a cancellation
        _delay(0, cancelled=True)
        d = _claim(pid)
        assert d["amount"] == 80 and [p["amount"] for p in _pays(d["id"])] == [80]  # nothing more owed


def test_a_cancellation_not_covered_pays_nothing():
    with client:
        pid = _cover("Dee Bagonly", delay=False, bag=True)
        _delay(0, cancelled=True)
        assert _claim(pid) is None


def test_a_failed_payment_is_retried_in_place_never_duplicated():
    with client:
        pid = _cover("Eve Card", card=True)
        payments.FAIL_NEXT = 1
        _delay(200)
        d = _claim(pid)
        rows = _pays(d["id"])
        assert d["status"] == "paid" and [(r["amount"], r["status"]) for r in rows] == [(40, "failed")]
        _delay(200)  # the same event again must not send a second payment
        assert len(_pays(d["id"])) == 1
        with db.conn() as c:
            pay_id = db.one(c, "SELECT id FROM payments WHERE decision_id=?", (d["id"],))["id"]
        r = client.post(f"/api/console/payments/{pay_id}/retry").json()
        assert r["status"] == "succeeded" and r["attempts"] == 2
        assert client.post(f"/api/console/payments/{pay_id}/retry").status_code == 400  # nothing left to retry
        assert [(x["amount"], x["status"]) for x in _pays(d["id"])] == [(40, "succeeded")]


def test_a_nothing_is_sent_for_a_claim_that_is_not_approved():
    with client:
        pid = _cover("Fay Risky")
        with db.conn() as c:
            c.execute("UPDATE policies SET f_shared_account=1, f_schedule_changed=1, f_covers_2h=4 WHERE id=?", (pid,))
        _delay(200)
        d = _claim(pid)
        assert d["status"] == "held" and _pays(d["id"]) == []
        _delay(0, cancelled=True)  # a held claim's total rises, but still nothing is sent
        assert _claim(pid)["amount"] == 80 and _pays(d["id"]) == []


# ------------------------------------------------------------------ B. late report after "on time"

def _closed_then_reported(minutes, cancelled=False, source_event="delayed"):
    pid = _cover("Gus Late")
    client.post("/api/webhook/flight-event", json={"flight": FL, "event": "on_time"})
    client.post("/api/webhook/flight-event", json={"flight": FL, "event": "cancelled" if cancelled else source_event, "minutes": minutes})
    return pid


def test_b_late_report_goes_to_review_with_both_reports():
    with client:
        pid = _closed_then_reported(250)
        info = client.get(f"/api/console/decision/{pid}").json()
        d, ev = info["decision"], info["decision"]["evidence"]
        assert d["status"] == "held" and d["trigger"] == "late_report"
        assert ev["conflict"] and ev["previous"]["status"].startswith("on time") and ev["reports"][0]["minutes"] == 250
        assert ev["reports"][0]["source"] == "flight status webhook"
        assert any("disagree" in r for r in d["reasons"]) and _pays(d["id"]) == []
        assert info["assessment"]["needs_verified_delay"] is True


def test_b_conflicting_and_duplicate_reports_update_one_review():
    with client:
        pid = _closed_then_reported(250)
        client.post("/api/webhook/flight-event", json={"flight": FL, "event": "delayed", "minutes": 250})  # duplicate
        client.post("/api/demo/delay", json={"flight": FL, "minutes": 400})  # a different source and figure
        d = client.get(f"/api/console/decision/{pid}").json()["decision"]
        assert [(r["minutes"], r["source"]) for r in d["evidence"]["reports"]] == [(250, "flight status webhook"), (400, "demo controls")]
        assert len([x for x in client.get("/api/console/review").json() if x["policy_id"] == pid]) == 1


def test_b_report_without_a_qualifying_delay_creates_no_task():
    with client:
        pid = _closed_then_reported(0)  # a "delayed" report with no figure
        assert _claim(pid) is None


def test_b_resolution_needs_the_verified_record_and_a_reason():
    with client:
        pid = _closed_then_reported(250)
        did = _claim(pid)["id"]
        assert client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "ok"}).status_code == 400
        assert client.post(f"/api/console/review/{did}", json={"action": "approve", "verified_minutes": 250}).status_code == 400  # no reason
        assert client.post(f"/api/console/review/{did}", json={"action": "decline"}).status_code == 400  # no reason
        r = client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "Airline record: 4h10", "verified_minutes": 250})
        assert r.status_code == 200 and r.json()["status"] == "paid" and r.json()["amount"] == 40
        assert [p["amount"] for p in _pays(did)] == [40]
        assert client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "again", "verified_minutes": 250}).status_code == 400
        assert [p["amount"] for p in _pays(did)] == [40]


# ------------------------------------------------------------------ C. what counts as the delay (live data)

def _live(dep, arr=None, status="active", date="2026-10-11"):
    return {"flight_date": date, "flight_status": status, "departure": {"iata": "BKK", "airport": "Suvarnabhumi", **dep},
            "arrival": {"iata": "CNX", "airport": "Chiang Mai", **(arr or {})}, "airline": {"name": "Sample Air"}, "flight": {"iata": "TG102"}}


def _track(monkeypatch, *records):
    """Cover a live flight, then feed it one status record after another through the normal refresh path."""
    first = live.normalize(records[0])
    with db.conn() as c:
        live.upsert_flight(c, {**first, "delay_minutes": 0})
        c.execute("UPDATE flights SET sched_ts=? WHERE code=?", (4102444800, first["code"]))  # far future, so cover can be sold
        # Cover bought before the flight (the purchase path is tested elsewhere; this exercises what live data pays).
        pid = engine._new_id("CL")
        c.execute("INSERT INTO policies (id,traveler,flight,payout_account,delay_cover,baggage_cover,premium,status,market,currency,created_at) "
                  "VALUES (?,?,?,?,1,0,5,'active','TH','THB',?)", (pid, "Hal Live", first["code"], "KODA points wallet", 0))
    for rec in records:
        f = live.normalize(rec)
        monkeypatch.setattr(live, "lookup", lambda code, date=None, f=f: f)
        track.refresh_flight(first["code"])
    return pid, first["code"]


S = "2026-10-11T10:00:00+00:00"


def test_c_estimate_is_a_forecast_and_pays_only_once_actual(monkeypatch):
    with client:
        pid, code = _track(monkeypatch, _live({"scheduled": S, "estimated": "2026-10-11T13:20:00+00:00"}))
        assert _claim(pid) is None
        with db.conn() as c:
            f = db.one(c, "SELECT delay_minutes, delay_basis FROM flights WHERE code=?", (code,))
        assert (f["delay_minutes"], f["delay_basis"]) == (200, "estimated")  # shown as a forecast
        monkeypatch.setattr(live, "lookup", lambda c_, d=None: live.normalize(_live({"scheduled": S, "actual": "2026-10-11T13:20:00+00:00"})))
        track.refresh_flight(code)
        assert _claim(pid)["amount"] == 40


def test_c_forecast_that_shrinks_pays_nothing(monkeypatch):
    with client:
        pid, _ = _track(monkeypatch, _live({"scheduled": S, "estimated": "2026-10-11T13:20:00+00:00"}),
                        _live({"scheduled": S, "actual": "2026-10-11T11:40:00+00:00"}))
        assert _claim(pid) is None


def test_c_pay_on_estimate_switch_restores_the_old_behaviour(monkeypatch):
    monkeypatch.setenv("KODA_PAY_ON_ESTIMATE", "1")
    with client:
        pid, _ = _track(monkeypatch, _live({"scheduled": S, "estimated": "2026-10-11T13:20:00+00:00"}))
        assert _claim(pid)["amount"] == 40


def test_c_departure_is_what_counts(monkeypatch):
    with client:
        on_time_late_arrival = _live({"scheduled": S, "actual": "2026-10-11T10:05:00+00:00"},
                                     {"scheduled": "2026-10-11T11:15:00+00:00", "actual": "2026-10-11T15:30:00+00:00"})
        pid, _ = _track(monkeypatch, on_time_late_arrival)
        assert _claim(pid) is None
    setup_function()
    with client:
        late_departure_early_arrival = _live({"scheduled": S, "actual": "2026-10-11T13:05:00+00:00"},
                                             {"scheduled": "2026-10-11T11:15:00+00:00", "actual": "2026-10-11T13:40:00+00:00"})
        pid, _ = _track(monkeypatch, late_departure_early_arrival)
        assert _claim(pid)["amount"] == 40  # 3h05 late leaving: covered under the departure rule


def test_c_delay_across_midnight_and_time_zones():
    rec = _live({"scheduled": "2026-10-11T23:30:00+00:00", "actual": "2026-10-12T02:45:00+00:00", "timezone": "Asia/Bangkok"})
    assert live.delay_info(rec["departure"]) == (195, "actual")
    sg = _live({"scheduled": "2026-10-11T23:50:00+00:00", "actual": "2026-10-12T05:50:00+00:00", "timezone": "Asia/Singapore"})
    assert live.delay_info(sg["departure"])[0] == 360


def test_c_missing_contradictory_and_reported_only_data():
    assert live.normalize(_live({"actual": "2026-10-11T13:00:00+00:00"})) is None              # no scheduled time: unusable
    assert live.delay_info({"scheduled": S, "actual": "2026-10-11T09:40:00+00:00"}) == (0, "actual")  # left early: not a delay
    assert live.delay_info({"scheduled": S, "delay": 200}) == (200, "reported")                 # feed field only: a forecast
    assert live.delay_info({"scheduled": S}) == (0, "none")
    assert track.payable({"delay_basis": "reported", "status": "active"}) is False
    assert track.payable({"delay_basis": "none", "status": "cancelled"}) is True                # a cancellation is final


def test_c_boundary_and_later_correction(monkeypatch):
    with client:
        pid, code = _track(monkeypatch, _live({"scheduled": S, "actual": "2026-10-11T13:00:00+00:00"}))  # exactly 180
        assert _claim(pid)["amount"] == 40
        monkeypatch.setattr(live, "lookup", lambda c_, d=None: live.normalize(_live({"scheduled": S, "actual": "2026-10-11T12:30:00+00:00"})))
        track.refresh_flight(code)  # the airline corrects it to 150 after payment
        d = _claim(pid)
        assert d["amount"] == 40 and [p["amount"] for p in _pays(d["id"])] == [40]  # not clawed back, not paid again


# ------------------------------------------------------------------ D. re-check paid from the verified delay

def _recheck(name="Ivy Recheck"):
    pid = _cover(name)
    _delay(120)  # recorded 2h: under the rule
    cid = client.get(f"/api/traveler/{pid}").json()["customer"]["id"]
    with db.conn() as c:
        pnr = db.one(c, "SELECT pnr FROM policies WHERE id=?", (pid,))["pnr"]
        c.execute("INSERT OR REPLACE INTO bookings (ref,leg,passenger,flight,trip,channel,customer_id) VALUES (?,1,?,?,'One way','Agoda',?)", (pnr, name, FL, cid))
    did = client.post("/api/app/recheck", json={"policy": pid, "note": "Airline said 3h20"}, headers={"x-customer": cid}).json()["id"]
    return pid, did, cid


def test_d_recheck_tier_comes_from_the_verified_delay():
    expect = {179: None, 180: 40, 359: 40, 360: 80}
    for minutes, amount in expect.items():
        setup_function()
        with client:
            pid, did, _ = _recheck()
            assert _claim(pid)["amount"] == 0  # no tier is assumed while the record is in doubt
            r = client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "Airline record checked", "verified_minutes": minutes})
            if amount is None:
                assert r.status_code == 400 and "under the" in r.json()["detail"]
            else:
                assert r.status_code == 200 and r.json()["amount"] == amount and [p["amount"] for p in _pays(did)] == [amount]
    setup_function()
    with client:
        pid, did, _ = _recheck()
        r = client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "Airline: cancelled", "verified_cancelled": True})
        assert r.json()["amount"] == 80


def test_d_reviewer_cannot_type_an_amount_and_repeats_pay_once():
    with client:
        pid, did, _ = _recheck()
        r = client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "Airline 3h20", "verified_minutes": 200, "amount": 9999})
        assert r.json()["amount"] == 40
        assert client.post(f"/api/console/review/{did}", json={"action": "approve", "note": "again", "verified_minutes": 400}).status_code == 400
        assert [p["amount"] for p in _pays(did)] == [40]
        hist = client.get(f"/api/console/decision/{pid}").json()
        assert hist["decision"]["evidence"]["verified"]["minutes"] == 200 and hist["assessment"]["sent"] == 40
        assert any(h["type"] == "evidence_verified" for h in hist["history"])  # original assessment and re-check both on record


def test_d_no_recheck_on_a_trip_that_already_has_a_claim():
    with client:
        pid = _cover("Jon Paid")
        _delay(200)
        cid = client.get(f"/api/traveler/{pid}").json()["customer"]["id"]
        r = client.post("/api/app/recheck", json={"policy": pid, "note": "please check"}, headers={"x-customer": cid})
        assert r.status_code == 400


# ------------------------------------------------------------------ E. second review

def test_e_one_second_review_with_a_reason_then_final():
    with client:
        pid, did, cid = _recheck()
        H = {"x-customer": cid}
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "Record shows 2h"})
        assert client.post("/api/app/recheck", json={"policy": pid, "note": "no"}, headers=H).status_code == 400  # reason too short
        r = client.post("/api/app/recheck", json={"policy": pid, "note": "Here is the airline's delay letter"}, headers=H)
        assert r.status_code == 200 and r.json()["status"] == "held"
        assert client.post("/api/app/recheck", json={"policy": pid, "note": "Here is the airline's delay letter"}, headers=H).status_code == 400  # duplicate
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "Letter checked, 2h"})
        r = client.post("/api/app/recheck", json={"policy": pid, "note": "Here is the airline's delay letter"}, headers=H)
        assert r.status_code == 400 and "already had a second review" in r.json()["detail"]


def test_e_concurrent_second_review_requests_create_one():
    with client:
        pid, did, cid = _recheck()
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "Record shows 2h"})
        codes = []
        go = lambda: codes.append(client.post("/api/app/recheck", json={"policy": pid, "note": "Here is the airline's letter"}, headers={"x-customer": cid}).status_code)
        ts = [threading.Thread(target=go) for _ in range(4)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(codes) == [200, 400, 400, 400]
        with db.conn() as c:
            assert engine.second_reviews_used(c, did) == 1


def test_e_admin_exception_beyond_the_limit_is_recorded():
    with client:
        pid, did, cid = _recheck()
        H = {"x-customer": cid}
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "Record shows 2h"})
        client.post("/api/app/recheck", json={"policy": pid, "note": "Here is the airline's delay letter"}, headers=H)
        client.post(f"/api/console/review/{did}", json={"action": "decline", "note": "Letter checked, 2h"})
        assert client.post(f"/api/console/review/{did}/reopen", json={"reason": "short"}).status_code == 422
        r = client.post(f"/api/console/review/{did}/reopen", json={"reason": "Airline confirmed a data error by phone"})
        assert r.status_code == 200 and r.json()["status"] == "held"
        hist = client.get(f"/api/console/decision/{pid}").json()["history"]
        assert any(h["type"] == "review_exception" and "data error" in h["what"] for h in hist)


def test_copilot_never_calls_a_disputed_delay_confirmed():
    with client:
        pid, did, _ = _recheck()
        rec = client.get(f"/api/console/copilot/{did}").json()
        assert rec["action"] == "ask_proof" and "confirmed" not in rec["why"][0].lower()
        assert "airline's record of the delay" in rec["draft"]
