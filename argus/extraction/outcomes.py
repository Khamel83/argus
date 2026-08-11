"""Immutable extraction planning, execution, and acceptance values."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable, Protocol

from argus.contracts import CanonicalOutcome
from argus.extraction.rejection import (
    ExtractionRejection,
    RejectionAction,
    RejectionCode,
)


class AttemptOutcome(str, Enum):
    CONTENT = "content"
    EMPTY = "empty"
    ADAPTER_REQUEST_REJECTED = "adapter_request_rejected"
    PROVIDER_AUTHENTICATION_REJECTED = "provider_authentication_rejected"
    PROVIDER_POLICY_REJECTED = "provider_policy_rejected"
    RATE_LIMITED = "rate_limited"
    BALANCE_EXHAUSTED = "balance_exhausted"
    TIMEOUT = "timeout"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PARSE_ERROR = "parse_error"
    UNKNOWN_FAILURE = "unknown_failure"


class CacheOutcome(str, Enum):
    MISS = "miss"
    HIT_ELIGIBLE = "hit_eligible"
    HIT_INELIGIBLE = "hit_ineligible"


class ExtractorExecutionDecision(str, Enum):
    INVOKED = "invoked"
    POLICY_SKIPPED = "policy_skipped"


class ArtifactDisposition(str, Enum):
    NONE = "none"
    DIAGNOSTIC_ONLY = "diagnostic_only"
    PARTIAL = "partial"
    USABLE = "usable"


class TerminalCauseKind(str, Enum):
    PREFLIGHT = "preflight"
    OPERATION_DEADLINE = "operation_deadline"
    ATTEMPT_TERMINAL = "attempt_terminal"
    CHAIN_EXHAUSTED = "chain_exhausted"


class RejectionSourceKind(str, Enum):
    ARTIFACT_QUALITY = "artifact_quality"
    ARTIFACT_INCOMPLETE = "artifact_incomplete"
    PREFLIGHT = "preflight"
    OPERATION_DEADLINE = "operation_deadline"
    ATTEMPT_TERMINAL = "attempt_terminal"
    CHAIN_EXHAUSTED = "chain_exhausted"


@dataclass(frozen=True, slots=True)
class AuthenticationScope:
    fingerprint: str
    authority_receipt_ref: str


@dataclass(frozen=True, slots=True)
class AuthenticationScopeAuthorityReceipt:
    receipt_ref: str
    scope: str
    access_scope: str
    privacy_scope: str
    authentication_scope_fingerprint: str
    issued_at: str


ANONYMOUS_AUTHENTICATION_SCOPE = AuthenticationScope(
    fingerprint="anonymous",
    authority_receipt_ref="auth-public-anonymous-v1",
)


@dataclass(frozen=True, slots=True)
class ExtractionCandidate:
    extractor: str
    eligible: bool
    spend_class: str
    policy_rule_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionRequest:
    request_id: str
    extraction_run_id: str | None
    normalized_url: str
    access_scope: str
    caller: str
    profile: str
    privacy_scope: str
    authentication_scope: AuthenticationScope = (
        ANONYMOUS_AUTHENTICATION_SCOPE
    )


@dataclass(frozen=True, slots=True)
class ExtractionPlan:
    plan_ref: str
    normalized_url: str
    access_scope: str
    mode: str
    candidates: tuple[ExtractionCandidate, ...]
    cache_policy_ref: str
    extraction_plan_version: str
    quality_policy_version: str
    completeness_policy_version: str
    partial_allowed: bool
    deadline_ms: int
    caller: str
    profile: str
    privacy_scope: str
    authentication_scope: AuthenticationScope = (
        ANONYMOUS_AUTHENTICATION_SCOPE
    )
    cache_max_age_seconds: int = 604_800
    # Additive policy facts used by the accepted extraction cache.  Defaults
    # preserve the v1 autonomous/legacy plan contract.
    effective_max_provider_tier: int = 3
    provider_restrictions: tuple[str, ...] = ()
    eligible_extractors: tuple[str, ...] = ()
    freshness_window_seconds: int = 604_800
    original_evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.candidates, list):
            object.__setattr__(self, "candidates", tuple(self.candidates))
        for field_name in (
            "provider_restrictions",
            "eligible_extractors",
        ):
            value = getattr(self, field_name)
            if isinstance(value, list):
                object.__setattr__(self, field_name, tuple(value))


@dataclass(frozen=True, slots=True)
class CacheOriginEvidence:
    extraction_run_id: str
    outcome: CanonicalOutcome
    artifact_disposition: ArtifactDisposition
    artifact: ArtifactEvaluation | None
    rejection: ExtractionRejection | None
    steps: tuple[ExtractorDecision, ...]
    acceptance_receipt: ExtractionAcceptanceReceipt
    extraction_outcome_policy_version: str
    extraction_plan_version: str
    quality_policy_version: str
    completeness_policy_version: str
    accepted_at: str
    normalized_url_identity: str
    mode: str
    access_scope: str
    authentication_scope_fingerprint: str
    cache_policy_version: str
    privacy_scope: str
    partial_allowed: bool
    cache_created_at: str
    cache_max_age_seconds: int = 604_800
    profile: str = "autonomous"
    effective_max_provider_tier: int = 3
    provider_restrictions: tuple[str, ...] = ()
    eligible_extractors: tuple[str, ...] = ()
    freshness_window_seconds: int = 604_800
    original_evidence_ref: str | None = None

    @classmethod
    def from_accepted(
        cls,
        accepted: "AcceptedExtractionOutcome",
        *,
        acceptance_repository,
    ) -> "CacheOriginEvidence":
        from argus.extraction.cache import ExtractionCacheIdentity

        durable = acceptance_repository.load_extraction_outcome_by_receipt(
            accepted.acceptance_receipt.receipt_ref
        )
        if durable != accepted:
            raise ExtractionContractRejected()
        identity = ExtractionCacheIdentity.from_accepted(accepted)
        return cls(
            extraction_run_id=accepted.extraction_run_id,
            outcome=accepted.outcome,
            artifact_disposition=accepted.artifact_disposition,
            artifact=accepted.artifact,
            rejection=accepted.rejection,
            steps=accepted.steps,
            acceptance_receipt=accepted.acceptance_receipt,
            extraction_outcome_policy_version=(
                accepted.extraction_outcome_policy_version
            ),
            extraction_plan_version=accepted.plan.extraction_plan_version,
            quality_policy_version=accepted.plan.quality_policy_version,
            completeness_policy_version=(
                accepted.plan.completeness_policy_version
            ),
            accepted_at=accepted.acceptance_receipt.accepted_at,
            normalized_url_identity=identity.normalized_url,
            mode=identity.mode,
            access_scope=identity.access_scope,
            authentication_scope_fingerprint=(
                identity.authentication_scope_fingerprint
            ),
            cache_policy_version=identity.cache_policy_version,
            privacy_scope=identity.privacy_scope,
            partial_allowed=identity.partial_allowed,
            cache_created_at=durable.acceptance_receipt.accepted_at,
            cache_max_age_seconds=identity.cache_max_age_seconds,
            profile=identity.profile,
            effective_max_provider_tier=identity.effective_max_provider_tier,
            provider_restrictions=identity.provider_restrictions,
            eligible_extractors=identity.eligible_extractors,
            freshness_window_seconds=identity.freshness_window_seconds,
            original_evidence_ref=identity.original_evidence_ref,
        )


@dataclass(frozen=True, slots=True)
class CacheDecision:
    outcome: CacheOutcome
    origin_run_ref: str | None = None
    age_seconds: int | None = None
    origin_evidence: CacheOriginEvidence | None = None
    current_identity: object | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class ExtractionProvenance:
    source_type: str
    egress: str
    machine: str
    authentication_scope_ref: str | None = None
    cookie_scope_ref: str | None = None
    archive_ref: str | None = None


@dataclass(frozen=True, slots=True)
class SpendEvidence:
    actual_usd: Decimal
    reserved_usd: Decimal
    spend_attempt_ref: str


@dataclass(frozen=True, slots=True)
class ExtractorDecision:
    ordinal: int
    extractor: str
    decision: ExtractorExecutionDecision
    attempt_outcome: AttemptOutcome | None = None
    latency_ms: int | None = None
    provenance: ExtractionProvenance | None = None
    spend: SpendEvidence | None = None
    policy_rule_ref: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactEvaluation:
    artifact_ref: str
    content_identity: str
    text: str
    title: str
    author: str
    published_date: str | None
    word_count: int
    quality_passed: bool | None
    is_complete: bool | None
    completeness_confidence: Decimal | None
    completeness_signals: tuple[str, ...]
    completeness_assessment_version: str
    completeness_recommended_action: str
    provenance: ExtractionProvenance

    def __post_init__(self) -> None:
        if isinstance(self.completeness_signals, list):
            object.__setattr__(
                self,
                "completeness_signals",
                tuple(self.completeness_signals),
            )


@dataclass(frozen=True, slots=True)
class TerminalCause:
    kind: TerminalCauseKind
    preflight_outcome: CanonicalOutcome | None = None
    authority_ref: str | None = None
    deadline_ref: str | None = None
    ordinal: int | None = None
    policy_rule_ref: str | None = None
    invoked_ordinals: tuple[int, ...] = ()
    distinct_attempt_outcomes: tuple[AttemptOutcome, ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.invoked_ordinals, list):
            object.__setattr__(
                self,
                "invoked_ordinals",
                tuple(self.invoked_ordinals),
            )
        if isinstance(self.distinct_attempt_outcomes, list):
            object.__setattr__(
                self,
                "distinct_attempt_outcomes",
                tuple(self.distinct_attempt_outcomes),
            )


@dataclass(frozen=True, slots=True)
class RawExtractionResult:
    cache_decision: CacheDecision
    steps: tuple[ExtractorDecision, ...]
    artifact: ArtifactEvaluation | None
    selected_extractor: str | None
    terminal_cause: TerminalCause | None
    operation_latency_ms: int

    def __post_init__(self) -> None:
        if isinstance(self.steps, list):
            object.__setattr__(self, "steps", tuple(self.steps))


@dataclass(frozen=True, slots=True)
class RejectionFacts:
    source_kind: RejectionSourceKind
    terminal_outcome: CanonicalOutcome | None
    attempt_outcomes: tuple[AttemptOutcome, ...]
    provider: str | None
    quality_passed: bool | None
    is_complete: bool | None
    attempt_count: int
    last_status: str | None
    total_latency_ms: int
    eligible_fallback_remains: bool
    autonomous: bool


RejectionMapper = Callable[[RejectionFacts], object]


@dataclass(frozen=True, slots=True)
class ExtractionFinalizationClaim:
    extraction_outcome_policy_version: str
    extraction_run_id: str
    request_id: str
    plan: ExtractionPlan
    artifact: ArtifactEvaluation | None
    steps: tuple[ExtractorDecision, ...]
    terminal_cause: TerminalCause | None
    selected_extractor: str | None
    cache_decision: CacheDecision
    operation_latency_ms: int


class ExtractionOutcomeRepository(Protocol):
    def accept_extraction_outcome(
        self,
        projection: "FinalizedExtractionProjection",
    ) -> "ExtractionAcceptanceReceipt": ...

    def finalize_extraction_once(
        self,
        claim: ExtractionFinalizationClaim,
        build_projection: Callable[[], "FinalizedExtractionProjection"],
    ) -> "AcceptedExtractionOutcome": ...


@dataclass(frozen=True, slots=True)
class OutcomePolicy:
    version: str
    autonomous: bool = True
    repository: ExtractionOutcomeRepository | None = field(
        default=None,
        compare=False,
        repr=False,
    )
    rejection_mapper: RejectionMapper | None = field(
        default=None,
        compare=False,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class ExtractionAcceptanceReceipt:
    receipt_ref: str
    accepted_at: str
    scope: str


@dataclass(frozen=True, slots=True)
class FinalizedExtractionProjection:
    extraction_outcome_policy_version: str
    outcome: CanonicalOutcome
    artifact_disposition: ArtifactDisposition
    extraction_run_id: str
    request_id: str
    plan_ref: str
    plan: ExtractionPlan
    artifact: ArtifactEvaluation | None
    rejection: ExtractionRejection | None
    steps: tuple[ExtractorDecision, ...]
    terminal_cause: TerminalCause | None
    selected_extractor: str | None
    cache_decision: CacheDecision
    operation_latency_ms: int
    normalized_url_identity: str | None = None


@dataclass(frozen=True, slots=True)
class AcceptedExtractionOutcome:
    extraction_outcome_policy_version: str
    outcome: CanonicalOutcome
    artifact_disposition: ArtifactDisposition
    extraction_run_id: str
    request_id: str
    plan_ref: str
    plan: ExtractionPlan
    artifact: ArtifactEvaluation | None
    rejection: ExtractionRejection | None
    steps: tuple[ExtractorDecision, ...]
    terminal_cause: TerminalCause | None
    selected_extractor: str | None
    cache_decision: CacheDecision
    operation_latency_ms: int
    acceptance_receipt: ExtractionAcceptanceReceipt
    normalized_url_identity: str | None = None

    @classmethod
    def accepted(
        cls,
        projection: FinalizedExtractionProjection,
        receipt: ExtractionAcceptanceReceipt,
    ) -> "AcceptedExtractionOutcome":
        return cls(
            extraction_outcome_policy_version=(
                projection.extraction_outcome_policy_version
            ),
            outcome=projection.outcome,
            artifact_disposition=projection.artifact_disposition,
            extraction_run_id=projection.extraction_run_id,
            request_id=projection.request_id,
            plan_ref=projection.plan_ref,
            plan=projection.plan,
            artifact=projection.artifact,
            rejection=projection.rejection,
            steps=projection.steps,
            terminal_cause=projection.terminal_cause,
            selected_extractor=projection.selected_extractor,
            cache_decision=projection.cache_decision,
            operation_latency_ms=projection.operation_latency_ms,
            acceptance_receipt=receipt,
            normalized_url_identity=projection.normalized_url_identity,
        )

    def to_legacy_extracted_content(self):
        """Project the accepted semantic truth to the readable v1 object."""
        from argus.extraction.completeness import CompletenessResult
        from argus.extraction.models import (
            AcceptedExtractionExecutionEvidence,
            ExtractedContent,
            ExtractionAttempt,
            ExtractorName,
        )

        artifact = self.artifact
        completeness = None
        if artifact is not None and artifact.is_complete is not None:
            completeness = CompletenessResult(
                is_complete=artifact.is_complete,
                confidence=float(artifact.completeness_confidence or 0),
                truncation_type=(
                    "clean" if artifact.is_complete else "incomplete"
                ),
                signals=list(artifact.completeness_signals),
                word_count=artifact.word_count,
                recommended_action=artifact.completeness_recommended_action,
            )
        extractor = None
        if self.selected_extractor is not None:
            try:
                extractor = ExtractorName(self.selected_extractor)
            except ValueError:
                extractor = None
        legacy = ExtractedContent(
            url=self.plan.normalized_url,
            extraction_run_id=self.extraction_run_id,
            title=artifact.title if artifact is not None else "",
            text=artifact.text if artifact is not None else "",
            author=artifact.author if artifact is not None else "",
            date=artifact.published_date if artifact is not None else None,
            word_count=artifact.word_count if artifact is not None else 0,
            extractor=extractor,
            error=(
                None
                if self.outcome
                in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
                else self.outcome.value
            ),
            quality_passed=(
                artifact.quality_passed is True if artifact is not None else False
            ),
            attempts=[
                ExtractionAttempt(
                    extractor=step.extractor,
                    status=(
                        step.attempt_outcome.value
                        if step.attempt_outcome is not None
                        else step.decision.value
                    ),
                    latency_ms=step.latency_ms or 0,
                    failure_summary=(
                        step.policy_rule_ref.removeprefix("extract-").removesuffix(
                            "-v1"
                        )
                        if step.decision is ExtractorExecutionDecision.POLICY_SKIPPED
                        else None
                    ),
                )
                for step in self.steps
            ],
            completeness_result=completeness,
            source_type=(
                artifact.provenance.source_type if artifact is not None else None
            ),
            egress=artifact.provenance.egress if artifact is not None else "unknown",
            machine=artifact.provenance.machine if artifact is not None else None,
        )
        legacy.rejection = self.rejection
        legacy.artifact_disposition = self.artifact_disposition
        legacy.acceptance_receipt = self.acceptance_receipt
        legacy.accepted_outcome = self.outcome
        legacy.cache_hit = self.cache_decision.outcome is CacheOutcome.HIT_ELIGIBLE
        legacy.cache_source_extractor = self.selected_extractor if legacy.cache_hit else None
        legacy.extractors_tried = [step.extractor for step in self.steps]
        invoked_steps = tuple(
            step
            for step in self.steps
            if step.decision is ExtractorExecutionDecision.INVOKED
        )
        spend_steps = tuple(step.spend for step in invoked_steps if step.spend)
        actual_usd = sum(
            (spend.actual_usd for spend in spend_steps),
            start=Decimal("0"),
        )
        reserved_usd = sum(
            (spend.reserved_usd for spend in spend_steps),
            start=Decimal("0"),
        )
        legacy.accepted_execution_evidence = AcceptedExtractionExecutionEvidence(
            operation_id=self.extraction_run_id,
            receipt_ref=self.acceptance_receipt.receipt_ref,
            accepted_at=self.acceptance_receipt.accepted_at,
            receipt_scope=self.acceptance_receipt.scope,
            actual_usd=actual_usd,
            reserved_usd=reserved_usd,
            spend_delta_usd=actual_usd - reserved_usd,
            spend_attempt_refs=tuple(
                spend.spend_attempt_ref for spend in spend_steps
            ),
            spend_complete=len(spend_steps) == len(invoked_steps),
            cache_decision=self.cache_decision.outcome.value,
            cache_age_seconds=self.cache_decision.age_seconds,
            operation_latency_ms=self.operation_latency_ms,
            extractor_call_count=len(invoked_steps),
        )
        return legacy


class ExtractionContractRejected(RuntimeError):
    """Privacy-safe failure for malformed internal extraction evidence."""

    outcome = CanonicalOutcome.EXTRACTION_FAILED
    code = RejectionCode.PROVIDER_UNAVAILABLE
    recommended_action = RejectionAction.TERMINAL

    def __init__(self):
        super().__init__("internal extraction evidence failed closed validation")


class ExtractionPersistenceFailed(RuntimeError):
    """A valid projection could not receive durable acceptance."""

    outcome = CanonicalOutcome.PERSISTENCE_FAILED

    def __init__(self):
        super().__init__("extraction acceptance could not be durably persisted")


class ExtractionAcceptanceConflict(RuntimeError):
    outcome = CanonicalOutcome.INVALID_REQUEST

    def __init__(self):
        super().__init__("extraction run identity has different durable source facts")


class ExtractionPreflightRejected(RuntimeError):
    """Caller-boundary failure before a run or rejection record exists."""

    def __init__(self, outcome: CanonicalOutcome):
        if outcome not in {
            CanonicalOutcome.INVALID_REQUEST,
            CanonicalOutcome.AUTHENTICATION_REJECTED,
        }:
            raise ValueError("preflight exception requires a caller-boundary outcome")
        self.outcome = outcome
        super().__init__("extraction request was rejected before execution")
