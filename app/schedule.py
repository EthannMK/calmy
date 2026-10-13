"""Saved real flight schedule, used for dates that live data cannot answer and as an offline fallback.

The free live data plan only knows flights that fly today. A traveler who books a trip a few weeks ahead
(on this app, or through Trip.com, Agoda or the airline) still needs to pick a real flight on that date. So
tools/sync_flights.py saves the real departures it sees into BigQuery and into data/flights_snapshot.json.
This module reads that file and projects each service onto the days of the next month. It assumes the
service flies daily, which is true for most routes but not all, so the screens call these "scheduled
flights" and the live status is checked closer to the day.

If the live service is down during a presentation, the same file keeps the flight picker working.
"""
import json
import os
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

PATH = os.environ.get("KODA_SNAPSHOT") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "flights_snapshot.json")
MAX_DAYS = 30            # how far ahead a trip can be picked
_cache: dict = {"mtime": None, "data": None}


def _load() -> dict | None:
    """The snapshot file, re-read only when it changes. None when there is no usable file."""
    try:
        m = os.path.getmtime(PATH)
    except OSError:
        return _builtin()
    if _cache["mtime"] != m:
        try:
            with open(PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            _cache.update(mtime=m, data=d if d.get("flights") else None)
        except (OSError, ValueError):
            _cache.update(mtime=m, data=None)
    return _cache["data"] or _builtin()


def _builtin() -> dict | None:
    """Sample schedule for when there is no saved real one. Switched off with KODA_NO_SAMPLE_SCHEDULE (used by tests)."""
    if os.environ.get("KODA_NO_SAMPLE_SCHEDULE"):
        return None
    if "builtin" not in _cache:
        from . import sample_schedule
        _cache["builtin"] = sample_schedule.build()
    return _cache["builtin"]


def available() -> bool:
    return _load() is not None


def info() -> dict:
    d = _load()
    if not d:
        return {"available": False, "flights": 0, "generated_at": None, "airports": []}
    return {"available": True, "builtin": bool(d.get("builtin")), "flights": len(d["flights"]), "generated_at": d.get("generated_at"),
            "airports": sorted({f["origin"] for f in d["flights"]})}


def window(today: date | None = None) -> tuple[str, str]:
    t = today or date.today()
    return t.isoformat(), (t + timedelta(days=MAX_DAYS)).isoformat()


def _project(s: dict, day: date) -> dict | None:
    """One saved service on one day, with the departure time read in the airport's own time zone."""
    try:
        hh, mm = (int(x) for x in s["time"].split(":"))
        when = datetime(day.year, day.month, day.day, hh, mm, tzinfo=ZoneInfo(s.get("tz") or "UTC"))
    except (KeyError, ValueError, TypeError, Exception):
        return None
    return {
        "code": s["code"], "airline": s["airline"], "origin": s["origin"], "origin_city": s["origin_city"],
        "dest": s["dest"], "dest_city": s["dest_city"],
        "depart_label": when.strftime("%a %d %b, %H:%M"), "scheduled": when.strftime("%H:%M"),
        "delay_minutes": 0, "status": "scheduled", "flight_date": day.isoformat(),
        "sched_ts": when.timestamp(), "market": s.get("market") or "GLOBAL", "fetched_at": 0,
    }


def valid_date(day: str) -> date | None:
    """The day as a date, only when it lies between today and a month ahead."""
    try:
        d = date.fromisoformat(day)
    except (TypeError, ValueError):
        return None
    lo, hi = date.today() - timedelta(days=1), date.today() + timedelta(days=MAX_DAYS)  # a day of slack for time zones
    return d if lo <= d <= hi else None


def for_date(origin: str, day: str, limit: int = 30) -> list[dict]:
    """Flights leaving an airport on a day, earliest first. Past departures are dropped."""
    d, dt = _load(), valid_date(day)
    if not d or not dt:
        return []
    now = time.time()
    out = [p for s in d["flights"] if s["origin"] == origin.upper() and (p := _project(s, dt)) and p["sched_ts"] > now]
    out.sort(key=lambda f: f["sched_ts"])
    return out[:limit]


def find(code: str, day: str) -> dict | None:
    """One saved flight on one day, for example TG 102 on 2026-10-20."""
    d, dt = _load(), valid_date(day)
    q = code.replace(" ", "").upper()
    if not d or not dt:
        return None
    for s in d["flights"]:
        if s["code"].replace(" ", "").upper() == q:
            p = _project(s, dt)
            if p and p["sched_ts"] > time.time():
                return p
    return None
