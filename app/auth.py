"""Sign-in for travelers and insurer staff.

Travelers prove a booking with the reference and last name, then enter a one-time code sent to the contact on the
booking. Staff sign in with their work email and a one-time code. A code lasts 5 minutes and allows 5 tries.
A session is an opaque random token. Only its hash is stored, and it ends after 30 minutes (travelers) or
60 minutes (staff) without use.

Demo mode (KODA_DEMO_CODES, on by default): no email or text is really sent, so the code is returned to the screen
and labelled as a demo code. Turn it off and the code is only ever delivered out of band. In production the code goes
through the insurer's email or SMS provider and the booking site passes in a verified identity.

Roles: admin can change rules, airspace alerts and partners. reviewer can decide held claims. agent can look but not change.
"""
import hashlib
import os
import re
import secrets
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import JSONResponse

from . import bookings, db

router = APIRouter(prefix="/api/auth")
CODE_TTL, MAX_TRIES = 300, 5
IDLE = {"traveler": 1800, "staff": 3600}
_starts: dict[str, list[float]] = {}   # recent code requests per subject, to slow guessing


def enabled() -> bool:
    return os.environ.get("KODA_AUTH", "on") != "off"


def demo_codes() -> bool:
    return os.environ.get("KODA_DEMO_CODES", "on") != "off"


def _hash(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _mask(contact: str | None) -> str:
    if not contact:
        return "the contact on your booking"
    if "@" in contact:
        u, d = contact.split("@", 1)
        return u[0] + "*" * max(2, len(u) - 1) + "@" + d
    return "*" * max(0, len(contact) - 4) + contact[-4:]


def _throttle(key: str) -> None:
    now = time.time()
    hits = [t for t in _starts.get(key, []) if now - t < 600]
    if len(hits) >= 6:
        raise HTTPException(429, "Too many code requests. Wait a few minutes and try again.")
    _starts[key] = hits + [now]


def _challenge(c, kind: str, subject: str, contact: str | None) -> dict:
    code = f"{secrets.randbelow(10**6):06d}"
    cid = secrets.token_urlsafe(12)
    c.execute("INSERT INTO auth_codes (id,kind,subject,hash,expires,tries) VALUES (?,?,?,?,?,0)",
              (cid, kind, subject, _hash(cid + code), time.time() + CODE_TTL))
    out = {"challenge": cid, "sent_to": _mask(contact), "expires_in": CODE_TTL}
    if demo_codes():
        out["demo_code"] = code
    return out


def _session(c, kind: str, subject: str, role: str) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    c.execute("INSERT INTO sessions (token_hash,kind,subject,role,created,last_seen) VALUES (?,?,?,?,?,?)",
              (_hash(token), kind, subject, role, now, now))
    c.execute("DELETE FROM sessions WHERE last_seen<?", (now - 86400,))
    c.execute("DELETE FROM auth_codes WHERE expires<?", (now - 3600,))
    return token


def who(token: str | None) -> dict | None:
    """The live session for a token, refreshed on use. None when missing or expired."""
    if not token:
        return None
    with db.conn() as c:
        s = db.one(c, "SELECT * FROM sessions WHERE token_hash=?", (_hash(token),))
        if not s:
            return None
        now = time.time()
        if now - s["last_seen"] > IDLE[s["kind"]]:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (s["token_hash"],))
            return None
        c.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?", (now, s["token_hash"]))
        return s


def _profile(c, s: dict) -> dict:
    if s["kind"] == "staff":
        st = db.one(c, "SELECT id,name,title,email,initials,role FROM staff WHERE id=?", (s["subject"],)) or {}
        return {"kind": "staff", "id": s["subject"], "role": s["role"], **st}
    cu = db.one(c, "SELECT id,name,email FROM customers WHERE id=?", (s["subject"],)) or {}
    return {"kind": "traveler", "id": s["subject"], "role": "traveler", **cu}


# ------------------------------------------------------------------ routes

class TravelerStart(BaseModel):
    ref: str
    surname: str


class StaffStart(BaseModel):
    email: str


class Verify(BaseModel):
    challenge: str
    code: str


