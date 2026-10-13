"""Illustrative pricing model for KODA delay cover.

This is a sensitivity tool, not a forecast and not an actuarial price. Every input below is either a figure from a
cited public source or a clearly labelled hypothetical. The point of the model is to show which assumptions move the
answer, and by how much, so the unvalidated S$5 demo premium can be discussed with real arithmetic attached.

Run it:
    python3 tools/pricing_model.py

Change an assumption in ASSUMPTIONS and run it again. Nothing here touches the app, the demo premium or the
claims logic: the premium the product charges lives in the Rules table per market and is edited in the console.

Why the outcomes are mutually exclusive: KODA's delay cover pays one claim per trip and the largest applicable
figure is the total (`engine.entitlement`). A flight cannot both "depart 3 to 6 hours late" and "be cancelled" for
payout purposes, so the probabilities below are split into exclusive buckets and never added on top of each other.
"""

# --------------------------------------------------------------------------------------------------- assumptions

ASSUMPTIONS = {
    # ---- What the product pays. SOURCED: KODA's own Singapore rules (app/markets.py, Rules page in the console).
    "payout_tier1_sgd": 45.0,          # departs 3h or more late, under 6h
    "payout_tier2_sgd": 90.0,          # departs 6h or more late
    "payout_cancel_sgd": 90.0,         # flight cancelled
    "points_bonus": 0.25,              # payouts are made in points worth 25% more at an airport partner

    # ---- How often a claim happens, per insured departure.
    # HYPOTHETICAL. No public source gives the delay-length tail for Changi departures. Public data gives an
    # on-time percentage and a cancellation count, nothing about how long the late flights were late.
    # These three buckets are exclusive and are the main thing an insurer would have to replace with real data.
    "p_cancelled": 0.0175,             # DERIVED, not published: 272 cancellations / 15,530 Changi departures, Jan 2026 (NextFly)
    "p_delay_3_to_6h": 0.020,          # HYPOTHETICAL central case
    "p_delay_6h_plus": 0.005,          # HYPOTHETICAL central case

    # ---- What it costs to run, per insured departure. All HYPOTHETICAL unless noted.
    "gst_rate": 0.09,                  # Singapore GST. TREATMENT UNCONFIRMED: cover for travel outside Singapore
                                       # may be zero-rated. Set to 0.0 to see the effect of zero-rating.
    "distribution_commission": 0.25,   # share of gross premium paid to an airline or booking site that sells the cover
    "payout_fee_sgd": 0.35,            # cost of moving money on a claim that pays (card payout or bank transfer)
    "flight_data_cost_sgd": 0.04,      # flight status lookups over the life of one insured flight
    "servicing_cost_sgd": 0.30,        # cloud, storage and support attributable to one policy
    "human_review_cost_sgd": 12.0,     # cost of a person reviewing one held claim
    "p_held_given_claim": 0.12,        # DERIVED from the demo book: 3 claims held of 24 decisions. A person only
                                       # reviews a claim, so this is a share of claims, never of all insured flights.
    "p_support_contact": 0.04,         # share of insured flights where the traveler contacts support at all
    "support_cost_sgd": 1.50,          # cost of a support contact that the assistant does not resolve by itself
    "p_assistant_resolves": 0.70,      # share of those contacts the assistant handles with no person involved

    # ---- Risk margin the insurer would want on top of expected cost.
    "risk_margin": 0.15,               # share of net premium held back for volatility and adverse years
}

PREMIUMS_TO_TEST = [3.0, 4.0, 5.0, 7.0, 9.0]

# Frequency scenarios. The central case is ASSUMPTIONS as written; the others scale the two delay buckets only,
# because the cancellation rate is the one frequency figure with a public anchor.
FREQUENCY_SCENARIOS = {
    "Low delay tail": 0.5,
    "Central (as set above)": 1.0,
    "High delay tail": 2.0,
    "Severe (correlated disruption)": 4.0,
}


# --------------------------------------------------------------------------------------------------- the model

