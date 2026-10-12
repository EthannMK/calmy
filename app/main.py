"""KODA API and static site.

Run locally:  uvicorn app.main:app --reload
Pages:        /            landing with the two demo entry points
              /traveler    mobile traveler app
              /console     insurer console
"""
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import secrets
import threading
from typing import Literal

import re
import time

from . import context, copilot, db, engine, forecast, live, markets, partners, rights, track

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
    """Fonts, styles and scripts rarely change. Let the browser keep them for a day instead of refetching."""
    resp = await call_next(request)
    if request.url.path.startswith("/static/") and resp.status_code == 200:
        resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp



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
        partners.sync(c, pid)
        if p["payout_account"].startswith(markets.POINTS_RAIL):
            pts = partners.balance(c, pid)
            pts["redemptions"] = db.rows(c, "SELECT r.*, t.name partner FROM redemptions r JOIN partners t ON t.id=r.partner_id "
                                            "WHERE r.policy_id=? ORDER BY r.created_at DESC", (pid,))
        return {
            "forecast": fc, "alert": al, "rights": rt, "points": pts,
            "weather": context.for_event(flight["code"], flight["origin"], bool(flight.get("cancelled")), flight["delay_minutes"]) if flight else None,
            "policy": p,
            "flight": flight,
            "market": markets.public(p["market"]),
            "rules": rules,
            "delay_decision": delay,
            "baggage": _bag(c, pid),
        }


class NewPolicy(BaseModel):
    name: str = Field(..., min_length=2, max_length=40)
    last4: str = Field(..., pattern=r"^\d{4}$")
    flight: str = Field(..., min_length=3, max_length=8)
    rail: str | None = None  # payout rail, must be one of the market's rails


@app.post("/api/policies")
def create_policy(body: NewPolicy):
    """Start a quote for a flight. A sample flight is used as is. Any other number is looked up live.

    The departure country sets the market: currency, payout rails, rules and legal notes.
    """
    code = re.sub(r"\s+", " ", body.flight.strip().upper())
    with db.conn() as c:
        sample = db.one(c, "SELECT * FROM flights WHERE source='sim' AND REPLACE(code,' ','')=?", (code.replace(" ", ""),))
    if sample:
        f = sample
    else:
        if not live.enabled():
            raise HTTPException(503, "Live flight data is not connected, so flights cannot be searched right now.")
        try:
            f = live.from_cache(code) or live.lookup(body.flight)  # a flight just shown in a list costs no new lookup
        except live.LiveError as e:
            raise HTTPException(502, str(e))
        if f["sched_ts"] and f["sched_ts"] < time.time():
            raise HTTPException(400, f"{f['code']} has already left. Pick a flight that has not departed yet.")
    context.warm(f["origin"])  # weather is fetched now, in the background, so the later event never waits on it
    market = f.get("market") or "GLOBAL"
    info = markets.public(market)
    rail = body.rail or info["rails"][0]
    if rail not in info["rails"]:
        raise HTTPException(400, f"Pick one of the payout options for {info['name']}: " + ", ".join(info["rails"]) + ".")
    with db.conn() as c:
        if not sample:
            live.upsert_flight(c, f)
        pid = engine._new_id("CL")
        pnr = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(6))  # booking reference
        c.execute(
            "INSERT INTO policies (id,traveler,flight,payout_account,status,market,currency,pnr) VALUES (?,?,?,?,'quote',?,?,?)",
            (pid, body.name.strip(), f["code"], (f"{rail} wallet" if rail == markets.POINTS_RAIL else f"{rail} ending {body.last4}"), market, info["currency"], pnr),
        )
        db.log(c, pid, "quote_created", {"flight": f["code"], "market": market})
    return {"policy": pid, "flight": f, "market": info}


