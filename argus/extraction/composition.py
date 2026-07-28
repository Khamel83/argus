"""Pure composition of accepted retrieval and extraction evidence."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from argus.contracts import CanonicalOutcome
from argus.extraction.outcomes import (
    AcceptedExtractionOutcome,
    ArtifactDisposition,
    ExtractionAcceptanceReceipt,
)

_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_LINKS = 200
_DISPOSITION_RANK = {
    ArtifactDisposition.NONE: 0,
    ArtifactDisposition.DIAGNOSTIC_ONLY: 1,
    ArtifactDisposition.PARTIAL: 2,
    ArtifactDisposition.USABLE: 3,
}
_TERMINAL_RETRIEVAL = {
    CanonicalOutcome.EMPTY,
    CanonicalOutcome.INVALID_REQUEST,
    CanonicalOutcome.AUTHENTICATION_REJECTED,
    CanonicalOutcome.POLICY_REJECTED,
    CanonicalOutcome.TIMEOUT,
    CanonicalOutcome.PERSISTENCE_FAILED,
    CanonicalOutcome.PROVIDERS_FAILED,
    CanonicalOutcome.EXTRACTION_FAILED,
    CanonicalOutcome.UNREADY,
}


@runtime_checkable
class RetrievalEvidenceView(Protocol):
    """Only the S3 facts required from the future accepted retrieval."""

    outcome: CanonicalOutcome
    result_cluster_refs: tuple[str, ...]
    acceptance_receipt: object | None


@dataclass(frozen=True, slots=True)
class ArtifactSelection:
    result_cluster_ref: str
    required: bool
    minimum_disposition: ArtifactDisposition


@dataclass(frozen=True, slots=True)
class AggregateArtifactFloor:
    count: int
    minimum_disposition: ArtifactDisposition


@dataclass(frozen=True, slots=True)
class ArtifactRequirement:
    requirement_ref: str
    selections: tuple[ArtifactSelection, ...]
    aggregate_floor: AggregateArtifactFloor
    max_extractions: int
    deadline_ms: int
    spend_policy_ref: str

    def __post_init__(self) -> None:
        if isinstance(self.selections, list):
            object.__setattr__(self, "selections", tuple(self.selections))


@dataclass(frozen=True, slots=True)
class ResultExtractionLink:
    link_ref: str
    result_cluster_ref: str
    extraction_run_id: str | None
    extraction_outcome: CanonicalOutcome
    artifact_disposition: ArtifactDisposition
    artifact_ref: str | None
    rejection_ref: str | None
    acceptance_receipt: object | None
    required: bool
    eligible_path: bool
    attempted: bool
    artifact_identity: str | None = None
    access_scope: str | None = None
    policy_versions: tuple[str, ...] = ()
    reuse_origin: str | None = None
    accepted_outcome: AcceptedExtractionOutcome | None = None

    def __post_init__(self) -> None:
        if isinstance(self.policy_versions, list):
            object.__setattr__(
                self,
                "policy_versions",
                tuple(self.policy_versions),
            )

    @classmethod
    def from_accepted(
        cls,
        *,
        link_ref: str,
        result_cluster_ref: str,
        accepted_outcome: AcceptedExtractionOutcome,
        required: bool,
        reuse_origin: str | None = None,
    ) -> "ResultExtractionLink":
        if not isinstance(accepted_outcome, AcceptedExtractionOutcome):
            raise InvalidArtifactRequirement(
                "extraction link requires a typed accepted outcome"
            )
        artifact = accepted_outcome.artifact
        rejection = accepted_outcome.rejection
        attempted = any(
            step.decision is not None and step.decision.value == "invoked"
            for step in accepted_outcome.steps
        )
        return cls(
            link_ref=link_ref,
            result_cluster_ref=result_cluster_ref,
            extraction_run_id=accepted_outcome.extraction_run_id,
            extraction_outcome=accepted_outcome.outcome,
            artifact_disposition=accepted_outcome.artifact_disposition,
            artifact_ref=artifact.artifact_ref if artifact is not None else None,
            rejection_ref=(
                rejection.rejection_ref if rejection is not None else None
            ),
            acceptance_receipt=accepted_outcome.acceptance_receipt,
            required=required,
            eligible_path=True,
            attempted=attempted,
            artifact_identity=(
                artifact.content_identity if artifact is not None else None
            ),
            access_scope=accepted_outcome.plan.access_scope,
            policy_versions=(
                accepted_outcome.plan.extraction_plan_version,
                accepted_outcome.plan.quality_policy_version,
                accepted_outcome.plan.completeness_policy_version,
                accepted_outcome.extraction_outcome_policy_version,
            ),
            reuse_origin=reuse_origin,
            accepted_outcome=accepted_outcome,
        )


@dataclass(frozen=True, slots=True)
class RetrievalComposition:
    retrieval_outcome: CanonicalOutcome
    artifact_outcome: CanonicalOutcome | None
    composite_outcome: CanonicalOutcome
    accepted_artifact_refs: tuple[str, ...]
    degraded_artifact_refs: tuple[str, ...]
    rejected_extraction_refs: tuple[str, ...]
    links: tuple[ResultExtractionLink, ...]
    composition_trace: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CompositionAcceptanceReceipt:
    receipt_ref: str
    accepted_at: str
    scope: str


class InvalidArtifactRequirement(ValueError):
    outcome = CanonicalOutcome.INVALID_REQUEST

    def __init__(self, message: str = "artifact requirement is invalid"):
        super().__init__(message)


def _safe_ref(value: object) -> bool:
    return isinstance(value, str) and _SAFE_REF.fullmatch(value) is not None


def _invalid(message: str) -> InvalidArtifactRequirement:
    return InvalidArtifactRequirement(message)


def _validate_requirement_shape(requirement: ArtifactRequirement) -> None:
    if not isinstance(requirement, ArtifactRequirement):
        raise _invalid("artifact requirement must be typed")
    if (
        not _safe_ref(requirement.requirement_ref)
        or not _safe_ref(requirement.spend_policy_ref)
        or type(requirement.max_extractions) is not int
        or not 0 <= requirement.max_extractions <= _MAX_LINKS
        or type(requirement.deadline_ms) is not int
        or not 1 <= requirement.deadline_ms <= 120_000
        or not isinstance(requirement.aggregate_floor, AggregateArtifactFloor)
    ):
        raise _invalid("artifact requirement contains an invalid bounded field")
    selections = requirement.selections
    if not isinstance(selections, tuple) or len(selections) > _MAX_LINKS:
        raise _invalid("artifact selections exceed the bounded maximum")
    selection_refs = [selection.result_cluster_ref for selection in selections]
    if (
        len(set(selection_refs)) != len(selection_refs)
        or any(not _safe_ref(reference) for reference in selection_refs)
    ):
        raise _invalid("artifact selection references must be unique and bounded")
    for selection in selections:
        if (
            type(selection.required) is not bool
            or selection.minimum_disposition
            not in {ArtifactDisposition.USABLE, ArtifactDisposition.PARTIAL}
        ):
            raise _invalid("artifact selection minimum is invalid")
    floor = requirement.aggregate_floor
    if (
        type(floor.count) is not int
        or not 0 <= floor.count <= len(selections)
        or floor.minimum_disposition
        not in {ArtifactDisposition.USABLE, ArtifactDisposition.PARTIAL}
    ):
        raise _invalid("aggregate artifact floor is invalid")


def _validate_requirement(
    retrieval: RetrievalEvidenceView,
    links: tuple[ResultExtractionLink, ...],
    requirement: ArtifactRequirement,
) -> None:
    _validate_requirement_shape(requirement)
    selections = requirement.selections
    selection_refs = [selection.result_cluster_ref for selection in selections]
    retrieval_refs = tuple(retrieval.result_cluster_refs)
    if len(set(retrieval_refs)) != len(retrieval_refs):
        raise _invalid("accepted retrieval contains duplicate cluster references")
    if not set(selection_refs).issubset(retrieval_refs):
        raise _invalid("artifact selection is not in the accepted retrieval")
    if not isinstance(links, tuple) or len(links) > _MAX_LINKS:
        raise _invalid("result extraction links exceed the bounded maximum")
    link_refs = [link.link_ref for link in links]
    cluster_refs = [link.result_cluster_ref for link in links]
    if (
        len(set(link_refs)) != len(link_refs)
        or len(set(cluster_refs)) != len(cluster_refs)
        or set(cluster_refs) != set(selection_refs)
    ):
        raise _invalid("selected results and extraction links must be bijective")
    selection_by_ref = {
        selection.result_cluster_ref: selection for selection in selections
    }
    for link in links:
        selection = selection_by_ref[link.result_cluster_ref]
        if (
            not _safe_ref(link.link_ref)
            or (
                link.extraction_run_id is not None
                and not _safe_ref(link.extraction_run_id)
            )
            or not isinstance(link.extraction_outcome, CanonicalOutcome)
            or not isinstance(link.artifact_disposition, ArtifactDisposition)
            or type(link.required) is not bool
            or link.required is not selection.required
            or type(link.eligible_path) is not bool
            or type(link.attempted) is not bool
            or (link.attempted and not link.eligible_path)
        ):
            raise _invalid("result extraction link has inconsistent typed facts")
        if link.attempted and link.extraction_run_id is None:
            raise _invalid("attempted extraction link requires a run identity")
        if link.extraction_run_id is None:
            if (
                link.accepted_outcome is not None
                or link.acceptance_receipt is not None
                or link.artifact_ref is not None
                or link.rejection_ref is not None
                or link.artifact_disposition is not ArtifactDisposition.NONE
                or link.attempted
            ):
                raise _invalid(
                    "no-run extraction link contains fabricated durable facts"
                )
        else:
            accepted = link.accepted_outcome
            if (
                not isinstance(accepted, AcceptedExtractionOutcome)
                or not isinstance(
                    link.acceptance_receipt,
                    ExtractionAcceptanceReceipt,
                )
            ):
                raise _invalid(
                    "run-bearing link requires its typed accepted outcome"
                )
            expected = ResultExtractionLink.from_accepted(
                link_ref=link.link_ref,
                result_cluster_ref=link.result_cluster_ref,
                accepted_outcome=accepted,
                required=link.required,
                reuse_origin=link.reuse_origin,
            )
            if link != expected:
                raise _invalid(
                    "link facts do not match their accepted extraction outcome"
                )
        for reference in (
            link.artifact_ref,
            link.rejection_ref,
            link.artifact_identity,
            link.access_scope,
            link.reuse_origin,
        ):
            if reference is not None and not _safe_ref(reference):
                raise _invalid("result extraction link has an invalid bounded reference")
        if any(not _safe_ref(version) for version in link.policy_versions):
            raise _invalid("artifact policy versions must be bounded")
        artifact_exists = (
            link.artifact_disposition is not ArtifactDisposition.NONE
        )
        if artifact_exists != bool(
            link.artifact_ref
            and link.artifact_identity
            and link.access_scope
            and link.policy_versions
        ):
            raise _invalid("artifact identity facts do not match its disposition")
        if link.artifact_disposition is ArtifactDisposition.USABLE and (
            link.extraction_outcome is not CanonicalOutcome.SUCCESS
            or link.rejection_ref is not None
        ):
            raise _invalid("usable artifact link contradicts extraction outcome")
        if link.artifact_disposition is ArtifactDisposition.PARTIAL and (
            link.extraction_outcome is not CanonicalOutcome.DEGRADED
            or link.rejection_ref is None
        ):
            raise _invalid("partial artifact link lacks its bounded rejection")
        if link.artifact_disposition in {
            ArtifactDisposition.DIAGNOSTIC_ONLY,
            ArtifactDisposition.NONE,
        } and link.extraction_outcome in {
            CanonicalOutcome.SUCCESS,
            CanonicalOutcome.DEGRADED,
        }:
            raise _invalid("rejected artifact link has a success-like outcome")
        if (
            link.extraction_run_id is not None
            and link.extraction_outcome
            not in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
            and link.rejection_ref is None
            and link.extraction_outcome is not CanonicalOutcome.PERSISTENCE_FAILED
        ):
            raise _invalid("failed extraction link lacks its bounded rejection")

    by_extraction: dict[str, list[ResultExtractionLink]] = {}
    by_artifact: dict[str, list[ResultExtractionLink]] = {}
    for link in links:
        if link.extraction_run_id is not None:
            by_extraction.setdefault(link.extraction_run_id, []).append(link)
        if link.artifact_ref is not None:
            by_artifact.setdefault(link.artifact_ref, []).append(link)
    for group in (*by_extraction.values(), *by_artifact.values()):
        if len(group) < 2:
            continue
        identities = {
            (
                link.artifact_ref,
                link.artifact_identity,
                link.access_scope,
                link.policy_versions,
                link.reuse_origin,
            )
            for link in group
        }
        if len(identities) != 1 or group[0].reuse_origin is None:
            raise _invalid(
                "many-to-one extraction reuse lacks one proven reuse authority"
            )
    if len(by_extraction) > requirement.max_extractions:
        raise _invalid("distinct extraction runs exceed max_extractions")


def _meets(
    link: ResultExtractionLink,
    minimum: ArtifactDisposition,
) -> bool:
    return (
        _DISPOSITION_RANK[link.artifact_disposition]
        >= _DISPOSITION_RANK[minimum]
    )


def compose_retrieval_evidence(
    accepted_retrieval: RetrievalEvidenceView,
    result_extraction_links,
    artifact_requirement: ArtifactRequirement | None,
) -> RetrievalComposition:
    """Apply the declared per-result and aggregate artifact floors."""
    if not isinstance(accepted_retrieval, RetrievalEvidenceView):
        raise _invalid("accepted retrieval does not implement the evidence view")
    try:
        retrieval_outcome = CanonicalOutcome(accepted_retrieval.outcome)
    except (TypeError, ValueError) as error:
        raise _invalid("accepted retrieval outcome is not canonical") from error
    try:
        links = tuple(result_extraction_links)
    except TypeError as error:
        raise _invalid("result extraction links must be a bounded sequence") from error

    if artifact_requirement is None:
        if links:
            raise _invalid("plain retrieval cannot carry extraction links")
        return RetrievalComposition(
            retrieval_outcome=retrieval_outcome,
            artifact_outcome=None,
            composite_outcome=retrieval_outcome,
            accepted_artifact_refs=(),
            degraded_artifact_refs=(),
            rejected_extraction_refs=(),
            links=(),
            composition_trace=("no_artifact_requirement",),
        )

    _validate_requirement_shape(artifact_requirement)
    if retrieval_outcome in _TERMINAL_RETRIEVAL:
        if links:
            raise _invalid("terminal retrieval cannot carry extraction links")
        return RetrievalComposition(
            retrieval_outcome=retrieval_outcome,
            artifact_outcome=None,
            composite_outcome=retrieval_outcome,
            accepted_artifact_refs=(),
            degraded_artifact_refs=(),
            rejected_extraction_refs=(),
            links=(),
            composition_trace=("retrieval_precedes_extraction",),
        )

    _validate_requirement(accepted_retrieval, links, artifact_requirement)
    selection_by_ref = {
        selection.result_cluster_ref: selection
        for selection in artifact_requirement.selections
    }
    accepted_refs = tuple(
        link.artifact_ref
        for link in links
        if link.artifact_ref is not None
        and link.artifact_disposition
        in {ArtifactDisposition.USABLE, ArtifactDisposition.PARTIAL}
    )
    degraded_refs = tuple(
        link.artifact_ref
        for link in links
        if link.artifact_ref is not None
        and link.artifact_disposition is ArtifactDisposition.PARTIAL
    )
    rejected_refs = tuple(
        link.extraction_run_id
        for link in links
        if link.extraction_run_id is not None
        if link.extraction_outcome
        not in {CanonicalOutcome.SUCCESS, CanonicalOutcome.DEGRADED}
    )

    if accepted_retrieval.acceptance_receipt is None or any(
        link.extraction_run_id is not None and link.acceptance_receipt is None
        for link in links
    ):
        artifact_outcome = CanonicalOutcome.PERSISTENCE_FAILED
        composite = CanonicalOutcome.PERSISTENCE_FAILED
        reason = "durable_acceptance_missing"
    else:
        required_met = all(
            not selection.required
            or _meets(link, selection.minimum_disposition)
            for link in links
            for selection in (selection_by_ref[link.result_cluster_ref],)
        )
        aggregate_pass_count = sum(
            _meets(link, artifact_requirement.aggregate_floor.minimum_disposition)
            for link in links
        )
        aggregate_met = (
            aggregate_pass_count >= artifact_requirement.aggregate_floor.count
        )
        unavailable_required = any(
            selection_by_ref[link.result_cluster_ref].required
            and not link.eligible_path
            for link in links
        )
        possible_count = sum(
            _meets(link, artifact_requirement.aggregate_floor.minimum_disposition)
            or (link.eligible_path and link.attempted)
            for link in links
        )
        aggregate_impossible = (
            possible_count < artifact_requirement.aggregate_floor.count
        )
        if unavailable_required or aggregate_impossible:
            artifact_outcome = CanonicalOutcome.UNREADY
            composite = CanonicalOutcome.UNREADY
            reason = "artifact_floor_unready"
        elif not required_met or not aggregate_met:
            artifact_outcome = CanonicalOutcome.EXTRACTION_FAILED
            composite = CanonicalOutcome.EXTRACTION_FAILED
            reason = "artifact_floor_unmet"
        else:
            degraded = any(
                link.artifact_disposition is ArtifactDisposition.PARTIAL
                or link.extraction_outcome is not CanonicalOutcome.SUCCESS
                for link in links
            )
            artifact_outcome = (
                CanonicalOutcome.DEGRADED
                if degraded
                else CanonicalOutcome.SUCCESS
            )
            composite = (
                CanonicalOutcome.DEGRADED
                if retrieval_outcome is CanonicalOutcome.DEGRADED or degraded
                else retrieval_outcome
            )
            reason = "artifact_floor_met"

    return RetrievalComposition(
        retrieval_outcome=retrieval_outcome,
        artifact_outcome=artifact_outcome,
        composite_outcome=composite,
        accepted_artifact_refs=accepted_refs,
        degraded_artifact_refs=degraded_refs,
        rejected_extraction_refs=rejected_refs,
        links=links,
        composition_trace=(reason,),
    )
