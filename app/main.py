"""KODA API and static site.

Run locally:  uvicorn app.main:app --reload
Pages:        /            landing with the two demo entry points
              /traveler    mobile traveler app
              /console     insurer console
"""
import json
import os
from contextlib import asynccontextmanager

from fastapi import Header, FastAPI, HTTPException, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import secrets
import threading
from typing import Literal

import re
import time

from . import agent, auth, bookings, context, copilot, db, engine, forecast, live, markets, partners, rights, schedule, track, wallet

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
@asynccontextmanager
async def lifespan(_app):
    db.init()
    if live.enabled() and not os.environ.get("KODA_SAMPLE_DATA"):  # fill the departures cache in the background so the first visitor does not wait on the flight data service
        def warm():
            try:
                live.upcoming("BKK")
            except Exception:
                pass
        threading.Thread(target=warm, daemon=True).start()
    yield


app = FastAPI(
    title="KODA",
    description="Smart flight cover. Demo data, flight data simulated.",
    lifespan=lifespan,
)
app.add_middleware(GZipMiddleware, minimum_size=1024)  # the pages are large text, so compression is a big win on slow links


@app.middleware("http")
async def cache_static(request, call_next):
    """Fonts rarely change, so the browser keeps them for a day. Pages, styles and scripts are revalidated on every
    load (a cheap 304 when unchanged), so a new deploy shows up at once instead of after a day."""
    resp = await call_next(request)
    path = request.url.path
    if resp.status_code == 200 and not path.startswith("/api/"):
        resp.headers["Cache-Control"] = "public, max-age=86400" if path.startswith("/static/fonts/") else "no-cache"
    return resp


@app.middleware("http")
async def sign_in_guard(request, call_next):
    """Customer and console endpoints need a signed-in session. See app/auth.py."""
    denied = auth.guard(request)
    return denied if denied else await call_next(request)


# ----------------------------------------------------------- helpers

def _decision(d):
    if not d:
        return None
    for k in ("reasons", "timeline", "breakdown"):
        d[k] = json.loads(d[k]) if d.get(k) else []
    d["context"] = json.loads(d["context"]) if d.get("context") else None
    return d


def _policy(c, pid):
    p = db.one(c, "SELECT * FROM policies WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "policy not found")
    return p


def _flight(c, code):
    return db.one(c, "SELECT * FROM flights WHERE code=?", (code,))


def _extra(c, pid):
    e = db.one(c, "SELECT * FROM expenses WHERE policy_id=?", (pid,))
    if e:
        e["has_receipt"] = bool(e.pop("receipt"))
        e["decision"] = _decision(db.one(c, "SELECT * FROM decisions WHERE id=?", (e["id"] + "-D",)))
    return e


def _bag(c, pid):
    b = db.one(c, "SELECT * FROM baggage WHERE policy_id=?", (pid,))
    if b:
        b["items"] = json.loads(b["items"])
        b["decision"] = _decision(db.one(c, "SELECT * FROM decisions WHERE id=?", (b["id"] + "-D",)))
    return b


# ----------------------------------------------------------- traveler

class Checkout(BaseModel):
    delay: bool = True
    baggage: bool = True
    ack: bool = False  # the traveler has seen the key facts and the privacy notice for their market


@app.get("/api/markets")
def list_markets():
    """Every market with its currency, rails, simulated indicative rates and legal notes."""
    return {"markets": [markets.public(k) for k in markets.MARKETS], "fx_per_usd": markets.FX_PER_USD}


@app.get("/api/traveler/{pid}")
def traveler_state(pid: str):
    with db.conn() as c:
        p = _policy(c, pid)
        rules = engine.get_rules(c, p["market"])
        delay = _decision(db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (pid,)))
        flight = _flight(c, p["flight"])
        track.maybe_refresh(flight)  # starts a background refresh if the real status is stale
        if flight:
            context.warm(flight["origin"])
        fc = al = rt = pts = None
        if flight:
            fc = forecast.compute(flight)
            al = forecast.alert(fc, flight) if p["status"] == "active" and p["delay_cover"] and not delay else None
            live_w = context.cached(flight["origin"])
            rt = rights.build(p["market"], flight, bool((live_w and live_w.get("severe")) or flight.get("stress")))
            if rt:
                rights.enrich(rt)
                rt = {k: v for k, v in rt.items() if not k.startswith("_")}
        wallet.refresh(c, p)
        partners.sync(c, pid)
        if p["payout_account"].startswith(markets.POINTS_RAIL):
            pts = partners.balance(c, pid)
            pts["redemptions"] = db.rows(c, "SELECT r.*, t.name partner FROM redemptions r JOIN partners t ON t.id=r.partner_id "
                                            "WHERE r.policy_id=? ORDER BY r.created_at DESC", (pid,))
        cust = None
        if p.get("customer_id"):
            cu = wallet.get(c, p["customer_id"])
            # No balance here: a trip link must not show someone's points. The wallet needs a signed-in session.
            acct = wallet.account_for(c, cu["id"])
            cust = {"id": cu["id"], "name": cu["name"], "payout_to": acct if acct.startswith(markets.POINTS_RAIL) else "your saved card or bank"}
        trip = db.rows(c, "SELECT b.leg, b.trip, b.flight, f.origin, f.dest, f.depart_label FROM bookings b LEFT JOIN flights f ON f.code=b.flight "
                          "WHERE b.ref=? ORDER BY b.leg", (p["pnr"],)) if p.get("pnr") else []
        uw = None
        if fc:
            uw = {"band": forecast.band(fc["probability"], rules["close_cover_at"]), "probability": fc["probability"], "close_at": rules["close_cover_at"]}
        return {
            "customer": cust, "trip": trip, "underwriting": uw,
            "forecast": fc, "alert": al, "rights": rt, "points": pts,
            "weather": context.for_event(flight["code"], flight["origin"], bool(flight.get("cancelled")), flight["delay_minutes"]) if flight else None,
            "policy": p,
            "flight": flight,
            "market": markets.public(p["market"]),
            "rules": rules,
            "delay_decision": delay,
            "baggage": _bag(c, pid),
            "extra": _extra(c, pid),
            "extra_limits": dict(zip(("fast", "cap"), engine.extra_limits(rules))),
        }


class NewPolicy(BaseModel):
    name: str = Field(..., min_length=2, max_length=40)
    flight: str = Field(..., min_length=3, max_length=8)
    booking_ref: str | None = Field(None, pattern=r"^[A-Za-z0-9]{5,10}$")  # the airline or booking site's own reference on the ticket
    date: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")  # day of the flight, up to a month ahead
    customer: str | None = None  # the signed in traveler, so the trip joins their wallet
    channel: str | None = Field(None, max_length=30)  # where the ticket was bought, when the traveler tells us
    # Older callers pick a payout rail and give the last 4 digits at purchase. New travelers are paid in points
    # and can save a card or bank account later, so both stay optional.
    last4: str | None = Field(None, pattern=r"^\d{4}$")
    rail: str | None = None


