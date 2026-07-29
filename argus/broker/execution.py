"""Provider execution services for the search broker."""

import fnmatch
import math
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Dict, List, Mapping, Sequence

from argus.broker.budgets import BudgetTracker, PROVIDER_TIERS
from argus.broker.health import HealthTracker
from argus.broker.planning import RetrievalPlan
from argus.broker.readiness import ExecutionContext, ProviderReadinessService
from argus.broker.provider_evidence import (
    LegacyProviderBatchAdapter,
    ProviderSearchBatch,
    EgressType,
    attempt_timeout_seconds,
    failure_batch,
    run_with_attempt_deadline,
    with_trusted_provenance,
)
from argus.broker.reachability import ReachabilityMatrix
from argus.config import EgressNode, NodeConfig
from argus.logging import get_logger
from argus.models import ProviderName, ProviderTrace, SearchQuery, SearchResult
from argus.providers.base import BaseProvider
from argus.providers.valyu import VALYU_RESULT_CAP, VALYU_UNIT_PRICE_USD


def caller_tier_cap(caller: str, caps: Mapping[str, int]) -> int | None:
    """Max provider tier this caller may use, or None if uncapped.

    Patterns are fnmatch-style; the most restrictive matching cap wins.
    """
    if not caller or not caps:
        return None
    matches = [cap for pattern, cap in caps.items() if fnmatch.fnmatch(caller, pattern)]
    return min(matches) if matches else None


logger = get_logger("broker.execution")

_COST_ESTIMATES = {
    ProviderName.BRAVE: 1.0,
    ProviderName.SERPER: 1.0,
    ProviderName.TAVILY: 1.0,
    ProviderName.EXA: 1.0,
    ProviderName.PARALLEL: 1.0,
    ProviderName.LINKUP: 1.0,
    ProviderName.YOU: 1.0,
    ProviderName.VALYU: VALYU_UNIT_PRICE_USD,
    ProviderName.SEARCHAPI: 1.0,
}

# Tier 0 providers are always queried — free and unlimited.
_TIER_0_PROVIDERS = {p for p, t in PROVIDER_TIERS.items() if t == 0}


def conservative_charge_estimate(
    provider: ProviderName,
    query: SearchQuery,
) -> float:
    """Return the finite worst-case charge for one adapter request.

    Valyu prices each requested result, capped by the adapter at 20.
    Every other paid adapter currently consumes one request-based credit, so
    its configured single-request estimate is already independent of result
    count.
    """
    unit_charge = _COST_ESTIMATES.get(provider)
    if provider == ProviderName.VALYU:
        if (
            isinstance(query.max_results, bool)
            or not isinstance(query.max_results, int)
            or query.max_results <= 0
        ):
            raise ValueError("max_results must be a positive integer")
        estimate = min(query.max_results, VALYU_RESULT_CAP) * VALYU_UNIT_PRICE_USD
    else:
        estimate = unit_charge
    if estimate is None or not math.isfinite(float(estimate)) or float(estimate) <= 0:
        raise ValueError(f"no finite positive estimate for {provider.value}")
    return float(estimate)


@dataclass(frozen=True, slots=True)
class ProviderInvocationEvidence:
    """Authorization-bound proof that one provider invocation began."""

    provider: ProviderName
    attempt_id: str
    egress: str = "local"


@dataclass
class ProviderExecutionOutcome:
    traces: List[ProviderTrace]
    provider_results: Dict[str, List[SearchResult]]
    live_providers_used: int
    budget_pace_warnings: List[str] = field(default_factory=list)
    provider_batches: Dict[str, ProviderSearchBatch] = field(default_factory=dict)
    invoked_attempts: tuple[ProviderInvocationEvidence, ...] = ()

    def contributor_attempt_refs(self) -> tuple[str, ...]:
        """Expose bounded provider attempt lineage for durable acceptance."""
        refs: list[str] = []
        for _provider, batch in sorted(self.provider_batches.items()):
            attempt_id = batch.request_evidence.attempt_id
            if attempt_id is None:
                # An accepted cache entry must never invent lineage for a
                # legacy adapter that did not emit an attempt reference.
                return ()
            refs.append(attempt_id)
        return tuple(refs)


