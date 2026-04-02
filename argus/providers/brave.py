"""
Brave Search provider adapter.

API: https://api.search.brave.com/res/v1/web/search
"""

import time
from typing import List, Tuple

import httpx

from argus.config import ProviderConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchResult,
    SearchQuery,
)
from argus.broker.dedupe import extract_domain
from argus.providers.base import BaseProvider

logger = get_logger("providers.brave")

BRAVE_API_BASE = "https://api.search.brave.com/res/v1/web/search"


class BraveProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.BRAVE

    def is_available(self) -> bool:
        return self._config.enabled and bool(self._config.api_key)

    def status(self) -> ProviderStatus:
        if not self._config.enabled:
            return ProviderStatus.DISABLED_BY_CONFIG
        if not self._config.api_key:
            return ProviderStatus.UNAVAILABLE_MISSING_KEY
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        start = time.monotonic()

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._config.api_key,
        }
        params = {"q": query.query, "count": query.max_results}

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.get(BRAVE_API_BASE, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            web_results = data.get("web", {}).get("results", [])
            results = self._normalize(web_results)
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
            logger.warning("Brave search failed: %s", e)
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
            url = item.get("url") or ""
            if not url:
                continue
            results.append(SearchResult(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("description", ""),
                domain=extract_domain(url),
                provider=self.name,
                score=0.0,
                raw_rank=i,
                metadata={
                    "age": item.get("age", ""),
                    "language": item.get("language", ""),
                    "family_friendly": item.get("family_friendly", ""),
                },
            ))
        return results

