"""Result processing and persistence seams for broker responses."""

import uuid

from argus.broker.cache import SearchCache
from argus.broker.dedupe import dedupe_results
from argus.broker.ranking import reciprocal_rank_fusion
from argus.logging import get_logger
from argus.models import SearchQuery, SearchResponse
from argus.persistence.db import SearchPersistenceGateway

logger = get_logger("broker.pipeline")


class SearchResultPipeline:
    def __init__(
        self,
        *,
        cache: SearchCache,
        persistence: SearchPersistenceGateway | None = None,
    ):
        self._cache = cache
        self._persistence = persistence or SearchPersistenceGateway()

    def get_cached(
        self,
        query: SearchQuery,
        run_id: str,
        compute_attribution: bool = False,
    ) -> SearchResponse | None:
        cached = self._cache.get(
            query.query,
            query.mode,
            include_attribution=compute_attribution,
        )
        if cached is None:
            return None
        cached.search_run_id = run_id
        cached.cached = True
        return cached

    def build_response(self, query: SearchQuery, provider_results: dict, traces: list, budget_warnings: list | None = None, compute_attribution: bool = False) -> SearchResponse:
        merged = reciprocal_rank_fusion(provider_results, compute_attribution=compute_attribution)
        final_results = dedupe_results(merged)[: query.max_results]
        response = SearchResponse(
            query=query.query,
            mode=query.mode,
            results=final_results,
            traces=traces,
            total_results=len(final_results),
            cached=False,
            search_run_id=uuid.uuid4().hex[:16],
            budget_warnings=budget_warnings or [],
        )
        if final_results:
            self._cache.put(
                query.query,
                query.mode,
                response,
                include_attribution=compute_attribution,
            )
        self._persistence.record_completed_search(query, response)
        return response
