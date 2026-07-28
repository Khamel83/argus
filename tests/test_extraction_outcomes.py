from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from decimal import Decimal

import pytest

from argus.contracts import CanonicalOutcome
from argus.extraction.models import ExtractedContent, ExtractorName
from argus.extraction.outcomes import (
    ArtifactDisposition,
    ArtifactEvaluation,
    AttemptOutcome,
    CacheDecision,
    CacheOutcome,
    ExtractionCandidate,
    ExtractionPlan,
    ExtractionProvenance,
    ExtractionRequest,
    ExtractorDecision,
    ExtractorExecutionDecision,
    OutcomePolicy,
    RawExtractionResult,
    SpendEvidence,
    TerminalCause,
    TerminalCauseKind,
)
from argus.extraction.rejection import RejectionAction, RejectionCode


class MemoryOutcomeRepository:
    def __init__(self):
        self.projections = []

    def accept_extraction_outcome(self, projection):
        from argus.extraction.outcomes import ExtractionAcceptanceReceipt

        self.projections.append(projection)
        self.receipt = ExtractionAcceptanceReceipt(
            receipt_ref=f"receipt-{projection.extraction_run_id}",
            accepted_at="2026-07-27T12:00:00Z",
            scope="test",
        )
        return self.receipt

    def load_extraction_outcome_by_receipt(self, receipt_ref):
        from argus.extraction.outcomes import AcceptedExtractionOutcome

        if (
            not self.projections
            or self.receipt.receipt_ref != receipt_ref
        ):
            return None
        return AcceptedExtractionOutcome.accepted(
            self.projections[-1],
            self.receipt,
        )


def _request(run_id: str = "extract-1"):
    return ExtractionRequest(
        request_id="request-1",
        extraction_run_id=run_id,
        normalized_url="https://example.com/article",
        access_scope="public",
        caller="test",
        profile="autonomous",
        privacy_scope="public",
    )


def _plan(*, partial_allowed: bool = False, candidates=("trafilatura", "jina")):
    return ExtractionPlan(
        plan_ref="plan-1",
        normalized_url="https://example.com/article",
        access_scope="public",
        mode="default",
        candidates=tuple(
            ExtractionCandidate(
                extractor=name,
                eligible=True,
                spend_class="free",
            )
            for name in candidates
        ),
        cache_policy_ref="cache-v1",
        extraction_plan_version="1",
        quality_policy_version="quality-v1",
        completeness_policy_version="complete-v1",
        partial_allowed=partial_allowed,
        deadline_ms=30_000,
        caller="test",
        profile="autonomous",
        privacy_scope="public",
    )


def _step(
    ordinal: int,
    outcome: AttemptOutcome,
    *,
    extractor: str = "trafilatura",
):
    return ExtractorDecision(
        ordinal=ordinal,
        extractor=extractor,
        decision=ExtractorExecutionDecision.INVOKED,
        attempt_outcome=outcome,
        latency_ms=10,
        provenance=ExtractionProvenance(
            source_type="normalized_text",
            egress="local",
            machine="test_node",
        ),
        spend=SpendEvidence(
            actual_usd=Decimal("0"),
            reserved_usd=Decimal("0"),
            spend_attempt_ref=f"spend-{ordinal}",
        ),
    )


def _artifact(*, quality=True, complete=True):
    return ArtifactEvaluation(
        artifact_ref="artifact-1",
        content_identity="sha256:" + ("a" * 64),
        text="normalized artifact",
        title="Article",
        author="Ada",
        published_date="2026-07-27",
        word_count=2,
        quality_passed=quality,
        is_complete=complete,
        completeness_confidence=Decimal("1"),
        completeness_signals=("ending_punctuation",),
        completeness_assessment_version="complete-v1",
        completeness_recommended_action="use_as_is",
        provenance=ExtractionProvenance(
            source_type="normalized_text",
            egress="local",
            machine="test_node",
        ),
    )


def _raw(*, artifact=None, steps=None, terminal_cause=None, selected="trafilatura"):
    return RawExtractionResult(
        cache_decision=CacheDecision(outcome=CacheOutcome.MISS),
        steps=tuple(steps or (_step(0, AttemptOutcome.CONTENT),)),
        artifact=artifact,
        selected_extractor=selected if artifact is not None else None,
        terminal_cause=terminal_cause,
        operation_latency_ms=12,
    )


def _finalize(raw, *, plan=None, mapper=None, repository=None):
    from argus.extraction.finalizer import ExtractionFinalizer

    repository = repository or MemoryOutcomeRepository()
    finalizer = ExtractionFinalizer(
        repository=repository,
        rejection_mapper=mapper,
        clock=lambda: "2026-07-27T12:00:00Z",
    )
    return finalizer.finalize_extraction(
        _request(),
        plan or _plan(),
        raw,
        OutcomePolicy(version="outcome-v1", autonomous=True),
    )


@pytest.mark.parametrize(
    ("quality", "complete", "partial_allowed", "outcome", "disposition", "code"),
    [
        (True, True, False, "success", "usable", None),
        (True, False, True, "degraded", "partial", "incomplete_content"),
        (
            True,
            False,
            False,
            "extraction_failed",
            "diagnostic_only",
            "incomplete_content",
        ),
        (
            True,
            None,
            False,
            "extraction_failed",
            "diagnostic_only",
            "incomplete_content",
        ),
        (
            False,
            True,
            False,
            "extraction_failed",
            "diagnostic_only",
            "quality_gate_failed",
        ),
        (
            None,
            True,
            False,
            "extraction_failed",
            "diagnostic_only",
            "quality_gate_failed",
        ),
    ],
)
def test_quality_completeness_truth_table(
    quality, complete, partial_allowed, outcome, disposition, code
):
    accepted = _finalize(
        _raw(artifact=_artifact(quality=quality, complete=complete)),
        plan=_plan(partial_allowed=partial_allowed),
    )

    assert accepted.outcome.value == outcome
    assert accepted.artifact_disposition.value == disposition
    assert (
        accepted.rejection.code.value if accepted.rejection is not None else None
    ) == code


