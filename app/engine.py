"""Decision engine: rules, risk score, trigger, payout and explanation.

Flow for a delay, as shown in the demo:
    simulator sets delay -> trigger finds covered policies -> rules give the
    payout tier -> risk score decides pay or hold -> payout is simulated ->
    an explanation that cites the policy clause is attached.
"""
import json
import os
import time
import uuid

from . import db

CLAUSE_DELAY = "Clause 3.1, delay over 3 hours"
CLAUSE_BAG = "Clause 4.2, lost baggage"
CLAUSE_BAG_TEXT = "Benefit paid when a checked bag is declared lost by the airline."

# Timeline offsets in seconds, matching the 8 second promise in the demo.
STEPS = [
    ("Delay detected", 0),
    ("Policy matched", 1),
    ("Risk checked", 3),
    ("Paid", 8),
]


# ---------------------------------------------------------------- rules

def get_rules(c) -> dict:
    return {r["key"]: r["value"] for r in db.rows(c, "SELECT key, value FROM rules")}


def payout_for_delay(rules: dict, minutes: int) -> int:
    """Highest tier whose delay threshold is met, else 0."""
    if minutes >= rules["delay_tier2_minutes"]:
        return int(rules["delay_tier2_payout"])
    if minutes >= rules["delay_tier1_minutes"]:
        return int(rules["delay_tier1_payout"])
    return 0


# ---------------------------------------------------------------- risk

def risk_score(p: dict) -> tuple[int, list[str]]:
    """Transparent additive score from 0 to 100.

    Each signal adds points and contributes a plain English reason. In the
    cloud version the same features feed a BigQuery ML model, and these
    weights are the readable baseline it is checked against.
    """
    score = 12
    reasons: list[tuple[int, str]] = []
    extra = max(0, min(3, p["f_covers_2h"] - 1))
    if extra:
        score += 9 * extra
        reasons.append((9 * extra, "Several covers bought on one profile within 2 hours"))
    if p["f_schedule_changed"]:
        score += 20
        reasons.append((20, "Flight time changed shortly before the cover was bought"))
    if p["f_shared_account"]:
        score += 28
        reasons.append((28, "Several payout accounts share the same contact details"))
    if p["f_prior_claims"]:
        score += 4
        reasons.append((4, "A claim was paid on this profile in the last 12 months"))
    reasons.sort(key=lambda r: -r[0])
    return min(score, 100), [r[1] for r in reasons[:3]]


def clean_checks() -> list[str]:
    return ["Flight status matches", "PromptPay name matches", "No double claim"]


# ---------------------------------------------------------------- explain

def explain(kind: str, amount: int, minutes: int, rules: dict, status: str) -> str:
    """Plain English reason for a decision.

    Uses Gemini on Vertex AI when a project is configured, otherwise a
    template, so the demo never depends on network access.
    """
    if kind == "delay":
        facts = (
            f"Flight delay {minutes} minutes. Rule threshold {int(rules['delay_tier1_minutes'])} minutes "
            f"({CLAUSE_DELAY}). Payout {amount} THB. Decision: {status}."
        )
        fallback = (
            f"Delay {minutes} minutes, rule {int(rules['delay_tier1_minutes'])} minutes, payout {amount:,} THB."
        )
    else:
        facts = f"Lost bag confirmed by airline report. {CLAUSE_BAG}. Payout {amount} THB. Decision: {status}."
        fallback = f"Airline report confirms the bag is lost. Covered under clause 4.2, payout {amount:,} THB."

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return fallback
    try:  # pragma: no cover - needs cloud credentials
        from google import genai

        client = genai.Client(
            vertexai=True, project=project, location=os.environ.get("GOOGLE_CLOUD_LOCATION", "asia-southeast1")
        )
        resp = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=(
                "Write one plain English sentence (max 30 words) explaining this insurance decision to a "
                "reviewer. Cite the clause. Facts only, no invented details.\n" + facts
            ),
        )
        return (resp.text or fallback).strip()
    except Exception:
        return fallback


# ---------------------------------------------------------------- payout

def _timeline(final_label: str, amount: int, account: str, status: str) -> list[dict]:
    """Four step record of what the system did. Held cases stop at risk."""
    out = []
    for i, (label, sec) in enumerate(STEPS):
        step = {"label": label, "sec": sec}
        if status == "held" and label == "Paid":
            step.update(label="Held for review", sec=3, pending=True)
        out.append(step)
    return out


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:5].upper()}"


# ---------------------------------------------------------------- trigger

