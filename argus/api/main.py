"""
FastAPI application for Argus search broker.
"""

import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from argus.api.routes_admin import router as admin_router
from argus.api.routes_health import router as health_router
from argus.api.routes_search import router as search_router
from argus.logging import get_logger

logger = get_logger("api")

app = FastAPI(
    title="Argus",
    description="Standalone search broker service",
    version="1.0.0",
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
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("x-request-id", uuid.uuid4().hex[:16])
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response
