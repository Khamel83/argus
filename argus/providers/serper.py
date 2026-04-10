"""
Serper (Google Search API) provider adapter.

API: https://google.serper.dev/search
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

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        start = time.monotonic()

        headers = {
            "X-API-KEY": self._config.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "q": query.query,
            "num": query.max_results,
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.post(SERPER_API_BASE, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()

            organic = data.get("organic", [])
            results = self._normalize(organic)
            latency_ms = int((time.monotonic() - start) * 1000)

            credit_info = {}
            if "credits" in data:
                credit_info["credits"] = data["credits"]
            if "credit_limit" in data:
                credit_info["credit_limit"] = data["credit_limit"]
            for hdr in ("X-Credits-Remaining", "X-RateLimit-Remaining"):
                if hdr in resp.headers:
                    credit_info[hdr] = resp.headers[hdr]

            trace = ProviderTrace(
                provider=self.name,
                status="success",
                results_count=len(results),
                latency_ms=latency_ms,
                credit_info=credit_info or None,
            )
            return results, trace

        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Serper search failed: %s", e)
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
            url = item.get("link") or ""
            if not url:
                continue
            results.append(SearchResult(
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
            ))
        return results

    @staticmethod
    def _extract_domain(url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return ""
