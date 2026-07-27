from __future__ import annotations

from dataclasses import dataclass, replace

import pytest

from argus.contracts import CanonicalOutcome
from argus.extraction.composition import (
    AggregateArtifactFloor,
    ArtifactRequirement,
    ArtifactSelection,
    ResultExtractionLink,
    compose_retrieval_evidence,
)
from argus.extraction.outcomes import ArtifactDisposition


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
):
    suffix = cluster.removeprefix("cluster-")
    return ResultExtractionLink(
        link_ref=f"link-{suffix}",
        result_cluster_ref=cluster,
        extraction_run_id=(
            f"extract-{suffix}"
            if extraction_run_id is None
            else extraction_run_id
        ),
        extraction_outcome=outcome,
        artifact_disposition=disposition,
        artifact_ref=(
            f"artifact-{suffix}"
            if disposition is not ArtifactDisposition.NONE
            else None
        ),
        rejection_ref=None if outcome is CanonicalOutcome.SUCCESS else f"reject-{suffix}",
        acceptance_receipt=f"receipt-{suffix}",
        required=required,
        eligible_path=eligible_path,
        attempted=attempted,
        artifact_identity=(
            f"sha256:{suffix:0>64}"
            if disposition is not ArtifactDisposition.NONE
            else None
        ),
        access_scope="public",
        policy_versions=("plan-v1", "quality-v1", "complete-v1"),
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
    link = replace(_link(), acceptance_receipt=None)
    composition = compose_retrieval_evidence(
        Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",)),
        (link,),
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
    first = replace(
        _link("cluster-1"),
        extraction_run_id="extract-shared",
        artifact_ref="artifact-shared",
        artifact_identity="sha256:" + ("a" * 64),
        reuse_origin="reuse-authority-1",
    )
    second = replace(
        _link("cluster-2"),
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
    shared = {
        "extraction_run_id": "extract-shared",
        "artifact_ref": "artifact-shared",
        "artifact_identity": "sha256:" + ("a" * 64),
        "reuse_origin": "reuse-authority-1",
    }
    links = (
        replace(_link("cluster-1"), **shared),
        replace(_link("cluster-2"), **shared),
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
    from datetime import datetime

    from sqlalchemy import func, select

    from argus.persistence.search_ledger import (
        ExtractionOutcomeAcceptanceRow,
        ExtractionOutcomeArtifactRow,
        ExtractionOutcomePlanRow,
        ResultExtractionLinkRow,
        RetrievalCompositionRow,
        create_search_ledger_repository,
    )

    repository = create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'composition.db'}",
        create_schema=True,
    )
    now = datetime(2026, 7, 27, 12, 0)
    with repository.session_factory.begin() as session:
        session.add(
            ExtractionOutcomePlanRow(
                id="plan-row-1",
                plan_ref="plan-1",
                extraction_run_id="extract-1",
                request_id="request-1",
                normalized_url="https://example.com/article",
                access_scope="public",
                mode="default",
                plan_json="{}",
                source_fingerprint="a" * 64,
                created_at=now,
            )
        )
        session.add(
            ExtractionOutcomeArtifactRow(
                id="artifact-row-1",
                plan_id="plan-row-1",
                artifact_ref="artifact-1",
                content_identity="sha256:" + ("a" * 64),
                content_text="artifact",
                disposition="usable",
                quality_passed=True,
                is_complete=True,
                evaluation_json="{}",
            )
        )
        session.add(
            ExtractionOutcomeAcceptanceRow(
                receipt_ref="receipt-1",
                plan_id="plan-row-1",
                outcome="success",
                artifact_disposition="usable",
                outcome_policy_version="outcome-v1",
                projection_json="{}",
                acceptance_fingerprint="b" * 64,
                accepted_at=now,
                scope="sqlite_development",
            )
        )
    retrieval = Retrieval(CanonicalOutcome.SUCCESS, ("cluster-1",))
    requirement = _requirement()
    composition = compose_retrieval_evidence(
        retrieval,
        (_link(),),
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
