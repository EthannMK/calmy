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

from . import wallet, context, db, markets, partners

CLAUSE_DELAY = "Clause 3.1, delay over 3 hours"
CLAUSE_CANCEL = "Clause 3.2, flight cancelled"
CLAUSE_BAG = "Clause 4.2, lost baggage"
CLAUSE_EXTRA = "Clause 3.4, extra delay costs"
CLAUSE_BAG_TEXT = "Benefit paid when a checked bag is declared lost by the airline."

# Timeline offsets in seconds, matching the 8 second promise in the demo.
STEPS = [
    ("Event detected", 0),
    ("Policy matched", 1),
    ("Risk checked", 3),
    ("Paid", 8),
    ("Traveler notified", 9),
]


# ---------------------------------------------------------------- rules

def get_rules(c, market: str = "TH") -> dict:
    """Insurer rules for one market. Money values are in that market's currency."""
    r = {x["key"]: x["value"] for x in db.rows(c, "SELECT key, value FROM rules WHERE market=?", (market,))}
    r.setdefault("cancel_payout", r.get("delay_tier2_payout", 0))
    r.setdefault("close_cover_at", 80)  # databases made before this rule existed
    return r


def currency_of(market: str) -> str:
    return markets.MARKETS.get(market, markets.MARKETS["GLOBAL"])["currency"]


SYMBOL = {"SGD": "S$", "USD": "US$", "GBP": "£", "EUR": "€", "THB": "THB ", "JPY": "¥", "AUD": "A$", "INR": "₹", "CAD": "C$", "BRL": "R$"}


def money(amount, currency: str) -> str:
    """One way of writing an amount, used in messages, explanations and the apps."""
    sym = SYMBOL.get(currency)
    return f"{sym}{int(amount):,}" if sym else f"{int(amount):,} {currency}"


def entitlement(rules: dict, minutes: int | None, cancelled: bool = False) -> dict:
    """The one payout calculation for delay cover. Automatic decisions, late reports, re-checks and reviewer approvals
    all use it, so a person can never pay a different amount from the published rules.

    Delay cover is one benefit with one ceiling (Calmy's wording: S$45 from 3 hours, S$90 from 6 hours, S$90 if
    cancelled, and no second claim of the same type on the same flight). So this returns the *total* the disruption
    is worth; whatever was already paid is subtracted by the caller, never added on top."""
    t1, t2 = int(rules["delay_tier1_minutes"]), int(rules["delay_tier2_minutes"])
    if cancelled:
        return {"tier": "cancelled", "amount": int(rules["cancel_payout"]), "rule": "Flight cancelled", "threshold": None}
    m = int(minutes or 0)
    if m >= t2:
        return {"tier": "tier 2", "amount": int(rules["delay_tier2_payout"]), "rule": f"{_hm(t2)} or more", "threshold": t2}
    if m >= t1:
        return {"tier": "tier 1", "amount": int(rules["delay_tier1_payout"]), "rule": f"{_hm(t1)} or more", "threshold": t1}
    return {"tier": None, "amount": 0, "rule": f"under {_hm(t1)}", "threshold": t1}


def payout_for_delay(rules: dict, minutes: int) -> int:
    """Highest tier whose delay threshold is met, else 0."""
    return entitlement(rules, minutes)["amount"]


# ---------------------------------------------------------------- risk

AI_LAST = {"ok": None, "error": None}  # latest Gemini outcome, shown in the demo panel

DAY = 86400


def features(c, p: dict) -> dict:
    """Risk inputs for one policy, computed from what the system has actually seen.

    Seeded sample profiles carry preset signals. For every policy the live value is
    also computed from the data, and the higher of the two is used.

    A points wallet belongs to one traveler, so it says nothing about shared accounts. For those policies the
    checks group by traveler account. A real card or bank account that several travelers use is a signal.
    """
    acct, name = p["payout_account"], (p["traveler"] or "").strip().lower()
    cid = p.get("customer_id")
    t0 = p.get("created_at") or time.time()
    wallet_only = (acct or "").startswith(markets.POINTS_RAIL)
    same_person = db.rows(
        c,
        "SELECT id, pnr FROM policies WHERE status='active' AND ABS(COALESCE(created_at, ?) - ?) <= 7200 "
        "AND ((payout_account=? AND payout_account NOT LIKE ?) OR LOWER(TRIM(traveler))=? OR (customer_id IS NOT NULL AND customer_id=?))",
        (t0, t0, acct, markets.POINTS_RAIL + "%", name, cid),
    )
    others_on_account = 0 if wallet_only else db.one(
        c,
        "SELECT COUNT(*) n FROM policies WHERE status IN ('active','archived') AND payout_account=? AND LOWER(TRIM(traveler))<>?",
        (acct, name),
    )["n"]
    prior_paid = db.one(
        c,
        "SELECT COUNT(*) n FROM decisions d JOIN policies q ON q.id=d.policy_id "
        "WHERE d.status='paid' AND q.id<>? AND d.created_at > ? AND ((q.payout_account=? AND q.payout_account NOT LIKE ?) OR (q.customer_id IS NOT NULL AND q.customer_id=?))",
        (p["id"], time.time() - 365 * DAY, acct, markets.POINTS_RAIL + "%", cid),
    )["n"]
    f = db.one(c, "SELECT scheduled FROM flights WHERE code=?", (p["flight"],)) or {}
    changed = bool(p.get("sched_at_purchase") and f.get("scheduled") and p["sched_at_purchase"] != f["scheduled"])
    return {
        **p,
        # The two legs of one booking are one purchase, so they count once.
        "f_covers_2h": max(p["f_covers_2h"], len({r["pnr"] or r["id"] for r in same_person})),
        "f_shared_account": int(bool(p["f_shared_account"]) or others_on_account > 0),
        "f_prior_claims": int(bool(p["f_prior_claims"]) or prior_paid > 0),
        "f_schedule_changed": int(bool(p["f_schedule_changed"]) or changed),
    }