TERMINAL_MAPPING = {
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


@pytest.mark.parametrize(("attempt_outcome", "expected"), TERMINAL_MAPPING.items())
def test_closed_terminal_attempt_mapping(attempt_outcome, expected):
    terminal = TerminalCause(
        kind=TerminalCauseKind.CHAIN_EXHAUSTED,
        invoked_ordinals=(0,),
        distinct_attempt_outcomes=(attempt_outcome,),
    )
    accepted = _finalize(
        _raw(
            artifact=None,
            selected=None,
            steps=(_step(0, attempt_outcome),),
            terminal_cause=terminal,
        ),
        plan=_plan(candidates=("trafilatura",)),
    )

    assert (accepted.outcome, accepted.rejection.code) == expected
    assert accepted.rejection.recommended_action is not RejectionAction.MANUAL_REVIEW


def test_attempt_taxonomy_is_exhaustively_mapped():
    assert set(TERMINAL_MAPPING) == set(AttemptOutcome)


@pytest.mark.parametrize(
    "outcome",
    [
        CanonicalOutcome.INVALID_REQUEST,
        CanonicalOutcome.AUTHENTICATION_REJECTED,
    ],
)
def test_preflight_caller_rejection_has_no_run_rejection_mapper_or_publication(outcome):
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import ExtractionPreflightRejected

    repository = MemoryOutcomeRepository()
    mapper_calls = []
    raw = RawExtractionResult(
        cache_decision=CacheDecision(outcome=CacheOutcome.MISS),
        steps=(),
        artifact=None,
        selected_extractor=None,
        terminal_cause=TerminalCause(
            kind=TerminalCauseKind.PREFLIGHT,
            preflight_outcome=outcome,
            authority_ref="caller-boundary-1",
        ),
        operation_latency_ms=0,
    )
    request = replace(_request(), extraction_run_id=None)

    with pytest.raises(ExtractionPreflightRejected) as raised:
        ExtractionFinalizer(
            repository=repository,
            rejection_mapper=lambda facts: mapper_calls.append(facts),
            clock=lambda: "2026-07-27T12:00:00Z",
        ).finalize_extraction(
            request,
            _plan(),
            raw,
            OutcomePolicy(version="outcome-v1"),
        )

    assert raised.value.outcome is outcome
    assert repository.projections == []
    assert mapper_calls == []
    assert not hasattr(raised.value, "extraction_run_id")
    assert not hasattr(raised.value, "rejection")


def test_operation_deadline_and_heterogeneous_exhaustion_are_closed():
    deadline = _finalize(
        _raw(
            artifact=None,
            selected=None,
            steps=(_step(0, AttemptOutcome.TIMEOUT),),
            terminal_cause=TerminalCause(
                kind=TerminalCauseKind.OPERATION_DEADLINE,
                deadline_ref="deadline-1",
            ),
        )
    )
    heterogeneous = _finalize(
        _raw(
            artifact=None,
            selected=None,
            steps=(
                _step(0, AttemptOutcome.PARSE_ERROR),
                _step(1, AttemptOutcome.RATE_LIMITED, extractor="jina"),
            ),
            terminal_cause=TerminalCause(
                kind=TerminalCauseKind.CHAIN_EXHAUSTED,
                invoked_ordinals=(0, 1),
                distinct_attempt_outcomes=(
                    AttemptOutcome.PARSE_ERROR,
                    AttemptOutcome.RATE_LIMITED,
                ),
            ),
        )
    )

    assert (deadline.outcome, deadline.rejection.code) == (
        CanonicalOutcome.TIMEOUT,
        RejectionCode.TIMEOUT,
    )
    assert (heterogeneous.outcome, heterogeneous.rejection.code) == (
        CanonicalOutcome.EXTRACTION_FAILED,
        RejectionCode.PROVIDER_UNAVAILABLE,
    )
    assert heterogeneous.rejection.provider is None


def test_operation_deadline_may_retain_only_diagnostic_artifact():
    diagnostic = _finalize(
        _raw(
            artifact=_artifact(quality=False, complete=None),
            terminal_cause=TerminalCause(
                kind=TerminalCauseKind.OPERATION_DEADLINE,
                deadline_ref="deadline-1",
            ),
        )
    )

    assert diagnostic.outcome is CanonicalOutcome.TIMEOUT
    assert diagnostic.artifact_disposition is ArtifactDisposition.DIAGNOSTIC_ONLY
    assert diagnostic.rejection.code is RejectionCode.TIMEOUT
    assert diagnostic.artifact.text == "normalized artifact"


def test_fallback_success_retains_failed_steps_without_final_rejection():
    steps = (
        _step(0, AttemptOutcome.TIMEOUT),
        _step(1, AttemptOutcome.CONTENT, extractor="jina"),
    )
    accepted = _finalize(
        _raw(
            artifact=replace(
                _artifact(),
                provenance=ExtractionProvenance(
                    source_type="paid_api",
                    egress="datacenter",
                    machine="test_node",
                ),
            ),
            steps=steps,
            selected="jina",
        )
    )

    assert accepted.outcome is CanonicalOutcome.SUCCESS
    assert accepted.rejection is None
    assert accepted.steps == steps
    assert accepted.selected_extractor == "jina"


def test_eligible_cache_hit_is_a_decision_not_an_extractor_attempt():
    from argus.extraction.cache import ExtractionCacheIdentity
    from argus.extraction.outcomes import CacheOriginEvidence

    repository = MemoryOutcomeRepository()
    origin = _finalize(
        _raw(artifact=_artifact()),
        repository=repository,
    )
    accepted = _finalize(
        RawExtractionResult(
            cache_decision=CacheDecision(
                outcome=CacheOutcome.HIT_ELIGIBLE,
                origin_run_ref=origin.extraction_run_id,
                age_seconds=0,
                origin_evidence=CacheOriginEvidence.from_accepted(
                    origin,
                    acceptance_repository=repository,
                ),
                current_identity=ExtractionCacheIdentity.from_accepted(origin),
            ),
            steps=(),
            artifact=_artifact(),
            selected_extractor="trafilatura",
            terminal_cause=None,
            operation_latency_ms=1,
        ),
        repository=repository,
    )

    assert accepted.outcome is CanonicalOutcome.SUCCESS
    assert accepted.steps == ()
    assert accepted.cache_decision.origin_run_ref == origin.extraction_run_id


def test_cache_decision_integrity_fails_closed():
    from argus.extraction.outcomes import ExtractionContractRejected

    with pytest.raises(ExtractionContractRejected):
        _finalize(
            RawExtractionResult(
                cache_decision=CacheDecision(
                    outcome=CacheOutcome.HIT_ELIGIBLE,
                    origin_run_ref=None,
                    age_seconds=-1,
                ),
                steps=(),
                artifact=_artifact(),
                selected_extractor="trafilatura",
                terminal_cause=None,
                operation_latency_ms=1,
            )
        )


@pytest.mark.parametrize(
    "mutator",
    [
        lambda raw: replace(raw, steps=raw.steps + (_step(1, AttemptOutcome.EMPTY),) * 16),
        lambda raw: replace(raw, steps=(_step(1, AttemptOutcome.CONTENT),)),
        lambda raw: replace(
            raw,
            steps=(
                _step(0, AttemptOutcome.EMPTY),
                _step(0, AttemptOutcome.EMPTY),
            ),
        ),
        lambda raw: replace(
            raw,
            terminal_cause=TerminalCause(
                kind=TerminalCauseKind.CHAIN_EXHAUSTED,
                invoked_ordinals=(4,),
                distinct_attempt_outcomes=(AttemptOutcome.EMPTY,),
            ),
        ),
    ],
    ids=["seventeen-steps", "dangling-ordinal", "duplicate-ordinal", "bad-exhausted-set"],
)
def test_invalid_internal_trace_fails_closed_without_publication(mutator):
    from argus.extraction.outcomes import ExtractionContractRejected

    repository = MemoryOutcomeRepository()
    raw = _raw(artifact=_artifact())
    with pytest.raises(ExtractionContractRejected) as raised:
        _finalize(mutator(raw), repository=repository)

    assert raised.value.outcome is CanonicalOutcome.EXTRACTION_FAILED
    assert raised.value.code is RejectionCode.PROVIDER_UNAVAILABLE
    assert raised.value.recommended_action is RejectionAction.TERMINAL
    assert repository.projections == []
    assert "https://" not in str(raised.value)


def test_sixteenth_step_is_inclusively_valid():
    names = tuple(f"extractor_{ordinal}" for ordinal in range(16))
    steps = tuple(
        _step(
            ordinal,
            AttemptOutcome.CONTENT if ordinal == 15 else AttemptOutcome.EMPTY,
            extractor=names[ordinal],
        )
        for ordinal in range(16)
    )
    accepted = _finalize(
        _raw(artifact=_artifact(), steps=steps, selected=names[-1]),
        plan=_plan(candidates=names),
    )

    assert len(accepted.steps) == 16
    assert accepted.steps[-1].ordinal == 15


def test_step_must_match_ordered_plan_candidate_and_eligibility():
    from argus.extraction.outcomes import ExtractionContractRejected

    mismatched = _raw(
        artifact=_artifact(),
        steps=(_step(0, AttemptOutcome.CONTENT, extractor="jina"),),
        selected="jina",
    )
    with pytest.raises(ExtractionContractRejected):
        _finalize(mismatched, plan=_plan(candidates=("trafilatura", "jina")))

    ineligible_plan = replace(
        _plan(candidates=("trafilatura",)),
        candidates=(
            ExtractionCandidate(
                extractor="trafilatura",
                eligible=False,
                spend_class="free",
            ),
        ),
    )
    with pytest.raises(ExtractionContractRejected):
        _finalize(
            _raw(artifact=_artifact()),
            plan=ineligible_plan,
        )


@pytest.mark.parametrize(
    "bad",
    [
        replace(_artifact(), artifact_ref="x" * 129),
        replace(_artifact(), completeness_signals=("x" * 65,)),
        replace(
            _artifact(),
            provenance=ExtractionProvenance(
                source_type="https://example.com/private",
                egress="local",
                machine="test_node",
            ),
        ),
    ],
)
def test_bounds_and_privacy_labels_fail_closed(bad):
    from argus.extraction.outcomes import ExtractionContractRejected

    with pytest.raises(ExtractionContractRejected) as raised:
        _finalize(_raw(artifact=bad))

    rendered = str(raised.value)
    assert "example.com" not in rendered
    assert "x" * 65 not in rendered


def test_rejection_mapper_is_called_exactly_once_and_persisted_identically():
    calls = []

    def mapper(facts):
        from argus.extraction.finalizer import map_extraction_rejection

        calls.append(facts)
        return map_extraction_rejection(facts)

    repository = MemoryOutcomeRepository()
    accepted = _finalize(
        _raw(
            artifact=None,
            selected=None,
            steps=(_step(0, AttemptOutcome.EMPTY),),
            terminal_cause=TerminalCause(
                kind=TerminalCauseKind.CHAIN_EXHAUSTED,
                invoked_ordinals=(0,),
                distinct_attempt_outcomes=(AttemptOutcome.EMPTY,),
            ),
        ),
        mapper=mapper,
        repository=repository,
        plan=_plan(candidates=("trafilatura",)),
    )

    assert len(calls) == 1
    assert repository.projections[0].rejection is accepted.rejection
    assert accepted.acceptance_receipt.receipt_ref == "receipt-extract-1"


def test_persistence_failure_has_no_fabricated_receipt():
    from argus.extraction.outcomes import ExtractionPersistenceFailed

    class FailingRepository:
        def accept_extraction_outcome(self, projection):
            raise RuntimeError("injected database failure with secret=do-not-copy")

    with pytest.raises(ExtractionPersistenceFailed) as raised:
        _finalize(_raw(artifact=_artifact()), repository=FailingRepository())

    assert raised.value.outcome is CanonicalOutcome.PERSISTENCE_FAILED
    assert not hasattr(raised.value, "acceptance_receipt")
    assert "secret" not in str(raised.value)


def test_outcome_values_are_immutable_and_project_to_legacy_content():
    accepted = _finalize(_raw(artifact=_artifact()))

    with pytest.raises(FrozenInstanceError):
        accepted.outcome = CanonicalOutcome.DEGRADED

    legacy = accepted.to_legacy_extracted_content()
    assert isinstance(legacy, ExtractedContent)
    assert legacy.extractor is ExtractorName.TRAFILATURA
    assert legacy.text == "normalized artifact"
    assert legacy.completeness_result.recommended_action == "use_as_is"
    assert legacy.rejection is None


def test_cache_identity_isolates_access_and_policy_and_rejects_diagnostic_content():
    from argus.extraction.cache import ExtractionCache, ExtractionCacheIdentity

    repository = MemoryOutcomeRepository()
    usable = _finalize(_raw(artifact=_artifact()), repository=repository)
    cache = ExtractionCache(acceptance_repository=repository)
    public = ExtractionCacheIdentity.from_accepted(usable)
    private = replace(
        public,
        access_scope="private",
        authentication_scope_fingerprint="account-a",
    )
    cache.put(public, usable)

    assert cache.get(public) is usable
    assert cache.get(private) is None
    assert cache.get(
        replace(public, outcome_policy_version="outcome-v2")
    ) is None

    diagnostic = replace(
        usable,
        artifact_disposition=ArtifactDisposition.DIAGNOSTIC_ONLY,
    )
    cache.put(replace(public, extraction_plan_version="2"), diagnostic)
    assert cache.get(replace(public, extraction_plan_version="2")) is None


@pytest.mark.asyncio
async def test_archive_lookup_miss_never_posts(monkeypatch):
    import argus.extraction.archive_extractor as archive

    async def miss(url):
        return None

    async def forbidden_post(*args, **kwargs):
        raise AssertionError("archive creation POST is outside the default chain")

    monkeypatch.setattr(archive, "_search_existing", miss)
    monkeypatch.setattr(archive, "_submit_and_fetch", forbidden_post)

    result = await archive.extract_archive_is("https://example.com/article")

    assert result.error == "archive_is: no existing archive found"


@pytest.mark.asyncio
async def test_archive_creation_requires_distinct_one_use_authorization(monkeypatch):
    import argus.extraction.archive_extractor as archive

    calls = []

    async def submitted(url):
        calls.append(url)
        return "https://archive.ph/bounded/example"

    monkeypatch.setattr(archive, "_submit_authorized", submitted)
    class Authority:
        def verify(self, authorization):
            return True

    class Store:
        def __init__(self):
            self.used = set()

        def consume(self, *, receipt, idempotency_key, target):
            identity = (receipt, idempotency_key, target)
            if identity in self.used:
                return False
            self.used.add(identity)
            return True

    store = Store()
    authorization = archive.ArchiveCreationAuthorization(
        caller_policy_ref="archive-policy-v1",
        authority_receipt="authority-receipt-1",
        idempotency_key="archive-create-1",
        bounded_target="https://example.com/article",
        profile="operator",
    )

    created = await archive.create_archive(
        "https://example.com/article",
        authorization=authorization,
        authority=Authority(),
        authorization_store=store,
    )
    with pytest.raises(archive.ArchiveCreationPolicyRejected):
        await archive.create_archive(
            "https://example.com/article",
            authorization=authorization,
            authority=Authority(),
            authorization_store=store,
        )

    assert created == "https://archive.ph/bounded/example"
    assert calls == ["https://example.com/article"]


def test_sql_repository_atomically_accepts_projection_and_idempotently_reloads(
    tmp_path,
):
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'outcomes.db'}",
        create_schema=True,
    )
    mapper_calls = []

    def mapper(facts):
        from argus.extraction.finalizer import map_extraction_rejection

        mapper_calls.append(facts)
        return map_extraction_rejection(facts)

    finalizer = ExtractionFinalizer(
        repository=repository,
        rejection_mapper=mapper,
        clock=lambda: "2026-07-27T12:00:00Z",
    )
    first = finalizer.finalize_extraction(
        _request(),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    second = finalizer.finalize_extraction(
        _request(),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )

    assert second == first
    snapshot = repository.load_extraction_outcome("extract-1")
    assert snapshot.outcome is CanonicalOutcome.SUCCESS
    assert snapshot.rejection is None
    assert snapshot.acceptance_receipt == first.acceptance_receipt
    assert mapper_calls == []

    from sqlalchemy import func, select
    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomeArtifactRow,
        ExtractionOutcomePlanRow,
        ExtractionOutcomeStepRow,
    )

    with repository.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(ExtractionOutcomePlanRow)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ExtractionOutcomeStepRow)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ExtractionOutcomeArtifactRow)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ExtractionOutcomeAcceptanceRow)
        ) == 1


