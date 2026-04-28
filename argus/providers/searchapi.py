"""SearchApi.io provider adapter.

API: https://www.searchapi.io/api/v1/search?engine=google
"""

import time
from typing import List, Tuple
from urllib.parse import urlparse

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
from argus.providers.base import BaseProvider

logger = get_logger("providers.searchapi")

SEARCHAPI_ENDPOINT = "https://www.searchapi.io/api/v1/search"


class SearchApiProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.SEARCHAPI

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
        params = {
            "engine": "google",
            "q": query.query,
            "num": min(query.max_results, 100),
            "api_key": self._config.api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.get(SEARCHAPI_ENDPOINT, params=params)
                resp.raise_for_status()
                data = resp.json()

            results = self._normalize(data, query.max_results)
            latency_ms = int((time.monotonic() - start) * 1000)
            credit_info = {}
            for hdr in ("X-RateLimit-Remaining", "X-Rate-Limit-Remaining"):
                if hdr in resp.headers:
                    credit_info[hdr] = resp.headers[hdr]
            if "search_metadata" in data:
                metadata = data["search_metadata"]
                if isinstance(metadata, dict):
                    for key in ("id", "status", "created_at", "processed_at"):
                        if key in metadata:
                            credit_info[f"search_metadata.{key}"] = metadata[key]

            return results, ProviderTrace(
                provider=self.name,
                status="success",
                results_count=len(results),
                latency_ms=latency_ms,
                credit_info=credit_info or None,
            )
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("SearchApi search failed: %s", exc)
            return [], ProviderTrace(
                provider=self.name,
                status="error",
                latency_ms=latency_ms,
                error=str(exc),
            )

    def _normalize(self, data: dict, max_results: int) -> List[SearchResult]:
        raw_results = data.get("organic_results") or data.get("organic") or []
        results = []
        for i, item in enumerate(raw_results[:max_results]):
            url = item.get("link") or item.get("url") or ""
            if not url:
                continue
            results.append(SearchResult(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("snippet") or item.get("description", ""),
                domain=urlparse(url).netloc,
                provider=self.name,
                score=0.0,
                raw_rank=item.get("position", i),
                metadata={
                    "position": item.get("position", i),
                    "displayed_link": item.get("displayed_link", ""),
                    "date": item.get("date", ""),
                },
            ))
        return results
