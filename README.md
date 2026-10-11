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

## Real flight data (optional)

The app runs on a simulator by default. To replay a flight that is really delayed, get a free
key at aviationstack.com and set `AVIATIONSTACK_KEY`. Then open Demo controls in the traveler app.

- Free plan: 100 requests a month, HTTP only. Calls are made from the server, cached for 15 minutes,
  and capped by `AVIATIONSTACK_BUDGET` (default 80 a month).
- Delay is computed from scheduled versus actual or estimated time, because the API's own delay field is not always consistent.
- Real delays of 3 hours or more are rare, so this is a replay: the cover is treated as bought before departure.
  Screens label the flight "Live data" or "Simulated".
- A delay that later grows past the second level tops the payout up (1,500 then 3,000 in total). Nothing is paid twice.

## Deploy to Cloud Run

State lives in one SQLite file, so run a single instance.

    gcloud run deploy calmy --source . --region asia-southeast1 --allow-unauthenticated \
      --min-instances 1 --max-instances 1 \
      --set-env-vars GOOGLE_CLOUD_PROJECT=<project>,GOOGLE_CLOUD_LOCATION=us-central1,AVIATIONSTACK_KEY=<optional>

The service account needs the Vertex AI User role for Gemini. Without it the app still works
and uses the template explanation.

## Rebuild the stylesheet

    cd build && npm install && npx tailwindcss -c tailwind.config.js -i in.css -o ../static/app.css --minify
