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

from . import db

BASE = "http://api.aviationstack.com/v1/flights"
MONTHLY_BUDGET = int(os.environ.get("AVIATIONSTACK_BUDGET", "80"))  # keep headroom under 100
CACHE_SECONDS = 15 * 60
HUBS = ["BKK", "DMK", "CNX", "HKT"]  # Thai airports for the "really delayed now" list

_cache: dict[str, tuple[float, object]] = {}


def enabled() -> bool:
    return bool(os.environ.get("AVIATIONSTACK_KEY"))


class LiveError(Exception):
    """Raised with a message that is safe to show to the user."""


def usage(c) -> dict:
    month = time.strftime("%Y-%m")
    row = db.one(c, "SELECT calls FROM api_usage WHERE month=?", (month,))
    used = row["calls"] if row else 0
    return {"enabled": enabled(), "used": used, "budget": MONTHLY_BUDGET, "month": month}


def _count_call(c):
    month = time.strftime("%Y-%m")
    c.execute("INSERT INTO api_usage (month, calls) VALUES (?, 1) ON CONFLICT(month) DO UPDATE SET calls = calls + 1", (month,))


def _http_get(params: dict) -> dict:
    """One HTTP call. Isolated so tests can replace it."""
    url = BASE + "?" + urllib.parse.urlencode({"access_key": os.environ["AVIATIONSTACK_KEY"], **params})
    try:
        with urllib.request.urlopen(url, timeout=12) as r:
            return json.loads(r.read().decode())
    except Exception as e:  # network, timeout, bad JSON
        raise LiveError("The live flight service did not answer. Try again in a minute.") from e


def _fetch(c, params: dict, key: str):
    """Cached, budgeted fetch."""
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    if not enabled():
        raise LiveError("Live flight data is not switched on for this demo.")
    if usage(c)["used"] >= MONTHLY_BUDGET:
        raise LiveError("The monthly free allowance for live flight data is used up. The simulator still works.")
    _count_call(c)
    c.commit()  # count the call even if the request fails below
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
    }


def lookup(c, flight_iata: str) -> dict:
    """Status of one flight by number, for example TG102."""
    q = flight_iata.replace(" ", "").upper()
    if not q.isalnum() or not (3 <= len(q) <= 7):
        raise LiveError("Enter a flight number like TG102.")
    body = _fetch(c, {"flight_iata": q, "limit": 5}, "f:" + q)
    flights = [f for f in (normalize(r) for r in body.get("data", [])) if f]
    if not flights:
        raise LiveError(f"No live record found for {q}.")
    # Most relevant record: one that is flying or about to, else the latest.
    flights.sort(key=lambda f: (f["status"] not in ("active", "scheduled"), f["flight_date"] or ""), reverse=False)
    return flights[0]


def delayed_now(c, min_minutes: int = 30) -> list[dict]:
    """Flights leaving Thai hubs that are really delayed right now."""
    out = []
    for hub in HUBS[:1]:  # one hub per refresh keeps the monthly budget safe
        body = _fetch(c, {"dep_iata": hub, "limit": 100}, "d:" + hub)
        for r in body.get("data", []):
            f = normalize(r)
            if f and f["delay_minutes"] >= min_minutes and f["status"] != "cancelled":
                out.append(f)
    out.sort(key=lambda f: -f["delay_minutes"])
    return out[:8]


def upsert_flight(c, f: dict):
    """Store a real flight so the engine and screens can use it like any other."""
    c.execute(
        "INSERT INTO flights (code,airline,origin,origin_city,dest,dest_city,depart_label,scheduled,"
        "delay_minutes,travelers,source,fetched_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(code) DO UPDATE SET airline=excluded.airline, origin=excluded.origin, origin_city=excluded.origin_city, "
        "dest=excluded.dest, dest_city=excluded.dest_city, depart_label=excluded.depart_label, scheduled=excluded.scheduled, "
        "delay_minutes=excluded.delay_minutes, source='live', fetched_at=excluded.fetched_at",
        (f["code"], f["airline"], f["origin"], f["origin_city"], f["dest"], f["dest_city"], f["depart_label"],
         f["scheduled"], 0, 1, "live", time.time()),
    )
