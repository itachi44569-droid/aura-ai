"""
3-tier memory system:
  1. Short-term  — last N messages (sliding window, in-RAM)
  2. Working     — facts extracted from conversations (SQLite, per user)
  3. Long-term   — compressed summaries of old conversations (SQLite)
"""
import sqlite3
import json
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime

SHORT_TERM_WINDOW = 12   # messages kept in RAM per user
SUMMARIZE_AFTER   = 30   # messages before compressing to long-term

# ── Database setup ─────────────────────────────────────────────────────────────

def _init_db(path: str):
    with sqlite3.connect(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS messages (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT NOT NULL,
                role      TEXT NOT NULL,
                content   TEXT NOT NULL,
                ts        REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS user_facts (
                user_id   TEXT PRIMARY KEY,
                facts     TEXT NOT NULL DEFAULT '{}',
                updated   REAL NOT NULL DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id   TEXT NOT NULL,
                summary   TEXT NOT NULL,
                ts        REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id);
        """)

@contextmanager
def _db(path: str):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()

# ── Memory class ───────────────────────────────────────────────────────────────

class Memory:
    def __init__(self, db_path: str = "brain.db"):
        self.db_path   = db_path
        self._cache: dict[str, deque] = defaultdict(lambda: deque(maxlen=SHORT_TERM_WINDOW))
        _init_db(db_path)

    # ── Short-term ─────────────────────────────────────────────────────────────

    def get_history(self, user_id: str) -> list[dict]:
        """Return recent messages as [{role, content}, ...] for LLM context."""
        if user_id not in self._cache or len(self._cache[user_id]) == 0:
            self._load_from_db(user_id)
        return list(self._cache[user_id])

    def add_exchange(self, user_id: str, user_msg: str, ai_msg: str):
        """Persist one user/assistant exchange."""
        now = datetime.utcnow().timestamp()
        self._cache[user_id].append({"role": "user",      "content": user_msg})
        self._cache[user_id].append({"role": "assistant", "content": ai_msg})
        with _db(self.db_path) as conn:
            conn.executemany(
                "INSERT INTO messages(user_id, role, content, ts) VALUES(?,?,?,?)",
                [(user_id, "user", user_msg, now), (user_id, "assistant", ai_msg, now)],
            )
        self._maybe_summarize(user_id)

    def _load_from_db(self, user_id: str):
        with _db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?",
                (user_id, SHORT_TERM_WINDOW),
            ).fetchall()
        for row in reversed(rows):
            self._cache[user_id].append({"role": row["role"], "content": row["content"]})

    # ── Working memory (user facts) ───────────────────────────────────────────

    def get_user_facts(self, user_id: str) -> str:
        """Return extracted facts about this user as a readable string."""
        with _db(self.db_path) as conn:
            row = conn.execute(
                "SELECT facts FROM user_facts WHERE user_id=?", (user_id,)
            ).fetchone()
        if not row:
            return ""
        facts: dict = json.loads(row["facts"])
        if not facts:
            return ""
        return "\n".join(f"- {k}: {v}" for k, v in facts.items())

    def update_user_facts(self, user_id: str, new_facts: dict):
        """Merge new facts into the user's persistent profile."""
        with _db(self.db_path) as conn:
            row = conn.execute(
                "SELECT facts FROM user_facts WHERE user_id=?", (user_id,)
            ).fetchone()
            existing = json.loads(row["facts"]) if row else {}
            existing.update({k: v for k, v in new_facts.items() if v})
            conn.execute(
                "INSERT INTO user_facts(user_id, facts, updated) VALUES(?,?,?) "
                "ON CONFLICT(user_id) DO UPDATE SET facts=excluded.facts, updated=excluded.updated",
                (user_id, json.dumps(existing), datetime.utcnow().timestamp()),
            )

    # ── Long-term (compression) ───────────────────────────────────────────────

    def get_summaries(self, user_id: str) -> list[str]:
        with _db(self.db_path) as conn:
            rows = conn.execute(
                "SELECT summary FROM summaries WHERE user_id=? ORDER BY id DESC LIMIT 3",
                (user_id,),
            ).fetchall()
        return [r["summary"] for r in rows]

    def save_summary(self, user_id: str, summary: str):
        with _db(self.db_path) as conn:
            conn.execute(
                "INSERT INTO summaries(user_id, summary, ts) VALUES(?,?,?)",
                (user_id, summary, datetime.utcnow().timestamp()),
            )

    def _maybe_summarize(self, user_id: str):
        """Trigger compression when message count exceeds threshold."""
        with _db(self.db_path) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE user_id=?", (user_id,)
            ).fetchone()[0]
        if count > 0 and count % SUMMARIZE_AFTER == 0:
            return True   # Brain will handle actual summarization (needs LLM)
        return False

    # ── Stats ─────────────────────────────────────────────────────────────────

    def total_users(self) -> int:
        with _db(self.db_path) as conn:
            return conn.execute("SELECT COUNT(DISTINCT user_id) FROM messages").fetchone()[0]

    def total_messages(self) -> int:
        with _db(self.db_path) as conn:
            return conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
