"""Endpoints that serve the traveler app: the home summary, claims, re-check requests, QR vouchers, the outlet scan
and the cash-out preview. Everything here reads and writes through the same engine and wallet as the rest of the app."""
import io
import time

import segno
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import auth, db, engine, forecast, markets, wallet

router = APIRouter()
FX_FEE = 0.015  # the card network and our margin on a currency change, shown before the traveler confirms


def _cid(request: Request) -> str:
    s = auth.who(request.headers.get("x-session"))
    if s and s["kind"] == "traveler":
        return s["subject"]
    if auth.enabled():
        raise HTTPException(401, "Sign in to see your trips.")
    return request.headers.get("x-customer") or "CU-GEORGE"


@router.get("/api/app/mode")
def mode():
    """The app signs in by itself while demo mode is on. Real sign-in with one-time codes stays in the code."""
    return {"demo": auth.demo_codes(), "traveler": "CU-GEORGE", "staff": "ST-1"}


def _decision(c, pid):
    return db.one(c, "SELECT id,status,amount,points,delay_minutes,trigger,explanation,message,note,decided_by,created_at FROM decisions "
                     "WHERE policy_id=? AND kind='delay'", (pid,))


def _leg(c, row, rules_cache) -> dict:
    now = time.time()
    f = {k: row[k] for k in ("code", "airline", "origin", "origin_city", "dest", "dest_city", "depart_label", "scheduled", "sched_ts",
                              "delay_minutes", "cancelled", "market", "flight_date") if k in row.keys()}
    market = f.get("market") or "SG"
    rules = rules_cache.setdefault(market, engine.get_rules(c, market))
    departed = bool(f["sched_ts"] and f["sched_ts"] < now)
    pol = db.one(c, "SELECT id,status,delay_cover,baggage_cover,payout_account FROM policies WHERE flight=? AND pnr=? AND status IN ('active','archived')",
                 (f["code"], row["ref"]))
    leg = {"leg": row["leg"], "flight": f, "departed": departed, "policy": None, "cover": "none", "decision": None, "bag": None, "state": "", "can": []}
    if not departed and not f.get("cancelled"):
        fc = forecast.compute(f)
        leg["risk"] = {"probability": fc["probability"], "level": fc["level"], "band": forecast.band(fc["probability"], rules["close_cover_at"])}
    if not pol:
        leg["state"] = "no_cover" if not departed else "ended"
        if not departed:
            leg["can"] = ["buy"]
        return leg
    leg["policy"] = pol["id"]
    leg["cover"] = "both" if pol["delay_cover"] and pol["baggage_cover"] else "delay" if pol["delay_cover"] else "baggage"
    d = _decision(c, pol["id"])
    leg["decision"] = d
    b = db.one(c, "SELECT id,status,total FROM baggage WHERE policy_id=?", (pol["id"],))
    leg["bag"] = b
    if not d and not departed:
        leg["state"] = "covered"
        miss = [k for k, have in (("delay", pol["delay_cover"]), ("baggage", pol["baggage_cover"])) if not have]
        if miss:
            leg["can"] = ["add"]
        leg["missing"] = miss
    elif d:
        leg["state"] = {"paid": "paid", "held": "review", "declined": "declined"}.get(d["status"], "review")
        if d["status"] == "declined":
            leg["can"] = ["review"]
    elif pol["delay_cover"] and (f.get("delay_minutes") or 0) > 0:
        leg["state"] = "not_covered"
        leg["can"] = ["recheck"]
        leg["rule_minutes"] = int(rules["delay_tier1_minutes"])
    else:
        leg["state"] = "completed"
    if departed and pol["baggage_cover"] and not b:
        leg["can"].append("bag")
    return leg


