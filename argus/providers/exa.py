"""
Exa provider adapter.

API: https://api.exa.ai/search
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

logger = get_logger("providers.exa")

EXA_API_BASE = "https://api.exa.ai/search"


class ExaProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.EXA

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
            "x-api-key": self._config.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "query": query.query,
            "num_results": min(query.max_results, 10),
            "type": "auto",
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.post(EXA_API_BASE, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            raw_results = data.get("results", [])
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
            logger.warning("Exa search failed: %s", e)
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
                snippet=item.get("text", ""),
                domain=extract_domain(url),
                provider=self.name,
                score=item.get("score", 0.0),
                raw_rank=i,
                metadata={
                    "id": item.get("id", ""),
                    "published_date": item.get("published_date", ""),
                    "author": item.get("author", ""),
                },
            ))
        return results

