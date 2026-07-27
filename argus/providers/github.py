"""
GitHub Search provider adapter.

API: https://api.github.com/search/{type}
Auth: Bearer token (optional — 10 req/min unauthenticated, 5k/min with token)
Pricing: Free (unauthenticated rate limit: 10/min; authenticated: 30/min, 5k/min for search)
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
from argus.broker.provider_evidence import ProviderSearchBatch, QueryRelation

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

    async def search(self, query: SearchQuery) -> ProviderSearchBatch:
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
        request_evidence = self._request_evidence(
            query,
            timeout_seconds=self._attempt_timeout(query),
            provider_request_material=self._canonical_request_material(params),
            query_relation=(
                QueryRelation.PROVIDER_REWRITE
                if provider_query != query.query
                else QueryRelation.EXACT
            ),
        )

        resp = None
        try:
            async with httpx.AsyncClient(
                timeout=self._attempt_timeout(query)
            ) as client:
                resp = await client.get(GITHUB_API_BASE, params=params, headers=headers)

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

        except httpx.HTTPStatusError as e:
            logger.warning(
                "GitHub search failed (HTTP %s)",
                e.response.status_code,
            )
            return self._failure_batch(
                e,
                started_at=start,
                request_evidence=request_evidence,
                observed_status=self._response_status(resp),
            )

        except Exception as e:
            logger.warning("GitHub search failed: %s", type(e).__name__)
            return self._failure_batch(
                e,
                started_at=start,
                request_evidence=request_evidence,
                observed_status=self._response_status(resp),
            )

    def _normalize(self, items: list) -> List[SearchResult]:
        results = []
        for i, item in enumerate(items):
            url = item.get("html_url") or ""
            if not url:
                continue
            results.append(
                SearchResult(
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
                )
            )
        return results
