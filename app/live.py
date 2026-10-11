"""Real flight data from the AviationStack free plan.

Why this design:
  * The free plan allows 100 requests a month and is plain HTTP only, so every
    call is made from the server, cached, and counted against a monthly budget.
  * Real delays of 3 hours or more are rare, so the demo can "replay" a flight
    that is really delayed right now. The replay is labelled as such.
  * The API's own `delay` field is not always consistent with its timestamps,
    so the delay is computed from scheduled versus actual or estimated time.

Without AVIATIONSTACK_KEY the module reports itself disabled and the app stays
on the simulator.
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import db, markets

BASE = "http://api.aviationstack.com/v1/flights"
MONTHLY_BUDGET = int(os.environ.get("AVIATIONSTACK_BUDGET", "80"))  # keep headroom under 100
CACHE_SECONDS = 15 * 60
HUBS = ["BKK", "LHR", "JFK", "SIN", "DEL", "SYD"]  # quick picks in the app; any airport code works

_cache: dict[str, tuple[float, object]] = {}


def enabled() -> bool:
    return bool(os.environ.get("AVIATIONSTACK_KEY"))


class LiveError(Exception):
    """Raised with a message that is safe to show to the user."""


def usage() -> dict:
    """Calls used this month. Opens its own short connection, so callers must not hold one open
    across a network call (the database lock would block every other request)."""
    month = time.strftime("%Y-%m")
    with db.conn() as c:
        row = db.one(c, "SELECT calls FROM api_usage WHERE month=?", (month,))
    used = row["calls"] if row else 0
    return {"enabled": enabled(), "used": used, "budget": MONTHLY_BUDGET, "month": month}


def _count_call():
    month = time.strftime("%Y-%m")
    with db.conn() as c:
        c.execute("INSERT INTO api_usage (month, calls) VALUES (?, 1) ON CONFLICT(month) DO UPDATE SET calls = calls + 1", (month,))


def _http_get(params: dict) -> dict:
    """One HTTP call. Isolated so tests can replace it."""
    url = BASE + "?" + urllib.parse.urlencode({"access_key": os.environ["AVIATIONSTACK_KEY"], **params})
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # network, timeout, bad JSON
        raise LiveError("The live flight service did not answer. Try again in a minute.") from e


def _fetch(params: dict, key: str):
    """Cached, budgeted fetch. The network call runs without the database lock."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    if not enabled():
        raise LiveError("Live flight data is not switched on for this demo.")
    if usage()["used"] >= MONTHLY_BUDGET:
        raise LiveError("The monthly free allowance for live flight data is used up. The simulator still works.")
    _count_call()  # counted before the request, so a failed call still uses allowance
    body = _http_get(params)
    if "error" in body:
        msg = (body["error"] or {}).get("message") or "request refused"
        raise LiveError(f"The live flight service said: {msg}")
    _cache[key] = (time.time(), body)
    return body


