"""Health and budget endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Request

from argus.broker.router import SearchBroker
from argus.models import ProviderName

router = APIRouter()


def get_broker(request: Request) -> SearchBroker:
    return request.app.state.get_broker()


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
async def provider_health(broker: SearchBroker = Depends(get_broker)):
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
    healthy = any(
        status["effective_status"] in ("enabled", "healthy")
        for status in providers.values()
    )
    return {
        "status": "ok" if healthy else "degraded",
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
async def health(broker: SearchBroker = Depends(get_broker)):
    from argus import __version__

    all_providers = {}
    for pname in ProviderName:
        status = broker.get_provider_status(pname)
        all_providers[pname.value] = status

    healthy = any(
        s["effective_status"] in ("enabled", "healthy") for s in all_providers.values()
    )

    return {
        "status": "ok" if healthy else "degraded",
        "version": __version__,
    }


@router.get("/admin/health/detail")
async def health_detail(broker: SearchBroker = Depends(get_broker)):
    from argus.extraction.playwright_extractor import browser_capability_status
    from argus.recovery.evidence import recovery_status_from_environment

    providers = {}
    for pname in ProviderName:
        providers[pname.value] = broker.get_provider_status(pname)

    health_all = broker.health_tracker.get_all_status()

    reachability = broker._reachability.get_all()
    for pname_str, entry in providers.items():
        try:
            pname = ProviderName(pname_str)
            r = reachability.get(pname)
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
