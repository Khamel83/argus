"""Liveness, cached readiness, status, health, and budget endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from argus.operations.provider_presentation import ProviderPresentationService
from argus.operations.status import OperationalStatusService

router = APIRouter()


def get_provider_presentation(request: Request) -> ProviderPresentationService:
    return request.app.state.provider_presentation


def get_operational_status(request: Request) -> OperationalStatusService:
    return request.app.state.operational_status


@router.get("/live")
async def live():
    """Network-free process/event-loop liveness; never checks dependencies."""
    return {"status": "alive"}


@router.get("/startup")
async def startup(status: OperationalStatusService = Depends(get_operational_status)):
    """Public minimal cached initialization state."""
    return status.startup_status()


@router.get("/ready")
async def ready(status: OperationalStatusService = Depends(get_operational_status)):
    """Public minimal cached readiness; no live probes run in this request."""
    payload = status.readiness_status()
    if not payload["ready"]:
        return JSONResponse(status_code=503, content=payload)
    return payload


@router.get("/admin/status")
async def operator_status(
    status: OperationalStatusService = Depends(get_operational_status),
):
    """Authenticated detailed status from the HTTP execution authority."""
    return status.full_status()


@router.get("/capabilities")
async def capabilities(request: Request):
    """Return frozen release support, never MCP-process liveness."""
    payload = {
        "schema_version": "1.0",
        "execution_authority": "http-api",
        "role": "primary",
        "capabilities": {
            "search": True,
            "extraction": True,
            "recovery": True,
            "expansion": True,
            "provider_health": True,
            "budgets": True,
            "workflows": True,
        },
    }
    release_contract_support = request.app.state.capability_manifest.as_dict()
    payload.update(release_contract_support)
    return payload


@router.get("/provider-health")
async def provider_health(
    presentation: ProviderPresentationService = Depends(get_provider_presentation),
    operational: OperationalStatusService = Depends(get_operational_status),
):
    try:
        return presentation.provider_health(operational)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Execution authority state unavailable",
        ) from exc


@router.get("/budgets")
async def caller_budgets(
    presentation: ProviderPresentationService = Depends(get_provider_presentation),
):
    try:
        return presentation.caller_budgets()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Execution authority state unavailable",
        ) from exc


@router.get("/health")
async def health():
    """Compatibility liveness surface.

    Dependency health is intentionally excluded so legacy container checks
    cannot turn an external outage into a restart storm. New integrations
    should use ``/live``, ``/startup``, ``/ready``, and ``/admin/status``.
    """
    from argus import __version__

    return {
        "status": "ok",
        "version": __version__,
        "semantics": "liveness_compatibility",
    }


@router.get("/admin/health/detail")
async def health_detail(
    presentation: ProviderPresentationService = Depends(get_provider_presentation),
):
    return presentation.health_detail()


@router.get("/admin/budgets")
async def budgets(
    presentation: ProviderPresentationService = Depends(get_provider_presentation),
):
    return presentation.admin_budgets()