def risk_parts(p: dict) -> list[tuple[str, int]]:
    """Every contribution to the score as (plain reason, points), starting with the base."""
    parts = [("Base score for any traveler", 12)]
    extra = max(0, min(3, p["f_covers_2h"] - 1))
    if extra:
        parts.append(("Several covers bought on one profile within 2 hours", 9 * extra))
    if p["f_schedule_changed"]:
        parts.append(("Flight time changed shortly before the cover was bought", 20))
    if p["f_shared_account"]:
        parts.append(("Several payout accounts share the same contact details", 28))
    if p.get("f_adverse"):
        parts.append(("Cover was bought while a delay was already likely (forecast check)", 35))
    if p["f_prior_claims"]:
        parts.append(("A claim was paid on this profile in the last 12 months", 4))
    return parts


def risk_score(p: dict) -> tuple[int, list[str]]:
    """Transparent additive score from 0 to 100.

    Each signal adds points and contributes a plain English reason. In the
    cloud version the same features feed a BigQuery ML model, and these
    weights are the readable baseline it is checked against.
    """
    parts = risk_parts(p)
    signals = sorted(parts[1:], key=lambda r: -r[1])
    return min(sum(pts for _, pts in parts), 100), [label for label, _ in signals[:3]]


def clean_checks(score: int, threshold: float, event: str | None = None) -> list[str]:
    """The checks the engine really ran before paying without a person. Nothing here is assumed or decorative."""
    out = [event] if event else []
    return out + [f"Risk score {score} is under the auto-pay limit of {int(threshold)}", "No earlier claim of this kind on this trip"]


# ---------------------------------------------------------------- explain

def template_explanation(kind: str, amount: int, minutes: int, rules: dict, currency: str = "SGD") -> str:
    """Instant explanation that needs no network. Stored first, upgraded later."""
    if kind == "cancel":
        return f"Flight cancelled, covered under {CLAUSE_CANCEL}, payout {money(amount, currency)}."
    if kind == "delay":
        return f"Delay {minutes} minutes, rule {int(rules['delay_tier1_minutes'])} minutes, payout {money(amount, currency)}."
    return f"Lost bag declared with an airline report. Covered under clause 4.2, payout {money(amount, currency)}."


def held_explanation(reasons: list, amount: int, currency: str) -> str:
    """Plain reason a claim went to a person. It recommends review and never a denial."""
    top = "; ".join(r.rstrip(".").lower() for r in reasons[:3])
    return (f"Sent to a person for review, not denied. The delay qualifies for {money(amount, currency)}, "
            f"but the AI saw these signals: {top}.")


def explain(kind: str, amount: int, minutes: int, rules: dict, status: str, currency: str = "SGD", reasons: list | None = None) -> str:
    """Plain English reason for a decision, written by Gemini on Vertex AI.

    Called from a background thread, never while the database is locked, and
    with a short timeout. Any failure returns the template so a decision is
    never delayed or lost because of the model.
    """
    if kind == "cancel":
        facts = f"Flight cancelled. {CLAUSE_CANCEL}. Payout {amount} {currency}. Decision: {status}."
    elif kind == "delay":
        facts = (
            f"Flight delay {minutes} minutes. Rule threshold {int(rules['delay_tier1_minutes'])} minutes "
            f"({CLAUSE_DELAY}). Payout {amount} {currency}. Decision: {status}."
        )
    else:
        facts = f"Lost bag declared with an airline report. {CLAUSE_BAG}. Payout {amount} {currency}. Decision: {status}."
    if status == "held" and reasons:
        facts += " Risk signals found: " + "; ".join(reasons) + ". The AI recommends review by a person, never denial."
    fallback = held_explanation(reasons, amount, currency) if status == "held" and reasons else template_explanation(kind, amount, minutes, rules, currency)

    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return fallback
    try:  # pragma: no cover - needs cloud credentials
        from google import genai
        from google.genai import types

        client = genai.Client(
            vertexai=True, project=project, location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
            http_options=types.HttpOptions(timeout=8000),  # milliseconds
        )
        resp = client.models.generate_content(
            model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
            contents=(
                "Write one plain English sentence (max 30 words) explaining this insurance decision to a "
                "reviewer. Cite the clause. Facts only, no invented details.\n" + facts
            ),
        )
        AI_LAST["ok"], AI_LAST["error"] = time.time(), None
        from . import copilot
        # The model only words the reason. A sentence with a number that is not in the facts is dropped for the template.
        return copilot.safe_rewrite(facts + " " + fallback, (resp.text or "").strip(), decisions=False) or fallback
    except Exception as e:  # pragma: no cover
        AI_LAST["error"] = f"{type(e).__name__}: {str(e)[:140]}"
        return fallback


# ---------------------------------------------------------------- payout

def _timeline(final_label: str, amount: int, account: str, status: str) -> list[dict]:
    """Four step record of what the system did. Held cases stop at risk."""
    out = []
    for i, (label, sec) in enumerate(STEPS):
        step = {"label": label, "sec": sec}
        if status == "held" and label == "Paid":
            step.update(label="Held for review", sec=3, pending=True)
        if status == "held" and label == "Traveler notified":
            step.update(label="Traveler told a person will look", sec=3)
        out.append(step)
    return out


