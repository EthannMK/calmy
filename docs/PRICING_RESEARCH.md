# KODA delay cover: pricing research and validation status

**Status of the S$5 premium: an unvalidated product assumption.** It has not been tested with customers, reviewed by
an actuary, or quoted by an insurer. Nothing in this document validates it. What this document does is separate the
price question into parts that can be answered from evidence and parts that cannot, record what the evidence actually
says, and show with explicit arithmetic which unknowns decide the answer.

Research date: **13 October 2026**. Every source below was accessed on that date. Prices and products change, so the
access date matters as much as the figure.

Do not describe KODA as commercially priced, market-benchmarked or actuarially fair. It is a prototype with a
placeholder price.

---

## 1. What we were able to verify, and what we were not

| Reference | Accessed | What we got | What we could not get |
|---|---|---|---|
| **Tiq Travel Insurance** (Etiqa Insurance Pte. Ltd., Singapore) | 13 Oct 2026 | Automated flight delay payout, no manual claim, from 3 hours, S$50 per 3 hours up to S$300 (Entry and Savvy) or S$500 (Luxury). Single-trip plans "from just $8.78", footnoted as Zone 1, 1 day, Entry plan, with 55% off. Add-ons from S$0.64. Automation limited to flights departing and returning to Singapore; transit delays must be claimed manually. Flight data provider not named. | Any price for the delay benefit on its own. It is a benefit inside a travel insurance plan, not a standalone product. Savvy, Luxury and annual prices are not shown. |
| **Trip.com Singapore, Trip Delay Cover** (underwritten by Chubb Insurance Singapore and MSIG Insurance Singapore) | 13 Oct 2026 | Chubb: S$200 per 6 hours up to S$800, threshold 6 consecutive hours, plans from S$22.50. MSIG: S$200 per 8 hours, max S$1,000, plans from S$28.00 return and S$35.00 one way. MSIG requires check-in per the original itinerary and written confirmation of the delay from the carrier. | A standalone delay price. Both are comprehensive travel insurance. The "from" prices cover the whole bundle. |
| **DBS / POSB "FlySmart Flight Delay Cover"** | 13 Oct 2026 | The policy wording PDF exists and is hosted on posb.com.sg. The document title in search results reads "FlySmart Flight Delay Cover". | **The PDF could not be fetched today: the host disallows automated retrieval.** We could not re-verify the premium, the payout tiers, the current availability, or the eligibility rules. |
| **NextFly, Changi January 2026 statistics** | 13 Oct 2026 | Changi departures, January 2026: on-time performance 71.51%, 272 departing flights cancelled out of 15,530 departures. No underlying dataset or authority named by the page. | Any breakdown of delay length. Nothing on how many departures exceeded 1, 3 or 6 hours. |
| **destination.com Flight-Delay Index 2026** | 13 Oct 2026 | Changi ranked second globally at 88.9% on-time for calendar year 2025, measured as arrival within 15 minutes, with sources including Cirium and airport operator reports. | Any delay-length distribution. The report gives no share of flights delayed over 1, 3 or 6 hours for Changi or anywhere else. |

**A naming correction for our own documents.** `docs/INSURANCE_RULES.md` calls the product "FlySmart DelayCover".
The evidence available today supports **"FlySmart Flight Delay Cover"** as the product name, with `delaycover` appearing
only in the PDF filename. The mechanics attributed to FlySmart in that document were taken from an earlier read of the
wording and **could not be re-verified today**. They should be treated as unconfirmed until someone opens the PDF by
hand and checks them.

---

## 2. The like-for-like problem

**No public standalone per-flight delay premium was found for Singapore.** Every delay benefit we could attach a price
to is a component of a comprehensive travel insurance policy that also covers medical costs, baggage, cancellation and
personal liability. Comparing S$5 against those prices is not a comparison of like with like, and we should not present
it as one.

What a fair comparison looks like, on the evidence we have:

