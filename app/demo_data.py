"""The demo world: 50 travelers with a realistic book of business, so every screen has something to show.

What is seeded
  * Four named demo travelers with booking references that show the four cover states:
      BOTH42 round trip with delay and baggage cover on both legs, paid in points
      DLY731 delay cover only, paid straight to a saved Visa card
      BAG518 baggage cover only
      NONE26 a booking with no cover
  * 46 more travelers: cover bought for both, for delay only, for baggage only, and some bookings
    (made on Trip.com, Agoda or an airline site) that never bought cover.
  * Past trips decided by the real engine: on time, short delays, 3 and 6 hour payouts, cancellations,
    held cases from a card shared by three travelers, a burst buyer and a buy-into-a-delay case, and lost bags.
  * Points earned, points spent at airport partners, cash-outs and saved cards.

Everything runs through the same engine as live use, then timestamps are moved into the past so the audit log,
decision list and wallets read like a book that has been running for a couple of months.
The data is deterministic: the same dates relative to today, the same people, every reset.
"""
import datetime
import random
import time
from zoneinfo import ZoneInfo

from . import db, engine, markets, partners, wallet

DAY = 86400
REF_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

STAFF = [
    ("ST-1", "Ethan", "", "ethan@koda.example", "E", "admin"),
    ("ST-2", "Arun Suksawat", "Senior claims reviewer", "arun.suksawat@koda.example", "AS", "reviewer"),
    ("ST-3", "Malinee Chaiyasit", "Customer support agent", "malinee.chaiyasit@koda.example", "MC", "agent"),
    ("ST-4", "Pimchanok Tanakorn", "Head of claims", "pimchanok.tanakorn@koda.example", "PT", "admin"),
]
REVIEWERS = [s[1] for s in STAFF]

TZ = {"BKK": "Asia/Bangkok", "CNX": "Asia/Bangkok", "HKT": "Asia/Bangkok", "USM": "Asia/Bangkok", "SIN": "Asia/Singapore",
      "LHR": "Europe/London", "NRT": "Asia/Tokyo", "DXB": "Asia/Dubai", "HKG": "Asia/Hong_Kong",
      "KUL": "Asia/Kuala_Lumpur", "CGK": "Asia/Jakarta", "DPS": "Asia/Makassar"}

# Route templates: outbound a to b at t1, return b to a at t2. Flight numbers are generated, so each trip has its own.
ROUTES = [
    {"al": "TG", "name": "Thai Airways", "a": "BKK", "ac": "Bangkok", "b": "CNX", "bc": "Chiang Mai", "t1": "09:30", "t2": "18:10", "n": 900},
    {"al": "FD", "name": "Thai AirAsia", "a": "BKK", "ac": "Bangkok", "b": "HKT", "bc": "Phuket", "t1": "07:10", "t2": "16:40", "n": 3000},
    {"al": "PG", "name": "Bangkok Airways", "a": "BKK", "ac": "Bangkok", "b": "USM", "bc": "Koh Samui", "t1": "14:00", "t2": "17:30", "n": 930},
    {"al": "SQ", "name": "Singapore Airlines", "a": "BKK", "ac": "Bangkok", "b": "SIN", "bc": "Singapore", "t1": "11:05", "t2": "19:55", "n": 970},
    {"al": "TG", "name": "Thai Airways", "a": "BKK", "ac": "Bangkok", "b": "NRT", "bc": "Tokyo", "t1": "23:40", "t2": "08:30", "n": 640},
    {"al": "EK", "name": "Emirates", "a": "BKK", "ac": "Bangkok", "b": "DXB", "bc": "Dubai", "t1": "01:45", "t2": "22:10", "n": 380},
    {"al": "BA", "name": "British Airways", "a": "LHR", "ac": "London", "b": "BKK", "bc": "Bangkok", "t1": "21:10", "t2": "23:55", "n": 920},
    {"al": "SQ", "name": "Singapore Airlines", "a": "SIN", "ac": "Singapore", "b": "HKG", "bc": "Hong Kong", "t1": "10:20", "t2": "20:15", "n": 990},
    {"al": "SQ", "name": "Singapore Airlines", "a": "SIN", "ac": "Singapore", "b": "BKK", "bc": "Bangkok", "t1": "09:15", "t2": "17:40", "n": 706},
    {"al": "SQ", "name": "Singapore Airlines", "a": "SIN", "ac": "Singapore", "b": "KUL", "bc": "Kuala Lumpur", "t1": "08:05", "t2": "19:30", "n": 118},
    {"al": "SQ", "name": "Singapore Airlines", "a": "SIN", "ac": "Singapore", "b": "CGK", "bc": "Jakarta", "t1": "13:20", "t2": "18:45", "n": 946},
]
GEORGE, GEORGE_ID = "George", "CU-GEORGE"
UNLINKED_REF = "AG4T7Q"  # George's Agoda ticket that is not in his account yet"
# Where the ticket was bought. Our own checkout means the traveler bought the ticket and the cover together;
# the others mean they already had a ticket and bought cover only.
PARTNER_CHECKOUT = "KODA partner checkout"
CHANNELS = ["Trip.com", "Agoda", "Airline website or app", "Another travel site"]
CHANNELS_WITH_US = CHANNELS + [PARTNER_CHECKOUT]

