"""Claims investigation agent.

When a claim is held for a person, the agent investigates it with a fixed set of read-only tools, then writes a short
case summary for the reviewer. It never pays and never declines: the rules pay, and only a person declines.

The tools are ordinary functions over our own data: the claim, the flight status, the delay forecast, weather and
airspace alerts, the policy terms, and the traveler's history. Each tool call is recorded, so the reviewer sees what the
agent looked at and what it found.

The tools run in a fixed order, so every investigation looks at the same things. The case summary is built from what the
tools return. When Gemini is configured (GOOGLE_CLOUD_PROJECT) it rewrites the summary from the same findings, and if it
fails or times out the built-in summary is used, so the reviewer always gets an investigation. Letting Gemini choose
which tools to call is a possible next step. It was left out because a fixed order is easier to audit.
"""
import os

from . import context, copilot, db, engine, forecast, markets

MAX_STEPS = 8


def _claim(did: str) -> dict:
    """The held claim: delay length, amount, risk score and the signals behind it."""
    with db.conn() as c:
        d = db.one(c, "SELECT * FROM decisions WHERE id=?", (did,))
        if not d:
            return {"error": "claim not found"}
        import json
        bd = json.loads(d["breakdown"]) if d.get("breakdown") else []
        return {"claim": d["id"], "policy": d["policy_id"], "delay_minutes": d["delay_minutes"], "amount": d["amount"],
                "risk_score": d["risk_score"], "signals": [f"{t} +{p}" for t, p in bd if p and not t.startswith("Base")]}


def _policy_of(did: str):
    with db.conn() as c:
        d = db.one(c, "SELECT policy_id FROM decisions WHERE id=?", (did,))
        return db.one(c, "SELECT * FROM policies WHERE id=?", (d["policy_id"],)) if d else None


def _flight(code: str) -> dict:
    with db.conn() as c:
        f = db.one(c, "SELECT * FROM flights WHERE code=?", (code,))
    if not f:
        return {"error": "flight not found"}
    return {"flight": code, "route": f"{f['origin']} to {f['dest']}", "delay_minutes": f["delay_minutes"], "cancelled": bool(f.get("cancelled")),
            "source": "live status" if f["source"] == "live" else "demo status"}


def _forecast(code: str) -> dict:
    with db.conn() as c:
        f = db.one(c, "SELECT * FROM flights WHERE code=?", (code,))
    if not f:
        return {"error": "flight not found"}
    fc = forecast.compute(f)
    return {"delay_chance_percent": fc["probability"], "source": fc["source"],
            "top_drivers": [f"{t} (+{p})" for t, p in sorted(fc["drivers"], key=lambda x: -x[1])[:3] if p > 0]}


def _conditions(airport: str) -> dict:
    live = context.cached(airport)
    al = forecast.airspace(airport)
    return {"airport": airport, "weather": (f"{live['weather']}, wind {live['wind_kmh']} km/h" if live else "no live reading, so none is assumed"),
            "airspace_alerts": [a["label"] for a in al] or "none"}


def _terms(market: str) -> dict:
    with db.conn() as c:
        r = engine.get_rules(c, market or "TH")
    return {"pays_from_minutes": [int(r["delay_tier1_minutes"]), int(r["delay_tier2_minutes"])],
            "payouts": [r["delay_tier1_payout"], r["delay_tier2_payout"]], "auto_pay_below_risk": r["risk_threshold"]}


def _history(customer_id: str, account: str) -> dict:
    with db.conn() as c:
        paid = db.one(c, "SELECT COUNT(*) n FROM decisions d JOIN policies p ON p.id=d.policy_id WHERE d.status='paid' AND p.customer_id=?", (customer_id,))["n"] if customer_id else 0
        trips = db.one(c, "SELECT COUNT(*) n FROM policies WHERE customer_id=? AND status IN ('active','archived')", (customer_id,))["n"] if customer_id else 0
        shared = db.one(c, "SELECT COUNT(DISTINCT COALESCE(customer_id,traveler)) n FROM policies WHERE payout_account=? AND status IN ('active','archived')", (account,))["n"] if account else 0
    points = (account or "").startswith(markets.POINTS_RAIL)
    return {"trips_with_cover": trips, "claims_paid_before": paid,
            "payout_account": "own points wallet" if points else f"saved account used by {shared} profile(s)"}


