"""Provider execution services for the search broker."""

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from argus.broker.budgets import BudgetTracker, PROVIDER_TIERS
from argus.broker.health import HealthTracker
from argus.logging import get_logger
from argus.models import ProviderName, ProviderTrace, SearchMode, SearchQuery, SearchResult
from argus.providers.base import BaseProvider

logger = get_logger("broker.execution")

_COST_ESTIMATES = {
    ProviderName.BRAVE: 1.0,
    ProviderName.SERPER: 1.0,
    ProviderName.TAVILY: 1.0,
    ProviderName.EXA: 1.0,
    ProviderName.PARALLEL: 1.0,
    ProviderName.LINKUP: 1.0,
    ProviderName.YOU: 1.0,
}

# Tier 0 providers are always queried — free and unlimited.
_TIER_0_PROVIDERS = {p for p, t in PROVIDER_TIERS.items() if t == 0}


@dataclass
class ProviderExecutionOutcome:
    traces: List[ProviderTrace]
    provider_results: Dict[str, List[SearchResult]]
    live_providers_used: int
    budget_pace_warnings: List[str] = field(default_factory=list)


class ProviderExecutor:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        health_tracker: HealthTracker,
        budget_tracker: BudgetTracker,
        routing_policy=None,  # kept for backward compat, not used
    ):
        self._providers = providers
        self._health = health_tracker
        self._budgets = budget_tracker

    def _should_query_paid(self, provider: ProviderName, tier: int) -> tuple[bool, str]:
        """Decide whether to query a paid provider based on budget pace.

        Returns (should_query, reason).
        """
        if self._budgets.is_budget_exhausted(provider):
            return False, "budget exhausted"

        if not self._budgets.is_over_pace(provider):
            return True, "under pace"

        # Over pace — only query if we haven't gotten many results yet
        # and we really need more. Tier 3 is even more conservative.
        if tier >= 3:
            return False, "over pace, one-time credits conserved"

        return False, "over pace, conserving monthly credits"

    async def execute(
        self,
        query: SearchQuery,
        provider_order: Sequence[ProviderName],
    ) -> ProviderExecutionOutcome:
        traces: List[ProviderTrace] = []
        provider_results: Dict[str, List[SearchResult]] = {}
        live_providers_used = 0
        pace_warnings: List[str] = []

        ordered = [p for p in provider_order if p != ProviderName.CACHE]
        total_results_so_far = 0

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

            tier = self._budgets.get_provider_tier(pname)

            # Tier 0: always query (free, unlimited)
            # Tier 1/3: check budget pace before spending credits
            if tier > 0:
                should_query, reason = self._should_query_paid(pname, tier)
                if not should_query:
                    remaining = self._budgets.get_remaining_budget(pname) or 0
                    used_today = self._budgets.used_today(pname)
                    pace = self._budgets.daily_pace(pname)
                    warning = (
                        f"{pname.value}: {reason} "
                        f"(used {used_today:.0f} today, pace {pace:.0f}/day, "
                        f"{remaining:.0f} remaining)"
                    )
                    pace_warnings.append(warning)
                    traces.append(
                        ProviderTrace(
                            provider=pname,
                            status="skipped",
                            error=reason,
                            budget_remaining=remaining,
                        )
                    )
                    continue

            results, trace = await self._execute_provider(query, provider, pname)
            traces.append(trace)
            if trace.status == "success":
                live_providers_used += 1
                provider_results[pname.value] = results
                total_results_so_far += len(results)

        return ProviderExecutionOutcome(
            traces=traces,
            provider_results=provider_results,
            live_providers_used=live_providers_used,
            budget_pace_warnings=pace_warnings,
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
