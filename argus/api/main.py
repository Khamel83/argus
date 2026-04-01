"""FastAPI application for Argus search broker."""

import os
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from argus.api.rate_limit import RateLimiter
from argus.api.routes_admin import router as admin_router
from argus.api.routes_extract import router as extract_router
from argus.api.routes_health import router as health_router
from argus.api.routes_search import router as search_router
from argus.broker.router import SearchBroker, create_broker
from argus.logging import get_logger

logger = get_logger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    try:
        broker = app.state.get_broker()
        broker.budget_tracker.close()
    except Exception:
        pass


def _build_rate_limiter() -> RateLimiter:
    return RateLimiter(
        max_requests=int(os.environ.get("ARGUS_RATE_LIMIT", "60")),
        window_seconds=int(os.environ.get("ARGUS_RATE_LIMIT_WINDOW", "60")),
        api_key=os.environ.get("ARGUS_API_KEY", ""),
    )


def _build_broker_provider(
    broker: Optional[SearchBroker],
    broker_factory: Optional[Callable[[], SearchBroker]],
) -> Callable[[], SearchBroker]:
    current = broker
    factory = broker_factory or create_broker

    def get_broker() -> SearchBroker:
        nonlocal current
        if current is None:
            current = factory()
        return current

    return get_broker


def create_app(
    *,
    broker: Optional[SearchBroker] = None,
    broker_factory: Optional[Callable[[], SearchBroker]] = None,
    rate_limiter: Optional[RateLimiter] = None,
) -> FastAPI:
    app = FastAPI(
        title="Argus",
        description="Standalone search broker service",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.get_broker = _build_broker_provider(broker, broker_factory)
    app.state.rate_limiter = rate_limiter or _build_rate_limiter()
    app.state.api_key = os.environ.get("ARGUS_API_KEY", "")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def api_key_auth(request: Request, call_next):
        api_key = request.app.state.api_key
        if not api_key:
            return await call_next(request)

        if request.url.path == "/api/health":
            return await call_next(request)

        provided = request.headers.get("x-api-key")
        if provided != api_key:
            return JSONResponse(status_code=401, content={"error": "Invalid or missing API key"})

        return await call_next(request)

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        api_key_header = request.headers.get("x-api-key")
        allowed, headers = request.app.state.rate_limiter.is_allowed(
            client_ip, request.url.path, api_key_header
        )

        if not allowed:
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Rate limit exceeded",
                    "retry_after": headers.get("Retry-After"),
                },
                headers=headers,
            )

        response = await call_next(request)
        for k, v in headers.items():
            response.headers[k] = v
        return response

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:16])
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    app.include_router(search_router, prefix="/api")
    app.include_router(health_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(extract_router, prefix="/api")
    return app


app = create_app()
