"""
Exa provider adapter.

API: https://api.exa.ai/search
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

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        start = time.monotonic()

        headers = {
            "x-api-key": self._config.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "query": query.query,
            "numResults": min(query.max_results, 10),
            "type": "auto",
            "contents": {
                "highlights": {
                    "maxCharacters": 500,
                },
            },
        }
        payload.update(self._freshness_params(query))
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(payload),
        )

        try:
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query)
            ) as client:
                resp = await client.post(EXA_API_BASE, json=payload, headers=headers)
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
            logger.warning("Exa search failed: %s", type(e).__name__)
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
                    title=item.get("title", ""),
                    snippet=item.get("text", ""),
                    domain=self._extract_domain(url),
                    provider=self.name,
                    score=item.get("score", 0.0),
                    raw_rank=i,
                    metadata={
                        "id": item.get("id", ""),
                        "published_date": item.get("published_date", ""),
                        "author": item.get("author", ""),
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
