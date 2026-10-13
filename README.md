# KODA, smart flight cover

Flight delay and lost bag cover that pays automatically. A traveler adds cover to a booking,
and when the flight is late the rules and a risk score decide, the payout is made in KODA points,
and the insurer console shows the decision and the reason behind it.

The demo is set in Singapore: the traveler is George, flying out of Changi, and the insurer is
Flightwise Insurance, where Ethan works as the claims officer. Flight data is real where we can get
it. Payouts, tickets bought through our own checkout, and the airport partner outlets are simulated
and labelled as such. No real money moves.

## Run locally

    pip install -r requirements.txt
    uvicorn app.main:app --reload

Open http://localhost:8000. Pages: `/traveler` (mobile app), `/console` (insurer console).

## Demo path (about 3 minutes)

Open `/judge`. Both apps sit side by side and both are already signed in: George on the phone,
Ethan at the insurer desk. There is no sign-in step and nothing to set up.

1. **George's trips.** The phone opens on Home: who he is, the delay forecast for his next covered
   flight, his Changi trips, and the two ways in (check a ticket he already holds, or book one with
   cover through the partner checkout). Open a trip and tap *See what this cover pays* for the payout
   ladder and the exclusions.
2. **Break the flight.** In the avatar menu, Demo controls sets a delay or a cancellation on one of
   his flights. It runs the real engine. George is paid in seconds, in points, with a message.
3. **The claim that did not pay.** His Jakarta trip was 2h 30m late, under the 3 hour rule. He taps
   *Ask us to check again* and writes what the airline told him.
4. **Decide as the insurer.** In the console, Review queue shows the case marked *Traveler asked*
   with George's note, the booking and the flight. Approve it and the points land on his phone,
   signed with Ethan's name.
5. **Spend it.** Wallet, then an outlet at Changi: a QR code is made, *Simulate the outlet scan*
   spends it, and the insurer's Points page flips to "Scanned at the outlet".
6. **Talk to a person.** Messages on the phone, then Conversations in the console: take over, reply,
   and George sees Ethan's name appear in the same chat.

Flights are real and need a live key (below). Delays, cancellations and payouts are simulated and
labelled as demo events.

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
| Cloud Firestore, Cloud Functions, Gmail API, Firebase rules | SQLite stands in for Firestore, the engine runs inside the Cloud Run service, and only the last four digits of the payout account are stored, never a full number | Next step |
| Payment gateway | Premiums and payouts are simulated and labelled. The money paths are isolated behind `app/wallet.py` and the cash-out preview, so production means a PSP for premiums (Stripe or Adyen), a card-payout or local-rails partner for payouts, and the FX quote at cash-out coming from that partner instead of our indicative rate | Way forward, by design not by accident |
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
| Traveler | Delay forecast and early alert | `app/forecast.py`: a trained model for the base chance, then live weather, the current delay and airspace alerts as readable drivers. At 55% or more a covered traveler gets an alert. Presenter buttons "Storm builds" and "Weather clears" drive it. |
| Traveler | Your rights card | `app/rights.py`: what the airline may also owe by market (EU261, UK261, US DOT, APPR, DGCA, ANAC, CAAT). Weather counts as extraordinary, so cash compensation shows as unlikely. Gemini rewords the summary when configured. Not legal advice. |
| Traveler | Points payout and airport partners | Payout rail "KODA points" pays 25% more as points (100 points = S$1). The Wallet tab lists the partners that accept points and are open during the wait, at any airport the traveler flies from, and issues a QR voucher. The Changi directory in `app/partners.py` is fictional prototype data: food and drink, lounges, and SIM or data packs. |
| Fraud | Adverse selection check | Cover bought while the forecast was already 55% or more adds 35 points to the risk score, so the claim goes to a person. It is never declined by the system. |
| Insurer | Exposure forecast and dynamic price | `GET /api/console/exposure`: expected payout, reserve (expected plus a safety margin) and a suggested delay price per flight. Informs only. |
| Insurer | Reviewer copilot | `GET /api/console/copilot/{id}` and `POST /api/console/copilot`: recommendation with confidence, evidence, an editable draft message and a question box. It advises approve, ask for proof or escalate. It never declines. |
| Traveler | Coverage visualizer | `GET /api/app/cover/{policy}`: the payout ladder for this flight (on time, 3 hours, 6 hours, cancelled) with a marker for where the flight actually is, what is covered, what is missing from this trip, and what is never covered. Each one opens the chat with that flight in context. |
| Traveler | Support assistant with human takeover | `app/chat.py`. The assistant answers from the traveler's own policy, flight status, rules and wallet, and hands the chat to a person whenever one is asked for. The console sees the transcript and the facts behind each reply, and can take over or hand back. |
| Traveler | QR redemption at the airport | Points are spent at a partner outlet as a QR voucher (`GET /api/app/qr/{code}.svg`, drawn server side with segno). `POST /api/outlet/scan` is what the outlet's till calls; a voucher works once, at its own outlet, and unused ones can be refunded. The insurer sees which vouchers were scanned. |
| Traveler | Re-check request | `POST /api/app/recheck`: a delay under the rule did not pay, so the traveler asks a person to look again and writes what happened. It lands in the review queue marked "Traveler asked", with the note. The system never declines; only a person does. |
| Traveler | Partner checkout | `POST /api/app/partner-booking`: a traveler with no ticket books a flight and the cover together. The ticket is simulated in the prototype and labelled; the cover is real in the system. Cover is never sold without a booking. |
| Traveler | Airline letter | `GET /api/traveler/{pid}/airline-letter`: a ready letter asking the airline for what it owes in that market. Gemini polishes it when connected. |
| Traveler | Delay notification | A banner appears on the phone the moment the payout or hold lands, with the same message as the Claims tab. |
| Traveler | Calendar flight picker | Pick any date up to a month ahead, then a flight; only the chosen flight stays on screen. The traveler says where the ticket was bought (airline, Trip.com, Agoda or another site) and enters the booking reference from it, which is stored on the policy. KODA sells the cover, not the ticket, so it works beside any booking site. A lost bag can only be reported once the flight has left. Later dates use the saved real schedule (`app/schedule.py`), labelled "Scheduled flight"; live status is checked closer to the day. |
| Data | Saved real schedule and BigQuery sync | `tools/sync_flights.py` pulls real departures from AviationStack, loads BigQuery table `koda.flights_snapshot` and writes `data/flights_snapshot.json`, which ships in the image as the offline fallback. Run it before the demo: `AVIATIONSTACK_KEY=... python3 tools/sync_flights.py --project <project>` (about 6 requests). `--from-bq` rebuilds the JSON from BigQuery. |