@app.get("/api/demo/sources")
def demo_sources():
    """Where each kind of data comes from right now. Shown only in the presenter panel."""
    u = live.usage()
    context.warm("BKK")  # so the weather status is real the first time the panel opens
    ai_on = bool(os.environ.get("GOOGLE_CLOUD_PROJECT"))
    flights = {"mode": "error" if u["last_error"] else "live" if u["enabled"] else "off",
               "detail": u["last_error"] or (f"{u['used']} of {u['budget']} lookups used this month" if u["enabled"]
                                               else "No flight data key on the server. Travelers cannot search flights until one is set.")}
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
        raise HTTPException(502, str(e))


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
        _policy(c, pid)
        try:
            claim = engine.submit_bag_claim(
                c, pid, body.report_id.strip(), body.bag_tag.strip(), [i.model_dump() for i in body.items]
            )
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))
    _enrich_later([{"decision": claim["id"] + "-D"}])
    return claim


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
    return live.usage()


@app.get("/api/live/lookup")
def live_lookup(flight: str = Query(..., min_length=3, max_length=8)):
    try:
        return live.lookup(flight)
    except live.LiveError as e:
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
        flights = db.rows(
            c,
            "SELECT f.*, (SELECT COUNT(*) FROM policies p WHERE p.flight=f.code AND p.status='active') AS covered "
            "FROM flights f ORDER BY f.sched_ts IS NULL, f.sched_ts, f.code",
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
        flights = db.rows(c, "SELECT * FROM flights ORDER BY sched_ts IS NULL, sched_ts, code")
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
                        "stress": int(f.get("stress") or 0)})
        premium = sum(markets.to_usd(r["premium"], r["currency"]) for r in db.rows(c, "SELECT premium, currency FROM policies WHERE status='active'"))
    reserve = total + 1.28 * (var ** 0.5)
    return {"expected_usd": round(total, 2), "reserve_usd": round(reserve), "premium_usd": round(premium),
            "flights": sorted(out, key=lambda x: -x["probability"]), "method": "Transparent score: weather, traffic, time of day and current delay. Not a trained model."}


def _copilot_ctx(c, decision_id):
    d = _decision(db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,)))
    if not d:
        raise HTTPException(404, "claim not found")
    p = _policy(c, d["policy_id"])
    others = db.one(c, "SELECT COUNT(*) n FROM policies WHERE status IN ('active','archived') AND payout_account=? AND LOWER(TRIM(traveler))<>?",
                    (p["payout_account"], (p["traveler"] or "").strip().lower()))["n"]
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


@app.get("/api/console/review")
def review_queue():
    with db.conn() as c:
        out = db.rows(c, "SELECT * FROM decisions WHERE status='held' AND kind='delay' ORDER BY created_at DESC")
        return [_decision(d) for d in out]


class Review(BaseModel):
    action: str  # approve | decline
    note: str = ""


@app.post("/api/console/review/{decision_id}")
def review(decision_id: str, body: Review):
    if body.action not in ("approve", "decline"):
        raise HTTPException(400, "action must be approve or decline")
    with db.conn() as c:
        try:
            d = engine.decide_held(c, decision_id, body.action == "approve", "Nok P.", body.note)
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
def baggage_decide(claim_id: str, body: Review):
    if body.action not in ("approve", "decline"):
        raise HTTPException(400, "action must be approve or decline")
    with db.conn() as c:
        try:
            return _decision(engine.decide_baggage(c, claim_id, body.action == "approve", "Nok P."))
        except ValueError as e:
            raise HTTPException(400, str(e))


@app.get("/api/console/rules")
def get_rules(market: str = "TH"):
    if market not in markets.MARKETS:
        raise HTTPException(404, "unknown market")
    with db.conn() as c:
        return db.rows(c, "SELECT * FROM rules WHERE market=?", (market,))


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
        for k, v in body.values.items():
            c.execute("UPDATE rules SET value=? WHERE market=? AND key=?", (merged[k], market, k))
        db.log(c, None, "rules_updated", {"market": market, **body.values})
        return engine.get_rules(c, market)


@app.get("/api/console/audit")
def audit():
    with db.conn() as c:
        return db.rows(c, "SELECT * FROM events ORDER BY id DESC LIMIT 50")


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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
