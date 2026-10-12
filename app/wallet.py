"""Customer accounts: profile, saved payment methods, the points wallet and cash-out.

How payouts reach a traveler:
  * By default a payout arrives as KODA points, worth 25% more than the cash amount, to spend at airport partners.
  * A traveler can save a card or a bank account. The first one saved switches on "auto top-up", so later
    payouts go straight to that card or account as cash. It can be switched off at any time.
  * Points can be turned into cash at any time. Cash-out returns the original payout (1 point = 0.8 cents),
    because the 25% bonus only exists as spending power at partners. That keeps points and cash fair to each other.

Only the last four digits and a label are stored. The prototype never keeps a full card or account number.
"""
import re
import time
import uuid

from . import db, markets

CASHOUT_RATE = 1 / (1 + markets.POINTS_BONUS)  # cash value of one point relative to its spending value
MIN_CASHOUT = 500  # points
BANKS = ["Siam Commercial Bank", "Bangkok Bank", "Kasikorn Bank", "Krungthai Bank", "DBS Bank", "HSBC", "Barclays", "Chase"]


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:5].upper()}"


# ------------------------------------------------------------------ customers

def create(c, name: str, email: str | None = None, phone: str | None = None, market: str = "TH") -> str:
    cid = _id("CU")
    c.execute("INSERT INTO customers (id,name,email,phone,market,auto_topup,notify,created_at) VALUES (?,?,?,?,?,0,1,?)",
              (cid, name.strip(), email, phone, market, time.time()))
    return cid


def get(c, cid: str | None) -> dict | None:
    return db.one(c, "SELECT * FROM customers WHERE id=?", (cid,)) if cid else None


def update(c, cid: str, name=None, email=None, phone=None, notify=None, auto_topup=None) -> dict:
    cu = get(c, cid)
    if not cu:
        raise ValueError("Account not found.")
    if name is not None:
        if len(name.strip()) < 2:
            raise ValueError("Enter your name.")
        cu["name"] = name.strip()
    if email is not None:
        e = email.strip()
        if e and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", e):
            raise ValueError("Enter a valid email address, for example name@example.com.")
        cu["email"] = e or None
    if phone is not None:
        ph = re.sub(r"[^\d+]", "", phone)
        if ph and not 7 <= len(ph) <= 16:
            raise ValueError("Enter a phone number with 7 to 15 digits.")
        cu["phone"] = ph or None
    if notify is not None:
        cu["notify"] = int(bool(notify))
    if auto_topup is not None:
        if auto_topup and not default_method(c, cid):
            raise ValueError("Save a card or bank account first, then switch on auto top-up.")
        cu["auto_topup"] = int(bool(auto_topup))
    c.execute("UPDATE customers SET name=?, email=?, phone=?, notify=?, auto_topup=? WHERE id=?",
              (cu["name"], cu["email"], cu["phone"], cu["notify"], cu["auto_topup"], cid))
    # The name on the account is what an insurer sees on every policy of this traveler.
    c.execute("UPDATE policies SET traveler=? WHERE customer_id=?", (cu["name"], cid))
    return cu


# ------------------------------------------------------------------ payment methods

