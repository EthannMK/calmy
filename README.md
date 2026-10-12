# KODA, smart flight cover

Flight delay and lost bag cover that pays automatically. A traveler buys cover at checkout.
When the flight is late, rules and a risk score decide, the payout is simulated, and the
insurer console shows the decision and the reason.

Demo data only. Flight data is simulated and no real money moves.

## Run locally

    pip install -r requirements.txt
    uvicorn app.main:app --reload

Open http://localhost:8000. Pages: `/traveler` (mobile app), `/console` (insurer console).

## Demo path (about 3 minutes)

Open `/judge`. A welcome card explains the page. Then follow the four steps in the top bar.

1. Cover a trip. The app picks a real upcoming departure (for example Thai Airways from BKK), adds cover for a traveler and, in the background, a second suspicious traveler on the same flight.
2. Break the flight. You act as the status feed: Storm builds, Delay 3h 12m, Cancel it. The first traveler gets a phone notification at the delay limit and is paid in seconds. Then the traveler can claim extra costs with a receipt and draft a letter to the airline.
3. Show the held case. The same delay is held for a person because the second profile scores 91.
4. Decide as the insurer in the review queue, with the reviewer copilot. Extra cost receipts are decided under Extra costs.

Flights are real and need a live key (below). The delay, storm and payout are simulated and labelled as demo events.
The insurer in the demo is Flightwise Insurance. KODA is the product it runs.

## Live flight data (required)

KODA covers real flights only. Set `AVIATIONSTACK_KEY` (free key at aviationstack.com). Without it the traveler app says live data is not connected. Then:

- The traveler picks a real upcoming departure from any airport, or types a flight number.
- The app tracks the real status. A flight is refreshed when someone views it and its data is older than 30 minutes, or when "Check now" is pressed. There is no always-on poller.
- Payout rules run on the real delay, computed from scheduled versus actual or estimated time.
- Free plan: 100 requests a month, HTTP only. Calls are cached for 15 minutes and capped by `AVIATIONSTACK_BUDGET` (default 80 a month).
- Real delays of 3 hours or more are rare, so the presenter bar can set a delay on a real flight. Once a demo event is set, live refresh pauses for that flight so it cannot undo the demo.
- A delay that later grows past the second level tops the payout up. Nothing is paid twice.
- Risk signals (several covers in 2 hours, shared payout account, prior paid claim, schedule change after purchase) are computed from stored data.

For automated tests only, `KODA_SAMPLE_DATA=1` seeds fictional flights so the tests run offline. The product never uses them.

## Deploy to Cloud Run

State lives in one SQLite file, so run a single instance.

    gcloud run deploy calmy --source . --region asia-southeast1 --allow-unauthenticated \
      --min-instances 1 --max-instances 1 \
      --set-env-vars GOOGLE_CLOUD_PROJECT=<project>,GOOGLE_CLOUD_LOCATION=us-central1,AVIATIONSTACK_KEY=<optional>

The service account needs the Vertex AI User role for Gemini. Without it the app still works
and uses the template explanation.

## How this prototype maps to the submission deck

| Deck item | In this prototype | Status |
|---|---|---|
| Four steps: event, AI checks policy, money sent, traveler notified | Five step timeline on every decision: Event detected, Policy matched, Risk checked, Paid, Traveler notified | Built |
| Delay and cancellation payout | Delay tiers plus a Cancelled flight payout rule (Clause 3.2), editable on the Rules page | Built |
| Cloud Run webhook for flight events | `POST /api/webhook/flight-event` with `delayed`, `cancelled`, `on_time`, `landed` | Built |
| Flight status API (AviationStack) | Real flights, 100 request monthly budget, cancelled status triggers a payout | Built, needs `AVIATIONSTACK_KEY` |
| Gemini checks the event against the policy | Gemini on Vertex AI writes the reason; rules and risk score decide | Built |
| Weather and airport data | Live weather from Open-Meteo shown on the traveler flight card and in each decision. Airport operations are simulated and labelled. Weather never changes the payout | Weather live, airport simulated |
| PNR tracked per booking | Every policy gets a 6 character PNR, shown to traveler and insurer | Built |
| No problem: trip quietly archived | `on_time` or `landed` archives the trip: no payout, no message | Built |
| Caring message | Stored on each decision and shown as a message card in the traveler app and in the console | Built (in app; email delivery is a Gmail API step, not connected) |
| Payout to card, bank or wallet | Payout options per market plus Card refund and Digital wallet. Money is simulated | Simulated |
| Zero paperwork | No form, receipt or letter asked. Performance page counts documents asked | Built |
| Cloud Firestore, Cloud Functions, Gmail API, payment gateway, Firebase rules | SQLite stands in for Firestore, the engine runs inside the Cloud Run service, and only the last four digits of the payout account are stored, never a full number | Next step |
| Prototype performance report | Console, Performance page: manual baselines against measured prototype results | Built |
| Baggage and fraud checks (Later) | Lost bag cover and an additive risk score already included | Built ahead of plan |

