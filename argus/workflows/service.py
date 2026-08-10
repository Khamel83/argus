"""Workflow execution for retrieval-oriented Argus features."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import uuid
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from argus.corpus import (
    CorpusPaths,
    describe_corpus_paths,
    get_corpus_paths,
    mirror_legacy_docs_cache,
)
from argus.contracts import AcceptedOperation, CanonicalOutcome
from argus.logging import get_logger
from argus.workflows.models import (
    CitationRef,
    StoredDocument,
    SummarySection,
    WorkflowArtifact,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)
from argus.workflows.summarizer import get_summarizer

logger = get_logger("workflows")

_ARTIFACT_DEFAULT_BYTES = 64 * 1024
_ARTIFACT_MAX_BYTES = 256 * 1024
_ARTIFACT_MEDIA_TYPES = {
    "report": "text/markdown; charset=utf-8",
    "manifest": "application/json; charset=utf-8",
}
_COST_STATES = {"confirmed", "estimated", "uncertain", "unavailable"}


class WorkflowArtifactError(RuntimeError):
    """Base class for safe workflow-artifact read failures."""


class WorkflowArtifactNotFound(WorkflowArtifactError):
    """The run or allowlisted artifact does not exist."""


class WorkflowArtifactNotReady(WorkflowArtifactError):
    """The workflow has not reached a terminal state."""


class WorkflowArtifactUnavailable(WorkflowArtifactError):
    """A registered artifact failed containment, hashing, or decoding."""


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _parse_dt(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _slug_from_url(url: str) -> str:
    parsed = urlparse(url)
    seed = f"{parsed.netloc}{parsed.path}".strip("/") or parsed.netloc or "target"
    from argus.corpus.paths import slugify

    return slugify(seed, default="target")


def _domain_root(hostname: str) -> str:
    host = hostname.lower().lstrip("www.")
    parts = [p for p in host.split(".") if p]
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def _normal_url(url: str) -> str:
    """Stable local dedupe key; execution authority remains injected."""
    parsed = urlparse(url)
    return (
        parsed._replace(
            scheme=parsed.scheme.lower(),
            netloc=parsed.netloc.lower(),
            fragment="",
        )
        .geturl()
        .rstrip("/")
    )


def _lead_text(text: str, limit: int = 280) -> str:
    cleaned = " ".join(part.strip() for part in text.splitlines() if part.strip())
    cleaned = cleaned[:limit].strip()
    if cleaned and not cleaned.endswith("."):
        cleaned += "..."
    return cleaned


class WorkflowOperationFailure(RuntimeError):
    """Bounded carrier for one already-classified accepted-operation failure."""

    def __init__(self, outcome, code: str):
        self.outcome = outcome
        self.operation_code = code
        self.stable_code = f"workflow_composition_{code}"
        super().__init__(self.stable_code)


class WorkflowService:
    """Async workflow executor with in-memory run tracking."""

    # Kept as class attributes so callers can map typed failures without
    # importing the workflow implementation module.
    ArtifactNotFound = WorkflowArtifactNotFound
    ArtifactNotReady = WorkflowArtifactNotReady
    ArtifactUnavailable = WorkflowArtifactUnavailable

    def __init__(
        self,
        accepted_operations,
        *,
        corpus_paths: CorpusPaths | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        caller: str = "workflows",
    ):
        self._accepted_operations = accepted_operations
        self._paths = corpus_paths or get_corpus_paths()
        self._runs: dict[str, WorkflowResult] = {}
        self._progress = progress_callback
        self._caller = caller or "workflows"

    def _report(self, current: int, total: int, message: str) -> None:
        if self._progress:
            try:
                self._progress(current, total, message)
            except Exception:
                pass

    async def _operation_search(
        self,
        run: WorkflowResult,
        *,
        query: str,
        mode: str,
        max_results: int,
    ):
        request = type(
            "WorkflowSearchRequest",
            (),
            {
                "query": query,
                "mode": mode,
                "max_results": max_results,
                "providers": None,
                "free_only": False,
                "caller": str(run.metadata.get("caller_label") or self._caller),
                "session_id": None,
                "include_attribution": False,
            },
        )()
        return await self._accepted_operations.search(
            request,
            principal=self._caller_for_run(run),
            request_id=uuid.uuid4().hex,
        )

    async def _operation_recover(
        self,
        run: WorkflowResult,
        *,
        url: str,
        title: str | None,
        domain: str | None,
    ):
        request = type(
            "WorkflowRecoverRequest",
            (),
            {"url": url, "title": title, "domain": domain},
        )()
        return await self._accepted_operations.recover(
            request,
            principal=self._caller_for_run(run),
            request_id=uuid.uuid4().hex,
        )

    async def _operation_acquire_site(
        self,
        run: WorkflowResult,
        *,
        url: str,
        soft_page_limit: int,
        hard_page_limit: int,
    ):
        request = type(
            "WorkflowSiteAcquisitionRequest",
            (),
            {
                "url": url,
                "soft_page_limit": soft_page_limit,
                "hard_page_limit": hard_page_limit,
                "caller": str(run.metadata.get("caller_label") or self._caller),
            },
        )()
        return await self._accepted_operations.acquire_site(
            request,
            principal=self._caller_for_run(run),
            request_id=uuid.uuid4().hex,
        )

    def get_paths(self) -> dict[str, Any]:
        return describe_corpus_paths()

    def get_run(self, run_id: str) -> WorkflowResult | None:
        run = self._runs.get(run_id)
        if run is not None:
            return run

        state_path = self._paths.workflow_runs_dir / f"{run_id}.json"
        if not state_path.exists():
            return None

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            run = self._deserialize_run(payload)
            self._runs[run.run_id] = run
            return run
        except Exception as exc:
            logger.warning("Failed to load persisted workflow run %s: %s", run_id, exc)
            return None

    def get_public_status(
        self,
        run: WorkflowResult,
        *,
        runtime: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Return the path-free status projection used by remote callers."""
        artifacts = []
        for kind in ("report", "manifest"):
            registered = next(
                (artifact for artifact in run.artifacts if artifact.kind == kind),
                None,
            )
            if registered is None or run.status not in {
                WorkflowStatus.COMPLETED,
                WorkflowStatus.FAILED,
            }:
                artifacts.append(
                    {
                        "kind": kind,
                        "available": False,
                        "description": (
                            registered.description if registered is not None else ""
                        ),
                        "media_type": _ARTIFACT_MEDIA_TYPES[kind],
                        "size_bytes": None,
                        "sha256": None,
                    }
                )
                continue
            try:
                metadata = self._artifact_metadata(run, kind)
            except WorkflowArtifactNotFound:
                metadata = {
                    "kind": kind,
                    "available": False,
                    "description": registered.description,
                    "media_type": _ARTIFACT_MEDIA_TYPES[kind],
                    "size_bytes": None,
                    "sha256": None,
                }
            artifacts.append({**metadata, "description": registered.description})

        documents = list(run.documents)
        source_urls = {
            _normal_url(document.url)
            for document in documents
            if isinstance(document.url, str) and document.url
        }
        domains = {
            _domain_root(document.domain or urlparse(document.url).netloc)
            for document in documents
            if document.url or document.domain
        }
        citations = []
        for citation in run.citations:
            evidence_ids = [citation.id]
            document = next(
                (item for item in documents if item.id == citation.id),
                None,
            )
            if document is not None:
                evidence_ids.extend(
                    self._safe_evidence_ids(document.metadata.get("evidence_ids"))
                )
            citations.append(
                {
                    "id": citation.id,
                    "title": citation.title,
                    "url": citation.url,
                    "disposition": citation.artifact_disposition,
                    "evidence_ids": list(dict.fromkeys(evidence_ids)),
                }
            )

        partial_reasons, degraded_reasons = self._status_reasons(run)
        return {
            "run_id": run.run_id,
            "kind": run.kind.value,
            "status": run.status.value,
            "target": run.target,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None,
            "status_url": run.status_url,
            "artifacts": artifacts,
            "citations": citations,
            "source_count": len(source_urls),
            "domain_count": len({domain for domain in domains if domain}),
            "primary_source_count": sum(
                1 for document in documents if self._is_primary_source(document)
            ),
            "partial_reasons": partial_reasons,
            "degraded_reasons": degraded_reasons,
            "cost_state": self._cost_state(run),
            "runtime": self._runtime_projection(runtime, run.metadata.get("runtime")),
            "error_code": run.error if run.status == WorkflowStatus.FAILED else None,
        }

    def read_artifact(
        self,
        run: WorkflowResult,
        artifact: str,
        *,
        offset: int = 0,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Read an allowlisted workflow artifact without exposing its path."""
        if artifact not in _ARTIFACT_MEDIA_TYPES:
            raise WorkflowArtifactNotFound("workflow artifact is not available")
        if run.status not in {WorkflowStatus.COMPLETED, WorkflowStatus.FAILED}:
            raise WorkflowArtifactNotReady("workflow artifact is not ready")
        metadata = self._artifact_metadata(run, artifact)
        registered_path = self._resolve_registered_artifact(run, artifact)
        bounded_offset = max(0, int(offset))
        bounded_limit = (
            _ARTIFACT_DEFAULT_BYTES
            if max_bytes is None
            else min(max(1, int(max_bytes)), _ARTIFACT_MAX_BYTES)
        )
        try:
            read_offset, chunk = self._read_utf8_slice(
                registered_path,
                offset=bounded_offset,
                max_bytes=bounded_limit,
            )
        except (OSError, UnicodeDecodeError) as exc:
            raise WorkflowArtifactUnavailable(
                "workflow artifact cannot be read"
            ) from exc
        try:
            content = chunk.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WorkflowArtifactUnavailable(
                "workflow artifact is not valid UTF-8"
            ) from exc
        bytes_returned = len(chunk)
        total_bytes = metadata["size_bytes"]
        truncated = read_offset + bytes_returned < total_bytes
        return {
            "run_id": run.run_id,
            "artifact": artifact,
            "kind": artifact,
            "media_type": metadata["media_type"],
            "total_bytes": total_bytes,
            "offset": read_offset,
            "bytes_returned": bytes_returned,
            "truncated": truncated,
            "next_offset": read_offset + bytes_returned if truncated else None,
            "sha256": metadata["sha256"],
            "content": content,
        }

    @staticmethod
    def _utf8_boundary(path: Path, offset: int) -> int:
        """Normalize an arbitrary byte offset to the start of its code point."""
        if offset <= 0:
            return 0
        try:
            size = path.stat().st_size
            position = min(offset, size)
            with path.open("rb") as handle:
                for _ in range(3):
                    if position == 0:
                        break
                    handle.seek(position)
                    marker = handle.read(1)
                    if not marker or marker[0] & 0xC0 != 0x80:
                        break
                    position -= 1
            return position
        except OSError:
            raise

    @classmethod
    def _read_utf8_slice(
        cls,
        path: Path,
        *,
        offset: int,
        max_bytes: int,
    ) -> tuple[int, bytes]:
        read_offset = cls._utf8_boundary(path, offset)
        with path.open("rb") as handle:
            handle.seek(read_offset)
            chunk = handle.read(max_bytes)
            if not chunk:
                return read_offset, chunk
            try:
                chunk.decode("utf-8")
            except UnicodeDecodeError as exc:
                if exc.reason != "unexpected end of data":
                    raise
                if exc.start == 0:
                    # A single code point is larger than the requested slice.
                    # Read only enough bytes to return that complete code point.
                    handle.seek(read_offset)
                    expanded = handle.read(min(4, max_bytes + 3))
                    expanded.decode("utf-8")
                    return read_offset, expanded
                complete = chunk[: exc.start]
                complete.decode("utf-8")
                return read_offset, complete
            return read_offset, chunk

    def _artifact_metadata(self, run: WorkflowResult, artifact: str) -> dict[str, Any]:
        path = self._resolve_registered_artifact(run, artifact)
        digest = hashlib.sha256()
        total_bytes = 0
        try:
            with path.open("rb") as handle:
                while chunk := handle.read(64 * 1024):
                    digest.update(chunk)
                    total_bytes += len(chunk)
        except (OSError, ValueError) as exc:
            raise WorkflowArtifactUnavailable(
                "workflow artifact identity cannot be verified"
            ) from exc
        return {
            "kind": artifact,
            "available": True,
            "media_type": _ARTIFACT_MEDIA_TYPES[artifact],
            "size_bytes": total_bytes,
            "sha256": digest.hexdigest(),
        }

    def _resolve_registered_artifact(self, run: WorkflowResult, artifact: str) -> Path:
        registered = next(
            (item for item in run.artifacts if item.kind == artifact),
            None,
        )
        if registered is None:
            raise WorkflowArtifactNotFound("workflow artifact is not available")
        try:
            snapshot = Path(run.snapshot_dir).resolve()
            path = Path(registered.path).resolve()
            if path == snapshot or not path.is_relative_to(snapshot):
                raise WorkflowArtifactUnavailable(
                    "workflow artifact failed containment verification"
                )
            if not path.is_file():
                raise WorkflowArtifactUnavailable(
                    "workflow artifact failed file verification"
                )
        except WorkflowArtifactUnavailable:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise WorkflowArtifactUnavailable(
                "workflow artifact failed containment verification"
            ) from exc
        return path

    @staticmethod
    def _safe_evidence_ids(value: Any) -> list[str]:
        if isinstance(value, str):
            value = [value]
        if not isinstance(value, (list, tuple, set)):
            return []
        return [
            item.strip()
            for item in value
            if isinstance(item, str)
            and item.strip()
            and len(item.strip()) <= 128
            and all(
                character.isalnum() or character in ":._-" for character in item.strip()
            )
        ]

    @staticmethod
    def _is_primary_source(document: StoredDocument) -> bool:
        values = {
            str(document.role or "").lower(),
            str(document.source_type or "").lower(),
        }
        return bool(values & {"primary", "official", "official_doc", "official_docs"})

    @classmethod
    def _status_reasons(cls, run: WorkflowResult) -> tuple[list[str], list[str]]:
        partial: list[str] = []
        degraded: list[str] = []
        for document in run.documents:
            disposition = str(document.metadata.get("artifact_disposition", ""))
            if disposition == "partial":
                partial.append(f"partial_artifact:{document.id}")
        composition = run.metadata.get("composition")
        if isinstance(composition, dict):
            if composition.get("outcome") == CanonicalOutcome.DEGRADED.value:
                degraded.append("workflow_composition_degraded")
            if composition.get("degraded_artifact_refs"):
                degraded.append("degraded_artifacts_present")
            if composition.get("rejected_extraction_refs"):
                degraded.append("rejected_extractions_present")
        for target, values in (
            (partial, run.metadata.get("partial_reasons")),
            (degraded, run.metadata.get("degraded_reasons")),
        ):
            if isinstance(values, str):
                values = [values]
            if isinstance(values, (list, tuple)):
                target.extend(
                    value.strip()
                    for value in values
                    if isinstance(value, str)
                    and value.strip()
                    and "/" not in value
                    and "\\" not in value
                    and len(value.strip()) <= 200
                )
        return list(dict.fromkeys(partial)), list(dict.fromkeys(degraded))

    @classmethod
    def _cost_state(cls, run: WorkflowResult) -> str:
        metadata = run.metadata
        explicit = metadata.get("cost_state")
        if isinstance(explicit, str) and explicit in _COST_STATES:
            return explicit
        spend = metadata.get("spend")
        if isinstance(spend, dict):
            availability = spend.get("availability")
            if availability == "available":
                return "confirmed"
            if availability in {"partial", "uncertain"}:
                return "uncertain"
        return "unavailable"

    @staticmethod
    def _runtime_projection(
        runtime: dict[str, Any] | None,
        metadata_runtime: Any,
    ) -> dict[str, Any]:
        source = runtime if isinstance(runtime, dict) else {}
        if not source and isinstance(metadata_runtime, dict):
            source = metadata_runtime
        build = source.get("build") if isinstance(source.get("build"), dict) else source
        deployment = (
            source.get("deployment")
            if isinstance(source.get("deployment"), dict)
            else {}
        )
        return {
            "version": str(build.get("version") or "unknown"),
            "source_revision": str(build.get("source_revision") or "unknown"),
            "image_identity": str(
                source.get("image_identity")
                or source.get("image_digest")
                or deployment.get("image_identity")
                or "unknown"
            ),
            "deployment_identity": str(
                source.get("deployment_identity")
                or source.get("deployment_id")
                or deployment.get("deployment_id")
                or "unknown"
            ),
        }

    def import_legacy_docs_cache(self, source_root: str) -> dict[str, Any]:
        return mirror_legacy_docs_cache(source_root, self._paths)

    async def start_recover_article(
        self,
        *,
        url: str,
        title: str | None = None,
        domain: str | None = None,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.RECOVER_ARTICLE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        asyncio.create_task(
            self._execute_run(
                run.run_id,
                self._recover_article_impl,
                url=url,
                title=title,
                domain=domain,
            )
        )
        return run

    async def start_capture_site(
        self,
        *,
        url: str,
        soft_page_limit: int = 75,
        hard_page_limit: int = 200,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.CAPTURE_SITE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        asyncio.create_task(
            self._execute_run(
                run.run_id,
                self._capture_site_impl,
                url=url,
                soft_page_limit=soft_page_limit,
                hard_page_limit=hard_page_limit,
            )
        )
        return run

    async def start_build_research_pack(
        self,
        *,
        topic: str,
        official_url: str | None = None,
        max_research_pages: int = 40,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.BUILD_RESEARCH_PACK,
            topic,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        asyncio.create_task(
            self._execute_run(
                run.run_id,
                self._build_research_pack_impl,
                topic=topic,
                official_url=official_url,
                max_research_pages=max_research_pages,
            )
        )
        return run

    async def start_search_and_summarize(
        self,
        *,
        query: str,
        max_search_results: int = 5,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        """Start a background search-and-summarize workflow.

        Returns a pending WorkflowResult; the actual work runs in the background.
        """
        run = self._create_run(
            WorkflowKind.SEARCH_AND_SUMMARIZE,
            query,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        asyncio.create_task(
            self._execute_run(
                run.run_id,
                self._search_and_summarize_impl,
                query=query,
                max_search_results=max_search_results,
            )
        )
        return run

    async def search_and_summarize(
        self,
        *,
        query: str,
        max_search_results: int = 5,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        """Run a search-and-summarize workflow synchronously and return the result."""
        run = self._create_run(
            WorkflowKind.SEARCH_AND_SUMMARIZE,
            query,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        return await self._execute_run(
            run.run_id,
            self._search_and_summarize_impl,
            query=query,
            max_search_results=max_search_results,
        )

    async def _search_and_summarize_impl(
        self,
        run: WorkflowResult,
        *,
        query: str,
        max_search_results: int = 5,
    ) -> WorkflowResult:
        """Implementation of the search-and-summarize workflow.

        Performs a search, extracts up to ``max_search_results`` pages, and uses the
        LLMSummarizer to synthesize a concise answer.
        """
        operation = await self._operation_search(
            run,
            query=query,
            mode="discovery",
            max_results=max_search_results,
        )
        docs, citations = await self._compose_search_documents(
            run,
            operation,
            max_results=max_search_results,
            section="search-results",
            role="source",
            source_type="web",
        )
        run.documents, run.citations = docs, citations
        summarizer = get_summarizer("llm")
        sections = await summarizer.summarize(
            title=query,
            prompt=query,
            documents=docs,
            citations=citations,
        )
        run.summary_sections = sections

        # Finalize run state
        self._finalize_run(
            run,
            title=query,
            report_name="report.md",
        )
        return run

    async def _compose_search_documents(
        self,
        run: WorkflowResult,
        operation: AcceptedOperation,
        *,
        max_results: int,
        section: str,
        role: str,
        source_type: str,
        selection_urls: tuple[str, ...] | None = None,
        citation_start: int = 0,
    ) -> tuple[list[StoredDocument], list[CitationRef]]:
        """Build documents solely from the accepted composition projection."""
        composition_kwargs = {
            "max_results": max_results,
            "principal": self._caller_for_run(run),
            "request_id": run.run_id,
        }
        if selection_urls is not None:
            composition_kwargs["selection_urls"] = selection_urls
        composed = await self._accepted_operations.compose_workflow(
            operation, **composition_kwargs
        )
        projection = composed.result or {}
        composition_record = {
            "outcome": composed.outcome.value,
            "requirement_ref": projection.get("requirement_ref"),
            "composition_receipt_ref": projection.get("composition_receipt_ref"),
            "accepted_artifact_refs": list(
                projection.get("accepted_artifact_refs", ())
            ),
            "degraded_artifact_refs": list(
                projection.get("degraded_artifact_refs", ())
            ),
            "rejected_extraction_refs": list(
                projection.get("rejected_extraction_refs", ())
            ),
            "composition_trace": list(projection.get("composition_trace", ())),
            "links": [dict(link) for link in projection.get("links", ())],
        }
        composition_records = run.metadata.setdefault("compositions", [])
        composition_records.append(composition_record)
        recorded_outcomes = [item["outcome"] for item in composition_records]
        terminal_failures = [
            outcome
            for outcome in recorded_outcomes
            if outcome
            not in {
                CanonicalOutcome.SUCCESS.value,
                CanonicalOutcome.DEGRADED.value,
            }
        ]
        aggregate_outcome = (
            CanonicalOutcome.PERSISTENCE_FAILED.value
            if CanonicalOutcome.PERSISTENCE_FAILED.value in terminal_failures
            else (
                terminal_failures[-1]
                if terminal_failures
                else (
                    CanonicalOutcome.DEGRADED.value
                    if CanonicalOutcome.DEGRADED.value in recorded_outcomes
                    else CanonicalOutcome.SUCCESS.value
                )
            )
        )
        run.metadata["composition"] = {
            "outcome": aggregate_outcome,
            "requirement_ref": composition_record["requirement_ref"],
            "composition_receipt_ref": composition_record["composition_receipt_ref"],
            "sub_compositions": composition_records,
            "links": [
                link for item in composition_records for link in item.get("links", ())
            ],
            "accepted_artifact_refs": [
                ref
                for item in composition_records
                for ref in item.get("accepted_artifact_refs", ())
            ],
            "degraded_artifact_refs": [
                ref
                for item in composition_records
                for ref in item.get("degraded_artifact_refs", ())
            ],
            "rejected_extraction_refs": [
                ref
                for item in composition_records
                for ref in item.get("rejected_extraction_refs", ())
            ],
            "composition_trace": [
                trace
                for item in composition_records
                for trace in item.get("composition_trace", ())
            ],
        }
        if projection.get("composition_receipt_ref"):
            run.metadata["composition_receipt_ref"] = projection[
                "composition_receipt_ref"
            ]
        if composed.outcome not in {
            CanonicalOutcome.SUCCESS,
            CanonicalOutcome.DEGRADED,
        }:
            code = (
                composed.error.code
                if composed.error is not None
                else composed.outcome.value
            )
            raise WorkflowOperationFailure(composed.outcome, code)
        if composed.result is None:
            raise WorkflowOperationFailure(
                CanonicalOutcome.PERSISTENCE_FAILED,
                CanonicalOutcome.PERSISTENCE_FAILED.value,
            )
        documents: list[StoredDocument] = []
        citations: list[CitationRef] = []
        output_dir = Path(run.snapshot_dir) / section
        output_dir.mkdir(parents=True, exist_ok=True)
        for ordinal, artifact in enumerate(
            projection["artifacts"],
            start=citation_start + 1,
        ):
            document = self._store_document(
                output_dir,
                citation_id=f"S{ordinal}",
                url=artifact["url"],
                title=artifact["title"],
                text=artifact["text"],
                word_count=artifact["word_count"],
                domain=urlparse(artifact["url"]).netloc.lower().lstrip("www."),
                role=role,
                source_type=source_type,
                extractor=artifact["extractor"],
                metadata={
                    "artifact_disposition": artifact["disposition"],
                    "lead_text": _lead_text(artifact["text"]),
                },
            )
            documents.append(document)
            citations.append(
                CitationRef(
                    id=document.id,
                    title=document.title,
                    url=document.url,
                    artifact_path=document.artifact_path,
                    note=(
                        f"{source_type}; artifact_disposition=partial"
                        if artifact["disposition"] == "partial"
                        else source_type
                    ),
                )
            )
        return documents, citations

    async def recover_article(
        self,
        *,
        url: str,
        title: str | None = None,
        domain: str | None = None,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.RECOVER_ARTICLE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        return await self._execute_run(
            run.run_id, self._recover_article_impl, url=url, title=title, domain=domain
        )

    async def capture_site(
        self,
        *,
        url: str,
        soft_page_limit: int = 75,
        hard_page_limit: int = 200,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.CAPTURE_SITE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        return await self._execute_run(
            run.run_id,
            self._capture_site_impl,
            url=url,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )

    async def build_research_pack(
        self,
        *,
        topic: str,
        official_url: str | None = None,
        max_research_pages: int = 40,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.BUILD_RESEARCH_PACK,
            topic,
            caller_identity=caller_identity,
            caller_label=caller_label,
        )
        return await self._execute_run(
            run.run_id,
            self._build_research_pack_impl,
            topic=topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
        )

    def _create_run(
        self,
        kind: WorkflowKind,
        target: str,
        *,
        caller_identity: str | None = None,
        caller_label: str = "",
    ) -> WorkflowResult:
        run_id = uuid.uuid4().hex[:12]
        slug = (
            _slug_from_url(target)
            if target.startswith(("http://", "https://"))
            else _slug_from_url(f"https://{target}")
        )
        snapshot_dir = self._paths.snapshots_dir / kind.value / slug / run_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        run = WorkflowResult(
            run_id=run_id,
            kind=kind,
            status=WorkflowStatus.PENDING,
            target=target,
            status_url=f"/api/workflows/{run_id}",
            snapshot_dir=str(snapshot_dir),
            metadata={
                "caller_identity": caller_identity or self._caller,
                "caller_label": caller_label,
            },
        )
        self._runs[run_id] = run
        return run

    def _caller_for_run(self, run: WorkflowResult) -> str:
        return str(run.metadata.get("caller_identity") or self._caller)

    async def _execute_run(self, run_id: str, handler, **kwargs) -> WorkflowResult:
        run = self._runs[run_id]
        run.status = WorkflowStatus.RUNNING
        run.started_at = datetime.now(tz=None)
        self._write_run_state(run)
        try:
            await handler(run, **kwargs)
        except WorkflowOperationFailure as exc:
            logger.warning(
                "Workflow %s stopped with accepted outcome %s",
                run.run_id,
                exc.outcome.value,
            )
            run.status = WorkflowStatus.FAILED
            run.error = exc.stable_code
            run.metadata["failure"] = {
                "outcome": exc.outcome.value,
                "code": exc.operation_code,
            }
            self._rewrite_failed_artifacts(run)
        except Exception as exc:
            logger.exception("Workflow %s failed", run.run_id)
            run.status = WorkflowStatus.FAILED
            run.error = type(exc).__name__
            self._rewrite_failed_artifacts(run)
        finally:
            if run.finished_at is None:
                run.finished_at = datetime.now(tz=None)
            self._write_run_state(run)
        return run

    async def _recover_article_impl(
        self, run: WorkflowResult, *, url: str, title: str | None, domain: str | None
    ):
        self._report(0, 3, "Searching for recovery candidates...")
        operation = await self._operation_recover(
            run, url=url, title=title, domain=domain
        )
        accepted_results = operation.result["results"] if operation.result else ()
        candidates = []
        seen = {_normal_url(url)}
        for result in accepted_results:
            normalized = _normal_url(str(result["url"]))
            if normalized in seen:
                continue
            seen.add(normalized)
            candidates.append(str(result["url"]))

        if not candidates:
            raise ValueError("No recovery candidates found")

        documents, citations = await self._compose_search_documents(
            run,
            operation,
            max_results=min(8, len(candidates)),
            section="recovered-sources",
            role="recovered_source",
            source_type="recovery_candidate",
            selection_urls=tuple(candidates[:8]),
        )
        self._report(1, 3, f"Extracted {len(documents)} candidate pages")
        if not documents:
            raise ValueError(
                "Recovery candidates were found but none could be extracted"
            )

        best = max(
            documents,
            key=lambda doc: (
                int(doc.metadata.get("quality_passed", False)),
                int(doc.metadata.get("is_complete", False)),
                doc.word_count,
            ),
        )
        run.metadata["recovered_url"] = best.url
        run.metadata["candidate_count"] = len(documents)
        run.metadata["search_run_id"] = operation.request_id

        prompt = f"Recover a dead article for {title or url}"
        sections = await get_summarizer().summarize(
            title=title or best.title or url,
            prompt=prompt,
            documents=documents,
            citations=citations,
        )
        sections.insert(
            0,
            SummarySection(
                heading="Recovered Article",
                body=(
                    f"Argus selected **{best.title or best.url}** as the best recovery candidate. "
                    f"Recovered URL: {best.url}"
                ),
                citation_ids=[best.id],
            ),
        )
        run.documents = documents
        run.citations = citations
        run.summary_sections = sections
        self._finalize_run(
            run,
            title=title or best.title or "Recovered Article",
            report_name="report.md",
        )

    async def _capture_site_impl(
        self,
        run: WorkflowResult,
        *,
        url: str,
        soft_page_limit: int,
        hard_page_limit: int,
    ):
        self._report(0, 4, "Discovering site URLs...")
        retrieval, candidates = await self._discover_site_urls(
            url,
            run=run,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )
        self._report(1, 4, f"Discovered {len(candidates)} URLs, extracting content...")
        documents, citations = await self._capture_explicit_urls(
            run,
            candidates,
            section="site-pages",
            role="site_page",
            source_type="site_capture",
            retrieval=retrieval,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )
        self._report(2, 4, f"Extracted {len(documents)} pages, generating summary...")
        if not documents:
            raise ValueError("Site capture did not yield any extractable pages")

        run.metadata["captured_pages"] = len(documents)
        run.metadata["candidate_urls"] = len(candidates)
        sections = await get_summarizer().summarize(
            title=url,
            prompt=f"Summarize the most important information from site {url}",
            documents=documents,
            citations=citations,
        )
        sections.insert(
            0,
            SummarySection(
                heading="Capture Scope",
                body=(
                    f"Argus stayed on-domain for `{url}` and saved {len(documents)} pages. "
                    f"The crawler used sitemap-assisted discovery and heuristic link scoring."
                ),
                citation_ids=[doc.id for doc in documents[:3]],
            ),
        )
        run.documents = documents
        run.citations = citations
        run.summary_sections = sections

        current_dir = self._paths.research_dir / "sites" / _slug_from_url(url)
        self._finalize_run(
            run,
            title=f"Site Capture: {url}",
            report_name="SUMMARY.md",
            current_dir=current_dir,
        )

    async def _build_research_pack_impl(
        self,
        run: WorkflowResult,
        *,
        topic: str,
        official_url: str | None,
        max_research_pages: int,
    ):
        self._report(0, 4, "Discovering official documentation URL...")
        official = official_url or await self._discover_official_docs_url(
            topic, run=run
        )
        if not official:
            raise ValueError("Could not determine an official documentation URL")

        self._report(1, 4, f"Capturing official docs from {official}...")
        official_docs, official_citations = await self._capture_site_documents(
            official,
            run=run,
            section="official-docs",
            role="official_doc",
            source_type="official_docs",
            soft_page_limit=50,
            hard_page_limit=120,
        )
        self._report(
            2,
            4,
            f"Captured {len(official_docs)} official docs, searching for external research...",
        )
        research_retrieval, research_urls = await self._discover_research_urls(
            topic,
            official_url=official,
            limit=max_research_pages,
            run=run,
        )
        research_docs, research_citations = await self._capture_explicit_urls(
            run,
            research_urls,
            section="external-research",
            role="external_research",
            source_type="external_research",
            retrieval=research_retrieval,
            citation_start=len(official_citations),
            soft_page_limit=max_research_pages,
            hard_page_limit=max_research_pages,
        )
        self._report(
            3, 4, f"Captured {len(research_docs)} external pages, generating summary..."
        )
        documents = official_docs + research_docs
        citations = official_citations + research_citations
        if not documents:
            raise ValueError("Research pack did not produce any saved documents")

        run.metadata["official_url"] = official
        run.metadata["official_docs_count"] = len(official_docs)
        run.metadata["external_research_count"] = len(research_docs)

        sections = await get_summarizer().summarize(
            title=topic,
            prompt=f"Build a research pack for {topic}",
            documents=documents,
            citations=citations,
        )
        sections.insert(
            0,
            SummarySection(
                heading="Pack Composition",
                body=(
                    f"Official docs were captured from {official}. "
                    f"Argus also saved {len(research_docs)} external supporting sources."
                ),
                citation_ids=[doc.id for doc in documents[:4]],
            ),
        )
        run.documents = documents
        run.citations = citations
        run.summary_sections = sections

        slug = _slug_from_url(official)
        cache_dir = self._paths.docs_cache_dir / slug
        pack_dir = self._paths.research_dir / "packs" / _slug_from_url(topic)
        self._finalize_run(
            run,
            title=f"Research Pack: {topic}",
            report_name="SUMMARY.md",
            current_dir=pack_dir,
            docs_cache_dir=cache_dir,
            docs_cache_url=official,
        )

    async def _discover_official_docs_url(
        self, topic: str, *, run: WorkflowResult
    ) -> str | None:
        operation = await self._operation_search(
            run, query=f"{topic} official docs", mode="discovery", max_results=8
        )
        result = operation.result or {}
        results = result.get("results", ())
        for item in results:
            url = str(item["url"])
            if any(
                keyword in url.lower()
                for keyword in ("/docs", "docs.", "/reference", "/api")
            ):
                return url
        return str(results[0]["url"]) if results else None

    async def _discover_research_urls(
        self,
        topic: str,
        *,
        official_url: str,
        limit: int,
        run: WorkflowResult,
    ) -> tuple[Any, list[str]]:
        official_root = _domain_root(urlparse(official_url).netloc)
        operation = await self._operation_search(
            run,
            query=f"{topic} documentation tutorial guide comparison best practices",
            mode="research",
            max_results=max(limit * 2, 20),
        )
        results = (operation.result or {}).get("results", ())
        urls: list[str] = []
        seen: set[str] = set()
        for result in results:
            result_url = str(result["url"])
            normalized = _normal_url(result_url)
            if normalized in seen:
                continue
            seen.add(normalized)
            if _domain_root(urlparse(result_url).netloc) == official_root:
                continue
            urls.append(result_url)
            if len(urls) >= limit:
                break
        return operation, urls

    async def _capture_site_documents(
        self,
        url: str,
        *,
        run: WorkflowResult,
        section: str,
        role: str,
        source_type: str,
        soft_page_limit: int,
        hard_page_limit: int,
    ) -> tuple[list[StoredDocument], list[CitationRef]]:
        retrieval, candidates = await self._discover_site_urls(
            url,
            run=run,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )
        return await self._capture_explicit_urls(
            run,
            candidates,
            section=section,
            role=role,
            source_type=source_type,
            retrieval=retrieval,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )

    async def _capture_explicit_urls(
        self,
        run: WorkflowResult,
        urls: list[str],
        *,
        section: str,
        role: str,
        source_type: str,
        retrieval=None,
        citation_start: int = 0,
        soft_page_limit: int | None = None,
        hard_page_limit: int | None = None,
    ) -> tuple[list[StoredDocument], list[CitationRef]]:
        documents: list[StoredDocument] = []
        citations: list[CitationRef] = []
        output_dir = Path(run.snapshot_dir) / section
        output_dir.mkdir(parents=True, exist_ok=True)

        bounded_urls = urls[: hard_page_limit or len(urls)]
        if soft_page_limit is not None:
            bounded_urls = bounded_urls[:soft_page_limit]
        if retrieval is not None and bounded_urls:
            return await self._compose_search_documents(
                run,
                retrieval,
                max_results=len(bounded_urls),
                section=section,
                role=role,
                source_type=source_type,
                selection_urls=tuple(bounded_urls),
                citation_start=citation_start,
            )

        for i, candidate_url in enumerate(bounded_urls):
            if hard_page_limit is not None and len(documents) >= hard_page_limit:
                break
            if i > 0 and i % 5 == 0:
                self._report(
                    i,
                    len(urls),
                    f"Extracting page {i}/{len(urls)}: {candidate_url[:60]}",
                )
            operation = await self._operation_search(
                run, query=candidate_url, mode="discovery", max_results=1
            )
            captured, captured_citations = await self._compose_search_documents(
                run,
                operation,
                max_results=1,
                section=section,
                role=role,
                source_type=source_type,
                selection_urls=(candidate_url,),
                citation_start=citation_start + len(documents),
            )
            documents.extend(captured)
            citations.extend(captured_citations)

            if soft_page_limit is not None and len(documents) >= soft_page_limit:
                break

        documents.sort(key=lambda item: item.word_count, reverse=True)
        return documents, citations

    async def _discover_site_urls(
        self,
        root_url: str,
        *,
        run: WorkflowResult,
        soft_page_limit: int,
        hard_page_limit: int,
    ) -> tuple[Any, list[str]]:
        operation = await self._operation_acquire_site(
            run,
            url=root_url,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )
        results = (operation.result or {}).get("results", ())
        urls = [str(result["url"]) for result in results]
        if not urls:
            raise RuntimeError("workflow_site_acquisition_unready")
        return operation, urls[: min(soft_page_limit, hard_page_limit)]

    def _store_document(
        self,
        destination: Path,
        *,
        citation_id: str,
        url: str,
        title: str,
        text: str,
        word_count: int,
        domain: str,
        role: str,
        source_type: str,
        extractor: str | None,
        egress: str | None = None,
        machine: str | None = None,
        metadata: dict[str, Any],
    ) -> StoredDocument:
        from argus.corpus.paths import slugify

        filename = (
            f"{citation_id.lower()}-{slugify(title or url, default='document')}.md"
        )
        artifact_path = destination / filename
        artifact_path.write_text(
            "\n".join(
                [
                    f"# {title or url}",
                    "",
                    f"- URL: {url}",
                    f"- Domain: {domain}",
                    f"- Source type: {source_type}",
                    f"- Role: {role}",
                    f"- Extractor: {extractor or 'unknown'}",
                    f"- Egress: {egress or 'unknown'}",
                    f"- Machine: {machine or 'unknown'}",
                    f"- Word count: {word_count}",
                    "",
                    text,
                    "",
                ]
            ),
            encoding="utf-8",
        )
        metadata_path = artifact_path.with_suffix(".json")
        metadata_path.write_text(
            json.dumps(metadata, indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )
        return StoredDocument(
            id=citation_id,
            url=url,
            title=title,
            artifact_path=str(artifact_path),
            word_count=word_count,
            domain=domain,
            role=role,
            source_type=source_type,
            extractor=extractor,
            egress=egress,
            machine=machine,
            metadata=metadata,
        )

    def _finalize_run(
        self,
        run: WorkflowResult,
        *,
        title: str,
        report_name: str,
        current_dir: Path | None = None,
        docs_cache_dir: Path | None = None,
        docs_cache_url: str | None = None,
    ) -> None:
        # A report and manifest are terminal artifacts. Mark the run complete
        # before rendering either one so their serialized status/timestamp
        # cannot lag the durable run state.
        run.status = WorkflowStatus.COMPLETED
        run.finished_at = datetime.now(tz=None)
        report_path = Path(run.snapshot_dir) / report_name
        manifest_path = Path(run.snapshot_dir) / "manifest.json"
        report_content = self._render_report(title, run)
        run.report_path = str(report_path)
        run.manifest_path = str(manifest_path)
        run.artifacts = [
            WorkflowArtifact(
                kind="report",
                path=str(report_path),
                description="Human-readable workflow report",
            ),
            WorkflowArtifact(
                kind="manifest",
                path=str(manifest_path),
                description="Structured workflow manifest",
            ),
        ]
        manifest_content = (
            json.dumps(self._serialize_run(run), indent=2, default=_json_default) + "\n"
        )
        self._atomic_write_text(report_path, report_content)
        self._atomic_write_text(manifest_path, manifest_content)

        if current_dir is not None:
            self._replace_directory(current_dir, Path(run.snapshot_dir))
            run.metadata["current_dir"] = str(current_dir)

        if docs_cache_dir is not None:
            self._write_docs_cache_dir(docs_cache_dir, title=title, run=run)
            if docs_cache_url:
                self._update_docs_cache_index(
                    docs_cache_dir.name, docs_cache_url, docs_cache_dir
                )

    @staticmethod
    def _atomic_write_text(path: Path, content: str) -> None:
        """Publish one text artifact with replace semantics."""
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            temporary.write_text(content, encoding="utf-8")
            os.replace(temporary, path)
        except OSError:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    def _rewrite_failed_artifacts(self, run: WorkflowResult) -> None:
        """Ensure a late finalization failure cannot leave completed artifacts."""
        if run.status is not WorkflowStatus.FAILED:
            return
        report_path = Path(run.report_path) if run.report_path else None
        if report_path is not None and report_path.is_file():
            title = run.kind.value.replace("-", " ").title()
            try:
                first_line = report_path.read_text(encoding="utf-8").splitlines()[0]
                title = first_line.removeprefix("# ").strip() or title
            except (OSError, UnicodeError, IndexError):
                pass
            try:
                self._atomic_write_text(report_path, self._render_report(title, run))
            except OSError:
                logger.warning("Failed to rewrite failed workflow report")
        manifest_path = Path(run.manifest_path) if run.manifest_path else None
        if manifest_path is not None and manifest_path.is_file():
            try:
                self._atomic_write_text(
                    manifest_path,
                    json.dumps(
                        self._serialize_run(run), indent=2, default=_json_default
                    )
                    + "\n",
                )
            except OSError:
                logger.warning("Failed to rewrite failed workflow manifest")

    def _write_docs_cache_dir(
        self, docs_cache_dir: Path, *, title: str, run: WorkflowResult
    ) -> None:
        docs_cache_dir.mkdir(parents=True, exist_ok=True)
        readme = docs_cache_dir / "README.md"
        official_docs = [
            document
            for document in run.documents
            if document.source_type == "official_docs"
        ]
        lines = [
            f"# {title}",
            "",
            f"> Generated by Argus workflow `{run.kind.value}`",
            "",
            "## Latest Summary",
            "",
        ]
        for section in run.summary_sections:
            lines.append(f"### {section.heading}")
            lines.append("")
            lines.append(section.body)
            lines.append("")
        if run.report_path:
            lines.append(f"Source report: {run.report_path}")
            lines.append("")
        readme.write_text("\n".join(lines), encoding="utf-8")
        sources_dir = docs_cache_dir / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        for document in official_docs:
            source_path = Path(document.artifact_path)
            if source_path.exists():
                shutil.copy2(source_path, sources_dir / source_path.name)

    def _update_docs_cache_index(self, slug: str, source_url: str, path: Path) -> None:
        index_path = self._paths.docs_cache_index
        existing = index_path.read_text(encoding="utf-8").splitlines()
        row = f"| {slug} | {source_url} | {datetime.now(tz=None).date().isoformat()} | {path} |"
        filtered = [line for line in existing if not line.startswith(f"| {slug} |")]
        filtered.append(row)
        index_path.write_text("\n".join(filtered) + "\n", encoding="utf-8")

    def _replace_directory(self, destination: Path, source: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)

    def _render_report(self, title: str, run: WorkflowResult) -> str:
        lines = [
            f"# {title}",
            "",
            f"- Run ID: {run.run_id}",
            f"- Workflow: {run.kind.value}",
            f"- Target: {run.target}",
            f"- Status: {run.status.value}",
            f"- Finished at: {run.finished_at.isoformat() if run.finished_at else 'unknown'}",
            f"- Snapshot: {run.snapshot_dir}",
            "",
            "## Summary",
            "",
        ]
        for section in run.summary_sections:
            citations = " ".join(f"[{cid}]" for cid in section.citation_ids)
            lines.append(f"### {section.heading}")
            lines.append("")
            lines.append(section.body)
            if citations:
                lines.append("")
                lines.append(f"Citations: {citations}")
            lines.append("")

        lines.extend(
            [
                "## References",
                "",
            ]
        )
        citation_map = {citation.id: citation for citation in run.citations}
        for citation_id in sorted(citation_map):
            citation = citation_map[citation_id]
            lines.append(
                f"- [{citation.id}] {citation.title} — {citation.url}\n"
                f"  Artifact: {citation.artifact_path}"
                + (
                    " (PARTIAL CONTENT)"
                    if citation.artifact_disposition == "partial"
                    else ""
                )
            )
        lines.append("")
        return "\n".join(lines)

    def _serialize_run(self, run: WorkflowResult) -> dict[str, Any]:
        return {
            "run_id": run.run_id,
            "kind": run.kind.value,
            "status": run.status.value,
            "target": run.target,
            "created_at": run.created_at,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "status_url": run.status_url,
            "snapshot_dir": run.snapshot_dir,
            "report_path": run.report_path,
            "manifest_path": run.manifest_path,
            "artifacts": [asdict(artifact) for artifact in run.artifacts],
            "documents": [asdict(document) for document in run.documents],
            "citations": [asdict(citation) for citation in run.citations],
            "summary_sections": [asdict(section) for section in run.summary_sections],
            "metadata": run.metadata,
            "error": run.error,
        }

    def _deserialize_run(self, payload: dict[str, Any]) -> WorkflowResult:
        return WorkflowResult(
            run_id=payload["run_id"],
            kind=WorkflowKind(payload["kind"]),
            status=WorkflowStatus(payload["status"]),
            target=payload["target"],
            created_at=_parse_dt(payload.get("created_at")) or datetime.now(tz=None),
            started_at=_parse_dt(payload.get("started_at")),
            finished_at=_parse_dt(payload.get("finished_at")),
            status_url=payload.get("status_url"),
            snapshot_dir=payload.get("snapshot_dir", ""),
            report_path=payload.get("report_path"),
            manifest_path=payload.get("manifest_path"),
            artifacts=[
                WorkflowArtifact(**artifact)
                for artifact in payload.get("artifacts", [])
            ],
            documents=[
                StoredDocument(**document) for document in payload.get("documents", [])
            ],
            citations=[
                CitationRef(**citation) for citation in payload.get("citations", [])
            ],
            summary_sections=[
                SummarySection(**section)
                for section in payload.get("summary_sections", [])
            ],
            metadata=payload.get("metadata", {}),
            error=payload.get("error"),
        )

    def _write_run_state(self, run: WorkflowResult) -> None:
        payload = self._serialize_run(run)
        state_path = self._paths.workflow_runs_dir / f"{run.run_id}.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(payload, indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )
