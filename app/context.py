"""Ground conditions shown next to each decision: weather and airport status.

The deck's architecture reads weather and airport data together with the policy rules.
Weather is live from Open-Meteo (free, no key) when the airport is known and the call works.
Airport operations (ground stops, queues) have no free live feed, so that line is a labelled
simulation. Both only explain the event to a reviewer. They never change who gets paid:
payout depends on the policy rule only.

The network call never runs while the database lock is held. Callers use prefetch() first,
or warm() in the background, and for_event() only reads the cache.
"""
import hashlib
import json
import os
import threading
import time
import urllib.request

AIRPORTS = {
    "BKK": (13.69, 100.75), "DMK": (13.91, 100.61), "CNX": (18.77, 98.96), "HKT": (8.11, 98.32),
    "SIN": (1.36, 103.99), "KUL": (2.75, 101.71), "HKG": (22.31, 113.91), "HND": (35.55, 139.78),
    "NRT": (35.77, 140.39), "ICN": (37.46, 126.44), "DXB": (25.25, 55.37), "BOM": (19.09, 72.87),
    "DEL": (28.56, 77.10), "LHR": (51.47, -0.45), "AMS": (52.31, 4.76), "CDG": (49.01, 2.55),
    "FRA": (50.03, 8.57), "MAD": (40.47, -3.56), "JFK": (40.64, -73.78), "LAX": (33.94, -118.41),
    "SYD": (-33.94, 151.18), "MEL": (-37.67, 144.84), "YVR": (49.19, -123.18), "YYZ": (43.68, -79.63),
    "GRU": (-23.43, -46.47), "GIG": (-22.81, -43.25),
}
_CODES = {0: "Clear sky", 1: "Mostly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Freezing fog",
          51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 61: "Light rain", 63: "Rain", 65: "Heavy rain",
          71: "Light snow", 73: "Snow", 75: "Heavy snow", 80: "Rain showers", 81: "Rain showers", 82: "Violent showers",
          95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail"}
_SEVERE = {45, 48, 65, 75, 82, 95, 96, 99}
TTL = 600
LAST = {"ok": None, "error": None}  # outcome of the latest weather call, shown in the demo panel
_cache: dict[str, tuple[float, dict]] = {}
_busy: set[str] = set()
_lock = threading.Lock()


def fetch(origin: str) -> dict | None:
    """Live weather at the airport. Returns None on any failure. Never raises."""
    pos = AIRPORTS.get(origin)
    if not pos or os.environ.get("KODA_NO_WEATHER"):
        return None
    url = ("https://api.open-meteo.com/v1/forecast?latitude=%s&longitude=%s&current=temperature_2m,weather_code,"
           "wind_speed_10m,precipitation,visibility" % pos)
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            cur = json.loads(r.read())["current"]
        LAST["ok"], LAST["error"] = time.time(), None
        code = int(cur.get("weather_code", 0))
        vis = cur.get("visibility")
        return {"airport": origin, "weather": _CODES.get(code, "Variable conditions"), "code": code,
                "temp_c": round(cur.get("temperature_2m", 0)), "wind_kmh": round(cur.get("wind_speed_10m", 0)),
                "rain_mm": cur.get("precipitation", 0), "visibility_km": round(vis / 1000, 1) if vis is not None else None,
                "severe": code in _SEVERE or (vis is not None and vis < 1000) or cur.get("wind_speed_10m", 0) >= 50,
                "source": "Live weather, Open-Meteo"}
    except Exception as e:
        LAST["error"] = f"Weather service not reachable ({type(e).__name__})"
        return None


def prefetch(origin: str) -> None:
    """Fill the cache now. Call outside the database lock."""
    if origin in AIRPORTS and time.time() - _cache.get(origin, (0, None))[0] > TTL:
        w = fetch(origin)
        if w:
            _cache[origin] = (time.time(), w)


def warm(origin: str) -> None:
    """Fill the cache in the background so a page never waits on the weather service."""
    with _lock:
        if origin in _busy or origin not in AIRPORTS:
            return
        _busy.add(origin)

    def run():
        try:
            prefetch(origin)
        finally:
            with _lock:
                _busy.discard(origin)

    threading.Thread(target=run, daemon=True).start()


def cached(origin: str) -> dict | None:
    hit = _cache.get(origin)
    return hit[1] if hit and time.time() - hit[0] <= TTL * 6 else None


_SIM_WEATHER = ["Thunderstorm", "Heavy rain", "Fog", "Clear sky", "Partly cloudy"]
_AIRPORT = ["Ground stop for 40 minutes", "Runway queue of 18 aircraft", "Crew rotation delay at the gate",
            "Normal operations", "De-icing and ramp congestion"]


def for_event(flight: str, origin: str, cancelled: bool, minutes: int) -> dict:
    """Weather (live when cached, else simulated) plus simulated airport operations. Reads cache only."""
    h = int(hashlib.sha256(f"{flight}{origin}{int(cancelled)}".encode()).hexdigest(), 16)
    bad = cancelled or minutes >= 180
    ops = _AIRPORT[(h >> 8) % 3] if bad else _AIRPORT[3]
    live = cached(origin)
    if live:
        w = dict(live)
        w["text"] = (f"{w['weather']}, {w['temp_c']} C, wind {w['wind_kmh']} km/h"
                     + (f", visibility {w['visibility_km']} km" if w.get("visibility_km") is not None else ""))
        w["operations"] = ops
        w["ops_source"] = "Simulated airport operations"
        w["fits"] = w["severe"]
        return w
    sim = _SIM_WEATHER[h % 3] if bad else _SIM_WEATHER[3 + h % 2]
    return {"airport": origin, "weather": sim, "text": sim, "operations": ops, "source": "Simulated ground conditions",
            "ops_source": "Simulated airport operations", "severe": bad, "fits": bad}
