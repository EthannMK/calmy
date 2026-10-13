# KODA cover: what is covered, how it works, who does what

Insurer in the demo: Flightwise Insurance. All money is US dollars in the prototype. Payouts are simulated. Flights are real.
Figures below are the Thailand market defaults. Flightwise can change any of them in the console under Rules, and a change applies to the next trigger only.

## 1. The product in one minute

KODA is smart flight cover. A traveler adds cover to a trip they already booked, anywhere: an airline site, Trip.com, Agoda or another travel site. KODA does not sell tickets. If the flight is late or cancelled, or a bag is lost, KODA pays without a claim form.

| Cover | Price | What it pays |
|---|---|---|
| Delay cover | $4 | $40 when the flight is 3 hours late or more. $80 at 6 hours or more. $80 if the flight is cancelled. |
| Baggage cover | $2 | Up to $35 for a bag that is lost. |

Extra costs (meals, a hotel, a taxi) can be claimed with a receipt after a delay payout. Up to $40 is paid at once. Anything above that, up to $160 in total, is checked by a person.

## 2. What is covered

- A delay of 3 hours or more on the covered flight, measured by the airline's real status feed.
- A cancelled flight.
- A lost bag, once the flight has left, with an airline report number and a list of items.
- Extra costs after a paid delay, with a receipt.
- Both flights of a round trip, if each one has cover. Each leg is its own cover.

## 3. What is not covered

- A delay under 3 hours.
- A bag that is only late. It must be lost and reported to the airline.
- A trip with only baggage cover gets no delay payout. A trip with only delay cover gets no lost bag payout.
- Cover bought after a delay is already known. Delay cover cannot be added once the flight is 30 minutes late or more.
- Cover added after the flight has left or been cancelled.
- A flight the traveler did not take.
- A flight where a delay is already very likely. Delay cover stops selling when the delay chance reaches 80% (Flightwise can change this in Rules). Baggage cover stays open.
- Anything that happened before the cover was added.
- A second claim of the same type on the same flight.
- Compensation the airline owes by law. KODA pays on top of it. The traveler should still claim from the airline.

## 4. How a traveler gets paid

1. The payout arrives as KODA points by default. 100 points are worth $1 to spend. A 25% bonus is added, so a $40 payout arrives as 5,000 points (worth $50 to spend).
2. Points can be spent at the airport: food, lounges, taxi and car rental, hotels, SIM cards, call and message packs, fast track and bag storage.
3. Points can be cashed out to a saved card or bank account. Cash-out pays $0.80 for every 100 points, so cash is worth exactly what the payout was, with no gain from switching. Minimum cash-out is 500 points.
4. A traveler who saves a card or bank can switch on auto top-up. New payouts then land as cash on the default saved method, with no points.
5. An unused voucher can be refunded. The points go back to the wallet, once.
6. History is never rewritten. A payout keeps the destination it had on the day it was paid, even if the traveler changes the setting later.
7. No card or account number is asked for at purchase. Only the last 4 digits of a saved card or bank are kept. No real money moves in the prototype.

## 5. Booking reference and Check my cover

- Booking reference: the 5 to 10 letters or numbers on the e-ticket. It ties the cover to the ticket. A reference that matches a booking on record (same reference, same passenger, same flight) is marked verified. Any other valid-looking reference is accepted and marked not verified. In production the reference is verified with the airline or booking site.
- Check my cover: the traveler enters the booking reference and last name. The app shows every flight on the booking, which cover each has, what was paid, and what is not covered. From there the traveler can add cover to a flight with none, or add the missing cover to a flight with one.
- Buying is one way, one flight at a time. A round trip is two covers under one reference.

### The four demo bookings

| Reference | Last name | What it shows |
|---|---|---|
| BOTH42 | Wongsakul | Round trip. Delay and baggage cover on both flights. Paid as points. |
| DLY731 | Tan | Delay cover only. Auto top-up to a saved Visa. A lost bag is not covered. |
| BAG518 | Nakamura | Baggage cover only. A delay is not covered, so a delay pays nothing. |
| NONE26 | Okafor | A booking with no cover. A delay pays nothing. Cover can still be added. |

## 6. Workflow, step by step

### Traveler

1. Add cover: pick a flight, enter name and booking reference, choose delay and baggage, accept the key facts, pay the price. Or open Check my cover to look up a booking.
2. KODA watches the flight and shows the chance of a delay.
3. If the flight is delayed or cancelled past the limit, the system checks the claim, pays or holds it, and sends a message with the reason.
4. The traveler spends or cashes out points from the Wallet and Profile.
5. A lost bag: report to the airline, then enter the report number and items in the app. Extra costs: enter the amount and a receipt photo.

### Insurer staff (the console)

1. Overview: cover sold, flights being watched, payouts, customers, bookings with no cover, delays where the traveler had no delay cover.
2. Review queue: claims the system held for a person. Read the reason and the copilot's advice. Approve or decline. Your name is signed on the decision.
3. Baggage claims and Extra costs: claims waiting for a person.
4. Customers: all customers with cover type, claims, payouts and points. Open one to see trips, saved methods, points history and flags.
5. Points and partners: points owed, spending, cash-outs, and the partner directory. Switch a partner on or off.
6. Policies, Decisions, Exposure forecast, Rules, Markets and law, Audit log and Performance cover the rest of the book.
7. My profile: name, title, work email, notifications. Switch reviewer from the menu at the top right.

## 7. Who decides what

- Delay claims: the system pays automatically when the delay reaches the rule and the risk score is below 45 out of 100.
- A score of 45 or more is held for a person. The system never declines. Only a person can decline, and the traveler can ask for a second review.
- Lost bag claims always go to a person, because item values are declared by the traveler.
- Extra costs: paid at once up to $40 if the score is low. Above that, a person checks the receipt.

## 8. Fraud checks

The risk score adds up readable signals, each with its points: cover bought while a delay was already likely, several covers bought on one profile within 2 hours, a changed flight time, a payment card or account shared by different customers, and a claim paid on the profile in the last 12 months. The points wallet belongs to one customer and is never counted as a shared account. A real card saved on several customer accounts is a signal.

## 9. Real scenarios

1. Pim, round trip BKK to CNX, both covers. Her outbound flight is 3h 20m late. She is paid 5,000 points in seconds, worth $50 at the airport. She spends 2,400 on a sleep pod, then cashes out the rest to a saved card for $20.80.
2. Marcus, one way BKK to SIN, delay cover and a saved Visa with auto top-up. A 6 hour delay pays $80 straight to the Visa. His bag is lost later, but he has no baggage cover, so that is not paid. He can add it before he flies.
3. Aiko, baggage cover only. Her flight is 4 hours late. Nothing is paid, because delay cover was not bought. Her bag is lost on another day. She reports it and a person approves up to $35.
4. Daniel, booked with no cover. His flight is 5 hours late. Nothing is paid. The console counts this under delayed flights with no delay cover. Delay cover can no longer be added.
5. A held claim. A traveler buys cover while a storm makes a delay likely, with a card also used by others. The score is above 45, so a person reviews it. The copilot suggests asking for the booking confirmation. The reviewer approves and signs it.

## 10. Prototype limits

- Payouts, cards and cash-outs are simulated. No money moves.
- The bookings table stands in for airline and booking site checks. Check my cover with a reference and last name is a demo shortcut. A real launch needs proper sign-in.
- The free flight data plan returns today's flights only. Later days come from a saved schedule, and status is checked closer to the day.
- The restaurant, lounge and service directory is made up.
- This is a design prototype, not insurance advice. A licensed insurer must approve the wording, prices and rules for each market.