def expected_claims_cost(a: dict, frequency_multiplier: float = 1.0) -> dict:
    """Expected claims cost for one insured departure, from mutually exclusive outcomes.

    Returns the cost and the pieces behind it, so a reader can see where the number comes from.
    """
    p_cancel = a["p_cancelled"]
    p_t2 = a["p_delay_6h_plus"] * frequency_multiplier
    p_t1 = a["p_delay_3_to_6h"] * frequency_multiplier
    # A cancelled flight cannot also be a late departure, so the delay buckets are capped by what is left.
    room = max(0.0, 1.0 - p_cancel)
    p_t1, p_t2 = min(p_t1, room), min(p_t2, room)
    outcomes = [
        ("Cancelled", p_cancel, a["payout_cancel_sgd"]),
        ("Departs 6h or more late", p_t2, a["payout_tier2_sgd"]),
        ("Departs 3h to 6h late", p_t1, a["payout_tier1_sgd"]),
    ]
    benefit_cost = sum(p * payout for _, p, payout in outcomes)
    p_any_claim = sum(p for _, p, _ in outcomes)
    # Points cost the insurer their face value; the 25% bonus is a partner-funded uplift in the demo design, so it
    # is shown separately rather than silently assumed to be free.
    bonus_cost_if_funded_by_us = benefit_cost * a["points_bonus"]
    return {
        "outcomes": outcomes,
        "p_any_claim": p_any_claim,
        "benefit_cost": benefit_cost,
        "payout_fees": p_any_claim * a["payout_fee_sgd"],
        "bonus_cost_if_funded_by_us": bonus_cost_if_funded_by_us,
        "total_excluding_bonus": benefit_cost + p_any_claim * a["payout_fee_sgd"],
    }


def expected_operating_cost(a: dict, frequency_multiplier: float = 1.0) -> dict:
    """Expected variable cost of running one insured departure, excluding the claim payments themselves.

    Human review is a cost per *held claim*, so it scales with how often a claim happens, not with how many
    policies are sold. Support is separate: a traveler can ask a question on a flight that never claims.
    """
    p_claim = expected_claims_cost(a, frequency_multiplier)["p_any_claim"]
    review = p_claim * a["p_held_given_claim"] * a["human_review_cost_sgd"]
    support = a["p_support_contact"] * (1 - a["p_assistant_resolves"]) * a["support_cost_sgd"]
    return {
        "flight_data": a["flight_data_cost_sgd"],
        "servicing": a["servicing_cost_sgd"],
        "human_review": review,
        "support": support,
        "total": a["flight_data_cost_sgd"] + a["servicing_cost_sgd"] + review + support,
    }


def contribution(a: dict, gross_premium: float, frequency_multiplier: float = 1.0) -> dict:
    """Expected contribution per insured departure at one premium point."""
    gst = gross_premium * a["gst_rate"] / (1 + a["gst_rate"])      # GST treated as included in the price shown
    commission = (gross_premium - gst) * a["distribution_commission"]
    net_premium = gross_premium - gst - commission
    claims = expected_claims_cost(a, frequency_multiplier)
    ops = expected_operating_cost(a, frequency_multiplier)
    before_margin = net_premium - claims["total_excluding_bonus"] - ops["total"]
    margin_wanted = net_premium * a["risk_margin"]
    return {
        "gross_premium": gross_premium, "gst": gst, "commission": commission, "net_premium": net_premium,
        "claims_cost": claims["total_excluding_bonus"], "operating_cost": ops["total"],
        "contribution": before_margin,
        "contribution_after_risk_margin": before_margin - margin_wanted,
        "loss_ratio": claims["total_excluding_bonus"] / net_premium if net_premium else float("inf"),
        "p_any_claim": claims["p_any_claim"],
    }


def break_even_premium(a: dict, frequency_multiplier: float = 1.0) -> float:
    """The gross premium at which expected contribution is zero, before any risk margin."""
    claims = expected_claims_cost(a, frequency_multiplier)["total_excluding_bonus"]
    ops = expected_operating_cost(a, frequency_multiplier)["total"]
    needed_net = claims + ops
    # Undo commission then GST to get back to a gross price.
    gross_before_gst = needed_net / (1 - a["distribution_commission"])
    return gross_before_gst * (1 + a["gst_rate"])


# --------------------------------------------------------------------------------------------------- reporting

def _money(x: float) -> str:
    return f"S${x:,.2f}"


