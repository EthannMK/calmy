"""Reviewer copilot: a recommendation, a draft message and answers about one held claim.

The copilot only advises. It can recommend approve, ask for proof, or escalate, and it never declines:
only a person does. Everything is built from the claim's own risk breakdown, so the answer can be
checked. Gemini rewrites the draft and answers free questions when it is configured; without it the
rule-based text is used.
"""
import os
import re

APPROVE, PROOF, ESCALATE = "approve", "ask_proof", "escalate"


def recommend(d: dict, p: dict, traveler_name: str, cur: str, others_on_account: int) -> dict:
    parts = d.get("breakdown") or []
    labels = {t: pts for t, pts in parts}
    has = lambda s: any(s in t for t in labels)
    score = d.get("risk_score") or 0
    evidence = [f"{t} (+{pts})" for t, pts in parts if pts and not t.startswith("Base")]
    if has("share the same contact") or others_on_account:
        action, conf = ESCALATE, min(92, 60 + score // 3)
        why = ["The payout account is shared with other profiles. That is the strongest sign of organised misuse, so a senior reviewer should look."]
    elif has("Flight time changed") or has("delay was already likely") or has("Several covers"):
        action, conf = PROOF, min(88, 52 + score // 3)
        why = ["The signals point to cover bought close to a known problem, not to organised misuse. Ask for the booking confirmation and the purchase time before deciding."]
    else:
        action, conf = APPROVE, min(90, 55 + (60 - score) if score < 60 else 55)
        why = ["Only mild signals. The delay is confirmed by flight data, and nothing else suggests misuse."]
    first = (traveler_name or "there").split()[0]
    drafts = {
        APPROVE: f"Hi {first}, thanks for your patience. We checked your claim for flight {{flight}} and approved it. {d['amount']} {cur} is on its way to your account.",
        PROOF: f"Hi {first}, we are sorry about your delay. To finish checking your claim for {{flight}} we need your booking confirmation email showing when the trip was booked. A reply to this message is enough.",
        ESCALATE: f"Hi {first}, thank you for your patience. A senior specialist is looking at your claim for {{flight}} and will contact you within one working day. You do not need to do anything now.",
    }
    return {"action": action, "confidence": int(conf), "why": why, "evidence": evidence, "draft": drafts[action],
            "label": {APPROVE: "Approve", PROOF: "Ask for proof", ESCALATE: "Escalate to a senior reviewer"}[action],
            "never_declines": "The copilot advises. Only a person can decline a claim."}


def answer(question: str, d: dict, rec: dict, p: dict) -> str:
    q = question.lower()
    parts = [(t, pts) for t, pts in (d.get("breakdown") or []) if not t.startswith("Base")]
    if any(w in q for w in ("why", "held", "flag", "score", "risk")):
        top = "; ".join(f"{t} (+{pts})" for t, pts in sorted(parts, key=lambda x: -x[1])[:3]) or "no signals beyond the base score"
        return f"Risk score {d['risk_score']} against the auto-pay line. The signals were: {top}."
    if any(w in q for w in ("pay", "amount", "how much", "cost")):
        return f"The rule pays {d['amount']} for a {d['delay_minutes']} minute delay ({d['clause']}). It is a fixed amount, not tied to the traveler's costs."
    if any(w in q for w in ("account", "history", "before", "prior", "claim")):
        return ("Payout account: " + p["payout_account"] + ". "
                + ("A claim was paid on this profile in the last 12 months. " if p.get("f_prior_claims") else "No earlier paid claim on this profile. ")
                + ("The account is shared with other profiles." if p.get("f_shared_account") else "The account is used by this traveler only."))
    if any(w in q for w in ("recommend", "should", "do", "next", "approve")):
        return f"I suggest: {rec['label']} ({rec['confidence']}% confident). {rec['why'][0]}"
    return f"I can explain the score, the amount, the account history or what to do next. For now: {rec['why'][0]}"


_NUM = re.compile(r"\d+(?:[.,]\d+)*")
_DECISION = ("approv", "declin", "reject", "guarantee", "promise", "refund", "compensat", "entitled", "owe you")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in _NUM.findall(text or "")}


def safe_rewrite(original: str, rewritten: str | None, decisions: bool = True) -> str | None:
    """Accept a model rewrite only if it rewords. It may not add a number the original did not have, may not add a
    decision or a promise the original did not make, and may not balloon. Anything else returns None, and the caller
    keeps the original. This is what stops text typed by a traveler (prompt injection) from changing a claim fact."""
    if not rewritten:
        return None
    if not _numbers(rewritten) <= _numbers(original):
        return None
    low, orig = rewritten.lower(), (original or "").lower()
    if decisions and any(w in low and w not in orig for w in _DECISION):
        return None
    if len(rewritten) > 3 * len(original or "") + 200:
        return None
    return rewritten


def ask_gemini(prompt: str) -> str | None:
    """Optional Gemini call. Returns None when not configured or on any failure. Never call with the database lock held."""
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return None
    try:  # pragma: no cover - needs cloud credentials
        from google import genai
        from google.genai import types
        client = genai.Client(vertexai=True, project=project, location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
                              http_options=types.HttpOptions(timeout=8000))
        resp = client.models.generate_content(model=os.environ.get("GEMINI_MODEL", "gemini-2.5-flash"), contents=prompt)
        return (resp.text or "").strip() or None
    except Exception:
        return None
