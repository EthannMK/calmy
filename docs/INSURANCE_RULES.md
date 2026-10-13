# KODA demo policy: rules as implemented

Status of this document: **proposed demo-policy assumptions**, not a licensed policy wording. Where a rule comes from
KODA's own published wording (`docs/COVERAGE_AND_WORKFLOW.md`, the in-app cover screens), it says so. Where it is a
design choice, it says that too. Nothing here claims compliance with Singapore law or MAS requirements.

Amounts below are the Singapore market defaults (the Rules page in the console can change them; a change applies to
the next event, never to past decisions). Other markets use the same structure with their own figures.

## 1. Delay cover: what triggers a payout

| Rule | Value | Source |
|---|---|---|
| Tier 1 | S$45 when the flight departs 3 hours or more late | KODA wording, section 1 |
| Tier 2 | S$90 total when it departs 6 hours or more late | KODA wording, section 1 |
| Cancellation | S$90 total | KODA wording, section 1 |
| Ceiling | One delay-cover claim per trip; the largest applicable figure is the total | KODA wording ("a second claim of the same type on the same flight" is not covered) |

Calculation: `engine.entitlement(rules, minutes, cancelled)`. This is the only place a delay-cover amount is
calculated. The automatic path, late reports, re-checks and reviewer approvals all call it. A reviewer cannot type an
amount.

Boundaries (tested): 2h 59m pays nothing, exactly 3h 00m pays tier 1, 5h 59m pays tier 1, exactly 6h 00m pays tier 2.

## 2. What counts as the delay (Decision C)

KODA's wording says a delay is "measured by the airline's real status feed". It does not say departure or arrival.

**Demo assumption, implemented:** the delay is the **actual departure time minus the scheduled departure time**,
both read in the departure airport's own clock. Midnight and time zones therefore cancel out (tested: 23:30 → 02:45
next day = 3h 15m).

- **Live flight data pays only on an actual departure time, or on a cancellation.** An *estimated* departure, or the
  feed's own delay field without timestamps, is a **forecast**. It is stored and shown ("expected 3h late"), and it
  blocks new delay cover from being sold, but it creates no claim and no payout. Setting `KODA_PAY_ON_ESTIMATE=1`
  restores paying on estimates, if you choose that model.
- A flight that leaves on time and arrives late pays nothing under this rule. A flight that leaves 3h late and makes
  up time in the air pays.
- A figure corrected downward after a payout does not claw the payout back. A larger figure raises the total once (§4).
- The demo controls and the flight-status webhook are treated as final reports from a named source; the source is
  recorded on every claim.

Why: the closest real product, DBS/Chubb's FlySmart DelayCover (automatic, fixed amounts, Changi departures), defines
the delay as *Actual Departure Time minus Scheduled Departure Time*, from a flight-data provider. Traditional cover
measures differently: AIG Travel Guard (Section E) uses *arrival* at least 6 hours late, paid per 6-hour block, against
a letter from the carrier. Both are contractual choices; neither is a legal rule.

## 3. Cover purchase limits

- Delay cover cannot be bought once the flight is 30 minutes or more late (on any basis, including a forecast), after
  departure, or after cancellation.
- Delay cover stops selling when the forecast delay chance reaches the "close cover" threshold (default 80%).
- Cover bought while a delay was already likely (55% or more) adds 35 points to the risk score, so the claim goes to a
  person. It is never declined by the system.

These are KODA's own documented rules (wording sections 3 and 8b). FlySmart has a comparable condition (cover must be
bought before the buyer knows of any circumstance that could disrupt the journey).

## 4. A delayed flight that is later cancelled (Decision A)

**Implemented, consistent with KODA's wording.** Delay cover is one benefit with one ceiling. When the outcome gets
worse (a longer delay, or a cancellation after a delay payout), the claim's total rises to what the worse outcome is
worth, and **only the difference is sent**. Example: S$45 paid at 3h 15m; the flight is then cancelled; the total
becomes S$90 and a S$45 top-up is sent. If S$90 was already paid at 6 hours, a cancellation sends nothing more.

Why: KODA's wording lists cancellation inside delay cover with the same S$90 as tier 2, and allows no second claim of
the same type. FlySmart's wording says a delayed flight that is later cancelled pays the maximum benefit, a single
capped amount. AIG forbids claiming one event under two sections. None of them pays delay and cancellation on top of
each other.

