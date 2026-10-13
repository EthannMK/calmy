"""Partners at the airport where KODA points can be spent: food and drink, lounges, and SIM or data packs.

Prototype directory: outlets are fictional and generated per airport. Only some have joined, so the
app filters to the ones that accept points and are open while the traveler waits. A real launch
would onboard partners through a signup flow and could find candidates with a places API.
"""
import hashlib
import time

from . import context, markets

KINDS = [
    ("Noodle Counter", "Quick meal", 14), ("Gate Bakery", "Bakery and coffee", 7), ("Sky Coffee", "Coffee", 6),
    ("Terminal Grill", "Sit-down meal", 22), ("Green Bowl", "Healthy bowls", 13), ("Last Call Bar", "Drinks and snacks", 15),
]


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


# Services other than food. (name, what it is, offer, price in SGD, opens, closes, category)
SERVICES = [
    ("Cloud Nine Lounge", "Lounge", "2 hour lounge pass with buffet, showers and Wi-Fi", 32, "05:00", "23:59", "lounge"),
    ("Quiet Pod Lounge", "Lounge", "Sleep pod for 3 hours", 24, "00:00", "23:59", "lounge"),
    ("Hello Mobile eSIM", "SIM and data", "Travel eSIM with 5 GB data, calls and messages", 9, "00:00", "23:59", "connect"),
    ("TalkHome Pack", "Calls and messages", "Call and message pack for 7 days", 4, "00:00", "23:59", "connect"),
]
CATEGORIES = [("food", "Food and drink"), ("lounge", "Lounges"), ("connect", "SIM and data")]

# Singapore Changi: the demo airport. Names are fictional demo partners, not real Changi tenants.
CHANGI_FOOD = [  # name, kind, terminal, walk, avg spend, opens, closes
    ("Kopi Kaya Corner", "Kopitiam breakfast and coffee", "T3", 3, 8, "06:00", "23:00"),
    ("Hawker Lane Chicken Rice", "Hawker meal", "T3", 5, 12, "10:00", "22:00"),
    ("Laksa Junction", "Noodles and laksa", "T2", 4, 13, "07:00", "23:30"),
    ("Satay by the Gate", "Satay and grill", "T2", 7, 18, "11:00", "23:00"),
    ("Bean Street Coffee", "Coffee and pastries", "T1", 2, 7, "05:30", "23:30"),
    ("Dim Sum Express", "Dim sum", "T1", 6, 15, "07:00", "21:30"),
]
CHANGI_SERVICES = [  # name, kind, offer, price, terminal, walk, category
    ("Merlion Rest Lounge", "Lounge", "2 hour lounge pass with buffet, showers and Wi-Fi", 32, "T3", 6, "lounge"),
    ("Orchid Sleep Pods", "Lounge", "Sleep pod for 3 hours", 24, "T2", 8, "lounge"),
    ("SingConnect eSIM", "SIM and data", "Travel eSIM with 5 GB data for 7 days", 9, "T3", 4, "connect"),
    ("Lion City SIM", "SIM and data", "Tourist SIM with 100 GB and calls for 14 days", 15, "T2", 5, "connect"),
]