def test_idempotent_rejected_retry_does_not_reinvoke_mapper_and_conflicts_on_source(
    tmp_path,
):
    from argus.extraction.finalizer import ExtractionFinalizer, map_extraction_rejection
    from argus.extraction.outcomes import ExtractionAcceptanceConflict
    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'retry.db'}",
        create_schema=True,
    )
    calls = []

    def mapper(facts):
        calls.append(facts)
        return map_extraction_rejection(facts)

    finalizer = ExtractionFinalizer(
        repository=repository,
        rejection_mapper=mapper,
        clock=lambda: "2026-07-27T12:00:00Z",
    )
    raw = _raw(
        artifact=None,
        selected=None,
        steps=(_step(0, AttemptOutcome.EMPTY),),
        terminal_cause=TerminalCause(
            kind=TerminalCauseKind.CHAIN_EXHAUSTED,
            invoked_ordinals=(0,),
            distinct_attempt_outcomes=(AttemptOutcome.EMPTY,),
        ),
    )

    first = finalizer.finalize_extraction(
        _request(),
        _plan(candidates=("trafilatura",)),
        raw,
        OutcomePolicy(version="outcome-v1"),
    )
    second = finalizer.finalize_extraction(
        _request(),
        _plan(candidates=("trafilatura",)),
        raw,
        OutcomePolicy(version="outcome-v1"),
    )

    assert second == first
    assert len(calls) == 1
    with pytest.raises(ExtractionAcceptanceConflict):
        finalizer.finalize_extraction(
            _request(),
            replace(
                _plan(candidates=("trafilatura",)),
                quality_policy_version="quality-v2",
            ),
            raw,
            OutcomePolicy(version="outcome-v1"),
        )
    assert len(calls) == 1


