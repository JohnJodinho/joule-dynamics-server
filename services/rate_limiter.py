"""
services/rate_limiter.py
────────────────────────
In-memory session rate limiter enforcing minute and daily rate limits.
"""

import time
from collections import defaultdict
from fastapi import HTTPException


def _is_idle_session(times: list, cutoff: float) -> bool:
    """Returns True if the request timestamps indicate idle session beyond cutoff."""
    return not times or max(times) < cutoff


class SessionRateLimiter:
    """Tracks per-session requests against RPM and daily quotas."""

    def __init__(self, requests_per_minute: int = 10, max_daily: int = 50):
        self.rpm = requests_per_minute
        self.max_daily = max_daily
        self.requests = defaultdict(list)
        self.daily_counts = defaultdict(lambda: {"count": 0, "reset_at": 0})

    def evict_stale(self, max_idle_seconds: int = 90_000) -> None:
        """Evicts sessions idle for longer than max_idle_seconds to prevent memory leaks."""
        cutoff = time.time() - max_idle_seconds
        stale_req = [sid for sid, times in self.requests.items() if _is_idle_session(times, cutoff)]
        stale_daily = [sid for sid, data in self.daily_counts.items() if data["reset_at"] < cutoff]
        for sid in stale_req:
            del self.requests[sid]
        for sid in stale_daily:
            del self.daily_counts[sid]

    def _check_daily_limit(self, client_id: str, now: float):
        """Verifies and updates the daily 24-hour request quota."""
        daily = self.daily_counts[client_id]
        if now > daily["reset_at"]:
            daily["count"] = 0
            daily["reset_at"] = now + 86400

        if daily["count"] >= self.max_daily:
            raise HTTPException(
                status_code=429,
                detail="Daily limit of 50 messages reached for this session."
            )

    def _check_rpm_limit(self, client_id: str, now: float):
        """Verifies and updates the rolling 60-second window quota."""
        window = [t for t in self.requests[client_id] if now - t < 60]
        self.requests[client_id] = window

        if len(window) >= self.rpm:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please wait 1 minute before sending another question."
            )

    def check_rate_limit(self, client_id: str):
        """Validates incoming request against session eviction, daily, and RPM limits."""
        now = time.time()
        self.evict_stale()
        self._check_daily_limit(client_id, now)
        self._check_rpm_limit(client_id, now)

        self.requests[client_id].append(now)
        self.daily_counts[client_id]["count"] += 1


limiter = SessionRateLimiter()
