"""Tiny .env loader for the Bank of Baroda simulator (no extra dependencies).

Loads ``bank_simulator/.env`` into ``os.environ`` (without overwriting values
already set in the real environment) so secrets like ``GEMINI_API_KEY`` are not
hard-coded. Imported for its side effect.
"""
from __future__ import annotations

import os

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


def load_env() -> None:
    """Parse the local .env file and populate os.environ (best effort)."""
    paths = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    ]
    for env_path in paths:
        if os.path.exists(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as fh:
                    for raw in fh:
                        line = raw.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        key, _, value = line.partition("=")
                        key, value = key.strip(), value.strip().strip('"').strip("'")
                        if key and key not in os.environ:
                            os.environ[key] = value
                break
            except OSError:
                pass


load_env()