@router.post("/traveler/start")
def traveler_start(body: TravelerStart):
    ref = bookings.clean_ref(body.ref)
    _throttle("t:" + ref)
    with db.conn() as c:
        b = bookings.lookup(c, ref, body.surname)
        cu = db.one(c, "SELECT * FROM customers WHERE id=?", (b["customer_id"],)) if b and b.get("customer_id") else None
        if not cu:
            # Same message for a wrong reference and a wrong name, so the form does not reveal which bookings exist.
            raise HTTPException(404, "We could not find that booking. Check the reference and the last name on the ticket.")
        return {**_challenge(c, "traveler", cu["id"], cu.get("email") or cu.get("phone")), "name": cu["name"]}


@router.post("/staff/start")
def staff_start(body: StaffStart):
    email = body.email.strip().lower()
    _throttle("s:" + email)
    with db.conn() as c:
        st = db.one(c, "SELECT * FROM staff WHERE LOWER(email)=?", (email,))
        if not st:
            raise HTTPException(404, "That email is not a registered insurer account.")
        return {**_challenge(c, "staff", st["id"], st["email"]), "name": st["name"]}


@router.post("/verify")
def verify(body: Verify):
    with db.conn() as c:
        ch = db.one(c, "SELECT * FROM auth_codes WHERE id=?", (body.challenge,))
        if not ch or ch["expires"] < time.time():
            raise HTTPException(400, "That code has expired. Go back and ask for a new one.")
        if ch["tries"] >= MAX_TRIES:
            raise HTTPException(429, "Too many wrong codes. Go back and ask for a new one.")
        c.execute("UPDATE auth_codes SET tries=tries+1 WHERE id=?", (ch["id"],))
        c.commit()  # a wrong guess must count even though this request ends in an error
        if not secrets.compare_digest(ch["hash"], _hash(ch["id"] + re.sub(r"\D", "", body.code))):
            left = MAX_TRIES - ch["tries"] - 1
            raise HTTPException(400, f"That code is not right. {left} {'try' if left == 1 else 'tries'} left." if left else "Too many wrong codes. Go back and ask for a new one.")
        c.execute("DELETE FROM auth_codes WHERE id=?", (ch["id"],))
        role = "traveler"
        if ch["kind"] == "staff":
            role = (db.one(c, "SELECT role FROM staff WHERE id=?", (ch["subject"],)) or {}).get("role") or "agent"
        token = _session(c, ch["kind"], ch["subject"], role)
        s = {"kind": ch["kind"], "subject": ch["subject"], "role": role}
        db.log(c, None, "signed_in", {"kind": ch["kind"], "who": ch["subject"]})
        return {"token": token, "me": _profile(c, s)}


@router.get("/me")
def me(request: Request):
    s = who(request.headers.get("x-session"))
    if not s:
        raise HTTPException(401, "Your session ended. Sign in again.")
    with db.conn() as c:
        return _profile(c, s)


@router.post("/logout")
def logout(request: Request):
    tok = request.headers.get("x-session")
    if tok:
        with db.conn() as c:
            c.execute("DELETE FROM sessions WHERE token_hash=?", (_hash(tok),))
    return {"ok": True}


class DemoSession(BaseModel):
    kind: str
    id: str


@router.post("/demo-session")
def demo_session(body: DemoSession):
    """Signs in a demo account without a code. For the presenter page only, and only while demo codes are on."""
    if not demo_codes():
        raise HTTPException(404, "Not available.")
    with db.conn() as c:
        if body.kind == "staff":
            st = db.one(c, "SELECT * FROM staff WHERE id=?", (body.id,))
            if not st:
                raise HTTPException(404, "Unknown staff account.")
            s = {"kind": "staff", "subject": st["id"], "role": st.get("role") or "agent"}
        else:
            if not db.one(c, "SELECT 1 FROM customers WHERE id=?", (body.id,)):
                raise HTTPException(404, "Unknown customer.")
            s = {"kind": "traveler", "subject": body.id, "role": "traveler"}
        return {"token": _session(c, s["kind"], s["subject"], s["role"]), "me": _profile(c, s)}


@router.get("/demo-accounts")
def demo_accounts():
    """The accounts a judge can sign in with. Prefilled on the sign-in screens."""
    if not demo_codes():
        return {"travelers": [], "staff": []}
    out = {"travelers": [], "staff": []}
    with db.conn() as c:
        for ref, (name, what) in bookings.DEMO_REFS.items():
            b = bookings.lookup(c, ref, name.split(" ")[-1])
            if b:
                out["travelers"].append({"ref": ref, "surname": name.split(" ")[-1], "name": name, "what": what, "customer": b["customer_id"]})
        out["staff"] = db.rows(c, "SELECT id,name,title,email,role FROM staff ORDER BY rowid")
    return out