| | KODA (prototype) | Tiq / Etiqa | Trip.com via Chubb | Trip.com via MSIG |
|---|---|---|---|---|
| Sold as | Delay cover on its own, per flight | Benefit inside a travel plan | Benefit inside a travel plan | Benefit inside a travel plan |
| Delay threshold | 3 hours | 3 hours | 6 consecutive hours | 8 hours |
| Delay benefit | S$45 at 3h, S$90 total at 6h | S$50 per 3h, max S$300 or S$500 | S$200 per 6h, max S$800 | S$200 per 8h, max S$1,000 |
| Cancellation | S$90 | Inside the plan's own cancellation cover | Inside the plan | Inside the plan |
| Claim process | Automatic, no form | Automatic, no manual claim | Claim required | Claim required, written confirmation from the carrier |
| Also includes | Nothing else. Baggage is a separate S$2 cover | Medical, baggage, cancellation and more | Medical, baggage, cancellation and more | Medical, baggage, cancellation and more |
| Price | **S$5, unvalidated assumption** | From S$8.78 for the whole plan (Zone 1, 1 day, Entry, 55% off) | From S$22.50 for the whole plan | From S$28.00 return, S$35.00 one way |

Three things follow from that table, and only the first two are about price.

**Our delay benefit is the smallest of the four.** S$45 at three hours against Tiq's S$50 per three-hour block to a
S$300 ceiling. A traveler comparing benefit alone would prefer Tiq, and Tiq's plan includes everything else as well.
Our product does not win on benefit size. It wins, if it wins at all, on being buyable for one flight without a travel
policy, and on paying without a form.

**The automatic payout is not a differentiator in this market.** Tiq already pays delay benefits automatically from a
3-hour threshold with no manual claim, in Singapore, today. Our pitch should not claim that automatic payout is new.
What is still unusual is the rest of it: cover sold per flight against any booking, the insurer-side workspace, and a
person reachable in the same conversation. That is the honest claim.

**The paperwork problem is real and documented.** MSIG's delay benefit on Trip.com requires written confirmation of the
delay from the carrier. That is a citable example of the friction KODA removes, and it is better evidence for the
problem statement than any figure we have been quoting.

---

## 3. Facts and assumptions, kept apart

**Observed, with a source and a date.** Everything in section 1.

**Our own product's figures**, which are design choices, not market evidence: S$5 delay cover, S$2 baggage cover,
S$45 and S$90 payout tiers, S$90 for a cancellation, S$40 lost bag limit, 100 points to S$1 with a 25% bonus, cash-out
at 80% of spending value. These live in the Rules table per market and are edited on the console Rules page, so a
scenario can be compared without touching the claims logic.

**Unvalidated assumptions that decide the answer**, none of which have a source:

| Assumption | Value used | Why it is a guess |
|---|---|---|
| Share of insured departures delayed 3 to 6 hours | 2.0% | No public source gives the delay tail for Changi. |
| Share delayed 6 hours or more | 0.5% | Same. |
| Share cancelled | 1.75% | Derived by us from 272 of 15,530 Changi departures in January 2026. One month, one airport, from a page that names no data source. |
| Distribution commission to an airline or booking site | 25% | No quote from any partner. Embedded insurance commissions vary widely. |
| Cost of a person reviewing a held claim | S$12 | No staffing model exists. |
| GST treatment | 9%, included in the price | **Unconfirmed.** Cover for travel outside Singapore may be zero-rated. This needs a tax opinion, not a guess. |
| Who funds the 25% points bonus | The airport partner | Asserted in our own design. No partner has agreed to anything. |

---

## 4. The illustrative model

The model lives in `tools/pricing_model.py`. Every input is at the top of that file and labelled either sourced or
hypothetical. Run `python3 tools/pricing_model.py` to change an assumption and see the effect.

It is a sensitivity tool. It is not a forecast. The frequencies driving it are invented.

Outcomes are kept mutually exclusive, because KODA's delay cover pays one claim per trip and the largest applicable
figure is the total (`engine.entitlement`). A flight cannot both depart three hours late and be cancelled for payout
purposes, so the probabilities are never added on top of each other.

### Expected cost per insured departure, central hypothetical case

