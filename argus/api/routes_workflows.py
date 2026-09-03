"""Workflow endpoints for retrieval-oriented Argus features."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from argus.api.schemas import (
    BuildResearchPackWorkflowRequest,
    CaptureSiteWorkflowRequest,
    CitationSchema,
    RecoverArticleWorkflowRequest,
    StoredDocumentSchema,
    SummarySectionSchema,
    WorkflowArtifactSchema,
    WorkflowRunResponse,
    WorkflowStartResponse,
    SearchAndSummarizeWorkflowRequest,
    WorkflowArtifactReadResponse,
    WorkflowStatusResponse,
)

from argus.workflows import WorkflowService
from argus.workflows.models import WorkflowStatus
from argus.workflows.service import (
    WorkflowArtifactNotFound,
    WorkflowArtifactNotReady,
    WorkflowArtifactNotPublished,
    WorkflowArtifactRangeError,
    WorkflowArtifactUnavailable,
    WorkflowAuthorityUnavailable,
    WorkflowOwnerMismatch,
    WorkflowOwnerUnavailable,
    WorkflowStartPersistenceError,
    _public_body,
    _public_error_code,
    _public_label,
    _public_title,
    _public_url,
)

router = APIRouter()


def get_workflows(request: Request) -> WorkflowService:
    return request.app.state.get_workflows()


def _to_response(run) -> WorkflowRunResponse:
    """Return the legacy shape with authority-local values redacted."""

    return WorkflowRunResponse(
        run_id=_public_label(run.run_id),
        kind=run.kind.value,
        status=run.status.value,
        target=_public_body(run.target),
        created_at=run.created_at.isoformat() if run.created_at else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        status_url=f"/api/workflows/{_public_label(run.run_id)}",
        snapshot_dir="",
        report_path=None,
        manifest_path=None,
        artifacts=[
            WorkflowArtifactSchema(
                kind=_public_label(artifact.kind),
                path="",
                description=_public_body(artifact.description) if artifact.description else "",
            )
            for artifact in run.artifacts
        ],
        documents=[
            StoredDocumentSchema(
                id=_public_label(document.id),
                url=_public_url(document.url),
                title=_public_title(document.title),
                artifact_path="",
                word_count=max(0, document.word_count),
                domain=_public_label(document.domain, fallback=""),
                role=_public_label(document.role, fallback="source"),
                source_type=_public_label(document.source_type, fallback="web"),
                extractor=_public_label(document.extractor, fallback="")
                if document.extractor
                else None,
                egress=_public_label(document.egress, fallback="")
                if document.egress
                else None,
                machine=_public_label(document.machine, fallback="")
                if document.machine
                else None,
                metadata={},
            )
            for document in run.documents
        ],
        citations=[
            CitationSchema(
                id=_public_label(citation.id),
                title=_public_title(citation.title),
                url=_public_url(citation.url),
                artifact_path="",
                note=_public_body(citation.note) if citation.note else "",
            )
            for citation in run.citations
        ],
        summary_sections=[
            SummarySectionSchema(
                heading=_public_title(section.heading),
                body=_public_body(section.body) if section.body else "",
                citation_ids=[_public_label(cid) for cid in section.citation_ids],
            )
            for section in run.summary_sections
        ],
        metadata={},
        error=_public_error_code(run.error) if run.error else None,
    )


def _principal(request: Request) -> str | None:
    """Return middleware-authenticated identity, without inventing one."""

    value = getattr(request.state, "caller_identity", None)
    return value if isinstance(value, str) and value else None


def _raise_owner_http_error(exc: Exception) -> None:
    if isinstance(exc, WorkflowOwnerMismatch):
        raise HTTPException(status_code=404, detail="Workflow run not found") from exc
    if isinstance(exc, WorkflowArtifactNotPublished):
        raise HTTPException(
            status_code=409,
            detail="Workflow artifact is not ready",
        ) from exc
    if isinstance(exc, WorkflowArtifactUnavailable):
        raise HTTPException(
            status_code=503,
            detail="Workflow authority unavailable",
        ) from exc
    if isinstance(exc, (WorkflowOwnerUnavailable, WorkflowAuthorityUnavailable)):
        raise HTTPException(
            status_code=503,
            detail="Workflow authority unavailable",
        ) from exc


@router.post("/workflows/recover-article", response_model=WorkflowRunResponse)
async def recover_article(
    req: RecoverArticleWorkflowRequest,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
):
    run = await workflows.start_recover_article(
        url=req.url,
        title=req.title,
        domain=req.domain,
        caller_identity=getattr(request.state, "caller_identity", "") or "unknown",
        caller_label=req.caller,
        runtime=_runtime_projection(request),
    )
    return _to_response(run)


@router.post("/workflows/capture-site", response_model=WorkflowRunResponse)
async def capture_site(
    req: CaptureSiteWorkflowRequest,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
):
    run = await workflows.start_capture_site(
        url=req.url,
        soft_page_limit=req.soft_page_limit,
        hard_page_limit=req.hard_page_limit,
        caller_identity=getattr(request.state, "caller_identity", "") or "unknown",
        caller_label=req.caller,
        runtime=_runtime_projection(request),
    )
    return _to_response(run)


@router.post("/workflows/build-research-pack", response_model=WorkflowRunResponse)
async def build_research_pack(
    req: BuildResearchPackWorkflowRequest,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
):
    # Pydantic URL objects are intentionally retained for schema validation,
    # but the legacy service boundary consumes plain URL strings.
    payload = req.model_dump(mode="json")
    start_kwargs = {
        "topic": payload["topic"],
        "official_url": payload["official_url"],
        "max_research_pages": payload["max_research_pages"],
        "free_only": payload["free_only"],
        "caller_identity": getattr(request.state, "caller_identity", "") or "unknown",
        "caller_label": payload["caller"],
        "runtime": _runtime_projection(request),
    }
    if payload.get("research_targets"):
        start_kwargs["research_targets"] = payload["research_targets"]
    run = await workflows.start_build_research_pack(
        **start_kwargs,
    )
    return _to_response(run)


@router.post(
    "/workflows/build-research-pack/start",
    response_model=WorkflowStartResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_build_research_pack(
    req: BuildResearchPackWorkflowRequest,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
):
    """Admit a path-free workflow only after its pending state is durable."""

    try:
        run = await workflows.start_build_research_pack_safe(
            request=req,
            caller_identity=getattr(request.state, "caller_identity", "") or "unknown",
            caller_label=req.caller,
            runtime=_runtime_projection(request),
        )
    except WorkflowStartPersistenceError as exc:
        raise HTTPException(
            status_code=503,
            detail="Workflow could not be durably accepted",
        ) from exc
    if run.status is WorkflowStatus.FAILED and run.error == "WorkflowStatePersistenceError":
        raise HTTPException(
            status_code=503,
            detail="Workflow could not be durably accepted",
        )
    presenter = getattr(workflows, "safe_start_response", None)
    return presenter(run) if callable(presenter) else WorkflowService.safe_start_response(run)


@router.post("/workflows/search-and-summarize", response_model=WorkflowRunResponse)
async def search_and_summarize(
    req: SearchAndSummarizeWorkflowRequest,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
):
    run = await workflows.start_search_and_summarize(
        query=req.query,
        max_search_results=req.max_search_results,
        caller_identity=getattr(request.state, "caller_identity", "") or "unknown",
        caller_label=req.caller,
        runtime=_runtime_projection(request),
    )
    return _to_response(run)


def _runtime_projection(request: Request) -> dict:
    status = getattr(request.app.state, "operational_status", None)
    full_status = getattr(status, "full_status", None)
    if not callable(full_status):
        return {}
    try:
        payload = full_status()
        if not isinstance(payload, Mapping):
            return {}
        return WorkflowService._runtime_projection(payload, None)
    except Exception:
        return {}


@router.get("/workflows/{run_id}/status", response_model=WorkflowStatusResponse)
async def workflow_status_projection(
    run_id: str,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
):
    principal = _principal(request)
    try:
        run = workflows.get_run(run_id, principal=principal)
    except (WorkflowOwnerMismatch, WorkflowOwnerUnavailable) as exc:
        _raise_owner_http_error(exc)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    try:
        return workflows.get_public_status(
            run,
            runtime=_runtime_projection(request),
            principal=principal,
        )
    except (
        WorkflowArtifactNotPublished,
        WorkflowArtifactUnavailable,
        WorkflowOwnerMismatch,
        WorkflowOwnerUnavailable,
    ) as exc:
        _raise_owner_http_error(exc)


@router.get(
    "/workflows/{run_id}/artifacts/{artifact}",
    response_model=WorkflowArtifactReadResponse,
)
async def workflow_artifact(
    run_id: str,
    artifact: str,
    request: Request,
    offset: int = Query(0, ge=0),
    max_bytes: int = Query(64 * 1024, ge=1, le=256 * 1024),
    workflows: WorkflowService = Depends(get_workflows),
):
    principal = _principal(request)
    try:
        run = workflows.get_run(run_id, principal=principal)
    except (WorkflowOwnerMismatch, WorkflowOwnerUnavailable) as exc:
        _raise_owner_http_error(exc)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    try:
        return workflows.read_artifact(
            run,
            artifact,
            offset=offset,
            max_bytes=max_bytes,
            principal=principal,
        )
    except WorkflowArtifactNotFound as exc:
        raise HTTPException(
            status_code=404, detail="Workflow artifact not found"
        ) from exc
    except WorkflowArtifactNotReady as exc:
        raise HTTPException(
            status_code=409, detail="Workflow artifact is not ready"
        ) from exc
    except WorkflowArtifactRangeError as exc:
        raise HTTPException(
            status_code=422, detail="Workflow artifact byte range is invalid"
        ) from exc
    except WorkflowArtifactNotPublished as exc:
        raise HTTPException(
            status_code=409, detail="Workflow artifact is not ready"
        ) from exc
    except WorkflowArtifactUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="Workflow authority unavailable",
        ) from exc
    except (WorkflowOwnerMismatch, WorkflowOwnerUnavailable) as exc:
        _raise_owner_http_error(exc)


@router.get("/workflows/{run_id}", response_model=WorkflowRunResponse)
async def workflow_status(
    run_id: str,
    request: Request,
    workflows: WorkflowService = Depends(get_workflows),
):
    principal = _principal(request)
    try:
        run = workflows.get_run(run_id, principal=principal)
    except (WorkflowOwnerMismatch, WorkflowOwnerUnavailable) as exc:
        _raise_owner_http_error(exc)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown workflow run: {run_id}")
    return _to_response(run)
