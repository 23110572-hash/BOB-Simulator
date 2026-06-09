"""Neon PostgreSQL persistence for the Bank of Baroda core simulator.

The simulator keeps all of its state in a hosted Neon Postgres database instead
of in process memory, across three separate tables:

* ``bank_accounts``         — the ten controllable customer accounts.
* ``bank_activity``         — the live action / verdict log.
* ``bank_verify_sessions``  — pending AI identity-verification challenges.

Connections are drawn from a small thread-safe pool so the synchronous FastAPI
handlers can each grab their own connection safely. The DSN is read from
``DATABASE_URL`` (loaded from ``bank_simulator/.env`` by ``_env``).
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Dict, List, Optional

# psycopg2 is imported defensively: if the binary wheel fails to load on the
# hosting runtime, we surface a clean error from the DB calls instead of
# crashing the whole serverless function at import time.
try:
    import psycopg2
    from psycopg2.extras import Json, RealDictCursor
    from psycopg2.pool import ThreadedConnectionPool
    _PSYCOPG2_IMPORT_ERROR: Optional[Exception] = None
except Exception as exc:  # pragma: no cover - depends on runtime wheels
    psycopg2 = None  # type: ignore
    Json = None  # type: ignore
    RealDictCursor = None  # type: ignore
    ThreadedConnectionPool = None  # type: ignore
    _PSYCOPG2_IMPORT_ERROR = exc

from accounts import SEED_ACCOUNTS

logger = logging.getLogger("bob_sim.db")

_POOL: Optional["ThreadedConnectionPool"] = None
_INITIALIZED = False


def _dsn() -> str:
    """Return the Neon Postgres DSN from the environment."""
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        raise RuntimeError(
            "DATABASE_URL is not set. Add the Neon connection string as an "
            "environment variable in your hosting dashboard (Vercel) or in "
            "bank_simulator/.env for local development."
        )
    return dsn


def _pool() -> "ThreadedConnectionPool":
    """Return the lazily-created connection pool."""
    global _POOL
    if _PSYCOPG2_IMPORT_ERROR is not None:
        raise RuntimeError(
            f"psycopg2 is not available in this runtime: {_PSYCOPG2_IMPORT_ERROR}"
        )
    if _POOL is None:
        _POOL = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=_dsn())
        logger.info("Connected to Neon Postgres connection pool.")
    return _POOL


@contextmanager
def _conn():
    """Yield a pooled connection, committing on success and returning it after."""
    pool = _pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


# --------------------------------------------------------------------------- #
# Schema + seed
# --------------------------------------------------------------------------- #
_SCHEMA = """
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
);

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
);
CREATE INDEX IF NOT EXISTS idx_bank_activity_ts ON bank_activity (ts DESC);

