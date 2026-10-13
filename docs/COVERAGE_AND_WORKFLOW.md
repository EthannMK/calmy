# KODA cover: what is covered, how it works, who does what

Insurer in the demo: KODA. All money is Singapore dollars in the prototype. Payouts are simulated. Flights are real where we can get them.
Figures below are the Singapore market defaults. KODA can change any of them in the console under Rules, and a change applies to the next trigger only.

## 1. The product in one minute

KODA is smart flight cover. A traveler adds cover to a trip they already booked, anywhere: an airline site, Trip.com, Agoda, another travel site, or a partner that sells KODA cover at its own checkout. A traveler with no ticket yet can book one with cover through our partner checkout. Cover always belongs to a booking. If the flight is late or cancelled, or a bag is lost, KODA pays without a claim form.

| Cover | Price | What it pays |
|---|---|---|
| Delay cover | S$5 | S$45 when the flight is 3 hours late or more. S$90 at 6 hours or more. S$90 if the flight is cancelled. |
| Baggage cover | S$2 | Up to S$40 for a bag that is lost. |

The two prices above are **unvalidated product assumptions**, not market-tested or actuarially set prices. Working,
competitor comparison and what would be needed to price this properly: `docs/PRICING_RESEARCH.md`.

## 2. What is covered

- A delay of 3 hours or more on the covered flight, measured by the airline's real status feed.
- A cancelled flight.
- A lost bag, once the flight has left, with an airline report number and a list of items.
- Both flights of a round trip, if each one has cover. Each leg is its own cover.

## 3. What is not covered

- A delay under 3 hours.
- A bag that is only late. It must be lost and reported to the airline.
- A trip with only baggage cover gets no delay payout. A trip with only delay cover gets no lost bag payout.
- Cover bought after a delay is already known. Delay cover cannot be added once the flight is 30 minutes late or more.
- Cover added after the flight has left or been cancelled.
- A flight the traveler did not take.
- A flight where a delay is already very likely. Delay cover stops selling when the delay chance reaches 80% (KODA can change this in Rules). Baggage cover stays open.
- Anything that happened before the cover was added.
- A second claim of the same type on the same flight.
- Compensation the airline owes by law. KODA pays on top of it. The traveler should still claim from the airline.

## 4. How a traveler gets paid

1. The payout arrives as KODA points by default. 100 points are worth S$1 to spend. A 25% bonus is added, so a S$45 payout arrives as 5,625 points (worth S$56.25 to spend).
2. Points are spent at an airport partner: food and drink, a lounge, or a SIM or data pack. The traveler picks an outlet, the app makes a QR code, and the outlet's till scans it. A voucher works once, at its own outlet. An unused voucher can be refunded. Points work at any airport where KODA has partners, not only the one the traveler is flying from.
3. Points can be cashed out to a saved card or bank account. Cash-out pays S$0.80 for every 100 points, so cash is worth exactly what the payout was, with no gain from switching. Minimum cash-out is 500 points. If the card is in another currency, the rate and the fee are shown before the traveler confirms, and the amount that will land is shown in that currency.
4. A traveler who saves a card or bank can switch on auto top-up. New payouts then land as cash on the default saved method, with no points.
5. An unused voucher can be refunded. The points go back to the wallet, once.
6. History is never rewritten. A payout keeps the destination it had on the day it was paid, even if the traveler changes the setting later.
7. No card or account number is asked for at purchase. Only the last 4 digits of a saved card or bank are kept. No real money moves in the prototype.

## 5. Booking reference and Check my cover

- Booking reference: the 5 to 10 letters or numbers on the e-ticket. It ties the cover to the ticket. A reference that matches a booking on record (same reference, same passenger, same flight) is marked verified. Any other valid-looking reference is accepted and marked not verified. In production the reference is verified with the airline or booking site.
- Check my cover: the traveler enters the booking reference and last name. The app shows every flight on the booking, which cover each has, what was paid, and what is not covered. From there the traveler can add cover to a flight with none, or add the missing cover to a flight with one.
- Buying is one way, one flight at a time. A round trip is two covers under one reference.