def test_sql_repository_rolls_back_projection_on_artifact_fault(tmp_path):
    from sqlalchemy import event, func, select

    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import ExtractionPersistenceFailed
    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomeArtifactRow,
        ExtractionOutcomePlanRow,
        create_search_ledger_repository,
    )

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'rollback.db'}",
        create_schema=True,
    )

    @event.listens_for(ExtractionOutcomeArtifactRow, "before_insert")
    def fail_artifact(mapper, connection, target):
        raise RuntimeError("injected failure")

    with pytest.raises(ExtractionPersistenceFailed):
        ExtractionFinalizer(
            repository=repository,
            clock=lambda: "2026-07-27T12:00:00Z",
        ).finalize_extraction(
            _request(),
            _plan(candidates=("trafilatura",)),
            _raw(artifact=_artifact()),
            OutcomePolicy(version="outcome-v1"),
        )

    event.remove(ExtractionOutcomeArtifactRow, "before_insert", fail_artifact)
    with repository.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(ExtractionOutcomePlanRow)
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(ExtractionOutcomeAcceptanceRow)
        ) == 0


def test_0007_sqlite_upgrade_is_additive_and_guarded_after_activation(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path / 'migration.db'}"
    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "0006_maya_outbox")
    engine = create_engine(url)
    legacy_tables = set(inspect(engine).get_table_names())
    command.upgrade(config, "head")
    upgraded = set(inspect(engine).get_table_names())
    expected = {
        "extraction_outcome_plans",
        "extraction_outcome_steps",
        "extraction_artifact_identities",
        "extraction_outcome_artifacts",
        "extraction_outcome_rejections",
        "extraction_outcome_acceptances",
        "result_extraction_links",
        "retrieval_compositions",
        "extraction_outcome_activations",
    }
    assert expected <= upgraded
    assert legacy_tables <= upgraded

    command.downgrade(config, "0006_maya_outbox")
    assert set(inspect(engine).get_table_names()) == legacy_tables
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO extraction_outcome_activations "
                "(receipt_ref, activated_at) VALUES "
                "('activation-receipt-1', '2026-07-27 12:00:00')"
            )
        )

    with pytest.raises(RuntimeError, match="activation receipt"):
        command.downgrade(config, "0006_maya_outbox")

    assert expected <= set(inspect(engine).get_table_names())