NAMES = [
    "Somchai Kittipong", "Nattaya Srisuk", "Anan Thongchai", "Kanya Wattana", "Preecha Boonmee", "Siriporn Rattana", "Chaiwat Pongsri",
    "Malee Suwan", "Ploy Chanthara", "Tawan Inthara", "Emma Johansson", "Lucas Meyer", "Sofia Rossi", "Oliver Bennett", "Chloe Dubois",
    "Noah Fischer", "Isabella Garcia", "Liam O'Connor", "Hannah Weber", "Mateo Alvarez", "Wei Zhang", "Mei Lin Chen", "Hiroshi Tanaka",
    "Yuki Sato", "Min-jun Kim", "Seo-yeon Park", "Arjun Mehta", "Priya Nair", "Rohan Kapoor", "Ananya Iyer", "Sarah Mitchell",
    "James Carter", "Olivia Harris", "Daniel Brooks", "Grace Thompson", "Elliot Walker", "Zoe Campbell", "Henry Foster", "Amelia Reed",
    "Jack Morgan", "Fatima Al-Sayed", "Omar Haddad", "Layla Hassan", "Karim Benali", "Aisha Khan", "Tariq Rahman",
]
BAG_ITEMS = [
    [("Clothes for the week", 18), ("Phone charger and cables", 9), ("Toiletries", 8)],
    [("Business shirts and trousers", 22), ("Laptop charger", 14), ("Leather shoes", 20)],
    [("Summer clothes", 16), ("Sandals", 6), ("Sunscreen and toiletries", 7)],
    [("Camera lens", 30), ("Camera strap", 5)],
    [("Winter jacket", 28), ("Gloves and scarf", 10)],
    [("Gifts for family", 25), ("Clothes", 14)],
    [("Medication and toiletries", 12), ("Clothes", 18), ("Headphones", 15)],
]


def _ref(rng) -> str:
    return "".join(rng.choice(REF_CHARS) for _ in range(6))


