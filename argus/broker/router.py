"""
Search broker router.

Orchestrates provider calls, caching, health checks, budget enforcement,
ranking, deduplication, and persistence.
"""

import asyncio
import uuid
from datetime import datetime
from typing import List, Optional

from argus.broker.budgets import BudgetTracker
from argus.broker.cache import SearchCache
from argus.broker.dedupe import dedupe_results
from argus.broker.health import HealthTracker
from argus.broker.policies import resolve_routing
from argus.broker.ranking import reciprocal_rank_fusion
from argus.config import get_config
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderTrace,
    SearchMode,
    SearchQuery,
    SearchResponse,
    SearchResult,
)
from argus.persistence.db import persist_search
from argus.providers.base import BaseProvider

logger = get_logger("broker.router")

# Cost-per-query estimates for budget tracking (USD)
_COST_ESTIMATES = {
    ProviderName.BRAVE: 0.003,
    ProviderName.SERPER: 0.001,
    ProviderName.TAVILY: 0.001,
    ProviderName.EXA: 0.001,
}


class SearchBroker:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        cache: Optional[SearchCache] = None,
        health_tracker: Optional[HealthTracker] = None,
        budget_tracker: Optional[BudgetTracker] = None,
    ):
        self._providers = providers
        self._cache = cache or SearchCache()
        self._health = health_tracker or HealthTracker()
        self._budgets = budget_tracker or BudgetTracker()
        self._config = get_config()

        # Initialize budgets from config
        budget_map = {
            ProviderName.BRAVE: self._config.brave.monthly_budget_usd,
            ProviderName.SERPER: self._config.serper.monthly_budget_usd,
            ProviderName.TAVILY: self._config.tavily.monthly_budget_usd,
            ProviderName.EXA: self._config.exa.monthly_budget_usd,
            ProviderName.SEARCHAPI: self._config.searchapi.monthly_budget_usd,
            ProviderName.YOU: self._config.you.monthly_budget_usd,
        }
        for pname, budget in budget_map.items():
            if budget > 0:
                self._budgets.set_budget(pname, budget)

    @property
    def cache(self) -> SearchCache:
        return self._cache

    @property
    def health_tracker(self) -> HealthTracker:
        return self._health

    @property
    def budget_tracker(self) -> BudgetTracker:
        return self._budgets

    async def search(self, query: SearchQuery) -> SearchResponse:
        run_id = uuid.uuid4().hex[:16]

        # 1. Check cache
        cached = self._cache.get(query.query, query.mode)
        if cached is not None:
            cached.search_run_id = run_id
            cached.cached = True
            logger.debug("Cache hit for query: %s (mode=%s)", query.query, query.mode)
            return cached

        # 2. Resolve routing order
        provider_order = resolve_routing(query.mode, query.providers)

        # 3. Execute providers
        traces: List[ProviderTrace] = []
        provider_results: dict[str, List[SearchResult]] = {}
        live_providers_used = 0

        for pname in provider_order:
            if pname == ProviderName.CACHE:
                continue  # already checked above

            provider = self._providers.get(pname)
            if provider is None:
                continue

            # Skip unavailable
            if not provider.is_available():
                traces.append(ProviderTrace(
                    provider=pname,
                    status="skipped",
                    error="not available",
                ))
                continue

            # Skip cooldown
            health_status = self._health.get_status(pname)
            if health_status is not None:
                traces.append(ProviderTrace(
                    provider=pname,
                    status="skipped",
                    error=f"health: {health_status.value}",
                ))
                continue

            # Skip budget exhausted
            if self._budgets.is_budget_exhausted(pname):
                traces.append(ProviderTrace(
                    provider=pname,
                    status="skipped",
                    error="budget exhausted",
                    budget_remaining=0.0,
                ))
                continue

            # Execute
            try:
                results, trace = await provider.search(query)
                traces.append(trace)
                live_providers_used += 1

                if trace.status == "success":
                    self._health.record_success(pname)
                    cost = _COST_ESTIMATES.get(pname, 0.0)
                    self._budgets.record_usage(pname, cost)
                    trace.budget_remaining = self._budgets.get_remaining_budget(pname)
                    provider_results[pname.value] = results

                elif trace.status == "error":
                    self._health.record_failure(pname)
            except Exception as e:
                logger.warning("Provider %s raised unhandled: %s", pname, e)
                self._health.record_failure(pname)
                traces.append(ProviderTrace(
                    provider=pname,
                    status="error",
                    error=str(e),
                ))

        # 4. Merge and rank
        merged = reciprocal_rank_fusion(provider_results)

        # 5. Dedupe
        final_results = dedupe_results(merged)

        # 6. Trim to max_results
        final_results = final_results[:query.max_results]

        # 7. Build response
        response = SearchResponse(
            query=query.query,
            mode=query.mode,
            results=final_results,
            traces=traces,
            total_results=len(final_results),
            cached=False,
            search_run_id=run_id,
        )

        # 8. Cache the response (if we got results)
        if final_results:
            self._cache.put(query.query, query.mode, response)

        # 9. Persist
        try:
            persist_search(query.query, query.mode.value, response)
        except Exception as e:
            logger.warning("Failed to persist search: %s", e)

        logger.info(
            "Search complete: query=%r mode=%s providers=%d results=%d run=%s",
            query.query,
            query.mode.value,
            live_providers_used,
            len(final_results),
            run_id,
        )

        return response

    def get_provider_status(self, provider: ProviderName) -> dict:
        """Get combined status for a provider (config + health + budget)."""
        provider_obj = self._providers.get(provider)
        base_status = provider_obj.status() if provider_obj else "unknown"

        health_status = self._health.get_status(provider)
        budget_status = self._budgets.check_status(provider)

        effective = base_status
        if health_status:
            effective = health_status.value
        if budget_status:
            effective = budget_status.value

        return {
            "provider": provider.value,
            "config_status": base_status,
            "health": self._health.get_health(provider).__dict__ if provider in self._health._health else None,
            "budget_remaining": self._budgets.get_remaining_budget(provider),
            "effective_status": effective,
        }


def create_broker() -> SearchBroker:
    """Factory: create a SearchBroker with all configured providers."""
    from argus.providers.brave import BraveProvider
    from argus.providers.exa import ExaProvider
    from argus.providers.searchapi import SearchApiProvider
    from argus.providers.searxng import SearXNGProvider
    from argus.providers.serper import SerperProvider
    from argus.providers.tavily import TavilyProvider
    from argus.providers.you import YouProvider

    config = get_config()

    providers: dict[ProviderName, BaseProvider] = {
        ProviderName.SEARXNG: SearXNGProvider(config.searxng),
        ProviderName.BRAVE: BraveProvider(config.brave),
        ProviderName.SERPER: SerperProvider(config.serper),
        ProviderName.TAVILY: TavilyProvider(config.tavily),
        ProviderName.EXA: ExaProvider(config.exa),
        ProviderName.SEARCHAPI: SearchApiProvider(config.searchapi),
        ProviderName.YOU: YouProvider(config.you),
    }

    return SearchBroker(providers=providers)
