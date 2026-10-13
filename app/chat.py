"""Support chat: a traveler talks to the KODA assistant, and a person at the insurer can step in.

How it works
  * One open chat per traveler. Every message is stored with who wrote it: the traveler, the assistant or a named
    member of staff, so the app can label each bubble and nobody has to guess whether they are talking to a bot.
  * The assistant answers from the traveler's own data. It reads the facts with the same read-only tool functions the
    claims agent uses (policy, flight status, forecast, rules, wallet) and answers from what they return. Gemini on
    Vertex AI rewrites the wording when it is configured; without it the grounded text is used as is. The answer step
    is one function (`_answer`), so it can be replaced with an ADK agent without touching the rest of the app.
  * The assistant never decides a claim. When the traveler asks for a person, or asks something only a person should
    answer (a decision, a complaint, anything about a declined claim), the chat is handed over: it is flagged for the
    insurer, the traveler is told, and the assistant stops answering until a person hands it back.
"""
import json
import time

from . import copilot, db, engine, forecast, markets, wallet

BOT_NAME = "KODA assistant"
HANDOVER_WORDS = ("human", "person", "agent", "someone", "manager", "officer", "speak to", "talk to", "call me",
                  "complain", "complaint", "unfair", "not fair", "lawyer", "ombudsman", "escalate")
DECISION_WORDS = ("why was", "declined", "refuse", "rejected", "appeal", "overturn", "pay me", "approve my")


# ----------------------------------------------------------------- the chat record

def open_chat(c, cid: str) -> dict:
    """The traveler's open chat, created on first use."""
    row = db.one(c, "SELECT * FROM chats WHERE customer_id=? AND status='open'", (cid,))
    if row:
        return row
    chat_id = engine._new_id("CH")
    c.execute("INSERT INTO chats (id,customer_id,status,mode,assigned_to,created_at,updated_at,waiting) VALUES (?,?,'open','bot',NULL,?,?,0)",
              (chat_id, cid, time.time(), time.time()))
    say(c, chat_id, "bot", BOT_NAME,
        "Hello. I can explain what your cover pays, check where a claim is, and tell you what a delay means for your trip. "
        "Ask me anything, or say you would like a person.")
    return db.one(c, "SELECT * FROM chats WHERE id=?", (chat_id,))


def say(c, chat_id: str, role: str, author: str, text: str, meta: dict | None = None) -> dict:
    mid = engine._new_id("MS")
    c.execute("INSERT INTO chat_messages (id,chat_id,role,author,text,meta,ts) VALUES (?,?,?,?,?,?,?)",
              (mid, chat_id, role, author, text, json.dumps(meta or {}), time.time()))
    c.execute("UPDATE chats SET updated_at=? WHERE id=?", (time.time(), chat_id))
    return db.one(c, "SELECT * FROM chat_messages WHERE id=?", (mid,))


def messages(c, chat_id: str, limit: int = 60) -> list[dict]:
    rows = db.rows(c, "SELECT id,role,author,text,meta,ts FROM chat_messages WHERE chat_id=? ORDER BY ts, rowid LIMIT ?", (chat_id, limit))
    for r in rows:
        r["meta"] = json.loads(r["meta"]) if r.get("meta") else {}
    return rows


def state(c, chat_id: str) -> dict:
    ch = db.one(c, "SELECT * FROM chats WHERE id=?", (chat_id,))
    return {"id": ch["id"], "mode": ch["mode"], "with": ch["assigned_to"] or BOT_NAME, "waiting": bool(ch["waiting"]),
            "messages": messages(c, chat_id)}


# ----------------------------------------------------------------- what the assistant knows

