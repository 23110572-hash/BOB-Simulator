from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("bob_sim.trust_client")

# The live TrustIQ backend (FastAPI on Render). This is the only place that
# serves /api/trust/evaluate.
_DEFAULT_TRUSTIQ_URL = "https://trustiq-67h0.onrender.com"


def _resolve_trustiq_url() -> str:
    """Resolve the TrustIQ base URL, ignoring obviously-wrong values.

    On Vercel a stray/old ``TRUSTIQ_URL`` env var can point at a Vercel
    deployment (or the simulator's own domain). Those hosts don't serve
    ``/api/trust/evaluate`` and answer with Vercel's NOT_FOUND/404 page, which
    surfaces to the operator as ``TrustIQ error 404``. Guard against that by
    falling back to the known-good Render backend whenever the configured URL
    is empty or clearly not the TrustIQ backend.
    """
    raw = (os.environ.get("TRUSTIQ_URL") or "").strip().rstrip("/")
    if not raw:
        return _DEFAULT_TRUSTIQ_URL
    # A vercel.app host is never the TrustIQ API — it's the simulator/front end.
    if "vercel.app" in raw.lower():
        return _DEFAULT_TRUSTIQ_URL
    return raw


TRUSTIQ_URL = _resolve_trustiq_url()
TRUSTIQ_API_KEY = os.environ.get("TRUSTIQ_API_KEY", "bob-trustiq-live-key-2026")

_EVALUATE_PATH = "/api/trust/evaluate"


class TrustIQError(RuntimeError):
    """Raised when TrustIQ cannot be reached or rejects the request."""


def evaluate(payload: dict) -> dict:
    """Send one banking event to TrustIQ and return its verdict.

    Args:
        payload: A TrustIQ ``TrustEvaluationRequest`` body.

    Returns:
        The parsed ``TrustEvaluation`` response as a dict.

    Raises:
        TrustIQError: If TrustIQ is unreachable or returns an error.
    """
    url = f"{TRUSTIQ_URL}{_EVALUATE_PATH}"
    headers = {"X-API-Key": TRUSTIQ_API_KEY, "Content-Type": "application/json"}
    try:
        with httpx.Client(timeout=8.0) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.RequestError as exc:
        logger.warning("TrustIQ unreachable: %s", exc)
        raise TrustIQError(
            f"Could not reach TrustIQ at {TRUSTIQ_URL}. Is the backend running?"
        ) from exc

    if resp.status_code == 401:
        raise TrustIQError(
            "TrustIQ rejected the API key (401). Check TRUSTIQ_API_KEY matches "
            "the backend's TRUSTIQ_API_KEY."
        )
    if resp.status_code >= 400:
        raise TrustIQError(f"TrustIQ error {resp.status_code}: {resp.text[:200]}")
    try:
        return resp.json()
    except Exception as exc:
        raise TrustIQError(
            f"TrustIQ returned a non-JSON response (the backend may be "
            f"starting up). Please retry in a moment."
        ) from exc


def health() -> Optional[dict]:
    """Return TrustIQ's root health payload, or None if unreachable.

    Never raises: any transport error, bad status, or non-JSON/slow response
    from a cold-starting backend simply yields None so the simulator keeps
    working regardless of TrustIQ's availability.
    """
    try:
        with httpx.Client(timeout=2.5) as client:
            resp = client.get(f"{TRUSTIQ_URL}/")
            if resp.status_code < 400:
                return resp.json()
    except Exception:
        return None
    return None
