"""
Serper (Google Search API) provider adapter.

API: https://google.serper.dev/search
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

logger = get_logger("providers.serper")

SERPER_API_BASE = "https://google.serper.dev/search"


class SerperProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.SERPER

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
            "X-API-KEY": self._config.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "q": query.query,
            "num": query.max_results,
        }
        self._freshness_params(query)
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(payload),
        )

        try:
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query)
            ) as client:
                resp = await client.post(SERPER_API_BASE, json=payload, headers=headers)
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
            logger.warning("Serper search failed: %s", type(e).__name__)
            return self._failure_batch(
                e, started_at=start, request_evidence=request_evidence
            )

    def _normalize(self, raw_results: list) -> List[SearchResult]:
        results = []
        for i, item in enumerate(raw_results):
            url = item.get("link") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    url=url,
                    title=item.get("title", ""),
                    snippet=item.get("snippet", ""),
                    domain=self._extract_domain(url),
                    provider=self.name,
                    score=0.0,
                    raw_rank=i,
                    metadata={
                        "position": item.get("position", i),
                        "date": item.get("date", ""),
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