def facts(c, cid: str) -> dict:
    """Read-only facts about this traveler, the same ones the apps show. The assistant answers only from these."""
    cu = wallet.get(c, cid) or {}
    rules = engine.get_rules(c, cu.get("market") or "SG")
    bal = wallet.balance(c, cid)
    trips = db.rows(c, "SELECT p.id, p.flight, p.pnr, p.delay_cover, p.baggage_cover, p.status, p.payout_account, "
                       "f.origin, f.dest, f.depart_label, f.sched_ts, f.delay_minutes, f.cancelled, f.scheduled, f.flight_date, f.market "
                       "FROM policies p LEFT JOIN flights f ON f.code=p.flight WHERE p.customer_id=? AND p.status IN ('active','archived') "
                       "ORDER BY f.sched_ts DESC", (cid,))
    now = time.time()
    out = []
    for t in trips:
        d = db.one(c, "SELECT id,status,amount,points,delay_minutes,decided_by,note,trigger FROM decisions WHERE policy_id=? AND kind='delay'", (t["id"],))
        bag = db.one(c, "SELECT status,total FROM baggage WHERE policy_id=?", (t["id"],))
        item = {"policy": t["id"], "flight": t["flight"], "booking": t["pnr"], "route": f"{t['origin']} to {t['dest']}",
                "departs": t["depart_label"], "upcoming": bool(t["sched_ts"] and t["sched_ts"] > now),
                "cover": "delay and baggage" if t["delay_cover"] and t["baggage_cover"] else "delay only" if t["delay_cover"]
                         else "baggage only" if t["baggage_cover"] else "none",
                "delay_minutes": t["delay_minutes"], "cancelled": bool(t["cancelled"]),
                "claim": ({"status": d["status"], "amount": d["amount"], "points": d["points"], "decided_by": d["decided_by"],
                           "note": d["note"], "from_recheck": d["trigger"] == "recheck"} if d else None),
                "bag_claim": ({"status": bag["status"], "declared": bag["total"]} if bag else None)}
        if item["upcoming"] and t["flight"]:
            fl = db.one(c, "SELECT * FROM flights WHERE code=?", (t["flight"],))
            if fl:
                item["delay_chance_percent"] = forecast.compute(fl)["probability"]
        item["_ts"] = t["sched_ts"] or 0
        out.append(item)
    # the soonest upcoming trip first, then past trips newest first: the order a traveler thinks in
    out.sort(key=lambda x: (0, x["_ts"]) if x["upcoming"] else (1, -x["_ts"]))
    for x in out:
        x.pop("_ts", None)
    return {
        "name": cu.get("name"), "market": cu.get("market"), "currency": markets.DEMO_CURRENCY,
        "points": bal["points"], "points_spend_value": bal["spend_usd"], "points_cash_value": bal["cash_usd"],
        "rules": {"pays_from_minutes": int(rules["delay_tier1_minutes"]), "tier1_payout": rules["delay_tier1_payout"],
                  "tier2_minutes": int(rules["delay_tier2_minutes"]), "tier2_payout": rules["delay_tier2_payout"],
                  "cancel_payout": rules["cancel_payout"], "lost_bag_payout": rules["lost_bag_payout"],
                  "delay_price": rules["delay_premium"], "baggage_price": rules["baggage_premium"]},
        "trips": out,
    }


# ----------------------------------------------------------------- the assistant

def _money(n) -> str:
    return engine.money(n, markets.DEMO_CURRENCY)


