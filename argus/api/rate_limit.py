"""Simple in-process rate limiter."""

import time
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from argus.logging import get_logger

logger = get_logger("api.rate_limit")


class RateLimiter:
    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
        exempt_paths: Optional[List[str]] = None,
        exempt_tokens: Optional[List[str]] = None,
    ):
        self._max_requests = max_requests
        self._window = window_seconds
        self._exempt_paths = {
            "/api/live",
            "/api/health",
            *(exempt_paths or []),
        }
        self._exempt_tokens = {token for token in (exempt_tokens or []) if token}
        # client_ip -> path -> [timestamps]
        self._requests: Dict[str, Dict[str, List[float]]] = defaultdict(
            lambda: defaultdict(list)
        )

    def is_allowed(
        self, client_ip: str, path: str, api_key_header: Optional[str] = None
    ) -> Tuple[bool, dict]:
        """Check if the request is allowed. Returns (allowed, headers)."""
        if api_key_header and api_key_header in self._exempt_tokens:
            return True, {}

        # Exempt paths
        if path in self._exempt_paths:
            return True, {}

        now = time.time()
        cutoff = now - self._window

        # Prune old timestamps and check count
        window = self._requests[client_ip][path]
        self._requests[client_ip][path] = [t for t in window if t > cutoff]
        window = self._requests[client_ip][path]

        if len(window) >= self._max_requests:
            retry_after = int(window[0] + self._window - now) + 1
            return False, {
                "Retry-After": str(retry_after),
                "X-RateLimit-Limit": str(self._max_requests),
                "X-RateLimit-Remaining": "0",
            }

        window.append(now)
        remaining = self._max_requests - len(window)
        return True, {
            "X-RateLimit-Limit": str(self._max_requests),
            "X-RateLimit-Remaining": str(remaining),
        }
