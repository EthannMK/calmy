"""Delay forecast, early alert, exposure and price suggestion.

This is a transparent additive score, not a trained model. Each driver adds points and is shown
to the user in plain words. In the cloud version the same inputs feed a BigQuery ML model and
Gemini writes the explanation. The score only informs: it warns travelers, helps the insurer
reserve and price, and flags cover bought when trouble was already likely. It never decides a payout.
"""
import hashlib

from . import context

ALERT_AT = 55          # probability that triggers the early alert
HEAVY_SHARE = 0.35     # share of delays that last 3 hours or more, used to size the expected payout
TARGET_LOSS_RATIO = 0.6


def _h(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest(), 16)


def compute(flight: dict) -> dict:
    """Probability (0 to 100) that this flight is delayed 30 minutes or more, with the reasons."""
    code, origin = flight["code"], flight.get("origin", "")
    sched = (flight.get("scheduled") or "12:00")[:5]
    hour = int(sched.split(":")[0])
    drivers = [("Typical chance on any flight", 12)]
    cur = flight.get("delay_minutes") or 0
    if flight.get("cancelled"):
        return {"probability": 100, "level": "high", "drivers": [("The flight is cancelled", 100)], "expected_delay": 0,
                "source": "Flight status"}
    if cur >= 30:
        drivers.append((f"Already delayed {cur} minutes", 45 if cur < 120 else 60))
    stress = int(flight.get("stress") or 0)
    live = context.cached(origin)
    if live and live.get("severe"):
        drivers.append((f"{live['weather']} at {origin} now (live weather)", 24))
    elif stress:
        drivers.append((f"Thunderstorm building near {origin}", 22 if stress == 1 else 38))
    if stress >= 2:
        drivers.append((f"Queue of departures at {origin} growing", 12))
    cong = 4 + _h(origin) % 7
    drivers.append((f"Usual traffic at {origin}", cong))
    if hour >= 17:
        drivers.append(("Evening departure, delays pile up through the day", 6))
    elif hour < 8:
        drivers.append(("Early departure, fewer knock-on delays", -3))
    prob = max(3, min(97, sum(p for _, p in drivers)))
    level = "high" if prob >= ALERT_AT else ("medium" if prob >= 30 else "low")
    exp = round(prob / 100 * (40 + 4 * (prob - 12)) / 5) * 5 if prob > 20 else 0
    return {"probability": prob, "level": level, "drivers": drivers, "expected_delay": max(0, exp),
            "source": "Live weather + simulated airport traffic" if (live and live.get("severe")) else "Simulated weather and airport traffic"}


def alert(fc: dict, flight: dict) -> dict | None:
    """Early alert text for a covered traveler, or None."""
    if fc["probability"] < ALERT_AT or flight.get("cancelled") or (flight.get("delay_minutes") or 0) >= 30:
        return None
    return {"title": f"A delay is likely on {flight['code']}",
            "text": f"About {fc['probability']}% chance of a delay, around {fc['expected_delay']} minutes. "
                    "You are covered, so nothing to do. If it passes 3 hours we pay you automatically."}


def pricing(fc: dict, base_premium: float, tier1_payout: float) -> dict:
    """Suggested delay price for this flight today, from expected payout and a target loss ratio."""
    p_claim = fc["probability"] / 100 * HEAVY_SHARE
    expected = p_claim * tier1_payout
    suggested = max(base_premium, round(expected / TARGET_LOSS_RATIO, 1))
    return {"p_claim": round(p_claim * 100, 1), "expected_payout": round(expected, 2),
            "suggested": suggested, "current": base_premium,
            "move": "raise" if suggested > base_premium else "hold"}
