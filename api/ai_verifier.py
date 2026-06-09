from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Dict, List, Optional, Tuple

import httpx

try:
    import _env  # noqa: F401  (loads a local .env into os.environ when present)
except ImportError:
    pass

logger = logging.getLogger("bob_sim.ai_verifier")


def _key() -> str:
    """Return the configured Gemini API key (empty string if unset)."""
    return os.environ.get("GEMINI_API_KEY", "").strip()


def _model() -> str:
    """Return the configured Gemini model name."""
    return os.environ.get("GEMINI_MODEL", "gemini-2.0-flash").strip()


def gemini_available() -> bool:
    """True when a Gemini API key is configured."""
    return bool(_key())


def _call_gemini(prompt: str) -> Optional[dict]:
    """Call Gemini and parse its JSON response, or None on any failure."""
    key = _key()
    if not key:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{_model()}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.3, "responseMimeType": "application/json"},
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            r = client.post(url, params={"key": key}, json=payload)
        if r.status_code >= 400:
            logger.warning("Gemini error %s: %s", r.status_code, r.text[:200])
            return None
        text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(text)
    except Exception as exc:  # pragma: no cover - network/parse guard
        logger.warning("Gemini call failed: %s", exc)
        return None


# --------------------------------------------------------------------------- #
# Account facts → verification questions
# --------------------------------------------------------------------------- #
def _facts(account: dict, recent: List[dict]) -> dict:
    """Extract the verifiable facts known about an account."""
    digits = re.sub(r"\D", "", account.get("account_number", ""))
    phone_digits = re.sub(r"\D", "", account.get("phone", ""))
    last_transfer = next(
        (a for a in recent
         if a.get("action") == "transfer" and a.get("amount") and a.get("executed")),
        None,
    )
    return {
        "name": account.get("name", ""),
        "home_city": account.get("home_city", ""),
        "account_last4": digits[-4:] if len(digits) >= 4 else digits,
        "phone_last4": phone_digits[-4:] if len(phone_digits) >= 4 else phone_digits,
        "last_transfer_amount": int(last_transfer["amount"]) if last_transfer else None,
    }


def _local_questions(facts: dict) -> List[dict]:
    """Deterministic fallback questions grounded in the account facts."""
    qs = [
        {"id": "q1", "prompt": "Which city is your Bank of Baroda home branch in?",
         "expected": facts["home_city"]},
        {"id": "q2", "prompt": "What are the last 4 digits of your account number?",
         "expected": facts["account_last4"]},
    ]
    if facts.get("last_transfer_amount"):
        qs.append({"id": "q3", "prompt": "What was the amount (in ₹) of your most recent transfer?",
                   "expected": str(facts["last_transfer_amount"])})
    else:
        qs.append({"id": "q3", "prompt": "What are the last 4 digits of your registered mobile number?",
                   "expected": facts["phone_last4"]})
    return qs


