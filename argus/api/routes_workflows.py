"""Workflow endpoints for retrieval-oriented Argus features."""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from argus.api.schemas import (
    BuildResearchPackWorkflowRequest,
    CaptureSiteWorkflowRequest,
    CitationSchema,
    RecoverArticleWorkflowRequest,
    StoredDocumentSchema,
    SummarySectionSchema,
    WorkflowArtifactSchema,
    WorkflowRunResponse,
    SearchAndSummarizeWorkflowRequest,
    WorkflowArtifactReadResponse,
    WorkflowStatusResponse,
)

from argus.workflows import WorkflowService
from argus.workflows.service import (
    WorkflowArtifactNotFound,
    WorkflowArtifactNotReady,
    WorkflowArtifactRangeError,
    WorkflowArtifactUnavailable,
)

router = APIRouter()


def get_workflows(request: Request) -> WorkflowService:
    return request.app.state.get_workflows()


def _to_response(run) -> WorkflowRunResponse:
    return WorkflowRunResponse(
        run_id=run.run_id,
        kind=run.kind.value,
        status=run.status.value,
        target=run.target,
        created_at=run.created_at.isoformat() if run.created_at else None,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        status_url=run.status_url,
        snapshot_dir=run.snapshot_dir,
        report_path=run.report_path,
        manifest_path=run.manifest_path,
        artifacts=[
            WorkflowArtifactSchema(**artifact.__dict__) for artifact in run.artifacts
        ],
        documents=[
            StoredDocumentSchema(**document.__dict__) for document in run.documents
        ],
        citations=[CitationSchema(**citation.__dict__) for citation in run.citations],
        summary_sections=[
            SummarySectionSchema(**section.__dict__) for section in run.summary_sections
        ],
        metadata=run.metadata,
        error=run.error,
    )


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
    run = await workflows.start_build_research_pack(
        topic=req.topic,
        official_url=req.official_url,
        max_research_pages=req.max_research_pages,
        caller_identity=getattr(request.state, "caller_identity", "") or "unknown",
        caller_label=req.caller,
        runtime=_runtime_projection(request),
    )
    return _to_response(run)


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
    run = workflows.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    try:
        return workflows.get_public_status(run, runtime=_runtime_projection(request))
    except WorkflowArtifactUnavailable as exc:
        raise HTTPException(
            status_code=500,
            detail="Workflow artifact unavailable",
        ) from exc


@router.get(
    "/workflows/{run_id}/artifacts/{artifact}",
    response_model=WorkflowArtifactReadResponse,
)
async def workflow_artifact(
    run_id: str,
    artifact: str,
    offset: int = Query(0, ge=0),
    max_bytes: int = Query(64 * 1024, ge=1, le=256 * 1024),
    workflows: WorkflowService = Depends(get_workflows),
):
    run = workflows.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    try:
        return workflows.read_artifact(
            run,
            artifact,
            offset=offset,
            max_bytes=max_bytes,
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
    except WorkflowArtifactUnavailable as exc:
        raise HTTPException(
            status_code=500,
            detail="Workflow artifact unavailable",
        ) from exc


@router.get("/workflows/{run_id}", response_model=WorkflowRunResponse)
async def workflow_status(
    run_id: str,
    workflows: WorkflowService = Depends(get_workflows),
):
    run = workflows.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown workflow run: {run_id}")
    return _to_response(run)