def _hm(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    return f"{h}h {m:02d}m" if h else f"{m} min"


def caring_message(flight: str, cancelled: bool, minutes: int, amount: int, status: str, account: str, cur: str = "SGD") -> str:
    """The message the traveler gets. Warm and short, says what was paid, why and where."""
    what = "was cancelled" if cancelled else f"is delayed by {_hm(minutes)}"
    if status == "paid" and account.startswith(markets.POINTS_RAIL):
        pts = round(amount * markets.POINTS_PER_USD * (1 + markets.POINTS_BONUS))
        return (f"Your flight {flight} {what}. We have added {pts:,} points to your KODA wallet, worth {money(pts / markets.POINTS_PER_USD, cur)} "
                f"at airport partners, {int(markets.POINTS_BONUS * 100)}% more than the {money(amount, cur)} payout. Spend them on food, a lounge or "
                "a data pack at the airport, or cash them out to a saved card. No forms, no waiting.")
    if status == "paid":
        return (f"Your flight {flight} {what}. We have sent {money(amount, cur)} to {account}. "
                "Have a meal or a coffee at the airport on us. No forms, no waiting.")
    return (f"Your flight {flight} {what}. We are sorry. A claims specialist is checking your cover now, "
            "so you do not need to do anything. We will message you as soon as a decision is made.")


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:7].upper()}"


# ---------------------------------------------------------------- trigger

def run_delay(c, flight_code: str, minutes: int, cancelled: bool = False, source: str = "flight data") -> list[dict]:
    """Simulator or live entry point: set the flight delay and process every policy.

    A policy already decided is never paid twice. If the delay later grows into
    a higher tier, the decision is raised to the higher amount (paid 1,500 at
    3 hours, then 3,000 in total at 6 hours).
    """
    c.execute("UPDATE flights SET delay_minutes=?, cancelled=? WHERE code=?", (minutes, int(cancelled), flight_code))
    fl = db.one(c, "SELECT * FROM flights WHERE code=?", (flight_code,)) or {}
    results = []
    policies = db.rows(
        c, "SELECT * FROM policies WHERE flight=? AND status='active' AND delay_cover=1", (flight_code,)
    )
    # A delay reported after the trip was closed as on time: the two reports disagree. It is never paid or refused by
    # the system; it goes to a person with both reports side by side, once per trip.
    for p in db.rows(c, "SELECT * FROM policies WHERE flight=? AND status='archived' AND delay_cover=1", (flight_code,)):
        r = late_report(c, p, minutes, cancelled, source)
        if r:
            results.append(r)
    for p in policies:
        wallet.refresh(c, p)  # the traveler's current choice of points or saved card decides where this goes
        # Money and rules follow the policy's own market (where the traveler bought cover).
        market = p["market"] or "TH"
        cur = currency_of(market)
        rules = get_rules(c, market)
        t0 = time.perf_counter()
        amount = entitlement(rules, minutes, cancelled)["amount"]
        db.log(c, p["id"], "cancel_detected" if cancelled else "delay_detected", {"flight": flight_code, "minutes": minutes, "source": source})
        if amount == 0:
            continue
        prior = db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (p["id"],))
        if prior:
            # One claim per trip. A worse outcome (a longer delay, or a cancellation after a delay payout) raises the
            # total to what that outcome is worth; only the difference is sent. A smaller figure never lowers it.
            total = max(int(prior["amount"]), amount)
            if prior["status"] in ("paid", "held") and total > prior["amount"]:
                extra = total - int(prior["amount"])
                kept = prior["delay_minutes"] if cancelled else minutes
                c.execute("UPDATE decisions SET amount=?, delay_minutes=?, explanation=?, clause=? WHERE id=?",
                          (total, kept, template_explanation("cancel" if cancelled else "delay", total, kept, rules, cur),
                           CLAUSE_CANCEL if cancelled else prior["clause"], prior["id"]))
                db.log(c, p["id"], "tier_upgrade", {"decision": prior["id"], "extra": extra, "total": total, "cause": "cancelled" if cancelled else "delay"})
                if prior["status"] == "paid":
                    c.execute("UPDATE decisions SET message=? WHERE id=?",
                              (caring_message(flight_code, cancelled, kept, total, "paid", p["payout_account"], cur), prior["id"]))
                    wallet.stamp(c, prior["id"])  # sends the difference, once
                partners.sync(c, p["id"])
                results.append({"policy": p["id"], "decision": prior["id"], "status": "upgraded", "extra": extra})
            continue  # double claim prevention
        feats = features(c, p)
        score, reasons = risk_score(feats)
        status = "paid" if score < rules["risk_threshold"] else "held"
        event = (f"Flight cancelled: {CLAUSE_CANCEL}" if cancelled else
                 f"Recorded delay of {_hm(minutes)} meets the {_hm(int(rules['delay_tier1_minutes']))} rule")
        reasons_out = clean_checks(score, rules["risk_threshold"], event) if status == "paid" else reasons
        did = _new_id("RQ") if status == "held" else _new_id("DC")
        c.execute(
            "INSERT INTO decisions (id,policy_id,kind,status,amount,risk_score,reasons,timeline,clause,explanation,"
            "delay_minutes,created_at,decided_by,note,trigger,message,context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                did, p["id"], "delay", status, amount, score,
                json.dumps(reasons_out), json.dumps(_timeline("Paid", amount, p["payout_account"], status)),
                CLAUSE_CANCEL if cancelled else CLAUSE_DELAY,
                (template_explanation("cancel" if cancelled else "delay", amount, minutes, rules, cur) if status == "paid" else
                 held_explanation(reasons_out, amount, cur)), minutes, time.time(),
                "system" if status == "paid" else None, None, "cancel" if cancelled else "delay",
                caring_message(flight_code, cancelled, minutes, amount, status, p["payout_account"], cur),
                json.dumps(context.for_event(flight_code, fl.get("origin", ""), cancelled, minutes)),
            ),
        )
        c.execute("UPDATE decisions SET elapsed_ms=? WHERE id=?", (max(1, int((time.perf_counter() - t0) * 1000)), did))
        c.execute("UPDATE decisions SET breakdown=? WHERE id=?", (json.dumps(risk_parts(feats)), did))
        db.log(c, p["id"], f"decision_{status}", {"decision": did, "score": score, "amount": amount})
        wallet.stamp(c, did)
        partners.sync(c, p["id"])
        results.append({"policy": p["id"], "decision": did, "status": status, "score": score})
    return results


