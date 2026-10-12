"""Market configuration: currency, payout rails, simulated prices and the rules of each market.

The market of a policy is set by where its flight departs. Money is simulated in the market's own
currency. The legal notes summarise the main laws that shape the product in each market and how
KODA handles them. They are a design aid for the demo and are not legal advice. A real launch
needs a licensed insurer and local counsel to confirm the current text of each law.
"""

# Units of local currency per 1 USD. Indicative demo values, used only to show totals in one currency.
FX_PER_USD = {"THB": 36.0, "USD": 1.0, "EUR": 0.92, "GBP": 0.79, "INR": 84.0, "SGD": 1.34,
              "JPY": 150.0, "AUD": 1.52, "BRL": 5.4, "CAD": 1.37}

# Rules every market starts with. Thresholds are the same everywhere. Prices and payouts are
# set by hand in local currency so the numbers look natural. They are not actuarially priced.
BASE_THRESHOLDS = {"delay_tier1_minutes": 180, "delay_tier2_minutes": 360, "risk_threshold": 45}
RULE_LABELS = {
    "delay_tier1_minutes": "Tier 1 delay (minutes)", "delay_tier1_payout": "Tier 1 payout",
    "delay_tier2_minutes": "Tier 2 delay (minutes)", "delay_tier2_payout": "Tier 2 payout",
    "lost_bag_payout": "Lost bag payout", "cancel_payout": "Cancelled flight payout", "risk_threshold": "Auto pay below this risk score",
    "delay_premium": "Delay cover price", "baggage_premium": "Baggage cover price",
}

# (delay price, baggage price, tier 1 payout, tier 2 payout, lost bag payout)
PRICES = {
    "TH": (4, 2, 40, 80, 35), "US": (5, 2, 45, 90, 40), "EU": (5, 2, 40, 80, 40),
    "UK": (5, 2, 45, 90, 40), "IN": (4, 2, 40, 80, 35), "SG": (5, 2, 45, 90, 40),
    "JP": (5, 2, 45, 90, 40), "AU": (5, 2, 45, 90, 40), "BR": (5, 2, 45, 90, 40),
    "CA": (5, 2, 45, 90, 40), "GLOBAL": (5, 2, 45, 90, 40),
}

# Timezone to market. Europe is split by country because EU and UK rules differ.
_TZ_COUNTRY = {
    "Asia/Bangkok": "TH", "Asia/Singapore": "SG", "Asia/Kolkata": "IN", "Asia/Calcutta": "IN", "Asia/Tokyo": "JP",
    "Europe/London": "UK", "Europe/Belfast": "UK", "Europe/Istanbul": "GLOBAL", "Europe/Moscow": "GLOBAL",
    "Europe/Kiev": "GLOBAL", "Europe/Kyiv": "GLOBAL", "Europe/Minsk": "GLOBAL", "Europe/Zurich": "GLOBAL",
    "Europe/Oslo": "GLOBAL",
}


def market_for_timezone(tz: str | None) -> str:
    """Pick the market from an IANA timezone name such as Europe/Paris."""
    if not tz:
        return "GLOBAL"
    if tz in _TZ_COUNTRY:
        return _TZ_COUNTRY[tz]
    if tz.startswith("Europe/"):
        return "EU"
    if tz.startswith("Australia/"):
        return "AU"
    if tz in ("America/Toronto", "America/Vancouver", "America/Edmonton", "America/Winnipeg", "America/Halifax",
              "America/Montreal", "America/St_Johns", "America/Regina"):
        return "CA"
    if tz == "America/Sao_Paulo" or tz.startswith("America/Fortaleza") or tz.startswith("America/Bahia"):
        return "BR"
    if tz.startswith("America/") and tz.split("/")[1] in (
        "New_York", "Chicago", "Denver", "Los_Angeles", "Phoenix", "Anchorage", "Detroit", "Boise",
        "Indiana", "Kentucky", "Juneau", "Honolulu"
    ):
        return "US"
    if tz == "Pacific/Honolulu":
        return "US"
    return "GLOBAL"


DEMO_CURRENCY = "USD"  # every amount in the demo is shown in US dollars, whatever the market's own currency


def _m(name, currency, rails, **kw):
    # `currency` stays on record as the market's real currency. All demo money is in USD.
    return {"name": name, "currency": DEMO_CURRENCY, "local_currency": currency, "rails": list(rails) + [r for r in ("Card refund", "Digital wallet") if r not in rails], **kw}


