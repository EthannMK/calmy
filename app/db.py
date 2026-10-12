"""SQLite storage and demo seed data for KODA.

The demo keeps all state in one SQLite file so that a reset is a single call.
On Google Cloud the same tables map to Cloud SQL, and the `events` table is
the one that is streamed to BigQuery as the audit log.
"""
import datetime
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

from . import markets

DB_PATH = os.environ.get("CALMY_DB", os.path.join(os.path.dirname(__file__), "..", "calmy.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    market TEXT NOT NULL,                 -- TH, US, EU, UK, IN, SG, JP, AU, BR, CA, GLOBAL
    key TEXT NOT NULL,
    value REAL NOT NULL,                  -- money values are in the market currency
    label TEXT NOT NULL,
    PRIMARY KEY (market, key)
);
CREATE TABLE IF NOT EXISTS flights (
    code TEXT PRIMARY KEY,
    airline TEXT, origin TEXT, origin_city TEXT, dest TEXT, dest_city TEXT,
    depart_label TEXT, scheduled TEXT, delay_minutes INTEGER DEFAULT 0,
    travelers INTEGER DEFAULT 0,
    source TEXT DEFAULT 'sim',            -- sim | live
    fetched_at REAL,
    market TEXT DEFAULT 'TH',             -- set by the departure country
    flight_date TEXT,                     -- the dated flight being tracked (live only)
    sched_ts REAL                         -- scheduled departure as epoch seconds (live only)
);
CREATE TABLE IF NOT EXISTS api_usage (
    month TEXT PRIMARY KEY,               -- YYYY-MM, free flight data plans are monthly
    calls INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS policies (
    id TEXT PRIMARY KEY,
    traveler TEXT, flight TEXT, payout_account TEXT,
    delay_cover INTEGER DEFAULT 0, baggage_cover INTEGER DEFAULT 0,
    premium INTEGER DEFAULT 0,
    status TEXT DEFAULT 'quote',           -- quote | active
    -- features the risk model reads (stand-in for BigQuery ML inputs)
    f_covers_2h INTEGER DEFAULT 1,         -- covers bought on this profile in 2 hours
    f_schedule_changed INTEGER DEFAULT 0,  -- flight time changed shortly before purchase
    f_shared_account INTEGER DEFAULT 0,    -- payout account shared with other profiles
    f_prior_claims INTEGER DEFAULT 0,      -- prior claims in 12 months
    market TEXT DEFAULT 'TH', currency TEXT DEFAULT 'USD',
    created_at REAL,                       -- when cover was bought
    sched_at_purchase TEXT                 -- scheduled time seen at purchase, to spot later changes
);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    policy_id TEXT, kind TEXT,             -- delay | baggage
    status TEXT,                           -- paid | held | declined | pending
    amount INTEGER, risk_score INTEGER,
    reasons TEXT, timeline TEXT, clause TEXT, explanation TEXT,
    delay_minutes INTEGER, created_at REAL, decided_by TEXT, note TEXT,
    breakdown TEXT                         -- JSON list of [label, points] that add up to the risk score
);
CREATE TABLE IF NOT EXISTS baggage (
    id TEXT PRIMARY KEY,
    policy_id TEXT, report_id TEXT, bag_tag TEXT, status TEXT,
    items TEXT, total INTEGER, created_at REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, policy_id TEXT, type TEXT, detail TEXT
);
"""

@contextmanager
def conn():
    """Yield a connection guarded by a lock so the demo is safe under threads."""
    with _lock:
        c = sqlite3.connect(DB_PATH)
        c.row_factory = sqlite3.Row
        try:
            yield c
            c.commit()
        finally:
            c.close()


# Columns added after the first release. Applied to an existing database file on start.
MIGRATIONS = [
    ("flights", "flight_date", "TEXT"), ("flights", "sched_ts", "REAL"), ("flights", "market", "TEXT DEFAULT 'TH'"),
    ("decisions", "breakdown", "TEXT"), ("policies", "market", "TEXT DEFAULT 'TH'"), ("policies", "currency", "TEXT DEFAULT 'USD'"),
    ("policies", "created_at", "REAL"), ("policies", "pnr", "TEXT"), ("flights", "cancelled", "INTEGER DEFAULT 0"),
    ("decisions", "trigger", "TEXT DEFAULT 'delay'"), ("decisions", "elapsed_ms", "INTEGER"), ("decisions", "message", "TEXT"), ("decisions", "context", "TEXT"), ("policies", "sched_at_purchase", "TEXT"),
]


def init():
    with conn() as c:
        old = [r["name"] for r in c.execute("PRAGMA table_info(rules)")]
        if old and "market" not in old:  # rules from before markets existed: rebuild and reseed
            c.execute("DROP TABLE rules")
        c.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        if not c.execute("SELECT 1 FROM rules LIMIT 1").fetchone():
            seed(c)


def seed(c):
    """Fill the demo world. Two travelers share one flight so the same delay
    produces one clean payout and one held case."""
    seed_rules(c)
    c.executemany(
        "INSERT OR REPLACE INTO flights (code,airline,origin,origin_city,dest,dest_city,depart_label,"
        "scheduled,delay_minutes,travelers,source) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("OA 108", "Orchid Air", "BKK", "Bangkok", "CNX", "Chiang Mai", "{D}, 11:30", "11:30", 0, 84, "sim"),
            ("OA 204", "Orchid Air", "BKK", "Bangkok", "HKT", "Phuket", "{D}, 14:10", "14:10", 0, 112, "sim"),
        ],
    )
    # Sample flights in other markets, so every currency and rule set can be tried without a live key.
    c.executemany(
        "INSERT OR REPLACE INTO flights (code,airline,origin,origin_city,dest,dest_city,depart_label,"
        "scheduled,delay_minutes,travelers,source,market) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            ("OA 310", "Orchid Air", "LHR", "London", "CDG", "Paris", "{D}, 09:20", "09:20", 0, 0, "sim", "UK"),
            ("OA 420", "Orchid Air", "FRA", "Frankfurt", "MAD", "Madrid", "{D}, 10:05", "10:05", 0, 0, "sim", "EU"),
            ("OA 530", "Orchid Air", "JFK", "New York", "LAX", "Los Angeles", "{D}, 08:00", "08:00", 0, 0, "sim", "US"),
            ("OA 640", "Orchid Air", "DEL", "Delhi", "BOM", "Mumbai", "{D}, 07:40", "07:40", 0, 0, "sim", "IN"),
            ("OA 750", "Orchid Air", "SYD", "Sydney", "MEL", "Melbourne", "{D}, 12:15", "12:15", 0, 0, "sim", "AU"),
            ("OA 860", "Orchid Air", "YYZ", "Toronto", "YVR", "Vancouver", "{D}, 13:30", "13:30", 0, 0, "sim", "CA"),
            ("OA 970", "Orchid Air", "GRU", "Sao Paulo", "GIG", "Rio de Janeiro", "{D}, 15:45", "15:45", 0, 0, "sim", "BR"),
        ],
    )
    c.executemany(
        "INSERT OR REPLACE INTO policies (id,traveler,flight,payout_account,delay_cover,baggage_cover,"
        "premium,status,f_covers_2h,f_schedule_changed,f_shared_account,f_prior_claims) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        [
            # Clean profile: scores low and is paid automatically.
            ("CL-44910", "Somchai K.", "OA 108", "PromptPay ending 4921", 0, 0, 0, "quote", 1, 0, 0, 0),
            # Risky profile: scores high and is held for a person.
            ("CL-45102", "Anan T.", "OA 108", "PromptPay ending 7730", 0, 0, 0, "quote", 4, 1, 1, 1),
        ],
    )
    # Departure dates follow the calendar, so the demo never shows a date in the past.
    tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%a %d %b")
    c.execute("UPDATE flights SET depart_label=REPLACE(depart_label, '{D}', ?)", (tomorrow,))
    c.execute("UPDATE policies SET pnr='K7M2QD' WHERE id='CL-44910'")
    c.execute("UPDATE policies SET pnr='R4T9XE' WHERE id='CL-45102'")


def seed_rules(c):
    """Every market starts with its own default prices and payouts in local currency."""
    for code in markets.MARKETS:
        for key, value in markets.rules_for(code).items():
            c.execute("INSERT OR REPLACE INTO rules VALUES (?,?,?,?)", (code, key, value, markets.RULE_LABELS[key]))


def reset():
    """Wipe everything and reseed. Backs the Reset demo data button."""
    with conn() as c:
        for t in ("decisions", "baggage", "events", "policies", "flights", "rules"):  # api_usage is kept
            c.execute(f"DELETE FROM {t}")
        seed(c)


def log(c, policy_id, type_, detail):
    c.execute(
        "INSERT INTO events (ts,policy_id,type,detail) VALUES (?,?,?,?)",
        (time.time(), policy_id, type_, json.dumps(detail)),
    )


def rows(c, sql, args=()):
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(c, sql, args=()):
    r = c.execute(sql, args).fetchone()
    return dict(r) if r else None