def _luhn(num: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(num)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _brand(num: str) -> str:
    if num.startswith("4"):
        return "Visa"
    if re.match(r"^(5[1-5]|2[2-7])", num):
        return "Mastercard"
    if num.startswith(("34", "37")):
        return "Amex"
    return "Card"


def methods(c, cid: str) -> list[dict]:
    return db.rows(c, "SELECT * FROM payment_methods WHERE customer_id=? ORDER BY is_default DESC, created_at", (cid,))


def default_method(c, cid: str) -> dict | None:
    return db.one(c, "SELECT * FROM payment_methods WHERE customer_id=? ORDER BY is_default DESC, created_at LIMIT 1", (cid,))


def add_method(c, cid: str, kind: str, number: str, holder: str, bank: str | None = None, expiry: str | None = None) -> dict:
    """Save a card or bank account. The first one saved becomes the default and switches auto top-up on."""
    if not get(c, cid):
        raise ValueError("Account not found.")
    digits = re.sub(r"\D", "", number or "")
    if len(holder.strip()) < 2:
        raise ValueError("Enter the name on the " + ("card." if kind == "card" else "account."))
    if kind == "card":
        if not 13 <= len(digits) <= 19 or not _luhn(digits):
            raise ValueError("That card number does not look right. Check the digits.")
        m = re.fullmatch(r"(\d{2})\s*/\s*(\d{2})", (expiry or "").strip())
        if not m or not 1 <= int(m.group(1)) <= 12:
            raise ValueError("Enter the expiry date as MM/YY.")
        now = time.gmtime()
        if (2000 + int(m.group(2)), int(m.group(1))) < (now.tm_year, now.tm_mon):
            raise ValueError("This card has expired.")
        label = f"{_brand(digits)} ending {digits[-4:]}"
    elif kind == "bank":
        if not 6 <= len(digits) <= 17:
            raise ValueError("Enter the account number, 6 to 17 digits.")
        if not bank or bank not in BANKS:
            raise ValueError("Pick your bank.")
        label = f"{bank} ending {digits[-4:]}"
    else:
        raise ValueError("Choose card or bank account.")
    if db.one(c, "SELECT 1 FROM payment_methods WHERE customer_id=? AND label=?", (cid, label)):
        raise ValueError("This is already saved.")
    first = not methods(c, cid)
    mid = _id("PM")
    c.execute("INSERT INTO payment_methods (id,customer_id,kind,label,holder,last4,is_default,created_at) VALUES (?,?,?,?,?,?,?,?)",
              (mid, cid, kind, label, holder.strip(), digits[-4:], int(first), time.time()))
    if first:
        c.execute("UPDATE customers SET auto_topup=1 WHERE id=?", (cid,))
    refresh_policies(c, cid)
    return db.one(c, "SELECT * FROM payment_methods WHERE id=?", (mid,)) | {"first": first}


def remove_method(c, cid: str, mid: str) -> None:
    m = db.one(c, "SELECT * FROM payment_methods WHERE id=? AND customer_id=?", (mid, cid))
    if not m:
        raise ValueError("Payment method not found.")
    c.execute("DELETE FROM payment_methods WHERE id=?", (mid,))
    rest = methods(c, cid)
    if rest and m["is_default"]:
        c.execute("UPDATE payment_methods SET is_default=1 WHERE id=?", (rest[0]["id"],))
    if not rest:
        c.execute("UPDATE customers SET auto_topup=0 WHERE id=?", (cid,))  # nowhere to send cash, so payouts return to points
    refresh_policies(c, cid)


def set_default(c, cid: str, mid: str) -> None:
    if not db.one(c, "SELECT 1 FROM payment_methods WHERE id=? AND customer_id=?", (mid, cid)):
        raise ValueError("Payment method not found.")
    c.execute("UPDATE payment_methods SET is_default=(id=?) WHERE customer_id=?", (mid, cid))
    refresh_policies(c, cid)


# ------------------------------------------------------------------ where a payout goes

def account_for(c, cid: str | None) -> str:
    """The label a payout would go to right now for this customer."""
    cu = get(c, cid)
    if cu and cu["auto_topup"]:
        m = default_method(c, cid)
        if m:
            return m["label"]
    return f"{markets.POINTS_RAIL} wallet"


def refresh(c, p: dict) -> dict:
    """Point a policy that follows the traveler's preference at the current destination. Mutates and returns p."""
    if p.get("payout_follow") and p.get("customer_id"):
        label = account_for(c, p["customer_id"])
        if label != p.get("payout_account"):
            c.execute("UPDATE policies SET payout_account=? WHERE id=?", (label, p["id"]))
            p["payout_account"] = label
    return p


def refresh_policies(c, cid: str) -> None:
    label = account_for(c, cid)
    c.execute("UPDATE policies SET payout_account=? WHERE customer_id=? AND payout_follow=1", (label, cid))


def stamp(c, decision_id: str) -> None:
    """Remember where a paid decision went, at the moment it was paid, so a later change of preference cannot rewrite history."""
    d = db.one(c, "SELECT d.id, d.status, d.payout, p.payout_account FROM decisions d JOIN policies p ON p.id=d.policy_id WHERE d.id=?", (decision_id,))
    if d and d["status"] == "paid" and not d["payout"]:
        c.execute("UPDATE decisions SET payout=? WHERE id=?", (d["payout_account"], decision_id))


# ------------------------------------------------------------------ points

def points_for(amount: float) -> int:
    return round(amount * markets.POINTS_PER_USD * (1 + markets.POINTS_BONUS))


def sync(c, cid: str) -> None:
    """Credit points for every paid decision of this customer that was paid as points. Safe to repeat."""
    rows = db.rows(c, "SELECT d.id, d.amount, d.payout, d.created_at, d.kind, p.flight, p.payout_account FROM decisions d "
                      "JOIN policies p ON p.id=d.policy_id WHERE p.customer_id=? AND d.status='paid'", (cid,))
    for d in rows:
        rail = d["payout"] or d["payout_account"] or ""
        pts = points_for(d["amount"]) if rail.startswith(markets.POINTS_RAIL) else 0
        c.execute("UPDATE decisions SET points=? WHERE id=?", (pts, d["id"]))
        if pts:
            what = {"delay": "Delay payout", "baggage": "Lost bag payout", "expense": "Extra costs payout"}.get(d["kind"], "Payout")
            c.execute("INSERT INTO wallet_tx (id,customer_id,kind,points,usd,label,created_at) VALUES (?,?,?,?,?,?,?) "
                      "ON CONFLICT(id) DO UPDATE SET points=excluded.points, usd=excluded.usd",
                      ("earn:" + d["id"], cid, "earn", pts, d["amount"], f"{what}, {d['flight']}", d["created_at"] or time.time()))


def balance(c, cid: str) -> dict:
    sync(c, cid)
    r = db.one(c, "SELECT COALESCE(SUM(points),0) bal, COALESCE(SUM(CASE WHEN kind='earn' THEN points END),0) earned, "
                  "COALESCE(-SUM(CASE WHEN kind='spend' THEN points END),0) spent, "
                  "COALESCE(-SUM(CASE WHEN kind='cashout' THEN points END),0) cashed FROM wallet_tx WHERE customer_id=?", (cid,))
    pts = int(r["bal"])
    return {"points": pts, "earned": int(r["earned"]), "spent": int(r["spent"]), "cashed": int(r["cashed"]),
            "spend_usd": round(pts / markets.POINTS_PER_USD, 2), "cash_usd": round(pts / markets.POINTS_PER_USD * CASHOUT_RATE, 2)}


def ledger(c, cid: str, limit: int = 30) -> list[dict]:
    sync(c, cid)
    return db.rows(c, "SELECT * FROM wallet_tx WHERE customer_id=? ORDER BY created_at DESC LIMIT ?", (cid, limit))


def cash_out(c, cid: str, points: int, method_id: str) -> dict:
    m = db.one(c, "SELECT * FROM payment_methods WHERE id=? AND customer_id=?", (method_id, cid))
    if not m:
        raise ValueError("Choose a saved card or bank account.")
    if points < MIN_CASHOUT:
        raise ValueError(f"Cash out at least {MIN_CASHOUT:,} points.")
    if points > balance(c, cid)["points"]:
        raise ValueError("You do not have that many points.")
    usd = round(points / markets.POINTS_PER_USD * CASHOUT_RATE, 2)
    rid = _id("CO")
    c.execute("INSERT INTO wallet_tx (id,customer_id,kind,points,usd,label,created_at) VALUES (?,?,?,?,?,?,?)",
              ("cash:" + rid, cid, "cashout", -points, usd, f"Cashed out to {m['label']}", time.time()))
    return {"id": rid, "points": points, "usd": usd, "to": m["label"], "balance": balance(c, cid)["points"]}


def spend(c, cid: str, partner: dict, points: int, code: str, rid: str) -> None:
    c.execute("INSERT INTO wallet_tx (id,customer_id,kind,points,usd,label,created_at) VALUES (?,?,?,?,?,?,?)",
              ("spend:" + rid, cid, "spend", -points, round(points / markets.POINTS_PER_USD, 2), f"{partner['name']}, {partner['airport']}", time.time()))


def vouchers(c, cid: str, limit: int = 8) -> list[dict]:
    rows = db.rows(c, "SELECT r.id, r.code, r.points, r.created_at, t.name partner, t.airport FROM redemptions r JOIN partners t ON t.id=r.partner_id "
                      "WHERE r.customer_id=? ORDER BY r.created_at DESC LIMIT ?", (cid, limit))
    for r in rows:
        r["refunded"] = bool(db.one(c, "SELECT 1 FROM wallet_tx WHERE id=?", ("refund:" + r["id"],)))
    return rows


def refund_voucher(c, cid: str, rid: str) -> dict:
    """Give the points back for a voucher that was not used. Only the owner can, and only once."""
    r = db.one(c, "SELECT r.*, t.name partner FROM redemptions r JOIN partners t ON t.id=r.partner_id WHERE r.id=? AND r.customer_id=?", (rid, cid))
    if not r:
        raise ValueError("Voucher not found.")
    if db.one(c, "SELECT 1 FROM wallet_tx WHERE id=?", ("refund:" + rid,)):
        raise ValueError("This voucher was already refunded.")
    c.execute("INSERT INTO wallet_tx (id,customer_id,kind,points,usd,label,created_at) VALUES (?,?,?,?,?,?,?)",
              ("refund:" + rid, cid, "refund", r["points"], round(r["points"] / markets.POINTS_PER_USD, 2), f"Refund, unused voucher at {r['partner']}", time.time()))
    return {"id": rid, "points": r["points"], "balance": balance(c, cid)["points"]}


# ------------------------------------------------------------------ summary for the apps

def payouts(c, cid: str, limit: int = 20) -> list[dict]:
    """Every payout made to this customer's policies, newest first, with where it went."""
    return db.rows(c, "SELECT d.id, d.kind, d.amount, d.payout, d.points, d.created_at, d.decided_by, p.flight, p.id policy_id FROM decisions d "
                      "JOIN policies p ON p.id=d.policy_id WHERE p.customer_id=? AND d.status='paid' ORDER BY d.created_at DESC LIMIT ?", (cid, limit))


def summary(c, cid: str) -> dict:
    cu = get(c, cid)
    if not cu:
        raise ValueError("Account not found.")
    bal = balance(c, cid)
    return {
        "customer": cu, "balance": bal, "methods": methods(c, cid), "ledger": ledger(c, cid),
        "payout_to": account_for(c, cid), "payouts": payouts(c, cid), "vouchers": vouchers(c, cid),
        "banks": BANKS, "cashout_rate": CASHOUT_RATE, "min_cashout": MIN_CASHOUT,
        "trips": db.rows(c, "SELECT p.id, p.flight, p.status, p.delay_cover, p.baggage_cover, p.pnr, f.origin, f.dest, f.depart_label, f.sched_ts, f.delay_minutes "
                            "FROM policies p LEFT JOIN flights f ON f.code=p.flight WHERE p.customer_id=? AND p.status IN ('active','archived') "
                            "ORDER BY COALESCE(f.sched_ts, p.created_at) DESC", (cid,)),
    }