### George's bookings in the demo

One account carries every state, so a judge never signs in or switches traveler.

| Reference | Flight | What it shows |
|---|---|---|
| SG7K2M | SIN to BKK and back | Round trip, delay and baggage cover on both legs. The happy path: a delay pays in seconds, in points. |
| HK9P4R | SIN to HKG | A booking from an airline site with no cover yet. Cover can be added until it leaves. |
| KL3D8T | SIN to KUL | A past trip, 3h 35m late, paid automatically. Points, a QR voucher and a cash-out sit behind it. |
| JK5N6W | SIN to CGK | A past trip, 2h 30m late, under the rule, so nothing was paid. George can ask a person to check it again. |

## 6. Workflow, step by step

### Traveler

1. Home shows who they are, the delay forecast on the next covered flight, and their trips. Two ways in: check a ticket or a cover they already hold (any reference: airline, travel site, or a partner that sells KODA cover), or book a flight with cover through the partner checkout.
2. Open a trip, then "See what this cover pays": the payout ladder with a marker for where this flight is, what is covered, what is missing from this trip, and what is never covered.
3. KODA watches the flight and shows the chance of a delay.
4. If the flight is delayed or cancelled past the limit, the system checks the claim, pays or holds it, and sends a message with the reason. The Claims tab is empty until there is something to show.
5. If a delay came in under the rule, the traveler can ask a person to check it again and say what happened. It goes to the review queue marked as a traveler request.
6. The traveler spends points on a QR voucher at an airport partner, or cashes out to a card with the rate and fee shown first.
7. A lost bag: report it to the airline, then enter the report number and the items. A person always decides a bag claim.
8. Messages: the KODA assistant answers from the traveler's own cover, claim and points, and a person at KODA can join the same chat at any time. Every message says who wrote it.

### Insurer staff (the console)

1. Overview: cover sold, flights being watched, payouts, customers, bookings with no cover, delays where the traveler had no delay cover.
2. Review queue: claims the system held for a person. Read the reason and the copilot's advice. Approve or decline. Your name is signed on the decision.
3. Baggage claims: lost bag claims waiting for a person.
4. Conversations: every open support chat. The ones asking for a person come first. Read the transcript, including which facts the assistant used for each reply, take the chat over, reply under your own name, and hand it back to the assistant.
5. Customers: all customers with cover type, claims, payouts and points, and how they came to us (cover only on an outside ticket, ticket and cover through our checkout, or a booking with no cover). Open one to see trips, saved methods, points history and flags.
6. Points and partners: points owed, spending, cash-outs, which vouchers were scanned at the outlet, and the partner directory. Switch a partner on or off.
7. Policies, Decisions, Exposure forecast, Rules, Markets and law, and Audit log cover the rest of the book.
8. My profile: name, title, work email, notifications. Switch reviewer from the menu at the top right.

## 7. Who decides what

- Delay claims: the system pays automatically when the delay reaches the rule and the risk score is below 45 out of 100.
- A score of 45 or more is held for a person. The system never declines. Only a person can decline, and the traveler can ask for a second review.
- Lost bag claims always go to a person, because item values are declared by the traveler.
- A re-check asked for by the traveler always goes to a person, with their note attached.
- The support assistant decides nothing. It explains cover, claims and points, and hands the chat to a person whenever one is asked for or when the question is about a decision.

## 8. Fraud checks

The risk score adds up readable signals, each with its points: cover bought while a delay was already likely, several covers bought on one profile within 2 hours, a changed flight time, a payment card or account shared by different customers, and a claim paid on the profile in the last 12 months. The points wallet belongs to one customer and is never counted as a shared account. A real card saved on several customer accounts is a signal.

## 8b. Why AI here, and who gains

