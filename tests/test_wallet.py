"""Points wallet, saved cards and banks, cash-out, auto top-up, booking lookup and the console customer views."""
import os
import tempfile

os.environ["KODA_SAMPLE_DATA"] = "1"
os.environ["CALMY_DB"] = os.path.join(tempfile.mkdtemp(), "w.db")
os.environ.pop("AVIATIONSTACK_KEY", None)
os.environ["KODA_NO_WEATHER"] = "1"

from fastapi.testclient import TestClient  # noqa: E402

from app import db, demo_data  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)
FL = "OA 108"


def setup_function():
    with client:
        client.post("/api/demo/reset")


def _trip(name="Lena Fischer", cid=None, ref="XK7Q2P", flight=FL, delay=True, bag=True):
    r = client.post("/api/policies", json={"name": name, "flight": flight, "booking_ref": ref, "customer": cid})
    assert r.status_code == 200, r.text
    pid = r.json()["policy"]
    assert client.post(f"/api/traveler/{pid}/checkout", json={"delay": delay, "baggage": bag, "ack": True}).status_code == 200
    return pid, r.json()["customer"]


def _late(m, flight=FL):
    return client.post("/api/demo/delay", json={"flight": flight, "minutes": m}).json()


def test_buying_needs_no_card_digits_and_payout_is_points():
    pid, cid = _trip()
    s = client.get(f"/api/traveler/{pid}").json()
    assert s["policy"]["payout_account"].startswith("KODA points")
    assert s["customer"]["id"] == cid and s["customer"]["balance"]["points"] == 0


def test_delay_pays_points_with_usd_equivalence():
    pid, cid = _trip()
    _late(200)
    b = client.get(f"/api/customer/{cid}").json()["balance"]
    assert b["points"] == 5000 and b["spend_usd"] == 50 and b["cash_usd"] == 40


def test_spend_points_at_airport_reduces_balance_and_gives_voucher():
    pid, cid = _trip()
    _late(200)
    sv = client.get(f"/api/customer/{cid}/services?airport=BKK").json()
    assert {c["key"] for c in sv["categories"]} >= {"food", "lounge", "transport"}
    fixed = next(p for p in sv["partners"] if p["price_usd"])
    r = client.post(f"/api/customer/{cid}/redeem", json={"partner": fixed["id"], "points": fixed["price_usd"] * 100})
    assert r.status_code == 200 and r.json()["points"] == fixed["price_usd"] * 100
    assert r.json()["balance"] == 5000 - fixed["price_usd"] * 100


def test_cannot_spend_more_than_balance():
    _, cid = _trip()
    sv = client.get(f"/api/customer/{cid}/services?airport=BKK").json()
    p = sv["partners"][0]
    assert client.post(f"/api/customer/{cid}/redeem", json={"partner": p["id"], "points": 100000}).status_code == 400


def test_card_validation_cashout_and_rate():
    pid, cid = _trip()
    _late(200)
    assert client.post(f"/api/customer/{cid}/methods", json={"kind": "card", "number": "4242 4242 4242 4241", "holder": "Lena F", "expiry": "12/30"}).status_code == 400
    ok = client.post(f"/api/customer/{cid}/methods", json={"kind": "card", "number": "4242 4242 4242 4242", "holder": "Lena F", "expiry": "12/30"})
    assert ok.status_code == 200
    mid = ok.json()["method"]["id"]
    assert client.post(f"/api/customer/{cid}/cashout", json={"points": 100, "method": mid}).status_code == 400  # below minimum
    r = client.post(f"/api/customer/{cid}/cashout", json={"points": 2500, "method": mid})
    assert r.status_code == 200 and r.json()["usd"] == 20 and r.json()["balance"] == 2500
    assert client.post(f"/api/customer/{cid}/cashout", json={"points": 9999, "method": mid}).status_code == 400
    led = client.get(f"/api/customer/{cid}").json()["ledger"]
    assert any(t["kind"] == "cashout" and t["points"] == -2500 for t in led)


