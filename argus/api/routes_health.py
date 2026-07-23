"""Liveness, cached readiness, status, health, and budget endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from argus.broker.router import SearchBroker
from argus.models import ProviderName
from argus.operations.presentation import provider_display_state
from argus.operations.status import OperationalStatusService

router = APIRouter()


def get_broker(request: Request) -> SearchBroker:
    return request.app.state.get_broker()


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
async def capabilities():
    """Return value-free truth about the HTTP execution authority."""
    return {
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


@router.get("/provider-health")
async def provider_health(
    broker: SearchBroker = Depends(get_broker),
    operational: OperationalStatusService = Depends(get_operational_status),
):
    try:
        providers = {
            pname.value: broker.get_provider_status(pname)
            for pname in ProviderName
            if pname != ProviderName.CACHE
        }
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Execution authority state unavailable",
        ) from exc
    cached = operational.full_status().get("providers") or {}
    for provider, evidence in cached.items():
        if provider in providers:
            providers[provider] = {
                **providers[provider],
                "state": evidence.get("state", "unknown"),
                "observations": evidence.get("observations") or {},
            }
    active_states = [
        provider_display_state(status)
        for status in providers.values()
        if provider_display_state(status) != "disabled"
    ]
    healthy = any(state in {"healthy", "degraded"} for state in active_states)
    fully_healthy = healthy and all(state == "healthy" for state in active_states)
    return {
        "status": "ok" if fully_healthy else "degraded",
        "providers": providers,
    }


@router.get("/budgets")
async def caller_budgets(broker: SearchBroker = Depends(get_broker)):
    try:
        providers = {}
        for pname in ProviderName:
            if pname == ProviderName.CACHE:
                continue
            providers[pname.value] = broker.spend_repository.provider_summary(
                pname,
                budget_limit=broker.budget_tracker.get_budget_limit(pname),
            )
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail="Execution authority state unavailable",
        ) from exc
    return {"providers": providers}


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
async def health_detail(broker: SearchBroker = Depends(get_broker)):
    from argus.extraction.playwright_extractor import browser_capability_status
    from argus.recovery.evidence import recovery_status_from_environment

    provider_evidence = broker.operational_provider_evidence()
    providers = {
        name: dict(entry.get("status") or {})
        for name, entry in provider_evidence.items()
    }

    health_all = broker.health_tracker.get_all_status()

    for pname_str, entry in providers.items():
        try:
            r = (provider_evidence.get(pname_str) or {}).get("reachability")
            if r:
                entry["best_egress"] = r["best"]
                entry["egress_probes"] = r["probes"]
            else:
                entry["best_egress"] = "local"
                entry["egress_probes"] = {}
        except ValueError:
            pass

    return {
        "status": "ok",
        "providers": providers,
        "health_tracking": health_all,
        "runtime": {
            "browser": browser_capability_status(),
            "recovery": recovery_status_from_environment(),
        },
    }


@router.get("/admin/budgets")
async def budgets(broker: SearchBroker = Depends(get_broker)):
    budget_info = {}
    for pname in ProviderName:
        budget_info[pname.value] = {
            "remaining": broker.budget_tracker.get_remaining_budget(pname),
            "monthly_usage": broker.budget_tracker.get_monthly_usage(pname),
            "usage_count": broker.budget_tracker.get_usage_count(pname),
            "exhausted": broker.budget_tracker.is_budget_exhausted(pname),
        }

    # Token balances for extraction services (Jina, etc.)
    token_balances = {}
    store = broker.budget_tracker._store
    if store:
        token_balances = store.get_all_token_balances()

    return {"budgets": budget_info, "token_balances": token_balances}
