# Calmy, smart flight cover

Flight delay and lost bag cover that pays automatically. A traveler buys cover at checkout.
When the flight is late, rules and a risk score decide, the payout is simulated, and the
insurer console shows the decision and the reason.

Demo data only. Flight data is simulated and no real money moves.

## Run locally

    pip install -r requirements.txt
    uvicorn app.main:app --reload

Open http://localhost:8000. Pages: `/traveler` (mobile app), `/console` (insurer console).

## Demo path (about 90 seconds)

1. Traveler app: tap Add cover (208 THB).
2. Console, Overview: tap Delay OA 108 by 3h 12m.
3. Traveler app, Claims tab: paid 1,500 THB in 8 seconds.
4. Open the second traveler at `/traveler?p=CL-45102`, buy cover, run the delay again from a fresh reset.
   That claim scores 91, is held, and appears in the console Review queue. Approve it there.
5. Traveler app, Cover tab: My bag is lost. Console, Baggage claims: approve 1,250 THB.
6. Console, Rules: change a payout, run another delay, and see the new rule apply.

Reset demo data is in the console footer and in the traveler Demo controls.

## How it works

| Piece | File | Notes |
| --- | --- | --- |
| Rules table | `app/db.py` | Insurer settings, editable in the console |
| Trigger and decision | `app/engine.py` | Delay tiers, double claim check, pay or hold |
| Risk score | `app/engine.py` | Readable baseline, same features go to BigQuery ML in the cloud version |
| Explanation | `app/engine.py` | Gemini on Vertex AI when `GOOGLE_CLOUD_PROJECT` is set, template otherwise |
| API | `app/main.py` | Traveler, console and demo endpoints |
| Screens | `static/` | Built from the Stitch designs, fonts bundled |

Default rules: 3 hours pays 1,500 THB, 6 hours pays 3,000 THB, lost bag pays 1,250 THB,
auto pay below risk score 45. Prices: delay cover 149 THB, baggage cover 59 THB (placeholders).

## Tests

    pip install pytest
    python -m pytest -q tests

## Markets and law

The departure country of a flight sets the market. Each market has its own currency, payout rails,
prices and payouts (simulated, in local currency), tax note and legal notes. Markets: Thailand, United States,
European Union, United Kingdom, India, Singapore, Japan, Australia, Brazil, Canada, and a global default.

What the law changes in the product, not only in text:

- Key facts and a privacy notice must be accepted before cover is bought. The acceptance is logged with the notes version.
- A claim is never denied by the system. It is paid, or held for a person. Only a person can decline, and the traveler can ask for a second review (GDPR Article 22 and fair claims handling).
- The airline's own duties (EU261, UK261, Canada APPR and others) are shown, and the payout never replaces them.
- Cover cannot be bought after a delay is known or after departure.
- Rules and prices are edited per market in the insurer console. The "Markets and law" page lists the laws and what Calmy does about each.

These notes are a design aid for the demo and not legal advice. A launch needs a licensed insurer and local counsel.
Source file: `app/markets.py`.

## Real flight data

Set `AVIATIONSTACK_KEY` (free key at aviationstack.com). Then:

- The traveler picks a real upcoming departure from any airport, or types a flight number.
- The app tracks the real status. A flight is refreshed when someone views it and its data is older than 30 minutes, or when "Check now" is pressed. There is no always-on poller.
- Payout rules run on the real delay, computed from scheduled versus actual or estimated time.
- Free plan: 100 requests a month, HTTP only. Calls are cached for 15 minutes and capped by `AVIATIONSTACK_BUDGET` (default 80 a month).
- Real delays of 3 hours or more are rare, so Test tools can simulate a delay on any flight, or replay a flight that is really late now. The screens label real flights and samples.
- A delay that later grows past the second level tops the payout up. Nothing is paid twice.
- Risk signals (several covers in 2 hours, shared payout account, prior paid claim, schedule change after purchase) are computed from stored data.

Without a key the app uses the sample flights, one or more for each market.

## Deploy to Cloud Run

State lives in one SQLite file, so run a single instance.

    gcloud run deploy calmy --source . --region asia-southeast1 --allow-unauthenticated \
      --min-instances 1 --max-instances 1 \
      --set-env-vars GOOGLE_CLOUD_PROJECT=<project>,GOOGLE_CLOUD_LOCATION=us-central1,AVIATIONSTACK_KEY=<optional>

The service account needs the Vertex AI User role for Gemini. Without it the app still works
and uses the template explanation.

## Design system

No build step and no CDN. `static/ui.css` holds the tokens and components, `static/ui.js` holds the
shared helpers and the charts (delay track, risk score breakdown, outcome bar, payout curve, route
art). Fonts and a 25 KB icon subset are served from `static/fonts`. Charts follow one rule set:
status colors always carry a label, one axis only, values labeled directly.