def _grounded(q: str, f: dict) -> tuple[str, list[str]]:
    """The answer built from the facts, with the facts it used. Never a decision, never a promise."""
    ql = q.lower()
    r = f["rules"]
    upcoming = next((t for t in f["trips"] if t["upcoming"]), None)
    latest = f["trips"][0] if f["trips"] else None
    used = []

    def trip_line(t):
        used.append(f"trip {t['booking']} {t['flight']}")
        return f"{t['flight']}, {t['route']}, {t['departs']}"

    if any(w in ql for w in ("point", "wallet", "cash", "spend", "voucher", "qr")):
        used.append("wallet balance")
        return (f"You have {f['points']:,} KODA points. That is {_money(f['points_spend_value'])} to spend at an airport partner, "
                f"or {_money(f['points_cash_value'])} if you cash it out to a card. In the Wallet tab you pick an outlet and we make a QR code "
                "to show at the counter."), used
    if any(w in ql for w in ("bag", "luggage", "suitcase")):
        used.append("baggage rule")
        if latest and latest["bag_claim"]:
            return (f"Your lost bag claim is {latest['bag_claim']['status']}. You declared {_money(latest['bag_claim']['declared'])}. "
                    "A person always checks a bag claim, because the value of what was inside is your own declaration."), used
        return (f"Baggage cover pays up to {_money(r['lost_bag_payout'])} for a checked bag the airline does not deliver. "
                "Get the airline's report number at the baggage desk first, then report it on the trip in the app."), used
    if any(w in ql for w in ("cancel", "cancelled")):
        used.append("cancellation rule")
        return f"If your flight is cancelled, delay cover pays {_money(r['cancel_payout'])}, with no claim form.", used
    if any(w in ql for w in ("claim", "paid", "payout", "money", "how much", "when will")):
        if latest and latest["claim"]:
            cl = latest["claim"]
            used.append(f"claim on {latest['flight']}")
            if cl["status"] == "paid":
                who = "automatically" if cl["decided_by"] == "system" else f"by {cl['decided_by']} at KODA"
                return (f"Your claim on {latest['flight']} was paid {who}: {_money(cl['amount'])}, "
                        f"{cl['points']:,} points in your wallet." if cl["points"] else
                        f"Your claim on {latest['flight']} was paid {who}: {_money(cl['amount'])}."), used
            if cl["status"] == "held":
                return (f"Your claim on {latest['flight']} is with a claims officer now. It would pay {_money(cl['amount'])} if approved. "
                        "You do not need to do anything; you will get a message here when it is decided."), used
        used.append("payout rules")
        return (f"A delay of {r['pays_from_minutes']} minutes or more pays {_money(r['tier1_payout'])}, and "
                f"{r['tier2_minutes']} minutes or more pays {_money(r['tier2_payout'])}. It is paid by itself, with no claim form, "
                "as soon as the airline data shows the delay."), used
    if any(w in ql for w in ("cover", "covered", "policy", "insur", "what do i have")):
        if upcoming:
            used.append("cover on the next trip")
            return (f"On your next trip ({trip_line(upcoming)}) you have {upcoming['cover']} cover. "
                    f"Delay cover pays from {r['pays_from_minutes']} minutes and baggage cover pays up to {_money(r['lost_bag_payout'])}. "
                    "Tap the cover card on the trip to see the whole ladder."), used
        if latest:
            return f"Your most recent trip ({trip_line(latest)}) had {latest['cover']} cover.", used
        return ("You have no trip with cover right now. On the Home tab you can check a ticket you already hold, "
                "or book a flight with cover through our partner checkout."), used
    if any(w in ql for w in ("delay", "late", "risk", "chance", "on time")):
        if upcoming and "delay_chance_percent" in upcoming:
            used.append(f"forecast for {upcoming['flight']}")
            return (f"Our model puts the chance of a delay over 30 minutes on {upcoming['flight']} at about "
                    f"{upcoming['delay_chance_percent']}%. You will get an alert if it passes {forecast.ALERT_AT}%, "
                    f"and cover pays from {r['pays_from_minutes']} minutes late."), used
        if latest and latest["delay_minutes"]:
            used.append(f"status of {latest['flight']}")
            return (f"{latest['flight']} was {latest['delay_minutes']} minutes late. The rule pays from "
                    f"{r['pays_from_minutes']} minutes."), used
    if any(w in ql for w in ("buy", "add cover", "price", "cost")):
        used.append("prices")
        return (f"Delay cover is {_money(r['delay_price'])} and baggage cover is {_money(r['baggage_price'])} per flight. "
                "You can add cover to a booking you already have, any time before the flight leaves, as long as no delay is known yet."), used
    if any(w in ql for w in ("hello", "hi ", "hey", "thanks", "thank you")):
        return f"Hello {(f['name'] or 'there').split()[0]}. Ask me about your cover, a claim, or your points.", used
    used.append("payout rules")
    return ("I can explain what your cover pays, where a claim is, your points, or the delay forecast on your next flight. "
            f"As a start: a delay of {r['pays_from_minutes']} minutes or more pays {_money(r['tier1_payout'])}, paid automatically."), used


def _answer(q: str, f: dict) -> dict:
    """One answer from the assistant. Swap the body for an ADK agent and the rest of the app is unchanged."""
    text, used = _grounded(q, f)
    better = copilot.ask_gemini(
        "You are a flight insurance assistant writing to a traveler in the app. Rewrite the answer below in friendly, "
        "plain English, under 70 words. Keep every number and fact exactly. Do not promise a payout and do not decide a claim. "
        "Return only the message.\n\nTraveler asked: " + q + "\n\nAnswer: " + text)
    return {"text": better or text, "ai": bool(better), "used": used}


def wants_person(q: str) -> bool:
    ql = q.lower()
    return any(w in ql for w in HANDOVER_WORDS) or any(w in ql for w in DECISION_WORDS)


