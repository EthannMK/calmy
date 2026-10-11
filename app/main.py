"""Calmy API and static site.

Run locally:  uvicorn app.main:app --reload
Pages:        /            landing with the two demo entry points
              /traveler    mobile traveler app
              /console     insurer console
"""
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import threading

from . import db, engine, live

STATIC = os.path.join(os.path.dirname(__file__), "..", "static")
@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(
    title="Calmy",
    description="Smart flight cover. Demo data, flight data simulated.",
    lifespan=lifespan,
)

# Baseline numbers that make the console look like a live book of business.
# They are labelled as demo data in the footer of every screen.
BASE = {"active": 1284, "paid_count": 37, "paid_amount": 55500}


# ----------------------------------------------------------- helpers

def _decision(d):
    if not d:
        return None
    for k in ("reasons", "timeline"):
        d[k] = json.loads(d[k]) if d.get(k) else []
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


@app.get("/api/traveler/{pid}")
def traveler_state(pid: str):
    with db.conn() as c:
        p = _policy(c, pid)
        rules = engine.get_rules(c)
        delay = _decision(db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (pid,)))
        return {
            "policy": p,
            "flight": _flight(c, p["flight"]),
            "rules": rules,
            "delay_decision": delay,
            "baggage": _bag(c, pid),
        }


@app.post("/api/traveler/{pid}/checkout")
def checkout(pid: str, body: Checkout):
    if not (body.delay or body.baggage):
        raise HTTPException(400, "choose at least one cover")
    with db.conn() as c:
        p = _policy(c, pid)
        f = _flight(c, p["flight"])
        if f and f["delay_minutes"] > 0 and p["status"] != "active":
            # Cover bought after a delay is known would be insuring a loss that already happened.
            raise HTTPException(400, "This flight is already delayed, so cover cannot be added any more. Reset the demo to start again.")
        rules = engine.get_rules(c)
        total = int(rules["delay_premium"]) * body.delay + int(rules["baggage_premium"]) * body.baggage
        c.execute(
            "UPDATE policies SET delay_cover=?, baggage_cover=?, premium=?, status='active' WHERE id=?",
            (int(body.delay), int(body.baggage), total, pid),
        )
        db.log(c, pid, "policy_active", {"delay": body.delay, "baggage": body.baggage, "total": total})
        return {"policy": pid, "total": total}


class BagClaim(BaseModel):
    report_id: str = "PIR-BKK-90412"
    bag_tag: str = "OA 482103"
    items: list[dict] = [
        {"name": "Hard-shell suitcase", "amount": 600},
        {"name": "Essential clothing (2 sets)", "amount": 350},
        {"name": "Toiletries kit", "amount": 150},
        {"name": "Travel chargers and accessories", "amount": 150},
    ]


@app.post("/api/traveler/{pid}/baggage")
def bag_claim(pid: str, body: BagClaim = BagClaim()):
    with db.conn() as c:
        _policy(c, pid)
        try:
            claim = engine.submit_bag_claim(c, pid, body.report_id, body.bag_tag, body.items)
        except (ValueError, KeyError, TypeError) as e:
            raise HTTPException(400, str(e))
    _enrich_later([{"decision": claim["id"] + "-D"}])
    return claim


# ----------------------------------------------------------- demo controls

class Delay(BaseModel):
    flight: str = "OA 108"
    minutes: int = Field(192, ge=0, le=2880)


def _enrich_later(results):
    ids = [r["decision"] for r in results if r.get("decision")]
    if ids:
        threading.Thread(target=engine.enrich_explanations, args=(ids,), daemon=True).start()


@app.post("/api/demo/delay")
def demo_delay(body: Delay):
    """Simulate flight data: set a delay and run the trigger."""
    with db.conn() as c:
        if not _flight(c, body.flight):
            raise HTTPException(404, "flight not found")
        covered = db.one(
            c, "SELECT COUNT(*) n FROM policies WHERE flight=? AND status='active' AND delay_cover=1", (body.flight,)
        )["n"]
        results = engine.run_delay(c, body.flight, body.minutes)
    _enrich_later(results)
    return {"results": results, "covered": covered}


class LiveReplay(BaseModel):
    policy: str
    flight: str  # IATA number such as TG102


@app.get("/api/live/status")
def live_status():
    with db.conn() as c:
        return live.usage(c)


@app.get("/api/live/lookup")
def live_lookup(flight: str = Query(..., min_length=3, max_length=8)):
    with db.conn() as c:
        try:
            return live.lookup(c, flight)
        except live.LiveError as e:
            raise HTTPException(502, str(e))


