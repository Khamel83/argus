from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal

import pytest

from argus.contracts import CanonicalOutcome
from argus.extraction.composition import (
    AggregateArtifactFloor,
    ArtifactRequirement,
    ArtifactSelection,
    ResultExtractionLink,
    compose_retrieval_evidence,
)
from argus.extraction.outcomes import (
    AcceptedExtractionOutcome,
    ArtifactDisposition,
    ArtifactEvaluation,
    AttemptOutcome,
    CacheDecision,
    CacheOutcome,
    ExtractionCandidate,
    ExtractionAcceptanceReceipt,
    ExtractionPlan,
    ExtractionProvenance,
    ExtractorDecision,
    ExtractorExecutionDecision,
    SpendEvidence,
)
from argus.extraction.rejection import (
    ExtractionRejection,
    RejectionAction,
    RejectionCode,
)


@dataclass(frozen=True)
class Retrieval:
    outcome: CanonicalOutcome
    result_cluster_refs: tuple[str, ...]
    acceptance_receipt: str | None = "retrieval-receipt"


def _requirement(
    count=1,
    *,
    selections=None,
    minimum=ArtifactDisposition.USABLE,
):
    selections = selections or (
        ArtifactSelection(
            result_cluster_ref="cluster-1",
            required=True,
            minimum_disposition=minimum,
        ),
    )
    return ArtifactRequirement(
        requirement_ref="requirement-1",
        selections=tuple(selections),
        aggregate_floor=AggregateArtifactFloor(
            count=count,
            minimum_disposition=minimum,
        ),
        max_extractions=len(selections),
        deadline_ms=30_000,
        spend_policy_ref="spend-v1",
    )


def _link(
    cluster="cluster-1",
    *,
    outcome=CanonicalOutcome.SUCCESS,
    disposition=ArtifactDisposition.USABLE,
    required=True,
    eligible_path=True,
    attempted=True,
    extraction_run_id=None,
    artifact_ref=None,
    artifact_identity=None,
    reuse_origin=None,
):
    suffix = cluster.removeprefix("cluster-")
    actual_run_id = (
        f"extract-{suffix}" if extraction_run_id is None else extraction_run_id
    )
    actual_artifact_ref = (
        artifact_ref or f"artifact-{suffix}"
        if disposition is not ArtifactDisposition.NONE
        else None
    )
    actual_artifact_identity = (
        artifact_identity or f"sha256:{suffix:0>64}"
        if disposition is not ArtifactDisposition.NONE
        else None
    )
    artifact = (
        ArtifactEvaluation(
            artifact_ref=actual_artifact_ref,
            content_identity=actual_artifact_identity,
            text=f"artifact {suffix}",
            title="Article",
            author="Ada",
            published_date=None,
            word_count=2,
            quality_passed=disposition
            in {ArtifactDisposition.USABLE, ArtifactDisposition.PARTIAL},
            is_complete=disposition is ArtifactDisposition.USABLE,
            completeness_confidence=Decimal("1"),
            completeness_signals=(),
            completeness_assessment_version="complete-v1",
            completeness_recommended_action="use_as_is",
            provenance=ExtractionProvenance(
                source_type="normalized_text",
                egress="local",
                machine="test_node",
            ),
        )
        if disposition is not ArtifactDisposition.NONE
        else None
    )
    rejection = (
        None
        if outcome is CanonicalOutcome.SUCCESS
        else ExtractionRejection(
            code=RejectionCode.INCOMPLETE_CONTENT
            if outcome is CanonicalOutcome.DEGRADED
            else RejectionCode.PROVIDER_UNAVAILABLE,
            provider="trafilatura",
            quality_passed=(
                artifact.quality_passed if artifact is not None else None
            ),
            is_complete=artifact.is_complete if artifact is not None else None,
            recommended_action=RejectionAction.TERMINAL,
            attempt_count=1,
            last_status="content"
            if artifact is not None
            else "provider_unavailable",
            total_latency_ms=1,
        )
    )
    plan = ExtractionPlan(
        plan_ref=f"plan-{suffix}",
        normalized_url=f"https://example.com/{suffix}",
        access_scope="public",
        mode="default",
        candidates=(
            ExtractionCandidate(
                extractor="trafilatura",
                eligible=eligible_path,
                spend_class="free",
            ),
        ),
        cache_policy_ref="cache-v1",
        extraction_plan_version="plan-v1",
        quality_policy_version="quality-v1",
        completeness_policy_version="complete-v1",
        partial_allowed=disposition is ArtifactDisposition.PARTIAL,
        deadline_ms=30_000,
        caller="test",
        profile="autonomous",
        privacy_scope="public",
    )
    accepted = AcceptedExtractionOutcome(
        extraction_outcome_policy_version="outcome-v1",
        outcome=outcome,
        artifact_disposition=disposition,
        extraction_run_id=actual_run_id,
        request_id=f"request-{suffix}",
        plan_ref=plan.plan_ref,
        plan=plan,
        artifact=artifact,
        rejection=rejection,
        steps=(
            (
                ExtractorDecision(
                    ordinal=0,
                    extractor="trafilatura",
                    decision=ExtractorExecutionDecision.INVOKED,
                    attempt_outcome=(
                        AttemptOutcome.CONTENT
                        if artifact is not None
                        else AttemptOutcome.PROVIDER_UNAVAILABLE
                    ),
                    latency_ms=1,
                    provenance=ExtractionProvenance(
                        source_type="normalized_text",
                        egress="local",
                        machine="test_node",
                    ),
                    spend=SpendEvidence(
                        actual_usd=Decimal("0"),
                        reserved_usd=Decimal("0"),
                        spend_attempt_ref=f"spend-{suffix}",
                    ),
                ),
            )
            if attempted
            else ()
        ),
        terminal_cause=None,
        selected_extractor="trafilatura" if artifact is not None else None,
        cache_decision=CacheDecision(CacheOutcome.MISS),
        operation_latency_ms=1,
        acceptance_receipt=ExtractionAcceptanceReceipt(
            receipt_ref=f"receipt-{suffix}",
            accepted_at="2026-07-27T12:00:00Z",
            scope="sqlite_development",
        ),
    )
    return ResultExtractionLink.from_accepted(
        link_ref=f"link-{suffix}",
        result_cluster_ref=cluster,
        accepted_outcome=accepted,
        required=required,
        reuse_origin=reuse_origin,
    )


