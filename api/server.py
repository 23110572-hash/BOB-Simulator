from __future__ import annotations

import logging
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# Ensure this file's own directory (the Vercel ``api/`` folder) is importable.
# Vercel's serverless Python runtime does not always place the function's
# directory on sys.path, which would make the sibling imports below fail with
# ModuleNotFoundError and crash the whole function on every invocation.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

try:
    import _env  # noqa: F401  (loads a local .env into os.environ when present)
except ImportError:
    pass
import ai_verifier
import db
from trust_client import TrustIQError, evaluate as trustiq_evaluate, health as trustiq_health

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("bob_sim.server")

app = FastAPI(title="Bank of Baroda — Core Simulator", version="1.0.0")

_HERE = os.path.dirname(os.path.abspath(__file__))


# Cities TrustIQ can geo-locate (lat, lon) — used to compute travel distance.
CITY_COORDS = {
    "Mumbai": (19.0760, 72.8777), "Delhi": (28.7041, 77.1025),
    "Bengaluru": (12.9716, 77.5946), "Chennai": (13.0827, 80.2707),
    "Kolkata": (22.5726, 88.3639), "Hyderabad": (17.3850, 78.4867),
    "Pune": (18.5204, 73.8567), "Ahmedabad": (23.0225, 72.5714),
    "Jaipur": (26.9124, 75.7873), "Surat": (21.1702, 72.8311),
    "London": (51.5074, -0.1278), "Dubai": (25.2048, 55.2708),
    "Singapore": (1.3521, 103.8198), "New York": (40.7128, -74.0060),
    "Moscow": (55.7558, 37.6173),
}

CHANNELS = ["mobile_banking", "internet_banking", "upi", "branch", "call_center", "atm"]

# Map the simulator's action to TrustIQ's richer event taxonomy.
ACTION_TO_EVENT = {
    "login": "login",
    "transfer": "transfer",
    "add_payee": "beneficiary_add",
    "profile_change": "profile_change",
    "account_recovery": "account_recovery",
}

