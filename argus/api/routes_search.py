"""
Search endpoints.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter

from argus.api.schemas import (
    ExpandRequest,
    RecoverUrlRequest,
    SearchRequest,
    SearchResponse,
    SearchResultSchema,
    ProviderTraceSchema,
)
from argus.broker.router import SearchBroker, create_broker
from argus.models import SearchMode, SearchQuery

router = APIRouter()

_broker: Optional[SearchBroker] = None


def get_broker() -> SearchBroker:
    global _broker
    if _broker is None:
        _broker = create_broker()
    return _broker


def _to_response(resp) -> SearchResponse:
    return SearchResponse(
        query=resp.query,
        mode=resp.mode.value,
        results=[
            SearchResultSchema(
                url=r.url,
                title=r.title,
                snippet=r.snippet,
                domain=r.domain,
                provider=r.provider.value if r.provider else None,
                score=r.score,
            )
            for r in resp.results
        ],
        traces=[
            ProviderTraceSchema(
                provider=t.provider.value,
                status=t.status,
                results_count=t.results_count,
                latency_ms=t.latency_ms,
                error=t.error,
                budget_remaining=t.budget_remaining,
            )
            for t in resp.traces
        ],
        total_results=resp.total_results,
        cached=resp.cached,
        search_run_id=resp.search_run_id,
    )


@router.post("/search", response_model=SearchResponse)
async def search(req: SearchRequest):
    broker = get_broker()

    query = SearchQuery(
        query=req.query,
        mode=SearchMode(req.mode),
        max_results=req.max_results,
    )

    if req.session_id:
        resp, session_id = await broker.search_with_session(query, session_id=req.session_id)
        response = _to_response(resp)
        response.session_id = session_id
        return response

    resp = await broker.search(query)
    return _to_response(resp)


@router.post("/recover-url", response_model=SearchResponse)
async def recover_url(req: RecoverUrlRequest):
    broker = get_broker()

    # Build recovery query from URL and optional hints
    query_parts = [req.url]
    if req.title:
        query_parts.append(req.title)
    if req.domain:
        query_parts.append(req.domain)

    search_query = SearchQuery(
        query=" ".join(query_parts),
        mode=SearchMode.RECOVERY,
        max_results=10,
    )

    resp = await broker.search(search_query)
    return _to_response(resp)


@router.post("/expand", response_model=SearchResponse)
async def expand(req: ExpandRequest):
    broker = get_broker()

    query_text = req.query
    if req.context:
        query_text = f"{req.query} {req.context}"

    search_query = SearchQuery(
        query=query_text,
        mode=SearchMode.DISCOVERY,
        max_results=15,
    )

    resp = await broker.search(search_query)
    return _to_response(resp)