@pytest.mark.parametrize(
    ("retrieval_outcome", "links", "requirement", "expected"),
    [
        (CanonicalOutcome.SUCCESS, (), None, CanonicalOutcome.SUCCESS),
        (CanonicalOutcome.EMPTY, (), _requirement(), CanonicalOutcome.EMPTY),
        (
            CanonicalOutcome.SUCCESS,
            (_link(),),
            _requirement(),
            CanonicalOutcome.SUCCESS,
        ),
        (
            CanonicalOutcome.DEGRADED,
            (_link(),),
            _requirement(),
            CanonicalOutcome.DEGRADED,
        ),
        (
            CanonicalOutcome.SUCCESS,
            (
                _link(
                    outcome=CanonicalOutcome.DEGRADED,
                    disposition=ArtifactDisposition.PARTIAL,
                ),
            ),
            _requirement(minimum=ArtifactDisposition.PARTIAL),
            CanonicalOutcome.DEGRADED,
        ),
        (
            CanonicalOutcome.SUCCESS,
            (
                _link(
                    outcome=CanonicalOutcome.UNREADY,
                    disposition=ArtifactDisposition.NONE,
                    eligible_path=False,
                    attempted=False,
                ),
            ),
            _requirement(),
            CanonicalOutcome.UNREADY,
        ),
        (
            CanonicalOutcome.SUCCESS,
            (
                _link(
                    outcome=CanonicalOutcome.EXTRACTION_FAILED,
                    disposition=ArtifactDisposition.DIAGNOSTIC_ONLY,
                ),
            ),
            _requirement(),
            CanonicalOutcome.EXTRACTION_FAILED,
        ),
    ],
)
def test_composite_precedence(retrieval_outcome, links, requirement, expected):
    retrieval = Retrieval(
        outcome=retrieval_outcome,
        result_cluster_refs=("cluster-1",) if requirement is not None else (),
    )
    composition = compose_retrieval_evidence(retrieval, links, requirement)
    assert composition.composite_outcome is expected


