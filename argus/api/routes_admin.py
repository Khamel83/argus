"""Admin endpoints for provider testing and runtime control."""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from argus.api.schemas import ProviderTestRequest
from argus.broker.router import SearchBroker
from argus.models import ProviderName, SearchMode, SearchQuery

router = APIRouter()


class DisableRequest(BaseModel):
    reason: str = ""


def get_broker(request: Request) -> SearchBroker:
    return request.app.state.get_broker()


def _resolve_provider(name: str) -> ProviderName:
    try:
        return ProviderName(name)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {name}")


@router.post("/admin/providers/{name}/disable")
async def disable_provider(
    name: str,
    req: DisableRequest,
    broker: SearchBroker = Depends(get_broker),
):
    pname = _resolve_provider(name)
    broker.health_tracker.force_disable(pname, req.reason)
    store = broker.budget_tracker._store
    if store:
        store.set_provider_override(pname.value, disabled=True, reason=req.reason)
    return {"provider": name, "status": "manually_disabled", "reason": req.reason}


@router.post("/admin/providers/{name}/enable")
async def enable_provider(name: str, broker: SearchBroker = Depends(get_broker)):
    pname = _resolve_provider(name)
    broker.health_tracker.force_enable(pname)
    store = broker.budget_tracker._store
    if store:
        store.set_provider_override(pname.value, disabled=False)
    return {"provider": name, "status": "enabled"}


@router.post("/admin/providers/{name}/reset-health")
async def reset_provider_health(name: str, broker: SearchBroker = Depends(get_broker)):
    pname = _resolve_provider(name)
    broker.health_tracker.reset_cooldown(pname)
    return {"provider": name, "status": "health_reset"}


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