def late_report(c, p: dict, minutes: int, cancelled: bool, source: str) -> dict | None:
    """A delay or cancellation reported for a trip already closed as on time. Both reports are kept and a person decides.
    Repeats update the same review; nothing is paid until a person approves with the verified figure."""
    rules = get_rules(c, p["market"] or "TH")
    worth = entitlement(rules, minutes, cancelled)["amount"]
    if not worth:
        return None
    now = time.time()
    report = {"minutes": int(minutes or 0), "cancelled": bool(cancelled), "source": source, "at": now}
    closed = db.one(c, "SELECT ts, detail FROM events WHERE policy_id=? AND type='trip_archived' ORDER BY ts DESC LIMIT 1", (p["id"],)) or {}
    d = db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (p["id"],))
    if d:
        if d["status"] != "held" or d.get("trigger") != "late_report":
            return None  # the trip already has a claim; that claim's own path applies
        ev = json.loads(d.get("evidence") or "{}")
        if any(r["minutes"] == report["minutes"] and r["cancelled"] == report["cancelled"] for r in ev.get("reports", [])):
            return None  # the same report again: no new task, no new payment
        ev.setdefault("reports", []).append(report)
        c.execute("UPDATE decisions SET evidence=?, amount=MAX(amount, ?), delay_minutes=MAX(delay_minutes, ?) WHERE id=?",
                  (json.dumps(ev), worth, report["minutes"], d["id"]))
        db.log(c, p["id"], "late_report_updated", {"decision": d["id"], **report})
        return {"policy": p["id"], "decision": d["id"], "status": "late_report_updated"}
    cur = currency_of(p["market"] or "TH")
    feats = features(c, p)
    score, _ = risk_score(feats)
    what = "a cancellation" if cancelled else f"a delay of {_hm(minutes)}"
    when_closed = time.strftime("%d %b %H:%M", time.localtime(closed["ts"])) if closed.get("ts") else "earlier"
    reasons = [f"The trip was closed as on time ({when_closed}).",
               f"Afterwards {source} reported {what}.",
               "The two reports disagree, so a person checks the airline record before anything is paid or refused."]
    evidence = {"conflict": True, "previous": {"status": "on time, trip closed", "at": closed.get("ts")}, "reports": [report]}
    did = _new_id("RQ")
    c.execute(
        "INSERT INTO decisions (id,policy_id,kind,status,amount,risk_score,reasons,timeline,clause,explanation,"
        "delay_minutes,created_at,decided_by,note,trigger,message,context,evidence) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (did, p["id"], "delay", "held", worth, score, json.dumps(reasons), json.dumps(_timeline("Paid", worth, p["payout_account"], "held")),
         CLAUSE_CANCEL if cancelled else CLAUSE_DELAY,
         f"Conflicting reports: the trip was closed as on time, then {what} was reported. If the airline record confirms it, the cover pays {money(worth, cur)}.",
         int(minutes or 0), now, None, None, "late_report",
         f"We received a new report about your flight {p['flight']} after it was marked on time. A claims officer is checking the airline's record. "
         "You do not need to do anything; we will message you here.", json.dumps({}), json.dumps(evidence)))
    c.execute("UPDATE decisions SET breakdown=? WHERE id=?", (json.dumps(risk_parts(feats)), did))
    db.log(c, p["id"], "late_report_review", {"decision": did, **report})
    return {"policy": p["id"], "decision": did, "status": "held", "score": score}


def request_recheck(c, policy_id: str, note: str) -> dict:
    """The traveler asks a person to look at a delay that did not reach the payout rule. A person decides; the system never denies."""
    p = db.one(c, "SELECT * FROM policies WHERE id=?", (policy_id,))
    if not p or not p["delay_cover"]:
        raise ValueError("This trip has no delay cover to re-check.")
    f = db.one(c, "SELECT * FROM flights WHERE code=?", (p["flight"],)) or {}
    existing = db.one(c, "SELECT status FROM decisions WHERE policy_id=? AND kind='delay'", (policy_id,))
    if existing and existing["status"] == "declined":
        return request_second_review(c, policy_id, note)  # the same button on a declined claim asks for a second look
    if existing:
        raise ValueError("This trip already has a claim. Open it from Claims to see where it is.")
    if not f.get("delay_minutes") and not f.get("cancelled"):
        raise ValueError("There is no recorded delay on this flight.")
    rules = get_rules(c, p["market"] or "SG")
    cur = currency_of(p["market"] or "SG")
    minutes = int(f.get("delay_minutes") or 0)
    amount = 0  # nothing is owed on the recorded figure; a reviewer sets the payout from the verified delay
    did = _new_id("RQ")
    reasons = [f"Traveler asked for a re-check: the recorded delay of {minutes} minutes is below the {int(rules['delay_tier1_minutes'])} minute rule.",
               "Needs a person to check the airline record. The payout is calculated from the verified delay, by the same rules."]
    c.execute(
        "INSERT INTO decisions (id,policy_id,kind,status,amount,risk_score,reasons,timeline,clause,explanation,"
        "delay_minutes,created_at,decided_by,note,trigger,message,context) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (did, policy_id, "delay", "held", amount, 0, json.dumps(reasons), json.dumps(_timeline("Paid", amount, p["payout_account"], "held")),
         CLAUSE_DELAY, f"Re-check asked by the traveler. The recorded delay was {minutes} minutes, below the {int(rules['delay_tier1_minutes'])} minute rule. "
                       f"If the airline record shows {_hm(int(rules['delay_tier1_minutes']))} or more, the cover pays {money(rules['delay_tier1_payout'], cur)}, "
                       f"or {money(rules['delay_tier2_payout'], cur)} from {_hm(int(rules['delay_tier2_minutes']))}.",
         minutes, time.time(), None, "Traveler note: " + (note.strip() or "none"), "recheck",
         "We have sent your request to a claims officer. You will get a message here when it is decided.", json.dumps({})))
    db.log(c, policy_id, "recheck_requested", {"decision": did, "note": note.strip()[:300]})
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (did,))


