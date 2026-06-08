from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger("bob_sim.trust_client")

TRUSTIQ_URL = os.environ.get("TRUSTIQ_URL", "http://127.0.0.1:8000").rstrip("/")
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
    return resp.json()


def health() -> Optional[dict]:
    """Return TrustIQ's root health payload, or None if unreachable."""
    try:
        with httpx.Client(timeout=4.0) as client:
            resp = client.get(f"{TRUSTIQ_URL}/")
            if resp.status_code < 400:
                return resp.json()
    except httpx.RequestError:
        return None
    return None
