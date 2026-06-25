"""
Authentication (users, sessions), application settings, and conversation history.
"""
import sqlite3
import hashlib
import secrets
import json
import os
from datetime import datetime

DB = os.getenv("DB_PATH", "brain.db")

DEFAULT_SETTINGS = {
    "bot_name":      "Aura AI",
    "logo_emoji":    "✦",
    "primary_color": "#4F46E5",
    "greeting": (
        "Hey! I'm Aura. I can search the web, check live prices, "
        "summarize YouTube videos, get news, do math, and a lot more. What do you need?"
    ),
    "persona_addon": "",
    "temperature":   "0.75",
    "require_login": "false",
}


# ── Init ──────────────────────────────────────────────────────────────────────

def init_auth():
    with sqlite3.connect(DB) as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    UNIQUE NOT NULL,
                password_hash TEXT    NOT NULL,
                name          TEXT    NOT NULL DEFAULT '',
                created_at    TEXT    NOT NULL,
                is_active     INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                created_at TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversations (
                id         TEXT    PRIMARY KEY,
                user_id    INTEGER NOT NULL,
                title      TEXT    NOT NULL DEFAULT 'Untitled',
                messages   TEXT    NOT NULL DEFAULT '[]',
                created_at TEXT    NOT NULL,
                updated_at TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_conv_user ON conversations(user_id, updated_at);
            CREATE TABLE IF NOT EXISTS shared_conversations (
                token      TEXT    PRIMARY KEY,
                title      TEXT    NOT NULL DEFAULT 'Shared Chat',
                messages   TEXT    NOT NULL DEFAULT '[]',
                created_at TEXT    NOT NULL
            );
        """)
        for k, v in DEFAULT_SETTINGS.items():
            c.execute(
                "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?,?)", (k, v)
            )
        c.commit()


# ── Passwords ─────────────────────────────────────────────────────────────────

def _hash_pw(password: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{h}"


def _verify_pw(password: str, stored: str) -> bool:
    try:
        salt, h = stored.split(":", 1)
        return hashlib.sha256(f"{salt}:{password}".encode()).hexdigest() == h
    except Exception:
        return False


# ── Users ─────────────────────────────────────────────────────────────────────

def register_user(email: str, password: str, name: str = "") -> dict:
    try:
        with sqlite3.connect(DB) as c:
            c.execute(
                "INSERT INTO users (email, password_hash, name, created_at) VALUES (?,?,?,?)",
                (email.lower().strip(), _hash_pw(password), name, datetime.utcnow().isoformat()),
            )
            c.commit()
        return {"ok": True}
    except sqlite3.IntegrityError:
        return {"ok": False, "error": "Email already registered"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def login_user(email: str, password: str) -> dict:
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            "SELECT * FROM users WHERE email=? AND is_active=1",
            (email.lower().strip(),),
        ).fetchone()
    if not row or not _verify_pw(password, row["password_hash"]):
        return {"ok": False, "error": "Invalid email or password"}
    token = secrets.token_hex(32)
    with sqlite3.connect(DB) as c:
        c.execute(
            "INSERT INTO sessions (token, user_id, created_at) VALUES (?,?,?)",
            (token, row["id"], datetime.utcnow().isoformat()),
        )
        c.commit()
    return {"ok": True, "token": token, "name": row["name"], "email": row["email"]}


def validate_token(token: str) -> dict | None:
    if not token:
        return None
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        row = c.execute("""
            SELECT u.id, u.email, u.name
            FROM sessions s JOIN users u ON u.id = s.user_id
            WHERE s.token=? AND u.is_active=1
        """, (token,)).fetchone()
    return dict(row) if row else None


def delete_session(token: str):
    with sqlite3.connect(DB) as c:
        c.execute("DELETE FROM sessions WHERE token=?", (token,))
        c.commit()


def list_users() -> list:
    with sqlite3.connect(DB) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT id, email, name, created_at, is_active FROM users ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def deactivate_user(user_id: int):
    with sqlite3.connect(DB) as c:
        c.execute("UPDATE users SET is_active=0 WHERE id=?", (user_id,))
        c.execute("DELETE FROM sessions WHERE user_id=?", (user_id,))
        c.commit()


def activate_user(user_id: int):
    with sqlite3.connect(DB) as c:
        c.execute("UPDATE users SET is_active=1 WHERE id=?", (user_id,))
        c.commit()


# ── Settings ──────────────────────────────────────────────────────────────────

def get_settings() -> dict:
    try:
        with sqlite3.connect(DB) as c:
            rows = c.execute("SELECT key, value FROM app_settings").fetchall()
        merged = dict(DEFAULT_SETTINGS)
        merged.update({r[0]: r[1] for r in rows})
        return merged
    except Exception:
        return dict(DEFAULT_SETTINGS)


def update_settings(updates: dict):
    with sqlite3.connect(DB) as c:
        for k, v in updates.items():
            c.execute(
                "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?,?)", (k, str(v))
            )
        c.commit()


# ── Conversation history ───────────────────────────────────────────────────────

def save_conversation(user_id: int, conv_id: str, title: str, messages: list) -> dict:
    now = datetime.utcnow().isoformat()
    msgs_json = json.dumps(messages, ensure_ascii=False)
    try:
        with sqlite3.connect(DB) as c:
            c.execute("""
                INSERT INTO conversations (id, user_id, title, messages, created_at, updated_at)
                VALUES (?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    title=excluded.title,
                    messages=excluded.messages,
                    updated_at=excluded.updated_at
            """, (conv_id, user_id, title, msgs_json, now, now))
            c.commit()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_conversations(user_id: int) -> list:
    try:
        with sqlite3.connect(DB) as c:
            c.row_factory = sqlite3.Row
            rows = c.execute(
                "SELECT id, title, created_at, updated_at FROM conversations "
                "WHERE user_id=? ORDER BY updated_at DESC LIMIT 50",
                (user_id,)
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_conversation(conv_id: str, user_id: int) -> dict | None:
    try:
        with sqlite3.connect(DB) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT * FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user_id)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["messages"] = json.loads(d["messages"])
        return d
    except Exception:
        return None


def share_conversation(conv_id: str, user_id: int) -> dict:
    conv = get_conversation(conv_id, user_id)
    if not conv:
        return {"ok": False, "error": "Conversation not found"}
    token = secrets.token_urlsafe(16)
    try:
        with sqlite3.connect(DB) as c:
            c.execute(
                "INSERT INTO shared_conversations (token, title, messages, created_at) VALUES (?,?,?,?)",
                (token, conv["title"], json.dumps(conv["messages"], ensure_ascii=False), datetime.utcnow().isoformat()),
            )
            c.commit()
        return {"ok": True, "token": token}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_shared_conversation(token: str) -> dict | None:
    try:
        with sqlite3.connect(DB) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                "SELECT * FROM shared_conversations WHERE token=?", (token,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["messages"] = json.loads(d["messages"])
        return d
    except Exception:
        return None


def delete_conversation(conv_id: str, user_id: int):
    try:
        with sqlite3.connect(DB) as c:
            c.execute(
                "DELETE FROM conversations WHERE id=? AND user_id=?",
                (conv_id, user_id)
            )
            c.commit()
    except Exception:
        pass
