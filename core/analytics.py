"""
Analytics — SQLite-based usage tracking. Zero cost, no external service.

Tracks:
  - Messages per user / channel / day
  - Tool usage counts
  - Average response latency
  - Top topics (via DuckDuckGo query logging)
  - Error rates
"""
import sqlite3
import time
import json
from collections import defaultdict
from datetime import datetime, date
from contextlib import contextmanager
from threading import Lock


class Analytics:
    def __init__(self, db_path: str = "brain.db"):
        self.db_path = db_path
        self._lock   = Lock()
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS analytics_events (
                    id        INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts        REAL    NOT NULL,
                    event     TEXT    NOT NULL,
                    user_id   TEXT,
                    channel   TEXT,
                    data      TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_ae_ts      ON analytics_events(ts);
                CREATE INDEX IF NOT EXISTS idx_ae_event   ON analytics_events(event);
                CREATE INDEX IF NOT EXISTS idx_ae_user    ON analytics_events(user_id);

                CREATE TABLE IF NOT EXISTS analytics_daily (
                    day          TEXT NOT NULL,
                    metric       TEXT NOT NULL,
                    value        INTEGER DEFAULT 0,
                    PRIMARY KEY (day, metric)
                );
            """)

    def _log(self, event: str, user_id: str = None, channel: str = None, **kwargs):
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    "INSERT INTO analytics_events(ts,event,user_id,channel,data) VALUES(?,?,?,?,?)",
                    (time.time(), event, str(user_id) if user_id else None,
                     channel, json.dumps(kwargs) if kwargs else None)
                )
                today = date.today().isoformat()
                conn.execute(
                    "INSERT INTO analytics_daily(day,metric,value) VALUES(?,?,1) "
                    "ON CONFLICT(day,metric) DO UPDATE SET value=value+1",
                    (today, event)
                )

    # ── Public logging API ────────────────────────────────────────────────────

    def log_message(self, user_id, channel: str = "telegram"):
        self._log("message", user_id=user_id, channel=channel)

    def log_tool_call(self, tool_name: str, user_id=None, success: bool = True):
        self._log("tool_call", user_id=user_id, tool=tool_name, success=int(success))

    def log_response(self, user_id, latency_ms: float, tokens: int = 0, channel: str = "telegram"):
        self._log("response", user_id=user_id, channel=channel,
                  latency_ms=round(latency_ms), tokens=tokens)

    def log_error(self, error_type: str, user_id=None, detail: str = ""):
        self._log("error", user_id=user_id, error_type=error_type, detail=detail[:200])

    def log_image(self, user_id, channel: str = "telegram"):
        self._log("image_analyzed", user_id=user_id, channel=channel)

    def log_voice(self, user_id, duration_s: float = 0, channel: str = "telegram"):
        self._log("voice_transcribed", user_id=user_id, channel=channel, duration_s=round(duration_s))

    # ── Stats API ─────────────────────────────────────────────────────────────

    def get_summary(self, days: int = 7) -> dict:
        since = time.time() - days * 86400
        with self._conn() as conn:
            total_msg   = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event='message' AND ts>?", (since,)).fetchone()[0]
            total_users = conn.execute("SELECT COUNT(DISTINCT user_id) FROM analytics_events WHERE event='message' AND ts>?", (since,)).fetchone()[0]
            tool_rows   = conn.execute(
                "SELECT json_extract(data,'$.tool') as tool, COUNT(*) as cnt "
                "FROM analytics_events WHERE event='tool_call' AND ts>? "
                "GROUP BY tool ORDER BY cnt DESC LIMIT 5", (since,)
            ).fetchall()
            avg_lat = conn.execute(
                "SELECT AVG(CAST(json_extract(data,'$.latency_ms') AS REAL)) "
                "FROM analytics_events WHERE event='response' AND ts>?", (since,)
            ).fetchone()[0]
            errors = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event='error' AND ts>?", (since,)).fetchone()[0]
            images = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event='image_analyzed' AND ts>?", (since,)).fetchone()[0]
            voices = conn.execute("SELECT COUNT(*) FROM analytics_events WHERE event='voice_transcribed' AND ts>?", (since,)).fetchone()[0]

        return {
            "period_days":    days,
            "total_messages": total_msg,
            "unique_users":   total_users,
            "avg_latency_ms": round(avg_lat or 0),
            "errors":         errors,
            "images_analyzed":images,
            "voice_notes":    voices,
            "top_tools":      [{"tool": r["tool"], "count": r["cnt"]} for r in tool_rows],
        }

    def get_daily_counts(self, metric: str = "message", days: int = 7) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT day, value FROM analytics_daily WHERE metric=? "
                "ORDER BY day DESC LIMIT ?", (metric, days)
            ).fetchall()
        return [{"day": r["day"], "count": r["value"]} for r in rows]
