"""Generic sliding-window rate limiter.

Thread-safe in-process rate limiting by key. Both the API rate limiter
and domain extraction rate limiter delegate to this.
"""

import time
from collections import defaultdict
from typing import Optional


class SlidingWindowLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self._max_requests = max_requests
        self._window = window_seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """Check if key is under limit. Returns (allowed, retry_after_seconds).

        If allowed, the request is recorded automatically.
        """
        now = time.time()
        cutoff = now - self._window

        window = self._requests[key]
        self._requests[key] = [t for t in window if t > cutoff]
        window = self._requests[key]

        if len(window) >= self._max_requests:
            retry_after = int(window[0] + self._window - now) + 1
            return False, retry_after

        window.append(now)
        return True, 0

    def remaining(self, key: str) -> int:
        now = time.time()
        cutoff = now - self._window
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]
        return self._max_requests - len(self._requests[key])

    def clear(self) -> None:
        self._requests.clear()
