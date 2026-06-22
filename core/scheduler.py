"""
Proactive scheduler — APScheduler-based reminders and daily briefings.
No API needed. Persists jobs in SQLite so they survive restarts.

Supported reminder formats:
  '10m'            → in 10 minutes
  '2h'             → in 2 hours
  '9:00 AM'        → today at 9 AM
  'daily 9am'      → every day at 9 AM
  'every monday'   → every Monday at 9 AM
  'tomorrow 8pm'   → next day 8 PM
"""
from __future__ import annotations
import os
import re
import asyncio
from datetime import datetime, timedelta
from typing import Callable, Awaitable, Optional
import pytz

try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.date     import DateTrigger
    from apscheduler.triggers.cron     import CronTrigger
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    _HAS_APScheduler = True
except ImportError:
    _HAS_APScheduler = False

DEFAULT_TZ  = os.getenv("TIMEZONE", "Asia/Kolkata")
DB_PATH     = os.getenv("DB_PATH", "brain.db")

# Callback type: async fn(user_id, message) -> None
SendCallback = Callable[[str, str], Awaitable[None]]


def _parse_when(when_str: str, tz: str = DEFAULT_TZ) -> tuple[str, dict]:
    """
    Parse natural-language 'when' into (trigger_type, kwargs).
    trigger_type: 'date' | 'cron'
    """
    tz_obj = pytz.timezone(tz)
    now    = datetime.now(tz_obj)
    w      = when_str.strip().lower()

    # '10m' or '30min'
    m = re.match(r"(\d+)\s*m(?:in)?$", w)
    if m:
        run_at = now + timedelta(minutes=int(m.group(1)))
        return "date", {"run_date": run_at, "timezone": tz}

    # '2h' or '3hours'
    m = re.match(r"(\d+)\s*h(?:ours?)?$", w)
    if m:
        run_at = now + timedelta(hours=int(m.group(1)))
        return "date", {"run_date": run_at, "timezone": tz}

    # 'daily 9am' or 'every day at 9:30pm'
    m = re.search(r"daily\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", w)
    if m:
        hour, minute, ampm = m.group(1), m.group(2) or "0", m.group(3) or "am"
        h = int(hour); mn = int(minute)
        if ampm == "pm" and h != 12: h += 12
        if ampm == "am" and h == 12: h = 0
        return "cron", {"hour": h, "minute": mn, "timezone": tz}

    # 'tomorrow 8pm'
    m = re.search(r"tomorrow\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", w)
    if m:
        hour, minute, ampm = m.group(1), m.group(2) or "0", m.group(3) or "am"
        h = int(hour); mn = int(minute)
        if ampm == "pm" and h != 12: h += 12
        if ampm == "am" and h == 12: h = 0
        run_at = (now + timedelta(days=1)).replace(hour=h, minute=mn, second=0, microsecond=0)
        return "date", {"run_date": run_at, "timezone": tz}

    # 'every monday 9am'
    days = {"monday":0,"tuesday":1,"wednesday":2,"thursday":3,"friday":4,"saturday":5,"sunday":6}
    for day_name, day_num in days.items():
        if day_name in w:
            m2 = re.search(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", w)
            if m2:
                hour, minute, ampm = m2.group(1), m2.group(2) or "0", m2.group(3) or "am"
                h = int(hour); mn = int(minute)
                if ampm == "pm" and h != 12: h += 12
                if ampm == "am" and h == 12: h = 0
            else:
                h, mn = 9, 0
            return "cron", {"day_of_week": day_num, "hour": h, "minute": mn, "timezone": tz}

    # Fallback: specific time today e.g. '9:00 AM'
    m = re.match(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", w)
    if m:
        hour, minute, ampm = m.group(1), m.group(2) or "0", m.group(3) or "am"
        h = int(hour); mn = int(minute)
        if ampm == "pm" and h != 12: h += 12
        if ampm == "am" and h == 12: h = 0
        run_at = now.replace(hour=h, minute=mn, second=0, microsecond=0)
        if run_at <= now:
            run_at += timedelta(days=1)
        return "date", {"run_date": run_at, "timezone": tz}

    raise ValueError(f"Could not parse reminder time: '{when_str}'")


class Scheduler:
    def __init__(self, send_callback: Optional[SendCallback] = None):
        self._callback = send_callback
        self._scheduler: Optional[object] = None

    def set_callback(self, callback: SendCallback):
        self._callback = callback

    def start(self):
        if not _HAS_APScheduler:
            print("[Scheduler] APScheduler not installed — reminders disabled.")
            return
        jobstores = {
            "default": SQLAlchemyJobStore(url=f"sqlite:///{DB_PATH}")
        }
        self._scheduler = AsyncIOScheduler(jobstores=jobstores, timezone=DEFAULT_TZ)
        self._scheduler.start()
        print("[Scheduler] Started — reminders active.")

    def stop(self):
        if self._scheduler:
            self._scheduler.shutdown(wait=False)

    def add_reminder(self, user_id: str, message: str, when: str) -> dict:
        if not _HAS_APScheduler or not self._scheduler:
            return {"error": "Scheduler not available (install apscheduler)"}
        try:
            trigger_type, kwargs = _parse_when(when)
            job_id = f"reminder_{user_id}_{hash(message + when) & 0xFFFFFF}"

            if trigger_type == "date":
                self._scheduler.add_job(
                    self._fire, "date", id=job_id, replace_existing=True,
                    args=[user_id, message],
                    **kwargs,
                )
                run_at = kwargs["run_date"]
                return {"status":"scheduled","when":str(run_at),"type":"one-time","job_id":job_id}
            else:
                self._scheduler.add_job(
                    self._fire, "cron", id=job_id, replace_existing=True,
                    args=[user_id, message],
                    **kwargs,
                )
                return {"status":"scheduled","when":when,"type":"recurring","job_id":job_id}
        except ValueError as e:
            return {"error": str(e)}

    def list_reminders(self, user_id: str) -> list[dict]:
        if not _HAS_APScheduler or not self._scheduler:
            return []
        prefix = f"reminder_{user_id}_"
        return [
            {"job_id": j.id, "next_run": str(j.next_run_time), "args": j.args}
            for j in self._scheduler.get_jobs()
            if j.id.startswith(prefix)
        ]

    def cancel_reminder(self, job_id: str) -> bool:
        if not _HAS_APScheduler or not self._scheduler:
            return False
        try:
            self._scheduler.remove_job(job_id)
            return True
        except Exception:
            return False

    def add_daily_briefing(self, user_id: str, hour: int = 9, minute: int = 0):
        """Schedule a daily briefing message."""
        if not _HAS_APScheduler or not self._scheduler:
            return
        self._scheduler.add_job(
            self._fire, "cron",
            id=f"briefing_{user_id}",
            replace_existing=True,
            hour=hour, minute=minute,
            timezone=DEFAULT_TZ,
            args=[user_id, "daily_briefing"],
        )

    async def _fire(self, user_id: str, message: str):
        if self._callback:
            try:
                if message == "daily_briefing":
                    from datetime import date
                    message = f"Good morning! Your daily briefing for {date.today().strftime('%B %d, %Y')}.\nType /stats to see your usage or ask me anything!"
                await self._callback(user_id, f"Reminder: {message}")
            except Exception as e:
                print(f"[Scheduler] Fire error for {user_id}: {e}")


# Singleton
scheduler = Scheduler()
