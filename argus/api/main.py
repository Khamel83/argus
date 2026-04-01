"""
FastAPI application for Argus search broker.
"""

import os
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from argus.api.rate_limit import RateLimiter
from argus.api.routes_admin import router as admin_router
from argus.api.routes_health import router as health_router
from argus.api.routes_search import router as search_router
from argus.logging import get_logger

logger = get_logger("api")

rate_limiter = RateLimiter(
    max_requests=int(os.environ.get("ARGUS_RATE_LIMIT", "60")),
    window_seconds=int(os.environ.get("ARGUS_RATE_LIMIT_WINDOW", "60")),
    api_key=os.environ.get("ARGUS_API_KEY", ""),
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    # Shutdown: close budget store if initialized
    try:
        from argus.api.routes_search import get_broker
        get_broker().budget_tracker.close()
    except Exception:
        pass


app = FastAPI(
    title="Argus",
    description="Standalone search broker service",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(search_router, prefix="/api")
app.include_router(health_router, prefix="/api")
app.include_router(admin_router, prefix="/api")


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    api_key_header = request.headers.get("x-api-key")

    allowed, headers = rate_limiter.is_allowed(
        client_ip, request.url.path, api_key_header
    )

    if not allowed:
        return JSONResponse(
            status_code=429,
            content={"error": "Rate limit exceeded", "retry_after": headers.get("Retry-After")},
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