@app.get("/api/live/delayed")
def live_delayed(min_minutes: int = Query(30, ge=0, le=1440)):
    """Flights from Thai hubs that are really delayed right now."""
    with db.conn() as c:
        try:
            return live.delayed_now(c, min_minutes)
        except live.LiveError as e:
            raise HTTPException(502, str(e))


@app.post("/api/demo/live")
def demo_live(body: LiveReplay):
    """Replay a real flight's real delay against a policy.

    This is a replay: the cover is treated as bought before departure even
    though the delay is already known. The response says so, and the screens
    label the flight as live data.
    """
    with db.conn() as c:
        p = _policy(c, body.policy)
        if p["status"] != "active" or not p["delay_cover"]:
            raise HTTPException(400, "Add delay cover first, then replay a real flight.")
        try:
            f = live.lookup(c, body.flight)
        except live.LiveError as e:
            raise HTTPException(502, str(e))
        live.upsert_flight(c, f)
        c.execute("UPDATE policies SET flight=? WHERE id=?", (f["code"], p["id"]))
        c.execute("DELETE FROM decisions WHERE policy_id=? AND kind='delay'", (p["id"],))  # fresh replay
        results = engine.run_delay(c, f["code"], f["delay_minutes"])
        db.log(c, p["id"], "live_replay", {"flight": f["code"], "delay": f["delay_minutes"]})
    _enrich_later(results)
    return {"flight": f, "results": results, "paid": bool(results)}


@app.post("/api/demo/reset")
def demo_reset():
    db.reset()
    return {"ok": True}


# ----------------------------------------------------------- console

@app.get("/api/console/overview")
def overview():
    with db.conn() as c:
        rules = engine.get_rules(c)
        active = db.one(c, "SELECT COUNT(*) n FROM policies WHERE status='active'")["n"]
        paid = db.rows(c, "SELECT amount FROM decisions WHERE status='paid'")
        held = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE status='held' AND kind='delay'")["n"]
        bag_waiting = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE status IN ('held','pending') AND kind='baggage'")["n"]
        flights = db.rows(c, "SELECT * FROM flights ORDER BY code")
        for f in flights:
            if f["source"] == "live":  # a replayed real flight only carries the demo policies
                f["travelers"] = db.one(c, "SELECT COUNT(*) n FROM policies WHERE flight=? AND status='active'", (f["code"],))["n"]
        decisions = db.rows(
            c, "SELECT id, policy_id, kind, status, amount, decided_by FROM decisions ORDER BY created_at DESC"
        )
        return {
            "stats": {
                "active": BASE["active"] + active,
                "paid_count": BASE["paid_count"] + len(paid),
                "paid_amount": BASE["paid_amount"] + sum(p["amount"] for p in paid),
                "held": held,
                "bag_waiting": bag_waiting,
                "avg_seconds": 8,
            },
            "flights": flights,
            "rules": rules,
            "decisions": decisions,
        }


@app.get("/api/console/decision/{policy_id}")
def console_decision(policy_id: str):
    with db.conn() as c:
        p = _policy(c, policy_id)
        d = _decision(db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (policy_id,)))
        return {"policy": p, "flight": _flight(c, p["flight"]), "decision": d, "rules": engine.get_rules(c)}


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
def get_rules():
    with db.conn() as c:
        return db.rows(c, "SELECT * FROM rules")


class RuleUpdate(BaseModel):
    values: dict[str, float]


@app.put("/api/console/rules")
def put_rules(body: RuleUpdate):
    """Insurer settings. Changes apply to the next trigger, not past decisions."""
    with db.conn() as c:
        current = engine.get_rules(c)
        merged = {**current}
        for k, v in body.values.items():
            if k not in current:
                raise HTTPException(400, f"unknown rule {k}")
            if v != v or v < 0 or v > 1_000_000:  # NaN, negative, absurd
                raise HTTPException(400, "values must be between 0 and 1,000,000")
            merged[k] = float(v)
        if not (1 <= merged["delay_tier1_minutes"] < merged["delay_tier2_minutes"]):
            raise HTTPException(400, "Tier 2 must start later than tier 1")
        if merged["delay_tier2_payout"] < merged["delay_tier1_payout"]:
            raise HTTPException(400, "Tier 2 must pay at least as much as tier 1")
        if not (1 <= merged["risk_threshold"] <= 100):
            raise HTTPException(400, "Risk threshold must be between 1 and 100")
        for k, v in body.values.items():
            c.execute("UPDATE rules SET value=? WHERE key=?", (merged[k], k))
        db.log(c, None, "rules_updated", body.values)
        return engine.get_rules(c)


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


@app.get("/console")
def console_page():
    return FileResponse(os.path.join(STATIC, "console.html"))


app.mount("/static", StaticFiles(directory=STATIC), name="static")
