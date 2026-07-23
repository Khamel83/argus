"""Admin endpoints for privileged operations."""

from fastapi import APIRouter, Depends, HTTPException, Request

from argus.api.schemas import (
    PathsResponse,
    ProviderSnapshotRequest,
    ProviderTestRequest,
    SpendResolutionRequest,
)
from argus.broker.router import SearchBroker
from argus.models import ProviderName, SearchMode, SearchQuery
from argus.workflows import WorkflowService

router = APIRouter(prefix="/admin")


def get_broker(request: Request) -> SearchBroker:
    return request.app.state.get_broker()


def get_workflows(request: Request) -> WorkflowService:
    return request.app.state.get_workflows()


def get_spend_repository(request: Request):
    return request.app.state.get_spend_repository()


@router.post("/test-provider")
async def test_provider(req: ProviderTestRequest, broker: SearchBroker = Depends(get_broker)):
    try:
        pname = ProviderName(req.provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    provider = broker._providers.get(pname)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"Provider not registered: {req.provider}")

    query = SearchQuery(query=req.query, mode=SearchMode.DISCOVERY, max_results=3)
    results, trace = await provider.search(query)

    return {
        "provider": req.provider,
        "available": provider.is_available(),
        "status": provider.status().value,
        "trace": {
            "status": trace.status,
            "results_count": trace.results_count,
            "latency_ms": trace.latency_ms,
            "error": trace.error,
        },
        "sample_results": [
            {"url": r.url, "title": r.title, "snippet": r.snippet[:100]}
            for r in results[:3]
        ],
    }


@router.get("/provider-spend")
async def provider_spend(
    broker: SearchBroker = Depends(get_broker),
    repository=Depends(get_spend_repository),
):
    providers = []
    for provider in ProviderName:
        if provider == ProviderName.CACHE:
            continue
        providers.append(
            repository.provider_summary(
                provider,
                budget_limit=broker.budget_tracker.get_budget_limit(provider),
            )
        )
    return {"providers": providers}


@router.get("/provider-spend/attempts")
async def provider_spend_attempts(
    status: str | None = None,
    provider: str | None = None,
    repository=Depends(get_spend_repository),
):
    provider_name = None
    if provider is not None:
        try:
            provider_name = ProviderName(provider)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Unknown provider") from exc
    attempts = repository.list_attempts(status=status, provider=provider_name)
    return {
        "attempts": [
            {
                "attempt_id": attempt.attempt_id,
                "provider": attempt.provider,
                "is_paid": attempt.is_paid,
                "status": attempt.status,
                "outcome": attempt.outcome,
                "reserved_charge": attempt.reserved_charge,
                "actual_charge": attempt.actual_charge,
                "usage": attempt.usage,
                "caller_identity": attempt.caller_identity,
                "caller_label": attempt.caller_label,
                "resolution_source": attempt.resolution_source,
                "created_at": attempt.created_at,
            }
            for attempt in attempts
        ]
    }


@router.post("/provider-spend/attempts/{attempt_id}/resolve")
async def resolve_provider_spend(
    attempt_id: str,
    payload: SpendResolutionRequest,
    request: Request,
    repository=Depends(get_spend_repository),
):
    try:
        attempt = repository.resolve(
            attempt_id,
            actual_charge=payload.actual_charge,
            outcome=payload.outcome,
            source=payload.source,
            actor_identity=getattr(request.state, "caller_identity", "admin"),
            idempotency_key=payload.idempotency_key,
            provider_snapshot_id=payload.provider_snapshot_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown provider attempt") from exc
    except Exception as exc:
        from argus.persistence.provider_spend import SpendConflictError

        if isinstance(exc, SpendConflictError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        raise
    return {
        "attempt_id": attempt.attempt_id,
        "provider": attempt.provider,
        "status": attempt.status,
        "outcome": attempt.outcome,
        "actual_charge": attempt.actual_charge,
    }


@router.post("/provider-spend/{provider}/snapshots")
async def record_provider_snapshot(
    provider: str,
    payload: ProviderSnapshotRequest,
    request: Request,
    repository=Depends(get_spend_repository),
):
    try:
        provider_name = ProviderName(provider)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Unknown provider") from exc
    snapshot = repository.record_provider_snapshot(
        provider=provider_name,
        balance=payload.balance,
        observed_at=payload.observed_at,
        actor_identity=getattr(request.state, "caller_identity", "admin"),
        idempotency_key=payload.idempotency_key,
    )
    return {
        "snapshot_id": snapshot.snapshot_id,
        "provider": snapshot.provider,
        "balance": snapshot.balance,
        "source": "provider",
        "observed_at": snapshot.observed_at,
    }


@router.get("/paths", response_model=PathsResponse)
async def corpus_paths(workflows: WorkflowService = Depends(get_workflows)):
    return PathsResponse(**workflows.get_paths())