def test_persistence_failure_precedes_artifact_floor():
    composition = compose_retrieval_evidence(
        Retrieval(
            CanonicalOutcome.SUCCESS,
            ("cluster-1",),
            acceptance_receipt=None,
        ),
        (_link(),),
        _requirement(),
    )
    assert composition.composite_outcome is CanonicalOutcome.PERSISTENCE_FAILED


def test_per_result_and_aggregate_floors_are_independent():
    selections = (
        ArtifactSelection(
            "cluster-1",
            required=True,
            minimum_disposition=ArtifactDisposition.USABLE,
        ),
        ArtifactSelection(
            "cluster-2",
            required=False,
            minimum_disposition=ArtifactDisposition.PARTIAL,
        ),
    )
    requirement = _requirement(
        count=1,
        selections=selections,
        minimum=ArtifactDisposition.PARTIAL,
    )
    links = (
        _link(
            "cluster-1",
            outcome=CanonicalOutcome.DEGRADED,
            disposition=ArtifactDisposition.PARTIAL,
        ),
        _link("cluster-2", required=False),
    )

    composition = compose_retrieval_evidence(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1", "cluster-2")),
        links,
        requirement,
    )

    assert composition.composite_outcome is CanonicalOutcome.EXTRACTION_FAILED
    assert composition.artifact_outcome is CanonicalOutcome.EXTRACTION_FAILED


def test_aggregate_readiness_uses_remaining_possible_selections():
    selections = tuple(
        ArtifactSelection(
            f"cluster-{ordinal}",
            required=False,
            minimum_disposition=ArtifactDisposition.USABLE,
        )
        for ordinal in range(1, 4)
    )
    requirement = _requirement(count=3, selections=selections)
    links = tuple(
        _link(
            f"cluster-{ordinal}",
            outcome=CanonicalOutcome.UNREADY
            if ordinal == 1
            else CanonicalOutcome.EXTRACTION_FAILED,
            disposition=ArtifactDisposition.NONE,
            required=False,
            eligible_path=ordinal != 1,
            attempted=ordinal != 1,
        )
        for ordinal in range(1, 4)
    )

    composition = compose_retrieval_evidence(
        Retrieval(
            CanonicalOutcome.SUCCESS,
            tuple(selection.result_cluster_ref for selection in selections),
        ),
        links,
        requirement,
    )

    assert composition.composite_outcome is CanonicalOutcome.UNREADY


def test_optional_failure_degrades_when_floors_are_met():
    selections = (
        ArtifactSelection(
            "cluster-1",
            required=True,
            minimum_disposition=ArtifactDisposition.USABLE,
        ),
        ArtifactSelection(
            "cluster-2",
            required=False,
            minimum_disposition=ArtifactDisposition.USABLE,
        ),
    )
    links = (
        _link("cluster-1"),
        _link(
            "cluster-2",
            outcome=CanonicalOutcome.EXTRACTION_FAILED,
            disposition=ArtifactDisposition.NONE,
            required=False,
        ),
    )
    composition = compose_retrieval_evidence(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1", "cluster-2")),
        links,
        _requirement(count=1, selections=selections),
    )

    assert composition.composite_outcome is CanonicalOutcome.DEGRADED
    assert composition.rejected_extraction_refs == ("extract-2",)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda links: links + (links[0],),
        lambda links: (replace(links[0], result_cluster_ref="dangling"),),
        lambda links: (),
    ],
    ids=["duplicate-cluster", "dangling-cluster", "missing-link"],
)
def test_links_are_bijective_with_selections(mutate):
    from argus.extraction.composition import InvalidArtifactRequirement

    with pytest.raises(InvalidArtifactRequirement):
        compose_retrieval_evidence(
            Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
            mutate((_link(),)),
            _requirement(),
        )


def test_link_cannot_lie_about_canonical_outcome_disposition_or_rejection():
    from argus.extraction.composition import InvalidArtifactRequirement

    lied = replace(
        _link(),
        extraction_outcome=CanonicalOutcome.EXTRACTION_FAILED,
        rejection_ref="rejection-1",
    )
    with pytest.raises(InvalidArtifactRequirement):
        compose_retrieval_evidence(
            Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
            (lied,),
            _requirement(),
        )

    partial_without_rejection = replace(
        _link(
            outcome=CanonicalOutcome.DEGRADED,
            disposition=ArtifactDisposition.PARTIAL,
        ),
        rejection_ref=None,
    )
    with pytest.raises(InvalidArtifactRequirement):
        compose_retrieval_evidence(
            Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
            (partial_without_rejection,),
            _requirement(minimum=ArtifactDisposition.PARTIAL),
        )


