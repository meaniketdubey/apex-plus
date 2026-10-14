"""Tiny in-memory sliding-window rate limiter.

Good enough for a single API process (it stops someone guessing invite codes from one account).
With several workers each keeps its own window; move this to Redis if that ever matters.
"""
import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, max_calls: int, per_seconds: float):
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._hits: dict[object, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: object) -> bool:
        """Record one attempt for `key`. False when the key is over its budget for the current window."""
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.per_seconds:
                hits.popleft()
            if len(hits) >= self.max_calls:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


# Invite-code lookups (preview + join) per user: generous for real use, useless for guessing a 64-bit token.
invite_code_limiter = RateLimiter(max_calls=10, per_seconds=300)