SECOND_REVIEWS = 1  # demo rule (a product choice, not a legal one): one second review, then exceptions are an admin's call


def second_reviews_used(c, decision_id: str) -> int:
    return db.one(c, "SELECT COUNT(*) n FROM events WHERE type='review_requested' AND detail LIKE ?", (f'%"{decision_id}"%',))["n"]


def can_ask_second_review(c, decision: dict | None) -> bool:
    return bool(decision and decision["status"] == "declined" and second_reviews_used(c, decision["id"]) < SECOND_REVIEWS)


def request_second_review(c, policy_id: str, note: str = "") -> dict:
    """A declined delay claim goes back to the review queue with the traveler's note. Same claim id, so both apps follow it."""
    d = db.one(c, "SELECT * FROM decisions WHERE policy_id=? AND kind='delay'", (policy_id,))
    if not d or d["status"] != "declined":
        raise ValueError("There is no declined claim to review.")
    if not can_ask_second_review(c, d):
        raise ValueError("This claim has already had a second review. If you have new information, send it to us in Messages.")
    if len((note or "").strip()) < 10:
        raise ValueError("Tell us what is new, for example what the airline's record or letter says.")
    earlier = f"Declined by {d['decided_by'] or 'a claims officer'}" + (f": {d['note']}" if d.get("note") else "")
    c.execute("UPDATE decisions SET status='held', decided_by=NULL, note=?, message=? WHERE id=?",
              (f"Second review asked. Traveler note: {note.strip() or 'none'}. Earlier: {earlier}",
               "We have sent your claim back to a claims officer for a second review. You will get a message here when it is decided.", d["id"]))
    db.log(c, policy_id, "review_requested", {"decision": d["id"], "note": note.strip()[:300]})
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (d["id"],))


# ---------------------------------------------------------------- case history

def reopen_exception(c, decision_id: str, admin: str, reason: str) -> dict:
    """An admin reopens a declined claim beyond the standard review limit. Always with a reason, always on the record."""
    d = db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,))
    if not d or d["status"] != "declined":
        raise ValueError("Only a declined claim can be reopened.")
    if len((reason or "").strip()) < 10:
        raise ValueError("Give the reason for the exception.")
    c.execute("UPDATE decisions SET status='held', decided_by=NULL, note=?, message=? WHERE id=?",
              (f"Reopened as an exception by {admin}: {reason.strip()}",
               "A senior officer has reopened your claim for another look. You will get a message here when it is decided.", decision_id))
    db.log(c, d["policy_id"], "review_exception", {"decision": decision_id, "by": admin, "reason": reason.strip()[:300]})
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,))


def assess(c, d: dict) -> dict:
    """What the rules say about a claim, for the reviewer: recorded figure, tier, total, already sent, still owed."""
    from . import payments
    p = db.one(c, "SELECT * FROM policies WHERE id=?", (d["policy_id"],)) or {}
    f = db.one(c, "SELECT * FROM flights WHERE code=?", (p.get("flight"),)) or {}
    rules = get_rules(c, p.get("market") or "TH")
    rec = entitlement(rules, d.get("delay_minutes"), bool(f.get("cancelled")) and d.get("trigger") != "recheck")
    pay = payments.summary(c, d["id"], d["amount"] if d["status"] == "paid" else None)
    needs = d.get("trigger") in ("recheck", "late_report") and d["status"] == "held"
    total = int(d["amount"] or 0)
    return {"recorded_minutes": int(d.get("delay_minutes") or 0), "cancelled": bool(f.get("cancelled")),
            "tier": rec["tier"], "rule": rec["rule"], "t1": int(rules["delay_tier1_minutes"]), "t2": int(rules["delay_tier2_minutes"]),
            "p1": int(rules["delay_tier1_payout"]), "p2": int(rules["delay_tier2_payout"]), "pc": int(rules["cancel_payout"]),
            "entitled_total": total, "sent": pay["sent"], "remaining": max(0, total - pay["sent"]) if d["status"] == "paid" else total,
            "needs_verified_delay": needs, "payment": pay, "evidence": json.loads(d.get("evidence") or "{}")}


