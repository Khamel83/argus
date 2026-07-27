"""
SearXNG provider adapter.

Self-hosted metasearch — the free local provider floor.
"""

import time
from typing import List

import httpx

from argus.config import SearXNGConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider
from argus.broker.provider_evidence import EgressType, ProviderSearchBatch

logger = get_logger("providers.searxng")


class SearXNGProvider(BaseProvider):
    def __init__(self, config: SearXNGConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.SEARXNG

    def is_available(self) -> bool:
        return self._config.enabled and bool(self._config.base_url)

    def status(self) -> ProviderStatus:
        if not self._config.enabled:
            return ProviderStatus.DISABLED_BY_CONFIG
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
        start = time.monotonic()

        # Decide which URL to use
        use_residential = query.metadata.get("prefer_residential", False)
        base_url = self._config.base_url

        if use_residential and self._config.residential_base_url:
            base_url = self._config.residential_base_url

        url = f"{base_url.rstrip('/')}/search"

        params = {
            "q": query.query,
            "format": "json",
            "pageno": 1,
        }
        params.update(self._freshness_params(query))
        headers = {"Accept": "application/json"}
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(params),
        )

        try:
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query)
            ) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            return self._normalized_batch(
                data,
                query,
                started_at=start,
                request_evidence=request_evidence,
                response_headers=self._response_headers(resp),
                egress=(
                    EgressType.RESIDENTIAL
                    if use_residential and self._config.residential_base_url
                    else None
                ),
            )

        except Exception as e:
            logger.warning(
                "SearXNG search failed: %s",
                type(e).__name__,
            )
            return self._failure_batch(
                e, started_at=start, request_evidence=request_evidence
            )

    def _normalize(
        self, raw_results: list, egress: str = "unknown", machine: str = None
    ) -> List[SearchResult]:
        results = []
        for i, item in enumerate(raw_results):
            url = item.get("url") or ""
            if not url:
                continue
            res = SearchResult(
                url=url,
                title=item.get("title", ""),
                snippet=item.get("content", ""),
                domain=self._extract_domain(url),
                provider=self.name,
                score=item.get("score", 0.0),
                raw_rank=i,
                metadata={
                    "engines": item.get("engines", []),
                    "engine": item.get("engine", ""),
                    "published_date": item.get("publishedDate"),
                    "author": item.get("author", ""),
                    "category": item.get("category", ""),
                    "egress": egress,
                },
            )
            if machine:
                res.metadata["machine"] = machine
            results.append(res)
        return results

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            from urllib.parse import urlparse

            return urlparse(url).netloc
        except Exception:
            return ""