@router.get("/api/app/home")
def home(request: Request):
    cid = _cid(request)
    with db.conn() as c:
        cu = wallet.get(c, cid)
        if not cu:
            raise HTTPException(404, "Account not found.")
        rows = db.rows(c, "SELECT b.ref, b.leg, b.trip, b.channel, b.passenger, f.* FROM bookings b JOIN flights f ON f.code=b.flight "
                          "WHERE b.customer_id=? ORDER BY f.sched_ts", (cid,))
        cache, trips = {}, {}
        for r in rows:
            t = trips.setdefault(r["ref"], {"ref": r["ref"], "trip": r["trip"], "channel": r["channel"], "passenger": r["passenger"], "legs": []})
            t["legs"].append(_leg(c, r, cache))
        out = sorted(trips.values(), key=lambda t: t["legs"][0]["flight"]["sched_ts"] or 0)
        for t in out:
            t["upcoming"] = any(not g["departed"] for g in t["legs"])
        claims = [{**g, "ref": t["ref"]} for t in out for g in t["legs"]
                  if g["state"] in ("paid", "review", "declined", "not_covered") or g["bag"]]
        claims.sort(key=lambda g: -(g["flight"]["sched_ts"] or 0))
        bal = wallet.balance(c, cid)
        return {"customer": {"id": cu["id"], "name": cu["name"], "email": cu["email"], "phone": cu["phone"], "market": cu["market"]},
                "balance": bal, "trips": out, "claims": claims, "currency": markets.DEMO_CURRENCY,
                "rule": {k: engine.get_rules(c, cu["market"] or "SG")[k] for k in ("delay_tier1_minutes", "delay_tier1_payout", "delay_tier2_minutes",
                                                                                   "delay_tier2_payout", "delay_premium", "baggage_premium")}}


class Recheck(BaseModel):
    policy: str
    note: str = Field("", max_length=300)


@router.post("/api/app/recheck")
def recheck(body: Recheck, request: Request):
    cid = _cid(request)
    with db.conn() as c:
        p = db.one(c, "SELECT customer_id FROM policies WHERE id=?", (body.policy,))
        if not p or p["customer_id"] != cid:
            raise HTTPException(404, "Trip not found.")
        try:
            d = engine.request_recheck(c, body.policy, body.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"id": d["id"], "status": d["status"]}


# ----------------------------------------------------------------------------- QR vouchers

def _png_or_svg(code: str) -> bytes:
    buf = io.BytesIO()
    segno.make("KODA:" + code, error="m").save(buf, kind="svg", scale=6, border=2, dark="#071C3F", xmldecl=False, svgns=True)
    return buf.getvalue()


@router.get("/api/app/qr/{code}.svg")
def qr(code: str):
    return Response(_png_or_svg(code), media_type="image/svg+xml", headers={"Cache-Control": "public, max-age=3600"})


class Scan(BaseModel):
    code: str = Field(..., min_length=4, max_length=40)
    partner: str | None = None  # the outlet's own id, sent by the till


@router.post("/api/outlet/scan")
def outlet_scan(body: Scan):
    """What the outlet's till calls when it scans a traveler's QR. A voucher works once, at its own outlet."""
    code = body.code.replace("KODA:", "").strip().upper()
    with db.conn() as c:
        r = db.one(c, "SELECT r.*, t.name partner_name, t.category FROM redemptions r JOIN partners t ON t.id=r.partner_id WHERE r.code=?", (code,))
        if not r:
            raise HTTPException(404, "This code is not a KODA voucher.")
        if body.partner and body.partner != r["partner_id"]:
            raise HTTPException(400, f"This voucher is for {r['partner_name']}, not this outlet.")
        if db.one(c, "SELECT 1 FROM wallet_tx WHERE id=?", ("refund:" + r["id"],)):
            raise HTTPException(400, "This voucher was refunded.")
        if r.get("status") == "used":
            raise HTTPException(400, "This voucher was already used.")
        c.execute("UPDATE redemptions SET status='used', used_at=? WHERE id=?", (time.time(), r["id"]))
        db.log(c, None, "voucher_used", {"voucher": r["id"], "partner": r["partner_name"], "points": r["points"]})
        return {"ok": True, "partner": r["partner_name"], "points": r["points"], "value": round(r["points"] / markets.POINTS_PER_USD, 2), "currency": markets.DEMO_CURRENCY}


# ----------------------------------------------------------------------------- cash out with currency change

def _fx(c, cid: str, points: int, method_id: str) -> dict:
    m = db.one(c, "SELECT * FROM payment_methods WHERE id=? AND customer_id=?", (method_id, cid))
    if not m:
        raise ValueError("Choose a saved card or bank account.")
    gross = round(points / markets.POINTS_PER_USD * wallet.CASHOUT_RATE, 2)  # in S$, the currency the points are held in
    cur = m.get("currency") or markets.DEMO_CURRENCY
    if cur == markets.DEMO_CURRENCY:
        return {"method": m["label"], "currency": cur, "gross": gross, "rate": 1.0, "fee": 0.0, "receive": gross, "points": points, "same": True}
    rate = markets.FX_PER_USD.get(cur, 1.0)
    fee = round(gross * FX_FEE, 2)
    return {"method": m["label"], "currency": cur, "gross": gross, "rate": rate, "fee": fee, "receive": round((gross - fee) * rate, 2), "points": points, "same": False}


