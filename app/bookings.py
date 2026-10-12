"""Booking records and the "Check my cover" lookup.

A booking is the traveler's ticket, bought from an airline, Trip.com, Agoda or another travel site. KODA does not
sell tickets. It sells cover, and the booking reference ties the two together. In this prototype the bookings table
stands in for the airline or booking site. A launch would verify the reference through their APIs.

Four demo references show the four ways a traveler can be covered:
  BOTH42  delay and baggage cover on both legs of a round trip
  DLY731  delay cover only
  BAG518  baggage cover only
  NONE26  a booking with no cover at all
"""
import re
import time

from . import db

DEMO_REFS = {
    "BOTH42": ("Pim Wongsakul", "Delay and baggage cover on both legs of a round trip"),
    "DLY731": ("Marcus Tan", "Delay cover only. A lost bag is not covered"),
    "BAG518": ("Aiko Nakamura", "Baggage cover only. A delay is not covered"),
    "NONE26": ("Daniel Okafor", "A booking with no cover yet"),
}


def clean_ref(ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", ref or "").upper()


def _surname_ok(passenger: str, surname: str) -> bool:
    s = (surname or "").strip().lower()
    if not s:
        return False
    parts = [t for t in re.split(r"[\s.]+", passenger.lower()) if t]
    return s == passenger.lower() or s in parts


def cover_kind(delay: bool, bag: bool) -> str:
    return "both" if delay and bag else "delay" if delay else "baggage" if bag else "none"


def leg_view(c, b: dict) -> dict:
    """One flight of a booking with its cover and what has happened to it so far."""
    f = db.one(c, "SELECT * FROM flights WHERE code=?", (b["flight"],)) or {}
    pol = db.one(c, "SELECT * FROM policies WHERE pnr=? AND flight=? AND status IN ('active','archived') ORDER BY created_at DESC",
                 (b["ref"], b["flight"]))
    now = time.time()
    departed = bool(f.get("sched_ts") and f["sched_ts"] < now)
    delay = int(f.get("delay_minutes") or 0)
    cancelled = bool(f.get("cancelled"))
    out = {"leg": b["leg"], "flight": {k: f.get(k) for k in ("code", "airline", "origin", "origin_city", "dest", "dest_city", "depart_label", "scheduled", "sched_ts", "delay_minutes", "cancelled", "market")},
           "departed": departed, "policy": None, "cover": "none", "delay_decision": None, "baggage_claim": None, "can_add": False, "missing": []}
    if pol:
        d = db.one(c, "SELECT id,status,amount,payout,points,decided_by,trigger FROM decisions WHERE policy_id=? AND kind='delay'", (pol["id"],))
        bg = db.one(c, "SELECT d.id,d.status,d.amount FROM decisions d WHERE d.policy_id=? AND d.kind='baggage'", (pol["id"],))
        out.update(policy=pol["id"], cover=cover_kind(bool(pol["delay_cover"]), bool(pol["baggage_cover"])), delay_decision=d, baggage_claim=bg)
        out["missing"] = [k for k, on in (("delay", pol["delay_cover"]), ("baggage", pol["baggage_cover"])) if not on]
    else:
        out["missing"] = ["delay", "baggage"]
    # Cover can still be added until the flight leaves, and delay cover only while no delay is known.
    addable = [] if (departed or cancelled) else [m for m in out["missing"] if not (m == "delay" and delay >= 30)]
    out["missing_addable"], out["can_add"] = addable, bool(addable)
    out["delayed"] = delay >= 30 or cancelled
    return out


def lookup(c, ref: str, surname: str) -> dict | None:
    ref = clean_ref(ref)
    legs = db.rows(c, "SELECT * FROM bookings WHERE ref=? ORDER BY leg", (ref,))
    if not legs or not _surname_ok(legs[0]["passenger"], surname):
        return None
    b0 = legs[0]
    return {"ref": ref, "passenger": b0["passenger"], "trip": b0["trip"], "channel": b0["channel"], "customer_id": b0["customer_id"],
            "legs": [leg_view(c, b) for b in legs]}


def match_for_policy(c, ref: str, name: str, flight_code: str) -> dict | None:
    """The booking leg a new policy is for, when the reference, the name and the flight all agree."""
    ref = clean_ref(ref)
    if not ref:
        return None
    for b in db.rows(c, "SELECT * FROM bookings WHERE ref=? AND flight=?", (ref, flight_code)):
        if _surname_ok(b["passenger"], (name or "").strip().split(" ")[-1] if name else ""):
            return b
    return None


def stats(c) -> dict:
    """Book-level numbers for the console overview."""
    now = time.time()
    customers = db.one(c, "SELECT COUNT(*) n FROM customers")["n"]
    refs = db.one(c, "SELECT COUNT(DISTINCT ref) n FROM bookings")["n"]
    uncovered = db.one(c, "SELECT COUNT(DISTINCT b.ref) n FROM bookings b WHERE NOT EXISTS "
                          "(SELECT 1 FROM policies p WHERE p.pnr=b.ref AND p.status IN ('active','archived'))")["n"]
    delayed_bare = db.one(c, "SELECT COUNT(*) n FROM bookings b JOIN flights f ON f.code=b.flight WHERE f.delay_minutes>=30 AND NOT EXISTS "
                             "(SELECT 1 FROM policies p WHERE p.pnr=b.ref AND p.flight=b.flight AND p.delay_cover=1 AND p.status IN ('active','archived'))")["n"]
    return {"customers": customers, "bookings": refs, "uncovered_bookings": uncovered, "delayed_no_cover": delayed_bare}