def case_history(c, policy_id: str, staff: bool = False) -> list[dict]:
    """One timeline per trip, read from the audit log, so the traveler and the insurer see the same record of what
    happened. Staff also see the risk score; travelers see plain steps. Nothing here is written, only read."""
    p = db.one(c, "SELECT currency FROM policies WHERE id=?", (policy_id,)) or {}
    cur = p.get("currency") or "SGD"
    out = []
    for e in db.rows(c, "SELECT ts, type, detail FROM events WHERE policy_id=? ORDER BY ts, id", (policy_id,)):
        d = json.loads(e["detail"] or "{}")
        t, by = e["type"], d.get("by")
        if t == "policy_active":
            what = "Cover started" + (" (added by demo controls)" if d.get("added_by") else "")
        elif t == "cover_added":
            what = "Cover added: " + " and ".join(k for k in ("delay", "baggage") if d.get(k))
        elif t in ("delay_detected", "cancel_detected"):
            what = "Flight cancelled" if t == "cancel_detected" else f"Flight delay recorded: {_hm(d.get('minutes') or 0)}"
        elif t == "decision_paid":
            what = f"Paid automatically: {money(d.get('amount', 0), cur)}" + (f" (risk score {d.get('score')})" if staff else "")
        elif t == "decision_held":
            what = f"Sent to a claims officer for a {money(d.get('amount', 0), cur)} claim" + (f" (risk score {d.get('score')})" if staff else "")
        elif t == "tier_upgrade":
            what = (f"Flight cancelled after the delay payout: topped up by {money(d.get('extra', 0), cur)} to {money(d.get('total', 0), cur)}"
                    if d.get("cause") == "cancelled" else f"Delay grew past the next level: topped up by {money(d.get('extra', 0), cur)}")
        elif t == "recheck_requested":
            what = "Traveler asked for a re-check" + (f": {d['note']}" if d.get("note") else "")
        elif t == "info_requested":
            what = f"{by or 'A claims officer'} asked the traveler: {d.get('text', '')}"
        elif t == "review_requested":
            what = "Traveler asked for a second review" + (f": {d['note']}" if d.get("note") else "")
        elif t in ("review_paid", "baggage_paid", "extra_paid"):
            what = f"Approved and paid by {by or 'a claims officer'}" + (f": {d['note']}" if staff and d.get("note") else "")
        elif t in ("review_declined", "baggage_declined", "extra_declined"):
            what = f"Declined by {by or 'a claims officer'}" + (f": {d['note']}" if d.get("note") else "")
        elif t == "baggage_claim":
            what = f"Lost bag reported, {money(d.get('total', 0), cur)} declared"
        elif t == "trip_archived":
            what = "Flight ended with no covered event, trip archived"
        elif t == "late_report_review":
            what = ("A cancellation" if d.get("cancelled") else f"A delay of {_hm(d.get('minutes') or 0)}") + \
                   f" was reported after the trip was closed as on time ({d.get('source', 'flight data')}). Sent to a claims officer"
        elif t == "late_report_updated":
            what = f"Another report: {'cancelled' if d.get('cancelled') else _hm(d.get('minutes') or 0)} ({d.get('source', 'flight data')})"
        elif t == "evidence_verified":
            what = (f"{by or 'A claims officer'} checked the airline record: " + ("cancelled" if d.get("cancelled") else _hm(d.get("verified_minutes") or 0)) +
                    f", {d.get('tier') or 'no tier'}, {money(d.get('amount', 0), cur)}")
        elif t == "review_exception":
            what = f"Reopened as an exception by {by or 'an admin'}" + (f": {d['reason']}" if staff and d.get("reason") else "")
        elif t == "event_after_close":
            what = ("Cancellation" if d.get("cancelled") else f"A delay of {_hm(d.get('minutes') or 0)}") + \
                   " was reported after this trip was closed as on time. Nothing was paid automatically."
        else:
            continue  # quotes, notices and other bookkeeping stay in the full audit log
        out.append({"ts": e["ts"], "type": t, "what": what})
    return out


def enrich_explanations(decision_ids: list[str]) -> None:
    """Upgrade stored template explanations with Gemini. Runs in a background thread."""
    if not os.environ.get("GOOGLE_CLOUD_PROJECT"):
        return
    for did in decision_ids:
        with db.conn() as c:
            d = db.one(c, "SELECT * FROM decisions WHERE id=?", (did,))
            pol = db.one(c, "SELECT market FROM policies WHERE id=?", (d["policy_id"],)) if d else None
            rules = get_rules(c, (pol or {}).get("market") or "TH")
        if not d:
            continue
        cur = currency_of((pol or {}).get("market") or "TH")
        text = explain("cancel" if d.get("trigger") == "cancel" else d["kind"], d["amount"], d["delay_minutes"], rules, d["status"], cur, json.loads(d["reasons"] or "[]"))  # network, no lock held
        with db.conn() as c:
            c.execute("UPDATE decisions SET explanation=? WHERE id=?", (text, did))


