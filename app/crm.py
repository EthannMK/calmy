"""Console endpoints for customers, points and partners, support chats, and the insurer staff profile."""
import time

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field

from . import bookings, db, markets, wallet

router = APIRouter(prefix="/api/console")


def _cover(rows):
    d = any(r["delay_cover"] for r in rows)
    b = any(r["baggage_cover"] for r in rows)
    return bookings.cover_kind(d, b)


PARTNER_CHANNEL = "KODA partner checkout"


def _source(pols: list[dict], bookings_rows: list[dict]) -> str:
    """How this customer came to us.

    ticket_and_cover  bought the ticket and the cover together through our own checkout
    cover_only        already had a ticket from an airline or a travel site, and bought cover from us
    both              has done both
    no_cover          has a booking with us but has never bought cover
    """
    if not pols:
        return "no_cover" if bookings_rows else "none"
    channels = {(p.get("channel") or "").strip() for p in pols}
    ours = PARTNER_CHANNEL in channels
    elsewhere = any(ch and ch != PARTNER_CHANNEL for ch in channels)
    if ours and elsewhere:
        return "both"
    if ours:
        return "ticket_and_cover"
    return "cover_only"


@router.get("/customers")
def customers(q: str = "", cover: str = "", payout: str = "", flag: str = "", source: str = ""):
    """Every customer with what they hold and what they have been paid.

    Filters: search, cover type, payout route, risk flag, and how they came to us (cover only on an outside ticket,
    or the ticket and the cover together through our own checkout).
    """
    with db.conn() as c:
        out = []
        for cu in db.rows(c, "SELECT * FROM customers ORDER BY created_at DESC, id"):
            pols = db.rows(c, "SELECT id,flight,delay_cover,baggage_cover,status,pnr,channel FROM policies WHERE customer_id=? AND status IN ('active','archived')", (cu["id"],))
            bks = db.rows(c, "SELECT ref, channel FROM bookings WHERE customer_id=?", (cu["id"],))
            kind = _cover(pols) if pols else "none"
            paid = db.rows(c, "SELECT d.amount, d.kind, d.status FROM decisions d JOIN policies p ON p.id=d.policy_id WHERE p.customer_id=?", (cu["id"],))
            bal = wallet.balance(c, cu["id"])
            trips = len({b["ref"] for b in bks})
            held = sum(1 for d in paid if d["status"] in ("held", "pending"))
            row = {"id": cu["id"], "name": cu["name"], "email": cu["email"], "phone": cu["phone"], "market": cu["market"],
                   "cover": kind, "policies": len(pols), "trips": trips,
                   "paid_usd": round(sum(d["amount"] for d in paid if d["status"] == "paid"), 2),
                   "claims": len(paid), "open_claims": held,
                   "points": bal["points"], "payout": "cash" if cu["auto_topup"] else "points", "source": _source(pols, bks),
                   "channels": sorted({(x.get("channel") or "").strip() for x in [*pols, *bks] if x.get("channel")}),
                   "joined": cu["created_at"]}
            hay = f"{cu['id']} {cu['name']} {cu['email'] or ''} {' '.join(p['pnr'] or '' for p in pols)} {' '.join(p['flight'] for p in pols)}".lower()
            if q and q.strip().lower() not in hay:
                continue
            if cover and row["cover"] != cover:
                continue
            if payout and row["payout"] != payout:
                continue
            if flag == "open" and not held:
                continue
            if source and row["source"] != source and not (source != "none" and row["source"] == "both"):
                continue
            out.append(row)
        return out


@router.get("/customers/{cid}")
def customer(cid: str):
    with db.conn() as c:
        cu = wallet.get(c, cid)
        if not cu:
            raise HTTPException(404, "customer not found")
        s = wallet.summary(c, cid)
        pols = db.rows(c, "SELECT p.*, f.origin, f.dest, f.depart_label, f.delay_minutes FROM policies p LEFT JOIN flights f ON f.code=p.flight "
                          "WHERE p.customer_id=? AND p.status IN ('active','archived') ORDER BY p.created_at DESC", (cid,))
        for p in pols:
            p["cover"] = bookings.cover_kind(bool(p["delay_cover"]), bool(p["baggage_cover"]))
            p["decisions"] = db.rows(c, "SELECT id,kind,status,amount,payout,decided_by,risk_score,delay_minutes FROM decisions WHERE policy_id=?", (p["id"],))
        refs = db.rows(c, "SELECT * FROM bookings WHERE customer_id=? ORDER BY ref, leg", (cid,))
        flags = []
        for m in s["methods"]:
            n = db.one(c, "SELECT COUNT(DISTINCT customer_id) n FROM payment_methods WHERE label=?", (m["label"],))["n"]
            if n > 1:
                flags.append(f"{m['label']} is saved on {n} different customer accounts")
        if any(d["status"] == "held" for p in pols for d in p["decisions"]):
            flags.append("A claim is waiting for a person to review it")
        return {**s, "policies": pols, "bookings": refs, "flags": flags, "source": _source(pols, refs)}


