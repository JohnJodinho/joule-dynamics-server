import time
from collections import defaultdict
from fastapi import HTTPException, Request

class SessionRateLimiter:
    def __init__(self, requests_per_minute: int = 10, max_daily: int = 50):
        self.rpm = requests_per_minute
        self.max_daily = max_daily
        self.requests = defaultdict(list)
        self.daily_counts = defaultdict(lambda: {"count": 0, "reset_at": 0})

    def check_rate_limit(self, client_id: str):
        now = time.time()
        
        # Check 24-hour Daily Limit
        daily = self.daily_counts[client_id]
        if now > daily["reset_at"]:
            daily["count"] = 0
            daily["reset_at"] = now + 86400
            
        if daily["count"] >= self.max_daily:
            raise HTTPException(
                status_code=429, 
                detail="Daily limit of 50 messages reached for this session."
            )

        # Check RPM Window Limit
        window = [t for t in self.requests[client_id] if now - t < 60]
        self.requests[client_id] = window
        
        if len(window) >= self.rpm:
            raise HTTPException(
                status_code=429, 
                detail="Rate limit exceeded. Please wait 1 minute before sending another question."
            )
            
        self.requests[client_id].append(now)
        daily["count"] += 1

limiter = SessionRateLimiter()