# Friendly labels for messages.
ACTION_LABEL = {
    "login": "Sign-in",
    "transfer": "Transfer",
    "add_payee": "Add payee",
    "profile_change": "Profile change",
    "account_recovery": "Account recovery",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _haversine(a, b) -> float:
    """Great-circle distance (km) between two (lat, lon) points."""
    r = 6371.0
    lat1, lon1, lat2, lon2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _distance_from_home(home_city: str, city: str) -> float:
    """Estimate km between an account's home city and the event city."""
    if home_city == city:
        return 1.0
    h, c = CITY_COORDS.get(home_city), CITY_COORDS.get(city)
    if h and c:
        return round(_haversine(h, c), 1)
    return 800.0


def _behavior(erratic: bool) -> dict:
    """Return a behavioural-biometric signal (genuine vs erratic/attacker)."""
    if erratic:
        return {"dwell_times": [40, 220, 35], "flight_times": [210, 30, 260],
                "swipe_velocity": 6.0, "mouse_entropy": 0.1, "tap_pressure": 0.95}
    return {"dwell_times": [100, 101, 99], "flight_times": [80, 81, 79],
            "swipe_velocity": 1.2, "mouse_entropy": 0.7, "tap_pressure": 0.5}


def _device(acct: dict, device_profile: str, vpn: bool) -> dict:
    """Build a device fingerprint for the registered phone or a new device."""
    if device_profile == "registered":
        return {"device_id": acct["device_id"], "os": acct["device_os"],
                "browser": "BoB World App", "screen_resolution": "1080x2400",
                "webgl_hash": acct["device_id"] + "GL", "is_vpn_or_tor": vpn}
    # An unrecognised device the customer has never used before.
    return {"device_id": "unknown_" + uuid.uuid4().hex[:6], "os": "Windows 11",
            "browser": "Chrome", "screen_resolution": "1920x1080",
            "webgl_hash": uuid.uuid4().hex[:8], "is_vpn_or_tor": vpn}


def _account_public(acct: dict) -> dict:
    """Return the public (UI-safe) view of an account."""
    return {
        "user_id": acct["user_id"], "name": acct["name"],
        "account_number": acct["account_number"], "ifsc": acct["ifsc"],
        "balance": round(acct["balance"], 2), "home_city": acct["home_city"],
        "device_id": acct["device_id"], "phone": acct["phone"],
        "last_action": acct.get("last_action"),
        "last_verdict": acct.get("last_verdict"),
    }


def _suspicion_reasons(req: "ActionRequest", city: str, home_city: str) -> List[str]:
    """Plain-English reasons an action looked suspicious (for the AI challenge)."""
    reasons: List[str] = []
    if req.device_profile == "new":
        reasons.append("a sign-in from an unrecognised device")
    if req.vpn:
        reasons.append("a connection through VPN/Tor")
    if city and city != home_city:
        reasons.append(f"a location far from home ({city})")
    if req.hour < 5 or req.hour >= 23:
        reasons.append("activity at an unusual hour")
    if req.erratic:
        reasons.append("an erratic interaction pattern")
    if req.action == "transfer" and req.amount >= 50000:
        reasons.append("a high-value transfer")
    if req.new_payee and req.action in ("transfer", "add_payee"):
        reasons.append("a brand-new payee")
    return reasons


def _apply(acct: dict, req: "ActionRequest", allowed: bool) -> Tuple[bool, bool, str]:
    """Enforce a decision against the bank's state.

    Args:
        acct: The account record (mutated for transfers).
        req: The action request.
        allowed: Whether the action is permitted.

    Returns:
        Tuple of (executed, blocked, human message).
    """
    label = ACTION_LABEL.get(req.action, req.action.title())

    if req.action == "transfer":
        if not allowed:
            return False, True, "Transfer denied — identity could not be verified. No money moved."
        if req.amount <= 0:
            return False, False, "Enter an amount greater than zero."
        if req.amount > acct["balance"]:
            return False, False, "Insufficient balance — transfer declined."
        acct["balance"] -= req.amount
        recipient = db.get_account(req.dest_account)
        if recipient is not None:
            recipient["balance"] += req.amount
            db.update_account(recipient)
        return True, False, f"₹{req.amount:,.0f} transferred."

    if not allowed:
        return False, True, f"{label} denied — identity could not be verified."

    msg = {
        "login": "Signed in.",
        "add_payee": "Payee added.",
        "profile_change": "Profile updated.",
        "account_recovery": "Account recovery approved — access restored.",
    }.get(req.action, "Done.")
    return True, False, msg


def _verdict_from_eval(ev: dict) -> dict:
    """Build the compact verdict view returned to the UI from a TrustIQ result."""
    return {
        "trust_score": ev.get("trust_score"),
        "trust_band": ev.get("trust_band"),
        "trust_trend": ev.get("trust_trend"),
        "identity_match": ev.get("identity_match_score"),
        "risk_score": ev.get("risk_score"),
        "action": ev.get("action", "silent_pass"),
        "headline": ev.get("ai_insight", {}).get("headline", ""),
        "narrative": ev.get("ai_insight", {}).get("narrative", ev.get("explanation", "")),
        "recommended_action": ev.get("ai_insight", {}).get("recommended_action", ""),
    }


def _log_activity(acct: dict, req: "ActionRequest", city: str, *, executed: bool,
                  blocked: bool, message: str, verdict: dict,
                  status: str, verification: Optional[dict] = None) -> None:
    """Record an entry in the live activity feed."""
    entry = {
        "id": uuid.uuid4().hex[:8],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "account": acct["name"],
        "user_id": acct["user_id"],
        "action": req.action,
        "channel": req.channel,
        "city": city,
        "amount": req.amount if req.action == "transfer" else None,
        "executed": executed,
        "blocked": blocked,
        "status": status,
        "message": message,
        "verdict": verdict,
        "verification": verification,
    }
    db.insert_activity(entry)


# --------------------------------------------------------------------------- #
# Request models
# --------------------------------------------------------------------------- #
class ActionRequest(BaseModel):
    """A customer action submitted from the simulator UI."""

    account_id: str
    action: str = Field("login", description="login|transfer|add_payee|profile_change")
    channel: str = "mobile_banking"
    device_profile: str = Field("registered", description="registered|new")
    city: str = ""
    vpn: bool = False
    erratic: bool = False
    hour: int = Field(12, ge=0, le=23)
    amount: float = 0.0
    dest_account: str = ""
    new_payee: bool = False


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.get("/")
def index() -> FileResponse:
    """Serve the simulator single-page UI."""
    path1 = os.path.join(_HERE, "index.html")
    if os.path.exists(path1):
        return FileResponse(path1)
    path2 = os.path.join(os.path.dirname(_HERE), "index.html")
    if os.path.exists(path2):
        return FileResponse(path2)
    raise HTTPException(status_code=404, detail="index.html not found")


@app.get("/api/accounts")
def list_accounts() -> dict:
    """Return all controllable accounts, cities, channels and TrustIQ status."""
    import traceback
    try:
        accounts = db.get_accounts()
        return {
            "accounts": [_account_public(a) for a in accounts.values()],
            "cities": list(CITY_COORDS.keys()),
            "channels": CHANNELS,
            "trustiq": trustiq_health(),
            "gemini": ai_verifier.gemini_available(),
        }
    except Exception as exc:
        logger.error("Failed to load accounts: %s", traceback.format_exc())
        raise HTTPException(
            status_code=500,
            detail=f"{type(exc).__name__}: {exc}",
        )


@app.get("/api/activity")
def activity() -> List[dict]:
    """Return the most recent simulated actions and their TrustIQ verdicts."""
    try:
        return db.get_activity(40)
    except Exception as exc:
        logger.error("Failed to load activity: %s", exc)
        return []


@app.post("/api/reset")
def reset() -> dict:
    """Reset all account balances and clear the activity log."""
    db.reset()
    return {"ok": True, "accounts": [_account_public(a) for a in db.get_accounts().values()]}


@app.post("/api/action")
def perform_action(req: ActionRequest) -> dict:
    """Perform a customer action, ask TrustIQ to score it, and enforce the verdict.

    Args:
        req: The action submitted from the UI.

    Returns:
        A dict with the TrustIQ verdict, whether the action was executed, a
        human message, and the updated account.
    """
    acct = db.get_account(req.account_id)
    if acct is None:
        raise HTTPException(status_code=404, detail="Unknown account")

    event_type = ACTION_TO_EVENT.get(req.action)
    if event_type is None:
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    city = req.city or acct["home_city"]
    payload = {
        "user_id": acct["user_id"],
        "channel": req.channel,
        "event_type": event_type,
        "behavioral": _behavior(req.erratic),
        "device": _device(acct, req.device_profile, req.vpn),
        "context": {
            "amount": req.amount,
            "destination_account": req.dest_account or None,
            "city": city,
            "hour_of_day": req.hour,
            "distance_from_home_km": _distance_from_home(acct["home_city"], city),
        },
        "session_id": f"{acct['user_id']}:sim",
        "is_employee": False,
    }

    # Beneficiary context for money movement / payee actions.
    if req.action in ("transfer", "add_payee"):
        payload["beneficiary"] = {
            "account": req.dest_account or "external",
            "is_new": req.new_payee,
            "age_days": 0.0 if req.new_payee else 120.0,
            "prior_transfer_count": 0 if req.new_payee else 9,
        }

    try:
        ev = trustiq_evaluate(payload)
    except TrustIQError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    decision = ev.get("action", "silent_pass")
    verdict = _verdict_from_eval(ev)
    acct["last_action"] = req.action
    acct["last_verdict"] = verdict
    db.update_account(acct)

    # SUSPICIOUS (would-be block) -> real-time AI identity verification instead
    # of a hard block. Normal / elevated actions pass with OTP-as-usual.
    if decision == "block":
        reasons = _suspicion_reasons(req, city, acct["home_city"])
        challenge = ai_verifier.generate_challenge(acct, reasons, req.action, db.get_activity(40))
        session_id = uuid.uuid4().hex[:12]
        db.save_verify_session(session_id, {
            "account_id": acct["user_id"],
            "request": req.model_dump(),
            "questions": challenge["questions"],   # includes server-side 'expected'
            "verdict": verdict,
            "city": city,
        })
        public_questions = [{"id": q["id"], "prompt": q["prompt"]} for q in challenge["questions"]]
        _log_activity(acct, req, city, executed=False, blocked=False,
                      message="Suspicious — real-time AI identity verification requested.",
                      verdict=verdict, status="verification_required")
        return {
            "status": "verification_required",
            "verification": {
                "session_id": session_id,
                "intro": challenge["intro"],
                "questions": public_questions,
                "ai_powered": challenge["ai_powered"],
            },
            "verdict": verdict,
            "account": _account_public(acct),
        }

    # Allowed (silently or after the usual OTP/push step-up).
    executed, blocked, message = _apply(acct, req, allowed=True)
    db.update_account(acct)
    if decision != "silent_pass" and executed:
        message += " (a one-time OTP / push confirmation was required)"
    _log_activity(acct, req, city, executed=executed, blocked=blocked,
                  message=message, verdict=verdict, status="done")
    return {
        "status": "done",
        "executed": executed,
        "blocked": blocked,
        "message": message,
        "verdict": verdict,
        "account": _account_public(acct),
    }


class VerifyRequest(BaseModel):
    """Submission of answers to an AI identity-verification challenge."""

    session_id: str
    answers: Dict[str, str] = Field(default_factory=dict)


@app.post("/api/verify/submit")
def verify_submit(req: VerifyRequest) -> dict:
    """Grade an AI verification challenge and allow or deny the pending action.

    Args:
        req: The session id and the customer's answers.

    Returns:
        The outcome, including whether the action was executed.
    """
    sess = db.pop_verify_session(req.session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Verification session not found or expired")

    acct = db.get_account(sess["account_id"])
    if acct is None:
        raise HTTPException(status_code=404, detail="Unknown account")

    action_req = ActionRequest(**sess["request"])
    result = ai_verifier.evaluate_answers(acct, sess["questions"], req.answers)

    executed, blocked, message = _apply(acct, action_req, allowed=result["verified"])
    verification = {
        "verified": result["verified"],
        "confidence": result["confidence"],
        "reason": result["reason"],
        "ai_powered": result["ai_powered"],
    }
    status_word = "verified" if result["verified"] else "denied"
    full_msg = (f"Identity verified by AI — {message}" if result["verified"]
                else f"AI verification failed — {message}")

    acct["last_action"] = action_req.action
    db.update_account(acct)
    _log_activity(acct, action_req, sess["city"], executed=executed, blocked=blocked,
                  message=full_msg, verdict=sess["verdict"], status=status_word,
                  verification=verification)

    return {
        "status": "done",
        "verified": result["verified"],
        "executed": executed,
        "blocked": blocked,
        "message": full_msg,
        "verification": verification,
        "verdict": sess["verdict"],
        "account": _account_public(acct),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9100)