def test_two_hundredth_unique_link_is_retained():
    selections = tuple(
        ArtifactSelection(
            f"cluster-{ordinal}",
            required=False,
            minimum_disposition=ArtifactDisposition.USABLE,
        )
        for ordinal in range(1, 201)
    )
    links = tuple(_link(f"cluster-{ordinal}", required=False) for ordinal in range(1, 201))
    composition = compose_retrieval_evidence(
        Retrieval(
            CanonicalOutcome.SUCCESS,
            tuple(selection.result_cluster_ref for selection in selections),
        ),
        links,
        _requirement(count=200, selections=selections),
    )

    assert len(composition.links) == 200
    assert composition.links[-1].result_cluster_ref == "cluster-200"


def test_two_hundred_first_link_is_typed_rejection_without_truncation():
    from argus.extraction.composition import InvalidArtifactRequirement

    selections = tuple(
        ArtifactSelection(
            f"cluster-{ordinal}",
            required=False,
            minimum_disposition=ArtifactDisposition.USABLE,
        )
        for ordinal in range(1, 202)
    )
    links = tuple(_link(f"cluster-{ordinal}", required=False) for ordinal in range(1, 202))

    with pytest.raises(InvalidArtifactRequirement) as raised:
        compose_retrieval_evidence(
            Retrieval(
                CanonicalOutcome.SUCCESS,
                tuple(selection.result_cluster_ref for selection in selections),
            ),
            links,
            _requirement(count=200, selections=selections),
        )

    assert raised.value.outcome is CanonicalOutcome.INVALID_REQUEST


def test_many_to_one_reuse_requires_same_identity_scope_policy_and_origin():
    selections = (
        ArtifactSelection("cluster-1", True, ArtifactDisposition.USABLE),
        ArtifactSelection("cluster-2", True, ArtifactDisposition.USABLE),
    )
    first = _link(
        "cluster-1",
        extraction_run_id="extract-shared",
        artifact_ref="artifact-shared",
        artifact_identity="sha256:" + ("a" * 64),
        reuse_origin="reuse-authority-1",
    )
    second = _link(
        "cluster-2",
        extraction_run_id="extract-shared",
        artifact_ref="artifact-shared",
        artifact_identity="sha256:" + ("a" * 64),
        reuse_origin="reuse-authority-1",
    )

    accepted = compose_retrieval_evidence(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1", "cluster-2")),
        (first, second),
        _requirement(count=2, selections=selections),
    )
    assert accepted.composite_outcome is CanonicalOutcome.SUCCESS

    from argus.extraction.composition import InvalidArtifactRequirement

    with pytest.raises(InvalidArtifactRequirement):
        compose_retrieval_evidence(
            Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1", "cluster-2")),
            (first, replace(second, access_scope="private")),
            _requirement(count=2, selections=selections),
        )


def test_authorized_reuse_counts_one_distinct_extraction_against_maximum():
    selections = (
        ArtifactSelection("cluster-1", True, ArtifactDisposition.USABLE),
        ArtifactSelection("cluster-2", True, ArtifactDisposition.USABLE),
    )
    requirement = replace(
        _requirement(count=2, selections=selections),
        max_extractions=1,
    )
    links = (
        _link(
            "cluster-1",
            extraction_run_id="extract-shared",
            artifact_ref="artifact-shared",
            artifact_identity="sha256:" + ("a" * 64),
            reuse_origin="reuse-authority-1",
        ),
        _link(
            "cluster-2",
            extraction_run_id="extract-shared",
            artifact_ref="artifact-shared",
            artifact_identity="sha256:" + ("a" * 64),
            reuse_origin="reuse-authority-1",
        ),
    )

    composition = compose_retrieval_evidence(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1", "cluster-2")),
        links,
        requirement,
    )

    assert composition.composite_outcome is CanonicalOutcome.SUCCESS


