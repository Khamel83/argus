"""Accepted retrieval facts and the immutable accepted-evidence cache."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
import hashlib
import json
from threading import RLock
from typing import Callable, Mapping, Protocol

from argus.contracts.outcomes import CanonicalOutcome


class CacheOutcome(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"
    PROVEN_EMPTY = "proven_empty"
    FRESHNESS_UNPROVEN = "freshness_unproven"
    STRUCTURAL_FLOOR_FAILURE = "structural_floor_failure"
    EVERY_PROVIDER_FAILURE = "every_provider_failure"
    UNREADY = "unready"
    TIMEOUT = "timeout"
    PERSISTENCE_FAILURE = "persistence_failure"


class CacheDecisionOutcome(str, Enum):
    MISS = "miss"
    HIT_ELIGIBLE = "hit_eligible"
    HIT_INELIGIBLE = "hit_ineligible"


def canonical_cache_outcome(
    outcome: CanonicalOutcome,
    *,
    reason: str,
) -> CacheOutcome:
    """Classify the completed fusion trace without inferring from result count."""
    if outcome is CanonicalOutcome.SUCCESS:
        return CacheOutcome.SUCCESS
    if outcome is CanonicalOutcome.DEGRADED:
        return CacheOutcome.DEGRADED
    if outcome is CanonicalOutcome.EMPTY:
        if reason in {"strict_empty", "empty"}:
            return CacheOutcome.PROVEN_EMPTY
        if reason == "freshness_unproven":
            return CacheOutcome.FRESHNESS_UNPROVEN
        if reason == "research_structural_floor_unmet":
            return CacheOutcome.STRUCTURAL_FLOOR_FAILURE
    if outcome is CanonicalOutcome.PROVIDERS_FAILED:
        return CacheOutcome.EVERY_PROVIDER_FAILURE
    if outcome is CanonicalOutcome.TIMEOUT:
        return CacheOutcome.TIMEOUT
    if outcome is CanonicalOutcome.UNREADY:
        return CacheOutcome.UNREADY
    if outcome is CanonicalOutcome.PERSISTENCE_FAILED:
        return CacheOutcome.PERSISTENCE_FAILURE
    raise ValueError(f"no cache admission outcome for {outcome.value!r}/{reason!r}")


@dataclass(frozen=True, slots=True)
class AcceptanceReceipt:
    receipt_ref: str
    accepted_at: datetime
    acceptance_fingerprint: str


@dataclass(frozen=True, slots=True)
class AcceptedRetrieval:
    """The complete immutable fact admitted by durable acceptance."""

    operation_id: str
    cache_fingerprint: str
    execution_cohort: str
    outcome: CacheOutcome
    results: tuple[Mapping[str, object], ...]
    contributor_attempt_refs: tuple[str, ...]
    origin_spend_usd: str
    acceptance_receipt: AcceptanceReceipt
    plan_id: str = ""
    reason: str = ""
    query: str = ""
    mode: str = "discovery"
    traces: tuple[Mapping[str, object], ...] = ()
    budget_warnings: tuple[str, ...] = ()

    def copy_for_receipt(
        self,
        *,
        current_spend_usd: str = "0",
        current_provider_calls: int = 0,
    ) -> "AcceptedRetrievalView":
        return AcceptedRetrievalView(
            operation_id=self.operation_id,
            cache_fingerprint=self.cache_fingerprint,
            execution_cohort=self.execution_cohort,
            outcome=self.outcome,
            results=deepcopy(list(self.results)),
            contributor_attempt_refs=tuple(self.contributor_attempt_refs),
            origin_spend_usd=self.origin_spend_usd,
            acceptance_receipt=self.acceptance_receipt,
            current_spend_usd=current_spend_usd,
            current_provider_calls=current_provider_calls,
            plan_id=self.plan_id,
            reason=self.reason,
            query=self.query,
            mode=self.mode,
            traces=deepcopy(list(self.traces)),
            budget_warnings=tuple(self.budget_warnings),
        )


@dataclass(slots=True)
class AcceptedRetrievalView:
    """A fresh caller-owned projection of one immutable accepted retrieval."""

    operation_id: str
    cache_fingerprint: str
    execution_cohort: str
    outcome: CacheOutcome
    results: list[dict[str, object]]
    contributor_attempt_refs: tuple[str, ...]
    origin_spend_usd: str
    acceptance_receipt: AcceptanceReceipt
    current_spend_usd: str
    current_provider_calls: int
    plan_id: str
    reason: str
    query: str
    mode: str
    traces: list[dict[str, object]]
    budget_warnings: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AcceptedSearchExecutionEvidence:
    """Safe accepted facts carried from the broker to transport projection."""

    operation_id: str
    receipt_ref: str
    accepted_at: datetime
    acceptance_fingerprint: str
    cache_decision: CacheDecisionOutcome
    origin_spend_usd: Decimal
    current_spend_usd: Decimal
    current_provider_calls: int

    def __post_init__(self) -> None:
        if not self.operation_id or not self.receipt_ref:
            raise ValueError("accepted search evidence requires identity")
        if self.accepted_at.tzinfo is None or self.accepted_at.utcoffset() is None:
            raise ValueError("accepted search evidence requires aware accepted_at")
        if len(self.acceptance_fingerprint) != 64:
            raise ValueError("accepted search evidence requires fingerprint")
        if self.origin_spend_usd < 0 or self.current_spend_usd < 0:
            raise ValueError("accepted search evidence spend must be nonnegative")
        if self.current_provider_calls < 0:
            raise ValueError("accepted search provider calls must be nonnegative")


@dataclass(frozen=True, slots=True)
class AcceptedSearchExecution:
    """Broker-owned accepted search fact for transport-neutral projection."""

    outcome: CanonicalOutcome
    reason: str
    response: object | None
    receipt: AcceptanceReceipt | None
    session_update_failed: bool = False
    evidence: AcceptedSearchExecutionEvidence | None = None


@dataclass(frozen=True, slots=True)
class CacheEntry:
    cache_fingerprint: str
    execution_cohort: str
    accepted: AcceptedRetrieval
    accepted_at: datetime

    @classmethod
    def from_accepted(
        cls,
        accepted: AcceptedRetrieval,
        *,
        accepted_at: datetime | None = None,
    ) -> "CacheEntry":
        if accepted.outcome not in {
            CacheOutcome.SUCCESS,
            CacheOutcome.DEGRADED,
            CacheOutcome.PROVEN_EMPTY,
        }:
            raise ValueError("only accepted usable or proven-empty outcomes are cacheable")
        if not accepted.contributor_attempt_refs:
            raise ValueError("cache entries require complete contributor lineage")
        return cls(
            cache_fingerprint=accepted.cache_fingerprint,
            execution_cohort=accepted.execution_cohort,
            accepted=accepted,
            accepted_at=accepted_at or accepted.acceptance_receipt.accepted_at,
        )


@dataclass(frozen=True, slots=True)
class CacheDecision:
    outcome: CacheDecisionOutcome
    accepted: AcceptedRetrievalView | None = None
    origin_receipt_ref: str | None = None


class AcceptancePersister(Protocol):
    def __call__(self, accepted: AcceptedRetrieval) -> AcceptanceReceipt: ...


def execution_cohort(plan, *, policy_identity: str = "") -> str:
    """Hash exact execution-policy facts that may share one leader."""
    payload = {
        "profile": plan.profile,
        "effective_max_provider_tier": plan.effective_max_provider_tier,
        "candidate_providers": [provider.value for provider in plan.candidate_providers],
        "egress_preference": plan.egress_preference.value,
        "revalidation": plan.revalidation.value,
        "spend_policy_version": plan.spend_policy_version,
        "organization_policy_version": plan.organization_policy_version,
        "policy_identity": policy_identity,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(b"argus-execution-cohort-v1\0" + encoded).hexdigest()


def acceptance_fingerprint(
    *,
    operation_id: str,
    plan_id: str,
    cache_fingerprint: str,
    execution_cohort_id: str,
    outcome: CacheOutcome,
    reason: str,
    results: tuple[Mapping[str, object], ...],
    contributor_attempt_refs: tuple[str, ...],
    origin_spend_usd: str,
) -> str:
    payload = {
        "operation_id": operation_id,
        "plan_id": plan_id,
        "cache_fingerprint": cache_fingerprint,
        "execution_cohort": execution_cohort_id,
        "outcome": outcome.value,
        "reason": reason,
        "results": results,
        "contributor_attempt_refs": contributor_attempt_refs,
        "origin_spend_usd": origin_spend_usd,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class RetrievalCache:
    """Fingerprint and cohort keyed cache of accepted immutable facts.

    It deliberately returns a deep copied view on every hit, so a caller can
    never mutate the durable/cache fact.  Publication is available only via
    ``accept_and_publish`` for the new seam; plain ``publish`` exists solely
    for loading a previously durable entry.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], datetime],
        on_publish: Callable[[], None] | None = None,
    ):
        self._clock = clock
        self._on_publish = on_publish
        self._entries: dict[tuple[str, str], CacheEntry] = {}
        self._lock = RLock()

    def publish(self, entry: CacheEntry) -> None:
        key = (entry.cache_fingerprint, entry.execution_cohort)
        with self._lock:
            existing = self._entries.get(key)
            if existing is not None and existing.accepted_at >= entry.accepted_at:
                return
            self._entries[key] = entry
            if self._on_publish is not None:
                self._on_publish()

    def decide(
        self,
        *,
        cache_fingerprint: str,
        execution_cohort: str,
        max_age_seconds: int,
    ) -> CacheDecision:
        key = (cache_fingerprint, execution_cohort)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return CacheDecision(CacheDecisionOutcome.MISS)
            age_seconds = (self._clock() - entry.accepted_at).total_seconds()
            if age_seconds < 0 or age_seconds > max_age_seconds:
                return CacheDecision(CacheDecisionOutcome.HIT_INELIGIBLE)
            return CacheDecision(
                CacheDecisionOutcome.HIT_ELIGIBLE,
                accepted=entry.accepted.copy_for_receipt(),
                origin_receipt_ref=entry.accepted.acceptance_receipt.receipt_ref,
            )

    def accept_and_publish(
        self,
        accepted: AcceptedRetrieval,
        *,
        persist: AcceptancePersister,
    ) -> AcceptanceReceipt:
        """Persist first, then publish once; persistence exceptions publish nothing."""
        receipt = persist(accepted)
        if receipt != accepted.acceptance_receipt:
            raise ValueError("durable receipt does not match accepted retrieval")
        self.publish(CacheEntry.from_accepted(accepted))
        return receipt

    def size(self) -> int:
        with self._lock:
            return len(self._entries)
