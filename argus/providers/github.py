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
            return self._skipped_batch("GitHub provider disabled by config")

        start = time.monotonic()
        headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "Argus-Search-Broker",
        }
        if self._config.api_key:
            headers["Authorization"] = f"token {self._config.api_key}"

        provider_query = query.query
        freshness = self._freshness_params(query)
        qualifier = freshness.get("query_qualifier")
        if qualifier:
            provider_query += f" pushed:{qualifier}"
        params = {
            "q": provider_query,
            "per_page": min(query.max_results, 30),
        }

        try:
            async with httpx.AsyncClient(timeout=self._attempt_timeout(query)) as client:
                resp = await client.get(GITHUB_API_BASE, params=params, headers=headers)

                # GitHub returns 403 on rate limit
                if resp.status_code == 403:
                    remaining = resp.headers.get("X-RateLimit-Remaining")
                    category = (
                        "rate limit"
                        if remaining == "0" or resp.headers.get("Retry-After")
                        else "policy rejection"
                    )
                    error = httpx.HTTPStatusError(
                        category,
                        request=resp.request,
                        response=resp,
                    )
                    return self._failure_batch(error, started_at=start)

                resp.raise_for_status()
                data = resp.json()

            return self._normalized_batch(data, query, started_at=start)

        except httpx.HTTPStatusError as e:
            logger.warning(
                "GitHub search failed (HTTP %s)",
                e.response.status_code,
            )
            return self._failure_batch(e, started_at=start)

        except Exception as e:
            logger.warning("GitHub search failed: %s", type(e).__name__)
            return self._failure_batch(e, started_at=start)

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
