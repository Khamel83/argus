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
        return ExtractionAcceptanceReceipt(
            receipt_ref=f"receipt-{projection.extraction_run_id}",
            accepted_at="2026-07-27T12:00:00Z",
            scope="test",
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
        )
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
    accepted = _finalize(
        RawExtractionResult(
            cache_decision=CacheDecision(
                outcome=CacheOutcome.HIT_ELIGIBLE,
                origin_run_ref="extract-origin-1",
                age_seconds=60,
            ),
            steps=(),
            artifact=_artifact(),
            selected_extractor="trafilatura",
            terminal_cause=None,
            operation_latency_ms=1,
        )
    )

    assert accepted.outcome is CanonicalOutcome.SUCCESS
    assert accepted.steps == ()
    assert accepted.cache_decision.origin_run_ref == "extract-origin-1"


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

    cache = ExtractionCache()
    public = ExtractionCacheIdentity(
        normalized_url="https://example.com/article",
        mode="default",
        access_scope="public",
        authentication_scope_fingerprint="anonymous",
        extraction_plan_version="1",
        quality_policy_version="quality-v1",
        completeness_policy_version="complete-v1",
        partial_allowed=False,
    )
    private = replace(
        public,
        access_scope="private",
        authentication_scope_fingerprint="account-a",
    )
    usable = _finalize(_raw(artifact=_artifact())).to_legacy_extracted_content()
    usable.artifact_disposition = ArtifactDisposition.USABLE
    cache.put(public, usable)

    assert cache.get(public) is usable
    assert cache.get(private) is None

    diagnostic = replace(usable)
    diagnostic.artifact_disposition = ArtifactDisposition.DIAGNOSTIC_ONLY
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
    )
    with pytest.raises(archive.ArchiveCreationPolicyRejected):
        await archive.create_archive(
            "https://example.com/article",
            authorization=authorization,
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
        _request(), _plan(), raw, OutcomePolicy(version="outcome-v1")
    )
    second = finalizer.finalize_extraction(
        _request(), _plan(), raw, OutcomePolicy(version="outcome-v1")
    )

    assert second == first
    assert len(calls) == 1
    with pytest.raises(ExtractionAcceptanceConflict):
        finalizer.finalize_extraction(
            _request(),
            replace(_plan(), quality_policy_version="quality-v2"),
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
            _plan(),
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
    assert "extraction_outcome_plans" in inspect(engine).get_table_names()
    command.downgrade(config, "0006_maya_outbox")
    assert "extraction_outcome_plans" not in inspect(engine).get_table_names()