@router.get("/points")
def points():
    """What KODA owes customers in points, what has been spent and cashed out, and the partner directory."""
    with db.conn() as c:
        ids = [r["id"] for r in db.rows(c, "SELECT id FROM customers")]
        bal = [wallet.balance(c, i) for i in ids]
        liability = sum(b["points"] for b in bal)
        tx = db.rows(c, "SELECT t.*, cu.name FROM wallet_tx t JOIN customers cu ON cu.id=t.customer_id WHERE t.kind IN ('spend','cashout') ORDER BY t.created_at DESC LIMIT 60")
        for t in tx:   # a spend makes a voucher: say whether the outlet has scanned it yet
            if t["kind"] == "spend":
                r = db.one(c, "SELECT status, code FROM redemptions WHERE id=?", (t["id"].split(":", 1)[-1],))
                t["voucher"] = (r or {}).get("status") or "ready"
                t["code"] = (r or {}).get("code")
        partners = db.rows(c, "SELECT p.*, (SELECT COUNT(*) FROM redemptions r WHERE r.partner_id=p.id) used, "
                              "(SELECT COALESCE(SUM(points),0) FROM redemptions r WHERE r.partner_id=p.id) pts FROM partners p ORDER BY p.airport, p.kind, p.name")
        return {
            "per_usd": markets.POINTS_PER_USD, "bonus": markets.POINTS_BONUS, "cashout_rate": wallet.CASHOUT_RATE, "min_cashout": wallet.MIN_CASHOUT,
            "liability_points": liability, "liability_usd": round(liability / markets.POINTS_PER_USD * wallet.CASHOUT_RATE, 2),
            "holders": sum(1 for b in bal if b["points"] > 0),
            "earned": sum(b["earned"] for b in bal), "spent": sum(b["spent"] for b in bal), "cashed": sum(b["cashed"] for b in bal),
            "activity": tx, "partners": partners,
        }


# ---------------------------------------------------------------- support chat

@router.get("/chats")
def chats():
    """Open support chats, the ones waiting for a person first."""
    from . import chat
    with db.conn() as c:
        return {"chats": chat.inbox(c), "waiting": chat.waiting_count(c), "bot": chat.BOT_NAME}


@router.get("/chats/{chat_id}")
def chat_one(chat_id: str):
    from . import chat
    with db.conn() as c:
        if not db.one(c, "SELECT 1 FROM chats WHERE id=?", (chat_id,)):
            raise HTTPException(404, "Chat not found.")
        cu = db.one(c, "SELECT cu.id, cu.name, cu.email, cu.phone, cu.market FROM chats ch JOIN customers cu ON cu.id=ch.customer_id WHERE ch.id=?", (chat_id,))
        return {**chat.state(c, chat_id), "customer": cu, "facts": chat.facts(c, cu["id"])}


class ChatReply(BaseModel):
    text: str | None = Field(None, max_length=600)
    action: str | None = None          # take_over | hand_back


@router.post("/chats/{chat_id}")
def chat_reply(chat_id: str, body: ChatReply, x_staff: str | None = Header(None)):
    """Take over a chat, reply as yourself, or hand it back to the assistant."""
    from . import chat
    with db.conn() as c:
        st = db.one(c, "SELECT name FROM staff WHERE id=?", (x_staff,)) if x_staff else None
        who = st["name"] if st else "KODA"
        try:
            if body.action == "take_over":
                return chat.take_over(c, chat_id, who)
            if body.action == "hand_back":
                return chat.hand_back(c, chat_id, who)
            if not (body.text or "").strip():
                raise ValueError("Write a message first.")
            return chat.staff_says(c, chat_id, who, body.text.strip())
        except ValueError as e:
            raise HTTPException(400, str(e))


class Accept(BaseModel):
    accepts: bool


@router.put("/partners/{pid}")
def partner_accepts(pid: str, body: Accept):
    with db.conn() as c:
        p = db.one(c, "SELECT * FROM partners WHERE id=?", (pid,))
        if not p:
            raise HTTPException(404, "partner not found")
        c.execute("UPDATE partners SET accepts=? WHERE id=?", (int(body.accepts), pid))
        db.log(c, None, "partner_updated", {"partner": p["name"], "airport": p["airport"], "accepts": body.accepts})
        return {**p, "accepts": int(body.accepts)}


# ---------------------------------------------------------------- staff

class StaffUpdate(BaseModel):
    name: str | None = None
    title: str | None = None
    email: str | None = None
    notify: bool | None = None


