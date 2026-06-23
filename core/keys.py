"""
API key management — stored in SQLite.
Free tier: 20 msgs/day (handled by rate limiter).
Paid tier: unlimited (key bypasses daily cap).
"""
import sqlite3
import uuid
import os
from datetime import datetime, date

DB = os.getenv("DB_PATH", "brain.db")


def _conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_keys_table():
    with _conn() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key        TEXT PRIMARY KEY,
                label      TEXT NOT NULL,
                tier       TEXT NOT NULL DEFAULT 'paid',
                daily_limit INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                active     INTEGER NOT NULL DEFAULT 1
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS key_usage (
                key   TEXT NOT NULL,
                day   TEXT NOT NULL,
                count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (key, day)
            )
        """)
        c.commit()


def generate_key(label: str, tier: str = "paid", daily_limit: int = 0) -> str:
    """Create a new API key. daily_limit=0 means unlimited."""
    init_keys_table()
    key = "nb-" + uuid.uuid4().hex[:24]
    with _conn() as c:
        c.execute(
            "INSERT INTO api_keys (key, label, tier, daily_limit, created_at) VALUES (?,?,?,?,?)",
            (key, label, tier, daily_limit, datetime.utcnow().isoformat())
        )
        c.commit()
    return key


def validate_key(key: str) -> dict | None:
    """Return key info if valid and active, else None."""
    if not key:
        return None
    init_keys_table()
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM api_keys WHERE key=? AND active=1", (key,)
        ).fetchone()
    return dict(row) if row else None


def check_key_limit(key: str, daily_limit: int) -> tuple[bool, str]:
    """Check if key has remaining quota for today. Returns (ok, reason)."""
    if daily_limit == 0:
        return True, ""
    today = date.today().isoformat()
    with _conn() as c:
        row = c.execute(
            "SELECT count FROM key_usage WHERE key=? AND day=?", (key, today)
        ).fetchone()
    used = row["count"] if row else 0
    if used >= daily_limit:
        return False, f"Daily limit of {daily_limit} messages reached for today."
    return True, ""


def increment_key_usage(key: str):
    today = date.today().isoformat()
    with _conn() as c:
        c.execute(
            "INSERT INTO key_usage (key, day, count) VALUES (?,?,1) "
            "ON CONFLICT(key,day) DO UPDATE SET count=count+1",
            (key, today)
        )
        c.commit()


def list_keys() -> list[dict]:
    init_keys_table()
    today = date.today().isoformat()
    with _conn() as c:
        rows = c.execute("SELECT * FROM api_keys ORDER BY created_at DESC").fetchall()
        result = []
        for r in rows:
            usage = c.execute(
                "SELECT count FROM key_usage WHERE key=? AND day=?", (r["key"], today)
            ).fetchone()
            d = dict(r)
            d["used_today"] = usage["count"] if usage else 0
            result.append(d)
    return result


def revoke_key(key: str):
    with _conn() as c:
        c.execute("UPDATE api_keys SET active=0 WHERE key=?", (key,))
        c.commit()
