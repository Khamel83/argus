"""Search broker router."""

import os
from typing import Optional

from argus.broker.budgets import BudgetTracker
from argus.broker.execution import ProviderExecutor, ProviderRoutingPolicy
from argus.broker.health import HealthTracker
from argus.broker.pipeline import SearchResultPipeline
from argus.broker.policies import resolve_routing
from argus.broker.session_flow import SessionSearchService
from argus.config import get_config
from argus.core.cache import TTLCache, search_cache_key
from argus.logging import get_logger
from argus.models import ProviderName, SearchQuery, SearchResponse
from argus.persistence.db import SearchPersistenceGateway
from argus.providers.base import BaseProvider

logger = get_logger("broker.router")


class SearchBroker:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        cache: Optional[TTLCache] = None,
        health_tracker: Optional[HealthTracker] = None,
        budget_tracker: Optional[BudgetTracker] = None,
        session_store=None,
        executor: Optional[ProviderExecutor] = None,
        result_pipeline: Optional[SearchResultPipeline] = None,
        session_service: Optional[SessionSearchService] = None,
    ):
        self._providers = providers
        self._cache = cache or TTLCache(
            ttl_seconds=168 * 3600,
            key_fn=search_cache_key,
        )
        self._health = health_tracker or HealthTracker()
        self._budgets = budget_tracker or BudgetTracker(
            persist_path=get_config().db_path
        )
        self._config = get_config()
        self._session_store = session_store
        self._executor = executor or ProviderExecutor(
            providers=self._providers,
            health_tracker=self._health,
            budget_tracker=self._budgets,
            routing_policy=ProviderRoutingPolicy(),
        )
        self._pipeline = result_pipeline or SearchResultPipeline(
            cache=self._cache,
            persistence=SearchPersistenceGateway(),
        )
        self._session_service = session_service or SessionSearchService(session_store=session_store)

        budget_map = {
            ProviderName.BRAVE: self._config.brave.monthly_budget_usd,
            ProviderName.SERPER: self._config.serper.monthly_budget_usd,
            ProviderName.TAVILY: self._config.tavily.monthly_budget_usd,
            ProviderName.EXA: self._config.exa.monthly_budget_usd,
        }
        for pname, budget in budget_map.items():
            if budget > 0:
                self._budgets.set_budget(pname, budget)

    @property
    def cache(self) -> TTLCache:
        return self._cache

    @property
    def health_tracker(self) -> HealthTracker:
        return self._health

    @property
    def budget_tracker(self) -> BudgetTracker:
        return self._budgets

    async def search(self, query: SearchQuery) -> SearchResponse:
        cache_run_id = os.urandom(8).hex()
        cached = self._pipeline.get_cached(query, cache_run_id)
        if cached is not None:
            logger.debug("Cache hit for query: %s (mode=%s)", query.query, query.mode)
            return cached

        provider_order = resolve_routing(query.mode, query.providers)
        outcome = await self._executor.execute(query, provider_order)
        response = self._pipeline.build_response(
            query,
            outcome.provider_results,
            outcome.traces,
        )

        logger.info(
            "Search complete: query=%r mode=%s providers=%d results=%d run=%s",
            query.query,
            query.mode.value,
            outcome.live_providers_used,
            len(response.results),
            response.search_run_id,
        )

        return response

    async def search_with_session(
        self, query: SearchQuery, session_id: Optional[str] = None
    ) -> tuple[SearchResponse, Optional[str]]:
        return await self._session_service.search_with_session(
            query,
            self.search,
            session_id=session_id,
        )

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
    from argus.providers.searxng import SearXNGProvider
    from argus.providers.serper import SerperProvider
    from argus.providers.tavily import TavilyProvider

    config = get_config()

    providers: dict[ProviderName, BaseProvider] = {
        ProviderName.SEARXNG: SearXNGProvider(config.searxng),
        ProviderName.BRAVE: BraveProvider(config.brave),
        ProviderName.SERPER: SerperProvider(config.serper),
        ProviderName.TAVILY: TavilyProvider(config.tavily),
        ProviderName.EXA: ExaProvider(config.exa),
    }

    from argus.sessions import SessionStore

    session_store = SessionStore()
    return SearchBroker(providers=providers, session_store=session_store)