def seed(c) -> None:
    cols = "(id,airport,name,kind,terminal,walk_min,opens,closes,accepts,avg_spend,category,offer,price_usd)"
    for iata in context.AIRPORTS:
        if iata == "SIN":
            for i, (name, kind, term, walk, spend, op, cl) in enumerate(CHANGI_FOOD):
                c.execute(f"INSERT OR REPLACE INTO partners {cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"SIN-{i}", "SIN", name, kind, term, walk, op, cl, 1, spend, "food", None, 0))
            for j, (name, kind, offer, price, term, walk, cat) in enumerate(CHANGI_SERVICES):
                c.execute(f"INSERT OR REPLACE INTO partners {cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                          (f"SIN-s{j}", "SIN", name, kind, term, walk, "00:00", "23:59", 1, price, cat, offer, price))
            continue
        for i, (name, kind, spend) in enumerate(KINDS):
            h = _h(f"{iata}{name}")
            accepts = 1 if (h % 3) else 0                    # about two in three have joined
            if i == 5:                                        # the late bar is always open late and never joined at first
                accepts = 0
            opens = "05:30" if i in (1, 2) else ("10:00" if i == 3 else "06:00")
            closes = "23:30" if i in (0, 5) else ("21:00" if i == 3 else "22:00")
            c.execute(f"INSERT OR REPLACE INTO partners {cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"{iata}-{i}", iata, name, kind, f"T{1 + (h >> 4) % 2}", 2 + (h >> 8) % 9, opens, closes, accepts, spend, "food", None, 0))
        for j, (name, kind, offer, price, opens, closes, cat) in enumerate(SERVICES):
            h = _h(f"{iata}{name}")
            accepts = 0 if (h % 7 == 0) else 1               # most service partners have joined
            c.execute(f"INSERT OR REPLACE INTO partners {cols} VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                      (f"{iata}-s{j}", iata, name, kind, f"T{1 + (h >> 4) % 2}", 1 + (h >> 8) % 12, opens, closes, accepts, price, cat, offer, price))


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def open_while_waiting(p: dict, scheduled: str | None, delay: int) -> bool:
    """True when the outlet is open at some point between the scheduled time and the new departure."""
    if not scheduled:
        return True
    start = _mins(scheduled[:5])
    return _mins(p["opens"]) <= start + delay and _mins(p["closes"]) >= start


def list_for(c, iata: str, scheduled: str | None, delay: int, points: int) -> dict:
    rows = [dict(r) for r in c.execute("SELECT * FROM partners WHERE airport=? ORDER BY walk_min", (iata,))]
    for p in rows:
        p["open"] = open_while_waiting(p, scheduled, delay)
    joined = [p for p in rows if p["accepts"] and p["open"]]
    others = [p for p in rows if not (p["accepts"] and p["open"])]
    best = None
    for p in joined:  # closer is better, and a spend the balance covers is better
        afford = 0 if points >= p["avg_spend"] * markets.POINTS_PER_USD else 12
        p["score"] = p["walk_min"] + afford
        p["worth"] = min(points, p["avg_spend"] * markets.POINTS_PER_USD)
        p["points_price"] = p["avg_spend"] * markets.POINTS_PER_USD
    foods = [p for p in joined if p.get("category", "food") == "food"]
    if foods:
        best = min(foods, key=lambda p: p["score"])
        why = f"{best['walk_min']} min walk"
        if delay:
            why += f", open through your {delay // 60}h {delay % 60:02d}m wait"
        best["pick"] = True
        best["pick_reason"] = f"Closest partner that is open for your wait ({why})."
    return {"airport": iata, "categories": [{"key": k, "label": v} for k, v in CATEGORIES], "partners": sorted(joined, key=lambda p: p["score"]),
            "not_partner": [{"name": p["name"], "reason": "Has not joined KODA points" if not p["accepts"] else "Closed while you wait"} for p in others],
            "pick": best["id"] if best else None, "directory": "Prototype directory of fictional outlets"}


def _customer_of(c, pid: str) -> str | None:
    r = c.execute("SELECT customer_id FROM policies WHERE id=?", (pid,)).fetchone()
    return r["customer_id"] if r else None


def balance(c, pid: str) -> dict:
    cid = _customer_of(c, pid)
    if cid:  # the wallet belongs to the traveler, not to one trip
        from . import wallet
        b = wallet.balance(c, cid)
        return {"earned": b["earned"], "spent": b["spent"] + b["cashed"], "balance": b["points"]}
    earned = c.execute("SELECT COALESCE(SUM(points),0) n FROM decisions WHERE policy_id=? AND status='paid'", (pid,)).fetchone()["n"]
    spent = c.execute("SELECT COALESCE(SUM(points),0) n FROM redemptions WHERE policy_id=?", (pid,)).fetchone()["n"]
    return {"earned": earned, "spent": spent, "balance": earned - spent}


def sync(c, pid: str) -> None:
    """Credit points on paid decisions that were paid as points. Derived, so safe to repeat."""
    cid = _customer_of(c, pid)
    if cid:
        from . import wallet
        wallet.sync(c, cid)
        return
    p = c.execute("SELECT payout_account FROM policies WHERE id=?", (pid,)).fetchone()
    if not p or not (p["payout_account"] or "").startswith(markets.POINTS_RAIL):
        return
    for d in c.execute("SELECT id, amount FROM decisions WHERE policy_id=? AND status='paid'", (pid,)).fetchall():
        pts = round(d["amount"] * markets.POINTS_PER_USD * (1 + markets.POINTS_BONUS))
        c.execute("UPDATE decisions SET points=? WHERE id=?", (pts, d["id"]))


def _voucher(pid_or_cid: str, partner_id: str, points: int) -> tuple[str, str]:
    rid = "RD-" + hashlib.sha256(f"{pid_or_cid}{partner_id}{time.time()}".encode()).hexdigest()[:6].upper()
    code = "KP-" + hashlib.sha256(rid.encode()).hexdigest()[:4].upper() + "-" + str(points)[-2:].zfill(2)
    return rid, code


def redeem(c, pid: str, partner_id: str, points: int) -> dict:
    pol = c.execute("SELECT * FROM policies WHERE id=?", (pid,)).fetchone()
    part = c.execute("SELECT * FROM partners WHERE id=?", (partner_id,)).fetchone()
    if not pol or not part:
        raise ValueError("Partner not found.")
    fl = c.execute("SELECT * FROM flights WHERE code=?", (pol["flight"],)).fetchone()
    if not fl or fl["origin"] != part["airport"]:
        raise ValueError("This outlet is not at your departure airport.")
    if not part["accepts"]:
        raise ValueError(f"{part['name']} does not accept KODA points.")
    if points < 100:
        raise ValueError("Spend at least 100 points ($1) at a time.")
    sync(c, pid)
    if points > balance(c, pid)["balance"]:
        raise ValueError("Not enough points in your wallet.")
    rid, code = _voucher(pid, partner_id, points)
    c.execute("INSERT INTO redemptions (id,policy_id,partner_id,points,code,created_at,customer_id) VALUES (?,?,?,?,?,?,?)",
              (rid, pid, partner_id, points, code, time.time(), pol["customer_id"]))
    if pol["customer_id"]:
        from . import wallet
        wallet.spend(c, pol["customer_id"], dict(part), points, code, rid)
    return {"id": rid, "code": code, "points": points, "partner": part["name"], "terminal": part["terminal"], "walk_min": part["walk_min"]}


def redeem_customer(c, cid: str, partner_id: str, points: int) -> dict:
    """Spend wallet points at any joined partner. A service with a fixed price takes exactly that price."""
    from . import wallet
    part = c.execute("SELECT * FROM partners WHERE id=?", (partner_id,)).fetchone()
    if not part:
        raise ValueError("Partner not found.")
    if not part["accepts"]:
        raise ValueError(f"{part['name']} does not accept KODA points.")
    price = (part["price_usd"] or 0) * markets.POINTS_PER_USD
    if price:
        points = price
    if points < 100:
        raise ValueError("Spend at least 100 points ($1) at a time.")
    if points > wallet.balance(c, cid)["points"]:
        raise ValueError("Not enough points in your wallet.")
    rid, code = _voucher(cid, partner_id, points)
    last = c.execute("SELECT id FROM policies WHERE customer_id=? ORDER BY created_at DESC LIMIT 1", (cid,)).fetchone()
    c.execute("INSERT INTO redemptions (id,policy_id,partner_id,points,code,created_at,customer_id) VALUES (?,?,?,?,?,?,?)",
              (rid, last["id"] if last else "", partner_id, points, code, time.time(), cid))
    wallet.spend(c, cid, dict(part), points, code, rid)
    return {"id": rid, "code": code, "points": points, "partner": part["name"], "category": part["category"], "offer": part["offer"],
            "airport": part["airport"], "terminal": part["terminal"], "walk_min": part["walk_min"], "balance": wallet.balance(c, cid)["points"]}
