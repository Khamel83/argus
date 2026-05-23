"""Provider execution services for the search broker."""

from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from argus.broker.budgets import BudgetTracker, PROVIDER_TIERS
from argus.broker.health import HealthTracker
from argus.broker.reachability import ReachabilityMatrix
from argus.config import EgressNode
from argus.logging import get_logger
from argus.models import ProviderName, ProviderTrace, SearchQuery, SearchResult
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
    ProviderName.VALYU: 0.0015,  # ~$1.50 per 1k fast searches
    ProviderName.SEARCHAPI: 1.0,
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
        reachability: ReachabilityMatrix | None = None,
        egress_nodes: dict[str, EgressNode] | None = None,
    ):
        self._providers = providers
        self._health = health_tracker
        self._budgets = budget_tracker
        self._reachability = reachability or ReachabilityMatrix()
        self._egress_nodes = egress_nodes or {}

    def _should_query_paid(self, provider: ProviderName, tier: int) -> tuple[bool, str]:
        """Decide whether to query a paid provider based on budget pace.

        Returns (should_query, reason).
        Tier 3 (one-time) providers are never "over pace" — exhaustion is the sole gate.
        Tier 1 (monthly) providers are paced to spread credits across 30 days.
        """
        if self._budgets.is_budget_exhausted(provider):
            return False, "budget exhausted"

        if not self._budgets.is_over_pace(provider):
            return True, "under pace"

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

            # Reachability check — route to worker if local is blocked
            best_egress = self._reachability.best_egress(pname)
            if best_egress is None:
                traces.append(ProviderTrace(
                    provider=pname, status="skipped", error="no reachable egress"
                ))
                continue
            if best_egress != "local":
                node = self._egress_nodes.get(best_egress)
                if node is None:
                    traces.append(ProviderTrace(
                        provider=pname, status="skipped",
                        error=f"egress node {best_egress!r} not found in config"
                    ))
                    continue
                from argus.broker.remote_provider import RemoteProviderClient
                remote = RemoteProviderClient(pname, node)
                results, trace = await remote.search(query)
                traces.append(trace)
                if trace.status == "success":
                    live_providers_used += 1
                    provider_results[pname.value] = results
                    total_results_so_far += len(results)
                    self._health.record_success(pname)
                    self._budgets.record_usage(pname, _COST_ESTIMATES.get(pname, 0.0))
                else:
                    self._health.record_failure(pname)
                continue

            tier = self._budgets.get_provider_tier(pname)

            if query.free_only and tier > 0:
                traces.append(ProviderTrace(provider=pname, status="skipped", error="free_only mode"))
                continue

            if query.providers is None and tier > 0 and total_results_so_far >= query.max_results:
                traces.append(
                    ProviderTrace(
                        provider=pname,
                        status="skipped",
                        error="free results satisfied query",
                    )
                )
                continue

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

                # Use actual cost if provided by trace, otherwise estimate
                cost = 0.0
                if trace.credit_info and "cost_usd" in trace.credit_info:
                    cost = trace.credit_info["cost_usd"]
                else:
                    cost = _COST_ESTIMATES.get(provider_name, 0.0)

                self._budgets.record_usage(provider_name, cost)
                trace.budget_remaining = self._budgets.get_remaining_budget(provider_name)
                trace.results_count = len(results)

                # Inject provenance metadata if not already set by the provider
                from argus.config import get_config
                cfg = get_config()
                for r in results:
                    if "egress" not in r.metadata:
                        r.metadata["egress"] = cfg.node.egress_type
                    if "machine" not in r.metadata and cfg.node.machine_name:
                        r.metadata["machine"] = cfg.node.machine_name

            elif trace.status == "error":
                self._health.record_failure(provider_name)
            return results, trace
        except Exception as exc:
            logger.warning("Provider %s raised unhandled: %s", provider_name, exc)
            self._health.record_failure(provider_name)
            return [], ProviderTrace(provider=provider_name, status="error", error=str(exc))
