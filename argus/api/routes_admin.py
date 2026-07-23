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
async def test_provider(
    req: ProviderTestRequest,
    request: Request,
    broker: SearchBroker = Depends(get_broker),
):
    try:
        pname = ProviderName(req.provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider}")

    query = SearchQuery(
        query=req.query,
        mode=SearchMode.DISCOVERY,
        max_results=3,
        providers=[pname],
        caller=getattr(request.state, "caller_identity", "admin"),
        metadata={"caller_label": "http-admin-smoke"},
    )
    response = await broker.search(query)
    trace = response.traces[0] if response.traces else None

    return {
        "provider": req.provider,
        "available": trace is not None,
        "status": trace.status if trace else "no_trace",
        "trace": {
            "status": trace.status if trace else "no_trace",
            "results_count": trace.results_count if trace else 0,
            "latency_ms": trace.latency_ms if trace else 0,
            "error": trace.error if trace else None,
        },
        "sample_results": [
            {"url": r.url, "title": r.title, "snippet": r.snippet[:100]}
            for r in response.results[:3]
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
        existing = repository.get_attempt(attempt_id)
        if payload.source == "provider":
            token = request.headers.get("x-provider-reconciliation-key")
            if not request.app.state.auth_config.matches_provider_reconciliation_token(
                existing.provider,
                token,
            ):
                raise HTTPException(
                    status_code=401,
                    detail="Valid provider reconciliation credential required",
                )
        attempt = repository.resolve(
            attempt_id,
            actual_charge=payload.actual_charge,
            outcome=payload.outcome,
            source=payload.source,
            actor_identity=(
                f"provider:{existing.provider}"
                if payload.source == "provider"
                else getattr(request.state, "caller_identity", "admin")
            ),
            idempotency_key=payload.idempotency_key,
            provider_snapshot_id=payload.provider_snapshot_id,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown provider attempt") from exc
    except Exception as exc:
        from argus.persistence.provider_spend import SpendConflictError

        if isinstance(exc, SpendConflictError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
    token = request.headers.get("x-provider-reconciliation-key")
    if not request.app.state.auth_config.matches_provider_reconciliation_token(
        provider_name.value,
        token,
    ):
        raise HTTPException(
            status_code=401,
            detail="Valid provider reconciliation credential required",
        )
    try:
        snapshot = repository.record_provider_snapshot(
            provider=provider_name,
            balance=payload.balance,
            observed_at=payload.observed_at,
            actor_identity=f"provider:{provider_name.value}",
            idempotency_key=payload.idempotency_key,
            provider_reference=payload.provider_reference,
            related_attempt_id=payload.related_attempt_id,
            authoritative_charge=payload.authoritative_charge,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown provider attempt") from exc
    except Exception as exc:
        from argus.persistence.provider_spend import SpendConflictError

        if isinstance(exc, SpendConflictError):
            raise HTTPException(
                status_code=409,
                detail="provider reference already used",
            ) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        raise
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
