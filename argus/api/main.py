"""FastAPI application for Argus search broker."""

import asyncio
import math
import os
import threading
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Callable, Optional

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
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
from argus.operations.status import (
    OperationalStatusService,
    create_operational_status,
    safe_correlation_id,
)
from argus.persistence.search_ledger import (
    SearchLedgerRepository,
    create_search_ledger_repository,
)
from argus.workflows import WorkflowService

logger = get_logger("api")
_HTTP_API_AUTHORITY_CAPABILITY = object()
_AUTHORITY_RETRY_SECONDS = 5.0


def _unavailable_browser_status() -> dict[str, object]:
    return {
        "declared": False,
        "available": False,
        "loaded": False,
        "degraded_reason": "browser_status_unavailable",
    }


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
    initialization_lock = threading.Lock()
    if broker_factory is None:

        def factory():
            return create_broker(authority_capability=_HTTP_API_AUTHORITY_CAPABILITY)
    else:
        factory = broker_factory

    def get_broker() -> SearchBroker:
        nonlocal current
        if current is None:
            with initialization_lock:
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
            current = WorkflowService(
                broker_provider(),
                authority_capability=_HTTP_API_AUTHORITY_CAPABILITY,
            )
        return current

    return get_workflows


def create_app(
    *,
    broker: Optional[SearchBroker] = None,
    broker_factory: Optional[Callable[[], SearchBroker]] = None,
    rate_limiter: Optional[RateLimiter] = None,
    search_repository: Optional[SearchLedgerRepository] = None,
    spend_repository=None,
    operational_status: OperationalStatusService | None = None,
) -> FastAPI:
    auth_config = AuthConfig.from_env()
    production_mode = (
        os.environ.get("ARGUS_ENV", "development").strip().lower() == "production"
    )

    @asynccontextmanager
    async def lifespan_with_probes(app: FastAPI):
        async def _run_authority_workers() -> None:
            """Initialize dependencies off-loop and maintain cached observations."""
            b: SearchBroker | None = None
            repository: SearchLedgerRepository | None = None
            child_tasks: list[asyncio.Task] = []
            try:
                while b is None:
                    try:
                        b = await asyncio.to_thread(app.state.get_broker)
                    except Exception as exc:
                        app.state.operational_status.mark_initialization_failed(
                            source="startup",
                            reason=(
                                f"broker_initialization_failed:{type(exc).__name__}"
                            ),
                        )
                        logger.warning(
                            "Execution authority initialization failed: %s",
                            type(exc).__name__,
                        )
                        await asyncio.sleep(_AUTHORITY_RETRY_SECONDS)

                while repository is None:
                    try:
                        repository = await asyncio.to_thread(
                            app.state.get_search_repository
                        )
                    except Exception as exc:
                        app.state.operational_status.mark_initialization_failed(
                            source="startup",
                            reason=(
                                f"repository_initialization_failed:{type(exc).__name__}"
                            ),
                        )
                        logger.warning(
                            "Persistence authority initialization failed: %s",
                            type(exc).__name__,
                        )
                        await asyncio.sleep(_AUTHORITY_RETRY_SECONDS)

                from argus.config import get_config
                from argus.operations.status import refresh_operational_status
                from argus.recovery.evidence import recovery_status_from_environment

                browser_status_reader = None
                try:
                    from argus.extraction.playwright_extractor import (
                        browser_capability_status,
                    )

                    browser_status_reader = browser_capability_status
                    browser_status = await asyncio.to_thread(browser_status_reader)
                except Exception:
                    browser_status = _unavailable_browser_status()
                try:
                    recovery_status = await asyncio.to_thread(
                        recovery_status_from_environment
                    )
                except Exception:
                    recovery_status = {
                        "state": "unavailable",
                        "schema_promotion_allowed": False,
                        "reasons": ["recovery_evidence_unavailable"],
                    }

                await asyncio.to_thread(
                    refresh_operational_status,
                    app.state.operational_status,
                    broker=b,
                    repository=repository,
                    browser_status=browser_status,
                    recovery_status=recovery_status,
                )

                async def _refresh_status_background() -> None:
                    while True:
                        await asyncio.sleep(15)
                        try:
                            current_recovery = await asyncio.to_thread(
                                recovery_status_from_environment
                            )
                            current_browser = (
                                await asyncio.to_thread(browser_status_reader)
                                if browser_status_reader is not None
                                else _unavailable_browser_status()
                            )
                            await asyncio.to_thread(
                                refresh_operational_status,
                                app.state.operational_status,
                                broker=b,
                                repository=repository,
                                browser_status=current_browser,
                                recovery_status=current_recovery,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Operational status refresh failed: %s",
                                type(exc).__name__,
                            )

                async def _run_probes_background() -> None:
                    """Run network probes every 30 minutes."""
                    while True:
                        try:
                            await asyncio.to_thread(
                                lambda: asyncio.run(b.refresh_provider_evidence())
                            )
                        except Exception as exc:
                            logger.warning(
                                "Reachability probe failed: %s",
                                type(exc).__name__,
                            )
                        await asyncio.sleep(30 * 60)

                async def _run_maya_outbox_background() -> None:
                    from argus.persistence.maya_outbox import (
                        MayaOutboxDispatcher,
                    )

                    cfg = get_config().maya_capture
                    dispatcher = MayaOutboxDispatcher(
                        repository,
                        endpoint=cfg.endpoint,
                        token=cfg.token,
                        timeout_seconds=cfg.timeout_seconds,
                        batch_size=cfg.batch_size,
                    )
                    while True:
                        try:
                            outcomes = await asyncio.to_thread(dispatcher.run_once)
                            app.state.operational_status.observe_maya_delivery(
                                outcomes,
                                ttl=timedelta(seconds=max(15, cfg.poll_seconds * 3)),
                            )
                        except Exception as exc:
                            logger.warning(
                                "Maya outbox worker failed: %s",
                                type(exc).__name__,
                            )
                            app.state.operational_status.observe_dependency(
                                "maya",
                                state="degraded",
                                source="maya_dispatcher",
                                ttl=timedelta(seconds=max(15, cfg.poll_seconds * 3)),
                                reason=f"delivery_failed:{type(exc).__name__}",
                            )
                        else:
                            try:
                                now = datetime.now(tz=None)
                                await asyncio.to_thread(
                                    repository.compact_maya_outbox,
                                    acknowledged_before=now
                                    - timedelta(days=cfg.acknowledged_retention_days),
                                    limit=100,
                                    now=now,
                                )
                            except Exception as exc:
                                logger.warning(
                                    "Maya outbox compaction failed: %s",
                                    type(exc).__name__,
                                )
                        await asyncio.sleep(cfg.poll_seconds)

                child_tasks.extend(
                    [
                        asyncio.create_task(_refresh_status_background()),
                        asyncio.create_task(_run_probes_background()),
                    ]
                )
                maya_cfg = get_config().maya_capture
                if repository is not None and maya_cfg.endpoint and maya_cfg.token:
                    app.state.operational_status.observe_maya_configuration(
                        configured=True,
                        ttl=timedelta(seconds=max(15, maya_cfg.poll_seconds * 3)),
                    )
                    child_tasks.append(
                        asyncio.create_task(_run_maya_outbox_background())
                    )
                else:
                    app.state.operational_status.observe_maya_configuration(
                        configured=False,
                        ttl=timedelta(days=1),
                    )

                await asyncio.gather(*child_tasks)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                app.state.operational_status.mark_initialization_failed(
                    source="startup",
                    reason=f"authority_worker_failed:{type(exc).__name__}",
                )
                logger.warning(
                    "Execution authority worker failed: %s",
                    type(exc).__name__,
                )
            finally:
                for task in child_tasks:
                    task.cancel()
                for task in child_tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                try:
                    from argus.extraction.playwright_extractor import close_browser

                    await close_browser()
                except Exception as exc:
                    logger.warning(
                        "Failed to close Playwright resources: %s",
                        type(exc).__name__,
                    )
                if b is not None:
                    try:
                        await asyncio.to_thread(b.budget_tracker.close)
                    except Exception as exc:
                        logger.warning(
                            "Failed to close broker budget tracker: %s",
                            type(exc).__name__,
                        )

        authority_task = asyncio.create_task(_run_authority_workers())
        yield
        authority_task.cancel()
        try:
            await authority_task
        except asyncio.CancelledError:
            pass

    app = FastAPI(
        title="Argus",
        description="Retrieval platform for AI agents",
        version="1.6.2",
        lifespan=lifespan_with_probes,
    )
    app.state.operational_status = operational_status or create_operational_status()

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        def json_safe(value):
            if isinstance(value, float) and not math.isfinite(value):
                return str(value)
            if isinstance(value, BaseException):
                return str(value)
            if isinstance(value, dict):
                return {key: json_safe(item) for key, item in value.items()}
            if isinstance(value, (list, tuple)):
                return [json_safe(item) for item in value]
            return value

        return JSONResponse(
            status_code=422,
            content={"detail": json_safe(exc.errors())},
        )

    # Broker singleton
    app.state.get_broker = _build_broker_provider(broker, broker_factory)
    ledger_repository = search_repository
    ledger_initialization_lock = threading.Lock()

    def get_search_repository() -> SearchLedgerRepository:
        nonlocal ledger_repository
        if ledger_repository is None:
            with ledger_initialization_lock:
                if ledger_repository is None:
                    ledger_repository = create_search_ledger_repository()
        return ledger_repository

    app.state.get_search_repository = get_search_repository
    current_spend_repository = spend_repository

    def get_spend_repository():
        nonlocal current_spend_repository
        if current_spend_repository is None:
            current_spend_repository = getattr(
                app.state.get_broker(), "_spend_repository", None
            )
        if current_spend_repository is None:
            from argus.persistence.provider_spend import (
                create_provider_spend_repository,
            )

            current_spend_repository = create_provider_spend_repository()
        return current_spend_repository

    app.state.get_spend_repository = get_spend_repository
    app.state.get_workflows = _build_workflow_provider(app.state.get_broker)
    app.state.rate_limiter = rate_limiter or _build_rate_limiter()
    app.state.auth_config = auth_config

    if auth_config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(auth_config.cors_origins),
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "X-API-Key",
                "X-Admin-API-Key",
                "X-Provider-Reconciliation-Key",
                "X-Request-Id",
            ],
        )

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
        request.state.caller_identity = auth.identity_for_token(token) or (
            "local" if is_local else ""
        )

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
            request.state.caller_identity = "admin"
            return await call_next(request)

        if is_caller_path(path) and (production_mode or not is_local):
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

    @app.middleware("http")
    async def operational_evidence_middleware(request: Request, call_next):
        """Attach safe correlation and record only bounded route-template metrics."""
        if request.url.path in {"/api/live", "/api/health"}:
            request_id = safe_correlation_id(request.headers.get("x-request-id"))
            request.state.request_id = request_id
            response = await call_next(request)
            response.headers["x-request-id"] = request_id
            return response
        request_id = safe_correlation_id(request.headers.get("x-request-id"))
        request.state.request_id = request_id
        started = request.app.state.operational_status.metrics.request_started()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-request-id"] = request_id
            deployment_id = request.app.state.operational_status.deployment.get(
                "deployment_id"
            )
            if deployment_id and deployment_id != "unknown":
                response.headers["x-argus-deployment-id"] = deployment_id
            return response
        finally:
            route = request.scope.get("route")
            route_template = getattr(route, "path", "unmatched")
            request.app.state.operational_status.metrics.request_finished(
                started=started,
                route=route_template,
                method=request.method,
                status_code=status_code,
            )
            logger.info(
                "http_request request_id=%s deployment_id=%s "
                "route=%s method=%s status_class=%s",
                request_id,
                request.app.state.operational_status.deployment.get(
                    "deployment_id", "unknown"
                ),
                route_template,
                request.method,
                f"{max(1, min(status_code // 100, 5))}xx",
            )

    app.include_router(search_router, prefix="/api")
    app.include_router(health_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")
    app.include_router(extract_router, prefix="/api")
    app.include_router(workflows_router, prefix="/api")
    app.include_router(dashboard_router)
    app.state.operational_status.metrics.register_route_templates(
        [
            route.path
            for route in app.routes
            if isinstance(getattr(route, "path", None), str)
        ]
    )
    return app


app = create_app()
