"""FastAPI application for Argus search broker."""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from argus.auth import (
    AuthConfig,
    extract_api_token,
    is_admin_path,
    is_caller_path,
    is_local_client,
    is_public_path,
)
from argus.api.rate_limit import RateLimiter
from argus.api.routes_admin import router as admin_router
from argus.api.routes_dashboard import router as dashboard_router
from argus.api.routes_extract import router as extract_router
from argus.api.routes_health import router as health_router
from argus.api.routes_search import router as search_router
from argus.api.routes_workflows import router as workflows_router
from argus.broker.router import SearchBroker, create_broker
from argus.logging import get_logger
from argus.persistence.search_ledger import (
    SearchLedgerRepository,
    create_search_ledger_repository,
)
from argus.workflows import WorkflowService

logger = get_logger("api")


def _build_rate_limiter() -> RateLimiter:
    auth = AuthConfig.from_env()
    return RateLimiter(
        max_requests=int(os.environ.get("ARGUS_RATE_LIMIT", "60")),
        window_seconds=int(os.environ.get("ARGUS_RATE_LIMIT_WINDOW", "60")),
        exempt_tokens=[auth.caller_api_key, auth.admin_api_key],
    )


def _build_broker_provider(
    broker: Optional[SearchBroker],
    broker_factory: Optional[Callable[[], SearchBroker]] = None,
) -> Callable[[], SearchBroker]:
    current = broker
    factory = broker_factory or create_broker

    def get_broker() -> SearchBroker:
        nonlocal current
        if current is None:
            current = factory()
        return current

    return get_broker


def _build_workflow_provider(
    broker_provider: Callable[[], SearchBroker],
) -> Callable[[], WorkflowService]:
    current: WorkflowService | None = None

    def get_workflows() -> WorkflowService:
        nonlocal current
        if current is None:
            current = WorkflowService(broker_provider())
        return current

    return get_workflows


def create_app(
    *,
    broker: Optional[SearchBroker] = None,
    broker_factory: Optional[Callable[[], SearchBroker]] = None,
    rate_limiter: Optional[RateLimiter] = None,
    search_repository: Optional[SearchLedgerRepository] = None,
) -> FastAPI:
    auth_config = AuthConfig.from_env()

    @asynccontextmanager
    async def lifespan_with_probes(app: FastAPI):
        # Initialize broker in lifespan startup (not lazily on first request)
        b = app.state.get_broker()

        # Background probe task — runs immediately on startup, then every 30 min
        probe_task: asyncio.Task | None = None

        async def _run_probes_background() -> None:
            """Run probes every 30 min, starting immediately."""
            from argus.config import get_config
            cfg = get_config()
            while True:
                try:
                    await b._reachability.probe_all(
                        local_providers=b._providers,
                        egress_nodes=list(cfg.egress_nodes),
                    )
                except Exception as exc:
                    logger.warning("Reachability probe failed: %s", exc)
                await asyncio.sleep(30 * 60)

        probe_task = asyncio.create_task(_run_probes_background())

        yield

        # Cancel the probe task on shutdown
        if probe_task:
            probe_task.cancel()
            try:
                await probe_task
            except asyncio.CancelledError:
                pass

        try:
            from argus.extraction.playwright_extractor import close_browser
            await close_browser()
        except Exception as exc:
            logger.warning("Failed to close Playwright resources: %s", exc)

        try:
            b.budget_tracker.close()
        except Exception as exc:
            logger.warning("Failed to close broker budget tracker: %s", exc)

    app = FastAPI(
        title="Argus",
        description="Retrieval platform for AI agents",
        version="1.6.2",
        lifespan=lifespan_with_probes,
    )

    # Broker singleton
    app.state.get_broker = _build_broker_provider(broker, broker_factory)
    ledger_repository = search_repository

    def get_search_repository() -> SearchLedgerRepository:
        nonlocal ledger_repository
        if ledger_repository is None:
            ledger_repository = create_search_ledger_repository()
        return ledger_repository

    app.state.get_search_repository = get_search_repository
    app.state.get_workflows = _build_workflow_provider(app.state.get_broker)
    app.state.rate_limiter = rate_limiter or _build_rate_limiter()
    app.state.auth_config = auth_config

    if auth_config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(auth_config.cors_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["Authorization", "Content-Type", "X-API-Key", "X-Admin-API-Key", "X-Request-Id"],
        )

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:16])
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        path = request.url.path
        client_ip = request.client.host if request.client else None
        is_local = is_local_client(client_ip)
        token = extract_api_token(
            request.headers,
            "x-api-key",
            "x-admin-api-key",
        )
        auth = request.app.state.auth_config

        if is_public_path(path):
            return await call_next(request)

        if is_admin_path(path):
            if not auth.has_admin_key():
                return JSONResponse(
                    status_code=503,
                    content={"error": "Admin API key is not configured"},
                )
            if not auth.matches_admin_token(token):
                return JSONResponse(
                    status_code=401,
                    content={"error": "Admin authentication required"},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return await call_next(request)

        if is_caller_path(path) and not is_local:
            if not auth.has_caller_key():
                return JSONResponse(
                    status_code=503,
                    content={"error": "API key is not configured for remote access"},
                )
            if not auth.matches_caller_token(token):
                return JSONResponse(
                    status_code=401,
                    content={"error": "Authentication required"},
                    headers={"WWW-Authenticate": "Bearer"},
                )

        return await call_next(request)

    @app.middleware("http")
    async def rate_limit_middleware(request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        token = extract_api_token(request.headers, "x-api-key", "x-admin-api-key")
        allowed, headers = request.app.state.rate_limiter.is_allowed(
            client_ip, request.url.path, token
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

    app.include_router(search_router, prefix="/api")
    app.include_router(health_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(extract_router, prefix="/api")
    app.include_router(workflows_router, prefix="/api")
    app.include_router(dashboard_router)
    return app


app = create_app()
