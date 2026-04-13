"""
Valyu Search provider adapter.

API: https://api.valyu.ai/v1/search (POST)
Auth: X-API-Key header
Pricing: CPM-based (~$0.0015 per 1-result fast_mode search)
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
from argus.providers.base import BaseProvider

logger = get_logger("providers.valyu")

VALYU_API_BASE = "https://api.valyu.ai/v1/search"


class ValyuProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.VALYU

    def is_available(self) -> bool:
        return self._config.enabled and bool(self._config.api_key)

    def status(self) -> ProviderStatus:
        if not self._config.enabled:
            return ProviderStatus.DISABLED_BY_CONFIG
        if not self._config.api_key:
            return ProviderStatus.UNAVAILABLE_MISSING_KEY
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        if not self.is_available():
            return [], ProviderTrace(provider=self.name, status="skipped")

        start = time.monotonic()

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self._config.api_key,
        }
        payload = {
            "query": query.query,
            "max_num_results": min(query.max_results, 20),
            "search_type": "web",
            "fast_mode": True,
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.post(VALYU_API_BASE, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            if not data.get("success"):
                error_msg = data.get("error", "unknown error")
                latency_ms = int((time.monotonic() - start) * 1000)
                trace = ProviderTrace(
                    provider=self.name,
                    status="error",
                    latency_ms=latency_ms,
                    error=error_msg,
                )
                return [], trace

            raw_results = data.get("results", [])
            results = self._normalize(raw_results)
            latency_ms = int((time.monotonic() - start) * 1000)

            trace = ProviderTrace(
                provider=self.name,
                status="success",
                results_count=len(results),
                latency_ms=latency_ms,
                credit_info={
                    "cost_usd": data.get("total_deduction_dollars", 0),
                    "total_characters": data.get("total_characters", 0),
                    "tx_id": data.get("tx_id", ""),
                },
            )
            return results, trace

        except httpx.HTTPStatusError as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Valyu search failed (HTTP %s): %s", e.response.status_code, e)
            trace = ProviderTrace(
                provider=self.name,
                status="error",
                latency_ms=latency_ms,
                error=str(e),
            )
            return [], trace

        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Valyu search failed: %s", e)
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
                snippet=item.get("description") or item.get("content", "")[:300],
                domain=self._extract_domain(url),
                provider=self.name,
                score=item.get("relevance_score", 0.0),
                raw_rank=i,
                metadata={
                    "source": item.get("source", ""),
                    "source_type": item.get("source_type", ""),
                    "publication_date": item.get("publication_date", ""),
                    "cost_usd": item.get("price", 0),
                },
            ))
        return results

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return ""