def decide_held(c, decision_id: str, approve: bool, reviewer: str, note: str,
                verified_minutes: int | None = None, verified_cancelled: bool = False) -> dict:
    """A person decides a held claim.

    Re-checks and late reports exist because the recorded figure is in doubt, so they can only be approved with the
    figure from the airline's record; the payout is then calculated by entitlement(), the same function the automatic
    path uses. A reviewer cannot type an amount. A decline, and an approval that overrides the recorded data, need a reason."""
    d = db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,))
    if not d or d["status"] != "held":
        raise ValueError("decision is not waiting for review")
    note = (note or "").strip()
    if not approve and not note:
        raise ValueError("Give the reason for declining. The traveler will see it.")
    if approve and d.get("trigger") in ("recheck", "late_report"):
        if verified_minutes is None and not verified_cancelled:
            raise ValueError("Enter the delay shown in the airline's record, or mark the flight cancelled. The payout is calculated from it.")
        if not note:
            raise ValueError("Give the reason, for example where the verified delay comes from.")
        pol0 = db.one(c, "SELECT market FROM policies WHERE id=?", (d["policy_id"],)) or {}
        rules = get_rules(c, pol0.get("market") or "TH")
        ent = entitlement(rules, verified_minutes, verified_cancelled)
        if not ent["amount"]:
            raise ValueError(f"A verified delay of {_hm(int(verified_minutes or 0))} is under the {_hm(int(rules['delay_tier1_minutes']))} rule, "
                             "so nothing is payable. Decline it with the reason, or ask the traveler for the airline's record.")
        ev = json.loads(d.get("evidence") or "{}")
        ev["verified"] = {"minutes": None if verified_cancelled else int(verified_minutes), "cancelled": bool(verified_cancelled), "by": reviewer, "at": time.time(), "reason": note}
        c.execute("UPDATE decisions SET amount=?, delay_minutes=?, evidence=?, clause=? WHERE id=?",
                  (ent["amount"], d["delay_minutes"] if verified_cancelled else int(verified_minutes), json.dumps(ev),
                   CLAUSE_CANCEL if verified_cancelled else CLAUSE_DELAY, decision_id))
        db.log(c, d["policy_id"], "evidence_verified", {"decision": decision_id, "by": reviewer, "recorded_minutes": d["delay_minutes"],
                                                        "verified_minutes": verified_minutes, "cancelled": bool(verified_cancelled),
                                                        "tier": ent["tier"], "amount": ent["amount"]})
        d = db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,))
    status = "paid" if approve else "declined"
    tl = json.loads(d["timeline"])
    msg = d.get("message")
    if approve:
        tl = [{"label": "Paid", "sec": 8}, {"label": "Traveler notified", "sec": 9}] if len(tl) < 5 else tl
        tl = tl[:3] + [{"label": "Paid", "sec": 8}, {"label": "Traveler notified", "sec": 9}]
        wallet.refresh(c, db.one(c, "SELECT * FROM policies WHERE id=?", (d["policy_id"],)))
        pol = db.one(c, "SELECT payout_account, currency FROM policies WHERE id=?", (d["policy_id"],)) or {}
        flight_code = db.one(c, "SELECT flight FROM policies WHERE id=?", (d["policy_id"],))["flight"]
        if d.get("trigger") in ("recheck", "late_report"):
            cur2 = pol.get("currency", "SGD")
            acct = pol.get("payout_account", "your account")
            where = ("your KODA wallet" if acct.startswith(markets.POINTS_RAIL) else acct)
            msg = (f"We looked at {flight_code} again with the airline record and your claim is approved. "
                   f"{money(d['amount'], cur2)} has gone to {where}. Thank you for telling us.")
        else:
            msg = caring_message(flight_code, d.get("trigger") == "cancel", d["delay_minutes"], d["amount"], "paid",
                                 pol.get("payout_account", "your account"), pol.get("currency", "SGD"))
    else:
        msg = ("Thank you for your patience. After review, this claim does not meet the cover terms. "
               + ("You can ask for a second review from the app." if second_reviews_used(c, decision_id) < SECOND_REVIEWS
                  else "This was the second review, so the decision is final. Message us in the app if you have new information."))
    c.execute(
        "UPDATE decisions SET status=?, decided_by=?, note=?, timeline=?, message=? WHERE id=?",
        (status, reviewer, note, json.dumps(tl), msg, decision_id),
    )
    db.log(c, d["policy_id"], f"review_{status}", {"decision": decision_id, "by": reviewer, "note": note})
    wallet.stamp(c, decision_id)
    partners.sync(c, d["policy_id"])
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (decision_id,))


# ---------------------------------------------------------------- baggage

def submit_bag_claim(c, policy_id: str, report_id: str, bag_tag: str, items: list[dict]) -> dict:
    p = db.one(c, "SELECT * FROM policies WHERE id=?", (policy_id,))
    # A trip closed after landing ("archived") still takes a lost-bag claim: landing is when a bag goes missing.
    if not p or p["status"] not in ("active", "archived"):
        raise ValueError("This trip has no active cover.")
    if not p["baggage_cover"]:
        raise ValueError("This trip has no baggage cover.")
    if db.one(c, "SELECT 1 FROM baggage WHERE policy_id=?", (policy_id,)):
        raise ValueError("a baggage claim already exists for this policy")
    total = sum(int(i["amount"]) for i in items)
    bid = _new_id("BC")
    dest = (db.one(c, "SELECT dest FROM flights WHERE code=?", (p["flight"],)) or {}).get("dest") or "the destination"
    c.execute(
        "INSERT INTO baggage VALUES (?,?,?,?,?,?,?,?)",
        (bid, policy_id, report_id, bag_tag, f"Bag not found at {dest}", json.dumps(items), total, time.time()),
    )
    market = p.get("market") or "TH"
    cur = currency_of(market)
    rules = get_rules(c, market)
    amount = min(total, int(rules["lost_bag_payout"]))
    feats = {**features(c, p), "f_covers_2h": 1}
    score, reasons = risk_score(feats)
    score += 6  # claim loading: values are self declared
    status = "pending" if score < rules["risk_threshold"] else "held"
    c.execute(
        "INSERT INTO decisions (id,policy_id,kind,status,amount,risk_score,reasons,timeline,clause,explanation,"
            "delay_minutes,created_at,decided_by,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            bid + "-D", policy_id, "baggage", status, amount, score,
            json.dumps(reasons if status == "held" else clean_checks(score, rules["risk_threshold"], f"Airline report {report_id} given")), json.dumps([]), CLAUSE_BAG,
            template_explanation("baggage", amount, 0, rules, cur), 0, time.time(), None, None,
        ),
    )
    c.execute("UPDATE decisions SET breakdown=? WHERE id=?",
              (json.dumps(risk_parts(feats) + [("Loading for self declared item values", 6)]), bid + "-D"))
    db.log(c, policy_id, "baggage_claim", {"claim": bid, "total": total, "score": score})
    return db.one(c, "SELECT * FROM baggage WHERE id=?", (bid,))


def decide_baggage(c, claim_id: str, approve: bool, reviewer: str) -> dict:
    d = _decide_simple(c, claim_id, approve, reviewer, "baggage")
    pol = db.one(c, "SELECT payout_account, currency FROM policies WHERE id=?", (d["policy_id"],)) or {}
    acct = pol.get("payout_account") or "your account"
    where = (f"as {wallet.points_for(d['amount']):,} points in your KODA wallet" if acct.startswith(markets.POINTS_RAIL) else f"to {acct}")
    msg = (f"A claims officer approved your lost bag claim. {money(d['amount'], pol.get('currency', 'SGD'))} was paid {where}." if approve
           else "After review, this lost bag claim could not be approved. Message us in the app if you have more from the airline.")
    c.execute("UPDATE decisions SET message=? WHERE id=?", (msg, d["id"]))
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (d["id"],))