@dataclass(frozen=True)
class ProviderInvocationOutcome:
    batch: ProviderSearchBatch
    uncertain_charge: bool
    compatibility_trace: ProviderTrace


def _bind_attempt_identity(
    batch: ProviderSearchBatch,
    attempt_id: str,
) -> ProviderSearchBatch:
    if batch.request_evidence.attempt_id == attempt_id:
        return batch
    if batch.request_evidence.attempt_id is not None:
        raise ValueError("provider batch attempt identity does not match authority")
    return replace(
        batch,
        request_evidence=replace(
            batch.request_evidence,
            attempt_id=attempt_id,
        ),
    )


class ProviderExecutor:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        health_tracker: HealthTracker,
        budget_tracker: BudgetTracker,
        routing_policy=None,  # kept for backward compat, not used
        reachability: ReachabilityMatrix | None = None,
        egress_nodes: dict[str, EgressNode] | None = None,
        caller_tier_caps: Mapping[str, int] | None = None,
        spend_repository=None,
        monotonic=time.monotonic,
        node_config: NodeConfig | None = None,
        readiness_service: ProviderReadinessService | None = None,
    ):
        self._providers = providers
        self._health = health_tracker
        self._budgets = budget_tracker
        self._reachability = reachability or ReachabilityMatrix()
        self._egress_nodes = egress_nodes or {}
        self._caller_tier_caps = dict(caller_tier_caps or {})
        self._spend = spend_repository
        self._monotonic = monotonic
        self._node_config = node_config or NodeConfig()
        self._readiness = readiness_service or (
            ProviderReadinessService.from_legacy_observation_sources(
                providers=providers,
                health_tracker=health_tracker,
                budget_tracker=budget_tracker,
                reachability=self._reachability,
                spend_repository=spend_repository,
                monotonic=monotonic,
            )
        )

    def _should_query_paid(
        self, provider: ProviderName, tier: int
    ) -> tuple[bool, str]:
        """Compatibility projection; readiness remains the decision owner."""
        allowed, reason, *_ = self._readiness.paid_pacing(provider)
        return allowed, reason

    async def execute(
        self,
        query: SearchQuery,
        provider_order: Sequence[ProviderName],
        *,
        plan: RetrievalPlan,
        operation_deadline: float | None = None,
        provider_phase_deadline: float | None = None,
    ) -> ProviderExecutionOutcome:
        if not isinstance(plan, RetrievalPlan):
            raise TypeError("validated retrieval plan is required")
        if (
            not isinstance(operation_deadline, (int, float))
            or not isinstance(provider_phase_deadline, (int, float))
            or provider_phase_deadline > operation_deadline
        ):
            raise ValueError("validated operation deadlines are required")
        traces: List[ProviderTrace] = []
        provider_results: Dict[str, List[SearchResult]] = {}
        provider_batches: Dict[str, ProviderSearchBatch] = {}
        live_providers_used = 0
        pace_warnings: List[str] = []
        invoked_attempts: list[ProviderInvocationEvidence] = []
        attempt_scope = str(query.metadata.get("attempt_scope") or uuid.uuid4().hex)
        probe_execution_authorization = None
        probe_key = None
        probe_result_key = None
        if query.metadata.get("probe_no_fallback") is True:
            expected = query.metadata.get("probe_provider")
            if (
                len(provider_order) != 1
                or provider_order[0].value != expected
                or query.providers != [provider_order[0]]
            ):
                raise ValueError("probe execution requires one exact provider")
            probe_key = str(query.metadata.get("probe_idempotency_key") or "")
            probe_execution_authorization = (
                self._readiness.repository.consume_probe_once(
                    provider=provider_order[0],
                    idempotency_key=probe_key,
                    durable_receipt=str(
                        query.metadata.get("probe_receipt") or ""
                    ),
                    attempt_id=(
                        str(query.metadata["probe_attempt_id"])
                        if query.metadata.get("probe_attempt_id") else None
                    ),
                )
            )
            if probe_execution_authorization is not None:
                probe_result_key = probe_key
            attempt_scope = probe_key

        ordered = [p for p in provider_order if p != ProviderName.CACHE]
        total_results_so_far = 0

        for index, pname in enumerate(ordered):
            provider = self._providers.get(pname)
            if provider is None:
                traces.append(
                    ProviderTrace(
                        provider=pname, status="skipped", error="not registered"
                    )
                )
                continue

            tier = PROVIDER_TIERS[pname]
            cap = caller_tier_cap(query.caller, self._caller_tier_caps)
            planned_egress = self._readiness.best_egress(pname)
            context = ExecutionContext(
                provider=pname,
                tier=tier,
                plan_providers=plan.candidate_providers,
                free_only=query.free_only,
                caller_tier_cap=cap,
                scope=self._readiness.execution_scope(
                    pname,
                    egress=planned_egress or "local",
                    request_class=plan.intent.value,
                ),
                plan_id=str(getattr(plan, "plan_id", None) or attempt_scope),
                caller_identity=query.caller or "unknown",
                idempotency_key=f"{attempt_scope}:{pname.value}:{index}",
                egress=planned_egress or "local",
                request_class=plan.intent.value,
            )

            # Reachability check — route to worker if local is blocked
            best_egress = planned_egress
            if best_egress is None:
                traces.append(
                    ProviderTrace(
                        provider=pname, status="skipped", error="no reachable egress"
                    )
                )
                continue
            if best_egress != "local":
                if tier > 0:
                    traces.append(
                        ProviderTrace(
                            provider=pname,
                            status="skipped",
                            error="paid providers cannot execute on an egress worker",
                        )
                    )
                    continue
                node = self._egress_nodes.get(best_egress)
                if node is None:
                    traces.append(
                        ProviderTrace(
                            provider=pname,
                            status="skipped",
                            error=f"egress node {best_egress!r} not found in config",
                        )
                    )
                    continue
                from argus.broker.remote_provider import RemoteProviderClient

                remote = RemoteProviderClient(pname, node)
                authorization = (
                    probe_execution_authorization
                    or self._readiness.authorize_execution(
                        context,
                        owner=(
                            f"{self._node_config.machine_name or 'local'}:"
                            f"{attempt_scope}"
                        ),
                        conservative_charge=0.0,
                        execution_timeout_seconds=60,
                    )
                )
                if not authorization.allowed:
                    traces.append(
                        ProviderTrace(
                            provider=pname,
                            status="skipped",
                            error=authorization.decision.reason,
                        )
                    )
                    continue
                invoked_attempts.append(
                    ProviderInvocationEvidence(
                        provider=pname,
                        attempt_id=authorization.attempt_id,
                        egress=best_egress,
                    )
                )
                batch = None
                try:
                    try:
                        legacy = await remote.search(query)
                        batch = LegacyProviderBatchAdapter.from_legacy(legacy)
                        batch = _bind_attempt_identity(
                            batch,
                            authorization.attempt_id,
                        )
                        results, trace = batch.results, batch.trace
                        provider_batches[pname.value] = batch
                        uncertain = not self._trace_charge_known(pname, trace)
                    except Exception as exc:
                        # Network failures can occur after the provider accepted
                        # work. The durable reservation remains uncertain.
                        results = []
                        trace = ProviderTrace(
                            provider=pname,
                            status="error",
                            error=(
                                f"remote provider request failed ({type(exc).__name__})"
                            ),
                            egress=best_egress,
                        )
                        batch = failure_batch(pname, exc)
                        uncertain = True
                except BaseException:
                    cancelled = failure_batch(
                        pname,
                        RuntimeError("remote_execution_cancelled"),
                    )
                    self._readiness.complete_execution(
                        authorization, failure=cancelled.failure, actual_charge=None,
                        charge_known=(tier == 0),
                        termination_known=False,
                        evidence_ref=f"execution:{authorization.attempt_id}",
                        probe_idempotency_key=probe_result_key,
                    )
                    raise
                self._readiness.complete_execution(
                    authorization,
                    failure=batch.failure if batch is not None else None,
                    actual_charge=0.0 if trace.status == "success" else None,
                    charge_known=(tier == 0),
                    evidence_ref=f"execution:{authorization.attempt_id}",
                    probe_idempotency_key=probe_result_key,
                )
                traces.append(trace)
                if trace.status == "success":
                    self._readiness.record_legacy_outcome(
                        pname,
                        egress=trace.egress or best_egress,
                        success=True,
                        latency_ms=trace.latency_ms,
                        scope=authorization.scope,
                    )
                    live_providers_used += 1
                    provider_results[pname.value] = results
                    total_results_so_far += len(results)
                else:
                    self._readiness.record_legacy_outcome(
                        pname,
                        egress=trace.egress or best_egress,
                        success=False,
                        latency_ms=trace.latency_ms,
                        scope=authorization.scope,
                    )
                continue

            if (
                query.providers is None
                and tier > 0
                and total_results_so_far >= query.max_results
            ):
                traces.append(
                    ProviderTrace(
                        provider=pname,
                        status="skipped",
                        error="free results satisfied query",
                    )
                )
                continue

            try:
                charge = (
                    conservative_charge_estimate(pname, query) if tier > 0 else 0.0
                )
                authorization = (
                    probe_execution_authorization
                    or self._readiness.authorize_execution(
                        context,
                        owner=(
                            f"{self._node_config.machine_name or 'local'}:"
                            f"{attempt_scope}"
                        ),
                        conservative_charge=charge,
                        execution_timeout_seconds=60,
                    )
                )
            except ValueError as exc:
                traces.append(
                    ProviderTrace(
                        provider=pname,
                        status="skipped",
                        error=f"invalid conservative charge estimate: {exc}",
                    )
                )
                continue
            if not authorization.allowed:
                denied_reason = authorization.decision.reason
                if denied_reason == "exhausted":
                    denied_reason = "budget exhausted"
                elif denied_reason == "free_only":
                    denied_reason = "free_only mode"
                elif denied_reason == "caller_tier_cap":
                    denied_reason = "caller tier cap"
                traces.append(
                    ProviderTrace(
                        provider=pname,
                        status="skipped",
                        error=denied_reason,
                        budget_remaining=(
                            0.0
                            if authorization.decision.code == "spend_blocked"
                            else None
                        ),
                    )
                )
                continue

            invoked_attempts.append(
                ProviderInvocationEvidence(
                    provider=pname,
                    attempt_id=authorization.attempt_id,
                    egress=best_egress,
                )
            )
            try:
                invocation = await self._execute_provider(
                    query,
                    provider,
                    pname,
                    attempt_id=authorization.attempt_id,
                    scope=authorization.scope,
                    plan=plan,
                    provider_phase_deadline=provider_phase_deadline,
                )
                batch = invocation.batch
                batch = _bind_attempt_identity(
                    batch,
                    authorization.attempt_id,
                )
                results, trace = batch.results, invocation.compatibility_trace
                provider_batches[pname.value] = batch
                uncertain = invocation.uncertain_charge
            except BaseException:
                # The provider may have accepted work before cancellation or
                # transport loss. Preserve the reservation as uncertain.
                self._readiness.complete_execution(
                    authorization,
                    failure=None,
                    actual_charge=None,
                    charge_known=(tier == 0),
                    termination_known=False,
                    evidence_ref=f"execution:{authorization.attempt_id}",
                    probe_idempotency_key=probe_result_key,
                )
                raise
            actual_charge = None
            if trace.credit_info and "cost_usd" in trace.credit_info:
                actual_charge = float(trace.credit_info["cost_usd"])
            elif trace.status == "success":
                actual_charge = charge
            self._readiness.complete_execution(
                authorization,
                failure=batch.failure,
                actual_charge=actual_charge,
                charge_known=(tier == 0 or not uncertain),
                evidence_ref=f"execution:{authorization.attempt_id}",
                probe_idempotency_key=probe_result_key,
            )
            trace.budget_remaining = self._readiness.budget_projection(pname)["remaining"]
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
            provider_batches=provider_batches,
            invoked_attempts=tuple(invoked_attempts),
        )

    async def _execute_provider(
        self,
        query: SearchQuery,
        provider: BaseProvider,
        provider_name: ProviderName,
        *,
        attempt_id: str | None = None,
        scope=None,
        plan: RetrievalPlan | None = None,
        provider_phase_deadline: float | None = None,
    ) -> ProviderInvocationOutcome:
        if scope is None:
            scope = self._readiness.execution_scope(
                provider_name,
                egress="local",
                request_class=query.mode.value,
            )
        metadata = dict(query.metadata)
        if attempt_id is not None:
            metadata["_provider_attempt_id"] = attempt_id
        if plan is not None:
            metadata["_retrieval_plan"] = plan
            metadata["_freshness_window"] = plan.freshness
        if provider_phase_deadline is not None:
            metadata["_provider_phase_deadline"] = provider_phase_deadline
            metadata["_monotonic"] = self._monotonic
        adapter_query = replace(
            query,
            metadata=metadata,
        )
        try:
            if provider_phase_deadline is None:
                raw_output = await provider.search(adapter_query)
            else:
                configured_timeout = float(
                    getattr(getattr(provider, "_config", None), "timeout_seconds", 15)
                )
                # Refuse before constructing the provider coroutine, then enforce
                # the same absolute deadline around the real adapter execution.
                attempt_timeout_seconds(
                    configured_timeout=configured_timeout,
                    provider_phase_deadline=provider_phase_deadline,
                    monotonic=self._monotonic,
                )
                raw_output = await run_with_attempt_deadline(
                    provider.search(adapter_query),
                    configured_timeout=configured_timeout,
                    provider_phase_deadline=provider_phase_deadline,
                    monotonic=self._monotonic,
                )
            if isinstance(raw_output, ProviderSearchBatch):
                batch = raw_output
            elif (
                isinstance(raw_output, tuple)
                and len(raw_output) == 2
                and isinstance(raw_output[1], ProviderTrace)
            ):
                batch = LegacyProviderBatchAdapter.from_legacy(raw_output)
            else:
                raise TypeError("provider returned an invalid batch contract")
            try:
                trusted_egress = EgressType(self._node_config.egress_type)
            except ValueError:
                trusted_egress = EgressType.UNKNOWN
            batch = with_trusted_provenance(
                batch,
                egress=trusted_egress,
                machine=self._node_config.machine_name or None,
            )
            results = batch.results
            trace = batch.trace
            if trace.status == "success":
                self._readiness.record_legacy_outcome(
                    provider_name,
                    egress=trace.egress or "local",
                    success=True,
                    latency_ms=trace.latency_ms,
                    scope=scope,
                )

                # Use actual cost if provided by trace, otherwise estimate
                cost = 0.0
                if trace.credit_info and "cost_usd" in trace.credit_info:
                    cost = float(trace.credit_info["cost_usd"])
                elif provider_name != ProviderName.VALYU:
                    cost = _COST_ESTIMATES.get(provider_name, 0.0)

                if math.isfinite(cost) and cost >= 0:
                    trace.budget_remaining = self._readiness.record_budget_usage(
                        provider_name, cost
                    )
                trace.results_count = len(results)
                if trace.credit_info and "cost_usd" in trace.credit_info:
                    reported_cost = float(trace.credit_info["cost_usd"])
                    if not math.isfinite(reported_cost) or reported_cost < 0:
                        trace.error = (
                            "provider returned an invalid charge; "
                            "reservation left uncertain"
                        )

            elif trace.status == "error":
                self._readiness.record_legacy_outcome(
                    provider_name,
                    egress=trace.egress or "local",
                    success=False,
                    latency_ms=trace.latency_ms,
                    scope=scope,
                )
            return ProviderInvocationOutcome(
                batch=batch,
                uncertain_charge=not self._trace_charge_known(provider_name, trace),
                compatibility_trace=trace,
            )
        except Exception as error:
            logger.warning(
                "Provider %s raised unhandled: %s",
                provider_name,
                type(error).__name__,
            )
            self._readiness.record_legacy_outcome(
                provider_name,
                egress="local",
                success=False,
                latency_ms=0,
                scope=scope,
            )
            failure = failure_batch(provider_name, error)
            return ProviderInvocationOutcome(failure, True, failure.trace)

    @staticmethod
    def _trace_charge_known(provider: ProviderName, trace: ProviderTrace) -> bool:
        if trace.status == "success":
            if provider != ProviderName.VALYU:
                return True
            return bool(
                trace.credit_info
                and "cost_usd" in trace.credit_info
                and math.isfinite(float(trace.credit_info["cost_usd"]))
                and float(trace.credit_info["cost_usd"]) >= 0
            )
        if not trace.credit_info:
            return False
        return (
            "cost_usd" in trace.credit_info
            or trace.credit_info.get("charge_known") is True
        )
