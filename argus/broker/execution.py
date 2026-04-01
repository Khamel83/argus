"""Provider execution services for the search broker."""

from dataclasses import dataclass
from typing import Dict, List, Sequence

from argus.broker.budgets import BudgetTracker
from argus.broker.health import HealthTracker
from argus.logging import get_logger
from argus.models import ProviderName, ProviderTrace, SearchMode, SearchQuery, SearchResult
from argus.providers.base import BaseProvider

logger = get_logger("broker.execution")

_COST_ESTIMATES = {
    ProviderName.BRAVE: 0.003,
    ProviderName.SERPER: 0.001,
    ProviderName.TAVILY: 0.001,
    ProviderName.EXA: 0.001,
}

_EARLY_STOP_THRESHOLDS = {
    SearchMode.RECOVERY: 3,
    SearchMode.DISCOVERY: 5,
    SearchMode.GROUNDING: 3,
    SearchMode.RESEARCH: 8,
}


@dataclass
class ProviderExecutionOutcome:
    traces: List[ProviderTrace]
    provider_results: Dict[str, List[SearchResult]]
    live_providers_used: int


class ProviderRoutingPolicy:
    """Cheap-first routing policy with explicit early-stop and hedge triggers."""

    def should_stop(
        self,
        *,
        query: SearchQuery,
        results_count: int,
        trace: ProviderTrace,
        remaining_providers: Sequence[ProviderName],
    ) -> bool:
        if not remaining_providers:
            return True
        if trace.status != "success":
            return False
        if results_count <= 0:
            return False
        threshold = min(
            query.max_results,
            _EARLY_STOP_THRESHOLDS.get(query.mode, query.max_results),
        )
        return results_count >= max(1, threshold)

    def skipped_after_stop(self, providers: Sequence[ProviderName]) -> List[ProviderTrace]:
        return [
            ProviderTrace(provider=provider, status="skipped", error="early stop")
            for provider in providers
        ]


class ProviderExecutor:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        health_tracker: HealthTracker,
        budget_tracker: BudgetTracker,
        routing_policy: ProviderRoutingPolicy | None = None,
    ):
        self._providers = providers
        self._health = health_tracker
        self._budgets = budget_tracker
        self._routing_policy = routing_policy or ProviderRoutingPolicy()

    async def execute(
        self,
        query: SearchQuery,
        provider_order: Sequence[ProviderName],
    ) -> ProviderExecutionOutcome:
        traces: List[ProviderTrace] = []
        provider_results: Dict[str, List[SearchResult]] = {}
        live_providers_used = 0

        ordered = [provider for provider in provider_order if provider != ProviderName.CACHE]
        for index, pname in enumerate(ordered):
            provider = self._providers.get(pname)
            if provider is None:
                traces.append(
                    ProviderTrace(provider=pname, status="skipped", error="not registered")
                )
                continue

            if not provider.is_available():
                traces.append(
                    ProviderTrace(provider=pname, status="skipped", error="not available")
                )
                continue

            health_status = self._health.get_status(pname)
            if health_status is not None:
                traces.append(
                    ProviderTrace(
                        provider=pname,
                        status="skipped",
                        error=f"health: {health_status.value}",
                    )
                )
                continue

            if self._budgets.is_budget_exhausted(pname):
                traces.append(
                    ProviderTrace(
                        provider=pname,
                        status="skipped",
                        error="budget exhausted",
                        budget_remaining=0.0,
                    )
                )
                continue

            results, trace = await self._execute_provider(query, provider, pname)
            traces.append(trace)
            if trace.status == "success":
                live_providers_used += 1
                provider_results[pname.value] = results
                if self._routing_policy.should_stop(
                    query=query,
                    results_count=trace.results_count,
                    trace=trace,
                    remaining_providers=ordered[index + 1 :],
                ):
                    traces.extend(self._routing_policy.skipped_after_stop(ordered[index + 1 :]))
                    break

        return ProviderExecutionOutcome(
            traces=traces,
            provider_results=provider_results,
            live_providers_used=live_providers_used,
        )

    async def _execute_provider(
        self,
        query: SearchQuery,
        provider: BaseProvider,
        provider_name: ProviderName,
    ) -> tuple[List[SearchResult], ProviderTrace]:
        try:
            results, trace = await provider.search(query)
            if trace.status == "success":
                self._health.record_success(provider_name)
                cost = _COST_ESTIMATES.get(provider_name, 0.0)
                self._budgets.record_usage(provider_name, cost)
                trace.budget_remaining = self._budgets.get_remaining_budget(provider_name)
                trace.results_count = len(results)
            elif trace.status == "error":
                self._health.record_failure(provider_name)
            return results, trace
        except Exception as exc:
            logger.warning("Provider %s raised unhandled: %s", provider_name, exc)
            self._health.record_failure(provider_name)
            return [], ProviderTrace(provider=provider_name, status="error", error=str(exc))