The base delay chance comes from a trained model (`tools/train_model.py`, gradient boosted trees on 336,776 real flights, tested on later months it never saw). Live delay, live weather and insurer airspace alerts are added as readable drivers. The model card is in the console under Exposure forecast. It was trained on New York flights from 2013, not Thai flights, and the card says so.


## The demo world

One traveler and one officer, so a judge never has to pick an account.

- **George** (`CU-GEORGE`), based in Singapore, paid in KODA points, with a Visa in SGD and a
  Mastercard in THB so the cash-out currency change is visible. His account carries every scenario:
  a covered round trip to Bangkok, a booking to Hong Kong with no cover yet, a past Kuala Lumpur trip
  that was paid, and a past Jakarta trip that fell under the rule and can be re-checked.
- **Ethan** (`ST-1`), claims officer at Flightwise, admin role, so he can decide claims, change rules
  and take over a chat.
- Around him, 50 more customers with mixed cover, claims, points and payout routes, so every console
  page has a real book of business. About a quarter came through our own checkout (ticket and cover
  together); the rest bought cover on a ticket from an airline or a travel site. The Customers page
  filters by that.
- Money is in Singapore dollars end to end. 100 points = S$1 to spend at an airport partner, with a
  25% bonus on every payout; cash-out is 80% of the spending value, and a currency change shows the
  rate and the fee before the traveler confirms.
- Full cover rules and workflows: docs/COVERAGE_AND_WORKFLOW.md

## Sign-in, search and the investigation agent

Sign-in. The real thing is a one-time code: travelers use a booking reference, a last name and a code; staff use a work email and a code. A code lasts 5 minutes and allows 5 tries, and sessions end after 30 minutes (travelers) or 60 minutes (staff) without use. For the demo that is switched off: both apps sign themselves in as George and Ethan through `POST /api/auth/demo-session`, so a judge is never stopped by a code. Set `KODA_DEMO_CODES=off` to turn the demo sign-in off and get the code screens back. Roles: admin (rules, airspace alerts, partners), reviewer (decide held claims), agent (look only). Code is in `app/auth.py`.

Flight search. `GET /api/flights/search` returns flights with delay risk, whether delay cover is open, the cover price and an approximate flight time. Sources in order: live status, the saved real schedule, then a built-in sample schedule, so search never comes back empty.

Investigation agent. `app/agent.py` investigates a held claim with six read-only tools (claim, flight status, delay forecast, weather and airspace, policy terms, traveler history), records each step and writes a case summary. It advises only. Gemini rewrites the summary when a Google Cloud project is set.

Support assistant with human takeover. `app/chat.py` is the traveler's chat. The assistant answers from that traveler's own facts (`chat.facts`: policies, flight status, forecast, rules, wallet) and the answer step is one function, `chat._answer`, which builds the message from those facts and lets Gemini do the wording. Swapping that function for an ADK agent on Vertex AI changes nothing else in the app. The assistant never decides a claim: when the traveler asks for a person, or asks about a decision, the chat is flagged and the insurer sees it in Conversations, where Ethan can take over, reply under his own name, and hand it back. Every bubble says who wrote it, and the console transcript shows which facts the assistant read for each reply.

Environment: `KODA_AUTH=off` turns sign-in checks off (used by most tests).