CREATE TABLE IF NOT EXISTS bank_verify_sessions (
    session_id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    request    JSONB NOT NULL,
    questions  JSONB NOT NULL,
    verdict    JSONB,
    city       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

# Columns that make up the public/runtime account record.
_ACCOUNT_COLS = (
    "user_id", "name", "account_number", "ifsc", "balance", "home_city",
    "device_id", "device_os", "phone", "last_action", "last_verdict",
)


def init_db() -> None:
    """Create the tables if needed and seed the ten accounts once."""
    global _INITIALIZED
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM bank_accounts")
            count = cur.fetchone()[0]
        if count == 0:
            _seed_accounts(conn)
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


def _seed_accounts(conn) -> None:
    """Insert the seed accounts (idempotent via ON CONFLICT)."""
    with conn.cursor() as cur:
        for pos, a in enumerate(SEED_ACCOUNTS):
            cur.execute(
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
def get_accounts() -> Dict[str, dict]:
    """Return all accounts keyed by user_id, in seed order."""
    _ensure_initialized()
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, name, account_number, ifsc, balance, home_city, "
                "device_id, device_os, phone, last_action, last_verdict "
                "FROM bank_accounts ORDER BY position ASC"
            )
            rows = cur.fetchall()
    return {r["user_id"]: dict(r) for r in rows}


def get_account(user_id: str) -> Optional[dict]:
    """Return a single account record or None."""
    _ensure_initialized()
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT user_id, name, account_number, ifsc, balance, home_city, "
                "device_id, device_os, phone, last_action, last_verdict "
                "FROM bank_accounts WHERE user_id = %s",
                (user_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


def update_account(acct: dict) -> None:
    """Persist mutable account fields (balance, last action/verdict)."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE bank_accounts SET balance=%s, last_action=%s, "
                "last_verdict=%s, updated_at=now() WHERE user_id=%s",
                (acct["balance"], acct.get("last_action"),
                 Json(acct.get("last_verdict")) if acct.get("last_verdict") is not None else None,
                 acct["user_id"]),
            )


# --------------------------------------------------------------------------- #
# Activity log
# --------------------------------------------------------------------------- #
def insert_activity(entry: dict) -> None:
    """Append an entry to the activity log."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bank_activity
                    (id, ts, account, user_id, action, channel, city, amount,
                     executed, blocked, status, message, verdict, verification)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """,
                (entry["id"], entry["timestamp"], entry["account"], entry["user_id"],
                 entry["action"], entry["channel"], entry.get("city"), entry.get("amount"),
                 entry["executed"], entry["blocked"], entry["status"], entry.get("message"),
                 Json(entry.get("verdict")) if entry.get("verdict") is not None else None,
                 Json(entry.get("verification")) if entry.get("verification") is not None else None),
            )


def get_activity(limit: int = 40) -> List[dict]:
    """Return the most recent activity entries, newest first."""
    _ensure_initialized()
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "SELECT id, ts, account, user_id, action, channel, city, amount, "
                "executed, blocked, status, message, verdict, verification "
                "FROM bank_activity ORDER BY ts DESC LIMIT %s",
                (limit,),
            )
            rows = cur.fetchall()
    out = []
    for r in rows:
        d = dict(r)
        ts = d.pop("ts")
        d["timestamp"] = ts.isoformat() if ts else None
        out.append(d)
    return out


# --------------------------------------------------------------------------- #
# Verification sessions
# --------------------------------------------------------------------------- #
def save_verify_session(session_id: str, data: dict) -> None:
    """Store a pending AI verification challenge."""
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO bank_verify_sessions
                    (session_id, account_id, request, questions, verdict, city)
                VALUES (%s,%s,%s,%s,%s,%s)
                """,
                (session_id, data["account_id"], Json(data["request"]),
                 Json(data["questions"]), Json(data.get("verdict")), data.get("city")),
            )


def pop_verify_session(session_id: str) -> Optional[dict]:
    """Fetch and delete a verification session (single use)."""
    with _conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                "DELETE FROM bank_verify_sessions WHERE session_id = %s "
                "RETURNING account_id, request, questions, verdict, city",
                (session_id,),
            )
            row = cur.fetchone()
    return dict(row) if row else None


# --------------------------------------------------------------------------- #
# Reset / health
# --------------------------------------------------------------------------- #
def reset() -> None:
    """Restore seed balances, clear last verdicts, and wipe logs/sessions."""
    _ensure_initialized()
    with _conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bank_activity")
            cur.execute("DELETE FROM bank_verify_sessions")
            for a in SEED_ACCOUNTS:
                cur.execute(
                    "UPDATE bank_accounts SET balance=%s, last_action=NULL, "
                    "last_verdict=NULL, updated_at=now() WHERE user_id=%s",
                    (a["balance"], a["user_id"]),
                )
        # Make sure any accounts that were never seeded exist.
        _seed_accounts(conn)


def health() -> bool:
    """Return True if the database is reachable."""
    try:
        with _conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        return True
    except Exception as exc:  # pragma: no cover - demo resilience
        logger.warning("Database health check failed: %s", exc)
        return False