def test_first_card_turns_on_auto_topup_and_next_payout_is_cash():
    pid, cid = _trip()
    client.post(f"/api/customer/{cid}/methods", json={"kind": "card", "number": "4242424242424242", "holder": "Lena F", "expiry": "12/30"})
    s = client.get(f"/api/customer/{cid}").json()
    assert s["customer"]["auto_topup"] == 1 and "ending 4242" in s["payout_to"]
    _late(200)
    s = client.get(f"/api/customer/{cid}").json()
    assert s["balance"]["points"] == 0
    assert any("4242" in (p["payout"] or "") and p["points"] == 0 for p in s["payouts"])


def test_switching_back_to_points_and_history_is_not_rewritten():
    pid, cid = _trip(flight=FL)
    client.post(f"/api/customer/{cid}/methods", json={"kind": "card", "number": "4242424242424242", "holder": "Lena F", "expiry": "12/30"})
    _late(200)
    r = client.put(f"/api/customer/{cid}", json={"auto_topup": False})
    assert r.json()["payout_to"].startswith("KODA points")
    p = client.get(f"/api/customer/{cid}").json()["payouts"][0]
    assert "4242" in p["payout"] and p["points"] == 0  # the earlier payout still shows the card


def test_removing_last_method_turns_auto_topup_off():
    _, cid = _trip()
    m = client.post(f"/api/customer/{cid}/methods", json={"kind": "bank", "number": "1234567890", "holder": "Lena F", "bank": "DBS Bank"}).json()["method"]
    assert client.delete(f"/api/customer/{cid}/methods/{m['id']}").json()["customer"]["auto_topup"] == 0


def test_auto_topup_needs_a_saved_method_and_profile_validates():
    _, cid = _trip()
    assert client.put(f"/api/customer/{cid}", json={"auto_topup": True}).status_code == 400
    assert client.put(f"/api/customer/{cid}", json={"email": "nope"}).status_code == 400
    r = client.put(f"/api/customer/{cid}", json={"name": "Lena Fischer-Ng", "email": "lena@example.com", "phone": "+66 81 234 5678"})
    assert r.status_code == 200 and r.json()["customer"]["name"] == "Lena Fischer-Ng"


def test_demo_refs_show_four_cover_states_and_wrong_name_fails():
    with db.conn() as c:
        demo_data.seed_all(c)
    kinds = {}
    for ref, sur in (("BOTH42", "Wongsakul"), ("DLY731", "Tan"), ("BAG518", "Nakamura"), ("NONE26", "Okafor")):
        r = client.get(f"/api/booking/{ref}", params={"surname": sur})
        assert r.status_code == 200, ref
        kinds[ref] = [l["cover"] for l in r.json()["legs"]]
    assert kinds == {"BOTH42": ["both", "both"], "DLY731": ["delay"], "BAG518": ["baggage"], "NONE26": ["none"]}
    assert client.get("/api/booking/BOTH42", params={"surname": "Smith"}).status_code == 404


def test_delay_on_baggage_only_booking_pays_nothing():
    with db.conn() as c:
        demo_data.seed_all(c)
    leg = client.get("/api/booking/BAG518", params={"surname": "Nakamura"}).json()["legs"][0]
    res = client.post("/api/demo/delay", json={"flight": leg["flight"]["code"], "minutes": 240}).json()
    assert res["results"] == [] or all(r["policy"] != leg["policy"] for r in res["results"])
    assert client.get("/api/booking/BAG518", params={"surname": "Nakamura"}).json()["legs"][0]["delay_decision"] is None