# ----------------------------------------------------------------- the traveler's side

def traveler_says(cid: str, text: str) -> dict:
    """Store the traveler's message, then either hand over to a person or answer. Gemini is called outside the lock."""
    with db.conn() as c:
        ch = open_chat(c, cid)
        chat_id = ch["id"]
        say(c, chat_id, "traveler", wallet.get(c, cid)["name"], text)
        mode, assigned = ch["mode"], ch["assigned_to"]
        handover = mode == "bot" and wants_person(text)
        if handover:
            c.execute("UPDATE chats SET waiting=1 WHERE id=?", (chat_id,))
            db.log(c, None, "chat_handover_asked", {"chat": chat_id, "customer": cid})
            say(c, chat_id, "bot", BOT_NAME,
                "I have asked a person at KODA to join this chat. They can see everything we have said. "
                "You will see their name on their messages when they reply.")
            return state(c, chat_id)
        if mode == "human":
            c.execute("UPDATE chats SET waiting=1 WHERE id=?", (chat_id,))
            return state(c, chat_id)
        f = facts(c, cid)
    a = _answer(text, f)                      # outside the lock: this may call Gemini
    with db.conn() as c:
        say(c, chat_id, "bot", BOT_NAME, a["text"], {"ai": a["ai"], "used": a["used"]})
        return state(c, chat_id)


def ask_for_person(cid: str) -> dict:
    with db.conn() as c:
        ch = open_chat(c, cid)
        c.execute("UPDATE chats SET waiting=1 WHERE id=?", (ch["id"],))
        say(c, ch["id"], "traveler", wallet.get(c, cid)["name"], "I would like to speak to a person, please.")
        say(c, ch["id"], "bot", BOT_NAME, "A person at KODA has been asked to join. You will see their name when they reply.")
        db.log(c, None, "chat_handover_asked", {"chat": ch["id"], "customer": cid})
        return state(c, ch["id"])


# ----------------------------------------------------------------- the insurer's side

def inbox(c) -> list[dict]:
    rows = db.rows(c, "SELECT ch.*, cu.name, cu.email FROM chats ch JOIN customers cu ON cu.id=ch.customer_id "
                      "WHERE ch.status='open' ORDER BY ch.waiting DESC, ch.updated_at DESC")
    for r in rows:
        last = db.one(c, "SELECT role,author,text,ts FROM chat_messages WHERE chat_id=? ORDER BY ts DESC, rowid DESC LIMIT 1", (r["id"],))
        r["last"] = last
        r["count"] = db.one(c, "SELECT COUNT(*) n FROM chat_messages WHERE chat_id=?", (r["id"],))["n"]
    return rows


def waiting_count(c) -> int:
    return db.one(c, "SELECT COUNT(*) n FROM chats WHERE status='open' AND waiting=1")["n"]


def take_over(c, chat_id: str, staff_name: str) -> dict:
    ch = db.one(c, "SELECT * FROM chats WHERE id=?", (chat_id,))
    if not ch:
        raise ValueError("Chat not found.")
    c.execute("UPDATE chats SET mode='human', assigned_to=?, waiting=0 WHERE id=?", (staff_name, chat_id))
    say(c, chat_id, "system", staff_name, f"{staff_name} from KODA has joined this chat.")
    db.log(c, None, "chat_taken_over", {"chat": chat_id, "by": staff_name})
    return state(c, chat_id)


def hand_back(c, chat_id: str, staff_name: str) -> dict:
    c.execute("UPDATE chats SET mode='bot', assigned_to=NULL, waiting=0 WHERE id=?", (chat_id,))
    say(c, chat_id, "system", staff_name, f"{staff_name} has handed this chat back to the KODA assistant.")
    db.log(c, None, "chat_handed_back", {"chat": chat_id, "by": staff_name})
    return state(c, chat_id)


def staff_says(c, chat_id: str, staff_name: str, text: str) -> dict:
    ch = db.one(c, "SELECT * FROM chats WHERE id=?", (chat_id,))
    if not ch:
        raise ValueError("Chat not found.")
    if ch["mode"] != "human":
        take_over(c, chat_id, staff_name)
    say(c, chat_id, "staff", staff_name, text)
    c.execute("UPDATE chats SET waiting=0 WHERE id=?", (chat_id,))
    return state(c, chat_id)
