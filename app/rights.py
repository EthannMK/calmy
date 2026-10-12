"""What the airline may also owe the traveler. Plain summary by market, written for the situation.

KODA pays on top of these duties and never replaces them. This is information, not legal advice.
A background Gemini call can reword the summary; the rule-based text is always available.
"""
import os
import threading

_cache: dict[str, str] = {}


def _item(name, status, detail):
    return {"name": name, "status": status, "detail": detail}   # status: owed | maybe | no


def build(market: str, flight: dict, weather_severe: bool) -> dict | None:
    """Rights for the current event, or None when there is no event yet."""
    mins = flight.get("delay_minutes") or 0
    cancelled = bool(flight.get("cancelled"))
    if not cancelled and mins < 120:
        return None
    extra = weather_severe
    ev = "was cancelled" if cancelled else f"is delayed {mins // 60}h {mins % 60:02d}m"
    items = []
    claim = "Ask at the airline desk or in its app. Keep your boarding pass and receipts."
    if market in ("EU", "UK"):
        cur, amts = ("EUR", "250, 400 or 600") if market == "EU" else ("GBP", "220, 350 or 520")
        law = "EU261" if market == "EU" else "UK261"
        if cancelled or mins >= 180:
            items.append(_item("Cash compensation", "no" if extra else "maybe",
                               f"{amts} {cur} by distance under {law}, unless the cause was extraordinary. "
                               + ("Severe weather usually counts as extraordinary, so this is unlikely." if extra else "A technical or crew problem usually does not count.")))
        items.append(_item("Meals and refreshments", "owed", "The airline must give vouchers or food while you wait."))
        if cancelled or mins >= 300:
            items.append(_item("Refund or another flight", "owed", "You choose a refund within 7 days or a new flight."))
        if cancelled or mins >= 600:
            items.append(_item("Hotel and transfer", "maybe", "Owed when the new departure is the next day."))
    elif market == "US":
        if cancelled:
            items.append(_item("Refund", "owed", "The DOT rule gives you an automatic refund to your original payment when the flight is cancelled."))
        else:
            items.append(_item("Refund", "maybe", "A refund is due if the delay is significant and you choose not to travel."))
        items.append(_item("Cash compensation", "no", "US law has no cash compensation for delays."))
        items.append(_item("Meals", "maybe", "Many airlines promise meals for long controllable delays in their customer service plan."))
    elif market == "CA":
        if cancelled or mins >= 180:
            items.append(_item("Cash compensation", "no" if extra else "maybe",
                               "400, 700 or 1,000 CAD by arrival delay when the cause is within the airline's control."))
        items.append(_item("Food and communication", "owed", "The airline must look after you during the wait."))
        items.append(_item("Refund or rebooking", "owed" if cancelled or mins >= 360 else "maybe", "Offered for long delays and cancellations."))
    elif market == "IN":
        items.append(_item("Meals and refreshments", "owed", "DGCA rules require refreshments for delays of 2 hours or more."))
        items.append(_item("Refund or another flight", "owed" if cancelled or mins >= 360 else "maybe", "Offered for cancellations and very long delays."))
        items.append(_item("Cash compensation", "maybe" if cancelled and not extra else "no", "Mainly for cancellations with short notice."))
    elif market == "BR":
        items.append(_item("Communication and food", "owed", "ANAC rules: call or message from 1 hour, food from 2 hours."))
        if mins >= 240 or cancelled:
            items.append(_item("Hotel and transfer", "owed", "From 4 hours, if you need to stay overnight."))
        items.append(_item("Refund or another flight", "owed" if cancelled or mins >= 240 else "maybe", "You choose when the delay passes 4 hours."))
    else:
        if market == "TH":
            items.append(_item("Meals and refreshments", "owed", "CAAT passenger rules expect airlines to look after you during long delays."))
        items.append(_item("Refund or another flight", "owed" if cancelled else "maybe", "A cancelled flight is usually refundable. Check the airline's conditions."))
        if market != "TH":
            items.append(_item("Meals", "maybe", "Many airlines provide food for long delays."))
        items.append(_item("Cash compensation", "no", "No fixed compensation applies in this market."))
    owed = [i["name"].lower() for i in items if i["status"] == "owed"]
    summary = (f"Flight {flight['code']} {ev}. " + (("The airline owes you: " + ", ".join(owed) + ". ") if owed else "")
               + "Your KODA payout is separate and does not reduce what the airline owes.")
    key = f"{market}|{int(cancelled)}|{mins // 60}|{int(extra)}"
    return {"items": items, "summary": _cache.get(key, summary), "how": claim, "ai": key in _cache,
            "disclaimer": "General information, not legal advice.", "_key": key, "_plain": summary}


def enrich(r: dict) -> None:
    """Reword the summary with Gemini in the background. Silent on any failure."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project or r["_key"] in _cache:
        return

    def run():
        try:  # pragma: no cover - needs cloud credentials
            from google import genai
            from google.genai import types
            client = genai.Client(vertexai=True, project=project, location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                                  http_options=types.HttpOptions(timeout=8000))
            facts = r["_plain"] + " " + " ".join(f"{i['name']}: {i['status']}. {i['detail']}" for i in r["items"])
            resp = client.models.generate_content(
                model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"),
                contents="Rewrite as 2 short, warm sentences for a stressed traveler. Use only these facts, add nothing, no legal advice.\n" + facts)
            if resp.text:
                _cache[r["_key"]] = resp.text.strip()
        except Exception:
            pass
    threading.Thread(target=run, daemon=True).start()