Rules decide the money. A delay of 3 hours pays S$45. That is a rule on purpose, because it is cheap, instant and auditable. AI works on what rules cannot: ranking odd cases, writing a clear message for each traveler, and forecasting delays.

Delay forecast. Each flight gets a delay chance. The base chance comes from a trained model that looks at hour, weekday, distance and weather. It was trained on 336,776 real flights and tested on later months it had not seen (ranking score 0.68, where 0.5 is a coin flip). Live facts are then added as separate drivers: a delay already running, live weather, a storm, and airspace alerts set by a person. Every driver is shown in plain words. Limits: the training data is New York 2013, so it does not know Singapore airlines or Changi routes yet. The model card in the console shows the numbers. In production the model is retrained on saved flights from our own region.

Selling bands. Under 55%: cover is sold normally. 55% to 79%: cover is sold, the traveler gets an early warning, and a claim gets an extra check by a person. 80% or more: delay cover is closed for that flight and baggage stays open. The traveler is told why. The threshold is set in the console under Rules.

Who gains. The insurer gains first: fewer claims on losses that are already known, and a fair book for everyone who bought earlier. The traveler gains an early alert and an honest "closed" message instead of buying cover that would not pay. A distribution partner such as an airline or booking site gains from a better insurance attach rate and fewer complaints.

Airspace alerts. A person at the insurer marks an airport as disrupted. It adds 15 or 30 points to the forecast and shows as a named driver. It is a judgement by a person, not a scraped news feed.

## 9. Real scenarios

1. George, round trip SIN to BKK, both covers. The outbound is 3h 15m late. He is paid 5,625 points in seconds, worth S$56.25 at Changi. He spends 700 on a kopitiam breakfast with a QR code, and the insurer sees the voucher scanned.
2. George, past trip SIN to KUL, 3h 35m late, paid automatically. He cashes 4,900 points out to his Mastercard in Thai baht: S$39.20 less a 1.5% currency fee, 567.36 THB on the card, all shown before he confirms.
3. George, past trip SIN to CGK, 2h 30m late. Under the 3 hour rule, so nothing was paid. He asks for a re-check and writes that the airline app showed 3h 20m. Ethan sees the request with the note, checks the airline record, approves it and signs it. The points land on George's phone with Ethan's name on them.
4. George, booking to Hong Kong from an airline site with no cover. A delay would pay nothing, so the app offers to add cover with the price and the key facts, up until the flight leaves.
5. A traveler with no ticket. They book SIN to DPS through the partner checkout and the cover starts with the booking. The ticket is simulated in the prototype and labelled as such.
6. A held claim. A traveler buys cover while a storm makes a delay likely, with a card also used by others. The score is above 45, so a person reviews it. The copilot suggests asking for the booking confirmation. The reviewer approves and signs it.
7. A chat that needs a person. George asks the assistant about a declined claim. The assistant does not answer it: the chat is flagged, Ethan takes it over in the console, and George sees Ethan's name join the chat.

## 10. Prototype limits

- Payouts, cards and cash-outs are simulated. No money moves.
- The bookings table stands in for airline and booking site checks. Check my cover with a reference and last name is a demo shortcut. A real launch needs proper sign-in.
- The free flight data plan returns today's flights only. Later days come from a saved schedule, and status is checked closer to the day.
- The Changi partner directory (food and drink, lounges, SIM and data) is made up, and labelled in the app as demo partners.
- The partner checkout simulates the airline ticket. Only the cover is real in the system.
- Premiums and payouts are simulated. In production premiums go through a payment gateway and payouts through a card-payout or local-rails partner, with the cash-out rate quoted by that partner instead of our indicative rate.
- The support assistant answers from the traveler's own records and Gemini does the wording. It has no memory beyond the chat, and it never decides a claim.
- This is a design prototype, not insurance advice. A licensed insurer must approve the wording, prices and rules for each market.
