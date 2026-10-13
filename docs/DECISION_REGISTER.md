# KODA decision register

Owner of every open item: the product owner (Ethan). Date of this register: 13 Oct 2026.
"Implemented as" means what the code does now; every item can be changed.

## Decided and implemented

| ID | Decision | Basis | Implemented as | Changeable via |
|---|---|---|---|---|
| A | A delayed flight later cancelled tops up to the cancellation total; only the difference is sent | KODA wording puts cancellation inside delay cover at the tier-2 amount, with no second claim of the same type; FlySmart pays the maximum benefit once for delayed-then-cancelled | `engine.entitlement` + `run_delay`; `payments.settle` sends the difference | Code (a configuration switch could be added if you want cumulative benefits) |
| B | A delay reported after "on time" goes to a person with both reports | Product owner's stated default; conflicting evidence should not be decided automatically | `engine.late_report`; one review per trip | Code |
| C | Live data pays on actual departure only; an estimate is a forecast | Product owner's stated default; FlySmart uses actual departure | `track.payable`; `live.delay_info` labels the basis | `KODA_PAY_ON_ESTIMATE=1` restores paying on estimates |
| D | Re-checks are paid from the verified delay through the same tiers; reviewers cannot type amounts | Product owner's stated default; GIA: an airline letter substantiates a delay claim | `decide_held(verified_minutes, verified_cancelled)` | Code |
| E | One second review with a reason; admin exception beyond it | Product owner's stated demo rule (a product choice, not law) | `request_second_review`, `reopen_exception` | `SECOND_REVIEWS` in `engine.py` |
| F | Approval and payment are recorded separately; transfers are simulated and labelled | Engineering safeguard | `payments` table and both apps | n/a |
| G | Model output is used only if it adds no number, decision or promise | Engineering safeguard against prompt injection | `copilot.safe_rewrite` | n/a |

## Open: needs your decision

| ID | Question | Recommended | Alternatives | Traveler impact | Insurer impact | Blocks? |
|---|---|---|---|---|---|---|
| D-1 | Confirm the delay is measured on **departure** (as built) | Keep departure: it is instant, automatic and matches FlySmart | Arrival delay (AIG model): closer to the traveler's real loss, but needs arrival data and changes the copy | Departure: a flight that leaves late but lands on time still pays | Arrival: fewer payouts, more data needed | No |
| D-2 | Confirm payouts wait for the **actual** departure | Keep actual-only (now the default) | Pay on estimate (old behaviour, `KODA_PAY_ON_ESTIMATE=1`): faster, but can pay a delay that later shrinks, with no clawback | Actual: paid at departure, not hours before | Estimate: some payouts for delays that never happened | No |
| D-3 | Should a late or conflicting report ever pay automatically, for example from a trusted source? | No: a person decides (now the default) | Pay automatically from named sources | Faster for genuine cases | Exposure to bad data | No |
| D-4 | Is one second review the right limit, and what is the reply time to promise? | Keep one, plus admin exceptions; promise no deadline until an insurer sets one | Two reviews; no limit | Clarity versus more chances | Reviewer workload | No |
| D-5 | Are travelers allowed to see their own risk signals? | No (current) | Show the signals | Transparency | Helps people game the score | No |
| D-6 | Should a payout ever be reversed (fraud found later, duplicate booking)? | Not in the demo | Add reversals with a person's sign-off | Certainty of payment | Recovery of wrong payouts | No |

## Production and compliance (before any real launch)

| ID | Item |
|---|---|
| P-1 | A licensed insurer must own and approve the policy wording, prices, exclusions and claims rules for each market. |
| P-2 | The complaints process (internal dispute resolution, then FIDReC in Singapore) has to be set by that insurer; the in-app review limit must fit it (D-4). |
| P-3 | Real payment rails with confirmed settlement, failure codes and reconciliation; today every transfer is simulated. |
| P-4 | A persistent database and more than one instance; today state is a SQLite file in `/tmp` that resets on every deploy. |
| P-5 | Close the demo routes, sign the flight-status webhook (`KODA_WEBHOOK_SECRET` exists), and require sign-in for flight refresh and the outlet scan. |
| P-6 | Decide the flight-data provider of record and its definition of "actual departure" (gate versus runway time). FlySmart uses gate time. |