# Each note: area, the law or rule, what KODA does about it.
MARKETS = {
    "TH": _m("Thailand", "THB", ["PromptPay", "Bank transfer"],
             regulator="Office of Insurance Commission (OIC)", tax="Taxes included where they apply.",
             statutory=None,
             notes=[
                 ("Insurance", "Non-life Insurance Act B.E. 2535, supervised by the OIC", "Cover must be underwritten by a licensed non-life insurer. The agency only sells it."),
                 ("Distribution", "Insurance broker and agent licensing under OIC rules", "The selling agency needs the right OIC licence or must act for the insurer."),
                 ("Data", "Personal Data Protection Act B.E. 2562 (PDPA)", "Explicit consent is shown and recorded before purchase."),
                 ("Air passengers", "CAAT passenger rights notification", "Airline assistance duties stay with the airline. KODA pays on top."),
             ]),
    "US": _m("United States", "USD", ["ACH bank transfer", "Zelle", "Debit card push"],
             regulator="State insurance departments (NAIC model laws)", tax="State premium tax varies by state.",
             statutory="No federal cash compensation for delays. The DOT refund rule gives a refund right for cancelled flights and significant changes.",
             notes=[
                 ("Insurance", "Insurance is regulated state by state", "Needs a licensed insurer and a licensed producer in the traveler's state. Rates and forms may need state filing."),
                 ("Claims handling", "State unfair claims settlement practices acts", "Claims are acknowledged and decided promptly. Time limits differ by state, so the strictest target is used."),
                 ("Air passengers", "DOT automatic refund rule (cancellations and significant changes)", "KODA never replaces a refund. The traveler is told to claim it from the airline."),
                 ("Data", "State privacy laws such as the California CCPA and CPRA", "A privacy notice and consent are shown before purchase."),
             ]),
    "EU": _m("European Union", "EUR", ["SEPA Instant", "SEPA transfer"],
             regulator="National supervisors under EIOPA", tax="Insurance premium tax varies by country.",
             statutory="EU Regulation 261/2004: the airline owes 250, 400 or 600 EUR for an arrival delay of 3 hours or more, by distance, unless the cause was extraordinary. It also owes meals and care.",
             notes=[
                 ("Distribution", "Insurance Distribution Directive (EU) 2016/97", "A short Insurance Product Information Document (IPID) is shown before purchase. The seller must check demands and needs."),
                 ("Air passengers", "Regulation (EC) 261/2004", "The cover pays on top of airline compensation. Travelers are told to claim from the airline too."),
                 ("Data", "GDPR (EU) 2016/679", "Consent and purpose are shown. Article 22: a person can always review a claim, and KODA never denies a claim automatically."),
                 ("Baggage", "Montreal Convention", "The airline is liable first for lost bags. The cover tops up what the airline does not pay."),
             ]),
    "UK": _m("United Kingdom", "GBP", ["Faster Payments", "Bank transfer"],
             regulator="Financial Conduct Authority (FCA)", tax="Insurance Premium Tax on travel cover is 20%, included in the price.",
             tax_rate=0.20,
             statutory="UK Regulation 261: the airline owes 220, 350 or 520 GBP for an arrival delay of 3 hours or more, by distance, unless the cause was extraordinary.",
             notes=[
                 ("Distribution", "FCA ICOBS rules and the IPID requirement", "An IPID is shown before purchase. Claims are handled promptly and fairly."),
                 ("Conduct", "FCA Consumer Duty and fair value rules", "Price and payout are shown plainly. The fair value test is a launch step for the insurer."),
                 ("Air passengers", "UK261 (retained EU law)", "The cover pays on top of airline compensation."),
                 ("Data", "UK GDPR and the Data Protection Act 2018", "Consent is recorded. A person can review any claim."),
             ]),
    "IN": _m("India", "INR", ["UPI", "IMPS bank transfer"],
             regulator="Insurance Regulatory and Development Authority (IRDAI)", tax="GST of 18% on premiums is included in the price.", tax_rate=0.18,
             statutory="DGCA rules require airlines to give meals and refreshments by delay length and to offer a refund or another flight for long delays. Cash compensation rules apply mainly to cancellations and denied boarding.",
             notes=[
                 ("Insurance", "Insurance Act 1938 and IRDAI regulations on protection of policyholders", "Needs a licensed insurer. Policy wording and claims timelines follow IRDAI rules."),
                 ("Air passengers", "DGCA Civil Aviation Requirements on passenger facilitation", "Airline duties stay with the airline. KODA pays on top."),
                 ("Data", "Digital Personal Data Protection Act 2023", "Clear consent notice shown and recorded before purchase."),
                 ("Payments", "UPI payout rail", "Payouts are sent to the traveler's own account only."),
             ]),
    "SG": _m("Singapore", "SGD", ["PayNow", "Bank transfer (FAST)"],
             regulator="Monetary Authority of Singapore (MAS)", tax="GST of 9% is included where it applies.", tax_rate=0.09,
             statutory="No statutory delay compensation. Airlines follow their own conditions.",
             notes=[
                 ("Insurance", "Insurance Act 1966 and MAS notices on conduct and claims", "Needs a licensed insurer. Product information and fair dealing outcomes apply."),
                 ("Data", "Personal Data Protection Act 2012", "Consent recorded before purchase."),
                 ("Payments", "PayNow and FAST payout rails", "Payouts go to the traveler's own account only."),
             ]),
    "JP": _m("Japan", "JPY", ["Zengin bank transfer", "Konbini payout"],
             regulator="Financial Services Agency (FSA)", tax="Insurance premiums are exempt from consumption tax.",
             statutory="No statutory delay compensation.",
             notes=[
                 ("Insurance", "Insurance Business Act", "Needs a licensed insurer. Sellers register as insurance agents. Important matters are explained before sale."),
                 ("Data", "Act on the Protection of Personal Information (APPI)", "Purpose of use is shown and consent recorded."),
             ]),
    "AU": _m("Australia", "AUD", ["PayID", "Bank transfer (NPP)"],
             regulator="ASIC and APRA", tax="GST of 10% is included in the price.", tax_rate=0.10,
             statutory="No statutory delay compensation. Australian Consumer Law guarantees apply to the airline service.",
             notes=[
                 ("Distribution", "Corporations Act design and distribution obligations (DDO)", "A Target Market Determination is published. Product Disclosure Statement and Financial Services Guide are made available before purchase."),
                 ("Claims handling", "General Insurance Code of Practice", "A claim decision is given within 10 business days of getting all the information. KODA aims for hours."),
                 ("Contracts", "Insurance Contracts Act 1984 (duty of utmost good faith)", "Terms are plain and claims are decided fairly."),
                 ("Data", "Privacy Act 1988", "Privacy notice and consent shown before purchase."),
             ]),
    "BR": _m("Brazil", "BRL", ["Pix", "Bank transfer (TED)"],
             regulator="SUSEP", tax="Taxes (IOF) are included where they apply.",
             statutory="ANAC Resolution 400 sets airline assistance by delay length (communication, food, accommodation). Compensation for damage is decided under consumer law.",
             notes=[
                 ("Insurance", "SUSEP rules and consumer protection code (CDC)", "Needs a licensed insurer. Terms must be clear and not misleading."),
                 ("Air passengers", "ANAC Resolution 400", "Airline duties stay with the airline. KODA pays on top."),
                 ("Data", "General Data Protection Law (LGPD)", "Consent and purpose shown and recorded."),
                 ("Payments", "Pix payout rail (Central Bank of Brazil)", "Payouts go to the traveler's own Pix key only."),
             ]),
    "CA": _m("Canada", "CAD", ["Interac e-Transfer", "Bank transfer (EFT)"],
             regulator="Provincial insurance regulators", tax="Provincial premium tax varies.",
             statutory="Air Passenger Protection Regulations: for delays within the airline's control a large airline owes 400, 700 or 1,000 CAD for 3 to 6, 6 to 9, or 9 or more hours of arrival delay.",
             notes=[
                 ("Insurance", "Provincial insurance acts", "Needs a licensed insurer and an agent licensed in the traveler's province."),
                 ("Air passengers", "Air Passenger Protection Regulations (APPR)", "The cover pays on top of airline compensation."),
                 ("Data", "PIPEDA and Quebec Law 25", "Consent recorded before purchase."),
             ]),
    "GLOBAL": _m("Other countries", "USD", ["Bank transfer", "Card or wallet payout"],
                 regulator="Local insurance regulator", tax="Taxes included where they apply.",
                 statutory="The Montreal Convention makes the airline liable first for lost checked bags, up to 1,519 SDR, and for delay damage.",
                 notes=[
                     ("Insurance", "Local insurance law", "Needs a licensed insurer in the traveler's country. This market is a default for the demo only."),
                     ("Baggage", "Montreal Convention", "The airline is liable first for lost bags. The cover tops up what the airline does not pay."),
                     ("Data", "Local data protection law", "A consent notice is shown and recorded before purchase."),
                 ]),
}