Idempotency: the outstanding amount is always *total entitled minus what the payment ledger already holds*. Repeated
events, retries and second clicks cannot send money twice (tested).

## 5. Approval and payment are separate

| Stage | Where it lives |
|---|---|
| Event received, assessed | events log; claim created by the engine |
| Manual review | `decisions.status = held` (risk hold, re-check, conflicting reports) |
| More information requested | `held`, plus an `info_requested` event; the question is in the traveler's Messages |
| Approved / declined | `decisions.status = paid` (approved for payment) or `declined` |
| Payment pending / succeeded / failed | `payments` table, one row per transfer (first payout, then top-ups) |

- Nothing is ever sent for a claim that is not approved.
- KODA points are an internal ledger and always settle. Card and bank transfers are **simulated** and labelled so in
  both apps. No real money moves.
- A failed transfer stays failed and visible ("Approved, transfer being retried" to the traveler). A reviewer retries
  it in place; it is never duplicated.
- Not implemented: reversals and refunds of a payout.

## 6. Re-checks (Decision D)

A traveler whose recorded delay was under the rule can ask for a re-check. The claim goes to a person with **no
amount**: the recorded figure is exactly what is in doubt.

To approve, the reviewer must enter the **delay shown in the airline's record** (or mark the flight cancelled) and
give the reason. The payout is calculated from that figure by `entitlement()`. A verified figure under the rule
cannot be approved; the reviewer declines with the reason, or asks the traveler for the airline's letter. The recorded
figure, the verified figure, who verified it and why are all kept on the claim and in the case history.

Why: the GIA travel-insurance guide says travelers need a letter from the airline confirming the delay; the decision
should rest on that record, not on the traveler's account or on a fixed tier.

## 7. Conflicting reports (Decision B)

If a delay or cancellation is reported after the trip was closed as on time, the system **neither pays nor refuses**.
It opens one review with both reports side by side: the earlier status with its time, and each later report with its
figure, source and time. Repeated identical reports change nothing; a different report is added to the same review.
The reviewer resolves it like a re-check (verified figure and reason to approve; a reason to decline).

## 8. Reviews and appeals (Decision E)

- **Demo rule (a product choice, not a legal one):** a declined claim can go back to a person once ("second review").
  The traveler must say what is new (at least a short sentence). The app explains the limit before they use it.
- A duplicate or simultaneous request creates one review (tested with four parallel requests).
- After the second review the decision is final in the app, and the traveler is pointed to Messages.
- **Exceptions:** an admin can reopen a declined claim beyond the limit, with a reason that is recorded. Reviewers
  and agents cannot.
- Every decline needs a reason, which the traveler sees. Approvals that override recorded data (re-checks, conflicting
  reports) need a reason; approval notes stay internal.
- In Singapore, a consumer who cannot resolve a dispute with an insurer can take it to FIDReC (GIA guide; FlySmart
  wording). Whether KODA's in-app limit fits a real insurer's complaints process is an open question (register D-7).

## 9. AI boundaries

The engine's rules decide money. Gemini only words messages and summaries. Every model rewrite is checked
(`copilot.safe_rewrite`): it may not add a number, a decision or a promise that the grounded text did not contain,
otherwise the grounded text is used. Traveler text is passed to the model as quoted data. The copilot never declines,
shows no probability-like confidence, and on a disputed delay says it cannot verify the airline record.

## Sources

- GIA, *Travel Insurance* consumer guide (updated after 1 July 2024): https://gia.org.sg/images/resources/For-Consumers-PDF-file/GIA_TravelInsurance_Guide.pdf
- DBS TravellerShield Plus, FlySmart DelayCover policy wording (Chubb): https://dbs.com.sg/iwov-resources/media/pdf/insurance/travellershield/policy-wording-flysmart-delaycover.pdf
- AIG Travel Guard Direct policy wording (on or after 18 Jan 2025): https://www.aig.sg/content/dam/aig/apac/singapore/travel/travel-guard-direct-policy-wording-on-after-18-jan-2025.pdf
- MoneySense, introduction to general insurance (fair and prompt claims handling under the General Insurance Code of Practice): https://www.moneysense.gov.sg/introduction-to-general-insurance/
- Not reachable from here: the AIG Travel Guard wording dated 1 Dec 2023 (fetch error), and AviationStack's field documentation (renders only with JavaScript). Timestamp handling is verified against the code and real-record test fixtures instead.