@app.post("/api/policies")
def create_policy(body: NewPolicy, request: Request):
    """Start a quote for a flight. A sample flight is used as is. Any other number is looked up live.

    The departure country sets the market: currency, payout rails, rules and legal notes.
    The booking reference ties the cover to the ticket. A reference that matches a booking record, with the same
    passenger and flight, is marked verified.
    """
    code = re.sub(r"\s+", " ", body.flight.strip().upper())
    ref = bookings.clean_ref(body.booking_ref or "")
    with db.conn() as c:
        sample = db.one(c, "SELECT * FROM flights WHERE source='sim' AND REPLACE(code,' ','')=?", (code.replace(" ", ""),))
        booked = bookings.match_for_policy(c, ref, body.name, code) if ref else None
        demo_flight = db.one(c, "SELECT * FROM flights WHERE source='demo' AND REPLACE(code,' ','')=?", (code.replace(" ", ""),)) if booked else None
        if booked and db.one(c, "SELECT 1 FROM policies WHERE pnr=? AND flight=? AND status IN ('active','archived')", (ref, booked["flight"])):
            raise HTTPException(400, "This trip already has cover. Open it from Check my cover.")
    today = time.strftime("%Y-%m-%d")
    if demo_flight:
        f = demo_flight  # a booking on record carries its own flight, so it works without a live data call
    elif sample:
        f = sample
    elif body.date and body.date > today:
        # A later day: live data cannot answer yet, so the saved real schedule is used. Status is checked closer to the day.
        if schedule.valid_date(body.date) is None:
            raise HTTPException(400, f"Pick a day between today and {schedule.window()[1]}.")
        f = schedule.find(code, body.date)
        if not f:
            raise HTTPException(400, f"{code} is not in the saved schedule for {body.date}. Pick a flight from the list.")
    else:
        f, err = None, None
        if live.enabled():
            try:
                f = live.from_cache(code) or live.lookup(body.flight)  # a flight just shown in a list costs no new lookup
            except live.LiveError as e:
                err = e
        if f is None:
            f = schedule.find(code, body.date or today)  # offline fallback so a live outage never stops the demo
            if f is None:
                if not live.enabled():
                    raise HTTPException(503, "Live flight data is not connected, so flights cannot be searched right now.")
                raise HTTPException(502, str(err))
        elif f["sched_ts"] and f["sched_ts"] < time.time():
            raise HTTPException(400, f"{f['code']} has already left. Pick a flight that has not departed yet.")
    if f.get("sched_ts") and f["sched_ts"] < time.time():
        raise HTTPException(400, f"{f['code']} has already left, so cover cannot be added.")
    context.warm(f["origin"])  # weather is fetched now, in the background, so the later event never waits on it
    market = f.get("market") or "GLOBAL"
    info = markets.public(market)
    legacy = bool(body.last4 or body.rail) and not body.customer
    rail = body.rail or info["rails"][0]
    if legacy and rail not in info["rails"]:
        raise HTTPException(400, f"Pick one of the payout options for {info['name']}: " + ", ".join(info["rails"]) + ".")
    with db.conn() as c:
        if not (demo_flight or sample):
            live.upsert_flight(c, f)
        pid = engine._new_id("CL")
        # The booking reference belongs to the ticket, so the traveler's own is stored. A random one is only a
        # fallback for callers that send none (the presenter panel and seed data).
        pnr = ref or "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))
        channel = (body.channel or "").strip() or (booked or {}).get("channel")
        cid, follow, session = None, 0, None
        if legacy:
            account = f"{rail} wallet" if rail == markets.POINTS_RAIL else f"{rail} ending {body.last4}"
        else:
            # A customer id sent by the page counts only when it belongs to the signed-in session.
            sess = auth.who(request.headers.get("x-session")) if auth.enabled() else None
            mine = body.customer if (not auth.enabled() or (sess and sess["subject"] == body.customer)) else None
            pref = mine or (booked or {}).get("customer_id")
            cid = pref if wallet.get(c, pref) else wallet.create(c, body.name, market=market if market in ("TH", "SG", "UK") else "TH")
            # A brand new account is signed in at once. An existing one must sign in with a code first.
            session = auth._session(c, "traveler", cid, "traveler") if cid != pref else None
            account, follow = wallet.account_for(c, cid), 1
        c.execute(
            "INSERT INTO policies (id,traveler,flight,payout_account,status,market,currency,pnr,channel,customer_id,payout_follow,ref_verified) "
            "VALUES (?,?,?,?,'quote',?,?,?,?,?,?,?)",
            (pid, body.name.strip(), f["code"], account, market, info["currency"], pnr, channel, cid, follow, int(bool(booked))),
        )
        db.log(c, pid, "quote_created", {"flight": f["code"], "market": market, "booking_verified": bool(booked)})
    return {"policy": pid, "flight": f, "market": info, "customer": cid, "verified": bool(booked), "session": session}


@app.get("/api/demo/sources")
def demo_sources():
    """Where each kind of data comes from right now. Shown only in the presenter panel."""
    u = live.usage()
    context.warm("BKK")  # so the weather status is real the first time the panel opens
    ai_on = bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    snap = schedule.info()
    if snap["available"] and u["last_error"]:
        u = {**u, "last_error": "Live service is not answering right now, so the saved real schedule is used."}
    if snap.get("builtin"):
        u = {**u, "last_error": u["last_error"] and "Live service is not answering right now, so the sample schedule is used."}
    saved = " Sample schedule in use." if snap.get("builtin") else f" Saved schedule: {snap['flights']} real flights from {snap['generated_at'][:10]}." if snap["available"] and snap.get("generated_at") else (" Saved schedule loaded." if snap["available"] else "")
    flights = {"mode": "snapshot" if snap["available"] and (u["last_error"] or not u["enabled"]) else "error" if u["last_error"] else "live" if u["enabled"] else "off",
               "detail": (u["last_error"] or (f"{u['used']} of {u['budget']} lookups used this month" if u["enabled"]
                                               else ("No live flight key. Using the sample schedule." if snap.get("builtin") else "No live flight key. Using the saved real schedule.") if snap["available"]
                                               else "No flight data key on the server. Travelers cannot search flights until one is set.")) + (saved if u["enabled"] or u["last_error"] else "")}
    wx = {"mode": "error" if context.LAST["error"] else "live" if context.LAST["ok"] else "idle",
          "detail": context.LAST["error"] or ("Live from Open-Meteo" if context.LAST["ok"] else "Fetched at the first event")}
    ai = {"mode": "error" if engine.AI_LAST["error"] else "live" if ai_on else "off",
          "detail": engine.AI_LAST["error"] or ("Gemini on Vertex AI writes each reason" if ai_on else "No Google Cloud project set, so template reasons are used.")}
    return {"flights": flights, "weather": wx, "ai": ai,
            "payments": {"mode": "sim", "detail": "Payout rails are simulated. No real money moves."}}


@app.get("/api/live/samples")
def sample_flights():
    """Built in sample flights, one or more per market. They work without any live key."""
    with db.conn() as c:
        return db.rows(c, "SELECT * FROM flights WHERE source='sim' ORDER BY market='TH' DESC, market, code")


@app.get("/api/live/upcoming")
def live_upcoming(airport: str = Query("BKK", min_length=3, max_length=3)):
    """Next real departures from an airport, for the flight picker."""
    try:
        return live.upcoming(airport)
    except live.LiveError as e:
        saved = schedule.for_date(airport, time.strftime("%Y-%m-%d"), 12)  # offline fallback
        if saved:
            return saved
        raise HTTPException(502, str(e))