# ------------------------------------------------------------------ guard

# Console actions that need more than a signed-in agent. Matched on method and path prefix.
_ADMIN = [("PUT", "/api/console/rules"), ("POST", "/api/console/airspace"), ("DELETE", "/api/console/airspace"),
          ("PUT", "/api/console/partners")]
_DECIDE = [("POST", "/api/console/review/"), ("POST", "/api/console/baggage/"), ("POST", "/api/console/extra/"), ("POST", "/api/console/payments/")]


def _denied(code: int, text: str) -> JSONResponse:
    return JSONResponse({"detail": text}, status_code=code)


def guard(request: Request) -> JSONResponse | None:
    """Return an error response when the request needs a session it does not have. None lets it through."""
    path, method = request.url.path, request.method
    # Presenter tools (reset, simulated delays, sample travelers) exist only in demo mode. Outside it they would let
    # anyone wipe the data or trigger payouts, so they are switched off together with the demo sign-in.
    if path.startswith("/api/demo/") and not demo_codes():
        return _denied(404, "Not available.")
    # The flight status feed can be given a shared secret. Unset keeps the open demo webhook working as before.
    hook_secret = os.environ.get("KODA_WEBHOOK_SECRET")
    if path == "/api/webhook/flight-event" and hook_secret and not secrets.compare_digest(request.headers.get("x-webhook-secret", ""), hook_secret):
        return _denied(401, "Webhook secret missing or wrong.")
    if not enabled():
        return None
    tok = request.headers.get("x-session")
    if path.startswith("/api/customer/"):
        cid = path.split("/")[3]
        s = who(tok)
        if not s or s["kind"] != "traveler":
            return _denied(401, "Sign in to see your points and details.")
        if s["subject"] != cid:
            return _denied(403, "This account belongs to someone else.")
    elif path.startswith("/api/traveler/") or (path.startswith("/api/airport/") and request.query_params.get("pid")):
        # Per-trip endpoints act on one policy: its points, its claims, its cover. A policy that belongs to an account
        # may only be used by that account's signed-in traveler. Anonymous quotes (no account yet) stay reachable by id.
        pid = path.split("/")[3] if path.startswith("/api/traveler/") else request.query_params.get("pid")
        with db.conn() as c:
            p = db.one(c, "SELECT customer_id FROM policies WHERE id=?", (pid,))
        if p and p["customer_id"]:
            s = who(tok)
            if not s or s["kind"] != "traveler":
                return _denied(401, "Sign in to see your trips.")
            if s["subject"] != p["customer_id"]:
                return _denied(404, "policy not found")  # same answer as a wrong id, so ids cannot be probed
    elif path.startswith("/api/app/") and path != "/api/app/mode" and not path.startswith("/api/app/qr/"):
        s = who(tok)
        if not s or s["kind"] != "traveler":
            return _denied(401, "Sign in to see your trips.")
    elif path.startswith("/api/console/"):
        s = who(tok)
        if not s or s["kind"] != "staff":
            return _denied(401, "Sign in to the insurer desk.")
        if (any(method == m and path.startswith(p) for m, p in _ADMIN) or (method == "POST" and path.endswith("/reopen"))) and s["role"] != "admin":
            return _denied(403, "Only an admin can do this: change rules, airspace alerts or partners, or reopen a claim as an exception.")
        if any(method == m and path.startswith(p) for m, p in _DECIDE) and s["role"] not in ("admin", "reviewer"):
            return _denied(403, "Your role can look but not decide. Ask a reviewer.")
        if path.startswith("/api/console/staff/") and method == "PUT" and s["role"] != "admin" and path.rsplit("/", 1)[1] != s["subject"]:
            return _denied(403, "You can only edit your own profile.")
        # The signed-in person is the one who signs decisions, whatever the page sent in X-Staff.
        request.scope["headers"] = [(k, v) for k, v in request.scope["headers"] if k != b"x-staff"] + [(b"x-staff", s["subject"].encode())]
    return None
