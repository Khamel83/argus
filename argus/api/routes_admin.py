"""
Admin endpoints for provider testing.
"""

from typing import Optional

from fastapi import APIRouter, HTTPException

from argus.api.schemas import ProviderTestRequest
from argus.broker.router import SearchBroker, create_broker
from argus.models import ProviderName, SearchMode, SearchQuery

router = APIRouter()

_broker: Optional[SearchBroker] = None


def get_broker() -> SearchBroker:
    global _broker
    if _broker is None:
        _broker = create_broker()
    return _broker


@router.post("/test-provider")
async def test_provider(req: ProviderTestRequest):
    broker = get_broker()

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
