"""Workflow domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from argus.contracts import AcceptedOperation, CanonicalOutcome
from argus.extraction.composition import (
    AggregateArtifactFloor,
    ArtifactRequirement,
    ArtifactSelection,
    ResultExtractionLink,
    compose_retrieval_evidence,
)
from argus.extraction.outcomes import ArtifactDisposition


_MAX_WORKFLOW_SELECTIONS = 200

# Workflow-owned aliases keep the adapter free of direct extraction imports.
WorkflowAggregateArtifactFloor = AggregateArtifactFloor
WorkflowArtifactDisposition = ArtifactDisposition
WorkflowArtifactRequirement = ArtifactRequirement
WorkflowArtifactSelection = ArtifactSelection
WorkflowResultExtractionLink = ResultExtractionLink
compose_workflow_evidence = compose_retrieval_evidence


@dataclass(frozen=True, slots=True)
class WorkflowEvidenceView:
    """Bounded composition facts derived from one accepted search operation."""

    outcome: CanonicalOutcome
    result_cluster_refs: tuple[str, ...]
    acceptance_receipt: str | None

    @classmethod
    def from_operation(
        cls,
        operation: AcceptedOperation,
        *,
        max_results: int,
    ) -> "WorkflowEvidenceView":
        if not isinstance(operation, AcceptedOperation):
            raise TypeError("workflow search requires an AcceptedOperation")
        if not 0 <= max_results <= _MAX_WORKFLOW_SELECTIONS:
            raise ValueError("workflow selections must be bounded")
        result = operation.result
        if result is None:
            return cls(operation.outcome, (), None)
        results = result.get("results")
        receipt = result.get("acceptance_receipt")
        if not isinstance(results, tuple) or not isinstance(receipt, Mapping):
            raise ValueError("accepted search lacks immutable evidence facts")
        receipt_ref = receipt.get("receipt_ref")
        if not isinstance(receipt_ref, str) or not receipt_ref:
            raise ValueError("accepted search lacks a durable receipt")
        selected = results[:max_results]
        if not all(isinstance(item, Mapping) for item in selected):
            raise ValueError("accepted search contains an invalid result projection")
        return cls(
            outcome=operation.outcome,
            result_cluster_refs=tuple(
                f"{operation.request_id}-{ordinal}"
                for ordinal, _ in enumerate(selected)
            ),
            acceptance_receipt=receipt_ref,
        )


class WorkflowKind(str, Enum):
    RECOVER_ARTICLE = "recover-article"
    CAPTURE_SITE = "capture-site"
    BUILD_RESEARCH_PACK = "build-research-pack"
    SEARCH_AND_SUMMARIZE = "search-and-summarize"


class WorkflowStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class CitationRef:
    id: str
    title: str
    url: str
    artifact_path: str
    note: str = ""


@dataclass
class SummarySection:
    heading: str
    body: str
    citation_ids: list[str] = field(default_factory=list)


@dataclass
class WorkflowArtifact:
    kind: str
    path: str
    description: str = ""


@dataclass
class StoredDocument:
    id: str
    url: str
    title: str
    artifact_path: str
    word_count: int = 0
    domain: str = ""
    role: str = "source"
    source_type: str = "web"
    extractor: str | None = None
    egress: str | None = None
    machine: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowResult:
    run_id: str
    kind: WorkflowKind
    status: WorkflowStatus
    target: str
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=None))
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status_url: str | None = None
    snapshot_dir: str = ""
    report_path: str | None = None
    manifest_path: str | None = None
    artifacts: list[WorkflowArtifact] = field(default_factory=list)
    documents: list[StoredDocument] = field(default_factory=list)
    citations: list[CitationRef] = field(default_factory=list)
    summary_sections: list[SummarySection] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