def test_no_eligible_path_link_does_not_fabricate_extraction_identity():
    link = replace(
        _link(
            outcome=CanonicalOutcome.UNREADY,
            disposition=ArtifactDisposition.NONE,
            eligible_path=False,
            attempted=False,
        ),
        extraction_run_id=None,
        accepted_outcome=None,
        acceptance_receipt=None,
        rejection_ref=None,
    )
    composition = compose_retrieval_evidence(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
        (link,),
        _requirement(),
    )

    assert composition.composite_outcome is CanonicalOutcome.UNREADY
    assert composition.rejected_extraction_refs == ()


def test_repository_atomically_accepts_composition_links_and_is_idempotent(
    tmp_path,
):
    from sqlalchemy import func, select

    from argus.persistence.search_ledger import (
        ResultExtractionLinkRow,
        RetrievalCompositionRow,
        create_search_ledger_repository,
    )

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'composition.db'}",
        create_schema=True,
    )
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import OutcomePolicy
    from tests.test_extraction_outcomes import _artifact, _plan, _raw, _request

    accepted = ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    ).finalize_extraction(
        _request(),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    retrieval = Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",))
    requirement = _requirement()
    link = ResultExtractionLink.from_accepted(
        link_ref="link-1",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )
    composition = compose_retrieval_evidence(
        retrieval,
        (link,),
        requirement,
    )

    first = repository.accept_retrieval_composition(
        retrieval,
        composition,
        requirement,
    )
    second = repository.accept_retrieval_composition(
        retrieval,
        composition,
        requirement,
    )

    assert second == first
    with repository.session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(RetrievalCompositionRow)
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(ResultExtractionLinkRow)
        ) == 1
        stored = session.scalar(select(RetrievalCompositionRow))
        assert "password" not in stored.projection_json
        assert "composition-secret" not in stored.projection_json


def test_no_run_link_is_durable_without_fabricated_extraction_receipt(tmp_path):
    from sqlalchemy import select

    from argus.persistence.search_ledger import (
        ResultExtractionLinkRow,
        create_search_ledger_repository,
    )

    link = replace(
        _link(
            outcome=CanonicalOutcome.UNREADY,
            disposition=ArtifactDisposition.NONE,
            eligible_path=False,
            attempted=False,
        ),
        extraction_run_id=None,
        accepted_outcome=None,
        acceptance_receipt=None,
        rejection_ref=None,
    )

    composition = compose_retrieval_evidence(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
        (link,),
        _requirement(),
    )

    assert composition.composite_outcome is CanonicalOutcome.UNREADY
    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'no-run-link.db'}"
    )
    repository.accept_retrieval_composition(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
        composition,
        _requirement(),
    )
    with repository.session_factory() as session:
        durable_link = session.scalar(select(ResultExtractionLinkRow))
    assert durable_link.extraction_acceptance_ref is None
    assert durable_link.extraction_plan_id is None
    assert durable_link.artifact_row_id is None
    assert durable_link.rejection_row_id is None


def test_terminal_retrieval_still_validates_requirement_bounds():
    from argus.extraction.composition import InvalidArtifactRequirement

    invalid = replace(_requirement(), deadline_ms=0)

    with pytest.raises(InvalidArtifactRequirement):
        compose_retrieval_evidence(
            Retrieval(CanonicalOutcome.EMPTY, ("cluster-1",)),
            (),
            invalid,
        )


