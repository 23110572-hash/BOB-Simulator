"""Best-effort .env loader for local development.

On Vercel (and any other hosting platform) environment variables are injected
directly into ``os.environ``, so this module is a no-op there. It only loads a
local ``.env`` file when running the simulator on a developer machine.

It looks for a ``.env`` first next to this file (``api/.env``) and then one
level up (``bank_simulator/.env``), so it works no matter where the process is
started from. Existing environment variables are never overwritten.
"""
from __future__ import annotations

import os

_CANDIDATE_PATHS = [
    os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"),
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"),
]


def load_env() -> None:
    """Parse the first local .env file found and populate os.environ."""
    for path in _CANDIDATE_PATHS:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                for raw in fh:
                    line = raw.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, value = line.partition("=")
                    key, value = key.strip(), value.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = value
        except OSError:
            pass
        # Stop after the first file that exists.
        return


load_env()
