"""Search broker router."""

import asyncio
import os
import time
import uuid
import weakref
from dataclasses import replace
from decimal import Decimal
from datetime import datetime, timezone
from collections.abc import Callable
from typing import Optional

from argus.broker.budgets import PROVIDER_TIERS, BudgetTracker
from argus.broker.accepted import (
    AcceptanceReceipt,
    AcceptedRetrieval,
    AcceptedSearchExecution,
    CacheDecisionOutcome,
    CacheEntry,
    RetrievalCache,
    acceptance_fingerprint,
    canonical_cache_outcome,
    execution_cohort,
)
from argus.broker.cache import SearchCache
from argus.broker.execution import (
    ProviderExecutor,
    caller_tier_cap,
    conservative_charge_estimate,
)
from argus.broker.fusion import PSL_DOMAIN_POLICY_VERSION, fuse_evidence, project_search_results
from argus.broker.provider_evidence import LegacyProviderBatchAdapter
from argus.broker.health import HealthTracker
from argus.broker.planning import (
    EgressPreference,
    ExecutionPolicySnapshot,
    RetrievalControls,
    resolve_plan,
)
from argus.broker.pipeline import SearchResultPipeline
from argus.broker.policies import resolve_routing
from argus.broker.reachability import ReachabilityMatrix
from argus.broker.readiness import (
    ExecutableProviderRegistry,
    ProviderReadinessService,
)
from argus.broker.session_flow import SessionSearchService
from argus.config import EgressNode, get_config
from argus.contracts import CanonicalOutcome
from argus.logging import get_logger
from argus.models import (
    FusionPolicy,
    ProviderName,
    ProviderTrace,
    SearchQuery,
    SearchResponse,
    is_adapter_provider,
)
from argus.persistence.db import SearchPersistenceGateway
from argus.persistence.evidence import RetrievalEvidence
from argus.providers.base import BaseProvider

logger = get_logger("broker.router")


