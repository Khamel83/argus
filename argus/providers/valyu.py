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
from argus.broker.provider_evidence import FailureCategory

logger = get_logger("providers.valyu")

VALYU_API_BASE = "https://api.valyu.ai/v1/search"
VALYU_RESULT_CAP = 20
VALYU_UNIT_PRICE_USD = 0.0015


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
            return self._skipped_batch("Valyu provider not configured")

        start = time.monotonic()

        headers = {
            "Content-Type": "application/json",
            "X-API-Key": self._config.api_key,
        }
        payload = {
            "query": query.query,
            "max_num_results": min(query.max_results, VALYU_RESULT_CAP),
            "search_type": "web",
            "fast_mode": True,
        }
        payload.update(self._freshness_params(query))

        try:
            async with httpx.AsyncClient(timeout=self._attempt_timeout(query)) as client:
                resp = await client.post(VALYU_API_BASE, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            if not data.get("success"):
                error_msg = data.get("error", "unknown error")
                category = (
                    FailureCategory.BALANCE_EXHAUSTED
                    if "credit" in str(error_msg).lower()
                    else FailureCategory.PROVIDER_UNAVAILABLE
                )
                return self._typed_failure_batch(
                    category,
                    str(error_msg),
                    started_at=start,
                )

            return self._normalized_batch(data, query, started_at=start)

        except httpx.HTTPStatusError as e:
            logger.warning(
                "Valyu search failed (HTTP %s)",
                e.response.status_code,
            )
            return self._failure_batch(e, started_at=start)

        except Exception as e:
            logger.warning("Valyu search failed: %s", type(e).__name__)
            return self._failure_batch(e, started_at=start)

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