def test_repository_rejects_cross_plan_receipt_artifact_splice(tmp_path):
    from datetime import datetime

    from argus.extraction.composition import InvalidArtifactRequirement
    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomeArtifactRow,
        ExtractionOutcomePlanRow,
        create_search_ledger_repository,
    )

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'cross-plan.db'}"
    )
    now = datetime(2026, 7, 27, 12, 0)
    with repository.session_factory.begin() as session:
        for suffix in ("a", "b"):
            session.add(
                ExtractionOutcomePlanRow(
                    id=f"plan-row-{suffix}",
                    plan_ref=f"plan-{suffix}",
                    extraction_run_id=f"extract-{suffix}",
                    request_id=f"request-{suffix}",
                    normalized_url=f"https://example.com/{suffix}",
                    access_scope="public",
                    mode="default",
                    plan_json="{}",
                    source_fingerprint=suffix * 64,
                    created_at=now,
                )
            )
            session.add(
                ExtractionOutcomeArtifactRow(
                    id=f"artifact-row-{suffix}",
                    plan_id=f"plan-row-{suffix}",
                    artifact_ref=f"artifact-{suffix}",
                    content_identity="sha256:" + (suffix * 64),
                    content_text=f"artifact {suffix}",
                    disposition="usable",
                    quality_passed=True,
                    is_complete=True,
                    evaluation_json="{}",
                )
            )
            session.add(
                ExtractionOutcomeAcceptanceRow(
                    receipt_ref=f"receipt-{suffix}",
                    plan_id=f"plan-row-{suffix}",
                    outcome="success",
                    artifact_disposition="usable",
                    outcome_policy_version="outcome-v1",
                    projection_json="{}",
                    acceptance_fingerprint=("c" if suffix == "a" else "d") * 64,
                    accepted_at=now,
                    scope="sqlite_development",
                )
            )

    link = replace(
        _link(),
        extraction_run_id="extract-a",
        acceptance_receipt="receipt-a",
        artifact_ref="artifact-b",
        artifact_identity="sha256:" + ("b" * 64),
    )
    retrieval = Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",))
    requirement = _requirement()
    valid = compose_retrieval_evidence(
        retrieval,
        (_link(),),
        requirement,
    )
    composition = replace(valid, links=(link,))

    with pytest.raises(InvalidArtifactRequirement):
        repository.accept_retrieval_composition(
            retrieval,
            composition,
            requirement,
        )


def test_link_is_constructed_from_and_cannot_splice_typed_accepted_outcome():
    from decimal import Decimal

    from argus.extraction.composition import InvalidArtifactRequirement
    from argus.extraction.outcomes import (
        AcceptedExtractionOutcome,
        ArtifactEvaluation,
        CacheDecision,
        CacheOutcome,
        ExtractionAcceptanceReceipt,
        ExtractionPlan,
        ExtractionProvenance,
    )

    artifact = ArtifactEvaluation(
        artifact_ref="artifact-typed",
        content_identity="sha256:" + ("a" * 64),
        text="typed artifact",
        title="Typed",
        author="Ada",
        published_date=None,
        word_count=2,
        quality_passed=True,
        is_complete=True,
        completeness_confidence=Decimal("1"),
        completeness_signals=(),
        completeness_assessment_version="complete-v1",
        completeness_recommended_action="use_as_is",
        provenance=ExtractionProvenance(
            source_type="normalized_text",
            egress="local",
            machine="test_node",
        ),
    )
    plan = ExtractionPlan(
        plan_ref="plan-typed",
        normalized_url="https://example.com/typed",
        access_scope="public",
        mode="default",
        candidates=(),
        cache_policy_ref="cache-v1",
        extraction_plan_version="plan-v1",
        quality_policy_version="quality-v1",
        completeness_policy_version="complete-v1",
        partial_allowed=False,
        deadline_ms=30_000,
        caller="test",
        profile="autonomous",
        privacy_scope="public",
    )
    accepted = AcceptedExtractionOutcome(
        extraction_outcome_policy_version="outcome-v1",
        outcome=CanonicalOutcome.SUCCESS,
        artifact_disposition=ArtifactDisposition.USABLE,
        extraction_run_id="extract-typed",
        request_id="request-typed",
        plan_ref="plan-typed",
        plan=plan,
        artifact=artifact,
        rejection=None,
        steps=(),
        terminal_cause=None,
        selected_extractor="trafilatura",
        cache_decision=CacheDecision(CacheOutcome.MISS),
        operation_latency_ms=1,
        acceptance_receipt=ExtractionAcceptanceReceipt(
            receipt_ref="receipt-typed",
            accepted_at="2026-07-27T12:00:00Z",
            scope="sqlite_development",
        ),
    )
    link = ResultExtractionLink.from_accepted(
        link_ref="link-typed",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )

    with pytest.raises(InvalidArtifactRequirement):
        compose_retrieval_evidence(
            Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
            (replace(link, artifact_ref="artifact-spliced"),),
            _requirement(),
        )
