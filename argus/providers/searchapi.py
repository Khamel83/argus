"""SearchApi.io provider adapter.

API: https://www.searchapi.io/api/v1/search?engine=google
"""

import time
from typing import List
from urllib.parse import urlparse

import httpx

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

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        start = time.monotonic()
        params = {
            "engine": "google",
            "q": query.query,
            "num": min(query.max_results, 10),
            "api_key": self._config.api_key,
        }
        params.update(self._freshness_params(query))
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(
                {key: value for key, value in params.items() if key != "api_key"}
            ),
        )

        try:
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query)
            ) as client:
                resp = await client.get(SEARCHAPI_ENDPOINT, params=params)
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
        except Exception as exc:
            logger.warning("SearchApi search failed: %s", type(exc).__name__)
            return self._failure_batch(
                exc, started_at=start, request_evidence=request_evidence
            )

    def _normalize(self, data: dict, max_results: int) -> List[SearchResult]:
        raw_results = data.get("organic_results") or data.get("organic") or []
        results = []
        for i, item in enumerate(raw_results[:max_results]):
            url = item.get("link") or item.get("url") or ""
            if not url:
                continue
            results.append(
                SearchResult(
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
                )
            )
        return results