class SearchBroker:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        cache: Optional[SearchCache] = None,
        health_tracker: Optional[HealthTracker] = None,
        budget_tracker: Optional[BudgetTracker] = None,
        session_store=None,
        executor: Optional[ProviderExecutor] = None,
        result_pipeline: Optional[SearchResultPipeline] = None,
        session_service: Optional[SessionSearchService] = None,
        reachability: ReachabilityMatrix | None = None,
        egress_nodes: dict[str, EgressNode] | None = None,
        spend_repository=None,
        authority_capability: object | None = None,
        utc_clock: Callable[[], datetime] | None = None,
        monotonic_clock: Callable[[], float] | None = None,
        readiness_service: ProviderReadinessService | None = None,
        provider_registry: ExecutableProviderRegistry | None = None,
        accepted_retrieval_cache=None,
    ):
        from argus.authority import broker_construction_allowed

        broker_construction_allowed(authority_capability=authority_capability)
        self._config = get_config()
        self._utc_clock = utc_clock or (lambda: datetime.now(timezone.utc))
        self._monotonic_clock = monotonic_clock or time.monotonic
        self._providers = providers
        self._cache = cache or SearchCache()
        self._accepted_retrieval_cache = accepted_retrieval_cache or RetrievalCache(
            clock=self._utc_clock
        )
        self._accepted_locks = weakref.WeakValueDictionary()
        self._health = health_tracker or HealthTracker()
        self._budgets = budget_tracker or BudgetTracker(
            persist_path=(
                None
                if self._config.env.strip().lower() == "production"
                else os.environ.get("ARGUS_BUDGET_DB_PATH", None)
            )
        )
        configured_budgets = {
            ProviderName.BRAVE: self._config.brave.monthly_budget_usd,
            ProviderName.SERPER: self._config.serper.monthly_budget_usd,
            ProviderName.TAVILY: self._config.tavily.monthly_budget_usd,
            ProviderName.EXA: self._config.exa.monthly_budget_usd,
            ProviderName.SEARCHAPI: self._config.searchapi.monthly_budget_usd,
            ProviderName.YOU: self._config.you.monthly_budget_usd,
            ProviderName.PARALLEL: self._config.parallel.monthly_budget_usd,
            ProviderName.LINKUP: self._config.linkup.monthly_budget_usd,
            ProviderName.VALYU: self._config.valyu.monthly_budget_usd,
            ProviderName.WOLFRAM: self._config.wolfram.monthly_budget_usd,
        }
        for provider_name, configured_budget in configured_budgets.items():
            if configured_budget > 0:
                self._budgets.set_budget(provider_name, configured_budget)
        self._session_store = session_store
        self._reachability = reachability or ReachabilityMatrix()
        self._egress_nodes = egress_nodes or {}
        if spend_repository is None:
            from argus.persistence.provider_spend import (
                create_provider_spend_repository,
            )

            spend_repository = create_provider_spend_repository()
        self._spend_repository = spend_repository
        self._reachability.set_spend_repository(self._spend_repository)
        self._readiness = readiness_service or (
            ProviderReadinessService.from_legacy_observation_sources(
                providers=self._providers,
                health_tracker=self._health,
                budget_tracker=self._budgets,
                reachability=self._reachability,
                spend_repository=self._spend_repository,
                monotonic=self._monotonic_clock,
            )
        )
        if provider_registry is not None:
            provider_registry.persist(self._readiness, self._providers)
        self._executor = executor or ProviderExecutor(
            providers=self._providers,
            health_tracker=self._health,
            budget_tracker=self._budgets,
            reachability=self._reachability,
            egress_nodes=self._egress_nodes,
            caller_tier_caps=self._config.caller_tier_caps,
            spend_repository=self._spend_repository,
            node_config=self._config.node,
            readiness_service=self._readiness,
        )
        self._pipeline = result_pipeline or SearchResultPipeline(
            cache=self._cache,
            persistence=SearchPersistenceGateway(),
        )
        self._session_service = session_service or SessionSearchService(
            session_store=session_store
        )

        budget_map = {
            ProviderName.BRAVE: self._config.brave.monthly_budget_usd,
            ProviderName.SERPER: self._config.serper.monthly_budget_usd,
            ProviderName.TAVILY: self._config.tavily.monthly_budget_usd,
            ProviderName.EXA: self._config.exa.monthly_budget_usd,
            ProviderName.SEARCHAPI: self._config.searchapi.monthly_budget_usd,
            ProviderName.YOU: self._config.you.monthly_budget_usd,
            ProviderName.PARALLEL: self._config.parallel.monthly_budget_usd,
            ProviderName.LINKUP: self._config.linkup.monthly_budget_usd,
            ProviderName.VALYU: self._config.valyu.monthly_budget_usd,
            ProviderName.WOLFRAM: self._config.wolfram.monthly_budget_usd,
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

    @property
    def spend_repository(self):
        """Return the durable provider-spend ledger."""
        return self._spend_repository

    @property
    def readiness_service(self) -> ProviderReadinessService:
        return self._readiness

    async def search(
        self,
        query: SearchQuery,
        compute_attribution: bool = False,
        persist_legacy: bool = True,
    ) -> SearchResponse:
        monotonic_started_at = self._monotonic_clock()

        # Phase 4/5: Residential Search Policy
        res_policy = self._config.residential.policy
        if res_policy != "off":
            if res_policy == "always":
                query.metadata["prefer_residential"] = True
            elif (
                res_policy == "prefer_on_datacenter"
                and self._config.node.egress_type != "residential"
            ):
                query.metadata["prefer_residential"] = True

        cap = caller_tier_cap(query.caller, self._config.caller_tier_caps)
        egress_preference = (
            EgressPreference.PREFER_RESIDENTIAL
            if query.metadata.get("prefer_residential") is True
            else EgressPreference.DEFAULT
        )
        plan = resolve_plan(
            query,
            RetrievalControls(),
            compute_attribution,
            ExecutionPolicySnapshot(
                effective_max_provider_tier=3 if cap is None else cap,
                egress_preference=egress_preference,
            ),
            self._utc_clock,
        )
        operation_deadline = monotonic_started_at + (plan.deadline_ms / 1_000)
        provider_phase_deadline = max(
            monotonic_started_at,
            operation_deadline - 5.0,
        )

        cache_run_id = os.urandom(8).hex()
        cached = self._pipeline.get_cached(
            query,
            cache_run_id,
            plan=plan,
            compute_attribution=compute_attribution,
        )
        if cached is not None:
            logger.debug("Cache hit (mode=%s)", query.mode)
            return cached

        provider_order = resolve_routing(query.mode, query.providers)
        outcome = await self._executor.execute(
            query,
            provider_order,
            plan=plan,
            operation_deadline=operation_deadline,
            provider_phase_deadline=provider_phase_deadline,
        )
        response = self._pipeline.build_response(
            query,
            outcome.provider_results,
            outcome.traces,
            plan=plan,
            budget_warnings=outcome.budget_pace_warnings,
            compute_attribution=compute_attribution,
            persist_legacy=persist_legacy,
        )

        logger.info(
            "Search complete: mode=%s providers=%d results=%d run=%s",
            query.mode.value,
            outcome.live_providers_used,
            len(response.results),
            response.search_run_id,
        )

        if outcome.budget_pace_warnings:
            for w in outcome.budget_pace_warnings:
                logger.warning("Budget pace: %s", w)

        return response

    def _accepted_plan(
        self,
        query: SearchQuery,
        *,
        compute_attribution: bool,
    ):
        if self._config.residential.policy == "always" or (
            self._config.residential.policy == "prefer_on_datacenter"
            and self._config.node.egress_type != "residential"
        ):
            query.metadata["prefer_residential"] = True
        cap = caller_tier_cap(query.caller, self._config.caller_tier_caps)
        egress_preference = (
            EgressPreference.PREFER_RESIDENTIAL
            if query.metadata.get("prefer_residential") is True
            else EgressPreference.DEFAULT
        )
        return resolve_plan(
            query,
            RetrievalControls(),
            compute_attribution,
            ExecutionPolicySnapshot(
                effective_max_provider_tier=3 if cap is None else cap,
                egress_preference=egress_preference,
                domain_policy_version=PSL_DOMAIN_POLICY_VERSION,
            ),
            self._utc_clock,
        )

    @staticmethod
    def _trace_projection(trace) -> dict[str, object]:
        return {
            "provider": trace.provider.value,
            "status": trace.status,
            "results_count": trace.results_count,
            "latency_ms": trace.latency_ms,
            "error": trace.error,
            "budget_remaining": trace.budget_remaining,
        }

    @staticmethod
    def _result_projection(result) -> dict[str, object]:
        return {
            "url": result.url,
            "title": result.title,
            "snippet": result.snippet,
            "domain": result.domain,
            "provider": result.provider.value if result.provider else None,
            "score": result.score,
            "egress": result.metadata.get("egress") if result.metadata else None,
            "machine": result.metadata.get("machine") if result.metadata else None,
            "score_attribution": dict(result.score_attribution),
        }

    @staticmethod
    def _origin_spend(traces, query: SearchQuery) -> str:
        total = Decimal("0")
        for trace in traces:
            if trace.credit_info and "cost_usd" in trace.credit_info:
                total += Decimal(str(trace.credit_info["cost_usd"]))
            elif (
                trace.status == "success"
                and PROVIDER_TIERS[trace.provider] > 0
            ):
                total += Decimal(
                    str(conservative_charge_estimate(trace.provider, query))
                )
        return format(total, "f")

    def _accepted_response(
        self,
        accepted: AcceptedRetrieval,
        *,
        cached: bool,
        include_attribution: bool,
    ) -> SearchResponse:
        from argus.models import ProviderTrace, SearchMode, SearchResult

        results = [
            SearchResult(
                url=str(item["url"]),
                title=str(item["title"]),
                snippet=str(item["snippet"]),
                domain=str(item["domain"]),
                provider=(
                    ProviderName(str(item["provider"]))
                    if item.get("provider")
                    else None
                ),
                score=float(item["score"]),
                metadata={
                    key: item[key]
                    for key in ("egress", "machine")
                    if item.get(key) is not None
                },
                score_attribution=(
                    dict(item.get("score_attribution") or {})
                    if include_attribution
                    else {}
                ),
            )
            for item in accepted.results
        ]
        traces = [
            ProviderTrace(
                provider=ProviderName(str(item["provider"])),
                status="cache" if cached else str(item["status"]),
                results_count=int(item.get("results_count") or 0),
                latency_ms=0 if cached else int(item.get("latency_ms") or 0),
                error=None if cached else item.get("error"),
                budget_remaining=item.get("budget_remaining"),
            )
            for item in accepted.traces
        ]
        return SearchResponse(
            query=accepted.query,
            mode=SearchMode(accepted.mode),
            results=results,
            traces=traces,
            total_results=len(results),
            cached=cached,
            search_run_id=accepted.operation_id,
            budget_warnings=list(accepted.budget_warnings),
        )

    async def search_accepted(
        self,
        query: SearchQuery,
        *,
        evidence_repository,
        compute_attribution: bool = False,
        empty_fallback=None,
    ) -> AcceptedSearchExecution:
        """Execute, durably accept, then publish one canonical retrieval fact."""
        plan = self._accepted_plan(query, compute_attribution=compute_attribution)
        cohort = execution_cohort(plan, policy_identity=query.caller)
        key = (plan.cache_fingerprint, cohort)
        lock = self._accepted_locks.setdefault(key, asyncio.Lock())
        async with lock:
            decision = self._accepted_retrieval_cache.decide(
                cache_fingerprint=plan.cache_fingerprint,
                execution_cohort=cohort,
                max_age_seconds=plan.freshness.max_cache_age_seconds or 0,
            )
            if decision.outcome is CacheDecisionOutcome.HIT_ELIGIBLE:
                origin = decision.accepted
                assert origin is not None
                operation_id = uuid.uuid4().hex
                cache_outcome = origin.outcome
                results = tuple(origin.results)
                fingerprint = acceptance_fingerprint(
                    operation_id=operation_id,
                    plan_id=plan.plan_id,
                    cache_fingerprint=plan.cache_fingerprint,
                    execution_cohort_id=cohort,
                    outcome=cache_outcome,
                    reason=origin.reason,
                    results=results,
                    contributor_attempt_refs=origin.contributor_attempt_refs,
                    origin_spend_usd=origin.origin_spend_usd,
                )
                receipt = AcceptanceReceipt(
                    receipt_ref=f"receipt:{operation_id}",
                    accepted_at=self._utc_clock(),
                    acceptance_fingerprint=fingerprint,
                )
                accepted = AcceptedRetrieval(
                    operation_id=operation_id,
                    plan_id=plan.plan_id,
                    cache_fingerprint=plan.cache_fingerprint,
                    execution_cohort=cohort,
                    outcome=cache_outcome,
                    reason=origin.reason,
                    query=query.query,
                    mode=query.mode.value,
                    results=results,
                    contributor_attempt_refs=origin.contributor_attempt_refs,
                    origin_spend_usd=origin.origin_spend_usd,
                    traces=tuple(origin.traces),
                    budget_warnings=tuple(origin.budget_warnings),
                    acceptance_receipt=receipt,
                )
                try:
                    evidence_repository.accept(
                        RetrievalEvidence(
                            accepted=accepted,
                            plan=plan,
                            cache_decision="hit_eligible",
                            origin_receipt_ref=decision.origin_receipt_ref,
                        )
                    )
                except Exception:
                    return AcceptedSearchExecution(
                        CanonicalOutcome.PERSISTENCE_FAILED,
                        "write_failed",
                        None,
                        None,
                    )
                return AcceptedSearchExecution(
                    {
                        "success": CanonicalOutcome.SUCCESS,
                        "degraded": CanonicalOutcome.DEGRADED,
                        "proven_empty": CanonicalOutcome.EMPTY,
                    }[cache_outcome.value],
                    origin.reason,
                    self._accepted_response(
                        accepted,
                        cached=True,
                        include_attribution=compute_attribution,
                    ),
                    receipt,
                )

            started = self._monotonic_clock()
            operation_deadline = started + plan.deadline_ms / 1_000
            provider_deadline = max(started, operation_deadline - 5.0)
            execution = await self._executor.execute(
                query,
                plan.candidate_providers,
                plan=plan,
                operation_deadline=operation_deadline,
                provider_phase_deadline=provider_deadline,
            )
            provider_batches = dict(execution.provider_batches)
            if empty_fallback is not None and not any(
                batch.results for batch in provider_batches.values()
            ):
                fallback_result = await empty_fallback()
                if fallback_result is not None:
                    fallback_result.provider = ProviderName.ARCHIVE
                    fallback_trace = ProviderTrace(
                        provider=ProviderName.ARCHIVE,
                        status="success",
                        results_count=1,
                    )
                    fallback_batch = LegacyProviderBatchAdapter.from_legacy(
                        ([fallback_result], fallback_trace)
                    )
                    fallback_batch = replace(
                        fallback_batch,
                        request_evidence=replace(
                            fallback_batch.request_evidence,
                            attempt_id=f"archive:{uuid.uuid4().hex}",
                        ),
                    )
                    provider_batches[ProviderName.ARCHIVE.value] = fallback_batch
                    execution.traces.append(fallback_trace)
            batches = tuple(
                provider_batches[name] for name in sorted(provider_batches)
            )
            if not batches:
                failed = any(trace.status == "error" for trace in execution.traces)
                outcome = (
                    CanonicalOutcome.PROVIDERS_FAILED
                    if failed
                    else CanonicalOutcome.UNREADY
                )
                reason = "providers_failed" if failed else "no_reachable_provider"
                fusion = None
            else:
                fusion = fuse_evidence(
                    plan,
                    batches,
                    FusionPolicy(
                        operation_deadline=operation_deadline,
                        monotonic=self._monotonic_clock,
                        activatable=True,
                        publication_reserve_seconds=1.0,
                    ),
                    self._utc_clock,
                )
                outcome, reason = fusion.outcome, fusion.reason
            cache_outcome = canonical_cache_outcome(outcome, reason=reason)
            rendered = (
                project_search_results(
                    fusion,
                    include_attribution=compute_attribution,
                )
                if fusion is not None
                else []
            )
            results = tuple(self._result_projection(item) for item in rendered)
            traces = tuple(self._trace_projection(item) for item in execution.traces)
            contributor_refs = tuple(
                batch.request_evidence.attempt_id
                for batch in batches
                if batch.request_evidence.attempt_id is not None
            )
            operation_id = uuid.uuid4().hex
            origin_spend = self._origin_spend(execution.traces, query)
            fingerprint = acceptance_fingerprint(
                operation_id=operation_id,
                plan_id=plan.plan_id,
                cache_fingerprint=plan.cache_fingerprint,
                execution_cohort_id=cohort,
                outcome=cache_outcome,
                reason=reason,
                results=results,
                contributor_attempt_refs=contributor_refs,
                origin_spend_usd=origin_spend,
            )
            receipt = AcceptanceReceipt(
                receipt_ref=f"receipt:{operation_id}",
                accepted_at=self._utc_clock(),
                acceptance_fingerprint=fingerprint,
            )
            accepted = AcceptedRetrieval(
                operation_id=operation_id,
                plan_id=plan.plan_id,
                cache_fingerprint=plan.cache_fingerprint,
                execution_cohort=cohort,
                outcome=cache_outcome,
                reason=reason,
                query=query.query,
                mode=query.mode.value,
                results=results,
                contributor_attempt_refs=contributor_refs,
                origin_spend_usd=origin_spend,
                traces=traces,
                budget_warnings=tuple(execution.budget_pace_warnings),
                acceptance_receipt=receipt,
            )
            cacheable = cache_outcome.value in {
                "success",
                "degraded",
                "proven_empty",
            } and bool(contributor_refs)
            readiness = tuple(
                {
                    "provider": provider.value,
                    **self._readiness.render_snapshot(provider).as_legacy_status(),
                }
                for provider in plan.candidate_providers
            )
            try:
                evidence_repository.accept(
                    RetrievalEvidence(
                        accepted=accepted,
                        plan=plan,
                        provider_batches=batches,
                        fusion=fusion,
                        readiness=readiness,
                        traces=traces,
                        cache_decision=decision.outcome.value,
                        cache_published=cacheable,
                    )
                )
            except Exception:
                return AcceptedSearchExecution(
                    CanonicalOutcome.PERSISTENCE_FAILED,
                    "write_failed",
                    None,
                    None,
                )
            if cacheable:
                self._accepted_retrieval_cache.publish(CacheEntry.from_accepted(accepted))
            response = self._accepted_response(
                accepted,
                cached=False,
                include_attribution=compute_attribution,
            )
            return AcceptedSearchExecution(outcome, reason, response, receipt)

    async def search_with_session_accepted(
        self,
        query: SearchQuery,
        *,
        evidence_repository,
        session_id: str | None = None,
        compute_attribution: bool = False,
    ):
        holder = {}

        async def execute(effective_query):
            result = await self.search_accepted(
                effective_query,
                evidence_repository=evidence_repository,
                compute_attribution=compute_attribution,
            )
            holder["result"] = result
            if result.response is None:
                return SearchResponse(
                    query=effective_query.query,
                    mode=effective_query.mode,
                    results=[],
                    total_results=0,
                    search_run_id=None,
                )
            return result.response

        try:
            _response, resolved_session_id = (
                await self._session_service.search_with_session(
                    query,
                    execute,
                    session_id=session_id,
                )
            )
        except Exception:
            accepted = holder.get("result")
            if accepted is None or accepted.receipt is None:
                raise
            logger.error(
                "Accepted search %s committed but session update failed",
                accepted.receipt.receipt_ref,
            )
            return accepted, session_id
        return holder["result"], resolved_session_id

    async def search_with_session(
        self,
        query: SearchQuery,
        session_id: Optional[str] = None,
        compute_attribution: bool = False,
        persist_legacy: bool = True,
    ) -> tuple[SearchResponse, Optional[str]]:
        return await self._session_service.search_with_session(
            query,
            lambda effective_query: self.search(
                effective_query,
                compute_attribution=compute_attribution,
                persist_legacy=persist_legacy,
            ),
            session_id=session_id,
        )

    def session_exists(self, session_id: str) -> bool:
        """Check a durable retrieval session without creating it."""
        return self._session_service.session_exists(session_id)

    def get_provider_status(self, provider: ProviderName) -> dict:
        """Explicit compatibility projection of the readiness snapshot."""
        readiness = getattr(self, "_readiness", None)
        legacy_projection = not isinstance(readiness, ProviderReadinessService)
        if legacy_projection:
            readiness = ProviderReadinessService.from_legacy_observation_sources(
                providers=self._providers,
                health_tracker=self._health,
                budget_tracker=self._budgets,
                reachability=getattr(self, "_reachability", ReachabilityMatrix()),
            )
            self._readiness = readiness
        readiness._refresh_legacy_observations(provider, "local")
        projection = readiness.render_snapshot(provider).as_legacy_status()
        if legacy_projection:
            projection["health"] = readiness.legacy_health_projection(provider)
        return projection

    def operational_provider_evidence(self) -> dict[str, dict]:
        """Return the broker-owned, public provider evidence snapshot."""
        return {
            provider.value: {
                "status": self.get_provider_status(provider),
                "readiness": self._readiness.render_snapshot(provider).as_dict(),
            }
            for provider in ProviderName
            if is_adapter_provider(provider)
        }

    def provider_readiness_projection(self, provider: ProviderName) -> dict:
        return self._readiness.readiness_projection(provider)

    def provider_budget_projection(self, provider: ProviderName) -> dict:
        return self._readiness.budget_projection(provider)

    async def refresh_provider_evidence(self) -> None:
        """Refresh cached projections without provider invocation or reservation."""
        if not isinstance(
            getattr(self, "_readiness", None), ProviderReadinessService
        ):
            # Compatibility for isolated legacy test doubles that bypass the
            # constructor; real brokers always own readiness and never enter.
            current_outcomes = await self._reachability.probe_all(
                local_providers=self._providers,
                egress_nodes=list(self._egress_nodes.values()),
            )
            for provider, outcomes in current_outcomes.by_provider().items():
                if any(outcomes):
                    self._health.record_success(provider)
                elif outcomes:
                    self._health.record_failure(provider)
            return
        for provider in self._providers:
            self._readiness._refresh_legacy_observations(provider, "local")
            self._readiness.render_snapshot(provider)


def create_broker(*, authority_capability: object | None = None) -> SearchBroker:
    """Factory: create a SearchBroker with all configured providers."""
    from argus.authority import broker_construction_allowed

    broker_construction_allowed(authority_capability=authority_capability)
    from argus.providers.brave import BraveProvider
    from argus.providers.duckduckgo import DuckDuckGoProvider
    from argus.providers.exa import ExaProvider
    from argus.providers.linkup import LinkupProvider
    from argus.providers.parallel import ParallelProvider
    from argus.providers.searchapi import SearchApiProvider
    from argus.providers.searxng import SearXNGProvider
    from argus.providers.serper import SerperProvider
    from argus.providers.tavily import TavilyProvider
    from argus.providers.valyu import ValyuProvider
    from argus.providers.github import GitHubProvider
    from argus.providers.you import YouProvider
    from argus.providers.wolfram import WolframProvider
    from argus.providers.yahoo import YahooProvider

    config = get_config()
    egress_nodes = {n.name: n for n in config.egress_nodes}
    reachability = ReachabilityMatrix()

    providers: dict[ProviderName, BaseProvider] = {
        ProviderName.SEARXNG: SearXNGProvider(config.searxng),
        ProviderName.DUCKDUCKGO: DuckDuckGoProvider(config.duckduckgo),
        ProviderName.YAHOO: YahooProvider(config.yahoo),
        ProviderName.BRAVE: BraveProvider(config.brave),
        ProviderName.SERPER: SerperProvider(config.serper),
        ProviderName.TAVILY: TavilyProvider(config.tavily),
        ProviderName.EXA: ExaProvider(config.exa),
        ProviderName.SEARCHAPI: SearchApiProvider(config.searchapi),
        ProviderName.YOU: YouProvider(config.you),
        ProviderName.PARALLEL: ParallelProvider(config.parallel),
        ProviderName.LINKUP: LinkupProvider(config.linkup),
        ProviderName.VALYU: ValyuProvider(config.valyu),
        ProviderName.GITHUB: GitHubProvider(config.github),
        ProviderName.WOLFRAM: WolframProvider(config.wolfram),
    }

    from argus.sessions import SessionStore

    session_store = SessionStore()
    return SearchBroker(
        providers=providers,
        session_store=session_store,
        reachability=reachability,
        egress_nodes=egress_nodes,
        authority_capability=authority_capability,
        provider_registry=ExecutableProviderRegistry.from_runtime(
            config=config,
            providers=providers,
            durable_spend_repository=True,
        ),
    )