def test_from_accepted_derives_readiness_without_caller_control():
    accepted = _link().accepted_outcome

    first = ResultExtractionLink.from_accepted(
        link_ref="derived-link",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )
    second = ResultExtractionLink.from_accepted(
        link_ref="derived-link",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )

    assert first == second
    assert first.eligible_path is True
    assert first.attempted is True


def test_same_accepted_failure_has_one_deterministic_readiness():
    accepted_failure = _link(
        outcome=CanonicalOutcome.EXTRACTION_FAILED,
        disposition=ArtifactDisposition.DIAGNOSTIC_ONLY,
        eligible_path=True,
        attempted=True,
    ).accepted_outcome

    links = tuple(
        ResultExtractionLink.from_accepted(
            link_ref="failure-link",
            result_cluster_ref="cluster-1",
            accepted_outcome=accepted_failure,
            required=True,
        )
        for _ in range(2)
    )

    assert links[0] == links[1]
    assert (links[0].eligible_path, links[0].attempted) == (True, True)


def test_preflight_unready_without_candidates_derives_false_false():
    accepted = replace(
        _link().accepted_outcome,
        outcome=CanonicalOutcome.UNREADY,
        artifact_disposition=ArtifactDisposition.NONE,
        plan=replace(_link().accepted_outcome.plan, candidates=()),
        artifact=None,
        rejection=None,
        steps=(),
        selected_extractor=None,
    )

    link = ResultExtractionLink.from_accepted(
        link_ref="preflight-link",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )

    assert (link.eligible_path, link.attempted) == (False, False)


