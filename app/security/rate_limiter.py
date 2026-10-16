import os
import time
from collections import defaultdict
from threading import Lock
from typing import Annotated

from fastapi import HTTPException, Request, status


class SlidingWindowRateLimiter:
    """Thread-safe sliding-window in-memory rate limiter."""

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str) -> tuple[bool, int]:
        now = time.time()
        window_start = now - self.window_seconds
        with self._lock:
            # Prune timestamps older than window
            timestamps = [t for t in self._history[key] if t > window_start]
            if len(timestamps) >= self.max_requests:
                # Calculate retry after
                retry_after = int(timestamps[0] + self.window_seconds - now) + 1
                self._history[key] = timestamps
                return False, max(retry_after, 1)

            timestamps.append(now)
            self._history[key] = timestamps
            return True, 0

    def reset(self) -> None:
        with self._lock:
            self._history.clear()


# Default limiter instances
_ip_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")),
    window_seconds=60,
)
_email_limiter = SlidingWindowRateLimiter(
    max_requests=int(os.getenv("RATE_LIMIT_PER_EMAIL_HOUR", "30")),
    window_seconds=3600,
)


def check_rate_limit(request: Request, key_prefix: str = "ip") -> None:
    """FastAPI dependency to rate limit by client IP address."""
    # Check if disabled in testing/dev
    if os.getenv("DISABLE_RATE_LIMIT", "false").lower() == "true":
        return

    client_ip = request.client.host if request.client else "unknown"
    allowed, retry_after = _ip_limiter.is_allowed(f"{key_prefix}:{client_ip}")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )


def check_email_rate_limit(email: str) -> None:
    """Rate limit submissions per email address to stop abuse/spam bombing."""
    if os.getenv("DISABLE_RATE_LIMIT", "false").lower() == "true":
        return

    allowed, retry_after = _email_limiter.is_allowed(f"email:{email.lower().strip()}")
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Too many requests from this email. Try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
