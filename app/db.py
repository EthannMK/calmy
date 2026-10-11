"""SQLite storage and demo seed data for Calmy.

The demo keeps all state in one SQLite file so that a reset is a single call.
On Google Cloud the same tables map to Cloud SQL, and the `events` table is
the one that is streamed to BigQuery as the audit log.
"""
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager

DB_PATH = os.environ.get("CALMY_DB", os.path.join(os.path.dirname(__file__), "..", "calmy.db"))
_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS rules (
    key TEXT PRIMARY KEY,
    value REAL NOT NULL,
    label TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS flights (
    code TEXT PRIMARY KEY,
    airline TEXT, origin TEXT, origin_city TEXT, dest TEXT, dest_city TEXT,
    depart_label TEXT, scheduled TEXT, delay_minutes INTEGER DEFAULT 0,
    travelers INTEGER DEFAULT 0
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
    f_prior_claims INTEGER DEFAULT 0       -- prior claims in 12 months
);
CREATE TABLE IF NOT EXISTS decisions (
    id TEXT PRIMARY KEY,
    policy_id TEXT, kind TEXT,             -- delay | baggage
    status TEXT,                           -- paid | held | declined | pending
    amount INTEGER, risk_score INTEGER,
    reasons TEXT, timeline TEXT, clause TEXT, explanation TEXT,
    delay_minutes INTEGER, created_at REAL, decided_by TEXT, note TEXT
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

DEFAULT_RULES = [
    ("delay_tier1_minutes", 180, "Tier 1 delay (minutes)"),
    ("delay_tier1_payout", 1500, "Tier 1 payout (THB)"),
    ("delay_tier2_minutes", 360, "Tier 2 delay (minutes)"),
    ("delay_tier2_payout", 3000, "Tier 2 payout (THB)"),
    ("lost_bag_payout", 1250, "Lost bag payout (THB)"),
    ("risk_threshold", 45, "Auto pay below this risk score"),
    ("delay_premium", 149, "Delay cover price (THB)"),
    ("baggage_premium", 59, "Baggage cover price (THB)"),
]


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


def init():
    with conn() as c:
        c.executescript(SCHEMA)
        if not c.execute("SELECT 1 FROM rules LIMIT 1").fetchone():
            seed(c)


def seed(c):
    """Fill the demo world. Two travelers share one flight so the same delay
    produces one clean payout and one held case."""
    c.executemany("INSERT OR REPLACE INTO rules VALUES (?,?,?)", DEFAULT_RULES)
    c.executemany(
        "INSERT OR REPLACE INTO flights VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("OA 108", "Orchid Air", "BKK", "Bangkok", "CNX", "Chiang Mai", "Thu 14 Nov, 11:30", "11:30", 0, 84),
            ("OA 204", "Orchid Air", "BKK", "Bangkok", "HKT", "Phuket", "Thu 14 Nov, 14:10", "14:10", 0, 112),
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


def reset():
    """Wipe everything and reseed. Backs the Reset demo data button."""
    with conn() as c:
        for t in ("decisions", "baggage", "events", "policies", "flights", "rules"):
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