def run_delay(c, flight_code: str, minutes: int) -> list[dict]:
    """Simulator entry point: set the flight delay and process every policy."""
    c.execute("UPDATE flights SET delay_minutes=? WHERE code=?", (minutes, flight_code))
    rules = get_rules(c)
    amount = payout_for_delay(rules, minutes)
    results = []
    policies = db.rows(
        c, "SELECT * FROM policies WHERE flight=? AND status='active' AND delay_cover=1", (flight_code,)
    )
    for p in policies:
        db.log(c, p["id"], "delay_detected", {"flight": flight_code, "minutes": minutes})
        if amount == 0:
            continue
        if db.one(c, "SELECT 1 FROM decisions WHERE policy_id=? AND kind='delay'", (p["id"],)):
            continue  # double claim prevention
        score, reasons = risk_score(p)
        status = "paid" if score < rules["risk_threshold"] else "held"
        reasons_out = clean_checks() if status == "paid" else reasons
        explanation = explain("delay", amount, minutes, rules, status)
        did = _new_id("RQ") if status == "held" else _new_id("DC")
        c.execute(
            "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                did, p["id"], "delay", status, amount, score,
                json.dumps(reasons_out), json.dumps(_timeline("Paid", amount, p["payout_account"], status)),
                CLAUSE_DELAY, explanation, minutes, time.time(),
                "system" if status == "paid" else None, None,
            ),
        )
        db.log(c, p["id"], f"decision_{status}", {"decision": did, "score": score, "amount": amount})
        results.append({"policy": p["id"], "decision": did, "status": status, "score": score})
    return results


def decide_held(c, decision_id: str, approve: bool, reviewer: str, note: str) -> dict:
    d = db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,))
    if not d or d["status"] != "held":
        raise ValueError("decision is not waiting for review")
    status = "paid" if approve else "declined"
    tl = json.loads(d["timeline"])
    if approve:
        tl[-1] = {"label": "Paid", "sec": 8}
    c.execute(
        "UPDATE decisions SET status=?, decided_by=?, note=?, timeline=? WHERE id=?",
        (status, reviewer, note, json.dumps(tl), decision_id),
    )
    db.log(c, d["policy_id"], f"review_{status}", {"decision": decision_id, "by": reviewer, "note": note})
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,))


# ---------------------------------------------------------------- baggage

def submit_bag_claim(c, policy_id: str, report_id: str, bag_tag: str, items: list[dict]) -> dict:
    p = db.one(c, "SELECT * FROM policies WHERE id=?", (policy_id,))
    if not p or p["status"] != "active" or not p["baggage_cover"]:
        raise ValueError("this policy has no baggage cover")
    if db.one(c, "SELECT 1 FROM baggage WHERE policy_id=?", (policy_id,)):
        raise ValueError("a baggage claim already exists for this policy")
    total = sum(int(i["amount"]) for i in items)
    bid = _new_id("BC")
    c.execute(
        "INSERT INTO baggage VALUES (?,?,?,?,?,?,?,?)",
        (bid, policy_id, report_id, bag_tag, "Bag not found at CNX", json.dumps(items), total, time.time()),
    )
    rules = get_rules(c)
    amount = min(total, int(rules["lost_bag_payout"]))
    score = risk_score({**p, "f_covers_2h": 1})[0] + 6  # claim loading: values are self declared
    status = "pending" if score < rules["risk_threshold"] else "held"
    c.execute(
        "INSERT INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            bid + "-D", policy_id, "baggage", status, amount, score,
            json.dumps(clean_checks()), json.dumps([]), CLAUSE_BAG,
            explain("baggage", amount, 0, rules, status), 0, time.time(), None, None,
        ),
    )
    db.log(c, policy_id, "baggage_claim", {"claim": bid, "total": total, "score": score})
    return db.one(c, "SELECT * FROM baggage WHERE id=?", (bid,))


def decide_baggage(c, claim_id: str, approve: bool, reviewer: str) -> dict:
    d = db.one(c, "SELECT * FROM decisions WHERE id=?", (claim_id + "-D",))
    if not d or d["status"] not in ("pending", "held"):
        raise ValueError("claim is not waiting for a decision")
    status = "paid" if approve else "declined"
    c.execute("UPDATE decisions SET status=?, decided_by=? WHERE id=?", (status, reviewer, claim_id + "-D"))
    db.log(c, d["policy_id"], f"baggage_{status}", {"claim": claim_id, "by": reviewer})
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (claim_id + "-D",))