def test_0007_postgresql_upgrade_and_pre_activation_rollback(
    postgres_ledger_url,
):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "0006_maya_outbox")
    command.upgrade(config, "head")
    engine = create_engine(postgres_ledger_url)
    inspector = inspect(engine)
    assert "extraction_outcome_plans" in inspector.get_table_names()
    composite_fks = [
        foreign_key
        for foreign_key in inspector.get_foreign_keys(
            "result_extraction_links"
        )
        if len(foreign_key["constrained_columns"]) == 2
    ]
    assert len(composite_fks) == 3
    assert all(
        foreign_key["options"].get("match") == "FULL"
        for foreign_key in composite_fks
    )
    assert {
        "ck_result_extraction_links_acceptance_pair",
        "ck_result_extraction_links_artifact_pair",
        "ck_result_extraction_links_rejection_pair",
    } <= {
        check["name"]
        for check in inspector.get_check_constraints(
            "result_extraction_links"
        )
    }
    command.downgrade(config, "0006_maya_outbox")
    assert "extraction_outcome_plans" not in inspect(engine).get_table_names()


def test_issue57_mapper_is_sole_rejection_code_classifier():
    seen = []

    def mapper(facts):
        from argus.extraction.finalizer import map_extraction_rejection

        seen.append(facts)
        assert not hasattr(facts, "code")
        return map_extraction_rejection(facts)

    accepted = _finalize(
        _raw(artifact=_artifact(quality=False, complete=None)),
        mapper=mapper,
    )

    assert accepted.rejection.code is RejectionCode.QUALITY_GATE_FAILED
    assert len(seen) == 1


def test_chain_exhaustion_must_close_every_eligible_plan_candidate():
    from argus.extraction.outcomes import ExtractionContractRejected

    raw = _raw(
        artifact=None,
        steps=(_step(0, AttemptOutcome.EMPTY),),
        terminal_cause=TerminalCause(
            kind=TerminalCauseKind.CHAIN_EXHAUSTED,
            invoked_ordinals=(0,),
            distinct_attempt_outcomes=(AttemptOutcome.EMPTY,),
        ),
        selected=None,
    )

    with pytest.raises(ExtractionContractRejected):
        _finalize(raw, plan=_plan(candidates=("trafilatura", "jina")))


def test_attempt_terminal_rule_is_bound_to_plan_and_closes_variant_fields():
    from argus.extraction.outcomes import ExtractionContractRejected

    plan = replace(
        _plan(candidates=("trafilatura",)),
        candidates=(
            ExtractionCandidate(
                extractor="trafilatura",
                eligible=True,
                spend_class="free",
                policy_rule_ref="stop-rule-1",
            ),
        ),
    )
    step = replace(_step(0, AttemptOutcome.PARSE_ERROR), policy_rule_ref="invented")
    terminal = TerminalCause(
        kind=TerminalCauseKind.ATTEMPT_TERMINAL,
        ordinal=0,
        policy_rule_ref="invented",
        deadline_ref="contradictory-deadline",
    )

    with pytest.raises(ExtractionContractRejected):
        _finalize(
            _raw(
                artifact=None,
                steps=(step,),
                terminal_cause=terminal,
                selected=None,
            ),
            plan=plan,
        )


def test_fallback_eligibility_is_derived_from_unvisited_immutable_plan():
    plan = replace(
        _plan(candidates=("trafilatura", "jina")),
        candidates=(
            ExtractionCandidate(
                extractor="trafilatura",
                eligible=True,
                spend_class="free",
                policy_rule_ref="stop-rule-1",
            ),
            ExtractionCandidate(
                extractor="jina",
                eligible=True,
                spend_class="monthly",
            ),
        ),
    )
    step = replace(
        _step(0, AttemptOutcome.PARSE_ERROR),
        policy_rule_ref="stop-rule-1",
    )
    terminal = TerminalCause(
        kind=TerminalCauseKind.ATTEMPT_TERMINAL,
        ordinal=0,
        policy_rule_ref="stop-rule-1",
    )

    accepted = _finalize(
        _raw(
            artifact=None,
            steps=(step,),
            terminal_cause=terminal,
            selected=None,
        ),
        plan=plan,
    )

    assert (
        accepted.rejection.recommended_action
        is RejectionAction.FALLBACK_PROVIDER
    )


def test_mapper_output_must_exactly_match_bounded_source_facts():
    from argus.extraction.outcomes import ExtractionContractRejected
    from argus.extraction.rejection import ExtractionRejection

    def unsafe_mapper(facts):
        from argus.extraction.rejection import classify_typed_extraction_rejection

        safe = classify_typed_extraction_rejection(facts)
        return ExtractionRejection(
            code=safe.code,
            provider="https://user:secret@example.test/?token=raw",
            quality_passed=safe.quality_passed,
            is_complete=safe.is_complete,
            recommended_action=safe.recommended_action,
            attempt_count=safe.attempt_count + 1,
            last_status="raw bearer credential",
            total_latency_ms=safe.total_latency_ms,
        )

    with pytest.raises(ExtractionContractRejected):
        _finalize(
            _raw(artifact=_artifact(quality=False, complete=None)),
            mapper=unsafe_mapper,
        )


def test_aggregate_latency_over_signed_64_is_rejected_before_mapper():
    from argus.extraction.outcomes import ExtractionContractRejected

    calls = []
    steps = (
        replace(_step(0, AttemptOutcome.EMPTY), latency_ms=2**63 - 1),
        replace(
            _step(1, AttemptOutcome.EMPTY, extractor="jina"),
            latency_ms=1,
        ),
    )
    terminal = TerminalCause(
        kind=TerminalCauseKind.CHAIN_EXHAUSTED,
        invoked_ordinals=(0, 1),
        distinct_attempt_outcomes=(AttemptOutcome.EMPTY,),
    )

    with pytest.raises(ExtractionContractRejected):
        _finalize(
            _raw(
                artifact=None,
                steps=steps,
                terminal_cause=terminal,
                selected=None,
            ),
            mapper=lambda facts: calls.append(facts),
        )

    assert calls == []


def test_concurrent_same_run_classifies_once_and_reloads_committed_outcome(
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Lock

    from argus.extraction.finalizer import (
        ExtractionFinalizer,
        map_extraction_rejection,
    )
    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'concurrent-outcome.db'}"
    )
    calls = 0
    calls_lock = Lock()

    def mapper(facts):
        nonlocal calls
        with calls_lock:
            calls += 1
        return map_extraction_rejection(facts)

    finalizer = ExtractionFinalizer(
        repository=repository,
        rejection_mapper=mapper,
        clock=lambda: "2026-07-27T12:00:00Z",
    )
    raw = _raw(artifact=_artifact(quality=False, complete=None))

    def run():
        return finalizer.finalize_extraction(
            _request(),
            _plan(),
            raw,
            OutcomePolicy(version="outcome-v1", autonomous=True),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: run(), range(2)))

    assert calls == 1
    assert outcomes[0] == outcomes[1]