def generate_challenge(account: dict, reasons: List[str], action: str,
                       recent: List[dict]) -> dict:
    """Build a real-time identity challenge for a suspicious action.

    Args:
        account: The full account record.
        reasons: Plain-English reasons the action looked suspicious.
        action: The action being attempted (login/transfer/account_recovery/…).
        recent: Recent activity entries for this account.

    Returns:
        A dict with ``intro``, ``questions`` (each with a server-side
        ``expected`` answer) and ``ai_powered``.
    """
    facts = _facts(account, recent)
    reason_text = "; ".join(reasons) if reasons else "unusual activity on the account"

    if gemini_available():
        prompt = (
            "You are the identity-verification assistant for Bank of Baroda. A customer action "
            "looked suspicious and must be verified in real time before it is allowed.\n"
            f"Action attempted: {action}.\n"
            f"Why it looked suspicious: {reason_text}.\n"
            "Known account facts (use ONLY these to form questions and set the correct answer):\n"
            f"{json.dumps(facts)}\n\n"
            "Return STRICT JSON with this shape:\n"
            '{"intro": "<1-2 warm but firm sentences telling the customer a quick security check '
            'is needed and why>", "questions": [{"id":"q1","prompt":"<question>","expected":"<correct answer>"}, ...]}\n'
            "Rules: produce exactly 3 questions; base each strictly on the known facts; keep prompts "
            "short and answerable in a few words; do not reveal the answers in the prompts; the "
            "'expected' value must be the correct answer derived from the facts."
        )
        data = _call_gemini(prompt)
        if data and isinstance(data.get("questions"), list) and data["questions"]:
            qs = []
            for i, q in enumerate(data["questions"][:3], 1):
                qs.append({
                    "id": q.get("id") or f"q{i}",
                    "prompt": str(q.get("prompt", "")).strip(),
                    "expected": str(q.get("expected", "")).strip(),
                })
            return {"intro": str(data.get("intro", "")).strip(),
                    "questions": qs, "ai_powered": True}

    # Local fallback.
    intro = (
        f"We noticed {reason_text}. For your security, please confirm a few details "
        "about your account before we continue."
    )
    return {"intro": intro, "questions": _local_questions(facts), "ai_powered": False}


# --------------------------------------------------------------------------- #
# Grading
# --------------------------------------------------------------------------- #
def _norm(s: str) -> str:
    """Normalise an answer for fuzzy comparison."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _local_grade(questions: List[dict], answers: Dict[str, str]) -> Tuple[bool, float, str]:
    """Deterministic fallback grading of answers vs expected values."""
    total = len(questions) or 1
    correct = 0
    for q in questions:
        given = _norm(answers.get(q["id"], ""))
        expected = _norm(q.get("expected", ""))
        if not expected:
            continue
        if given and (given == expected or expected in given or given in expected):
            correct += 1
    ratio = correct / total
    verified = correct >= math.ceil(total * 0.6)
    confidence = round(ratio, 2)
    reason = (f"{correct}/{total} security answers matched the account records."
              if verified else
              f"Only {correct}/{total} answers matched — identity could not be confirmed.")
    return verified, confidence, reason


def evaluate_answers(account: dict, questions: List[dict],
                     answers: Dict[str, str]) -> dict:
    """Grade the customer's answers and decide whether to verify them.

    Args:
        account: The account record.
        questions: The issued questions (each with an ``expected`` answer).
        answers: Mapping of question id -> the customer's answer.

    Returns:
        A dict with ``verified`` (bool), ``confidence`` (0-1), ``reason`` and
        ``ai_powered``.
    """
    if gemini_available():
        graded = [
            {"prompt": q.get("prompt", ""), "expected": q.get("expected", ""),
             "given": answers.get(q["id"], "")}
            for q in questions
        ]
        prompt = (
            "You are the identity-verification assistant for Bank of Baroda, grading a customer's "
            "answers to security questions in real time. Be reasonably strict: small formatting or "
            "spelling differences are fine, but wrong values, blanks or guesses must fail.\n"
            f"Questions with the correct answer and what the customer gave:\n{json.dumps(graded)}\n\n"
            'Return STRICT JSON: {"verified": <true|false>, "confidence": <0..1>, '
            '"reason": "<one short sentence>"}. Mark verified true only if the customer clearly '
            "answered enough questions correctly to confirm they are the genuine account holder."
        )
        data = _call_gemini(prompt)
        if data and "verified" in data:
            try:
                return {
                    "verified": bool(data["verified"]),
                    "confidence": float(data.get("confidence", 0.5)),
                    "reason": str(data.get("reason", "")).strip(),
                    "ai_powered": True,
                }
            except (TypeError, ValueError):
                pass

    verified, confidence, reason = _local_grade(questions, answers)
    return {"verified": verified, "confidence": confidence, "reason": reason,
            "ai_powered": False}