| Outcome | Probability | Payout | Expected cost |
|---|---|---|---|
| Cancelled | 1.75% | S$90 | S$1.58 |
| Departs 6h or more late | 0.50% | S$90 | S$0.45 |
| Departs 3h to 6h late | 2.00% | S$45 | S$0.90 |
| **Any claim** | **4.25%** | | **S$2.93** |
| Payout fees | | | S$0.01 |
| Flight status data, servicing, review and support | | | S$0.42 |

Human review is costed per held claim, not per policy sold. A person only ever looks at a claim, so at 4.25% claim
frequency and 12% of claims held, that is about one review for every 200 policies. It is a small number, and that is
the point of the automation.

### Contribution by premium point and delay tail

Expected contribution per insured departure, after claims and operating cost, before any risk margin. LR is the
implied loss ratio against net premium retained.

| Premium | Low tail (half) | Central | High tail (double) | Severe (quadruple) |
|---|---|---|---|---|
| S$3.00 | −S$0.60 (110% LR) | −S$1.29 (142% LR) | −S$2.69 (208% LR) | −S$5.48 (340% LR) |
| S$4.00 | S$0.09 (82% LR) | −S$0.61 (107% LR) | −S$2.00 (156% LR) | −S$4.79 (255% LR) |
| **S$5.00 (demo price)** | **S$0.78 (66% LR)** | **S$0.08 (85% LR)** | **−S$1.31 (125% LR)** | **−S$4.10 (204% LR)** |
| S$7.00 | S$2.15 (47% LR) | S$1.46 (61% LR) | S$0.06 (89% LR) | −S$2.73 (146% LR) |
| S$9.00 | S$3.53 (37% LR) | S$2.83 (47% LR) | S$1.44 (69% LR) | −S$1.35 (113% LR) |

Break-even gross premium: S$3.87 on a low tail, **S$4.88 central**, S$6.91 on a high tail, S$10.96 in a severe
correlated event.

### What this says about S$5

Under the central hypothetical case, S$5 lands almost exactly on break-even. Expected contribution is S$0.08 per
insured flight and the implied loss ratio is 85%. After a 15% risk margin it is negative. It does not recover any
fixed cost, any acquisition cost beyond the assumed commission, or any capital charge.

So S$5 is not an absurd number. It is a thin one, and it is only break-even if the invented frequencies are roughly
right. **That is not a validation. It means the price is plausible enough to be worth testing properly, and not safe
enough to build a business case on.**

### Which assumption decides the answer

Doubling one input at a time, at S$5 central:

| Change | Effect on contribution |
|---|---|
| Cancellation rate doubled | −S$1.61 |
| 3 to 6 hour delay rate doubled | −S$0.94 |
| Distribution commission 25% to 40% | −S$0.69 |
| 6 hour or more delay rate doubled | −S$0.46 |
| Tier 1 payout S$45 to S$67.50 | −S$0.45 |
| Share of claims needing a person doubled | −S$0.06 |
| Cost of a human review doubled | −S$0.06 |

**The cancellation rule is the largest single exposure in the product.** Paying the full S$90 tier-2 amount on any
cancellation costs S$1.58 per insured flight on its own, which is more than half of expected claims cost and about a
third of the gross premium. It rests on one derived figure from one month at one airport. Two product questions follow,
and both are for the product owner rather than for engineering:

1. Should a cancellation announced weeks ahead, where the airline rebooks the traveler at no cost, pay the same S$90 as
   a cancellation on the day? Most comprehensive policies treat those differently.
2. Should the cancellation benefit be the tier-2 amount, or a separate smaller figure?

Neither should be changed to make the economics look better. They are listed because the arithmetic exposed them.

### A second, separate finding about the app's own pricing code

`app/forecast.py` suggests a per-flight price with this logic: `p_claim = P(delay of 30 minutes or more) × 0.35`,
then `expected payout = p_claim × tier 1`, then `suggested price = expected payout / 0.6`.

