"""Admin endpoints for privileged operations."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from argus.api.schemas import (
    PathsResponse,
    ProviderSnapshotRequest,
    ProviderTestRequest,
    SpendResolutionRequest,
)
from argus.api.admin_operations import (
    AdminApplicationService,
    AdminConflictError,
    AdminInvalidError,
    AdminNotFoundError,
    AdminUnauthorizedError,
    UnknownAdminProviderError,
)
from argus.api.admin_presenters import present_admin_facts
from argus.api.provider_operations import (
    ProbeRejected,
    ProviderApplicationService,
    UnknownProviderError,
)
from argus.api.provider_presenters import present_provider_facts
from argus.workflows import WorkflowService

router = APIRouter(prefix="/admin")


def get_provider_presentation(request: Request) -> ProviderApplicationService:
    return request.app.state.provider_presentation


def get_workflows(request: Request) -> WorkflowService:
    return request.app.state.get_workflows()


def get_admin_operations(request: Request) -> AdminApplicationService:
    return request.app.state.admin_operations


@router.get("/maya-outbox/status")
async def maya_outbox_status(
    operations: AdminApplicationService = Depends(get_admin_operations),
):
    """Return bounded delivery state without payloads or capture identifiers."""
    return present_admin_facts(operations.maya_outbox_status())


@router.get("/maya-outbox/dead-letters")
async def list_maya_dead_letters(
    limit: int = Query(default=50, ge=1, le=100),
    operations: AdminApplicationService = Depends(get_admin_operations),
):
    return present_admin_facts(operations.list_maya_dead_letters(limit=limit))


@router.post("/maya-outbox/{intent_id}/recover")
async def recover_maya_dead_letter(
    intent_id: str,
    operations: AdminApplicationService = Depends(get_admin_operations),
):
    try:
        return present_admin_facts(operations.recover_maya_dead_letter(intent_id))
    except AdminConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail="Maya delivery is not a recoverable dead letter",
        ) from exc


@router.post("/test-provider")
async def test_provider(
    req: ProviderTestRequest,
    request: Request,
    presentation: ProviderApplicationService = Depends(get_provider_presentation),
):
    try:
        if not req.live:
            return present_provider_facts(presentation.fixture_provider(req.provider))
        return present_provider_facts(
            await presentation.live_provider(
                provider=req.provider,
                query_text=req.query,
                caller=getattr(request.state, "caller_identity", "admin"),
                idempotency_key=req.idempotency_key,
                durable_receipt=req.durable_receipt,
            )
        )
    except UnknownProviderError as exc:
        raise HTTPException(
            status_code=400, detail=f"Unknown provider: {req.provider}"
        ) from exc
    except ProbeRejected as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/provider-spend")
async def provider_spend(
    presentation: ProviderApplicationService = Depends(get_provider_presentation),
):
    return present_provider_facts(presentation.provider_spend())


@router.get("/provider-spend/attempts")
async def provider_spend_attempts(
    status: str | None = None,
    provider: str | None = None,
    operations: AdminApplicationService = Depends(get_admin_operations),
):
    try:
        facts = operations.list_spend_attempts(status=status, provider=provider)
    except UnknownAdminProviderError as exc:
        raise HTTPException(status_code=400, detail="Unknown provider") from exc
    return present_admin_facts(facts)


@router.post("/provider-spend/attempts/{attempt_id}/resolve")
async def resolve_provider_spend(
    attempt_id: str,
    payload: SpendResolutionRequest,
    request: Request,
    operations: AdminApplicationService = Depends(get_admin_operations),
):
    try:
        facts = operations.resolve_provider_spend(
            attempt_id=attempt_id,
            payload=payload,
            caller_identity=getattr(request.state, "caller_identity", "admin"),
            reconciliation_token=request.headers.get("x-provider-reconciliation-key"),
        )
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Unknown provider attempt") from exc
    except AdminUnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AdminConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AdminInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return present_admin_facts(facts)


@router.post("/provider-spend/{provider}/snapshots")
async def record_provider_snapshot(
    provider: str,
    payload: ProviderSnapshotRequest,
    request: Request,
    operations: AdminApplicationService = Depends(get_admin_operations),
):
    try:
        facts = operations.record_provider_snapshot(
            provider=provider,
            payload=payload,
            reconciliation_token=request.headers.get("x-provider-reconciliation-key"),
        )
    except UnknownAdminProviderError as exc:
        raise HTTPException(status_code=400, detail="Unknown provider") from exc
    except AdminNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Unknown provider attempt") from exc
    except AdminUnauthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AdminConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AdminInvalidError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return present_admin_facts(facts)


@router.get("/paths", response_model=PathsResponse)
async def corpus_paths(workflows: WorkflowService = Depends(get_workflows)):
    return PathsResponse(**workflows.get_paths())
