"""
Brave Search provider adapter.

API: https://api.search.brave.com/res/v1/web/search
"""

import time
from typing import List

import httpx  # noqa: F401 - explicit compatibility seam for adapter tests

from argus.config import ProviderConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider
from argus.broker.provider_evidence import ProviderSearchBatch

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

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        start = time.monotonic()

        headers = {
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "X-Subscription-Token": self._config.api_key,
        }
        params = {"q": query.query, "count": min(query.max_results, 20)}
        params.update(self._freshness_params(query))
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(params),
        )

        resp = None
        try:
            resp = await self._provider_request(
                query,
                BRAVE_API_BASE,
                method="GET",
                params=params,
                headers=headers,
            )
            native_failure = self._response_failure_batch(
                resp, started_at=start, request_evidence=request_evidence
            )
            if native_failure is not None:
                return native_failure
            data = resp.json()

            return self._normalized_batch(
                data,
                query,
                started_at=start,
                request_evidence=request_evidence,
                http_status=resp.status_code,
                response_headers=self._response_headers(resp),
            )

        except Exception as e:
            logger.warning("Brave search failed: %s", type(e).__name__)
            return self._failure_batch(
                e,
                started_at=start,
                request_evidence=request_evidence,
                observed_status=self._response_status(resp),
            )

    def _normalize(self, raw_results: list) -> List[SearchResult]:
        results = []
        for i, item in enumerate(raw_results):
            url = item.get("url") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("description", ""),
                    domain=self._extract_domain(url),
                    provider=self.name,
                    score=0.0,
                    raw_rank=i,
                    metadata={
                        "age": item.get("age", ""),
                        "language": item.get("language", ""),
                        "family_friendly": item.get("family_friendly", ""),
                    },
                )
            )
        return results

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            from urllib.parse import urlparse

            return urlparse(url).netloc
        except Exception:
            return ""
