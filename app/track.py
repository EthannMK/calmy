"""Keeps real flights up to date and fires the payout trigger from real delays.

A flight is refreshed when someone looks at a policy on it and its data is stale, or when
a person presses Check now. There is no always-on poller, because the free flight data plan
allows only 100 requests a month. The cache and monthly budget live in live.py.
"""
import os
import threading
import time

from . import context, db, engine, live


def pay_on_estimate() -> bool:
    """Off by default: a payout needs the actual departure time. On restores paying on the airline's estimate."""
    return os.environ.get("KODA_PAY_ON_ESTIMATE", "0") == "1"


def payable(fresh: dict) -> bool:
    """Whether live data is final enough to decide money on: an actual departure, or a cancellation."""
    return fresh.get("status") == "cancelled" or fresh.get("delay_basis") == "actual" or pay_on_estimate()

STALE_SECONDS = 30 * 60          # refresh at most this often for passive viewing
WINDOW_BEFORE, WINDOW_AFTER = 12 * 3600, 36 * 3600  # only track flights near their departure

_inflight: set[str] = set()
_lock = threading.Lock()


def refresh_flight(code: str) -> dict:
    """Fetch the real status, store it, and run the payout rules on the real delay.

    Raises live.LiveError with a message that is safe to show. The network call runs
    without the database lock held.
    """
    with db.conn() as c:
        f = db.one(c, "SELECT * FROM flights WHERE code=?", (code,))
    if not f:
        raise live.LiveError("Flight not found.")
    if f["source"] != "live":
        raise live.LiveError("This is a sample flight, so there is no real status to check.")
    if f.get("sim"):
        return {"flight": f, "results": [], "note": "A demo event is set on this flight, so the live status is paused."}
    fresh = live.lookup(code, f["flight_date"])
    if f.get("flight_date") and fresh.get("flight_date") != f["flight_date"]:
        # The live plan only knows flights that fly today, so a later day has no status yet. Never apply another day's delay.
        return {"flight": f, "results": [], "note": "Live status for that day is not available yet. It is checked closer to the day."}
    context.prefetch(fresh["origin"])  # network call, before the database lock
    with db.conn() as c:
        live.upsert_flight(c, fresh)
        if payable(fresh):
            results = engine.run_delay(c, code, fresh["delay_minutes"], cancelled=(fresh.get("status") == "cancelled"), source="live flight data")
        else:
            # A forecast: shown on the screens (the flight row now carries it), but no claim and no payout until the
            # airline records the actual departure.
            results = []
            db.log(c, None, "delay_forecast", {"flight": code, "minutes": fresh["delay_minutes"], "basis": fresh.get("delay_basis")})
        db.log(c, None, "flight_checked", {"flight": code, "delay": fresh["delay_minutes"], "status": fresh["status"], "basis": fresh.get("delay_basis")})
    ids = [r["decision"] for r in results if r.get("decision")]
    if ids:
        threading.Thread(target=engine.enrich_explanations, args=(ids,), daemon=True).start()
    return {"flight": fresh, "results": results}


def maybe_refresh(f: dict | None) -> None:
    """Start a background refresh when a tracked flight is stale and close to departure."""
    if not f or f.get("source") != "live" or not live.enabled() or not f.get("sched_ts"):
        return
    now = time.time()
    if now - (f.get("fetched_at") or 0) < STALE_SECONDS:
        return
    if not (f["sched_ts"] - WINDOW_AFTER <= now <= f["sched_ts"] + WINDOW_BEFORE):
        return
    with _lock:
        if f["code"] in _inflight:
            return
        _inflight.add(f["code"])

    def run():
        try:
            refresh_flight(f["code"])
        except Exception:  # never let a background refresh break a page
            pass
        finally:
            with _lock:
                _inflight.discard(f["code"])

    threading.Thread(target=run, daemon=True).start()
