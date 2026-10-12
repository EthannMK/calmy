"""Partner restaurants at the airport where KODA points can be spent.

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


def seed(c) -> None:
    for iata in context.AIRPORTS:
        for i, (name, kind, spend) in enumerate(KINDS):
            h = _h(f"{iata}{name}")
            accepts = 1 if (h % 3) else 0                    # about two in three have joined
            if i == 5:                                        # the late bar is always open late and never joined at first
                accepts = 0
            opens = "05:30" if i in (1, 2) else ("10:00" if i == 3 else "06:00")
            closes = "23:30" if i in (0, 5) else ("21:00" if i == 3 else "22:00")
            c.execute("INSERT OR REPLACE INTO partners VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (f"{iata}-{i}", iata, name, kind, f"T{1 + (h >> 4) % 2}", 2 + (h >> 8) % 9, opens, closes, accepts, spend))


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
    if joined:
        best = min(joined, key=lambda p: p["score"])
        why = f"{best['walk_min']} min walk"
        if delay:
            why += f", open through your {delay // 60}h {delay % 60:02d}m wait"
        best["pick"] = True
        best["pick_reason"] = f"Closest partner that is open for your wait ({why})."
    return {"airport": iata, "partners": sorted(joined, key=lambda p: p["score"]),
            "not_partner": [{"name": p["name"], "reason": "Has not joined KODA points" if not p["accepts"] else "Closed while you wait"} for p in others],
            "pick": best["id"] if best else None, "directory": "Prototype directory of fictional outlets"}


def balance(c, pid: str) -> dict:
    earned = c.execute("SELECT COALESCE(SUM(points),0) n FROM decisions WHERE policy_id=? AND status='paid'", (pid,)).fetchone()["n"]
    spent = c.execute("SELECT COALESCE(SUM(points),0) n FROM redemptions WHERE policy_id=?", (pid,)).fetchone()["n"]
    return {"earned": earned, "spent": spent, "balance": earned - spent}


def sync(c, pid: str) -> None:
    """Credit points on paid decisions of a policy that takes its payout as points. Derived, so safe to repeat."""
    p = c.execute("SELECT payout_account FROM policies WHERE id=?", (pid,)).fetchone()
    if not p or not (p["payout_account"] or "").startswith(markets.POINTS_RAIL):
        return
    for d in c.execute("SELECT id, amount FROM decisions WHERE policy_id=? AND status='paid'", (pid,)).fetchall():
        pts = round(d["amount"] * markets.POINTS_PER_USD * (1 + markets.POINTS_BONUS))
        c.execute("UPDATE decisions SET points=? WHERE id=?", (pts, d["id"]))


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
    rid = "RD-" + hashlib.sha256(f"{pid}{partner_id}{time.time()}".encode()).hexdigest()[:6].upper()
    code = "KP-" + hashlib.sha256(rid.encode()).hexdigest()[:4].upper() + "-" + str(points)[-2:].zfill(2)
    c.execute("INSERT INTO redemptions VALUES (?,?,?,?,?,?)", (rid, pid, partner_id, points, code, time.time()))
    return {"id": rid, "code": code, "points": points, "partner": part["name"], "terminal": part["terminal"], "walk_min": part["walk_min"]}