def _decide_simple(c, claim_id: str, approve: bool, reviewer: str, tag: str) -> dict:
    """A person approves or declines a claim that waits as 'pending' or 'held' (baggage and extra costs)."""
    d = db.one(c, "SELECT * FROM decisions WHERE id=?", (claim_id + "-D",))
    if not d or d["status"] not in ("pending", "held"):
        raise ValueError("claim is not waiting for a decision")
    status = "paid" if approve else "declined"
    wallet.refresh(c, db.one(c, "SELECT * FROM policies WHERE id=?", (d["policy_id"],)))
    c.execute("UPDATE decisions SET status=?, decided_by=? WHERE id=?", (status, reviewer, claim_id + "-D"))
    db.log(c, d["policy_id"], f"{tag}_{status}", {"claim": claim_id, "by": reviewer})
    wallet.stamp(c, claim_id + "-D")
    partners.sync(c, d["policy_id"])
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (claim_id + "-D",))


# ---------------------------------------------------------------- extra delay costs

def extra_limits(rules: dict) -> tuple[int, int]:
    """(fast track limit, total cap). Small, clean claims are paid at once. Anything above waits for a person."""
    return int(rules["delay_tier1_payout"]), int(rules["delay_tier2_payout"]) * 2


def submit_extra_claim(c, policy_id: str, what: str, amount: int, receipt: str | None) -> dict:
    """Costs above the instant payout, claimed with a receipt. Only after the delay payout has been made."""
    p = db.one(c, "SELECT * FROM policies WHERE id=?", (policy_id,))
    if not p or p["status"] != "active" or not p["delay_cover"]:
        raise ValueError("this policy has no delay cover")
    paid = db.one(c, "SELECT 1 FROM decisions WHERE policy_id=? AND kind='delay' AND status='paid'", (policy_id,))
    if not paid:
        raise ValueError("Extra costs can be claimed once your delay payout has been made.")
    if db.one(c, "SELECT 1 FROM expenses WHERE policy_id=?", (policy_id,)):
        raise ValueError("an extra costs claim already exists for this policy")
    if receipt and (not receipt.startswith("data:image/") or len(receipt) > 400_000):
        raise ValueError("the receipt photo is not valid or is too large")
    wallet.refresh(c, p)
    market = p.get("market") or "TH"
    cur = currency_of(market)
    rules = get_rules(c, market)
    fast, cap = extra_limits(rules)
    payable = min(int(amount), cap)
    eid = _new_id("EX")
    c.execute("INSERT INTO expenses VALUES (?,?,?,?,?,?)", (eid, policy_id, what, int(amount), receipt, time.time()))
    feats = {**features(c, p), "f_covers_2h": 1}
    score, reasons = risk_score(feats)
    score += 6  # claim loading: amounts are self declared
    reasons = list(reasons)
    if payable > fast:
        reasons.append(f"Above the fast track limit of {money(fast, cur)}, so a person checks the receipt")
    auto = score < rules["risk_threshold"] and payable <= fast
    status = "paid" if auto else "pending"
    acct = p["payout_account"] or "your account"
    where = (f"as {wallet.points_for(payable):,} points in your KODA wallet" if acct.startswith(markets.POINTS_RAIL) else f"to {acct}")
    msg = (f"We paid {money(payable, cur)} for your {what.lower()} {where}. No forms, no waiting."
           if auto else f"We have your {what.lower()} receipt. A person is checking it and will pay you here once it is approved.")
    expl = (f"Extra cost of {money(int(amount), cur)} for {what.lower()}. Fast track limit {money(fast, cur)}, total cap {money(cap, cur)}. "
            + ("Paid at once because it was under the fast track limit and nothing looked unusual." if auto
               else "A person checks it because it is above the fast track limit or the risk score was raised."))
    c.execute(
        "INSERT INTO decisions (id,policy_id,kind,status,amount,risk_score,reasons,timeline,clause,explanation,"
        "delay_minutes,created_at,decided_by,note,message) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid + "-D", policy_id, "expense", status, payable, score,
         json.dumps(reasons if not auto else clean_checks(score, rules["risk_threshold"], f"Amount under the fast track limit of {money(fast, cur)}")), json.dumps([]), CLAUSE_EXTRA, expl, 0, time.time(),
         "system" if auto else None, None, msg))
    c.execute("UPDATE decisions SET breakdown=? WHERE id=?",
              (json.dumps(risk_parts(feats) + [("Loading for self declared amounts", 6)]), eid + "-D"))
    db.log(c, policy_id, "extra_claim", {"claim": eid, "amount": int(amount), "status": status})
    wallet.stamp(c, eid + "-D")
    partners.sync(c, policy_id)
    return db.one(c, "SELECT * FROM expenses WHERE id=?", (eid,))


def decide_extra(c, claim_id: str, approve: bool, reviewer: str) -> dict:
    d = _decide_simple(c, claim_id, approve, reviewer, "extra")
    if approve:
        pol = db.one(c, "SELECT payout_account, currency FROM policies WHERE id=?", (d["policy_id"],)) or {}
        e = db.one(c, "SELECT what FROM expenses WHERE id=?", (claim_id,)) or {"what": "costs"}
        acct = pol.get("payout_account") or "your account"
        where = (f"as {wallet.points_for(d['amount']):,} points in your KODA wallet" if acct.startswith(markets.POINTS_RAIL) else f"to {acct}")
        c.execute("UPDATE decisions SET message=? WHERE id=?",
                  (f"A person approved your {e['what'].lower()} claim. {money(d['amount'], pol.get('currency', 'USD'))} was paid {where}.", d["id"]))
    else:
        c.execute("UPDATE decisions SET message=? WHERE id=?",
                  ("After review, this receipt could not be approved. Your delay payout is not affected.", d["id"]))
    return db.one(c, "SELECT * FROM decisions WHERE id=?", (d["id"],))
