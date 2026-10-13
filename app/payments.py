"""Payments: what was actually sent for an approved claim, kept apart from the claim decision.

A claim decision says how much the traveler is entitled to (decisions.amount, the running total). This ledger says
what has been sent towards it, in one row per transfer:

  * The first payout and every later top-up (a delay that grows, a cancellation after a delay payout) are separate
    rows, each for the difference still owed. The outstanding amount is always total entitled minus what the ledger
    already holds, so repeating an event, a retry or a second click can never send money twice.
  * A row is pending, succeeded or failed. A failed row is retried in place (same id, attempts + 1); it is never
    duplicated.
  * Every row in this prototype is simulated. KODA points are an internal ledger and always settle. Card and bank
    rails are simulated transfers and say so; no real money moves.

FAIL_NEXT is a test hook: set it to n and the next n card or bank attempts fail, so failure and retry can be tested.
"""
import time
import uuid

from . import db, markets

FAIL_NEXT = 0

SCHEMA = """
CREATE TABLE IF NOT EXISTS payments (
    id TEXT PRIMARY KEY,
    decision_id TEXT NOT NULL,
    seq INTEGER NOT NULL,                  -- 1 for the first payout, 2.. for top-ups
    amount INTEGER NOT NULL,               -- this transfer only, in the policy currency
    currency TEXT,
    rail TEXT,                             -- where it went, as stamped on the decision
    status TEXT NOT NULL,                  -- pending | succeeded | failed
    simulated INTEGER NOT NULL DEFAULT 1,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    created_at REAL, updated_at REAL,
    UNIQUE (decision_id, seq)
);
"""


def init(c) -> None:
    c.executescript(SCHEMA)


def rows(c, decision_id: str) -> list[dict]:
    return db.rows(c, "SELECT * FROM payments WHERE decision_id=? ORDER BY seq", (decision_id,))


def _attempt(c, p: dict) -> dict:
    """Try one transfer. Points settle inside KODA; card and bank are simulated and can be made to fail in tests."""
    global FAIL_NEXT
    now = time.time()
    points = (p["rail"] or "").startswith(markets.POINTS_RAIL)
    if not points and FAIL_NEXT > 0:
        FAIL_NEXT -= 1
        status, err = "failed", "Simulated rail declined the transfer"
    else:
        status, err = "succeeded", None
    c.execute("UPDATE payments SET status=?, attempts=attempts+1, last_error=?, updated_at=? WHERE id=?", (status, err, now, p["id"]))
    db.log(c, None, f"payment_{status}", {"payment": p["id"], "decision": p["decision_id"], "amount": p["amount"], "rail": p["rail"], "simulated": True})
    return db.one(c, "SELECT * FROM payments WHERE id=?", (p["id"],))


def settle(c, decision_id: str) -> dict | None:
    """Send whatever is still owed on an approved claim. Safe to call any number of times."""
    d = db.one(c, "SELECT d.id, d.status, d.amount, d.payout, p.payout_account, p.currency FROM decisions d "
                  "JOIN policies p ON p.id=d.policy_id WHERE d.id=?", (decision_id,))
    if not d or d["status"] != "paid":
        return None  # nothing is ever sent for a claim that is not approved
    have = db.rows(c, "SELECT seq, amount FROM payments WHERE decision_id=?", (decision_id,))
    owed = int(d["amount"]) - sum(r["amount"] for r in have)
    if owed <= 0:
        return None
    seq = (max((r["seq"] for r in have), default=0)) + 1
    now = time.time()
    pid = "PY-" + uuid.uuid4().hex[:8].upper()
    c.execute("INSERT INTO payments (id,decision_id,seq,amount,currency,rail,status,simulated,attempts,created_at,updated_at) "
              "VALUES (?,?,?,?,?,?,'pending',1,0,?,?)",
              (pid, decision_id, seq, owed, d["currency"], d["payout"] or d["payout_account"], now, now))
    return _attempt(c, db.one(c, "SELECT * FROM payments WHERE id=?", (pid,)))


def retry(c, payment_id: str) -> dict:
    p = db.one(c, "SELECT * FROM payments WHERE id=?", (payment_id,))
    if not p:
        raise ValueError("Payment not found.")
    if p["status"] != "failed":
        raise ValueError("Only a failed payment can be retried.")
    return _attempt(c, p)


def summary(c, decision_id: str, total: int | None = None) -> dict:
    """What the apps show: sent, still owed, and the state of the latest transfer."""
    r = rows(c, decision_id)
    sent = sum(x["amount"] for x in r if x["status"] == "succeeded")
    state = "none" if not r else "failed" if any(x["status"] == "failed" for x in r) else \
            "pending" if any(x["status"] == "pending" for x in r) else "succeeded"
    out = {"state": state, "sent": sent, "simulated": True, "payments": r}
    if total is not None:
        out["outstanding"] = max(0, int(total) - sent)
    return out
