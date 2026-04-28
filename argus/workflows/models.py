"""Workflow domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class WorkflowKind(str, Enum):
    RECOVER_ARTICLE = "recover-article"
    CAPTURE_SITE = "capture-site"
    BUILD_RESEARCH_PACK = "build-research-pack"


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
