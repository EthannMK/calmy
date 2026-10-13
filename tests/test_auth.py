"""Sign-in: one-time codes, sessions, who can see what, and what each staff role may do."""
import os
import tempfile

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "a.db")
os.environ["KODA_NO_WEATHER"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

client = TestClient(app)


@pytest.fixture(autouse=True)
def _on(monkeypatch):
    monkeypatch.setenv("KODA_AUTH", "on")
    from app import auth
    auth._starts.clear()
    with client:
        client.post("/api/demo/reset")
        from app import db, demo_data
        with db.conn() as c:  # the test database starts without the demo book, so add it
            demo_data.seed_all(c)
        yield


def _traveler(ref="BOTH42", surname="Wongsakul"):
    s = client.post("/api/auth/traveler/start", json={"ref": ref, "surname": surname}).json()
    v = client.post("/api/auth/verify", json={"challenge": s["challenge"], "code": s["demo_code"]}).json()
    return v["token"], v["me"]


def _staff(email):
    s = client.post("/api/auth/staff/start", json={"email": email}).json()
    v = client.post("/api/auth/verify", json={"challenge": s["challenge"], "code": s["demo_code"]}).json()
    return v["token"], v["me"]


def test_customer_pages_need_a_session():
    tok, me = _traveler()
    assert client.get(f"/api/customer/{me['id']}").status_code == 401
    assert client.get(f"/api/customer/{me['id']}", headers={"X-Session": tok}).status_code == 200


def test_one_traveler_cannot_open_another_account():
    tok, me = _traveler()
    other = client.get("/api/demo/bookings").json()[1]["customer"]
    assert other != me["id"]
    assert client.get(f"/api/customer/{other}", headers={"X-Session": tok}).status_code == 403


def test_wrong_code_is_refused_and_locks_after_five_tries():
    s = client.post("/api/auth/traveler/start", json={"ref": "BOTH42", "surname": "Wongsakul"}).json()
    for _ in range(5):
        r = client.post("/api/auth/verify", json={"challenge": s["challenge"], "code": "000000" if s["demo_code"] != "000000" else "111111"})
        assert r.status_code in (400, 429)
    assert client.post("/api/auth/verify", json={"challenge": s["challenge"], "code": s["demo_code"]}).status_code == 429


def test_unknown_booking_and_wrong_name_get_the_same_answer():
    a = client.post("/api/auth/traveler/start", json={"ref": "ZZZZZZ", "surname": "Nobody"})
    b = client.post("/api/auth/traveler/start", json={"ref": "BOTH42", "surname": "Nobody"})
    assert a.status_code == b.status_code == 404 and a.json() == b.json()


def test_demo_code_is_hidden_when_demo_codes_are_off(monkeypatch):
    monkeypatch.setenv("KODA_DEMO_CODES", "off")
    s = client.post("/api/auth/traveler/start", json={"ref": "BOTH42", "surname": "Wongsakul"}).json()
    assert "demo_code" not in s and "*" in s["sent_to"]
    assert client.post("/api/auth/demo-session", json={"kind": "traveler", "id": "x"}).status_code == 404


def test_session_ends_after_idle_and_on_sign_out(monkeypatch):
    tok, me = _traveler()
    assert client.get("/api/auth/me", headers={"X-Session": tok}).status_code == 200
    client.post("/api/auth/logout", headers={"X-Session": tok})
    assert client.get("/api/auth/me", headers={"X-Session": tok}).status_code == 401
    tok, _ = _traveler()
    from app import auth, db
    with db.conn() as c:
        c.execute("UPDATE sessions SET last_seen=last_seen-?", (auth.IDLE["traveler"] + 5,))
    assert client.get("/api/auth/me", headers={"X-Session": tok}).status_code == 401


def test_console_needs_staff_and_roles_limit_what_they_can_do():
    assert client.get("/api/console/overview").status_code == 401
    tok_t, _ = _traveler()
    assert client.get("/api/console/overview", headers={"X-Session": tok_t}).status_code == 401
    agent, _ = _staff("malinee.chaiyasit@koda.example")
    rev, _ = _staff("arun.suksawat@koda.example")
    adm, me = _staff("pimchanok.tanakorn@koda.example")
    assert me["role"] == "admin"
    H = lambda t: {"X-Session": t}  # noqa: E731
    assert client.get("/api/console/overview", headers=H(agent)).status_code == 200
    body = {"airport": "BKK", "label": "Test closure", "level": "moderate"}
    assert client.post("/api/console/airspace", json=body, headers=H(rev)).status_code == 403
    assert client.post("/api/console/airspace", json=body, headers=H(adm)).status_code == 200
    assert client.post("/api/console/review/NOPE", json={"action": "approve"}, headers=H(agent)).status_code == 403
    assert client.post("/api/console/review/NOPE", json={"action": "approve"}, headers=H(rev)).status_code != 403


def test_decision_is_signed_by_the_session_not_the_header():
    rev, me = _staff("arun.suksawat@koda.example")
    r = client.get("/api/console/staff/me", headers={"X-Session": rev, "X-Staff": "ST-4"})
    assert r.json()["id"] == me["id"]


def test_demo_accounts_are_listed_with_proper_names():
    d = client.get("/api/auth/demo-accounts").json()
    assert [t["ref"] for t in d["travelers"]] == ["BOTH42", "DLY731", "BAG518", "NONE26"]
    assert {s["role"] for s in d["staff"]} == {"admin", "reviewer", "agent"}
    assert all(s["name"] and "." not in s["name"] for s in d["staff"])


def test_new_buyer_is_signed_in_but_an_existing_customer_is_not():
    r = client.post("/api/policies", json={"name": "Lena Fischer", "flight": "OA 108", "booking_ref": "QQ12AB"}).json()
    assert r["session"] and client.get("/api/auth/me", headers={"X-Session": r["session"]}).status_code == 200
    body = {"name": "Lena Fischer", "flight": "OA 108", "booking_ref": "QQ12AC", "customer": r["customer"]}
    # Without the session, a customer id sent by the page is ignored and a separate account is made.
    other = client.post("/api/policies", json=body).json()
    assert other["customer"] != r["customer"]
    # With the owner's session, the trip joins the existing account and no new session is issued.
    mine = client.post("/api/policies", json=body, headers={"X-Session": r["session"]}).json()
    assert mine["customer"] == r["customer"] and mine["session"] is None


def test_trip_link_does_not_show_points():
    r = client.post("/api/policies", json={"name": "Lena Fischer", "flight": "OA 108", "booking_ref": "QQ12AB"}).json()
    # A trip that belongs to an account cannot be opened, spent from or claimed on without that account's session.
    assert client.get(f"/api/traveler/{r['policy']}").status_code == 401
    assert client.post(f"/api/traveler/{r['policy']}/redeem", json={"partner": "x", "points": 100}).status_code == 401
    other, _ = _traveler()
    assert client.get(f"/api/traveler/{r['policy']}", headers={"X-Session": other}).status_code == 404
    s = client.get(f"/api/traveler/{r['policy']}", headers={"X-Session": r["session"]}).json()
    assert "balance" not in s["customer"]


def test_only_an_admin_can_reopen_a_claim_beyond_the_review_limit():
    rev, _ = _staff("arun.suksawat@koda.example")
    adm, _ = _staff("ethan@koda.example")
    held = client.get("/api/console/review", headers={"X-Session": adm}).json()[0]["id"]
    assert client.post(f"/api/console/review/{held}", json={"action": "decline", "note": "Record does not support it"}, headers={"X-Session": rev}).status_code == 200
    reason = {"reason": "Airline confirmed a data error by phone"}
    assert client.post(f"/api/console/review/{held}/reopen", json=reason, headers={"X-Session": rev}).status_code == 403
    assert client.post(f"/api/console/review/{held}/reopen", json=reason, headers={"X-Session": adm}).status_code == 200
    assert client.post("/api/console/payments/PY-NONE/retry", headers={"X-Session": _staff("malinee.chaiyasit@koda.example")[0]}).status_code == 403