def test_distinct_runs_can_reuse_identical_artifact_and_rejection_refs(tmp_path):
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'reuse.db'}"
    )
    finalizer = ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    )
    policy = OutcomePolicy(version="outcome-v1", autonomous=True)

    successes = [
        finalizer.finalize_extraction(
            _request(f"extract-{ordinal}"),
            _plan(),
            _raw(artifact=_artifact()),
            policy,
        )
        for ordinal in (1, 2)
    ]

    exhausted = _raw(
        artifact=None,
        steps=(_step(0, AttemptOutcome.EMPTY),),
        terminal_cause=TerminalCause(
            kind=TerminalCauseKind.CHAIN_EXHAUSTED,
            invoked_ordinals=(0,),
            distinct_attempt_outcomes=(AttemptOutcome.EMPTY,),
        ),
        selected=None,
    )
    rejected = [
        finalizer.finalize_extraction(
            _request(f"reject-{ordinal}"),
            _plan(candidates=("trafilatura",)),
            exhausted,
            policy,
        )
        for ordinal in (1, 2)
    ]

    assert successes[0].artifact.artifact_ref == successes[1].artifact.artifact_ref
    assert rejected[0].rejection == rejected[1].rejection


def test_reused_artifact_ref_with_conflicting_identity_fails_closed(tmp_path):
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import ExtractionAcceptanceConflict
    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'reuse-conflict.db'}"
    )
    finalizer = ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    )
    policy = OutcomePolicy(version="outcome-v1")
    finalizer.finalize_extraction(
        _request("extract-a"),
        _plan(),
        _raw(artifact=_artifact()),
        policy,
    )
    conflicting = replace(
        _artifact(),
        content_identity="sha256:" + ("b" * 64),
        text="different governed content",
    )

    with pytest.raises(ExtractionAcceptanceConflict):
        finalizer.finalize_extraction(
            _request("extract-b"),
            _plan(),
            _raw(artifact=conflicting),
            policy,
        )


def test_policy_complete_cache_preserves_exact_accepted_origin_lineage():
    from argus.extraction.cache import ExtractionCache, ExtractionCacheIdentity

    repository = MemoryOutcomeRepository()
    accepted = _finalize(_raw(artifact=_artifact()), repository=repository)
    cache = ExtractionCache(acceptance_repository=repository)
    identity = ExtractionCacheIdentity.from_accepted(accepted)
    legacy = accepted.to_legacy_extracted_content()

    cache.put(identity, legacy)
    assert cache.get(identity) is None

    cache.put(identity, accepted)
    assert cache.get(identity) is accepted
    assert cache.get(
        replace(identity, normalized_url="sha256:" + ("0" * 64))
    ) is None


@pytest.mark.asyncio
async def test_archive_creation_requires_verified_durable_authority_before_post(
    monkeypatch,
):
    import argus.extraction.archive_extractor as archive

    posts = []

    async def submitted(url):
        posts.append(url)
        return "https://archive.ph/bounded/example"

    class Authority:
        def verify(self, authorization):
            return authorization.authority_receipt == "authority-receipt-verified"

    class DurableStore:
        def __init__(self):
            self.claims = set()

        def consume(self, *, receipt, idempotency_key, target):
            identity = (receipt, idempotency_key, target)
            if identity in self.claims:
                return False
            self.claims.add(identity)
            return True

    monkeypatch.setattr(archive, "_submit_authorized", submitted)
    authorization = archive.ArchiveCreationAuthorization(
        caller_policy_ref="archive-policy-v1",
        authority_receipt="authority-receipt-verified",
        idempotency_key="archive-create-durable-1",
        bounded_target="https://example.com/article",
        profile="operator",
    )
    store = DurableStore()

    created = await archive.create_archive(
        "https://example.com/article",
        authorization=authorization,
        authority=Authority(),
        authorization_store=store,
    )
    with pytest.raises(archive.ArchiveCreationPolicyRejected):
        await archive.create_archive(
            "https://example.com/article",
            authorization=authorization,
            authority=Authority(),
            authorization_store=store,
        )
    with pytest.raises(archive.ArchiveCreationPolicyRejected):
        await archive.create_archive(
            "https://example.com/article",
            authorization=replace(
                authorization,
                authority_receipt="self-attested",
                idempotency_key="archive-create-durable-2",
            ),
            authority=Authority(),
            authorization_store=store,
        )

    assert created == "https://archive.ph/bounded/example"
    assert posts == ["https://example.com/article"]


@pytest.mark.asyncio
async def test_archive_authority_failure_is_typed_and_never_posts(monkeypatch):
    import argus.extraction.archive_extractor as archive

    posts = []

    async def submitted(url):
        posts.append(url)

    class FailingAuthority:
        def verify(self, authorization):
            raise RuntimeError("authority backend secret=do-not-publish")

    class Store:
        def consume(self, **kwargs):
            raise AssertionError("store must not consume after failed verification")

    monkeypatch.setattr(archive, "_submit_authorized", submitted)
    authorization = archive.ArchiveCreationAuthorization(
        caller_policy_ref="archive-policy-v1",
        authority_receipt="authority-receipt-failure",
        idempotency_key="archive-create-failure",
        bounded_target="https://example.com/article",
        profile="operator",
    )

    with pytest.raises(archive.ArchiveCreationPolicyRejected) as raised:
        await archive.create_archive(
            "https://example.com/article",
            authorization=authorization,
            authority=FailingAuthority(),
            authorization_store=Store(),
        )

    assert "secret" not in str(raised.value)
    assert posts == []


def test_all_durable_projection_json_redacts_credential_bearing_url(tmp_path):
    from sqlalchemy import select

    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomePlanRow,
        create_search_ledger_repository,
    )

    raw_url = (
        "https://user:password@example.com/article"
        "?token=super-secret&safe=value#auth=fragment-secret"
    )
    request = replace(_request("extract-secret-url"), normalized_url=raw_url)
    plan = replace(_plan(), normalized_url=raw_url)
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'redacted-url.db'}"
    )
    ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    ).finalize_extraction(
        request,
        plan,
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )

    with repository.session_factory() as session:
        durable_plan = session.scalar(select(ExtractionOutcomePlanRow))
        acceptance = session.scalar(select(ExtractionOutcomeAcceptanceRow))
    persisted = " ".join(
        (
            durable_plan.normalized_url,
            durable_plan.plan_json,
            acceptance.projection_json,
        )
    )
    assert "password" not in persisted
    assert "super-secret" not in persisted
    assert "fragment-secret" not in persisted
    assert "normalized_url_identity" in persisted


