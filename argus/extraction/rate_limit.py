"""Domain-level rate limiter for content extraction.

Delegates sliding-window counting to core.rate_limit.SlidingWindowLimiter.
Adds domain extraction from URLs.
"""

from typing import Tuple

from argus.core.rate_limit import SlidingWindowLimiter
from argus.logging import get_logger
from urllib.parse import urlparse

logger = get_logger("extraction.rate_limit")


class DomainRateLimiter:
    def __init__(self, max_requests: int = 10, window_seconds: int = 60):
        self._limiter = SlidingWindowLimiter(max_requests, window_seconds)

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    def is_allowed(self, url: str) -> Tuple[bool, int]:
        """Check if extracting from this URL's domain is allowed.

        Returns (allowed, retry_after_seconds).
        """
        domain = self._extract_domain(url)
        if not domain:
            return True, 0

        allowed, retry_after = self._limiter.is_allowed(domain)

        if not allowed:
            logger.warning("Domain rate limit hit for %s, retry after %ds", domain, retry_after)
        else:
            remaining = self._limiter.remaining(domain)
            logger.debug("Domain %s: %d remaining", domain, remaining)

        return allowed, retry_after

    def clear(self) -> None:
        self._limiter.clear()
