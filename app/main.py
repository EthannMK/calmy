"""Calmy API and static site.

Run locally:  uvicorn app.main:app --reload
Pages:        /            landing with the two demo entry points
              /traveler    mobile traveler app
              /console     insurer console
"""
import json
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import db, engine

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
BASE = {"active": 1284, "paid_count": 37, "paid_amount": 55500, "held": 3}


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
            return engine.submit_bag_claim(c, pid, body.report_id, body.bag_tag, [i for i in body.items])
        except ValueError as e:
            raise HTTPException(400, str(e))


# ----------------------------------------------------------- demo controls

class Delay(BaseModel):
    flight: str = "OA 108"
    minutes: int = 192


@app.post("/api/demo/delay")
def demo_delay(body: Delay):
    """Simulate flight data: set a delay and run the trigger."""
    with db.conn() as c:
        if not _flight(c, body.flight):
            raise HTTPException(404, "flight not found")
        return {"results": engine.run_delay(c, body.flight, body.minutes)}


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
        held = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE status='held'")["n"]
        flights = db.rows(c, "SELECT * FROM flights ORDER BY code")
        decisions = db.rows(c, "SELECT id, policy_id, kind, status FROM decisions ORDER BY created_at DESC")
        return {
            "stats": {
                "active": BASE["active"] + active,
                "paid_count": BASE["paid_count"] + len(paid),
                "paid_amount": BASE["paid_amount"] + sum(p["amount"] for p in paid),
                "held": BASE["held"] + held,
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
        out = db.rows(c, "SELECT * FROM decisions WHERE status='held' ORDER BY created_at DESC")
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
        known = {r["key"] for r in db.rows(c, "SELECT key FROM rules")}
        for k, v in body.values.items():
            if k not in known:
                raise HTTPException(400, f"unknown rule {k}")
            if v < 0:
                raise HTTPException(400, "values cannot be negative")
            c.execute("UPDATE rules SET value=? WHERE key=?", (v, k))
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
