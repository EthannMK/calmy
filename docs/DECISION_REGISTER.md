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

## Pricing assumptions: not validated

Full working in `docs/PRICING_RESEARCH.md` (researched 13 Oct 2026). Every figure here is a product assumption. None
has been tested with customers, reviewed by an actuary, or quoted by an insurer. Do not describe any of them as
market-validated, actuarially fair or benchmarked.

| ID | Assumption | Value | Validation status | Where it is set |
|---|---|---|---|---|
| PR-1 | Delay cover premium | S$5 per flight | **Unvalidated product assumption.** Roughly break-even under central hypothetical frequencies; negative after a risk margin; loses money if the delay tail is twice our guess. Break-even is S$4.88 central, S$6.91 on a high tail. | Rules table per market, console Rules page |
| PR-2 | Baggage cover premium | S$2 per flight | Unvalidated. Not modelled; the research covers delay cover only. | Rules table per market |
| PR-3 | Share of departures delayed 3 to 6 hours | 2.0% | **Invented for sensitivity.** No public source gives the delay tail for Changi. | `tools/pricing_model.py` |
| PR-4 | Share delayed 6 hours or more | 0.5% | **Invented for sensitivity.** | `tools/pricing_model.py` |
| PR-5 | Cancellation rate | 1.75% | Derived by us from 272 of 15,530 Changi departures, Jan 2026 (NextFly, which names no data source). One month, one airport. | `tools/pricing_model.py` |
| PR-6 | Distribution commission | 25% of gross premium | Invented. No partner has quoted anything. | `tools/pricing_model.py` |
| PR-7 | GST treatment of the premium | 9%, included in the price | **Unconfirmed.** Cover for travel outside Singapore may be zero-rated. Needs a tax opinion. | `app/markets.py` tax_rate, `tools/pricing_model.py` |
| PR-8 | Who funds the 25% points bonus | The airport partner | Asserted in our own design. No partner has agreed to it. If KODA funded it, add 25% to expected claims cost. | Product design |
| PR-9 | `HEAVY_SHARE` in the app's price suggestion | 0.35 | **Unvalidated and load-bearing.** It asserts 35% of 30-minute delays become 3-hour delays, and it drives the suggested price and the displayed reserve. The same function prices only tier 1, so it understates severity while overstating frequency. | `app/forecast.py` |

Two product questions the pricing arithmetic exposed, for the product owner on their merits and **not** to be decided
on the economics:

| ID | Question | Why it came up |
|---|---|---|
| PR-A | Should a cancellation announced weeks ahead, where the airline rebooks at no cost, pay the same S$90 as a same-day cancellation? | Paying S$90 on any cancellation is S$1.58 per insured flight, over half of expected claims cost and about a third of the premium. It is the largest single exposure in the product. |
| PR-B | Should the cancellation benefit be the tier-2 amount, or a separate smaller figure? | Same reason. Most comprehensive policies treat the two cases differently. |

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
| P-6 | Decide the flight-data provider of record and its definition of "actual departure" (gate versus runway time). |
| P-7 | Re-verify the FlySmart citations in `docs/INSURANCE_RULES.md` by hand. The product name is "FlySmart Flight Delay Cover" (the earlier "FlySmart DelayCover" came from the PDF filename), and the policy host blocks automated retrieval, so the mechanics attributed to it could not be re-checked on 13 Oct 2026. |
| P-8 | Get the premium priced by a licensed insurer or an actuary. Public data does not contain the delay tail, so it cannot be answered from research (`docs/PRICING_RESEARCH.md`). |