@router.get("/staff")
def staff():
    with db.conn() as c:
        rows = db.rows(c, "SELECT * FROM staff ORDER BY name")
        for r in rows:
            r["decisions"] = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE decided_by=?", (r["name"],))["n"]
        return rows


@router.get("/staff/me")
def staff_me(x_staff: str | None = Header(None)):
    with db.conn() as c:
        st = db.one(c, "SELECT * FROM staff WHERE id=?", (x_staff,)) if x_staff else None
        st = st or db.one(c, "SELECT * FROM staff ORDER BY rowid LIMIT 1")
        if not st:
            raise HTTPException(404, "no staff")
        st["decisions"] = db.one(c, "SELECT COUNT(*) n FROM decisions WHERE decided_by=?", (st["name"],))["n"]
        st["waiting"] = db.one(c, "SELECT (SELECT COUNT(*) FROM decisions WHERE status='held' AND kind='delay') + "
                                  "(SELECT COUNT(*) FROM decisions WHERE status='pending' AND kind IN ('baggage','expense')) n")["n"]
        return st


@router.put("/staff/{sid}")
def staff_update(sid: str, body: StaffUpdate):
    import re
    with db.conn() as c:
        st = db.one(c, "SELECT * FROM staff WHERE id=?", (sid,))
        if not st:
            raise HTTPException(404, "staff not found")
        if body.name is not None:
            n = body.name.strip()
            if len(n) < 2 or len(n) > 40:
                raise HTTPException(400, "Enter a name between 2 and 40 characters.")
            # Past decisions keep the name they were signed with, renamed so the history still reads true.
            c.execute("UPDATE decisions SET decided_by=? WHERE decided_by=?", (n, st["name"]))
            st["name"] = n
            st["initials"] = "".join(w[0] for w in n.replace(".", " ").split()[:2]).upper()
        if body.title is not None:
            st["title"] = body.title.strip()[:60]
        if body.email is not None:
            e = body.email.strip()
            if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
                raise HTTPException(400, "Enter a valid work email, for example name@koda.example.")
            st["email"] = e
        if body.notify is not None:
            st["notify"] = int(body.notify)
        c.execute("UPDATE staff SET name=?,title=?,email=?,initials=?,notify=? WHERE id=?",
                  (st["name"], st["title"], st["email"], st["initials"], st["notify"], sid))
        db.log(c, None, "staff_updated", {"staff": sid, "name": st["name"]})
        return st


# ---------------------------------------------------------------- airspace alerts

class Airspace(BaseModel):
    airport: str
    label: str
    level: str = "moderate"  # moderate adds 15 points to the delay chance, severe adds 30


@router.get("/airspace")
def airspace_list():
    with db.conn() as c:
        return db.rows(c, "SELECT * FROM airspace_alerts ORDER BY created_at DESC")


@router.post("/airspace")
def airspace_add(body: Airspace, x_staff: str | None = Header(None)):
    """The insurer marks an airport region as disrupted, for example closed airspace or a security incident.
    It is a person's judgement, not a scraped news feed, and it adds a readable driver to the delay forecast."""
    import re
    import secrets
    from . import forecast
    ap = body.airport.strip().upper()
    if not re.fullmatch(r"[A-Z]{3}", ap):
        raise HTTPException(400, "Enter a three letter airport code such as BKK.")
    label = body.label.strip()
    if not 3 <= len(label) <= 80:
        raise HTTPException(400, "Describe the disruption in 3 to 80 characters.")
    if body.level not in ("moderate", "severe"):
        raise HTTPException(400, "Choose moderate or severe.")
    with db.conn() as c:
        st = db.one(c, "SELECT name FROM staff WHERE id=?", (x_staff,)) if x_staff else None
        aid = "AS-" + secrets.token_hex(3).upper()
        c.execute("INSERT INTO airspace_alerts VALUES (?,?,?,?,?,?)", (aid, ap, label, 30 if body.level == "severe" else 15, (st or {}).get("name", "Staff"), time.time()))
        db.log(c, None, "airspace_alert_added", {"airport": ap, "label": label, "level": body.level})
    forecast.invalidate_airspace()
    return {"id": aid}


@router.delete("/airspace/{aid}")
def airspace_remove(aid: str):
    from . import forecast
    with db.conn() as c:
        if not c.execute("DELETE FROM airspace_alerts WHERE id=?", (aid,)).rowcount:
            raise HTTPException(404, "alert not found")
        db.log(c, None, "airspace_alert_removed", {"id": aid})
    forecast.invalidate_airspace()
    return {"ok": True}


@router.get("/model")
def model_card():
    """The forecast model's data, accuracy on unseen flights and limits."""
    from . import forecast
    return forecast.card()
