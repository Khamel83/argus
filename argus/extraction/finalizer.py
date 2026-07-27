"""Single-authority extraction outcome finalization."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable

from argus.contracts import CanonicalOutcome
from argus.extraction.outcomes import (
    AcceptedExtractionOutcome,
    ArtifactDisposition,
    AttemptOutcome,
    CacheDecision,
    CacheOutcome,
    ExtractionAcceptanceReceipt,
    ExtractionAcceptanceConflict,
    ExtractionContractRejected,
    ExtractionOutcomeRepository,
    ExtractionPersistenceFailed,
    ExtractionPlan,
    ExtractionPreflightRejected,
    ExtractionRequest,
    ExtractorDecision,
    ExtractorExecutionDecision,
    FinalizedExtractionProjection,
    OutcomePolicy,
    RawExtractionResult,
    RejectionFacts,
    RejectionMapper,
    TerminalCause,
    TerminalCauseKind,
)
from argus.extraction.rejection import (
    ExtractionRejection,
    RejectionAction,
    RejectionCode,
    classify_typed_extraction_rejection,
)

_SAFE_LABEL = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_LATENCY_MS = 2**63 - 1
_MAX_NANODOLLARS = 2**63 - 1


@dataclass(frozen=True, slots=True)
class _Decision:
    outcome: CanonicalOutcome
    disposition: ArtifactDisposition
    code: RejectionCode | None
    provider: str | None


_TERMINAL_ATTEMPT_MAPPING = {
    AttemptOutcome.ADAPTER_REQUEST_REJECTED: (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.PARSE_ERROR,
    ),
    AttemptOutcome.PROVIDER_AUTHENTICATION_REJECTED: (
        CanonicalOutcome.UNREADY,
        RejectionCode.PROVIDER_UNAVAILABLE,
    ),
    AttemptOutcome.PROVIDER_POLICY_REJECTED: (
        CanonicalOutcome.POLICY_REJECTED,
        RejectionCode.UNSUPPORTED_SOURCE,
    ),
    AttemptOutcome.EMPTY: (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.EMPTY_RESULT,
    ),
    AttemptOutcome.RATE_LIMITED: (
        CanonicalOutcome.UNREADY,
        RejectionCode.RATE_LIMITED,
    ),
    AttemptOutcome.BALANCE_EXHAUSTED: (
        CanonicalOutcome.UNREADY,
        RejectionCode.PROVIDER_UNAVAILABLE,
    ),
    AttemptOutcome.TIMEOUT: (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.TIMEOUT,
    ),
    AttemptOutcome.PROVIDER_UNAVAILABLE: (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.PROVIDER_UNAVAILABLE,
    ),
    AttemptOutcome.PARSE_ERROR: (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.PARSE_ERROR,
    ),
    AttemptOutcome.UNKNOWN_FAILURE: (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.PROVIDER_UNAVAILABLE,
    ),
    AttemptOutcome.CONTENT: (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.PROVIDER_UNAVAILABLE,
    ),
}


def map_extraction_rejection(facts: RejectionFacts) -> ExtractionRejection:
    """Compatibility facade over issue #57's typed rejection authority."""
    return classify_typed_extraction_rejection(facts)


def _safe_label(value: object) -> bool:
    return isinstance(value, str) and _SAFE_LABEL.fullmatch(value) is not None


def _safe_ref(value: object, *, maximum: int = 128) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value.encode("utf-8")) <= maximum
        and _SAFE_REF.fullmatch(value) is not None
    )


