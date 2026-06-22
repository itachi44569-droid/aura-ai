"""
Safety layer — rate limiting + content filter.
Zero cost, runs entirely in memory + SQLite.

Rate limits (configurable via env vars):
  RATE_MESSAGES_PER_MIN = 20   (messages per user per minute)
  RATE_MESSAGES_PER_DAY = 200  (messages per user per day)

Content filter:
  - Keyword blocklist (fast, no API)
  - Prompt injection detection
  - Response-level toxicity check (optional, uses Groq on suspicion only)
"""
import time
import os
import re
from collections import defaultdict, deque

# ── Rate Limiter ──────────────────────────────────────────────────────────────

RATE_PER_MIN = int(os.getenv("RATE_MESSAGES_PER_MIN", "20"))
RATE_PER_DAY = int(os.getenv("RATE_MESSAGES_PER_DAY", "200"))


class RateLimiter:
    def __init__(self):
        self._minute_buckets: dict[str, deque] = defaultdict(deque)
        self._day_counts:     dict[str, dict]  = defaultdict(lambda: {"date": "", "count": 0})

    def check(self, user_id: str) -> tuple[bool, str]:
        now    = time.time()
        uid    = str(user_id)
        window = now - 60

        # Per-minute
        bucket = self._minute_buckets[uid]
        while bucket and bucket[0] < window:
            bucket.popleft()
        if len(bucket) >= RATE_PER_MIN:
            wait = int(60 - (now - bucket[0]))
            return False, f"Slow down! You're sending too many messages. Please wait {wait}s."
        bucket.append(now)

        # Per-day
        today = time.strftime("%Y-%m-%d")
        day   = self._day_counts[uid]
        if day["date"] != today:
            day["date"], day["count"] = today, 0
        day["count"] += 1
        if day["count"] > RATE_PER_DAY:
            return False, f"Daily message limit ({RATE_PER_DAY}) reached. Resets at midnight."

        return True, ""


# ── Content Filter ────────────────────────────────────────────────────────────

# Patterns that suggest prompt injection attempts
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?previous\s+instructions",
    r"forget\s+your\s+(system\s+)?prompt",
    r"you\s+are\s+now\s+(a\s+)?(?:dan|jailbreak|unrestricted)",
    r"act\s+as\s+(?:an?\s+)?(?:evil|uncensored|unrestricted)",
    r"disregard\s+your\s+training",
    r"developer\s+mode",
    r"override\s+(?:your\s+)?(?:safety|guidelines|instructions)",
    r"\[system\]|\<system\>",
]

_INJECTION_RE = re.compile("|".join(_INJECTION_PATTERNS), re.I)

# Topics that should trigger a warning (not a hard block)
_SENSITIVE_KEYWORDS = {
    "bomb", "explosive", "malware", "ransomware", "hack into",
    "steal password", "credit card", "ssn", "phishing",
}


class ContentFilter:
    def __init__(self):
        self._custom_blocklist: set[str] = set()

    def add_blocked_words(self, words: list[str]):
        self._custom_blocklist.update(w.lower() for w in words)

    def check_input(self, text: str) -> tuple[bool, str]:
        """Returns (is_ok, reason). is_ok=False means block the message."""
        lower = text.lower()

        # Injection detection
        if _INJECTION_RE.search(text):
            return False, "I detected a possible prompt injection attempt. I can't process that request."

        # Custom blocklist
        for word in self._custom_blocklist:
            if word in lower:
                return False, f"I'm not able to help with that topic."

        return True, ""

    def check_output(self, text: str) -> tuple[bool, str]:
        """Post-generation check. Returns (is_safe, warning_note)."""
        lower = text.lower()
        found = [kw for kw in _SENSITIVE_KEYWORDS if kw in lower]
        if found:
            return False, "Response may contain sensitive content — please review before sending."
        return True, ""


# ── Singleton instances (imported by brain.py) ────────────────────────────────
rate_limiter    = RateLimiter()
content_filter  = ContentFilter()