def test_add_missing_cover_to_a_demo_trip_and_duplicate_is_refused():
    with db.conn() as c:
        demo_data.seed_all(c)
    leg = client.get("/api/booking/DLY731", params={"surname": "Tan"}).json()["legs"][0]
    r = client.post(f"/api/traveler/{leg['policy']}/add-cover", json={"baggage": True})
    assert r.status_code == 200
    assert client.post(f"/api/traveler/{leg['policy']}/add-cover", json={"baggage": True}).status_code == 400
    assert client.get("/api/booking/DLY731", params={"surname": "Tan"}).json()["legs"][0]["cover"] == "both"
    dup = client.post("/api/policies", json={"name": "Marcus Tan", "flight": leg["flight"]["code"], "booking_ref": "DLY731"})
    assert dup.status_code == 400


def test_console_customers_points_staff_and_reviewer_signature():
    with db.conn() as c:
        demo_data.seed_all(c)
    cs = client.get("/api/console/customers").json()
    assert len(cs) == 50
    assert {x["cover"] for x in cs} >= {"both", "delay", "baggage", "none"}
    assert all(x["cover"] == "both" for x in client.get("/api/console/customers", params={"cover": "both"}).json())
    d = client.get("/api/console/customers/CU-DEMO1").json()
    assert d["customer"]["name"] == "Pim Wongsakul" and d["policies"]
    pt = client.get("/api/console/points").json()
    assert pt["liability_points"] >= 0 and pt["partners"]
    st = client.get("/api/console/staff").json()
    assert len(st) >= 4
    sid = st[1]["id"]
    r = client.put(f"/api/console/staff/{sid}", json={"name": "Arun Siri", "notify": False})
    assert r.json()["name"] == "Arun Siri" and r.json()["initials"] == "AS"
    assert client.put(f"/api/console/staff/{sid}", json={"email": "bad"}).status_code == 400
    held = client.get("/api/console/review").json()
    if held:
        client.post(f"/api/console/review/{held[0]['id']}", json={"action": "approve"}, headers={"X-Staff": sid})
        assert client.get(f"/api/console/staff").json()[1]["decisions"] >= 1
    p0 = pt["partners"][0]
    assert client.put(f"/api/console/partners/{p0['id']}", json={"accepts": not p0["accepts"]}).json()["accepts"] == int(not p0["accepts"])


def test_unused_voucher_can_be_refunded_once():
    _, cid = _trip()
    _late(200)
    sv = client.get(f"/api/customer/{cid}/services?airport=BKK").json()
    p = next(x for x in sv["partners"] if x["price_usd"])
    v = client.post(f"/api/customer/{cid}/redeem", json={"partner": p["id"], "points": p["price_usd"] * 100}).json()
    r = client.post(f"/api/customer/{cid}/vouchers/{v['id']}/refund")
    assert r.status_code == 200 and r.json()["balance"] == 5000
    assert client.post(f"/api/customer/{cid}/vouchers/{v['id']}/refund").status_code == 400
    assert client.post(f"/api/customer/CU-NOPE/vouchers/{v['id']}/refund").status_code == 404


def test_delay_cover_closes_when_a_delay_is_already_very_likely_but_baggage_stays_open():
    client.put("/api/console/rules?market=TH", json={"values": {"close_cover_at": 60}})
    client.post("/api/webhook/flight-event", json={"flight": FL, "event": "storm"})
    r = client.post("/api/policies", json={"name": "Lena Fischer", "flight": FL, "booking_ref": "ZZ9Q2P"})
    pid = r.json()["policy"]
    assert client.get(f"/api/traveler/{pid}").json()["underwriting"]["band"] == "closed"
    refused = client.post(f"/api/traveler/{pid}/checkout", json={"delay": True, "baggage": True, "ack": True})
    assert refused.status_code == 400 and "closed" in refused.json()["detail"]
    assert client.post(f"/api/traveler/{pid}/checkout", json={"delay": False, "baggage": True, "ack": True}).status_code == 200
    assert client.put("/api/console/rules?market=TH", json={"values": {"close_cover_at": 20}}).status_code == 400
