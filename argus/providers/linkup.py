"""
Linkup search provider.

API: https://api.linkup.so/search
AI-native search with high factual accuracy.
"""

import time
from typing import List

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

logger = get_logger("providers.linkup")

LINKUP_API_BASE = "https://api.linkup.so/v1/search"


class LinkupProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.LINKUP

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
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
        }
        body = {
            "q": query.query,
            "depth": "fast",
            "outputType": "searchResults",
            "maxResults": min(query.max_results, 20),
        }
        body.update(self._freshness_params(query))
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(body),
        )

        try:
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query)
            ) as client:
                resp = await client.post(LINKUP_API_BASE, json=body, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            return self._normalized_batch(
                data,
                query,
                started_at=start,
                request_evidence=request_evidence,
                response_headers=self._response_headers(resp),
            )

        except Exception as e:
            logger.warning("Linkup search failed: %s", type(e).__name__)
            return self._failure_batch(
                e, started_at=start, request_evidence=request_evidence
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
                    title=item.get("name", ""),
                    snippet=item.get("content", "") or item.get("snippet", ""),
                    domain=self._extract_domain(url),
                    provider=self.name,
                    score=0.0,
                    raw_rank=i,
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
