"""
Domain-level rate limiter for content extraction.

Prevents hammering any single domain. Sliding window per extracted domain.
Mirrors RateLimiter pattern from argus/api/rate_limit.py.
"""

import time
from collections import defaultdict
from typing import Dict, List
from urllib.parse import urlparse

from argus.logging import get_logger

logger = get_logger("extraction.rate_limit")


class DomainRateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._max_requests = max_requests
        self._window = window_seconds
        # domain -> [timestamps]
        self._requests: Dict[str, List[float]] = defaultdict(list)

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def is_allowed(self, url: str) -> tuple[bool, int]:
        """Check if extracting from this URL's domain is allowed.

        Returns (allowed, retry_after_seconds).
        """
        domain = self._extract_domain(url)
        if not domain:
            return True, 0

        now = time.time()
        cutoff = now - self._window

        # Prune old timestamps
        window = self._requests[domain]
        self._requests[domain] = [t for t in window if t > cutoff]
        window = self._requests[domain]

        if len(window) >= self._max_requests:
            retry_after = int(window[0] + self._window - now) + 1
            logger.warning(
                "Domain rate limit hit for %s: %d/%d in %ds window",
                domain, len(window), self._max_requests, self._window,
            )
            return False, retry_after

        window.append(now)
        remaining = self._max_requests - len(window)
        logger.debug(
            "Domain %s: %d/%d remaining", domain, remaining, self._max_requests
        )
        return True, 0

    def clear(self) -> None:
        self._requests.clear()