@router.get("/api/app/cashout-preview")
def cashout_preview(request: Request, points: int = Query(..., ge=1), method: str = Query(...)):
    cid = _cid(request)
    with db.conn() as c:
        try:
            out = _fx(c, cid, points, method)
        except ValueError as e:
            raise HTTPException(400, str(e))
        out["min"] = wallet.MIN_CASHOUT
        out["fee_rate"] = FX_FEE
        return out


class Cash(BaseModel):
    points: int = Field(..., ge=1, le=10_000_000)
    method: str


@router.post("/api/app/cashout")
def cashout(body: Cash, request: Request):
    cid = _cid(request)
    with db.conn() as c:
        try:
            fx = _fx(c, cid, body.points, body.method)
            r = wallet.cash_out(c, cid, body.points, body.method)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.log(c, None, "points_cashed_out", {"customer": cid, "points": r["points"], "currency": fx["currency"], "receive": fx["receive"], "to": r["to"]})
        return {**fx, "balance": r["balance"], "id": r["id"]}


# ----------------------------------------------------------------------------- partner checkout

class PartnerBooking(BaseModel):
    flight: str
    date: str = Field(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
    delay: bool = True
    baggage: bool = True
    ack: bool = False


@router.post("/api/app/partner-booking")
def partner_booking(body: PartnerBooking, request: Request):
    """A traveler with no ticket books through the partner checkout. The airline ticket is simulated in this prototype,
    so the booking reference is made here. Cover is only ever sold against a booking."""
    from . import main
    cid = _cid(request)
    if not (body.delay or body.baggage):
        raise HTTPException(400, "Choose at least one cover.")
    with db.conn() as c:
        cu = wallet.get(c, cid)
        name = cu["name"]
    q = main.create_policy(main.NewPolicy(name=name, flight=body.flight, date=body.date, customer=cid, channel="KODA partner checkout"), request)
    pid = q["policy"]
    main.checkout(pid, main.Checkout(delay=body.delay, baggage=body.baggage, ack=body.ack))
    with db.conn() as c:
        p = db.one(c, "SELECT pnr FROM policies WHERE id=?", (pid,))
        c.execute("INSERT OR REPLACE INTO bookings (ref,leg,passenger,flight,trip,channel,customer_id) VALUES (?,?,?,?,?,?,?)",
                  (p["pnr"], 1, cu["name"], q["flight"]["code"], "One way", "KODA partner checkout", cid))
    return {"ref": p["pnr"], "policy": pid, "flight": q["flight"]["code"]}


class AddCoverBody(BaseModel):
    ref: str
    flight: str
    delay: bool = False
    baggage: bool = False
    ack: bool = False


@router.post("/api/app/add-cover")
def add_cover(body: AddCoverBody, request: Request):
    """Add cover to a booking the traveler already holds, whether or not it has a policy yet."""
    from . import main
    cid = _cid(request)
    if not body.ack:
        raise HTTPException(400, "Please confirm you have read the key facts and the privacy notice.")
    with db.conn() as c:
        b = db.one(c, "SELECT * FROM bookings WHERE ref=? AND flight=? AND customer_id=?", (body.ref, body.flight, cid))
        if not b:
            raise HTTPException(404, "Booking not found.")
        pol = db.one(c, "SELECT id,status FROM policies WHERE pnr=? AND flight=? AND status IN ('active','archived')", (body.ref, body.flight))
        passenger, channel = b["passenger"], b["channel"]
    if pol:
        r = main.add_cover(pol["id"], main.AddCover(delay=body.delay, baggage=body.baggage))
        return {"policy": pol["id"], "total": r["added"]}
    q = main.create_policy(main.NewPolicy(name=passenger, flight=body.flight, booking_ref=body.ref, customer=cid, channel=channel), request)
    r = main.checkout(q["policy"], main.Checkout(delay=body.delay, baggage=body.baggage, ack=True))
    return {"policy": q["policy"], "total": r["total"]}
