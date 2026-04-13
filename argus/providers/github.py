"""
GitHub Search provider adapter.

API: https://api.github.com/search/{type}
Auth: Bearer token (optional — 10 req/min unauthenticated, 5k/min with token)
Pricing: Free (unauthenticated rate limit: 10/min; authenticated: 30/min, 5k/min for search)
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

logger = get_logger("providers.github")

GITHUB_API_BASE = "https://api.github.com/search/repositories"
GITHUB_CODE_BASE = "https://api.github.com/search/code"


class GitHubProvider(BaseProvider):
    def __init__(self, config: ProviderConfig):
        self._config = config

    @property
    def name(self) -> ProviderName:
        return ProviderName.GITHUB

    def is_available(self) -> bool:
        return self._config.enabled

    def status(self) -> ProviderStatus:
        if not self._config.enabled:
            return ProviderStatus.DISABLED_BY_CONFIG
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> Tuple[List[SearchResult], ProviderTrace]:
        if not self.is_available():
            return [], ProviderTrace(provider=self.name, status="skipped")

        start = time.monotonic()
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Argus-Search-Broker",
        }
        if self._config.api_key:
            headers["Authorization"] = f"token {self._config.api_key}"

        params = {
            "q": query.query,
            "per_page": min(query.max_results, 30),
            "sort": "stars",
        }

        try:
            async with httpx.AsyncClient(timeout=self._config.timeout_seconds) as client:
                resp = await client.get(GITHUB_API_BASE, params=params, headers=headers)

                # GitHub returns 403 on rate limit
                if resp.status_code == 403:
                    latency_ms = int((time.monotonic() - start) * 1000)
                    trace = ProviderTrace(
                        provider=self.name,
                        status="error",
                        latency_ms=latency_ms,
                        error="rate limited",
                    )
                    return [], trace

                resp.raise_for_status()
                data = resp.json()

            items = data.get("items", [])
            results = self._normalize(items)
            latency_ms = int((time.monotonic() - start) * 1000)

            # Extract rate limit info from headers
            credit_info = {}
            for hdr in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Used"):
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

        except httpx.HTTPStatusError as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("GitHub search failed (HTTP %s): %s", e.response.status_code, e)
            trace = ProviderTrace(
                provider=self.name,
                status="error",
                latency_ms=latency_ms,
                error=str(e),
            )
            return [], trace

        except Exception as e:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("GitHub search failed: %s", e)
            trace = ProviderTrace(
                provider=self.name,
                status="error",
                latency_ms=latency_ms,
                error=str(e),
            )
            return [], trace

    def _normalize(self, items: list) -> List[SearchResult]:
        results = []
        for i, item in enumerate(items):
            url = item.get("html_url") or ""
            if not url:
                continue
            results.append(SearchResult(
                url=url,
                title=item.get("full_name", ""),
                snippet=item.get("description", "") or "",
                domain="github.com",
                provider=self.name,
                score=0.0,
                raw_rank=i,
                metadata={
                    "stars": item.get("stargazers_count", 0),
                    "language": item.get("language", ""),
                    "forks": item.get("forks_count", 0),
                    "topics": item.get("topics", []),
                    "updated": item.get("updated_at", ""),
                },
            ))
        return results