The constant `HEAVY_SHARE = 0.35` asserts that 35% of all 30-minute delays become 3-hour delays. That is an unvalidated
assumption sitting in production code, and it is the most load-bearing number in the price the console suggests and in
the reserve it displays. On a 9% delay forecast it implies a 3.15% chance of a payable claim, which is above the 2.5%
used as the central case in this document.

The same function also prices only tier 1. It ignores tier 2 and cancellations, so it understates severity while
overstating frequency. The two errors point in opposite directions and should not be assumed to cancel out.

Recommendation: leave the constant as it is for the demo, label it in the console as an assumption, and put it on the
list below of things an insurer has to replace with real data.

---

## 5. The four questions, answered separately

**Market positioning: would a customer think S$5 is fair?** Unknown. Nobody has been asked. The comparison in section 2
suggests the benefit is small next to what a S$8.78 bundled plan offers, which is an argument for customer testing
before any price is defended. This needs customer research, not analysis.

**Expected claims cost: what will it pay out per flight?** Between roughly S$1.50 and S$12 per insured departure
depending on the delay tail, against a central hypothetical of S$2.94. The range is that wide because the input is
invented. This needs real flight data, not a better model.

**Financial viability: does S$5 cover claims, costs and a margin?** On the central hypothetical, it covers claims and
variable costs with S$0.08 to spare and nothing for risk, fixed cost or capital. On a high tail it loses money. So: not
demonstrated. This needs an actuary and real exposure data.

**Regulatory and underwriting feasibility.** Out of scope for us to settle, and the binding constraint on all of the
above. A licensed insurer must own the wording, the price, the exclusions and the claims rules. The complaints route in
Singapore runs through the insurer's own process and then FIDReC. Distribution through an airline or booking site has
its own licensing requirements. None of this can be answered by arithmetic. See `P-1` and `P-2` in
`docs/DECISION_REGISTER.md`.

---

## 6. Data required for credible pricing

An insurer or actuary would need, at minimum:

1. **Departure delay distribution by length** for the actual flights in scope, ideally SIN departures by airline, route,
   time of day and season, over several years. Not an on-time percentage. The distribution of the tail is the product.
2. **Cancellation rate split by notice period**, separating cancellations announced days ahead from same-day ones.
3. **Correlation structure.** A storm or an airspace closure causes many claims on one day. The severe column in the
   model is a crude stand-in for this. Real pricing needs the joint distribution, not a multiplier.
4. **Exposure data**: how many policies on which flights, so a single event's maximum loss can be estimated.
5. **Anti-selection evidence**: do buyers of per-flight cover skew towards flights that are already likely to be late?
   Our risk score assumes yes and charges nothing extra for it.
6. **Actual operating cost**, including the flight data contract, payout rails, support staffing and the review rate.
7. **Lapse and purchase timing**: how far ahead cover is bought, which decides how much is known at purchase.
8. **A definition of actual departure agreed with the data provider**, gate time or runway time, since the payout
   depends on it. See `P-6` in the decision register.

---

## 7. Recommendation

**Keep S$5 as the demo price. Do not defend it as a price.**

1. Leave the premium at S$5 in the demo. It is already configurable per market on the Rules page, so scenarios can be
   compared without touching claims logic. No code change is needed to test another price.
2. In the pitch, describe it as a placeholder. If asked, the honest answer is: "S$5 is a demo assumption. Our model
   says it is roughly break-even under central assumptions we have not validated, and the cancellation rule is the
   exposure that would break it. Real pricing needs delay-tail data and an actuary."
3. Test positioning with customers before changing the number. Price research without customers is arithmetic about
   nothing.
4. Refer the pricing itself to an insurer or actuary. The question is not solvable with public data, because the public
   data does not contain the delay tail.
5. Put the two product questions about the cancellation benefit (section 4) to the product owner, on their merits, not
   on the economics.
6. Treat `HEAVY_SHARE = 0.35` in `app/forecast.py` as a known unvalidated assumption and label it in the console.

Nothing in this document should be read as a recommendation to change the payout tiers, the coverage terms or the demo
experience in order to improve the appearance of the economics.
