"""Neon PostgreSQL persistence for the Bank of Baroda core simulator.

The simulator keeps all of its state in a hosted Neon Postgres database instead
of in process memory, across three separate tables:

* ``bank_accounts``         — the ten controllable customer accounts.
* ``bank_activity``         — the live action / verdict log.
* ``bank_verify_sessions``  — pending AI identity-verification challenges.

The driver is **pg8000**, a pure-Python Postgres client. Unlike psycopg2's
binary wheel, it loads and runs reliably on serverless runtimes (Vercel /
AWS Lambda), where the native libpq in ``psycopg2-binary`` can crash the
function process. The connection string is read from ``DATABASE_URL``.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
from typing import Any, Dict, List, Optional, Sequence

# pg8000 is imported defensively so a runtime issue surfaces as a clean error
# from the DB calls rather than crashing the whole serverless function.
try:
    import pg8000.dbapi as pg
    _DRIVER_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - depends on runtime wheels
    pg = None  # type: ignore
    _DRIVER_IMPORT_ERROR = exc

from accounts import SEED_ACCOUNTS

logger = logging.getLogger("bob_sim.db")

_CONN: Any = None
_INITIALIZED = False


# --------------------------------------------------------------------------- #
# Connection handling
# --------------------------------------------------------------------------- #
def _connect_params() -> dict:
    """Parse ``DATABASE_URL`` into pg8000 connection keyword arguments."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set. Add the Neon connection string as an "
            "environment variable in your hosting dashboard (Vercel) or in "
            "bank_simulator/.env for local development."
        )
    from urllib.parse import urlsplit, unquote

    parts = urlsplit(dsn)
    return {
        "user": unquote(parts.username) if parts.username else "",
        "password": unquote(parts.password) if parts.password else None,
        "host": parts.hostname or "localhost",
        "port": parts.port or 5432,
        "database": (parts.path or "/").lstrip("/") or "neondb",
    }


def _new_connection():
    """Open a fresh TLS connection to Neon."""
    if _DRIVER_IMPORT_ERROR is not None:
        raise RuntimeError(f"pg8000 is not available in this runtime: {_DRIVER_IMPORT_ERROR}")
    params = _connect_params()
    # Neon mandates TLS and uses SNI; a default verified context works with
    # Neon's publicly-trusted certificate.
    ssl_ctx = ssl.create_default_context()
    conn = pg.connect(ssl_context=ssl_ctx, timeout=10, **params)
    conn.autocommit = False
    logger.info("Connected to Neon Postgres via pg8000.")
    return conn


def _get_conn():
    """Return a cached connection, opening one on first use."""
    global _CONN
    if _CONN is None:
        _CONN = _new_connection()
    return _CONN


def _reset_conn() -> None:
    """Drop the cached connection so the next call reconnects."""
    global _CONN
    if _CONN is not None:
        try:
            _CONN.close()
        except Exception:
            pass
    _CONN = None


def _execute(sql: str, params: Sequence[Any] = (), *, fetch: bool = False,
             commit: bool = True, _retry: bool = True) -> Optional[List[dict]]:
    """Run a statement, returning rows as dicts when ``fetch`` is True.

    Transparently reconnects and retries once if the cached connection has
    gone stale (a common situation on serverless where instances are paused).
    """
    conn = _get_conn()
    try:
        cur = conn.cursor()
        cur.execute(sql, tuple(params))
        result: Optional[List[dict]] = None
        if fetch:
            cols = [c[0] for c in cur.description] if cur.description else []
            result = [dict(zip(cols, row)) for row in cur.fetchall()]
        cur.close()
        if commit:
            conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        if _retry:
            # The connection may have been closed by the server; reconnect once.
            _reset_conn()
            return _execute(sql, params, fetch=fetch, commit=commit, _retry=False)
        raise


def _jsonb(value: Any) -> Optional[str]:
    """Serialise a value for a JSONB column (or None)."""
    return None if value is None else json.dumps(value)