def report() -> None:
    a = ASSUMPTIONS
    print("KODA delay cover: illustrative pricing sensitivity")
    print("Not a forecast, not an actuarial price. Frequencies marked HYPOTHETICAL in the source are invented")
    print("for sensitivity only. See docs/PRICING_RESEARCH.md for sources and limitations.\n")

    c = expected_claims_cost(a)
    print("Expected claims cost per insured departure, central case")
    for name, p, payout in c["outcomes"]:
        print(f"  {name:<26} p={p * 100:5.2f}%  x {_money(payout):>9}  = {_money(p * payout)}")
    print(f"  {'Any claim':<26} p={c['p_any_claim'] * 100:5.2f}%")
    print(f"  Benefit cost                                        {_money(c['benefit_cost'])}")
    print(f"  Payout fees                                         {_money(c['payout_fees'])}")
    print(f"  Expected claims cost                                {_money(c['total_excluding_bonus'])}")
    print(f"  (If KODA, not the partner, funded the 25% points bonus, add {_money(c['bonus_cost_if_funded_by_us'])})\n")

    ops = expected_operating_cost(a)
    print("Expected operating cost per insured departure, central case")
    print(f"  Flight status data                                  {_money(ops['flight_data'])}")
    print(f"  Servicing and infrastructure                        {_money(ops['servicing'])}")
    print(f"  Human review ({a['p_held_given_claim'] * 100:.0f}% of claims x {_money(a['human_review_cost_sgd'])})          {_money(ops['human_review'])}")
    print(f"  Support a person has to handle                      {_money(ops['support'])}")
    print(f"  Expected operating cost                             {_money(ops['total'])}\n")

    print("Contribution by premium point and delay-tail scenario")
    print("  Each cell is expected contribution per insured departure, after claims and operating cost,")
    print("  before the risk margin. A negative number means the premium does not cover expected cost.\n")
    head = "  Premium  " + "".join(f"{name:>26}" for name in FREQUENCY_SCENARIOS)
    print(head)
    for prem in PREMIUMS_TO_TEST:
        row = f"  {_money(prem):<9}"
        for mult in FREQUENCY_SCENARIOS.values():
            r = contribution(a, prem, mult)
            row += f"{_money(r['contribution']) + f' ({r['loss_ratio'] * 100:.0f}% LR)':>26}"
        print(row + ("   <-- demo price" if prem == 5.0 else ""))

    print("\nBreak-even gross premium by scenario (expected contribution = 0, no risk margin)")
    for name, mult in FREQUENCY_SCENARIOS.items():
        print(f"  {name:<32} {_money(break_even_premium(a, mult))}")

    print("\nAt the S$5 demo price, central case")
    r = contribution(a, 5.0)
    print(f"  Gross premium                                       {_money(r['gross_premium'])}")
    print(f"  Less GST at {a['gst_rate'] * 100:.0f}% (treatment unconfirmed)          -{_money(r['gst'])}")
    print(f"  Less distribution commission at {a['distribution_commission'] * 100:.0f}%             -{_money(r['commission'])}")
    print(f"  Net premium retained                                {_money(r['net_premium'])}")
    print(f"  Less expected claims                                -{_money(r['claims_cost'])}")
    print(f"  Less expected operating cost                        -{_money(r['operating_cost'])}")
    print(f"  Expected contribution                               {_money(r['contribution'])}")
    print(f"  After a {a['risk_margin'] * 100:.0f}% risk margin                          {_money(r['contribution_after_risk_margin'])}")
    print(f"  Implied loss ratio                                  {r['loss_ratio'] * 100:.0f}%")

    print("\nWhich assumption moves the answer most, at S$5 central case")
    print("  Each line changes one input by the amount shown and reports the new expected contribution.")
    base = contribution(a, 5.0)["contribution"]
    for key, label, factor in [
        ("p_delay_3_to_6h", "3 to 6 hour delay rate", 2.0),
        ("p_delay_6h_plus", "6 hour or more delay rate", 2.0),
        ("p_cancelled", "cancellation rate", 2.0),
        ("distribution_commission", "distribution commission", 1.6),
        ("p_held_given_claim", "share of claims needing a person", 2.0),
        ("human_review_cost_sgd", "cost of a human review", 2.0),
        ("payout_tier1_sgd", "tier 1 payout", 1.5),
    ]:
        trial = dict(a)
        trial[key] = a[key] * factor
        got = contribution(trial, 5.0)["contribution"]
        print(f"  {label:<28} x{factor:<4} -> {_money(got):>10}  (change {_money(got - base)})")

    print("\nFor comparison, what the app's own in-flight pricing assumes (app/forecast.py)")
    print("  p_claim = P(delay of 30 min or more) x HEAVY_SHARE(0.35); expected payout = p_claim x tier 1;")
    print("  suggested price = expected payout / TARGET_LOSS_RATIO(0.6).")
    for prob in (9, 20, 32, 55):
        p_claim = prob / 100 * 0.35
        expected = p_claim * a["payout_tier1_sgd"]
        print(f"  A {prob:>2}% delay forecast implies p_claim {p_claim * 100:5.2f}%, expected payout {_money(expected)}, "
              f"suggested price {_money(max(5.0, expected / 0.6))}")
    print("  Note: HEAVY_SHARE says 35% of 30-minute delays become 3-hour delays. That is an unvalidated")
    print("  assumption and it is the single most load-bearing number in the app's price suggestion.")


if __name__ == "__main__":
    report()
