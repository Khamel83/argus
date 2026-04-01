"""HTTP API rate limiter.

Delegates sliding-window counting to core.rate_limit.SlidingWindowLimiter.
Adds HTTP-specific concerns: exempt paths, API key bypass, response headers.
"""

from typing import Dict, List, Optional, Tuple

from argus.core.rate_limit import SlidingWindowLimiter
from argus.logging import get_logger

logger = get_logger("api.rate_limit")


class RateLimiter:
    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
        exempt_paths: Optional[List[str]] = None,
        api_key: Optional[str] = None,
    ):
        self._max_requests = max_requests
        self._exempt_paths = set(exempt_paths or ["/api/health"])
        self._api_key = api_key
        self._limiter = SlidingWindowLimiter(max_requests, window_seconds)

    def is_allowed(
        self, client_ip: str, path: str, api_key_header: Optional[str] = None
    ) -> Tuple[bool, dict]:
        if self._api_key and api_key_header == self._api_key:
            return True, {}

        if path in self._exempt_paths:
            return True, {}

        key = f"{client_ip}:{path}"
        allowed, retry_after = self._limiter.is_allowed(key)

        if not allowed:
            return False, {
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(self._max_requests),
                "X-RateLimit-Remaining": "0",
            }

        remaining = self._limiter.remaining(key)
        return True, {
            "X-RateLimit-Limit": str(self._max_requests),
            "X-RateLimit-Remaining": str(remaining),
        }