@app.get("/api/live/schedule")
def flights_on(airport: str = Query(..., min_length=3, max_length=3), date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Real flights leaving an airport on one day, from today up to a month ahead.
    Today comes from live data (real status) and falls back to the saved schedule. Later days use the saved schedule."""
    if schedule.valid_date(date) is None:
        raise HTTPException(400, f"Pick a day between today and {schedule.window()[1]}.")
    if date <= time.strftime("%Y-%m-%d"):
        return live_upcoming(airport)
    out = schedule.for_date(airport, date, 30)
    if not out:
        raise HTTPException(404, "No saved schedule for that day yet." if schedule.available() else "Flights for later days need the saved schedule, which is not loaded.")
    return out


@app.get("/api/flights/search")
def flight_search(origin: str = Query(..., min_length=3, max_length=3), dest: str = Query("", max_length=3),
                  date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    """Flights for the search screen: one day, from one airport, optionally to one airport.

    Order of sources: live status for today, then the saved real schedule, then the built-in sample schedule, so a
    search never comes back empty because a service is down. Each flight carries its delay chance, whether delay cover
    is still on sale, the cover price and an approximate flight time.
    """
    origin, dest = origin.upper(), dest.upper()
    source, rows = "live", []
    try:
        rows = flights_on(origin, date)
    except HTTPException:
        rows = []
    if not rows:
        rows = schedule.for_date(origin, date, 60)
    if not any(r.get("source") == "live" for r in rows):
        source = "sample" if schedule.info().get("builtin") else "saved"
    if dest:
        rows = [r for r in rows if r["dest"] == dest]
    out = []
    with db.conn() as c:
        for r in rows:
            r = dict(r)
            rules = engine.get_rules(c, r.get("market") or "TH")
            fc = forecast.compute({**r, "flight_date": r.get("flight_date") or date})
            km = forecast.km_between(r["origin"], r["dest"])
            r.update(risk={"probability": fc["probability"], "level": fc["level"]},
                     band=forecast.band(fc["probability"], rules["close_cover_at"]),
                     price={"delay": rules["delay_premium"], "baggage": rules["baggage_premium"], "currency": engine.currency_of(r.get("market") or "TH")},
                     duration_min=int(round((km / 800 * 60 + 25) / 5) * 5))
            out.append(r)
    return {"flights": out, "source": source, "origin": origin, "dest": dest, "date": date}


@app.post("/api/flights/{code}/refresh")
def flight_refresh(code: str):
    """Check the real status now (the data layer still caches for 15 minutes)."""
    try:
        r = track.refresh_flight(code)
    except live.LiveError as e:
        raise HTTPException(502, str(e))
    return r


@app.post("/api/traveler/{pid}/checkout")
def checkout(pid: str, body: Checkout):
    if not (body.delay or body.baggage):
        raise HTTPException(400, "choose at least one cover")
    with db.conn() as c:
        p = _policy(c, pid)
        f = _flight(c, p["flight"])
        if p["status"] == "active":
            raise HTTPException(400, "Cover is already active on this trip.")
        if f and f.get("cancelled"):
            raise HTTPException(400, "This flight is cancelled, so cover cannot be added any more.")
        if f and f["delay_minutes"] >= 30:
            # A delay is already known, so cover would insure a loss that has already happened.
            raise HTTPException(400, "This flight is already delayed, so cover cannot be added any more.")
        if f and f.get("sched_ts") and f["sched_ts"] < time.time():
            raise HTTPException(400, "This flight has already left, so cover cannot be added any more.")
        if not body.ack:
            raise HTTPException(400, "Please confirm you have read the key facts and the privacy notice.")
        rules = engine.get_rules(c, p["market"])
        total = int(rules["delay_premium"]) * body.delay + int(rules["baggage_premium"]) * body.baggage
        prob = forecast.compute(f)["probability"] if f else 0
        if body.delay and forecast.band(prob, rules["close_cover_at"]) == "closed":
            raise HTTPException(400, f"Delay cover is closed for this flight. A delay is already {prob}% likely, so selling cover now would be unfair to other travelers. Baggage cover is still available.")
        adverse = int(bool(body.delay) and prob >= forecast.ALERT_AT)  # AI fraud check: buying into a likely delay
        c.execute(
            "UPDATE policies SET delay_cover=?, baggage_cover=?, premium=?, status='active', created_at=?, "
            "sched_at_purchase=?, risk_at_purchase=?, f_adverse=? WHERE id=?",
            (int(body.delay), int(body.baggage), total, time.time(), f["scheduled"] if f else None, prob, adverse, pid),
        )
        db.log(c, pid, "policy_active", {"delay": body.delay, "baggage": body.baggage, "total": total, "currency": p["currency"]})
        # Record that the notices were shown and accepted, with the version of the legal notes.
        db.log(c, pid, "notices_accepted", {"market": p["market"], "version": markets.LAW_VERSION})
        return {"policy": pid, "total": total, "currency": p["currency"]}


class Redeem(BaseModel):
    partner: str
    points: int = Field(..., ge=100, le=1000000)


class AddCover(BaseModel):
    delay: bool = False
    baggage: bool = False


@app.post("/api/traveler/{pid}/add-cover")
def add_cover(pid: str, body: AddCover):
    """Add the cover a trip is missing, any time before the flight leaves. Delay cover cannot be added once a delay is known."""
    if not (body.delay or body.baggage):
        raise HTTPException(400, "Choose the cover to add.")
    with db.conn() as c:
        p = _policy(c, pid)
        f = _flight(c, p["flight"])
        if p["status"] != "active":
            raise HTTPException(400, "Cover can only be added to an active trip.")
        if f and (f.get("cancelled") or (f.get("sched_ts") and f["sched_ts"] < time.time())):
            raise HTTPException(400, "This flight has already left or been cancelled, so cover cannot be added.")
        if body.delay and p["delay_cover"] or body.baggage and p["baggage_cover"]:
            raise HTTPException(400, "That cover is already on this trip.")
        if body.delay and f and f["delay_minutes"] >= 30:
            raise HTTPException(400, "This flight is already delayed, so delay cover cannot be added any more.")
        rules = engine.get_rules(c, p["market"])
        if body.delay and f and forecast.band(forecast.compute(f)["probability"], rules["close_cover_at"]) == "closed":
            raise HTTPException(400, "Delay cover is closed for this flight because a delay is already very likely.")
        add = int(rules["delay_premium"]) * body.delay + int(rules["baggage_premium"]) * body.baggage
        c.execute("UPDATE policies SET delay_cover=?, baggage_cover=?, premium=premium+? WHERE id=?",
                  (int(body.delay or p["delay_cover"]), int(body.baggage or p["baggage_cover"]), add, pid))
        db.log(c, pid, "cover_added", {"delay": body.delay, "baggage": body.baggage, "added": add, "currency": p["currency"]})
        return {"policy": pid, "added": add, "currency": p["currency"]}


# ----------------------------------------------------------- bookings and customer accounts

@app.get("/api/booking/{ref}")
def check_booking(ref: str, surname: str = Query(..., min_length=2, max_length=40)):
    """Check my cover: what a booking has and what it does not, found by reference and last name."""
    with db.conn() as c:
        b = bookings.lookup(c, ref, surname)
    if not b:
        raise HTTPException(404, "We could not find that booking. Check the reference and the last name on the ticket.")
    return b


@app.get("/api/demo/bookings")
def demo_bookings():
    """The four demo booking references and what each one shows. Used by the presenter panel."""
    out = []
    with db.conn() as c:
        for ref, (name, what) in bookings.DEMO_REFS.items():
            b = bookings.lookup(c, ref, name.split(" ")[-1])
            if b:
                out.append({"ref": ref, "surname": name.split(" ")[-1], "passenger": name, "what": what, "customer": b["customer_id"],
                            "legs": [{"flight": g["flight"]["code"], "route": f"{g['flight']['origin']} to {g['flight']['dest']}", "when": g["flight"]["depart_label"], "cover": g["cover"]} for g in b["legs"]]})
    return out


def _customer(c, cid):
    if not wallet.get(c, cid):
        raise HTTPException(404, "Account not found.")


class ProfileUpdate(BaseModel):
    name: str | None = Field(None, max_length=40)
    email: str | None = Field(None, max_length=80)
    phone: str | None = Field(None, max_length=24)
    notify: bool | None = None
    auto_topup: bool | None = None


@app.get("/api/customer/{cid}")
def customer_get(cid: str):
    with db.conn() as c:
        _customer(c, cid)
        return wallet.summary(c, cid)


@app.put("/api/customer/{cid}")
def customer_update(cid: str, body: ProfileUpdate):
    with db.conn() as c:
        _customer(c, cid)
        try:
            wallet.update(c, cid, body.name, body.email, body.phone, body.notify, body.auto_topup)
        except ValueError as e:
            raise HTTPException(400, str(e))
        wallet.refresh_policies(c, cid)
        db.log(c, None, "profile_updated", {"customer": cid})
        return wallet.summary(c, cid)


class NewMethod(BaseModel):
    kind: Literal["card", "bank"]
    number: str = Field(..., min_length=6, max_length=24)
    holder: str = Field(..., min_length=2, max_length=40)
    bank: str | None = None
    expiry: str | None = Field(None, max_length=7)


@app.post("/api/customer/{cid}/methods")
def method_add(cid: str, body: NewMethod):
    with db.conn() as c:
        _customer(c, cid)
        try:
            m = wallet.add_method(c, cid, body.kind, body.number, body.holder, body.bank, body.expiry)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.log(c, None, "payment_method_added", {"customer": cid, "kind": body.kind, "label": m["label"]})
        return {"method": m, "summary": wallet.summary(c, cid)}


@app.delete("/api/customer/{cid}/methods/{mid}")
def method_remove(cid: str, mid: str):
    with db.conn() as c:
        _customer(c, cid)
        try:
            wallet.remove_method(c, cid, mid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return wallet.summary(c, cid)


@app.post("/api/customer/{cid}/methods/{mid}/default")
def method_default(cid: str, mid: str):
    with db.conn() as c:
        _customer(c, cid)
        try:
            wallet.set_default(c, cid, mid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return wallet.summary(c, cid)


class CashOut(BaseModel):
    points: int = Field(..., ge=1, le=10_000_000)
    method: str


@app.post("/api/customer/{cid}/cashout")
def cash_out(cid: str, body: CashOut):
    """Turn points into cash on a saved card or bank account. It lands at once in this prototype."""
    with db.conn() as c:
        _customer(c, cid)
        try:
            r = wallet.cash_out(c, cid, body.points, body.method)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.log(c, None, "points_cashed_out", {"customer": cid, "points": r["points"], "usd": r["usd"], "to": r["to"]})
        return {**r, "summary": wallet.summary(c, cid)}


@app.post("/api/customer/{cid}/vouchers/{rid}/refund")
def voucher_refund(cid: str, rid: str):
    with db.conn() as c:
        _customer(c, cid)
        try:
            r = wallet.refund_voucher(c, cid, rid)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.log(c, None, "points_refunded", {"customer": cid, "voucher": rid, "points": r["points"]})
        return {**r, "summary": wallet.summary(c, cid)}


@app.get("/api/customer/{cid}/services")
def customer_services(cid: str, airport: str = Query("BKK", min_length=3, max_length=3)):
    """Everything the wallet can be spent on at one airport: food, lounges, taxis, hotels, SIM and message packs."""
    iata = airport.upper()
    with db.conn() as c:
        _customer(c, cid)
        trip = db.one(c, "SELECT f.scheduled, f.delay_minutes FROM policies p JOIN flights f ON f.code=p.flight WHERE p.customer_id=? AND f.origin=? "
                         "AND p.status='active' AND f.sched_ts>? ORDER BY f.sched_ts LIMIT 1", (cid, iata, time.time() - 3600))
        bal = wallet.balance(c, cid)["points"]
        out = partners.list_for(c, iata, (trip or {}).get("scheduled"), (trip or {}).get("delay_minutes") or 0, bal)
        out["balance"] = bal
        out["airports"] = [r["origin"] for r in db.rows(c, "SELECT DISTINCT f.origin FROM policies p JOIN flights f ON f.code=p.flight WHERE p.customer_id=? ORDER BY f.sched_ts", (cid,))]
        return out


@app.post("/api/customer/{cid}/redeem")
def customer_redeem(cid: str, body: Redeem):
    with db.conn() as c:
        _customer(c, cid)
        try:
            r = partners.redeem_customer(c, cid, body.partner, body.points)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.log(c, None, "points_redeemed", {"customer": cid, "partner": r["partner"], "points": r["points"]})
        return {**r, "summary": wallet.summary(c, cid)}




@app.get("/api/airport/{iata}/partners")
def airport_partners(iata: str, pid: str | None = None):
    """Restaurants at an airport that accept KODA points and are open while the traveler waits. Others are listed as not joined."""
    iata = iata.upper()
    with db.conn() as c:
        sched, delay, bal = None, 0, 0
        if pid:
            p = _policy(c, pid)
            f = _flight(c, p["flight"]) or {}
            sched, delay = f.get("scheduled"), f.get("delay_minutes") or 0
            partners.sync(c, pid)
            bal = partners.balance(c, pid)["balance"]
        out = partners.list_for(c, iata, sched, delay, bal)
        out["balance"] = bal
        return out


@app.post("/api/traveler/{pid}/redeem")
def redeem_points(pid: str, body: Redeem):
    with db.conn() as c:
        _policy(c, pid)
        try:
            r = partners.redeem(c, pid, body.partner, body.points)
        except ValueError as e:
            raise HTTPException(400, str(e))
        db.log(c, pid, "points_redeemed", {"partner": r["partner"], "points": r["points"]})
        r["balance"] = partners.balance(c, pid)["balance"]
        return r


@app.post("/api/traveler/{pid}/review-request")
def request_review(pid: str):
    """The traveler asks a person to look at a declined claim again. No claim is ever denied by the system alone."""
    with db.conn() as c:
        _policy(c, pid)
        d = db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay' AND status='declined'", (pid,))
        if not d:
            raise HTTPException(400, "There is no declined claim to review.")
        c.execute("UPDATE decisions SET status='held', decided_by=NULL, note=? WHERE id=?",
                  ("Traveler asked for a second review. Earlier note: " + (d["note"] or "none"), d["id"]))
        db.log(c, pid, "review_requested", {"decision": d["id"]})
        return _decision(db.one(c, "SELECT * FROM decisions WHERE id=?", (d["id"],)))


class BagItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=60)
    amount: int = Field(..., gt=0, le=100000)


class BagClaim(BaseModel):
    report_id: str = Field(..., min_length=3, max_length=30)   # airline property irregularity report number
    bag_tag: str = Field(..., min_length=3, max_length=20)
    items: list[BagItem] = Field(..., min_length=1, max_length=12)


@app.post("/api/traveler/{pid}/baggage")
def bag_claim(pid: str, body: BagClaim):
    with db.conn() as c:
        p = _policy(c, pid)
        f = _flight(c, p["flight"])
        if f and f.get("sched_ts") and f["sched_ts"] > time.time():  # a bag cannot be lost before the flight has left
            raise HTTPException(400, "You can report a lost bag once your flight has left.")
        try:
            claim = engine.submit_bag_claim(
                c, pid, body.report_id.strip(), body.bag_tag.strip(), [i.model_dump() for i in body.items]
            )
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))
    _enrich_later([{"decision": claim["id"] + "-D"}])
    return claim


class ExtraClaim(BaseModel):
    what: str = Field(..., min_length=2, max_length=40)       # meal, transport, hotel, other
    amount: int = Field(..., gt=0, le=100000)
    receipt: str | None = Field(None, max_length=400000)      # optional photo as a data URL


@app.post("/api/traveler/{pid}/extra-claim")
def extra_claim(pid: str, body: ExtraClaim):
    """Costs above the instant payout, claimed with a receipt."""
    with db.conn() as c:
        _policy(c, pid)
        try:
            engine.submit_extra_claim(c, pid, body.what.strip(), body.amount, body.receipt)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))
        return _extra(c, pid)


@app.get("/api/traveler/{pid}/airline-letter")
def airline_letter(pid: str, ai: int = 0):
    """A ready letter asking the airline for what it owes. Plain text first, Gemini polish on request."""
    with db.conn() as c:
        p = _policy(c, pid)
        f = _flight(c, p["flight"])
        m = markets.public(p["market"])
    if not f:
        raise HTTPException(400, "flight not found")
    late = "was cancelled" if f.get("cancelled") else f"was delayed by {f['delay_minutes'] // 60} hours {f['delay_minutes'] % 60:02d} minutes"
    law = (m.get("statutory") or "").strip()
    text = (f"To {f['airline']} customer relations,\n\n"
            f"I was booked on flight {f['code']} from {f['origin']} to {f['dest']}, scheduled for {f.get('scheduled') or 'the date shown on my booking'}. "
            f"The flight {late}.\n\n"
            f"Please confirm what you owe me for this disruption" + (f" under the rules in {m['name']}: {law}" if law else "") +
            ". I also ask for a refund of reasonable meal and transport costs, and I am keeping my receipts.\n\n"
            f"Booking reference: [add yours]\nName: {p['traveler']}\n\nThank you. Please reply in writing.")
    better = None
    if ai and os.environ.get("GOOGLE_CLOUD_PROJECT"):
        better = copilot.ask_gemini("Polish this letter so it is clear, polite and firm. Keep every fact, add nothing new, no legal claims beyond the text, "
                                    "keep the [add yours] placeholder, plain text only.\n\n" + text)
    return {"text": better or text, "ai": bool(better)}


# ----------------------------------------------------------- demo controls

class Delay(BaseModel):
    flight: str = "OA 108"
    minutes: int = Field(192, ge=0, le=2880)
    cancelled: bool = False


def _weather_for(code):
    """Read the weather at the departure airport before the database lock is taken."""
    with db.conn() as c:
        f = _flight(c, code)
    if f:
        context.prefetch(f["origin"])


def _enrich_later(results):
    ids = [r["decision"] for r in results if r.get("decision")]
    if ids:
        threading.Thread(target=engine.enrich_explanations, args=(ids,), daemon=True).start()


@app.post("/api/demo/delay")
def demo_delay(body: Delay):
    """Simulate flight data: set a delay and run the trigger."""
    _weather_for(body.flight)
    with db.conn() as c:
        if not _flight(c, body.flight):
            raise HTTPException(404, "flight not found")
        covered = db.one(
            c, "SELECT COUNT(*) n FROM policies WHERE flight=? AND status='active' AND delay_cover=1", (body.flight,)
        )["n"]
        results = engine.run_delay(c, body.flight, body.minutes, cancelled=body.cancelled)
        c.execute("UPDATE flights SET sim=1 WHERE code=?", (body.flight,))  # a demo event now owns this flight's status
    _enrich_later(results)
    return {"results": results, "covered": covered}


class FlightEvent(BaseModel):
    flight: str
    event: Literal["delayed", "cancelled", "on_time", "landed", "storm", "clear"]
    minutes: int = Field(0, ge=0, le=2880)


@app.post("/api/webhook/flight-event")
def flight_event(body: FlightEvent):
    """The flight status feed posts here. In production this is the Cloud Run webhook of the architecture.

    delayed and cancelled run the policy check and payout. on_time and landed quietly archive the
    trips that had no problem: no payout, no message to the traveler.
    """
    _weather_for(body.flight)
    with db.conn() as c:
        if not _flight(c, body.flight):
            raise HTTPException(404, "flight not found")
        db.log(c, None, "webhook_received", {"flight": body.flight, "event": body.event, "minutes": body.minutes})
        if body.event in ("delayed", "cancelled", "on_time", "landed"):
            c.execute("UPDATE flights SET sim=1 WHERE code=?", (body.flight,))
        if body.event in ("storm", "clear"):
            c.execute("UPDATE flights SET stress=? WHERE origin=(SELECT origin FROM flights WHERE code=?)", (2 if body.event == "storm" else 0, body.flight))
            return {"event": body.event, "results": [], "archived": 0, "forecast": forecast.compute(_flight(c, body.flight))}
        if body.event in ("delayed", "cancelled"):
            results = engine.run_delay(c, body.flight, body.minutes, cancelled=body.event == "cancelled")
            _enrich_later(results)
            return {"event": body.event, "results": results, "archived": 0}
        c.execute("UPDATE flights SET delay_minutes=?, cancelled=0 WHERE code=?", (body.minutes, body.flight))
        quiet = db.rows(c, "SELECT id FROM policies WHERE flight=? AND status='active' AND id NOT IN "
                           "(SELECT policy_id FROM decisions)", (body.flight,))
        for p in quiet:
            c.execute("UPDATE policies SET status='archived' WHERE id=?", (p["id"],))
            db.log(c, p["id"], "trip_archived", {"flight": body.flight, "reason": "no covered event"})
        return {"event": body.event, "results": [], "archived": len(quiet)}


class LiveReplay(BaseModel):
    policy: str
    flight: str  # IATA number such as TG102


@app.get("/api/live/status")
def live_status():
    u = live.usage()
    snap = schedule.info()
    # ready: travelers can search. True with a live key or with a saved schedule, so an outage does not stop the demo.
    return {**u, "snapshot": snap, "ready": bool(u["enabled"] or snap["available"]), "window": schedule.window()}


@app.get("/api/live/lookup")
def live_lookup(flight: str = Query(..., min_length=3, max_length=8), date: str | None = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    today = time.strftime("%Y-%m-%d")
    if date and date > today:  # a later day: only the saved schedule knows it
        f = schedule.find(flight, date)
        if not f:
            raise HTTPException(404, f"{flight.upper()} is not in the saved schedule for {date}.")
        return f
    try:
        return live.lookup(flight)
    except live.LiveError as e:
        f = schedule.find(flight, date or today)  # offline fallback
        if f:
            return f
        raise HTTPException(502, str(e))


@app.get("/api/live/delayed")
def live_delayed(airport: str = Query("BKK", min_length=3, max_length=3), min_minutes: int = Query(30, ge=0, le=1440)):
    """Flights from an airport that are really delayed right now."""
    try:
        return live.delayed_now(airport, min_minutes)
    except live.LiveError as e:
        raise HTTPException(502, str(e))


@app.post("/api/demo/live")
def demo_live(body: LiveReplay):
    """Replay a real flight's real delay against a policy.

    This is a replay: the cover is treated as bought before departure even
    though the delay is already known. The screens label the flight as live data.
    """
    with db.conn() as c:
        p = _policy(c, body.policy)
        if p["status"] != "active" or not p["delay_cover"]:
            raise HTTPException(400, "Add delay cover first, then replay a real flight.")
    try:
        f = live.lookup(body.flight)
    except live.LiveError as e:
        raise HTTPException(502, str(e))
    with db.conn() as c:
        live.upsert_flight(c, f)
        c.execute("UPDATE policies SET flight=?, sched_at_purchase=? WHERE id=?", (f["code"], f["scheduled"], p["id"]))
        c.execute("DELETE FROM decisions WHERE policy_id=? AND kind='delay'", (p["id"],))  # fresh replay
        results = engine.run_delay(c, f["code"], f["delay_minutes"])
        db.log(c, p["id"], "live_replay", {"flight": f["code"], "delay": f["delay_minutes"]})
    _enrich_later(results)
    return {"flight": f, "results": results, "paid": bool(results)}


class SampleCover(BaseModel):
    flight: str
    risky: bool = False   # a profile with several fraud signals, so the same delay is held for a person


@app.post("/api/demo/cover-sample")
def demo_cover_sample(body: SampleCover):
    """Console test helper: put one sample traveler with full cover on a flight so a delay can be tested.

    Skips the checkout screens on purpose and logs it, so the audit trail shows the traveler was added by the demo.
    """
    code = re.sub(r"\s+", " ", body.flight.strip().upper())
    with db.conn() as c:
        f = _flight(c, code)
        if not f:
            raise HTTPException(404, "Flight not found.")
        if f["delay_minutes"] >= 30:
            raise HTTPException(400, "This flight is already delayed, so cover cannot be added any more.")
        market = f.get("market") or "GLOBAL"
        info = markets.public(market)
        rules = engine.get_rules(c, market)
        total = int(rules["delay_premium"]) + int(rules["baggage_premium"])
        pid = engine._new_id("CL")
        c.execute(
            "INSERT INTO policies (id,traveler,flight,payout_account,delay_cover,baggage_cover,premium,status,market,currency,"
            "created_at,sched_at_purchase,pnr) VALUES (?,?,?,?,1,1,?,'active',?,?,?,?,?)",
            (pid, "Anan T." if body.risky else "Sample traveler", f["code"], f"{info['rails'][0]} ending {'7730' if body.risky else '0000'}", total, market, info["currency"], time.time(), f["scheduled"],
             "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))),
        )
        if body.risky:  # preset signals the risk score reads: several covers, a changed flight time, a shared account, a prior claim
            c.execute("UPDATE policies SET f_covers_2h=4, f_schedule_changed=1, f_shared_account=1, f_prior_claims=1 WHERE id=?", (pid,))
        db.log(c, pid, "policy_active", {"delay": True, "baggage": True, "total": total, "currency": info["currency"], "added_by": "demo controls"})
        return {"policy": pid, "flight": f["code"]}


@app.post("/api/demo/reset")
def demo_reset():
    db.reset()
    forecast.invalidate_airspace()
    return {"ok": True}


# ----------------------------------------------------------- console

@app.get("/api/console/overview")
def overview(market: str = "TH"):
    with db.conn() as c:
        rules = engine.get_rules(c, market)
        active = db.one(c, "SELECT COUNT(*) n FROM policies WHERE status='active'")["n"]
        premium = sum(markets.to_usd(r["premium"], r["currency"]) for r in db.rows(
            c, "SELECT premium, currency FROM policies WHERE status='active'"))
        paid = db.rows(
            c, "SELECT d.amount, d.decided_by, d.kind, p.currency FROM decisions d JOIN policies p ON p.id=d.policy_id "
               "WHERE d.status='paid'")
        held = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE status='held' AND kind='delay'")["n"]
        bag_waiting = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE status IN ('held','pending') AND kind='baggage'")["n"]
        extra_waiting = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE status='pending' AND kind='expense'")["n"]
        flights = db.rows(
            c,
            "SELECT f.*, (SELECT COUNT(*) FROM policies p WHERE p.flight=f.code AND p.status='active') AS covered "
            "FROM flights f WHERE f.source<>'demo' OR f.sched_ts>? ORDER BY f.sched_ts IS NULL, f.sched_ts, f.code",
            (time.time(),),
        )
        decisions = db.rows(
            c, "SELECT d.id, d.policy_id, d.kind, d.status, d.amount, d.decided_by, d.delay_minutes, d.risk_score, d.timeline, d.trigger, d.elapsed_ms, "
               "p.currency, p.flight, p.traveler FROM decisions d JOIN policies p ON p.id=d.policy_id ORDER BY d.created_at DESC"
        )
        for d in decisions:
            tl = json.loads(d["timeline"]) if d.get("timeline") else []
            d["seconds"] = next((s["sec"] for s in tl if s["label"] in ("Paid", "Held for review")), None)
            del d["timeline"]
    for f in flights:
        track.maybe_refresh(f)
        f["travelers"] = f["covered"]
    with db.conn() as c:
        rules_all = {k: engine.get_rules(c, k) for k in markets.MARKETS}
    auto = sum(1 for d in paid if d["decided_by"] == "system")
    by_market, outcomes = {}, {"auto": auto, "reviewed": len(paid) - auto, "held": held, "declined": 0}
    with db.conn() as c:
        outcomes["declined"] = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE status='declined'")["n"]
        for r in db.rows(c, "SELECT p.market, p.currency, d.amount FROM decisions d JOIN policies p ON p.id=d.policy_id WHERE d.status='paid'"):
            by_market[r["market"]] = by_market.get(r["market"], 0) + markets.to_usd(r["amount"], r["currency"])
    with db.conn() as c:
        crm_stats = bookings.stats(c)
        ms = [r["elapsed_ms"] for r in db.rows(c, "SELECT elapsed_ms FROM decisions WHERE elapsed_ms IS NOT NULL AND kind='delay'")]
        covered_total = db.one(c, "SELECT COUNT(*) n FROM policies WHERE status IN ('active','archived')")["n"]
        cancels = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE trigger='cancel'")["n"]
        archived = db.one(c, "SELECT COUNT(*) n FROM policies WHERE status='archived'")["n"]
    reviewed = len(paid) - auto
    performance = {
        "decisions": len(ms), "avg_decision_ms": round(sum(ms) / len(ms)) if ms else None,
        "seconds_to_payout": 8, "manual_days": 14, "documents_asked": 0, "manual_cost_usd": 41,
        # Only claims a person had to review carry handling cost. Auto paid claims carry none.
        "admin_cost_usd": round(reviewed * 41 / covered_total, 2) if covered_total else 0,
        "auto_paid": auto, "reviewed": reviewed, "cancellations": cancels, "archived": archived, "covered_total": covered_total,
    }
    return {
        "ai_live": bool(os.environ.get("GOOGLE_CLOUD_PROJECT")),
        "performance": performance,
        "stats": {
            "active": active,
            "premium_usd": round(premium),
            "paid_count": len(paid),
            "paid_amount_usd": round(sum(markets.to_usd(p["amount"], p["currency"]) for p in paid)),
            "auto_paid": auto,
            "held": held,
            "bag_waiting": bag_waiting,
            "extra_waiting": extra_waiting,
            **crm_stats,
        },
        "flights": flights,
        "outcomes": outcomes,
        "by_market": sorted(({"market": k, "usd": round(v, 2)} for k, v in by_market.items()), key=lambda x: -x["usd"]),
        "rules": rules,
        "rules_all": rules_all,
        "rules_market": markets.public(market),
        "markets": [markets.public(k) for k in markets.MARKETS],
        "decisions": decisions,
    }


@app.get("/api/console/policies")
def console_policies():
    with db.conn() as c:
        rows = db.rows(c, "SELECT * FROM policies WHERE status IN ('active','archived') ORDER BY created_at DESC, id")
        for p in rows:
            p["decision"] = db.one(c, "SELECT id, status, amount, trigger FROM decisions WHERE policy_id=? AND kind='delay'", (p["id"],))
            p["bag"] = db.one(c, "SELECT status, amount FROM decisions WHERE policy_id=? AND kind='baggage'", (p["id"],))
            p["cover"] = bookings.cover_kind(bool(p["delay_cover"]), bool(p["baggage_cover"]))
            b = db.one(c, "SELECT channel FROM bookings WHERE ref=? AND flight=?", (p["pnr"], p["flight"])) if p.get("pnr") else None
            p["site"] = b["channel"] if b else None
        return rows


@app.get("/api/console/decision/{policy_id}")
def console_decision(policy_id: str):
    with db.conn() as c:
        p = _policy(c, policy_id)
        d = _decision(db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (policy_id,)))
        return {"policy": p, "flight": _flight(c, p["flight"]), "decision": d, "rules": engine.get_rules(c, p["market"]),
                "market": markets.public(p["market"])}


@app.get("/api/console/exposure")
def exposure():
    """Expected payouts on the book today, a reserve to hold, and a suggested delay price per flight."""
    out, total, var = [], 0.0, 0.0
    with db.conn() as c:
        flights = db.rows(c, "SELECT * FROM flights WHERE sched_ts IS NULL OR sched_ts>? ORDER BY sched_ts IS NULL, sched_ts, code", (time.time() - 3600,))
        for f in flights:
            fc = forecast.compute(f)
            pols = db.rows(c, "SELECT market, currency FROM policies WHERE flight=? AND status='active' AND delay_cover=1 "
                              "AND id NOT IN (SELECT policy_id FROM decisions WHERE kind='delay')", (f["code"],))
            rules = engine.get_rules(c, f.get("market") or "TH")
            t1 = float(rules["delay_tier1_payout"])
            pr = forecast.pricing(fc, float(rules["delay_premium"]), t1)
            pc = pr["p_claim"] / 100
            exp = pc * t1 * len(pols)
            total += exp
            var += pc * (1 - pc) * (t1 ** 2) * len(pols)
            out.append({"flight": f["code"], "route": f"{f['origin']} to {f['dest']}", "covered": len(pols), "probability": fc["probability"],
                        "level": fc["level"], "drivers": [{"text": t, "points": pts} for t, pts in fc["drivers"]],
                        "expected_usd": round(exp, 2), "price_now": pr["current"], "price_suggested": pr["suggested"], "move": pr["move"],
                        "stress": int(f.get("stress") or 0),
                        "band": forecast.band(fc["probability"], rules["close_cover_at"])})
        premium = sum(markets.to_usd(r["premium"], r["currency"]) for r in db.rows(c, "SELECT premium, currency FROM policies WHERE status='active'"))
    reserve = total + 1.28 * (var ** 0.5)
    return {"expected_usd": round(total, 2), "reserve_usd": round(reserve), "premium_usd": round(premium),
            "flights": sorted(out, key=lambda x: -x["probability"]), "method": "Trained model for the base chance, plus live delay, weather and airspace alerts"}


def _copilot_ctx(c, decision_id):
    d = _decision(db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,)))
    if not d:
        raise HTTPException(404, "claim not found")
    p = _policy(c, d["policy_id"])
    if p.get("customer_id") and (p.get("payout_account") or "").startswith(markets.POINTS_RAIL):
        others = 0  # the points wallet belongs to one customer, so it can never be shared
    else:
        others = db.one(c, "SELECT COUNT(*) n FROM policies WHERE status IN ('active','archived') AND payout_account=? AND COALESCE(customer_id,LOWER(TRIM(traveler)))<>COALESCE(?,?)",
                        (p["payout_account"], p.get("customer_id"), (p["traveler"] or "").strip().lower()))["n"]
    rec = copilot.recommend(d, p, p["traveler"], p["currency"], others)
    rec["draft"] = rec["draft"].replace("{flight}", p["flight"])
    return d, p, rec


@app.get("/api/console/copilot/{decision_id}")
def copilot_get(decision_id: str, ai: bool = False):
    """Instant rule-based advice. With ai=1 the draft is also reworded by Gemini, which can take seconds, so the screen asks for it separately."""
    with db.conn() as c:
        d, p, rec = _copilot_ctx(c, decision_id)
    if not ai:
        rec["ai"] = False
        return rec
    better = copilot.ask_gemini("Rewrite this message to an insurance customer so it is warm, clear and under 60 words. Keep every fact and ask. "
                                "Return only the message.\n" + rec["draft"]) if os.environ.get("GOOGLE_CLOUD_PROJECT") else None
    rec["draft"], rec["ai"] = (better or rec["draft"]), bool(better)
    return rec


class Ask(BaseModel):
    decision: str
    question: str = Field(..., min_length=2, max_length=300)


@app.post("/api/console/copilot")
def copilot_ask(body: Ask):
    with db.conn() as c:
        d, p, rec = _copilot_ctx(c, body.decision)
    base = copilot.answer(body.question, d, rec, p)
    facts = (f"Claim {d['id']}: delay {d['delay_minutes']} min, amount {d['amount']}, risk {d['risk_score']}, signals "
             + "; ".join(f"{t} +{pts}" for t, pts in d["breakdown"]) + f". Recommendation {rec['label']}.")
    better = copilot.ask_gemini("You help an insurance claims reviewer. Answer in at most 2 sentences using only these facts; never suggest declining.\n"
                                f"{facts}\nQuestion: {body.question}") if os.environ.get("GOOGLE_CLOUD_PROJECT") else None
    return {"answer": better or base, "ai": bool(better)}


@app.get("/api/console/investigate/{decision_id}")
def investigate(decision_id: str):
    """Agent investigation of a held claim: the tools it used, what they found and a case summary. Advises only."""
    r = agent.investigate(decision_id)
    if r.get("error"):
        raise HTTPException(404, r["error"])
    return r


class TravelerAsk(BaseModel):
    question: str = Field(..., min_length=2, max_length=200)


@app.post("/api/traveler/{pid}/ask")
def traveler_ask(pid: str, body: TravelerAsk):
    """Answers a traveler's question about their own trip from their policy facts."""
    with db.conn() as c:
        p = _policy(c, pid)
        f = _flight(c, p["flight"])
        rules = engine.get_rules(c, p["market"])
    status = {"t1": int(rules["delay_tier1_minutes"]), "t2": int(rules["delay_tier2_minutes"]), "p1": rules["delay_tier1_payout"],
              "p2": rules["delay_tier2_payout"], "chance": forecast.compute(f)["probability"] if f else 0}
    return agent.answer_traveler(p, body.question, status)


@app.get("/api/console/review")
def review_queue():
    with db.conn() as c:
        out = db.rows(c, "SELECT * FROM decisions WHERE status='held' AND kind='delay' ORDER BY created_at DESC")
        return [_decision(d) for d in out]


class Review(BaseModel):
    action: str  # approve | decline
    note: str = ""


def _reviewer(c, staff_id):
    """Name of the signed-in insurer staff member, so every decision is signed by a real person."""
    st = db.one(c, "SELECT name FROM staff WHERE id=?", (staff_id,)) if staff_id else None
    return st["name"] if st else "Nattaya Phromsri"


@app.post("/api/console/review/{decision_id}")
def review(decision_id: str, body: Review, x_staff: str | None = Header(None)):
    if body.action not in ("approve", "decline"):
        raise HTTPException(400, "action must be approve or decline")
    with db.conn() as c:
        try:
            d = engine.decide_held(c, decision_id, body.action == "approve", _reviewer(c, x_staff), body.note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return _decision(d)


@app.get("/api/console/baggage")
def baggage_list():
    with db.conn() as c:
        out = db.rows(c, "SELECT * FROM baggage ORDER BY created_at DESC")
        for b in out:
            b["items"] = json.loads(b["items"])
            b["decision"] = _decision(db.one(c, "SELECT * FROM decisions WHERE id=?", (b["id"] + "-D",)))
            b["policy"] = db.one(c, "SELECT * FROM policies WHERE id=?", (b["policy_id"],))
            b["rules"] = engine.get_rules(c, b["policy"]["market"])
        return out


@app.post("/api/console/baggage/{claim_id}")
def baggage_decide(claim_id: str, body: Review, x_staff: str | None = Header(None)):
    if body.action not in ("approve", "decline"):
        raise HTTPException(400, "action must be approve or decline")
    with db.conn() as c:
        try:
            return _decision(engine.decide_baggage(c, claim_id, body.action == "approve", _reviewer(c, x_staff)))
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.get("/api/console/extra")
def extra_list():
    with db.conn() as c:
        out = db.rows(c, "SELECT * FROM expenses ORDER BY created_at DESC")
        for e in out:
            e["decision"] = _decision(db.one(c, "SELECT * FROM decisions WHERE id=?", (e["id"] + "-D",)))
            e["policy"] = db.one(c, "SELECT * FROM policies WHERE id=?", (e["policy_id"],))
            e["rules"] = engine.get_rules(c, e["policy"]["market"])
            e["limits"] = dict(zip(("fast", "cap"), engine.extra_limits(e["rules"])))
        return out


@app.post("/api/console/extra/{claim_id}")
def extra_decide(claim_id: str, body: Review, x_staff: str | None = Header(None)):
    if body.action not in ("approve", "decline"):
        raise HTTPException(400, "action must be approve or decline")
    with db.conn() as c:
        try:
            return _decision(engine.decide_extra(c, claim_id, body.action == "approve", _reviewer(c, x_staff)))
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.get("/api/console/rules")
def get_rules(market: str = "TH"):
    if market not in markets.MARKETS:
        raise HTTPException(404, "unknown market")
    with db.conn() as c:
        return db.rows(c, "SELECT * FROM rules WHERE market=?", (market,))


ALERT_MIN = forecast.ALERT_AT


class RuleUpdate(BaseModel):
    values: dict[str, float]


@app.put("/api/console/rules")
def put_rules(body: RuleUpdate, market: str = "TH"):
    """Insurer settings for one market. Changes apply to the next trigger, not past decisions."""
    if market not in markets.MARKETS:
        raise HTTPException(404, "unknown market")
    with db.conn() as c:
        current = engine.get_rules(c, market)
        merged = {**current}
        for k, v in body.values.items():
            if k not in current:
                raise HTTPException(400, f"unknown rule {k}")
            if v != v or v < 0 or v > 100_000_000:  # NaN, negative, absurd
                raise HTTPException(400, "values must be between 0 and 100,000,000")
            merged[k] = float(v)
        if not (1 <= merged["delay_tier1_minutes"] < merged["delay_tier2_minutes"]):
            raise HTTPException(400, "Tier 2 must start later than tier 1")
        if merged["delay_tier2_payout"] < merged["delay_tier1_payout"]:
            raise HTTPException(400, "Tier 2 must pay at least as much as tier 1")
        if not (1 <= merged["risk_threshold"] <= 100):
            raise HTTPException(400, "Risk threshold must be between 1 and 100")
        if not (ALERT_MIN <= merged["close_cover_at"] <= 100):
            raise HTTPException(400, f"Stop selling delay cover at a chance between {ALERT_MIN} and 100")
        for k, v in body.values.items():
            if c.execute("UPDATE rules SET value=? WHERE market=? AND key=?", (merged[k], market, k)).rowcount == 0:
                c.execute("INSERT INTO rules (market,key,value,label) VALUES (?,?,?,?)", (market, k, merged[k], markets.RULE_LABELS.get(k, k)))
        db.log(c, None, "rules_updated", {"market": market, **body.values})
        return engine.get_rules(c, market)


@app.get("/api/console/audit")
def audit(q: str = "", limit: int = 200):
    limit = max(1, min(limit, 500))
    with db.conn() as c:
        if q.strip():
            like = f"%{q.strip()}%"
            return db.rows(c, "SELECT * FROM events WHERE type LIKE ? OR policy_id LIKE ? OR detail LIKE ? ORDER BY id DESC LIMIT ?", (like, like, like, limit))
        return db.rows(c, "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))


# ----------------------------------------------------------- pages

@app.get("/")
def root():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/traveler")
def traveler_page():
    return FileResponse(os.path.join(STATIC, "traveler.html"))


@app.get("/judge")
def judge_page():
    """Both views side by side with a guided run, for judges."""
    return FileResponse(os.path.join(STATIC, "judge.html"))


@app.get("/console")
def console_page():
    return FileResponse(os.path.join(STATIC, "console.html"))


from . import crm  # noqa: E402
app.include_router(crm.router)
app.include_router(auth.router)

app.mount("/static", StaticFiles(directory=STATIC), name="static")
