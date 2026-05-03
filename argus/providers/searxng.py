"""
SearXNG provider adapter.

Self-hosted metasearch — the free local provider floor.
"""

import time
from typing import List, Tuple

import httpx

from argus.config import SearXNGConfig
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchResult,
    SearchQuery,
)
from argus.providers.base import BaseProvider

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

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        from argus.config import get_config
        config = get_config()
        start = time.monotonic()

        # Decide which URL to use
        use_residential = query.metadata.get("prefer_residential", False)
        base_url = self._config.base_url
        egress = config.node.egress_type
        machine = config.node.machine_name or None

        if use_residential and self._config.residential_base_url:
            base_url = self._config.residential_base_url
            egress = "residential"
            machine = None  # Remote residential node unknown here unless we add more metadata

        url = f"{base_url.rstrip('/')}/search"

        params = {
            "q": query.query,
            "format": "json",
            "pageno": 1,
        }
        headers = {"Accept": "application/json"}

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.get(url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            results = self._normalize(data.get("results", []), egress=egress, machine=machine)
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
            logger.warning("SearXNG search failed: %s", e)
            trace = ProviderTrace(
                provider=self.name,
                status="error",
                latency_ms=latency_ms,
                error=str(e),
            )
            return [], trace

    def _normalize(self, raw_results: list, egress: str = "unknown", machine: str = None) -> List[SearchResult]:
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