def test_0007_latency_column_is_bigint_and_links_bind_plan_rows(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{tmp_path / 'migration-shape.db'}"
    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    inspector = inspect(create_engine(url))
    step_columns = {
        column["name"]: str(column["type"]).upper()
        for column in inspector.get_columns("extraction_outcome_steps")
    }
    link_fks = {
        tuple(foreign_key["constrained_columns"])
        for foreign_key in inspector.get_foreign_keys(
            "result_extraction_links"
        )
    }

    assert "BIGINT" in step_columns["latency_ms"]
    assert (
        "extraction_acceptance_ref",
        "extraction_plan_id",
    ) in link_fks
    assert ("artifact_row_id", "artifact_plan_id") in link_fks
    assert ("rejection_row_id", "rejection_plan_id") in link_fks


def test_recovery_contract_covers_every_0007_table_and_relationship():
    from argus.recovery.database import REQUIRED_TABLES, _ORPHAN_CHECKS

    s3_tables = {
        "extraction_outcome_plans",
        "extraction_outcome_steps",
        "extraction_outcome_artifacts",
        "extraction_outcome_rejections",
        "extraction_outcome_acceptances",
        "retrieval_compositions",
        "result_extraction_links",
        "extraction_outcome_activations",
    }
    joined_checks = "\n".join(_ORPHAN_CHECKS)

    assert s3_tables <= REQUIRED_TABLES
    for child in (
        "extraction_outcome_steps",
        "extraction_outcome_artifacts",
        "extraction_outcome_rejections",
        "extraction_outcome_acceptances",
        "result_extraction_links",
    ):
        assert child in joined_checks
def test_issue57_classifier_executes_exactly_once_total(monkeypatch):
    import argus.extraction.rejection as rejection_module

    original = rejection_module.classify_typed_extraction_rejection
    calls = []

    def instrumented(facts):
        calls.append(facts)
        return original(facts)

    monkeypatch.setattr(
        rejection_module, "classify_typed_extraction_rejection", instrumented
    )

    accepted = _finalize(_raw(artifact=_artifact(quality=False, complete=None)))

    assert accepted.rejection is not None
    assert len(calls) == 1


def test_first_and_retry_return_same_safe_semantic_projection(tmp_path):
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.persistence.search_ledger import create_search_ledger_repository

    raw_url = (
        "https://user:password@example.com/private/path"
        "?session=opaque&code=authorization&jwt=signed#credential=hidden"
    )
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'safe-semantic.db'}"
    )
    finalizer = ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    )
    request = replace(_request("safe-semantic-run"), normalized_url=raw_url)
    plan = replace(_plan(), normalized_url=raw_url)
    first = finalizer.finalize_extraction(
        request,
        plan,
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    retry = finalizer.finalize_extraction(
        request,
        plan,
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )

    assert first == retry
    assert first.plan.normalized_url == "https://example.com/"
    assert all(
        secret not in repr(first)
        for secret in ("password", "opaque", "authorization", "signed", "hidden")
    )


def test_cache_identity_is_derived_and_verified_against_durable_acceptance(
    tmp_path,
):
    from argus.extraction.cache import ExtractionCache, ExtractionCacheIdentity
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.persistence.search_ledger import create_search_ledger_repository

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'durable-cache.db'}"
    )
    accepted = ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    ).finalize_extraction(
        _request("durable-cache-run"),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    identity = ExtractionCacheIdentity.from_accepted(accepted)
    cache = ExtractionCache(acceptance_repository=repository)

    cache.put(identity, accepted)

    assert cache.get(identity) == accepted
    private_key = replace(
        identity,
        access_scope="private",
        privacy_scope="private",
        authentication_scope_fingerprint="user-a",
    )
    cache.put(private_key, accepted)
    assert cache.get(private_key) is None


@pytest.mark.asyncio
async def test_sqlite_archive_authorization_consume_survives_restart(
    monkeypatch,
    tmp_path,
):
    import argus.extraction.archive_extractor as archive

    posts = []

    async def submitted(url):
        posts.append(url)
        return "https://archive.ph/restart/example"

    class Authority:
        def verify(self, authorization):
            return True

    monkeypatch.setattr(archive, "_submit_authorized", submitted)
    authorization = archive.ArchiveCreationAuthorization(
        caller_policy_ref="archive-policy-v1",
        authority_receipt="authority-receipt-restart",
        idempotency_key="archive-create-restart",
        bounded_target="https://example.com/article",
        profile="operator",
    )
    path = tmp_path / "archive-authorizations.db"
    first_store = archive.SQLiteArchiveCreationAuthorizationStore(path)
    await archive.create_archive(
        authorization.bounded_target,
        authorization=authorization,
        authority=Authority(),
        authorization_store=first_store,
    )

    restarted_store = archive.SQLiteArchiveCreationAuthorizationStore(path)
    with pytest.raises(archive.ArchiveCreationPolicyRejected):
        await archive.create_archive(
            authorization.bounded_target,
            authorization=authorization,
            authority=Authority(),
            authorization_store=restarted_store,
        )

    assert posts == [authorization.bounded_target]


def test_concurrent_conflicting_artifact_identity_has_one_canonical_winner(
    tmp_path,
):
    from concurrent.futures import ThreadPoolExecutor

    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import ExtractionAcceptanceConflict
    from argus.persistence.search_ledger import create_search_ledger_repository

    url = f"sqlite:///{tmp_path / 'artifact-race.db'}"
    repositories = [
        create_search_ledger_repository(url),
        create_search_ledger_repository(url, create_schema=False),
    ]
    barrier = __import__("threading").Barrier(2)

    def run(index):
        artifact = replace(
            _artifact(),
            content_identity="sha256:" + (("a" if index == 0 else "b") * 64),
            text=f"content-{index}",
        )
        barrier.wait()
        return ExtractionFinalizer(
            repository=repositories[index],
            clock=lambda: "2026-07-27T12:00:00Z",
        ).finalize_extraction(
            _request(f"artifact-race-{index}"),
            _plan(),
            _raw(artifact=artifact),
            OutcomePolicy(version="outcome-v1"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(run, index) for index in range(2)]
    outcomes = []
    failures = []
    for future in futures:
        try:
            outcomes.append(future.result())
        except ExtractionAcceptanceConflict as error:
            failures.append(error)

    assert len(outcomes) == 1
    assert len(failures) == 1


def test_two_repositories_execute_issue57_mapper_once_total(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier, Lock

    from argus.extraction.finalizer import (
        ExtractionFinalizer,
        map_extraction_rejection,
    )
    from argus.persistence.search_ledger import create_search_ledger_repository

    url = f"sqlite:///{tmp_path / 'cross-repository-finalize.db'}"
    repositories = [
        create_search_ledger_repository(url),
        create_search_ledger_repository(url, create_schema=False),
    ]
    barrier = Barrier(2)
    call_lock = Lock()
    calls = 0

    def mapper(facts):
        nonlocal calls
        with call_lock:
            calls += 1
        return map_extraction_rejection(facts)

    raw = _raw(artifact=_artifact(quality=False, complete=None))

    def run(index):
        barrier.wait()
        return ExtractionFinalizer(
            repository=repositories[index],
            rejection_mapper=mapper,
            clock=lambda: "2026-07-27T12:00:00Z",
        ).finalize_extraction(
            _request("cross-repository-run"),
            _plan(),
            raw,
            OutcomePolicy(version="outcome-v1"),
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        accepted = list(executor.map(run, range(2)))

    assert calls == 1
    assert accepted[0] == accepted[1]


def test_cache_origin_self_attested_receipt_is_rejected():
    from argus.extraction.outcomes import (
        CacheOriginEvidence,
        ExtractionAcceptanceReceipt,
        ExtractionContractRejected,
    )

    repository = MemoryOutcomeRepository()
    origin = _finalize(_raw(artifact=_artifact()), repository=repository)
    evidence = CacheOriginEvidence.from_accepted(
        origin,
        acceptance_repository=repository,
    )
    fabricated = replace(
        evidence,
        acceptance_receipt=ExtractionAcceptanceReceipt(
            receipt_ref="self-attested-receipt",
            accepted_at=evidence.accepted_at,
            scope=evidence.acceptance_receipt.scope,
        ),
    )
    raw = RawExtractionResult(
        cache_decision=CacheDecision(
            outcome=CacheOutcome.HIT_ELIGIBLE,
            origin_run_ref=origin.extraction_run_id,
            age_seconds=0,
            origin_evidence=fabricated,
        ),
        steps=(),
        artifact=_artifact(),
        selected_extractor="trafilatura",
        terminal_cause=None,
        operation_latency_ms=1,
    )

    with pytest.raises(ExtractionContractRejected):
        _finalize(raw, repository=repository)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("extraction_plan_version", "plan-v999"),
        ("quality_policy_version", "quality-v999"),
        ("completeness_policy_version", "complete-v999"),
        ("outcome_policy_version", "outcome-v999"),
        ("partial_allowed", True),
        ("authentication_scope_fingerprint", "sha256:" + ("f" * 64)),
    ],
)
def test_cache_hit_requires_exact_complete_current_identity(field, value):
    from argus.extraction.cache import ExtractionCacheIdentity
    from argus.extraction.outcomes import (
        CacheOriginEvidence,
        ExtractionContractRejected,
    )

    repository = MemoryOutcomeRepository()
    origin = _finalize(_raw(artifact=_artifact()), repository=repository)
    current_identity = ExtractionCacheIdentity.from_accepted(origin)
    origin_evidence = CacheOriginEvidence.from_accepted(
        origin,
        acceptance_repository=repository,
    )
    raw = RawExtractionResult(
        cache_decision=CacheDecision(
            outcome=CacheOutcome.HIT_ELIGIBLE,
            origin_run_ref=origin.extraction_run_id,
            age_seconds=0,
            origin_evidence=origin_evidence,
            current_identity=replace(current_identity, **{field: value}),
        ),
        steps=(),
        artifact=_artifact(),
        selected_extractor="trafilatura",
        terminal_cause=None,
        operation_latency_ms=1,
    )

    with pytest.raises(ExtractionContractRejected):
        _finalize(raw, repository=repository)


def test_cache_hit_age_is_derived_from_durable_cache_creation_time():
    from argus.extraction.cache import ExtractionCacheIdentity
    from argus.extraction.outcomes import (
        CacheOriginEvidence,
        ExtractionContractRejected,
    )

    repository = MemoryOutcomeRepository()
    origin = _finalize(_raw(artifact=_artifact()), repository=repository)
    raw = RawExtractionResult(
        cache_decision=CacheDecision(
            outcome=CacheOutcome.HIT_ELIGIBLE,
            origin_run_ref=origin.extraction_run_id,
            age_seconds=1,
            origin_evidence=CacheOriginEvidence.from_accepted(
                origin,
                acceptance_repository=repository,
            ),
            current_identity=ExtractionCacheIdentity.from_accepted(origin),
        ),
        steps=(),
        artifact=_artifact(),
        selected_extractor="trafilatura",
        terminal_cause=None,
        operation_latency_ms=1,
    )

    with pytest.raises(ExtractionContractRejected):
        _finalize(raw, repository=repository)


def test_ineligible_cache_hit_still_requires_exact_current_identity():
    from argus.extraction.cache import ExtractionCacheIdentity
    from argus.extraction.outcomes import (
        CacheOriginEvidence,
        ExtractionContractRejected,
    )

    repository = MemoryOutcomeRepository()
    origin = _finalize(_raw(artifact=_artifact()), repository=repository)
    identity = ExtractionCacheIdentity.from_accepted(origin)
    raw = replace(
        _raw(artifact=_artifact()),
        cache_decision=CacheDecision(
            outcome=CacheOutcome.HIT_INELIGIBLE,
            origin_run_ref=origin.extraction_run_id,
            age_seconds=0,
            origin_evidence=CacheOriginEvidence.from_accepted(
                origin,
                acceptance_repository=repository,
            ),
            current_identity=replace(identity, mode="research"),
        ),
    )

    with pytest.raises(ExtractionContractRejected):
        _finalize(raw, repository=repository)


def test_cache_creation_time_comes_from_durable_acceptance():
    from argus.extraction.outcomes import CacheOriginEvidence

    repository = MemoryOutcomeRepository()
    origin = _finalize(_raw(artifact=_artifact()), repository=repository)

    evidence = CacheOriginEvidence.from_accepted(
        origin,
        acceptance_repository=repository,
    )

    assert evidence.cache_created_at == origin.acceptance_receipt.accepted_at


def test_0007_has_canonical_artifact_identity_and_link_constraints(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{tmp_path / 'canonical-artifact.db'}"
    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    inspector = inspect(create_engine(url))

    assert "extraction_artifact_identities" in inspector.get_table_names()
    pk = inspector.get_pk_constraint("extraction_artifact_identities")
    assert pk["constrained_columns"] == ["artifact_ref"]
    artifact_fks = inspector.get_foreign_keys("extraction_outcome_artifacts")
    assert any(
        fk["constrained_columns"] == ["artifact_ref"]
        and fk["referred_table"] == "extraction_artifact_identities"
        for fk in artifact_fks
    )