def test_composition_replay_rebinds_before_fingerprint_return(tmp_path):
    from argus.extraction.composition import InvalidArtifactRequirement
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import OutcomePolicy
    from argus.persistence.search_ledger import create_search_ledger_repository
    from tests.test_extraction_outcomes import _artifact, _plan, _raw, _request

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'composition-replay.db'}"
    )
    accepted = ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    ).finalize_extraction(
        _request("composition-replay-run"),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    link = ResultExtractionLink.from_accepted(
        link_ref="replay-link",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )
    retrieval = Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",))
    requirement = _requirement()
    composition = compose_retrieval_evidence(retrieval, (link,), requirement)
    repository.accept_retrieval_composition(
        retrieval,
        composition,
        requirement,
    )
    forged_accepted = replace(
        accepted,
        artifact=replace(accepted.artifact, text="forged artifact text"),
        operation_latency_ms=accepted.operation_latency_ms + 1,
    )
    forged_link = replace(link, accepted_outcome=forged_accepted)
    forged_composition = replace(composition, links=(forged_link,))

    with pytest.raises(InvalidArtifactRequirement, match="durable accepted"):
        repository.accept_retrieval_composition(
            retrieval,
            forged_composition,
            requirement,
        )


def test_cross_plan_link_checks_require_one_accepted_plan(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect

    url = f"sqlite:///{tmp_path / 'cross-plan-checks.db'}"
    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    checks = {
        check["name"]
        for check in inspect(create_engine(url)).get_check_constraints(
            "result_extraction_links"
        )
    }

    assert "ck_result_extraction_links_artifact_same_plan" in checks
    assert "ck_result_extraction_links_rejection_same_plan" in checks


def test_concurrent_identical_composition_acceptance_is_idempotent(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import OutcomePolicy
    from argus.persistence.search_ledger import create_search_ledger_repository
    from tests.test_extraction_outcomes import _artifact, _plan, _raw, _request

    url = f"sqlite:///{tmp_path / 'composition-concurrent.db'}"
    repositories = [
        create_search_ledger_repository(url),
        create_search_ledger_repository(url, create_schema=False),
    ]
    accepted = ExtractionFinalizer(
        repository=repositories[0],
        clock=lambda: "2026-07-27T12:00:00Z",
    ).finalize_extraction(
        _request("composition-concurrent-run"),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    link = ResultExtractionLink.from_accepted(
        link_ref="concurrent-link",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )
    retrieval = Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",))
    requirement = _requirement()
    composition = compose_retrieval_evidence(retrieval, (link,), requirement)
    barrier = Barrier(2)

    def accept(index):
        barrier.wait()
        return repositories[index].accept_retrieval_composition(
            retrieval,
            composition,
            requirement,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(accept, range(2)))

    assert receipts[0] == receipts[1]


def test_postgresql_concurrent_identical_composition_acceptance(
    postgres_ledger_url,
):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import OutcomePolicy
    from argus.persistence.search_ledger import create_search_ledger_repository
    from tests.test_extraction_outcomes import _artifact, _plan, _raw, _request

    repositories = [
        create_search_ledger_repository(postgres_ledger_url),
        create_search_ledger_repository(
            postgres_ledger_url,
            create_schema=False,
        ),
    ]
    accepted = ExtractionFinalizer(
        repository=repositories[0],
        clock=lambda: "2026-07-27T12:00:00Z",
    ).finalize_extraction(
        _request("postgres-composition-concurrent"),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    link = ResultExtractionLink.from_accepted(
        link_ref="postgres-concurrent-link",
        result_cluster_ref="cluster-1",
        accepted_outcome=accepted,
        required=True,
    )
    retrieval = Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",))
    requirement = _requirement()
    composition = compose_retrieval_evidence(retrieval, (link,), requirement)
    barrier = Barrier(2)

    def accept(index):
        barrier.wait()
        return repositories[index].accept_retrieval_composition(
            retrieval,
            composition,
            requirement,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        receipts = list(executor.map(accept, range(2)))

    assert receipts[0] == receipts[1]


def test_repository_rejects_forged_typed_outcome_with_real_receipt(tmp_path):
    from argus.extraction.finalizer import ExtractionFinalizer
    from argus.extraction.outcomes import OutcomePolicy
    from argus.persistence.search_ledger import create_search_ledger_repository
    from tests.test_extraction_outcomes import _artifact, _plan, _raw, _request

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'forged-composition.db'}"
    )
    accepted = ExtractionFinalizer(
        repository=repository,
        clock=lambda: "2026-07-27T12:00:00Z",
    ).finalize_extraction(
        _request("forged-composition-run"),
        _plan(),
        _raw(artifact=_artifact()),
        OutcomePolicy(version="outcome-v1"),
    )
    forged = replace(accepted, operation_latency_ms=accepted.operation_latency_ms + 1)
    link = ResultExtractionLink.from_accepted(
        link_ref="forged-link",
        result_cluster_ref="cluster-1",
        accepted_outcome=forged,
        required=True,
    )
    retrieval = Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",))
    requirement = _requirement()
    composition = compose_retrieval_evidence(
        retrieval,
        (link,),
        requirement,
    )

    with pytest.raises(ValueError, match="durable accepted"):
        repository.accept_retrieval_composition(
            retrieval,
            composition,
            requirement,
        )


def test_nullable_composite_link_groups_are_match_full_and_checked(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path / 'match-full.db'}"
    config = Config()
    config.set_main_option("script_location", "migrations")
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")
    engine = create_engine(url)
    inspector = inspect(engine)
    checks = {
        check["name"]: check["sqltext"]
        for check in inspector.get_check_constraints("result_extraction_links")
    }
    foreign_keys = inspector.get_foreign_keys("result_extraction_links")
    with engine.connect() as connection:
        ddl = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='result_extraction_links'"
            )
        ).scalar_one()

    assert len(
        [fk for fk in foreign_keys if len(fk["constrained_columns"]) == 2]
    ) == 3
    assert ddl.upper().count("MATCH FULL") == 3
    assert "ck_result_extraction_links_acceptance_pair" in checks
    assert "ck_result_extraction_links_artifact_pair" in checks
    assert "ck_result_extraction_links_rejection_pair" in checks

    from sqlalchemy.exc import IntegrityError

    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO retrieval_compositions "
                "(receipt_ref, retrieval_acceptance_ref, requirement_ref, "
                "retrieval_outcome, artifact_outcome, composite_outcome, "
                "projection_json, source_fingerprint, accepted_at) VALUES "
                "('composition-check', 'retrieval-check', NULL, 'success', "
                "NULL, 'success', '{}', :fingerprint, "
                "'2026-07-27 12:00:00')"
            ),
            {"fingerprint": "f" * 64},
        )
    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO result_extraction_links "
                    "(id, composition_ref, result_cluster_ref, "
                    "extraction_acceptance_ref, extraction_plan_id) VALUES "
                    "('link-check', 'composition-check', 'cluster-check', "
                    "'fabricated-receipt', NULL)"
                )
            )
