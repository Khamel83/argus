"""Workflow execution for retrieval-oriented Argus features."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import os
import re
import shutil
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from tld import get_fld

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
from argus.workflows.targeted_research import (
    TargetCandidateFailure,
    TargetResearchPlan,
    TargetWorkflowFailure,
    TARGET_CANDIDATE_COMPOSITION_TIMEOUT_SECONDS,
    TARGET_EXTERNAL_REMAINDER_TIMEOUT_SECONDS,
    TARGET_GLOBAL_CONCURRENCY,
    TARGET_REQUIREMENT_SEARCH_TIMEOUT_SECONDS,
    TARGET_WORKFLOW_DEADLINE_SECONDS,
    flatten_requirements,
    make_target_search_requests,
    page_budget_math,
    plan_target_research,
)

logger = get_logger("workflows")

_ARTIFACT_DEFAULT_BYTES = 64 * 1024
_ARTIFACT_MAX_BYTES = 256 * 1024
_ARTIFACT_MEDIA_TYPES = {
    "report": "text/markdown; charset=utf-8",
    "manifest": "application/json; charset=utf-8",
}
_WORKFLOW_FAILURE_MARKER_SCHEMA = "argus.workflow-failure.v1"
_WORKFLOW_FAILURE_MARKER_SUFFIX = ".failure.json"
_SAFE_WORKFLOW_DEADLINE_SECONDS = TARGET_WORKFLOW_DEADLINE_SECONDS
_TARGET_SEARCH_TIMEOUT_SECONDS = TARGET_REQUIREMENT_SEARCH_TIMEOUT_SECONDS
_TARGET_CANDIDATE_TIMEOUT_SECONDS = TARGET_CANDIDATE_COMPOSITION_TIMEOUT_SECONDS
_TARGET_EXTERNAL_TIMEOUT_SECONDS = TARGET_EXTERNAL_REMAINDER_TIMEOUT_SECONDS
_TARGET_GLOBAL_CONCURRENCY = TARGET_GLOBAL_CONCURRENCY
# The extraction authority allows ten requests per root domain in a sixty-second
# window.  Keep official same-domain capture conservatively below that bound
# before composing artifacts; external research retains its caller-provided page
# limit.
_OFFICIAL_CAPTURE_PAGE_LIMIT = 8
_COST_STATES = {"confirmed", "estimated", "uncertain", "unavailable"}
_TARGET_EXECUTION_EVIDENCE_MISSING = "workflow_target_execution_evidence_missing"
_TARGET_EXECUTION_DIAGNOSTIC_FIELDS = frozenset(
    {
        "provider",
        "extractor",
        "status",
        "result_count",
        "timeout_source",
        "operation_latency_ms",
        "cache_latency_ms",
        "cache_state",
        "cache_age_ms",
        "cache_origin",
        "spend_provenance",
        "freshness_age_ms",
        "freshness_window",
        "freshness_reason",
        "free_profile_eligible",
        "egress",
        "machine",
        "source_type",
    }
)
_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
_REVISION_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$")
_IMAGE_DIGEST_RE = re.compile(
    r"^(?:ghcr\.io/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*@)?"
    r"sha256:[0-9a-fA-F]{64}$"
)


def _utf8_codepoint_width(first_byte: int) -> int:
    if first_byte < 0x80:
        return 1
    if 0xC2 <= first_byte <= 0xDF:
        return 2
    if 0xE0 <= first_byte <= 0xEF:
        return 3
    if 0xF0 <= first_byte <= 0xF4:
        return 4
    raise UnicodeDecodeError("utf-8", bytes([first_byte]), 0, 1, "invalid start byte")


class WorkflowArtifactError(RuntimeError):
    """Base class for safe workflow-artifact read failures."""


class WorkflowArtifactNotFound(WorkflowArtifactError):
    """The run or allowlisted artifact does not exist."""


class WorkflowArtifactNotReady(WorkflowArtifactError):
    """The workflow has not reached a terminal state."""


class WorkflowArtifactUnavailable(WorkflowArtifactError):
    """A registered artifact failed containment, hashing, or decoding."""


class WorkflowArtifactRangeError(WorkflowArtifactError):
    """The requested byte limit cannot contain the next UTF-8 code point."""


class WorkflowStartPersistenceError(RuntimeError):
    """A safe workflow start could not be durably recorded."""


class _RealWorkflowClock:
    """Small injectable clock seam shared by workflow persistence and deadlines."""

    @staticmethod
    def now() -> datetime:
        return datetime.now(timezone.utc)

    @staticmethod
    def monotonic() -> float:
        return time.monotonic()


def _json_default(value: Any):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Mapping):
        return dict(value)
    if hasattr(value, "value"):
        return value.value
    raise TypeError(f"Unsupported JSON value: {type(value)!r}")


def _plain_json_value(value: Any) -> Any:
    """Copy mapping/list containers into response-safe native containers."""
    if isinstance(value, Mapping):
        return {key: _plain_json_value(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_plain_json_value(nested) for nested in value]
    if isinstance(value, tuple):
        return tuple(_plain_json_value(nested) for nested in value)
    return value


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
    raw = str(hostname).strip().lower().rstrip(".")
    if not raw:
        return ""
    try:
        parsed = urlparse(raw if "://" in raw else f"//{raw}")
        host = parsed.hostname or raw
    except ValueError:
        host = raw
    host = host.strip().lower().rstrip(".")
    if not host:
        return ""
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        registrable = get_fld(host, fix_protocol=True)
    except Exception:
        registrable = None
    if isinstance(registrable, str) and registrable:
        return registrable.lower().rstrip(".")
    parts = [part for part in host.split(".") if part]
    if len(parts) <= 2:
        return host
    return ".".join(parts[-2:])


def _safe_runtime_identity(value: Any, *, kind: str) -> str:
    """Return only bounded runtime identifiers suitable for public status."""
    if not isinstance(value, str):
        return "unknown"
    candidate = value.strip()
    if len(candidate) > 128:
        return "unknown"
    if kind == "version":
        return candidate if _VERSION_RE.fullmatch(candidate) else "unknown"
    if kind == "source_revision":
        return candidate if _REVISION_RE.fullmatch(candidate) else "unknown"
    if kind == "image":
        return candidate if _IMAGE_DIGEST_RE.fullmatch(candidate) else "unknown"
    if kind == "deployment":
        return candidate if _SAFE_ID_RE.fullmatch(candidate) else "unknown"
    return "unknown"


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

    # Public names make the bounded execution contract easy to inspect and to
    # shorten in hermetic tests without patching asyncio's global clock.
    TARGET_SEARCH_TIMEOUT_SECONDS = _TARGET_SEARCH_TIMEOUT_SECONDS
    TARGET_CANDIDATE_TIMEOUT_SECONDS = _TARGET_CANDIDATE_TIMEOUT_SECONDS
    TARGET_EXTERNAL_TIMEOUT_SECONDS = _TARGET_EXTERNAL_TIMEOUT_SECONDS
    TARGET_WORKFLOW_TIMEOUT_SECONDS = _SAFE_WORKFLOW_DEADLINE_SECONDS
    TARGET_GLOBAL_CONCURRENCY = _TARGET_GLOBAL_CONCURRENCY

    # Kept as class attributes so callers can map typed failures without
    # importing the workflow implementation module.
    ArtifactNotFound = WorkflowArtifactNotFound
    ArtifactNotReady = WorkflowArtifactNotReady
    ArtifactUnavailable = WorkflowArtifactUnavailable
    ArtifactRange = WorkflowArtifactRangeError
    StartPersistenceError = WorkflowStartPersistenceError

    def __init__(
        self,
        accepted_operations,
        *,
        corpus_paths: CorpusPaths | None = None,
        progress_callback: Callable[[int, int, str], None] | None = None,
        caller: str = "workflows",
        clock: Any | None = None,
        clock_provider: Any | None = None,
    ):
        self._accepted_operations = accepted_operations
        self._paths = corpus_paths or get_corpus_paths()
        self._runs: dict[str, WorkflowResult] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._progress = progress_callback
        self._caller = caller or "workflows"
        self._clock = clock_provider or clock or _RealWorkflowClock()
        # The global semaphore is shared by all runs owned by this service.  A
        # target lock is scoped to a run, so independent targets may overlap
        # while a single target can never have two accepted calls in flight.
        self._target_global_semaphore = asyncio.Semaphore(self.TARGET_GLOBAL_CONCURRENCY)
        self._target_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._operation_tasks: dict[str, set[asyncio.Task]] = {}

    def _report(self, current: int, total: int, message: str) -> None:
        if self._progress:
            try:
                self._progress(current, total, message)
            except Exception:
                pass

    def _clock_now(self) -> datetime:
        """Return an aware UTC timestamp from the injected clock."""
        provider = self._clock
        value = getattr(provider, "now", None)
        if callable(value):
            value = value()
        elif getattr(provider, "utc_now", None) is not None:
            value = getattr(provider, "utc_now")
            if callable(value):
                value = value()
        else:
            value = datetime.now(timezone.utc)
        if not isinstance(value, datetime):
            value = datetime.now(timezone.utc)
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _clock_monotonic(self) -> float:
        provider = self._clock
        value = getattr(provider, "monotonic", None)
        try:
            value = value() if callable(value) else float(value)
            return float(value)
        except (TypeError, ValueError):
            return time.monotonic()

    @staticmethod
    def _is_targeted_run(run: WorkflowResult) -> bool:
        targets = run.metadata.get("research_targets")
        if isinstance(targets, (list, tuple)) and bool(targets):
            return True
        plan = run.metadata.get("research_plan")
        return isinstance(plan, Mapping) and bool(plan.get("targets"))

    def _ensure_target_deadline(self, run: WorkflowResult) -> None:
        """Create/preserve the persisted aware-UTC deadline for targeted runs."""
        if not self._is_targeted_run(run):
            return
        raw = run.metadata.get("deadline_at")
        deadline = _parse_dt(raw)
        if deadline is None or deadline.tzinfo is None or deadline.utcoffset() is None:
            deadline = self._clock_now() + timedelta(
                seconds=float(self.TARGET_WORKFLOW_TIMEOUT_SECONDS)
            )
            run.metadata["deadline_at"] = deadline.astimezone(timezone.utc).isoformat()
            run.metadata["deadline"] = run.metadata["deadline_at"]
        else:
            deadline = deadline.astimezone(timezone.utc)
            run.metadata["deadline_at"] = deadline.isoformat()
            run.metadata.setdefault("deadline", run.metadata["deadline_at"])
        anchor = run.metadata.get("_deadline_monotonic")
        if not isinstance(anchor, (int, float)):
            remaining = max(0.0, (deadline - self._clock_now()).total_seconds())
            run.metadata["_deadline_monotonic"] = self._clock_monotonic() + remaining

    def _remaining_workflow_seconds(self, run: WorkflowResult) -> float:
        self._ensure_target_deadline(run)
        anchor = run.metadata.get("_deadline_monotonic")
        if isinstance(anchor, (int, float)):
            monotonic_remaining = float(anchor) - self._clock_monotonic()
        else:
            deadline = _parse_dt(run.metadata.get("deadline_at"))
            monotonic_remaining = (
                (deadline.astimezone(timezone.utc) - self._clock_now()).total_seconds()
                if deadline is not None and deadline.tzinfo is not None
                else float(self.TARGET_WORKFLOW_TIMEOUT_SECONDS)
            )
        # Wall-clock comparison is the restart-safe source of truth.  During a
        # live run, taking the minimum also makes a clock that jumps forward
        # fail closed instead of granting extra execution time.
        deadline = _parse_dt(run.metadata.get("deadline_at"))
        if deadline is not None and deadline.tzinfo is not None:
            wall_remaining = (
                deadline.astimezone(timezone.utc) - self._clock_now()
            ).total_seconds()
            return min(monotonic_remaining, wall_remaining)
        return monotonic_remaining

    def _check_target_budget(self, run: WorkflowResult) -> float:
        remaining = self._remaining_workflow_seconds(run)
        if remaining <= 0:
            run.metadata.setdefault("timeout_source", "workflow_deadline")
            raise TargetWorkflowFailure("workflow_deadline_exceeded")
        return remaining

    @staticmethod
    def _record_target_timeout(run: WorkflowResult, source: str) -> None:
        run.metadata.setdefault("target_timeout_events", []).append(
            {"source": source, "at": run.metadata.get("deadline_at")}
        )

    def _operation_task_set(self, run_id: str) -> set[asyncio.Task]:
        return self._operation_tasks.setdefault(run_id, set())

    async def _cancel_outstanding_operations(self, run_id: str) -> None:
        tasks = list(self._operation_tasks.get(run_id, ()))
        if not tasks:
            self._operation_tasks.pop(run_id, None)
            return
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._operation_tasks.pop(run_id, None)

    async def _invoke_target_operation(
        self,
        run: WorkflowResult,
        *,
        target_key: str,
        operation_factory,
        timeout_seconds: float,
        timeout_code: str,
        deadline_marker: str | None = None,
    ):
        """Run one accepted call under target/global locks and remaining budget."""
        remaining = self._check_target_budget(run)
        if deadline_marker:
            marker = run.metadata.get(deadline_marker)
            if not isinstance(marker, (int, float)):
                marker = self._clock_monotonic() + min(
                    remaining, float(timeout_seconds)
                )
                run.metadata[deadline_marker] = marker
            remaining = min(remaining, float(marker) - self._clock_monotonic())
            if remaining <= 0:
                raise TargetWorkflowFailure("workflow_deadline_exceeded")
        timeout = min(float(timeout_seconds), remaining)
        lock = self._target_locks.setdefault((run.run_id, target_key), asyncio.Lock())
        try:
            await asyncio.wait_for(lock.acquire(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            if self._remaining_workflow_seconds(run) <= 0:
                raise TargetWorkflowFailure("workflow_deadline_exceeded") from exc
            self._record_target_timeout(run, timeout_code)
            raise TargetWorkflowFailure(timeout_code) from exc
        try:
            remaining = self._check_target_budget(run)
            timeout = min(float(timeout_seconds), remaining)
            if deadline_marker:
                marker = run.metadata.get(deadline_marker)
                if isinstance(marker, (int, float)):
                    timeout = min(timeout, float(marker) - self._clock_monotonic())
            if timeout <= 0:
                raise TargetWorkflowFailure("workflow_deadline_exceeded")
            async with self._target_global_semaphore:
                remaining = self._check_target_budget(run)
                timeout = min(timeout, remaining)
                if timeout <= 0:
                    raise TargetWorkflowFailure("workflow_deadline_exceeded")
                operation_started = self._clock_monotonic()
                operation_started_at = self._clock_now()
                task = asyncio.create_task(operation_factory())
                self._operation_task_set(run.run_id).add(task)
                try:
                    result = await asyncio.wait_for(task, timeout=timeout)
                except asyncio.TimeoutError as exc:
                    # wait_for waits for cancellation of the wrapped task, but
                    # gather again to make the cancellation proof explicit.
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    if self._remaining_workflow_seconds(run) <= 0:
                        raise TargetWorkflowFailure(
                            "workflow_deadline_exceeded"
                        ) from exc
                    self._record_target_timeout(run, timeout_code)
                    raise TargetWorkflowFailure(timeout_code) from exc
                except asyncio.CancelledError:
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                    raise
                finally:
                    self._operation_task_set(run.run_id).discard(task)
                operation_elapsed = max(
                    self._clock_monotonic() - operation_started,
                    (self._clock_now() - operation_started_at).total_seconds(),
                )
                if operation_elapsed >= float(timeout_seconds):
                    if self._remaining_workflow_seconds(run) <= 0:
                        raise TargetWorkflowFailure("workflow_deadline_exceeded")
                    self._record_target_timeout(run, timeout_code)
                    raise TargetWorkflowFailure(timeout_code)
                if self._remaining_workflow_seconds(run) <= 0:
                    raise TargetWorkflowFailure("workflow_deadline_exceeded")
                if deadline_marker:
                    marker = run.metadata.get(deadline_marker)
                    if isinstance(marker, (int, float)) and (
                        marker - self._clock_monotonic() <= 0
                    ):
                        raise TargetWorkflowFailure("workflow_deadline_exceeded")
                return result
        finally:
            lock.release()

    async def _operation_search(
        self,
        run: WorkflowResult,
        *,
        query: str,
        mode: str,
        max_results: int,
        request_id: str | None = None,
    ):
        request = type(
            "WorkflowSearchRequest",
            (),
            {
                "query": query,
                "mode": mode,
                "max_results": max_results,
                "providers": None,
                "free_only": bool(run.metadata.get("free_only", False)),
                "caller": str(run.metadata.get("caller_label") or self._caller),
                "session_id": None,
                "include_attribution": False,
            },
        )()
        return await self._accepted_operations.search(
            request,
            principal=self._caller_for_run(run),
            request_id=request_id or uuid.uuid4().hex,
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
                "free_only": bool(run.metadata.get("free_only", False)),
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
        if not _SAFE_ID_RE.fullmatch(str(run_id)):
            return None
        run = self._runs.get(run_id)
        if run is not None:
            return run

        state_path = self._paths.workflow_runs_dir / f"{run_id}.json"
        if not state_path.exists():
            return None

        try:
            payload = json.loads(state_path.read_text(encoding="utf-8"))
            run = self._deserialize_run(payload)
            failure_marker = self._read_failure_marker(run_id)
            if failure_marker is not None:
                self._apply_failure_marker(run, failure_marker)
            self._interrupt_orphaned_run(run)
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
            "status_url": f"/api/workflows/{run.run_id}/status",
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
            "runtime": self._status_runtime_projection(run, runtime),
            "runtime_observation": self._runtime_observation(run, runtime),
            "runtime_mismatch": self._runtime_observation(run, runtime)["mismatch"],
            "request_sha256": self._safe_request_hash(run),
            "deadline_at": self._safe_deadline(run),
            "research_plan": self._safe_research_plan(run),
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
                    # Do not exceed the caller's byte bound.  If the first
                    # code point cannot fit, make the caller request a larger
                    # page rather than returning an oversized page or
                    # advancing past data.
                    handle.seek(read_offset)
                    expanded = handle.read(4)
                    first_width = _utf8_codepoint_width(expanded[0])
                    if len(expanded) < first_width:
                        raise UnicodeDecodeError(
                            "utf-8",
                            expanded,
                            0,
                            len(expanded),
                            "unexpected end of data",
                        )
                    expanded[:first_width].decode("utf-8")
                    if first_width > max_bytes:
                        raise WorkflowArtifactRangeError(
                            "max_bytes is smaller than the next UTF-8 code point"
                        )
                    expanded = expanded[:first_width]
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
                raise WorkflowArtifactNotFound("workflow artifact is not available")
        except WorkflowArtifactUnavailable:
            raise
        except WorkflowArtifactNotFound:
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
        runtime: Mapping[str, Any] | None,
        metadata_runtime: Any,
    ) -> dict[str, Any]:
        source = runtime if isinstance(runtime, Mapping) else {}
        if not source and isinstance(metadata_runtime, Mapping):
            source = metadata_runtime
        build = (
            source.get("build") if isinstance(source.get("build"), Mapping) else source
        )
        deployment = (
            source.get("deployment")
            if isinstance(source.get("deployment"), Mapping)
            else {}
        )
        image = source.get("image") if isinstance(source.get("image"), Mapping) else {}
        return {
            "version": _safe_runtime_identity(build.get("version"), kind="version"),
            "source_revision": _safe_runtime_identity(
                build.get("source_revision"), kind="source_revision"
            ),
            "image_identity": _safe_runtime_identity(
                source.get("image_identity")
                or source.get("image_digest")
                or build.get("image_identity")
                or build.get("image_digest")
                or image.get("identity")
                or image.get("digest")
                or deployment.get("image_identity"),
                kind="image",
            ),
            "deployment_identity": _safe_runtime_identity(
                source.get("deployment_identity")
                or source.get("deployment_id")
                or deployment.get("deployment_id")
                or deployment.get("identity")
                or deployment.get("id"),
                kind="deployment",
            ),
        }

    @classmethod
    def _status_runtime_projection(
        cls,
        run: WorkflowResult,
        runtime: Mapping[str, Any] | None,
    ) -> dict[str, str]:
        """Prefer immutable run-start identity over the live deployment."""

        persisted = run.metadata.get("runtime")
        if isinstance(persisted, Mapping):
            return cls._runtime_projection(None, persisted)
        return cls._runtime_projection(runtime, None)

    @classmethod
    def _runtime_observation(
        cls,
        run: WorkflowResult,
        runtime: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        """Expose live-versus-persisted identity as observation evidence only."""

        persisted = cls._runtime_projection(None, run.metadata.get("runtime"))
        live = cls._runtime_projection(runtime, None)
        has_live = isinstance(runtime, Mapping) and bool(runtime)
        has_persisted = isinstance(run.metadata.get("runtime"), Mapping)
        mismatch = bool(has_live and has_persisted and live != persisted)
        return {
            "persisted": persisted,
            "live": live,
            "persisted_runtime": persisted,
            "live_runtime": live,
            "mismatch": mismatch,
        }

    @staticmethod
    def _safe_request_hash(run: WorkflowResult) -> str | None:
        value = run.metadata.get("request_sha256")
        return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None

    @staticmethod
    def _safe_deadline(run: WorkflowResult) -> str | None:
        value = run.metadata.get("deadline_at")
        if not isinstance(value, str):
            return None
        parsed = _parse_dt(value)
        if parsed is None or parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc).isoformat()

    @staticmethod
    def _safe_research_plan(run: WorkflowResult) -> dict[str, Any]:
        value = run.metadata.get("research_plan")
        if not isinstance(value, Mapping):
            return {}
        return _plain_json_value(value)

    def _is_recovery_sensitive_run(self, run: WorkflowResult) -> bool:
        metadata = run.metadata
        if metadata.get("safe_start") is True:
            return True
        targets = metadata.get("research_targets")
        if isinstance(targets, (list, tuple)) and bool(targets):
            return True
        plan = metadata.get("research_plan")
        if isinstance(plan, Mapping):
            targets = plan.get("targets")
            if isinstance(targets, (list, tuple)) and bool(targets):
                return True
        request = metadata.get("request")
        if isinstance(request, Mapping):
            targets = request.get("research_targets")
            if isinstance(targets, (list, tuple)) and bool(targets):
                return True
        return False

    def _interrupt_orphaned_run(self, run: WorkflowResult) -> None:
        """Terminalize a reloaded safe run that has no live in-process task."""

        if run.status not in {WorkflowStatus.PENDING, WorkflowStatus.RUNNING}:
            return
        if not self._is_recovery_sensitive_run(run):
            return
        live_task = self._tasks.get(run.run_id)
        if live_task is not None and not live_task.done():
            return

        run.status = WorkflowStatus.FAILED
        run.error = "workflow_interrupted"
        run.finished_at = self._clock_now()
        # An interrupted run has no accepted closure.  Never expose or create
        # report/manifest artifacts while recovering the durable state.
        run.report_path = None
        run.manifest_path = None
        run.artifacts = []
        run.metadata["failure"] = {
            "code": "workflow_interrupted",
            "reason": "process_reload_without_live_task",
        }
        try:
            self._write_run_state(run)
        except Exception:
            logger.exception("Failed to persist interrupted workflow %s", run.run_id)

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
        runtime: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.RECOVER_ARTICLE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
        )
        self._schedule_run(
            run,
            self._recover_article_impl,
            url=url,
            title=title,
            domain=domain,
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
        runtime: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.CAPTURE_SITE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
        )
        self._schedule_run(
            run,
            self._capture_site_impl,
            url=url,
            soft_page_limit=soft_page_limit,
            hard_page_limit=hard_page_limit,
        )
        return run

    async def start_build_research_pack(
        self,
        *,
        topic: str,
        official_url: str | None = None,
        max_research_pages: int = 40,
        free_only: bool = False,
        caller_identity: str | None = None,
        caller_label: str = "",
        runtime: Mapping[str, Any] | None = None,
        research_targets: list[Mapping[str, Any]] | None = None,
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.BUILD_RESEARCH_PACK,
            topic,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
            free_only=free_only,
            extra_metadata=(
                {"research_targets": _plain_json_value(research_targets)}
                if research_targets
                else None
            ),
        )
        self._schedule_run(
            run,
            self._build_research_pack_impl,
            topic=topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
            free_only=free_only,
            research_targets=research_targets,
        )
        return run

    async def start_build_research_pack_safe(
        self,
        *,
        request: Any,
        caller_identity: str | None = None,
        caller_label: str = "",
        runtime: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        """Durably admit a path-free v3 run before scheduling execution."""

        from argus.workflows.research_targets import (
            canonical_request_json,
            canonical_request_projection,
            canonical_request_sha256,
        )

        projection = canonical_request_projection(request)
        request_json = canonical_request_json(request)
        request_hash = canonical_request_sha256(request)
        target_projection = projection.get("research_targets", [])
        effective_caller_label = caller_label or str(projection.get("caller", ""))
        plan = {
            "contract_schema": "build-research-pack/v3",
            "free_only": bool(projection.get("free_only", False)),
            "caller_identity": caller_identity or self._caller,
            "caller_label": str(projection.get("caller", "")),
            "official_url": projection.get("official_url"),
            "max_research_pages": projection.get("max_research_pages", 40),
            "targets": _plain_json_value(target_projection),
        }
        sanitized_runtime = self._runtime_projection(runtime, None)
        metadata = {
            "safe_start": True,
            "request": _plain_json_value(projection),
            "request_projection": _plain_json_value(projection),
            "request_json": request_json,
            "request_sha256": request_hash,
            "research_plan": plan,
            "caller_identity": caller_identity or self._caller,
            "authenticated_principal": caller_identity or self._caller,
            "caller_label": effective_caller_label,
            "body_caller": effective_caller_label,
            "runtime": sanitized_runtime,
            "start_runtime": sanitized_runtime,
        }
        try:
            run = self._create_run(
                WorkflowKind.BUILD_RESEARCH_PACK,
                str(projection["topic"]),
                caller_identity=caller_identity,
                caller_label=effective_caller_label,
                runtime=runtime,
                free_only=bool(projection.get("free_only", False)),
                extra_metadata=metadata,
            )
        except Exception as exc:
            raise WorkflowStartPersistenceError(
                "Workflow could not be durably accepted"
            ) from exc
        created_at = run.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
            run.created_at = created_at
        deadline = created_at + timedelta(seconds=self.TARGET_WORKFLOW_TIMEOUT_SECONDS)
        run.status_url = f"/api/workflows/{run.run_id}/status"
        run.metadata["deadline_at"] = deadline.astimezone(timezone.utc).isoformat()
        run.metadata["deadline"] = run.metadata["deadline_at"]
        run.metadata["_deadline_monotonic"] = self._clock_monotonic() + float(
            self.TARGET_WORKFLOW_TIMEOUT_SECONDS
        )
        run.metadata["runtime"] = sanitized_runtime
        # The alias makes the immutable start identity explicit to readers while
        # retaining the existing runtime metadata contract used by manifests.
        run.metadata["start_runtime"] = _plain_json_value(
            run.metadata.get("runtime", sanitized_runtime)
        )

        try:
            self._write_run_state(run)
        except Exception:
            run.status = WorkflowStatus.FAILED
            run.error = "WorkflowStatePersistenceError"
            run.finished_at = self._clock_now()
            run.metadata["failure"] = {
                "code": "WorkflowStatePersistenceError",
                "reason": "initial_pending_state_write_failed",
            }
            try:
                self._write_run_state(run)
            except Exception as terminal_exc:
                self._runs.pop(run.run_id, None)
                raise WorkflowStartPersistenceError(
                    "Workflow could not be durably accepted"
                ) from terminal_exc
            logger.warning(
                "Safe workflow %s admitted only as a durable failure after pending write failure",
                run.run_id,
            )
            return run

        self._schedule_run(
            run,
            self._build_research_pack_impl,
            topic=str(projection["topic"]),
            official_url=projection.get("official_url"),
            max_research_pages=int(projection.get("max_research_pages", 40)),
            free_only=bool(projection.get("free_only", False)),
            research_targets=target_projection,
        )
        return run

    @staticmethod
    def safe_start_response(run: WorkflowResult) -> dict[str, Any]:
        """Return exactly the safe-start metadata contract."""

        created_at = run.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return {
            "run_id": run.run_id,
            "kind": run.kind.value,
            "status": run.status.value,
            "target": run.target,
            "created_at": created_at.astimezone(timezone.utc).isoformat(),
            "status_url": f"/api/workflows/{run.run_id}/status",
            "request_sha256": run.metadata.get("request_sha256", ""),
        }

    async def start_search_and_summarize(
        self,
        *,
        query: str,
        max_search_results: int = 5,
        caller_identity: str | None = None,
        caller_label: str = "",
        runtime: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        """Start a background search-and-summarize workflow.

        Returns a pending WorkflowResult; the actual work runs in the background.
        """
        run = self._create_run(
            WorkflowKind.SEARCH_AND_SUMMARIZE,
            query,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
        )
        self._schedule_run(
            run,
            self._search_and_summarize_impl,
            query=query,
            max_search_results=max_search_results,
        )
        return run

    async def search_and_summarize(
        self,
        *,
        query: str,
        max_search_results: int = 5,
        caller_identity: str | None = None,
        caller_label: str = "",
        runtime: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        """Run a search-and-summarize workflow synchronously and return the result."""
        run = self._create_run(
            WorkflowKind.SEARCH_AND_SUMMARIZE,
            query,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
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
        required_urls: tuple[str, ...] | None = None,
        allow_partial: bool | None = None,
        minimum_artifacts: int | None = None,
        request_id: str | None = None,
        require_target_execution_evidence: bool = False,
    ) -> tuple[list[StoredDocument], list[CitationRef]]:
        """Build documents solely from the accepted composition projection."""
        composition_kwargs = {
            "max_results": max_results,
            "principal": self._caller_for_run(run),
            "request_id": request_id or run.run_id,
        }
        if selection_urls is not None:
            composition_kwargs["selection_urls"] = selection_urls
        if required_urls is not None:
            composition_kwargs["required_urls"] = required_urls
        if allow_partial is not None:
            composition_kwargs["allow_partial"] = allow_partial
        if minimum_artifacts is not None:
            composition_kwargs["minimum_artifacts"] = minimum_artifacts
        # Keep the legacy call shape for runs created before the free profile
        # seam (and for the omitted/default false path).  A true policy bit is
        # explicit and must reach the accepted composition authority.
        if run.metadata.get("free_only") is True:
            composition_kwargs["free_only"] = True
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
            "composition_trace": [
                _plain_json_value(trace)
                for trace in projection.get("composition_trace", ())
            ],
            "links": [_plain_json_value(link) for link in projection.get("links", ())],
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
        if require_target_execution_evidence:
            self._validate_target_execution_evidence(projection.get("artifacts"))
        documents: list[StoredDocument] = []
        citations: list[CitationRef] = []
        output_dir = Path(run.snapshot_dir) / section
        output_dir.mkdir(parents=True, exist_ok=True)
        retrieval_projection = operation.result if isinstance(operation.result, Mapping) else {}
        retrieval_execution_evidence = retrieval_projection.get("execution_evidence")
        if not isinstance(retrieval_execution_evidence, Mapping):
            retrieval_execution_evidence = {}
        for ordinal, artifact in enumerate(
            projection["artifacts"],
            start=citation_start + 1,
        ):
            artifact_text = str(artifact.get("text", ""))
            execution_evidence = artifact.get("execution_evidence")
            if not isinstance(execution_evidence, Mapping):
                execution_evidence = {}
            execution_diagnostics = artifact.get("execution_diagnostics")
            if not isinstance(execution_diagnostics, (list, tuple)):
                execution_diagnostics = execution_evidence.get(
                    "execution_diagnostics",
                    execution_evidence.get("diagnostics", ()),
                )
            retrieval_timestamp = artifact.get("retrieved_at") or artifact.get(
                "retrieval_timestamp"
            )
            text_sha256 = artifact.get("text_sha256") or hashlib.sha256(
                artifact_text.encode("utf-8")
            ).hexdigest()
            artifact_metadata = {
                "artifact_disposition": artifact["disposition"],
                "lead_text": _lead_text(artifact_text),
                "provider": artifact.get("provider"),
                "retrieval_provider": artifact.get("retrieval_provider"),
                "retrieval_egress": artifact.get("retrieval_egress"),
                "retrieval_machine": artifact.get("retrieval_machine"),
                "retrieval_source_type": artifact.get("retrieval_source_type"),
                "extractor": artifact.get("extractor"),
                "egress": artifact.get("egress"),
                "machine": artifact.get("machine"),
                "source_type": artifact.get("source_type") or source_type,
                "retrieved_at": retrieval_timestamp,
                "retrieval_timestamp": retrieval_timestamp,
                "source_date": artifact.get("source_date"),
                "text_sha256": text_sha256,
                "source_text_sha256": artifact.get("source_text_sha256", text_sha256),
                "result_count": artifact.get("result_count", 1),
                "execution_evidence": _plain_json_value(execution_evidence),
                "search_execution_evidence": _plain_json_value(
                    retrieval_execution_evidence
                ),
                "execution_diagnostics": _plain_json_value(execution_diagnostics),
                "timeout_source": artifact.get(
                    "timeout_source", execution_evidence.get("timeout_source")
                ),
                "operation_latency_ms": artifact.get(
                    "operation_latency_ms", execution_evidence.get("operation_latency_ms")
                ),
                "cache_latency_ms": artifact.get(
                    "cache_latency_ms", execution_evidence.get("cache_latency_ms")
                ),
                "cache_state": artifact.get("cache_state"),
                "cache_age": artifact.get("cache_age"),
                "cache_origin": artifact.get("cache_origin"),
                "spend_provenance": _plain_json_value(
                    artifact.get("spend_provenance")
                ),
                "freshness_age": artifact.get("freshness_age"),
                "freshness_window": artifact.get("freshness_window"),
                "freshness_reason": artifact.get("freshness_reason"),
                "free_profile_eligible": artifact.get("free_profile_eligible"),
            }
            document = self._store_document(
                output_dir,
                citation_id=f"S{ordinal}",
                url=artifact["url"],
                title=artifact["title"],
                text=artifact_text,
                word_count=artifact["word_count"],
                domain=urlparse(artifact["url"]).netloc.lower().lstrip("www."),
                role=role,
                source_type=source_type,
                extractor=artifact["extractor"],
                egress=artifact.get("egress"),
                machine=artifact.get("machine"),
                metadata=artifact_metadata,
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
        runtime: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.RECOVER_ARTICLE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
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
        runtime: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.CAPTURE_SITE,
            url,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
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
        free_only: bool = False,
        caller_identity: str | None = None,
        caller_label: str = "",
        runtime: Mapping[str, Any] | None = None,
        research_targets: list[Mapping[str, Any]] | None = None,
    ) -> WorkflowResult:
        run = self._create_run(
            WorkflowKind.BUILD_RESEARCH_PACK,
            topic,
            caller_identity=caller_identity,
            caller_label=caller_label,
            runtime=runtime,
            free_only=free_only,
            extra_metadata=(
                {"research_targets": _plain_json_value(research_targets)}
                if research_targets
                else None
            ),
        )
        return await self._execute_run(
            run.run_id,
            self._build_research_pack_impl,
            topic=topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
            free_only=free_only,
            research_targets=research_targets,
        )

    def _create_run(
        self,
        kind: WorkflowKind,
        target: str,
        *,
        caller_identity: str | None = None,
        caller_label: str = "",
        runtime: Mapping[str, Any] | None = None,
        free_only: bool | None = None,
        extra_metadata: Mapping[str, Any] | None = None,
    ) -> WorkflowResult:
        run_id = uuid.uuid4().hex[:12]
        slug = (
            _slug_from_url(target)
            if target.startswith(("http://", "https://"))
            else _slug_from_url(f"https://{target}")
        )
        snapshot_dir = self._paths.snapshots_dir / kind.value / slug / run_id
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        metadata = {
            "caller_identity": caller_identity or self._caller,
            "caller_label": caller_label,
        }
        if free_only is not None:
            # Persist only the bounded policy bit required to replay accepted
            # operation requests after a process reload.
            metadata["free_only"] = bool(free_only)
        if runtime is not None:
            # Keep only the bounded identity fields before persisting the run.
            # The route may receive a richer operational status payload that can
            # contain local paths, provider details, or other sensitive values.
            metadata["runtime"] = self._runtime_projection(runtime, None)
        if extra_metadata:
            metadata.update(_plain_json_value(extra_metadata))
        run = WorkflowResult(
            run_id=run_id,
            kind=kind,
            status=WorkflowStatus.PENDING,
            target=target,
            created_at=self._clock_now(),
            status_url=f"/api/workflows/{run_id}",
            snapshot_dir=str(snapshot_dir),
            metadata=metadata,
        )
        if self._is_targeted_run(run):
            self._ensure_target_deadline(run)
        self._runs[run_id] = run
        return run

    def _schedule_run(self, run: WorkflowResult, handler, **kwargs) -> asyncio.Task:
        """Schedule one workflow and retain its task for reload/orphan checks."""

        task = asyncio.create_task(self._execute_run(run.run_id, handler, **kwargs))
        self._tasks[run.run_id] = task

        def _forget(completed: asyncio.Task) -> None:
            current = self._tasks.get(run.run_id)
            if current is completed:
                self._tasks.pop(run.run_id, None)

        task.add_done_callback(_forget)
        return task

    def _caller_for_run(self, run: WorkflowResult) -> str:
        return str(run.metadata.get("caller_identity") or self._caller)

    async def _execute_run(self, run_id: str, handler, **kwargs) -> WorkflowResult:
        run = self._runs[run_id]
        run.status = WorkflowStatus.RUNNING
        run.started_at = self._clock_now()
        self._ensure_target_deadline(run)
        self._operation_task_set(run_id)
        running_state_persisted = False
        try:
            self._write_run_state(run)
            running_state_persisted = True
            await handler(run, **kwargs)
        except TargetWorkflowFailure as exc:
            logger.warning(
                "Targeted workflow %s stopped with stable code %s",
                run.run_id,
                exc.code,
            )
            run.status = WorkflowStatus.FAILED
            run.error = exc.code
            run.metadata["failure"] = {
                "code": exc.code,
                "reason": "targeted_research_failure",
            }
            self._hide_target_composition_diagnostics(run)
            self._rewrite_failed_artifacts(run)
        except asyncio.CancelledError:
            # A caller cancellation is terminal for a targeted workflow as
            # well.  Do not let a child accepted call outlive its terminal
            # state write, and preserve the stable deadline code when the
            # scheduler was cancelled at the workflow boundary.
            await self._cancel_outstanding_operations(run_id)
            run.status = WorkflowStatus.FAILED
            run.error = (
                "workflow_deadline_exceeded"
                if self._is_targeted_run(run)
                else "workflow_cancelled"
            )
            run.metadata["failure"] = {
                "code": run.error,
                "reason": "workflow_task_cancelled",
            }
            self._rewrite_failed_artifacts(run)
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
            if not running_state_persisted:
                logger.exception(
                    "Failed to persist running state for workflow %s", run.run_id
                )
                run.error = "WorkflowStatePersistenceError"
                run.metadata["failure"] = {
                    "code": "WorkflowStatePersistenceError",
                    "reason": "running_state_write_failed",
                }
                # No handler has run when the RUNNING transition cannot be
                # recorded. Keep the safe terminal projection artifact-free.
                run.report_path = None
                run.manifest_path = None
                run.artifacts = []
            else:
                logger.exception("Workflow %s failed", run.run_id)
                run.error = type(exc).__name__
            run.status = WorkflowStatus.FAILED
            self._rewrite_failed_artifacts(run)
        finally:
            # Await every in-flight accepted task before making the terminal
            # state durable.  This is the cancellation proof for global and
            # phase timeout paths.
            await self._cancel_outstanding_operations(run_id)
            if run.finished_at is None:
                run.finished_at = self._clock_now()
            terminal_state_persisted = False
            try:
                self._write_run_state(run)
                terminal_state_persisted = True
            except Exception:
                # Artifacts may already be visible when the final state write
                # fails.  Never leave a completed artifact paired with a
                # running/unknown durable state.
                logger.exception("Failed to persist terminal workflow state")
                if run.status in {
                    WorkflowStatus.COMPLETED,
                    WorkflowStatus.FAILED,
                }:
                    if run.status is WorkflowStatus.COMPLETED:
                        run.status = WorkflowStatus.FAILED
                        run.error = "WorkflowStatePersistenceError"
                        run.metadata["failure"] = {
                            "code": "WorkflowStatePersistenceError",
                            "reason": "terminal_state_write_failed",
                        }
                    self._rewrite_failed_artifacts(run)
                    try:
                        self._write_run_state(run)
                        terminal_state_persisted = True
                    except Exception:
                        logger.exception("Failed to persist workflow failure state")
            if terminal_state_persisted:
                self._clear_failure_marker(run.run_id)
            else:
                self._write_failure_marker_safely(run)
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
        free_only: bool | None = None,
        research_targets: list[Mapping[str, Any]] | None = None,
    ):
        if research_targets:
            return await self._build_targeted_research_pack_impl(
                run,
                topic=topic,
                max_research_pages=max_research_pages,
                free_only=free_only,
                research_targets=research_targets,
            )

        # The metadata copy is the durable source for every nested operation;
        # keep it synchronized for direct/internal callers of this handler.
        if free_only is not None:
            run.metadata["free_only"] = bool(free_only)
        if research_targets:
            run.metadata["research_targets"] = _plain_json_value(research_targets)
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
            soft_page_limit=_OFFICIAL_CAPTURE_PAGE_LIMIT,
            hard_page_limit=_OFFICIAL_CAPTURE_PAGE_LIMIT,
            required_urls=(),
            allow_partial=True,
            minimum_artifacts=1,
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
            required_urls=(),
            allow_partial=True,
            minimum_artifacts=1,
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

    @staticmethod
    def _target_authority_failure(code: str | None) -> bool:
        if not code:
            return False
        return code in {
            "unready",
            "persistence_failed",
            "invalid_request",
            "authentication_rejected",
            "policy_rejected",
            "providers_failed",
            "contract_error",
            "accepted_contract_error",
        } or "contract" in code.lower()

    @staticmethod
    def _target_disposition_is_usable(documents: list[StoredDocument]) -> bool:
        return any(
            str(document.metadata.get("artifact_disposition", "")).lower()
            == "usable"
            for document in documents
        )

    @staticmethod
    def _validate_target_execution_evidence(
        artifacts: object,
    ) -> None:
        """Reject targeted artifacts without their accepted diagnostic chain.

        The accepted composition projection is the only authority for target
        artifacts.  Do not infer a successful provider/extractor path from an
        empty mapping or from a zero-cost/nullable field: the evidence and its
        per-attempt diagnostic records must be explicitly present.  Nullable
        values (for example, no cache age on a miss) remain valid as long as
        their keys are present in the mapping.
        """

        if not isinstance(artifacts, (list, tuple)) or not artifacts:
            raise TargetWorkflowFailure(_TARGET_EXECUTION_EVIDENCE_MISSING)

        for artifact in artifacts:
            if not isinstance(artifact, Mapping):
                raise TargetWorkflowFailure(_TARGET_EXECUTION_EVIDENCE_MISSING)
            execution_evidence = artifact.get("execution_evidence")
            if not isinstance(execution_evidence, Mapping) or not execution_evidence:
                raise TargetWorkflowFailure(_TARGET_EXECUTION_EVIDENCE_MISSING)

            execution_diagnostics = artifact.get("execution_diagnostics")
            if not isinstance(execution_diagnostics, (list, tuple)):
                execution_diagnostics = execution_evidence.get(
                    "execution_diagnostics",
                    execution_evidence.get("diagnostics"),
                )
            if (
                not isinstance(execution_diagnostics, (list, tuple))
                or not execution_diagnostics
                or any(not isinstance(item, Mapping) for item in execution_diagnostics)
            ):
                raise TargetWorkflowFailure(_TARGET_EXECUTION_EVIDENCE_MISSING)
            if any(
                not _TARGET_EXECUTION_DIAGNOSTIC_FIELDS.issubset(item)
                for item in execution_diagnostics
            ):
                raise TargetWorkflowFailure(_TARGET_EXECUTION_EVIDENCE_MISSING)

    @staticmethod
    def _hide_target_composition_diagnostics(run: WorkflowResult) -> None:
        """Keep failed candidate details out of public run state and reports."""

        for key in (
            "compositions",
            "composition",
            "composition_receipt_ref",
        ):
            run.metadata.pop(key, None)

    async def _compose_target_candidate(
        self,
        run: WorkflowResult,
        operation: AcceptedOperation,
        *,
        candidate_url: str,
        request_id: str,
        citation_start: int,
        required: bool,
    ) -> tuple[list[StoredDocument], list[CitationRef]]:
        """Try one receipt-bound candidate, translating only target-local failures."""

        try:
            documents, citations = await self._compose_search_documents(
                run,
                operation,
                max_results=1,
                section="targeted-research",
                role="primary" if required else "external_research",
                source_type=(
                    "targeted_first_party" if required else "external_research"
                ),
                selection_urls=(candidate_url,),
                required_urls=(candidate_url,),
                allow_partial=False,
                minimum_artifacts=1,
                citation_start=citation_start,
                request_id=request_id,
                require_target_execution_evidence=True,
            )
        except WorkflowOperationFailure as exc:
            code = exc.operation_code
            self._hide_target_composition_diagnostics(run)
            bare_code = (
                code.removeprefix("workflow_composition_")
                if isinstance(code, str)
                else code
            )
            if self._target_authority_failure(bare_code):
                raise TargetWorkflowFailure(bare_code) from exc
            raise TargetCandidateFailure(requirement_ref="target") from exc
        self._hide_target_composition_diagnostics(run)
        if not documents or not self._target_disposition_is_usable(documents):
            raise TargetCandidateFailure(requirement_ref="target")
        if any(_normal_url(document.url) != _normal_url(candidate_url) for document in documents):
            raise TargetCandidateFailure(requirement_ref="target")
        return documents, citations

    async def _build_targeted_research_pack_impl(
        self,
        run: WorkflowResult,
        *,
        topic: str,
        max_research_pages: int,
        free_only: bool | None,
        research_targets: list[Mapping[str, Any]],
    ) -> WorkflowResult:
        """Execute a target-first pack with bounded concurrent target lanes."""

        effective_free_only = bool(free_only)
        run.metadata["free_only"] = effective_free_only
        run.metadata["research_targets"] = _plain_json_value(research_targets)
        self._ensure_target_deadline(run)
        requirements = flatten_requirements(research_targets)
        # Validate the page budget before any accepted search is attempted.
        page_budget_math(len(requirements), max_research_pages)
        caller = str(run.metadata.get("caller_label") or self._caller)
        search_requests = make_target_search_requests(
            research_targets,
            free_only=effective_free_only,
            caller=caller,
        )

        self._report(0, 4, "Searching receipt-bound target requirements...")
        accepted_searches: list[Any | None] = [None] * len(search_requests)
        grouped: dict[int, list[tuple[int, Any]]] = {}
        for index, request in enumerate(search_requests):
            requirement = requirements[index]
            grouped.setdefault(requirement.target_index, []).append((index, request))

        async def search_target(target_index: int, requests: list[tuple[int, Any]]):
            for index, request in requests:
                accepted_searches[index] = await self._invoke_target_operation(
                    run,
                    target_key=f"target-{target_index}",
                    timeout_seconds=self.TARGET_SEARCH_TIMEOUT_SECONDS,
                    timeout_code="workflow_required_target_search_timeout",
                    operation_factory=lambda request=request: self._operation_search(
                        run,
                        query=request.query,
                        mode="research",
                        max_results=8,
                        request_id=request.request_id,
                    ),
                )

        search_tasks = [
            asyncio.create_task(search_target(target_index, requests))
            for target_index, requests in grouped.items()
        ]
        try:
            if search_tasks:
                await asyncio.gather(*search_tasks)
        except BaseException:
            for task in search_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*search_tasks, return_exceptions=True)
            raise
        if any(operation is None for operation in accepted_searches):
            raise TargetWorkflowFailure("workflow_required_target_unready")

        plan: TargetResearchPlan = plan_target_research(
            research_targets,
            accepted_searches,
            max_research_pages=max_research_pages,
            free_only=effective_free_only,
            caller=caller,
        )
        run.metadata["targeted_research"] = {
            "requirement_count": len(plan.requirements),
            "target_candidate_attempts": 0,
            "external_candidate_limit": len(plan.external_candidates),
            "page_budget": max_research_pages,
        }
        self._report(1, 4, "Extracting required target artifacts...")

        requirement_documents: dict[int, tuple[list[StoredDocument], list[CitationRef]]] = {}
        target_groups: dict[int, list[tuple[int, Any]]] = {}
        for selection_index, selection in enumerate(plan.requirements):
            target_groups.setdefault(selection.requirement.target_index, []).append(
                (selection_index, selection)
            )

        async def compose_target(
            target_index: int,
            selections: list[tuple[int, Any]],
        ):
            for selection_index, selection in selections:
                usable: tuple[list[StoredDocument], list[CitationRef]] | None = None
                for candidate in selection.candidates:
                    run.metadata["targeted_research"]["target_candidate_attempts"] += 1
                    try:
                        usable = await self._invoke_target_operation(
                            run,
                            target_key=f"target-{target_index}",
                            timeout_seconds=self.TARGET_CANDIDATE_TIMEOUT_SECONDS,
                            timeout_code="target_candidate_timeout",
                            operation_factory=lambda selection=selection, candidate=candidate, selection_index=selection_index: self._compose_target_candidate(
                                run,
                                accepted_searches[selection.search_index],
                                candidate_url=candidate.url,
                                request_id=selection.request_id,
                                citation_start=selection_index,
                                required=True,
                            ),
                        )
                    except TargetCandidateFailure:
                        continue
                    except TargetWorkflowFailure as exc:
                        if exc.code == "target_candidate_timeout":
                            continue
                        raise
                    if usable is not None:
                        break
                if usable is None:
                    raise TargetWorkflowFailure(
                        "workflow_required_target_extraction_failed",
                        requirement_ref=selection.requirement_ref,
                    )
                docs, cits = usable
                for document in docs[:1]:
                    document.metadata.update(
                        {
                            "target_name": selection.requirement.target_name,
                            "claim_class": selection.requirement.claim_class,
                            "requirement_ref": selection.requirement_ref,
                            "target_index": selection.requirement.target_index,
                            "requirement_index": selection.requirement.requirement_index,
                        }
                    )
                requirement_documents[selection_index] = (docs[:1], cits[:1])

        composition_tasks = [
            asyncio.create_task(compose_target(target_index, selections))
            for target_index, selections in target_groups.items()
        ]
        try:
            if composition_tasks:
                await asyncio.gather(*composition_tasks)
        except BaseException:
            for task in composition_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*composition_tasks, return_exceptions=True)
            raise

        documents: list[StoredDocument] = []
        citations: list[CitationRef] = []
        for selection_index in range(len(plan.requirements)):
            target_documents, target_citations = requirement_documents[selection_index]
            documents.extend(target_documents)
            citations.extend(target_citations)

        self._report(2, 4, "Extracting independent external evidence...")
        external_documents: list[StoredDocument] = []
        external_citations: list[CitationRef] = []
        external_slots = min(2, plan.page_math.external_page_slots)
        for candidate in plan.external_candidates:
            if len(external_documents) >= external_slots:
                break
            try:
                operation_index = next(
                    index
                    for index, operation in enumerate(accepted_searches)
                    if any(
                        _normal_url(str(item.get("url", "")))
                        == _normal_url(candidate.url)
                        for item in (operation.result or {}).get("results", ())
                        if isinstance(item, Mapping)
                    )
                )
                extracted = await self._invoke_target_operation(
                    run,
                    target_key="external",
                    timeout_seconds=self.TARGET_EXTERNAL_TIMEOUT_SECONDS,
                    timeout_code="external_remainder_timeout",
                    deadline_marker="_target_external_deadline_monotonic",
                    operation_factory=lambda candidate=candidate, operation_index=operation_index: self._compose_target_candidate(
                        run,
                        accepted_searches[operation_index],
                        candidate_url=candidate.url,
                        request_id=f"{candidate.requirement_ref}-{len(external_documents)}",
                        citation_start=len(documents) + len(external_documents),
                        required=len(external_documents) == 0,
                    ),
                )
            except TargetCandidateFailure:
                continue
            except TargetWorkflowFailure as exc:
                if exc.code == "external_remainder_timeout":
                    break
                raise
            external_docs, external_cits = extracted
            for document in external_docs[:1]:
                document.metadata.update(
                    {
                        "target_name": None,
                        "claim_class": "external-secondary",
                        "requirement_ref": candidate.requirement_ref,
                    }
                )
            external_documents.extend(external_docs[:1])
            external_citations.extend(external_cits[:1])

        if not external_documents:
            raise TargetWorkflowFailure("workflow_external_evidence_extraction_failed")
        if len(external_documents) < external_slots:
            run.metadata["degraded_reasons"] = ["degraded_external_unavailable"]

        documents.extend(external_documents)
        citations.extend(external_citations)
        run.documents = documents
        run.citations = citations
        run.metadata["targeted_research"].update(
            {
                "target_document_count": len(documents) - len(external_documents),
                "external_document_count": len(external_documents),
            }
        )
        run.summary_sections = [
            SummarySection(
                heading="Targeted Research",
                body=(
                    f"Saved one usable artifact for each of {len(plan.requirements)} "
                    f"target requirements and {len(external_documents)} independent "
                    "external source(s)."
                ),
                citation_ids=[document.id for document in documents[:4]],
            )
        ]
        self._report(3, 4, "Generating targeted research report...")
        self._check_target_budget(run)
        pack_dir = self._paths.research_dir / "packs" / _slug_from_url(topic)
        self._finalize_run(
            run,
            title=f"Research Pack: {topic}",
            report_name="SUMMARY.md",
            current_dir=pack_dir,
        )
        return run

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
            max_results=min(max(limit * 2, 20), 50),
        )
        results = (operation.result or {}).get("results", ())
        urls: list[str] = []
        seen: set[str] = set()
        domain_counts: dict[str, int] = {}
        for result in results:
            result_url = str(result["url"])
            normalized = _normal_url(result_url)
            if normalized in seen:
                continue
            seen.add(normalized)
            result_root = _domain_root(urlparse(result_url).netloc)
            if result_root == official_root:
                continue
            if domain_counts.get(result_root, 0) >= 2:
                continue
            urls.append(result_url)
            domain_counts[result_root] = domain_counts.get(result_root, 0) + 1
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
        required_urls: tuple[str, ...] | None = None,
        allow_partial: bool | None = None,
        minimum_artifacts: int | None = None,
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
            required_urls=required_urls,
            allow_partial=allow_partial,
            minimum_artifacts=minimum_artifacts,
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
        required_urls: tuple[str, ...] | None = None,
        allow_partial: bool | None = None,
        minimum_artifacts: int | None = None,
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
                required_urls=required_urls,
                allow_partial=allow_partial,
                minimum_artifacts=minimum_artifacts,
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
                required_urls=required_urls,
                allow_partial=allow_partial,
                minimum_artifacts=minimum_artifacts,
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
        run.finished_at = self._clock_now()
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
        # Persist the terminal state and expected allowlisted artifact
        # registrations before publishing either file.  A crash between these
        # writes can only expose a completed run with unavailable artifacts,
        # never a running run with completed artifacts.
        self._write_run_state(run)
        self._atomic_write_text(report_path, report_content)
        manifest_content = (
            json.dumps(self._public_manifest(run), indent=2, default=_json_default)
            + "\n"
        )
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
            with temporary.open("w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
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
                        self._public_manifest(run), indent=2, default=_json_default
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
        row = f"| {slug} | {source_url} | {self._clock_now().date().isoformat()} | {path} |"
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
                "  Disposition: "
                f"{citation.artifact_disposition}"
            )
        lines.append("")
        return "\n".join(lines)

    def _public_manifest(self, run: WorkflowResult) -> dict[str, Any]:
        """Build an explicit path-free manifest for remote artifact readers."""
        status = self.get_public_status(run)
        artifact_index = []
        for artifact in status["artifacts"]:
            if artifact["kind"] == "manifest":
                # A manifest cannot contain its own final SHA-256 without a
                # recursive fixed point.  The status endpoint computes the
                # truthful hash/size over the published bytes; keep the
                # embedded index descriptive rather than publishing stale
                # null metadata.
                artifact_index.append(
                    {
                        "kind": "manifest",
                        "available": True,
                        "description": artifact["description"],
                        "media_type": artifact["media_type"],
                    }
                )
            else:
                artifact_index.append(artifact)
        sources = []
        for document in run.documents:
            metadata = document.metadata if isinstance(document.metadata, dict) else {}
            safe_metadata: dict[str, Any] = {
                "artifact_disposition": (
                    metadata.get("artifact_disposition")
                    if metadata.get("artifact_disposition")
                    in {"usable", "partial", "rejected"}
                    else "usable"
                ),
                "evidence_ids": self._safe_evidence_ids(metadata.get("evidence_ids")),
            }
            sources.append(
                {
                    "id": document.id,
                    "url": document.url,
                    "title": document.title,
                    "word_count": document.word_count,
                    "domain": document.domain,
                    "role": document.role,
                    "source_type": document.source_type,
                    "extractor": document.extractor,
                    "egress": document.egress,
                    "machine": document.machine,
                    "metadata": safe_metadata,
                }
            )
        return {
            "schema": "argus.workflow-manifest.v1",
            "run_id": status["run_id"],
            "kind": status["kind"],
            "status": status["status"],
            "target": status["target"],
            "created_at": status["created_at"],
            "started_at": status["started_at"],
            "finished_at": status["finished_at"],
            "status_url": status["status_url"],
            "artifacts": artifact_index,
            "sources": sources,
            "citations": status["citations"],
            "summary_sections": [
                {
                    "heading": section.heading,
                    "body": section.body,
                    "citation_ids": list(section.citation_ids),
                }
                for section in run.summary_sections
            ],
            "source_count": status["source_count"],
            "domain_count": status["domain_count"],
            "primary_source_count": status["primary_source_count"],
            "partial_reasons": status["partial_reasons"],
            "degraded_reasons": status["degraded_reasons"],
            "cost_state": status["cost_state"],
            "runtime": status["runtime"],
            "research_plan": status["research_plan"],
            "error_code": status["error_code"],
        }

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
            created_at=_parse_dt(payload.get("created_at"))
            or self._clock_now(),
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

    def _failure_marker_path(self, run_id: str) -> Path:
        if not _SAFE_ID_RE.fullmatch(str(run_id)):
            raise ValueError("invalid workflow run identifier")
        return self._paths.workflow_runs_dir / (
            f"{run_id}{_WORKFLOW_FAILURE_MARKER_SUFFIX}"
        )

    @staticmethod
    def _safe_failure_marker_value(value: Any) -> str | None:
        if not isinstance(value, str) or len(value) > 128:
            return None
        return value if _SAFE_ID_RE.fullmatch(value) else None

    @classmethod
    def _failure_marker_projection(cls, run: WorkflowResult) -> dict[str, Any]:
        failure = run.metadata.get("failure")
        safe_failure: dict[str, str] = {}
        if isinstance(failure, Mapping):
            for key in ("code", "reason", "outcome"):
                value = cls._safe_failure_marker_value(failure.get(key))
                if value is not None:
                    safe_failure[key] = value
        error = cls._safe_failure_marker_value(run.error)
        if error is None:
            error = "WorkflowStatePersistenceError"
        if not safe_failure:
            safe_failure = {
                "code": error,
                "reason": "terminal_state_write_failed",
            }
        finished_at = run.finished_at
        if finished_at is None:
            finished_at = datetime.now(timezone.utc)
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        return {
            "schema": _WORKFLOW_FAILURE_MARKER_SCHEMA,
            "run_id": run.run_id,
            "status": WorkflowStatus.FAILED.value,
            "error": error,
            "finished_at": finished_at.astimezone(timezone.utc).isoformat(),
            "failure": safe_failure,
            "clear_artifacts": safe_failure.get("reason")
            == "running_state_write_failed",
        }

    def _write_failure_marker(self, run: WorkflowResult) -> None:
        """Record a bounded terminal failure when the main state is unavailable."""

        marker_path = self._failure_marker_path(run.run_id)
        marker = self._failure_marker_projection(run)
        self._atomic_write_text(
            marker_path,
            json.dumps(marker, indent=2, sort_keys=True) + "\n",
        )

    def _write_failure_marker_safely(self, run: WorkflowResult) -> None:
        try:
            self._write_failure_marker(run)
        except Exception:
            # The task has already reached a stable in-memory failure.  Keep
            # the marker attempt bounded and never surface an I/O detail to the
            # workflow task or its caller.
            logger.exception("Failed to persist workflow failure marker")

    def _read_failure_marker(self, run_id: str) -> dict[str, Any] | None:
        try:
            marker_path = self._failure_marker_path(run_id)
            if not marker_path.is_file():
                return None
            payload = json.loads(marker_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning("Failed to read workflow failure marker %s", run_id)
            return None
        if not isinstance(payload, Mapping):
            return None
        if payload.get("schema") != _WORKFLOW_FAILURE_MARKER_SCHEMA:
            return None
        if payload.get("run_id") != run_id:
            return None
        if payload.get("status") != WorkflowStatus.FAILED.value:
            return None
        error = self._safe_failure_marker_value(payload.get("error"))
        if error is None:
            return None
        failure = payload.get("failure")
        if not isinstance(failure, Mapping):
            return None
        safe_failure: dict[str, str] = {}
        for key in ("code", "reason", "outcome"):
            value = self._safe_failure_marker_value(failure.get(key))
            if value is not None:
                safe_failure[key] = value
        if not safe_failure:
            return None
        finished_at = _parse_dt(payload.get("finished_at"))
        return {
            "error": error,
            "failure": safe_failure,
            "finished_at": finished_at,
            "clear_artifacts": payload.get("clear_artifacts") is True,
        }

    @staticmethod
    def _apply_failure_marker(
        run: WorkflowResult, marker: Mapping[str, Any]
    ) -> None:
        run.status = WorkflowStatus.FAILED
        run.error = str(marker["error"])
        finished_at = marker.get("finished_at")
        if isinstance(finished_at, datetime):
            if finished_at.tzinfo is None:
                finished_at = finished_at.replace(tzinfo=timezone.utc)
            run.finished_at = finished_at.astimezone(timezone.utc)
        elif run.finished_at is None:
            run.finished_at = datetime.now(timezone.utc)
        run.metadata["failure"] = _plain_json_value(marker["failure"])
        if marker.get("clear_artifacts") is True:
            run.report_path = None
            run.manifest_path = None
            run.artifacts = []

    def _clear_failure_marker(self, run_id: str) -> None:
        try:
            marker_path = self._failure_marker_path(run_id)
            marker_path.unlink(missing_ok=True)
            directory_fd = os.open(marker_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            logger.warning("Failed to clear workflow failure marker %s", run_id)

    def _write_run_state(self, run: WorkflowResult) -> None:
        payload = self._serialize_run(run)
        state_path = self._paths.workflow_runs_dir / f"{run.run_id}.json"
        self._atomic_write_text(
            state_path,
            json.dumps(payload, indent=2, default=_json_default) + "\n",
        )