def _parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def delay_minutes(dep: dict) -> int:
    """Delay from timestamps first, falling back to the reported field."""
    sched = _parse_ts(dep.get("scheduled"))
    best = _parse_ts(dep.get("actual")) or _parse_ts(dep.get("estimated"))
    if sched and best:
        return max(0, int((best - sched).total_seconds() // 60))
    try:
        return max(0, int(dep.get("delay") or 0))
    except (TypeError, ValueError):
        return 0


def normalize(rec: dict) -> dict | None:
    """Turn one API record into the shape the flights table uses."""
    dep, arr = rec.get("departure") or {}, rec.get("arrival") or {}
    fl, al = rec.get("flight") or {}, rec.get("airline") or {}
    code = fl.get("iata")
    if fl.get("codeshared"):  # the same aircraft is also sold under another number; track the operating flight only
        return None
    if not code or not dep.get("iata") or not arr.get("iata") or not dep.get("scheduled"):
        return None
    sched = _parse_ts(dep["scheduled"])
    return {
        "code": code[:2] + " " + code[2:] if " " not in code else code,
        "airline": al.get("name") or code[:2],
        "origin": dep["iata"], "origin_city": (dep.get("airport") or dep["iata"]).replace(" International Airport", ""),
        "dest": arr["iata"], "dest_city": (arr.get("airport") or arr["iata"]).replace(" International Airport", ""),
        "depart_label": sched.strftime("%a %d %b, %H:%M") if sched else "",
        "scheduled": sched.strftime("%H:%M") if sched else "",
        "delay_minutes": delay_minutes(dep),
        "status": rec.get("flight_status") or "unknown",
        "flight_date": rec.get("flight_date"),
        "sched_ts": sched.timestamp() if sched else None,
        "market": markets.market_for_timezone(dep.get("timezone")),  # departure country decides the rules
    }


def lookup(flight_iata: str, flight_date: str | None = None) -> dict:
    """Status of one flight by number, for example TG102. Prefers the given date, else the
    flight that is flying or next to fly."""
    q = flight_iata.replace(" ", "").upper()
    if not q.isalnum() or not (3 <= len(q) <= 7):
        raise LiveError("Enter a flight number like TG102.")
    body = _fetch({"flight_iata": q, "limit": 10}, "f:" + q)
    flights = [f for f in (normalize(r) for r in body.get("data", [])) if f]
    if not flights:
        raise LiveError(f"No live record found for {q}.")
    if flight_date:
        same = [f for f in flights if f["flight_date"] == flight_date]
        if same:
            return same[0]
    now = time.time()
    # Nearest departure to now, so a number that flies daily returns today's or the next flight.
    flights.sort(key=lambda f: abs((f["sched_ts"] or now) - now))
    return flights[0]


def _board(hub: str) -> list[dict]:
    hub = hub.upper()
    if not (hub.isalpha() and len(hub) == 3):
        raise LiveError("Enter a three letter airport code such as BKK or LHR.")
    body = _fetch({"dep_iata": hub, "limit": 100}, "d:" + hub)
    return [f for f in (normalize(r) for r in body.get("data", [])) if f and f["status"] != "cancelled"]


def delayed_now(hub: str = "BKK", min_minutes: int = 30) -> list[dict]:
    """Flights leaving the airport that are really delayed right now."""
    out = [f for f in _board(hub) if f["delay_minutes"] >= min_minutes]
    out.sort(key=lambda f: -f["delay_minutes"])
    return out[:8]


def upcoming(hub: str = "BKK", limit: int = 12) -> list[dict]:
    """Next departures from an airport, soonest first. Shares one cached call with delayed_now."""
    now = time.time()
    out = [f for f in _board(hub) if f["sched_ts"] and f["sched_ts"] > now - 3600 and f["status"] in ("scheduled", "active", "unknown")]
    out.sort(key=lambda f: f["sched_ts"])
    seen, uniq = set(), []
    for f in out:  # one row per aircraft slot
        k = (f["sched_ts"], f["dest"])
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    return uniq[:limit]


def upsert_flight(c, f: dict):
    """Store or refresh a real flight so the engine and screens treat it like any other."""
    c.execute(
        "INSERT INTO flights (code,airline,origin,origin_city,dest,dest_city,depart_label,scheduled,"
        "delay_minutes,travelers,source,fetched_at,flight_date,sched_ts,market) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET airline=excluded.airline, origin=excluded.origin, origin_city=excluded.origin_city, "
        "dest=excluded.dest, dest_city=excluded.dest_city, depart_label=excluded.depart_label, scheduled=excluded.scheduled, "
        "delay_minutes=excluded.delay_minutes, source='live', fetched_at=excluded.fetched_at, "
        "flight_date=excluded.flight_date, sched_ts=excluded.sched_ts, market=excluded.market",
        (f["code"], f["airline"], f["origin"], f["origin_city"], f["dest"], f["dest_city"], f["depart_label"],
         f["scheduled"], f["delay_minutes"], 0, "live", time.time(), f.get("flight_date"), f.get("sched_ts"),
         f.get("market", "GLOBAL")),
    )