def investigate(did: str) -> dict:
    """Run the investigation. Returns the steps taken, a summary and the suggested next step."""
    p = _policy_of(did)
    if not p:
        return {"error": "claim not found"}
    steps = []

    def run(tool, label, fn, *args):
        out = fn(*args)
        steps.append({"tool": tool, "label": label, "result": out})
        return out

    cl = run("claim", "Read the held claim", _claim, did)
    fl = run("flight_status", f"Checked the status of {p['flight']}", _flight, p["flight"])
    fc = run("delay_forecast", "Looked at the delay chance and its drivers", _forecast, p["flight"])
    origin = fl.get("route", "BKK to BKK").split(" to ")[0]
    cond = run("conditions", f"Checked weather and airspace alerts at {origin}", _conditions, origin)
    terms = run("policy_terms", "Read the policy terms for this market", _terms, p.get("market"))
    hist = run("traveler_history", "Looked at the traveler's history", _history, p.get("customer_id"), p.get("payout_account"))

    with db.conn() as c:
        d = _decision_row(c, did)
        rec = copilot.recommend(d, p, p["traveler"], p["currency"], 0 if hist["payout_account"] == "own points wallet" else max(0, int(hist["payout_account"].split("by ")[-1].split(" ")[0]) - 1))
    confirmed = fl.get("delay_minutes", 0) >= terms["pays_from_minutes"][0] or fl.get("cancelled")
    summary = (f"The flight data {'confirms' if confirmed else 'does not confirm'} a delay that meets the policy ({cl.get('delay_minutes')} minutes against "
               f"{terms['pays_from_minutes'][0]}). The claim was held because of: {', '.join(cl.get('signals') or ['no single strong signal'])}. "
               f"Before the flight the delay chance was about {fc.get('delay_chance_percent', '?')}%. The traveler has {hist['trips_with_cover']} covered trips and "
               f"{hist['claims_paid_before']} earlier paid claims, and the payout goes to their {hist['payout_account']}. Suggested next step: {rec['label']}.")
    out = {"steps": steps, "summary": summary, "recommendation": {"action": rec["action"], "label": rec["label"], "confidence": rec["confidence"]},
           "ai": False, "note": "The agent advises. Only a person can decline a claim."}
    better = _gemini_summary(out)
    if better:
        out["summary"], out["ai"] = better, True
    return out


def _decision_row(c, did):
    import json
    d = db.one(c, "SELECT * FROM decisions WHERE id=?", (did,))
    d["breakdown"] = json.loads(d["breakdown"]) if d.get("breakdown") else []
    return d


def _gemini_summary(out: dict) -> str | None:
    """Gemini writes the case summary from what the tools returned. Returns None when not configured or on any failure."""
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return None
    facts = "\n".join(f"- {s['label']}: {s['result']}" for s in out["steps"])
    return copilot.ask_gemini(
        "You are a claims investigator for a travel insurer. Write a case summary of at most 80 words for a human reviewer. "
        "Use only these findings. Say what supports paying and what raises doubt. Never recommend declining; a person decides.\n" + facts)


def answer_traveler(p: dict, question: str, status: dict) -> dict:
    """Plain answer about the traveler's own trip, built from their policy facts. Gemini rewrites it when configured."""
    q = question.lower()
    covers = [n for n, on in (("delay", p.get("delay_cover")), ("lost bag", p.get("baggage_cover"))) if on]
    base = f"Your cover on {p['flight']}: {', '.join(covers) if covers else 'none yet'}."
    if any(w in q for w in ("paid", "payout", "money", "points", "when")):
        base += (f" A delay of {status['t1']} minutes or more pays {status['p1']} and {status['t2']} minutes or more pays {status['p2']}, "
                 "paid automatically. You do not need to submit a claim.")
    elif any(w in q for w in ("delay", "late", "risk", "chance")):
        base += f" The flight's delay chance is about {status['chance']}%. You get an alert if it passes {forecast.ALERT_AT}%."
    elif any(w in q for w in ("bag", "lost", "luggage")):
        base += " If your bag is lost, report it to the airline, then use My bag is lost in the Cover tab."
    else:
        base += " Ask me about payouts, delays or bags."
    better = copilot.ask_gemini("Rewrite this for a traveler in friendly, plain English under 50 words. Keep every fact. Return only the text.\n" + base)
    return {"answer": better or base, "ai": bool(better)}