## Live demo page

Open `/judge`. The two real apps sit side by side: the traveler app in a phone frame and the insurer console. One slim bar on top coaches the viewer through four steps (cover a trip, break the flight, try a risky traveler, decide as the insurer). For each step it shows the buttons to press, and a label above each app says what to do or watch on that side. A More menu holds the data sources list, links to open each app alone, and Reset demo data. The apps themselves contain no demo buttons.

Flight events go through the same webhook the product uses. The first visit in a browser session starts from clean data.

## Design system

No build step and no CDN. `static/ui.css` holds the tokens and components, `static/ui.js` holds the
shared helpers and the charts (delay track, risk score breakdown, outcome bar, payout curve, route
art). Fonts and a 25 KB icon subset are served from `static/fonts`. Charts follow one rule set:
status colors always carry a label, one axis only, values labeled directly.


## AI value features (added for the final round)

| Where | Feature | How it works in the prototype |
|---|---|---|
| Traveler | Delay forecast and early alert | `app/forecast.py`: a readable score from weather (live Open-Meteo when reachable, else simulated), airport traffic, time of day and the current delay. At 55% or more a covered traveler gets an alert. Presenter buttons "Storm builds" and "Weather clears" drive it. |
| Traveler | Your rights card | `app/rights.py`: what the airline may also owe by market (EU261, UK261, US DOT, APPR, DGCA, ANAC, CAAT). Weather counts as extraordinary, so cash compensation shows as unlikely. Gemini rewords the summary when configured. Not legal advice. |
| Traveler | Points payout and airport restaurants | Payout rail "KODA points" pays 25% more as points (1 point = 1 cent). The Wallet tab lists only restaurants that accepted points and are open during the wait, picks the best one, and issues a voucher code. The directory (`app/partners.py`) is fictional prototype data. |
| Fraud | Adverse selection check | Cover bought while the forecast was already 55% or more adds 35 points to the risk score, so the claim goes to a person. It is never declined by the system. |
| Insurer | Exposure forecast and dynamic price | `GET /api/console/exposure`: expected payout, reserve (expected plus a safety margin) and a suggested delay price per flight. Informs only. |
| Insurer | Reviewer copilot | `GET /api/console/copilot/{id}` and `POST /api/console/copilot`: recommendation with confidence, evidence, an editable draft message and a question box. It advises approve, ask for proof or escalate. It never declines. |
| Traveler | Extra costs claim | `POST /api/traveler/{pid}/extra-claim`: after the delay payout, a meal, transport or hotel receipt can be claimed. Under the tier 1 payout and with a clean score it is paid at once. Above it, or with a raised score, it waits in the console under Extra costs. Total is capped at twice the top payout. |
| Traveler | Airline letter | `GET /api/traveler/{pid}/airline-letter`: a ready letter asking the airline for what it owes in that market. Gemini polishes it when connected. |
| Traveler | Delay notification | A banner appears on the phone the moment the payout or hold lands, with the same message as the Claims tab. |
| Traveler | Calendar flight picker | Pick any date up to a month ahead, then a flight; only the chosen flight stays on screen. The traveler says where the ticket was bought (airline, Trip.com, Agoda or another site) and enters the booking reference from it, which is stored on the policy. KODA sells the cover, not the ticket, so it works beside any booking site. A lost bag can only be reported once the flight has left. Later dates use the saved real schedule (`app/schedule.py`), labelled "Scheduled flight"; live status is checked closer to the day. |
| Data | Saved real schedule and BigQuery sync | `tools/sync_flights.py` pulls real departures from AviationStack, loads BigQuery table `koda.flights_snapshot` and writes `data/flights_snapshot.json`, which ships in the image as the offline fallback. Run it before the demo: `AVIATIONSTACK_KEY=... python3 tools/sync_flights.py --project <project>` (about 6 requests). `--from-bq` rebuilds the JSON from BigQuery. |

The forecast is a transparent score, not a trained model. In production the same inputs feed BigQuery ML.


## Customers, points wallet and bookings

- Travelers are paid in KODA points by default (100 points = $1 to spend, 25% bonus). They can save a card or bank, cash out, or switch on auto top-up.
- Four demo booking references show the cover states: BOTH42, DLY731, BAG518 and NONE26. Use Check my cover with the last name (Wongsakul, Tan, Nakamura, Okafor).
- The console has Customers and Points and partners tabs and a working staff profile. The seed adds 50 demo customers with mixed cover and claims.
- Full cover rules and workflows: docs/COVERAGE_AND_WORKFLOW.md