def _plain_int(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _valid_money(value: object) -> bool:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        return False
    exponent = value.as_tuple().exponent
    if exponent < -9:
        return False
    return value * Decimal(1_000_000_000) <= _MAX_NANODOLLARS


def _validate_provenance(provenance) -> None:
    for label in (
        provenance.source_type,
        provenance.egress,
        provenance.machine,
    ):
        if not _safe_label(label):
            raise ExtractionContractRejected()
    for reference in (
        provenance.authentication_scope_ref,
        provenance.cookie_scope_ref,
        provenance.archive_ref,
    ):
        if reference is not None and not _safe_ref(reference):
            raise ExtractionContractRejected()


def _validate_step(step: ExtractorDecision) -> None:
    if (
        not isinstance(step, ExtractorDecision)
        or not _plain_int(step.ordinal, 0, 15)
        or not _safe_label(step.extractor)
        or not isinstance(step.decision, ExtractorExecutionDecision)
    ):
        raise ExtractionContractRejected()
    if step.policy_rule_ref is not None and not _safe_ref(step.policy_rule_ref):
        raise ExtractionContractRejected()
    if step.decision is ExtractorExecutionDecision.POLICY_SKIPPED:
        if any(
            value is not None
            for value in (
                step.attempt_outcome,
                step.latency_ms,
                step.provenance,
                step.spend,
            )
        ):
            raise ExtractionContractRejected()
        return
    if (
        not isinstance(step.attempt_outcome, AttemptOutcome)
        or not _plain_int(step.latency_ms, 0, _MAX_LATENCY_MS)
        or step.provenance is None
        or step.spend is None
    ):
        raise ExtractionContractRejected()
    _validate_provenance(step.provenance)
    if (
        not _valid_money(step.spend.actual_usd)
        or not _valid_money(step.spend.reserved_usd)
        or not _safe_ref(step.spend.spend_attempt_ref)
    ):
        raise ExtractionContractRejected()


def _validate_trace(raw: RawExtractionResult) -> tuple[ExtractorDecision, ...]:
    if not isinstance(raw.steps, tuple) or len(raw.steps) > 16:
        raise ExtractionContractRejected()
    for step in raw.steps:
        _validate_step(step)
    ordinals = tuple(step.ordinal for step in raw.steps)
    if ordinals != tuple(range(len(raw.steps))):
        raise ExtractionContractRejected()
    return tuple(
        step
        for step in raw.steps
        if step.decision is ExtractorExecutionDecision.INVOKED
    )


def _validate_terminal(
    terminal: TerminalCause | None,
    invoked: tuple[ExtractorDecision, ...],
) -> None:
    if terminal is None:
        return
    if not isinstance(terminal, TerminalCause):
        raise ExtractionContractRejected()
    invoked_by_ordinal = {step.ordinal: step for step in invoked}
    if terminal.kind is TerminalCauseKind.PREFLIGHT:
        if (
            terminal.preflight_outcome
            not in {
                CanonicalOutcome.INVALID_REQUEST,
                CanonicalOutcome.AUTHENTICATION_REJECTED,
                CanonicalOutcome.POLICY_REJECTED,
                CanonicalOutcome.UNREADY,
            }
            or not _safe_ref(terminal.authority_ref)
            or invoked
        ):
            raise ExtractionContractRejected()
    elif terminal.kind is TerminalCauseKind.OPERATION_DEADLINE:
        if not _safe_ref(terminal.deadline_ref):
            raise ExtractionContractRejected()
    elif terminal.kind is TerminalCauseKind.ATTEMPT_TERMINAL:
        step = invoked_by_ordinal.get(terminal.ordinal)
        if (
            step is None
            or not _safe_ref(terminal.policy_rule_ref)
            or step.policy_rule_ref != terminal.policy_rule_ref
        ):
            raise ExtractionContractRejected()
    elif terminal.kind is TerminalCauseKind.CHAIN_EXHAUSTED:
        actual_ordinals = tuple(step.ordinal for step in invoked)
        distinct = tuple(
            dict.fromkeys(step.attempt_outcome for step in invoked)
        )
        if (
            not actual_ordinals
            or terminal.invoked_ordinals != actual_ordinals
            or terminal.distinct_attempt_outcomes != distinct
        ):
            raise ExtractionContractRejected()
    else:
        raise ExtractionContractRejected()


def _validate_inputs(
    request: ExtractionRequest,
    plan: ExtractionPlan,
    raw: RawExtractionResult,
    policy: OutcomePolicy,
) -> tuple[ExtractorDecision, ...]:
    if (
        not isinstance(request, ExtractionRequest)
        or not isinstance(plan, ExtractionPlan)
        or not isinstance(raw, RawExtractionResult)
        or not isinstance(policy, OutcomePolicy)
    ):
        raise ExtractionContractRejected()
    caller_preflight_without_run = (
        request.extraction_run_id is None
        and raw.terminal_cause is not None
        and raw.terminal_cause.kind is TerminalCauseKind.PREFLIGHT
        and raw.terminal_cause.preflight_outcome
        in {
            CanonicalOutcome.INVALID_REQUEST,
            CanonicalOutcome.AUTHENTICATION_REJECTED,
        }
    )
    if (
        not _safe_ref(request.request_id, maximum=64)
        or (
            not caller_preflight_without_run
            and not _safe_ref(request.extraction_run_id, maximum=64)
        )
        or not _safe_ref(plan.plan_ref)
        or request.normalized_url != plan.normalized_url
        or request.access_scope != plan.access_scope
        or request.caller != plan.caller
        or request.profile != plan.profile
        or request.privacy_scope != plan.privacy_scope
        or not _safe_ref(plan.access_scope)
        or not _safe_ref(plan.caller, maximum=64)
        or not _safe_ref(plan.profile, maximum=64)
        or not _safe_ref(plan.privacy_scope, maximum=64)
        or not isinstance(plan.normalized_url, str)
        or len(plan.normalized_url.encode("utf-8")) > 2048
        or not plan.normalized_url.startswith(("http://", "https://"))
        or not _safe_label(plan.mode)
        or not _safe_ref(plan.cache_policy_ref)
        or not _safe_ref(plan.extraction_plan_version)
        or not _safe_ref(plan.quality_policy_version)
        or not _safe_ref(plan.completeness_policy_version)
        or not _safe_ref(policy.version)
        or type(plan.partial_allowed) is not bool
        or not _plain_int(plan.deadline_ms, 1, 120_000)
        or not _plain_int(raw.operation_latency_ms, 0, _MAX_LATENCY_MS)
    ):
        raise ExtractionContractRejected()
    if len(plan.candidates) > 16:
        raise ExtractionContractRejected()
    for candidate in plan.candidates:
        if (
            not _safe_label(candidate.extractor)
            or type(candidate.eligible) is not bool
            or not _safe_label(candidate.spend_class)
            or (
                candidate.policy_rule_ref is not None
                and not _safe_ref(candidate.policy_rule_ref)
            )
        ):
            raise ExtractionContractRejected()

    invoked = _validate_trace(raw)
    for step in raw.steps:
        if step.ordinal >= len(plan.candidates):
            raise ExtractionContractRejected()
        candidate = plan.candidates[step.ordinal]
        if (
            step.extractor != candidate.extractor
            or (
                step.decision is ExtractorExecutionDecision.INVOKED
                and not candidate.eligible
            )
            or (
                step.decision is ExtractorExecutionDecision.POLICY_SKIPPED
                and candidate.eligible
            )
        ):
            raise ExtractionContractRejected()
    cache = raw.cache_decision
    if not isinstance(cache, CacheDecision) or not isinstance(
        cache.outcome,
        CacheOutcome,
    ):
        raise ExtractionContractRejected()
    if cache.outcome is CacheOutcome.MISS:
        if cache.origin_run_ref is not None or cache.age_seconds is not None:
            raise ExtractionContractRejected()
    elif (
        not _safe_ref(cache.origin_run_ref, maximum=64)
        or not _plain_int(cache.age_seconds, 0, _MAX_LATENCY_MS)
    ):
        raise ExtractionContractRejected()
    if cache.outcome is CacheOutcome.HIT_ELIGIBLE and (
        raw.artifact is None or invoked
    ):
        raise ExtractionContractRejected()
    _validate_terminal(raw.terminal_cause, invoked)
    artifact = raw.artifact
    if artifact is not None:
        if (
            not _safe_ref(artifact.artifact_ref)
            or not _safe_ref(artifact.content_identity)
            or not isinstance(artifact.text, str)
            or not artifact.text.strip()
            or not isinstance(artifact.title, str)
            or not isinstance(artifact.author, str)
            or not _plain_int(artifact.word_count, 0, 2**31 - 1)
            or artifact.quality_passed not in {True, False, None}
            or artifact.is_complete not in {True, False, None}
            or len(artifact.completeness_signals) > 16
            or any(not _safe_label(signal) for signal in artifact.completeness_signals)
            or not _safe_ref(artifact.completeness_assessment_version)
            or artifact.completeness_recommended_action
            not in {"use_as_is", "try_full_fetch"}
        ):
            raise ExtractionContractRejected()
        if artifact.completeness_confidence is not None and (
            not isinstance(artifact.completeness_confidence, Decimal)
            or not artifact.completeness_confidence.is_finite()
            or not Decimal(0) <= artifact.completeness_confidence <= Decimal(1)
        ):
            raise ExtractionContractRejected()
        _validate_provenance(artifact.provenance)
        producers = {
            step.extractor
            for step in invoked
            if step.attempt_outcome is AttemptOutcome.CONTENT
        }
        candidate_names = {candidate.extractor for candidate in plan.candidates}
        producer_valid = (
            raw.selected_extractor in producers
            or (
                cache.outcome is CacheOutcome.HIT_ELIGIBLE
                and not producers
                and raw.selected_extractor in candidate_names
            )
        )
        terminal_valid = (
            raw.terminal_cause is None
            or (
                raw.terminal_cause.kind is TerminalCauseKind.OPERATION_DEADLINE
                and not (
                    artifact.quality_passed is True
                    and artifact.is_complete is True
                )
            )
        )
        if (
            not _safe_label(raw.selected_extractor)
            or not producer_valid
            or not terminal_valid
        ):
            raise ExtractionContractRejected()
    elif raw.selected_extractor is not None:
        raise ExtractionContractRejected()
    return invoked


def _artifact_decision(
    raw: RawExtractionResult,
    plan: ExtractionPlan,
) -> _Decision | None:
    artifact = raw.artifact
    if artifact is None:
        return None
    if artifact.quality_passed is not True:
        return _Decision(
            CanonicalOutcome.EXTRACTION_FAILED,
            ArtifactDisposition.DIAGNOSTIC_ONLY,
            RejectionCode.QUALITY_GATE_FAILED,
            raw.selected_extractor,
        )
    if artifact.is_complete is True:
        return _Decision(
            CanonicalOutcome.SUCCESS,
            ArtifactDisposition.USABLE,
            None,
            None,
        )
    if artifact.is_complete is False and plan.partial_allowed:
        return _Decision(
            CanonicalOutcome.DEGRADED,
            ArtifactDisposition.PARTIAL,
            RejectionCode.INCOMPLETE_CONTENT,
            raw.selected_extractor,
        )
    return _Decision(
        CanonicalOutcome.EXTRACTION_FAILED,
        ArtifactDisposition.DIAGNOSTIC_ONLY,
        RejectionCode.INCOMPLETE_CONTENT,
        raw.selected_extractor,
    )


def _terminal_decision(
    terminal: TerminalCause,
    invoked: tuple[ExtractorDecision, ...],
) -> _Decision:
    if terminal.kind is TerminalCauseKind.PREFLIGHT:
        outcome = terminal.preflight_outcome
        code = {
            CanonicalOutcome.POLICY_REJECTED: RejectionCode.UNSUPPORTED_SOURCE,
            CanonicalOutcome.UNREADY: RejectionCode.PROVIDER_UNAVAILABLE,
        }.get(outcome)
        return _Decision(outcome, ArtifactDisposition.NONE, code, None)
    if terminal.kind is TerminalCauseKind.OPERATION_DEADLINE:
        return _Decision(
            CanonicalOutcome.TIMEOUT,
            ArtifactDisposition.NONE,
            RejectionCode.TIMEOUT,
            None,
        )
    if terminal.kind is TerminalCauseKind.ATTEMPT_TERMINAL:
        step = next(step for step in invoked if step.ordinal == terminal.ordinal)
        outcome, code = _TERMINAL_ATTEMPT_MAPPING[step.attempt_outcome]
        return _Decision(outcome, ArtifactDisposition.NONE, code, step.extractor)

    distinct = terminal.distinct_attempt_outcomes
    if len(distinct) != 1:
        return _Decision(
            CanonicalOutcome.EXTRACTION_FAILED,
            ArtifactDisposition.NONE,
            RejectionCode.PROVIDER_UNAVAILABLE,
            None,
        )
    outcome, code = _TERMINAL_ATTEMPT_MAPPING[distinct[0]]
    provider = invoked[0].extractor if len(invoked) == 1 else None
    if distinct[0] is AttemptOutcome.CONTENT:
        provider = None
    return _Decision(outcome, ArtifactDisposition.NONE, code, provider)


class ExtractionFinalizer:
    """Validate, classify once, persist atomically, and return one accepted value."""

    def __init__(
        self,
        *,
        repository: ExtractionOutcomeRepository,
        rejection_mapper: RejectionMapper = map_extraction_rejection,
        clock: Callable[[], str],
    ):
        self.repository = repository
        self.rejection_mapper = rejection_mapper or map_extraction_rejection
        self.clock = clock

    def finalize_extraction(
        self,
        extraction_request: ExtractionRequest,
        extraction_plan: ExtractionPlan,
        raw_extractor_result: RawExtractionResult,
        outcome_policy: OutcomePolicy,
    ) -> AcceptedExtractionOutcome:
        invoked = _validate_inputs(
            extraction_request,
            extraction_plan,
            raw_extractor_result,
            outcome_policy,
        )
        if (
            extraction_request.extraction_run_id is None
            and raw_extractor_result.terminal_cause is not None
            and raw_extractor_result.terminal_cause.kind
            is TerminalCauseKind.PREFLIGHT
            and raw_extractor_result.terminal_cause.preflight_outcome
            in {
                CanonicalOutcome.INVALID_REQUEST,
                CanonicalOutcome.AUTHENTICATION_REJECTED,
            }
        ):
            raise ExtractionPreflightRejected(
                raw_extractor_result.terminal_cause.preflight_outcome
            )
        load_existing = getattr(self.repository, "load_extraction_outcome", None)
        if callable(load_existing):
            existing = load_existing(extraction_request.extraction_run_id)
            if existing is not None:
                current_source = (
                    outcome_policy.version,
                    extraction_request.request_id,
                    extraction_request.extraction_run_id,
                    extraction_plan,
                    raw_extractor_result.artifact,
                    raw_extractor_result.steps,
                    raw_extractor_result.terminal_cause,
                    raw_extractor_result.selected_extractor,
                    raw_extractor_result.cache_decision,
                    raw_extractor_result.operation_latency_ms,
                )
                durable_source = (
                    existing.extraction_outcome_policy_version,
                    existing.request_id,
                    existing.extraction_run_id,
                    existing.plan,
                    existing.artifact,
                    existing.steps,
                    existing.terminal_cause,
                    existing.selected_extractor,
                    existing.cache_decision,
                    existing.operation_latency_ms,
                )
                if current_source != durable_source:
                    raise ExtractionAcceptanceConflict()
                return existing
        if (
            raw_extractor_result.terminal_cause is not None
            and raw_extractor_result.terminal_cause.kind
            is TerminalCauseKind.OPERATION_DEADLINE
        ):
            decision = _terminal_decision(
                raw_extractor_result.terminal_cause,
                invoked,
            )
            if raw_extractor_result.artifact is not None:
                decision = _Decision(
                    outcome=decision.outcome,
                    disposition=ArtifactDisposition.DIAGNOSTIC_ONLY,
                    code=decision.code,
                    provider=decision.provider,
                )
        else:
            decision = _artifact_decision(raw_extractor_result, extraction_plan)
        if decision is None:
            if raw_extractor_result.terminal_cause is None:
                raise ExtractionContractRejected()
            decision = _terminal_decision(
                raw_extractor_result.terminal_cause,
                invoked,
            )

        rejection = None
        if decision.code is not None:
            last = invoked[-1] if invoked else None
            artifact = raw_extractor_result.artifact
            facts = RejectionFacts(
                code=decision.code,
                provider=decision.provider,
                quality_passed=(
                    artifact.quality_passed if artifact is not None else None
                ),
                is_complete=artifact.is_complete if artifact is not None else None,
                attempt_count=len(invoked),
                last_status=(
                    last.attempt_outcome.value
                    if last is not None and last.attempt_outcome is not None
                    else None
                ),
                total_latency_ms=sum(step.latency_ms or 0 for step in invoked),
                eligible_fallback_remains=(
                    outcome_policy.eligible_fallback_remains
                ),
                autonomous=outcome_policy.autonomous,
            )
            try:
                rejection = self.rejection_mapper(facts)
            except ExtractionContractRejected:
                raise
            except Exception as error:
                raise ExtractionContractRejected() from error
            if (
                not isinstance(rejection, ExtractionRejection)
                or (
                    outcome_policy.autonomous
                    and rejection.recommended_action is RejectionAction.MANUAL_REVIEW
                )
            ):
                raise ExtractionContractRejected()

        projection = FinalizedExtractionProjection(
            extraction_outcome_policy_version=outcome_policy.version,
            outcome=decision.outcome,
            artifact_disposition=decision.disposition,
            extraction_run_id=extraction_request.extraction_run_id,
            request_id=extraction_request.request_id,
            plan_ref=extraction_plan.plan_ref,
            plan=extraction_plan,
            artifact=raw_extractor_result.artifact,
            rejection=rejection,
            steps=raw_extractor_result.steps,
            terminal_cause=raw_extractor_result.terminal_cause,
            selected_extractor=raw_extractor_result.selected_extractor,
            cache_decision=raw_extractor_result.cache_decision,
            operation_latency_ms=raw_extractor_result.operation_latency_ms,
        )
        try:
            receipt = self.repository.accept_extraction_outcome(projection)
        except ExtractionAcceptanceConflict:
            raise
        except Exception as error:
            raise ExtractionPersistenceFailed() from error
        if (
            not isinstance(receipt, ExtractionAcceptanceReceipt)
            or not _safe_ref(receipt.receipt_ref)
            or not isinstance(receipt.accepted_at, str)
            or not receipt.accepted_at
            or not _safe_label(receipt.scope)
        ):
            raise ExtractionPersistenceFailed()
        return AcceptedExtractionOutcome.accepted(projection, receipt)


def finalize_extraction(
    extraction_request: ExtractionRequest,
    extraction_plan: ExtractionPlan,
    raw_extractor_result: RawExtractionResult,
    outcome_policy: OutcomePolicy,
    *,
    repository: ExtractionOutcomeRepository | None = None,
    rejection_mapper: RejectionMapper | None = None,
    clock: Callable[[], str] | None = None,
) -> AcceptedExtractionOutcome:
    """Functional facade for the required finalization interface."""
    actual_repository = repository or outcome_policy.repository
    if actual_repository is None:
        raise ExtractionPersistenceFailed()
    return ExtractionFinalizer(
        repository=actual_repository,
        rejection_mapper=(
            rejection_mapper
            or outcome_policy.rejection_mapper
            or map_extraction_rejection
        ),
        clock=clock or (lambda: ""),
    ).finalize_extraction(
        extraction_request,
        extraction_plan,
        raw_extractor_result,
        outcome_policy,
    )