class _World:
    """Holds the state while the demo world is built."""

    def __init__(self, c):
        self.c = c
        self.rng = random.Random(2026)
        self.now = time.time()
        self.nums = {}
        self.used = set()
        self.pending_bag = []

    # ---- flights
    def flight(self, route, leg: int, day_off: int, hhmm: str | None = None) -> dict:
        o, d = (route["a"], route["b"]) if leg == 1 else (route["b"], route["a"])
        oc, dc = (route["ac"], route["bc"]) if leg == 1 else (route["bc"], route["ac"])
        tz = ZoneInfo(TZ[o])
        base = datetime.datetime.now(tz).date() + datetime.timedelta(days=day_off)
        hh, mm = map(int, (hhmm or (route["t1"] if leg == 1 else route["t2"])).split(":"))
        dt = datetime.datetime(base.year, base.month, base.day, hh, mm, tzinfo=tz)
        key = f'{route["al"]}{route["a"]}{route["b"]}{route["n"]}'  # per route, so two routes on one airline never share a flight number
        n = self.nums.get(key, route["n"])
        while True:                      # a flight number is used once in the whole demo world
            n += 2
            code = f"{route['al']} {n}"
            if code not in self.used:
                break
        self.nums[key] = n
        self.used.add(code)
        f = {"code": code, "airline": route["name"], "origin": o, "origin_city": oc, "dest": d, "dest_city": dc,
             "depart_label": dt.strftime("%a %d %b, %H:%M"), "scheduled": dt.strftime("%H:%M"), "sched_ts": dt.timestamp(),
             "flight_date": dt.strftime("%Y-%m-%d"), "market": markets.market_for_timezone(TZ[o])}
        self.c.execute(
            "INSERT OR REPLACE INTO flights (code,airline,origin,origin_city,dest,dest_city,depart_label,scheduled,delay_minutes,travelers,"
            "source,fetched_at,market,flight_date,sched_ts) VALUES (?,?,?,?,?,?,?,?,0,0,'demo',?,?,?,?)",
            (code, f["airline"], o, oc, d, dc, f["depart_label"], f["scheduled"], self.now, f["market"], f["flight_date"], f["sched_ts"]))
        return f

    # ---- people
    def customer(self, cid, name, home, joined_days_ago, phone=None) -> str:
        first = name.split(" ")[0].lower().replace("'", "")
        last = name.split(" ")[-1].lower().replace("'", "")
        email = f"{first}.{last}@example.com"
        if not phone:   # a number that matches where the traveler lives
            phone = ("+65 8%03d %04d" % (self.rng.randint(100, 999), self.rng.randint(1000, 9999)) if home == "SG"
                     else "+44 7%03d %06d" % (self.rng.randint(100, 999), self.rng.randint(100000, 999999)) if home == "UK"
                     else "+66 8%d %03d %04d" % (self.rng.randint(0, 9), self.rng.randint(100, 999), self.rng.randint(1000, 9999)))
        self.c.execute("INSERT INTO customers (id,name,email,phone,market,auto_topup,notify,created_at) VALUES (?,?,?,?,?,0,1,?)",
                       (cid, name, email, phone, home, self.now - joined_days_ago * DAY))
        return cid

    def method(self, cid, kind, label, last4, holder, default=True, days_ago=40):
        self.c.execute("INSERT INTO payment_methods (id,customer_id,kind,label,holder,last4,is_default,created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (engine._new_id("PM"), cid, kind, label, holder, last4, int(default), self.now - days_ago * DAY))

    def auto_topup(self, cid, on=True):
        self.c.execute("UPDATE customers SET auto_topup=? WHERE id=?", (int(on), cid))
        wallet.refresh_policies(self.c, cid)

    # ---- policies and bookings
    def booking(self, ref, leg, passenger, flight, trip, channel, cid):
        self.c.execute("INSERT OR REPLACE INTO bookings (ref,leg,passenger,flight,trip,channel,customer_id) VALUES (?,?,?,?,?,?,?)",
                       (ref, leg, passenger, flight, trip, channel, cid))

    def policy(self, cid, name, f, ref, channel, delay, bag, bought_ts, flags=None) -> str:
        pid = engine._new_id("CL")
        flags = {"f_covers_2h": 1, "f_schedule_changed": 0, "f_shared_account": 0, "f_prior_claims": 0, "f_adverse": 0, **(flags or {})}
        rules = engine.get_rules(self.c, f["market"])
        premium = int(rules["delay_premium"]) * delay + int(rules["baggage_premium"]) * bag
        label = wallet.account_for(self.c, cid)
        self.c.execute(
            "INSERT INTO policies (id,traveler,flight,payout_account,delay_cover,baggage_cover,premium,status,f_covers_2h,f_schedule_changed,"
            "f_shared_account,f_prior_claims,market,currency,created_at,sched_at_purchase,pnr,channel,customer_id,payout_follow,ref_verified,f_adverse) "
            "VALUES (?,?,?,?,?,?,?,'active',?,?,?,?,?,?,?,?,?,?,?,1,1,?)",
            (pid, name, f["code"], label, delay, bag, premium, flags["f_covers_2h"], flags["f_schedule_changed"], flags["f_shared_account"],
             flags["f_prior_claims"], f["market"], markets.DEMO_CURRENCY, bought_ts, f["scheduled"], ref, channel, cid, flags["f_adverse"]))
        db.log(self.c, pid, "policy_active", {"delay": bool(delay), "baggage": bool(bag), "total": premium, "currency": markets.DEMO_CURRENCY, "booking": ref}, ts=bought_ts)
        db.log(self.c, pid, "notices_accepted", {"market": f["market"], "version": markets.LAW_VERSION}, ts=bought_ts)
        return pid

    # ---- time travel for rows the engine just wrote
    def backdate(self, pids, ts, since):
        if not pids:
            return
        q = ",".join("?" * len(pids))
        args = [ts, *pids, since]
        self.c.execute(f"UPDATE decisions SET created_at=? + (rowid % 50) WHERE policy_id IN ({q}) AND created_at>=?", args)
        self.c.execute(f"UPDATE events SET ts=? + id * 0.001 WHERE policy_id IN ({q}) AND ts>=?", args)
        self.c.execute(f"UPDATE baggage SET created_at=? WHERE policy_id IN ({q}) AND created_at>=?", args)
        self.c.execute(f"UPDATE expenses SET created_at=? WHERE policy_id IN ({q}) AND created_at>=?", args)

    def event(self, flight_code, minutes, cancelled=False):
        """Run the real engine for a flight event and move what it wrote to the moment of the event."""
        since = time.time() - 0.001
        pids = [r["id"] for r in db.rows(self.c, "SELECT id FROM policies WHERE flight=?", (flight_code,))]
        f = db.one(self.c, "SELECT sched_ts FROM flights WHERE code=?", (flight_code,))
        engine.run_delay(self.c, flight_code, minutes, cancelled=cancelled)
        self.backdate(pids, f["sched_ts"] + max(minutes, 0) * 60 - self.rng.randint(0, 900), since)


# ======================================================================== seed steps

def seed_staff(c):
    c.executemany("INSERT OR REPLACE INTO staff (id,name,title,email,initials,role,notify) VALUES (?,?,?,?,?,?,1)", STAFF)


def _demo_travelers(w: _World):
    """The four booking references used in the presentation."""
    c, now = w.c, w.now
    tg, sq, fd, pg = ROUTES[0], ROUTES[3], ROUTES[1], ROUTES[2]
    # BOTH42: round trip, delay and baggage cover on both legs, paid in points.
    cid = w.customer("CU-DEMO1", "Pim Wongsakul", "TH", 120, "+66 81 555 0142")
    out, back = w.flight(tg, 1, 2), w.flight(tg, 2, 6)
    for leg, f in ((1, out), (2, back)):
        w.booking("BOTH42", leg, "Pim Wongsakul", f["code"], "Round trip", "Trip.com", cid)
        w.policy(cid, "Pim Wongsakul", f, "BOTH42", "Trip.com", 1, 1, now - 2 * DAY)
    # DLY731: delay only, with a Visa card saved so payouts go straight to it.
    cid = w.customer("CU-DEMO2", "Marcus Tan", "SG", 200, "+65 8123 0731")
    w.method(cid, "card", "Visa ending 4242", "4242", "Marcus Tan", True, 60)
    w.auto_topup(cid, True)
    f = w.flight(sq, 1, 3)
    w.booking("DLY731", 1, "Marcus Tan", f["code"], "One way", "Agoda", cid)
    w.policy(cid, "Marcus Tan", f, "DLY731", "Agoda", 1, 0, now - 1 * DAY)
    # BAG518: baggage only, paid in points.
    cid = w.customer("CU-DEMO3", "Aiko Nakamura", "JP", 90, "+81 90 5518 2200")
    f = w.flight(fd, 1, 4)
    w.booking("BAG518", 1, "Aiko Nakamura", f["code"], "One way", "Airline website or app", cid)
    w.policy(cid, "Aiko Nakamura", f, "BAG518", "Airline website or app", 0, 1, now - 3 * DAY)
    # NONE26: a booking only. No cover yet.
    cid = w.customer("CU-DEMO4", "Daniel Okafor", "TH", 15, "+66 82 555 0126")
    f = w.flight(pg, 1, 3)
    w.booking("NONE26", 1, "Daniel Okafor", f["code"], "One way", "Another travel site", cid)


def _outcomes():
    """Past outcomes for travelers with delay cover, in a fixed mix."""
    o = ["ontime"] * 15 + ["short"] * 4 + ["d1"] * 12 + ["d2"] * 4 + ["cancel"] * 3
    return o


def _demo_george(w: _World):
    """The one traveler on the phone. His account shows every scenario in the app, all at Changi."""
    c, now = w.c, w.now
    r = {(x["a"], x["b"]): x for x in ROUTES if x["a"] == "SIN"}
    cid = w.customer(GEORGE_ID, GEORGE, "SG", 140, "+65 8123 4567")
    c.execute("UPDATE customers SET email=? WHERE id=?", ("george@example.com", cid))
    w.method(cid, "card", "Visa ending 4417", "4417", GEORGE, True, 60)
    w.method(cid, "card", "Mastercard ending 8812", "8812", GEORGE, False, 30)
    c.execute("UPDATE payment_methods SET currency='THB' WHERE customer_id=? AND last4='8812'", (cid,))
    # 1. Upcoming round trip to Bangkok, delay and baggage cover on both legs. Covered.
    out, back = w.flight(r[("SIN", "BKK")], 1, 2), w.flight(r[("SIN", "BKK")], 2, 6)
    for leg, f in ((1, out), (2, back)):
        w.booking("SG7K2M", leg, GEORGE, f["code"], "Round trip", "Trip.com", cid)
        w.policy(cid, GEORGE, f, "SG7K2M", "Trip.com", 1, 1, now - 2 * DAY)
    # 2. Upcoming one way to Hong Kong, booked on an airline site, no cover yet. He can add it.
    f = w.flight(r[("SIN", "HKG")], 1, 4)
    w.booking("HK9P4R", 1, GEORGE, f["code"], "One way", "Airline website or app", cid)
    # 3. Past trip to Kuala Lumpur, delayed 3h35, covered. Points paid, some already spent.
    f = w.flight(r[("SIN", "KUL")], 1, -12)
    w.booking("KL3D8T", 1, GEORGE, f["code"], "One way", "Agoda", cid)
    w.policy(cid, GEORGE, f, "KL3D8T", "Agoda", 1, 1, f["sched_ts"] - 6 * DAY)
    w.event(f["code"], 215)
    # 4. Past trip to Jakarta, delayed 2h30, below the 3 hour rule. Not covered, so he can ask for a re-check.
    f = w.flight(r[("SIN", "CGK")], 1, -20)
    w.booking("JK5N6W", 1, GEORGE, f["code"], "One way", "Trip.com", cid)
    w.policy(cid, GEORGE, f, "JK5N6W", "Trip.com", 1, 0, f["sched_ts"] - 5 * DAY)
    w.event(f["code"], 150)
    # 5. A ticket he bought on Agoda that is not in his KODA account yet. "Add a booking" in the Cover tab brings it in
    #    with the reference and his last name, and then he can cover it.
    f = w.flight(r[("SIN", "KUL")], 1, 9, "15:40")
    w.booking(UNLINKED_REF, 1, GEORGE, f["code"], "One way", "Agoda", None)


def seed_history(w: _World):
    c, rng, now = w.c, w.rng, w.now
    people = [(f"CU-{i:04d}", n) for i, n in enumerate(NAMES, start=101)]
    # Which travelers have a saved card or bank, and which of them are paid in cash.
    cash = {0, 2, 3, 6, 9, 11, 14, 17, 19, 23, 27, 33}          # saved method first, auto top-up on: payouts go straight to it
    later = {1, 5, 8, 13, 21}                                    # save a card after earning points, then cash out some
    ring = [3, 11, 19]                                           # three travelers who share one card
    burst, adverse = 25, 31
    no_cover = set(range(38, 46))                                # booked elsewhere, never bought cover
    for i, (cid, name) in enumerate(people):
        home = "TH" if i % 5 == 0 else "UK" if i % 13 == 7 else "SG"   # Singapore is the home market, with a few travelers elsewhere
        w.customer(cid, name, home, rng.randint(30, 220))
        if i in cash:
            if i in ring:
                w.method(cid, "card", "Visa ending 9917", "9917", name, True, 50)
            elif i % 3 == 0:
                bank = ["DBS Bank", "OCBC Bank", "United Overseas Bank"][i % 3]
                last4 = "%04d" % rng.randint(1000, 9999)
                w.method(cid, "bank", f"{bank} ending {last4}", last4, name, True, 70)
            else:
                brand = ["Visa", "Mastercard"][i % 2]
                last4 = "%04d" % rng.randint(1000, 9999)
                w.method(cid, "card", f"{brand} ending {last4}", last4, name, True, 70)
            w.auto_topup(cid, True)

    outs = _outcomes()
    rng.shuffle(outs)
    covers = ["both"] * 36 + ["delay"] * 14 + ["baggage"] * 9
    rng.shuffle(covers)
    plans = []                                                   # one dict per covered trip
    covered_people = [i for i in range(46) if i not in no_cover]

    def cov_flags(kind):
        return (1 if kind in ("both", "delay") else 0, 1 if kind in ("both", "baggage") else 0)

    # past trips: the outcome mix, one trip each, spread over the last 70 days
    for k, out in enumerate(outs):
        plans.append({"p": covered_people[k % len(covered_people)], "when": "past", "out": out, "cover": covers.pop() if covers else "both"})
    # About a third of the delayed trips belong to travelers paid straight to a saved card or bank, so both payout paths show up.
    cash_cycle = [i for i in sorted(cash) if i not in ring]
    j = 0
    for pl in plans:
        if pl["out"] in ("d1", "d2", "cancel") and cash_cycle and j % 3 == 0:
            pl["p"] = cash_cycle[(j // 3) % len(cash_cycle)]
        if pl["out"] in ("d1", "d2", "cancel"):
            j += 1
    # special profiles that the risk check holds for a person
    for r in ring:
        plans.append({"p": r, "when": "past", "out": "d1", "cover": "both", "flags": {"f_covers_2h": 2}, "special": "ring"})
    plans.append({"p": burst, "when": "past", "out": "d1", "cover": "both", "flags": {"f_covers_2h": 4, "f_schedule_changed": 1}, "special": "burst"})
    plans.append({"p": adverse, "when": "past", "out": "d2", "cover": "both", "flags": {"f_covers_2h": 2, "f_adverse": 1}, "special": "adverse"})
    # trips still to fly
    for k in range(26):
        plans.append({"p": covered_people[(k * 3 + 1) % len(covered_people)], "when": "future", "out": "future", "cover": covers.pop() if covers else "both"})
    # bookings that never bought cover (a few were delayed)
    ncplans = []
    for k, i in enumerate(sorted(no_cover)):
        for j in range(2 if k < 6 else 1):
            ncplans.append({"p": i, "when": "past" if (k + j) % 3 == 0 else "future", "out": "d1" if (k + j) % 6 == 0 else "ontime"})

    day_pool = list(range(-70, -1))
    rng.shuffle(day_pool)
    flights_with_events = []                                      # (flight code, minutes, cancelled)
    policies = []
    for n, pl in enumerate(plans):
        cid, name = people[pl["p"]]
        route = ROUTES[(n * 5 + pl["p"]) % len(ROUTES)]
        past = pl["when"] == "past"
        day = day_pool.pop() if past else rng.randint(1, 20)
        f = w.flight(route, 1, day)
        round_trip = (n % 6 == 0) and pl["out"] in ("ontime", "future") and not pl.get("special")
        ref = _ref(rng)
        # about a fifth bought the ticket through our own checkout, the rest came with a ticket already
        channel = PARTNER_CHECKOUT if n % 5 == 2 and not pl.get("special") else rng.choice(CHANNELS)
        d, b = cov_flags(pl["cover"])
        bought = (f["sched_ts"] - rng.randint(2, 18) * DAY) if past else (now - rng.randint(1, 10) * DAY)
        pid = w.policy(cid, name, f, ref, channel, d, b, bought, pl.get("flags"))
        w.booking(ref, 1, name, f["code"], "Round trip" if round_trip else "One way", channel, cid)
        policies.append({"pid": pid, "cid": cid, "f": f, "pl": pl, "d": d, "b": b, "ref": ref})
        if round_trip:  # the return flight of the same booking, a few days later, with the same cover
            f2 = w.flight(route, 2, day + rng.randint(3, 8))
            pid2 = w.policy(cid, name, f2, ref, channel, d, b, bought + 60, pl.get("flags"))
            w.booking(ref, 2, name, f2["code"], "Round trip", channel, cid)
            policies.append({"pid": pid2, "cid": cid, "f": f2, "pl": {**pl, "out": "ontime" if past and f2["sched_ts"] < now else "future"}, "d": d, "b": b, "ref": ref})
    # The signed-in demo traveler Pim has a past trip with a 3 hour delay, so her points card and wallet are not empty.
    pim = w.flight(ROUTES[1], 1, -12)
    pim_pid = w.policy("CU-DEMO1", "Pim Wongsakul", pim, "PIM204", "Trip.com", 1, 1, pim["sched_ts"] - 6 * DAY)
    w.booking("PIM204", 1, "Pim Wongsakul", pim["code"], "One way", "Trip.com", "CU-DEMO1")
    policies.append({"pid": pim_pid, "cid": "CU-DEMO1", "f": pim, "pl": {"p": 0, "when": "past", "out": "d1"}, "d": 1, "b": 1, "ref": "PIM204"})
    # uncovered bookings
    for n, pl in enumerate(ncplans):
        cid, name = people[pl["p"]]
        route = ROUTES[(n * 3 + pl["p"]) % len(ROUTES)]
        past = pl["when"] == "past"
        day = day_pool.pop() if past and day_pool else (-rng.randint(2, 30) if past else rng.randint(1, 25))
        f = w.flight(route, 1, day)
        ref = _ref(rng)
        w.booking(ref, 1, name, f["code"], "One way", rng.choice(CHANNELS), cid)
        if past and pl["out"] == "d1":
            flights_with_events.append((f["code"], rng.choice((195, 240, 310)), False))

    # ---- decisions for the past, by the real engine, then moved to the moment of the event
    mins = {"ontime": 0, "short": None, "d1": None, "d2": None}
    held_special = []
    for p in policies:
        pl, f = p["pl"], p["f"]
        if f["sched_ts"] > now:
            continue
        out = pl["out"]
        if out == "ontime":
            continue
        if out == "short":
            flights_with_events.append((f["code"], rng.choice((45, 70, 110)), False))
        elif out == "d1":
            flights_with_events.append((f["code"], rng.choice((185, 205, 240, 290)), False))
        elif out == "d2":
            flights_with_events.append((f["code"], rng.choice((375, 420, 520)), False))
        elif out == "cancel":
            flights_with_events.append((f["code"], 0, True))
    for code, minutes, cancelled in flights_with_events:
        w.event(code, minutes, cancelled)

    # ---- a person decides some held claims
    held = db.rows(c, "SELECT d.id, p.traveler, p.id pid, p.customer_id FROM decisions d JOIN policies p ON p.id=d.policy_id WHERE d.status='held' AND d.kind='delay' ORDER BY d.created_at")
    notes = [(True, "Arun Suksawat", "Called the traveler. Same household, tickets confirmed with the airline."),
             (False, "Nattaya Phromsri", "Schedule changed after purchase and the traveler could not explain it. Declined after review.")]
    ring_ids = {people[r][0] for r in ring}
    done = 0
    for h in held:
        if h["customer_id"] in ring_ids and done == 0:
            ok, who, note = notes[0]
        elif h["customer_id"] == people[burst][0]:
            ok, who, note = notes[1]
        else:
            continue
        before = db.one(c, "SELECT created_at FROM decisions WHERE id=?", (h["id"],))["created_at"]
        engine.decide_held(c, h["id"], ok, who, note)
        c.execute("UPDATE events SET ts=? + id * 0.0001 WHERE policy_id=? AND type LIKE 'review_%'", (before + 3600 * rng.randint(2, 20), h["pid"]))
        done += 1

    # ---- lost bags and extra costs
    bag_pols = [p for p in policies if p["b"] and p["f"]["sched_ts"] < now - 6 * 3600]
    rng.shuffle(bag_pols)
    for k, p in enumerate(bag_pols[:8]):
        since = time.time() - 0.001
        items = [{"name": n, "amount": a} for n, a in BAG_ITEMS[k % len(BAG_ITEMS)]]
        claim = engine.submit_bag_claim(c, p["pid"], "PIR" + "".join(rng.choice("0123456789") for _ in range(6)),
                                        p["f"]["code"].replace(" ", "") + "".join(rng.choice("0123456789") for _ in range(6)), items)
        ts = p["f"]["sched_ts"] + rng.randint(4, 30) * 3600
        w.backdate([p["pid"]], ts, since)
        if k in (0, 1, 2) and db.one(c, "SELECT status FROM decisions WHERE id=?", (claim["id"] + "-D",))["status"] == "pending":
            engine.decide_baggage(c, claim["id"], True, REVIEWERS[k % 4])
        elif k == 3:
            engine.decide_baggage(c, claim["id"], False, "Arun Suksawat")
    # ---- past trips with no payout are closed quietly
    for p in policies:
        if p["f"]["sched_ts"] < now - 3 * 3600:
            has = db.one(c, "SELECT 1 FROM decisions WHERE policy_id=?", (p["pid"],))
            if not has:
                c.execute("UPDATE policies SET status='archived' WHERE id=?", (p["pid"],))
                db.log(c, p["pid"], "trip_archived", {"flight": p["f"]["code"], "reason": "no covered event"}, ts=p["f"]["sched_ts"] + 6 * 3600)

    # ---- travelers who saved a card after earning points
    for i in later:
        cid, name = people[i]
        last4 = "%04d" % rng.randint(1000, 9999)
        w.method(cid, "card", f"{['Visa', 'Mastercard'][i % 2]} ending {last4}", last4, name, True, 5)
    # two of the uncovered bookers have a card on file
    for i in (38, 41):
        cid, name = people[i]
        last4 = "%04d" % rng.randint(1000, 9999)
        w.method(cid, "card", f"Visa ending {last4}", last4, name, True, 20)

    # ---- points spent and cashed out, a while after they were earned
    for cid, name in people:
        w_ = wallet.balance(c, cid)
        if w_["points"] < 3500 or rng.random() < 0.25:
            continue
        origin = (db.one(c, "SELECT f.origin FROM policies p JOIN flights f ON f.code=p.flight WHERE p.customer_id=? ORDER BY f.sched_ts LIMIT 1", (cid,)) or {"origin": "BKK"})["origin"]
        spots = db.rows(c, "SELECT id, price_usd FROM partners WHERE airport=? AND accepts=1 AND (price_usd>0 OR category='food') ORDER BY id", (origin,))
        rng.shuffle(spots)
        for s in spots[:2]:
            price = (s["price_usd"] or rng.choice((8, 12, 14))) * 100
            if wallet.balance(c, cid)["points"] < price + 500:
                continue
            r = partners.redeem_customer(c, cid, s["id"], price)
            ts = now - rng.randint(1, 30) * DAY
            # most vouchers were scanned at the outlet soon after; a few are still unused
            used = rng.random() > 0.2
            c.execute("UPDATE redemptions SET created_at=?, status=?, used_at=? WHERE id=?",
                      (ts, "used" if used else "ready", ts + rng.randint(10, 180) * 60 if used else None, r["id"]))
            c.execute("UPDATE wallet_tx SET created_at=? WHERE id=?", (ts, "spend:" + r["id"]))
            break
    for i in (1, 8):
        cid, _ = people[i]
        m = wallet.default_method(c, cid)
        bal = wallet.balance(c, cid)["points"]
        if m and bal >= wallet.MIN_CASHOUT:
            r = wallet.cash_out(c, cid, min(bal, 4000), m["id"])
            c.execute("UPDATE wallet_tx SET created_at=? WHERE id=?", (now - rng.randint(1, 12) * DAY, "cash:" + r["id"]))

    # earn rows follow the decision time
    c.execute("UPDATE wallet_tx SET created_at=(SELECT created_at FROM decisions WHERE decisions.id=substr(wallet_tx.id,6)) WHERE kind='earn'")
    # partners that joined KODA points recently
    c.execute("UPDATE customers SET created_at=MIN(created_at, ?) WHERE id LIKE 'CU-DEMO%'", (now - 10 * DAY,))


def seed_all(c):
    seed_staff(c)
    w = _World(c)
    _demo_travelers(w)
    _demo_george(w)
    seed_history(w)
