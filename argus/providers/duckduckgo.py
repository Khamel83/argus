"""
DuckDuckGo search provider.

Scrapes DuckDuckGo's public website — no API key, no Docker, no cost.
Falls under Tier 0 (free/unlimited) alongside SearXNG.
Less reliable than SearXNG (depends on DDG's HTML not changing).
Uses the `ddgs` package: https://pypi.org/project/ddgs/
"""

import time
from typing import List, Tuple

from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider

logger = get_logger("providers.duckduckgo")


class DuckDuckGoProvider(BaseProvider):
    def __init__(self):
        self._available = self._check_available()

    def _check_available(self) -> bool:
        try:
            from ddgs import DDGS  # noqa: F401
            return True
        except ImportError:
            return False

    @property
    def name(self) -> ProviderName:
        return ProviderName.DUCKDUCKGO

    def is_available(self) -> bool:
        return self._available

    def status(self) -> ProviderStatus:
        if not self._available:
            return ProviderStatus.UNAVAILABLE_MISSING_KEY
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        start = time.monotonic()

        if not self._available:
            return [], ProviderTrace(
                provider=self.name,
                status="skipped",
                error="ddgs package not installed (pip install ddgs)",
            )

        try:
            from ddgs import DDGS

            ddgs = DDGS()
            raw_results = list(ddgs.text(query.query, max_results=query.max_results))
            results = self._normalize(raw_results)
            latency_ms = int((time.monotonic() - start) * 1000)

            trace = ProviderTrace(
                provider=self.name,
                status="success",
                results_count=len(results),
                latency_ms=latency_ms,
            )
            return results, trace

        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("DuckDuckGo search failed: %s", e)
            trace = ProviderTrace(
                provider=self.name,
                status="error",
                latency_ms=latency_ms,
                error=str(e),
            )
            return [], trace

    def _normalize(self, raw_results: list) -> List[SearchResult]:
        results = []
        for i, item in enumerate(raw_results):
            url = item.get("href", "")
            if not url:
                continue
            results.append(SearchResult(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("body", ""),
                domain=self._extract_domain(url),
                provider=self.name,
                score=0.0,
                raw_rank=i,
            ))
        return results

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return ""