# Shown at checkout in every market: how KODA protects the traveler.
PRINCIPLES = [
    "KODA never denies a claim automatically. Only a person can decline, and you can ask for a person to look at any decision.",
    "A delay that is already known cannot be insured. Cover must be bought before it.",
    "The payout is a fixed amount for a fixed delay. It does not depend on your actual costs.",
    "Payouts go only to the account you gave us. Your data is used only to run this cover.",
]

LAW_VERSION = "2026-10"


def rules_for(market: str) -> dict:
    """Default rule values for a market."""
    dp, bp, t1, t2, bag = PRICES.get(market, PRICES["GLOBAL"])
    return {**BASE_THRESHOLDS, "delay_premium": dp, "baggage_premium": bp, "delay_tier1_payout": t1,
            "delay_tier2_payout": t2, "lost_bag_payout": bag, "cancel_payout": t2}


def public(market: str) -> dict:
    """Market info for the screens."""
    m = MARKETS.get(market, MARKETS["GLOBAL"])
    return {"code": market if market in MARKETS else "GLOBAL", "name": m["name"], "currency": m["currency"],
            "rails": m["rails"], "regulator": m["regulator"], "tax": m["tax"], "tax_rate": m.get("tax_rate", 0),
            "statutory": m.get("statutory"), "notes": [dict(area=a, law=l, handling=h) for a, l, h in m["notes"]],
            "principles": PRINCIPLES, "law_version": LAW_VERSION}


def to_usd(amount: float, currency: str) -> float:
    return amount / FX_PER_USD.get(currency, 1.0)