# --------------------------------------------------------------------------- #
# Schema + seed
# --------------------------------------------------------------------------- #
_SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS bank_accounts (
        user_id        TEXT PRIMARY KEY,
        position       INTEGER NOT NULL DEFAULT 0,
        name           TEXT NOT NULL,
        account_number TEXT NOT NULL,
        ifsc           TEXT NOT NULL,
        balance        DOUBLE PRECISION NOT NULL,
        home_city      TEXT NOT NULL,
        device_id      TEXT NOT NULL,
        device_os      TEXT NOT NULL,
        phone          TEXT NOT NULL,
        last_action    TEXT,
        last_verdict   JSONB,
        created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS bank_activity (
        id           TEXT PRIMARY KEY,
        ts           TIMESTAMPTZ NOT NULL,
        account      TEXT NOT NULL,
        user_id      TEXT NOT NULL,
        action       TEXT NOT NULL,
        channel      TEXT NOT NULL,
        city         TEXT,
        amount       DOUBLE PRECISION,
        executed     BOOLEAN NOT NULL,
        blocked      BOOLEAN NOT NULL,
        status       TEXT NOT NULL,
        message      TEXT,
        verdict      JSONB,
        verification JSONB
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_bank_activity_ts ON bank_activity (ts DESC)",
    """
    CREATE TABLE IF NOT EXISTS bank_verify_sessions (
        session_id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL,
        request    JSONB NOT NULL,
        questions  JSONB NOT NULL,
        verdict    JSONB,
        city       TEXT,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]


def init_db() -> None:
    """Create the tables if needed and seed the ten accounts once."""
    global _INITIALIZED
    for stmt in _SCHEMA_STATEMENTS:
        _execute(stmt)
    rows = _execute("SELECT COUNT(*) AS c FROM bank_accounts", fetch=True)
    if rows and rows[0]["c"] == 0:
        _seed_accounts()
        logger.info("Seeded %d bank accounts into Neon Postgres.", len(SEED_ACCOUNTS))
    _INITIALIZED = True


def _ensure_initialized() -> None:
    """Lazily create/seed the schema once per process.

    Vercel's serverless runtime does not always run FastAPI startup events, so
    every public entry point calls this to guarantee the tables exist before
    the first query. The work only happens once per cold start.
    """
    if _INITIALIZED:
        return
    init_db()


def _seed_accounts() -> None:
    """Insert the seed accounts (idempotent via ON CONFLICT)."""
    for pos, a in enumerate(SEED_ACCOUNTS):
        _execute(
            """
            INSERT INTO bank_accounts
                (user_id, position, name, account_number, ifsc, balance,
                 home_city, device_id, device_os, phone, last_action, last_verdict)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NULL)
            ON CONFLICT (user_id) DO NOTHING
            """,
            (a["user_id"], pos, a["name"], a["account_number"], a["ifsc"],
             a["balance"], a["home_city"], a["device_id"], a["device_os"],
             a["phone"]),
        )


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
_ACCOUNT_SELECT = (
    "SELECT user_id, name, account_number, ifsc, balance, home_city, "
    "device_id, device_os, phone, last_action, last_verdict FROM bank_accounts"
)


def get_accounts() -> Dict[str, dict]:
    """Return all accounts keyed by user_id, in seed order."""
    _ensure_initialized()
    rows = _execute(_ACCOUNT_SELECT + " ORDER BY position ASC", fetch=True) or []
    return {r["user_id"]: r for r in rows}


def get_account(user_id: str) -> Optional[dict]:
    """Return a single account record or None."""
    _ensure_initialized()
    rows = _execute(_ACCOUNT_SELECT + " WHERE user_id = %s", (user_id,), fetch=True) or []
    return rows[0] if rows else None


def update_account(acct: dict) -> None:
    """Persist mutable account fields (balance, last action/verdict)."""
    _execute(
        "UPDATE bank_accounts SET balance=%s, last_action=%s, "
        "last_verdict=%s::jsonb, updated_at=now() WHERE user_id=%s",
        (acct["balance"], acct.get("last_action"),
         _jsonb(acct.get("last_verdict")), acct["user_id"]),
    )


# --------------------------------------------------------------------------- #
# Activity log
# --------------------------------------------------------------------------- #
def insert_activity(entry: dict) -> None:
    """Append an entry to the activity log."""
    _execute(
        """
        INSERT INTO bank_activity
            (id, ts, account, user_id, action, channel, city, amount,
             executed, blocked, status, message, verdict, verification)
        VALUES (%s,%s::timestamptz,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
        """,
        (entry["id"], entry["timestamp"], entry["account"], entry["user_id"],
         entry["action"], entry["channel"], entry.get("city"), entry.get("amount"),
         entry["executed"], entry["blocked"], entry["status"], entry.get("message"),
         _jsonb(entry.get("verdict")), _jsonb(entry.get("verification"))),
    )


def get_activity(limit: int = 40) -> List[dict]:
    """Return the most recent activity entries, newest first."""
    _ensure_initialized()
    rows = _execute(
        "SELECT id, ts, account, user_id, action, channel, city, amount, "
        "executed, blocked, status, message, verdict, verification "
        "FROM bank_activity ORDER BY ts DESC LIMIT %s",
        (limit,), fetch=True,
    ) or []
    for d in rows:
        ts = d.pop("ts", None)
        d["timestamp"] = ts.isoformat() if hasattr(ts, "isoformat") else ts
    return rows


# --------------------------------------------------------------------------- #
# Verification sessions
# --------------------------------------------------------------------------- #
def save_verify_session(session_id: str, data: dict) -> None:
    """Store a pending AI verification challenge."""
    _execute(
        """
        INSERT INTO bank_verify_sessions
            (session_id, account_id, request, questions, verdict, city)
        VALUES (%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)
        """,
        (session_id, data["account_id"], _jsonb(data["request"]),
         _jsonb(data["questions"]), _jsonb(data.get("verdict")), data.get("city")),
    )


def pop_verify_session(session_id: str) -> Optional[dict]:
    """Fetch and delete a verification session (single use)."""
    rows = _execute(
        "DELETE FROM bank_verify_sessions WHERE session_id = %s "
        "RETURNING account_id, request, questions, verdict, city",
        (session_id,), fetch=True,
    ) or []
    return rows[0] if rows else None


# --------------------------------------------------------------------------- #
# Reset / health
# --------------------------------------------------------------------------- #
def reset() -> None:
    """Restore seed balances, clear last verdicts, and wipe logs/sessions."""
    _ensure_initialized()
    _execute("DELETE FROM bank_activity")
    _execute("DELETE FROM bank_verify_sessions")
    for a in SEED_ACCOUNTS:
        _execute(
            "UPDATE bank_accounts SET balance=%s, last_action=NULL, "
            "last_verdict=NULL, updated_at=now() WHERE user_id=%s",
            (a["balance"], a["user_id"]),
        )
    # Make sure any accounts that were never seeded exist.
    _seed_accounts()


def health() -> bool:
    """Return True if the database is reachable."""
    try:
        _execute("SELECT 1", fetch=True)
        return True
    except Exception as exc:  # pragma: no cover - demo resilience
        logger.warning("Database health check failed: %s", exc)
        return False
