"""Argus egress worker — minimal provider executor over HTTP.

Exposes:
  POST /exec    — run a single provider search, return results + trace
  GET  /health  — liveness check

Binds to ARGUS_WORKER_BIND (default 0.0.0.0:8273).
Auth: Authorization: Bearer <ARGUS_EGRESS_SHARED_SECRET>
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from argus.models import ProviderName, SearchMode, SearchQuery
from argus.providers.base import BaseProvider


class ExecRequest(BaseModel):
    provider: str
    query: str
    max_results: int = 10
    mode: str = "discovery"
    caller: str = ""


def _get_provider(provider_name: str) -> BaseProvider:
    """Instantiate the requested provider. Raises KeyError if unknown."""
    from argus.config import get_config
    cfg = get_config()
    name = ProviderName(provider_name)  # raises ValueError if unknown

    if name == ProviderName.YAHOO:
        from argus.providers.yahoo import YahooProvider
        return YahooProvider(cfg.yahoo)
    if name == ProviderName.DUCKDUCKGO:
        from argus.providers.duckduckgo import DuckDuckGoProvider
        return DuckDuckGoProvider()
    if name == ProviderName.SEARXNG:
        from argus.providers.searxng import SearXNGProvider
        return SearXNGProvider(cfg.searxng)
    if name == ProviderName.GITHUB:
        from argus.providers.github import GitHubProvider
        return GitHubProvider(cfg.github)
    if name == ProviderName.WOLFRAM:
        from argus.providers.wolfram import WolframProvider
        return WolframProvider(cfg.wolfram)
    if name == ProviderName.BRAVE:
        from argus.providers.brave import BraveProvider
        return BraveProvider(cfg.brave)
    if name == ProviderName.TAVILY:
        from argus.providers.tavily import TavilyProvider
        return TavilyProvider(cfg.tavily)
    if name == ProviderName.EXA:
        from argus.providers.exa import ExaProvider
        return ExaProvider(cfg.exa)
    if name == ProviderName.SERPER:
        from argus.providers.serper import SerperProvider
        return SerperProvider(cfg.serper)
    raise ValueError(f"Provider {provider_name!r} not supported by worker")


def _check_auth(request: Request) -> None:
    secret = os.environ.get("ARGUS_EGRESS_SHARED_SECRET", "")
    if not secret:
        return  # no secret configured — open (dev mode)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def create_worker_app() -> FastAPI:
    app = FastAPI(title="Argus Worker", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "node": os.environ.get("ARGUS_MACHINE_NAME", "worker"),
        }

    @app.post("/exec")
    async def exec_provider(req: ExecRequest, request: Request):
        _check_auth(request)

        try:
            provider = _get_provider(req.provider)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider!r}")

        query = SearchQuery(
            query=req.query,
            mode=SearchMode(req.mode),
            max_results=req.max_results,
            caller=req.caller,
        )

        results, trace = await provider.search(query)

        return {
            "results": [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "domain": r.domain,
                    "provider": r.provider.value if r.provider else req.provider,
                    "score": r.score,
                    "raw_rank": r.raw_rank,
                    "metadata": r.metadata,
                }
                for r in results
            ],
            "trace": {
                "provider": trace.provider.value,
                "status": trace.status,
                "results_count": trace.results_count,
                "latency_ms": trace.latency_ms,
                "error": trace.error,
            },
        }

    return app