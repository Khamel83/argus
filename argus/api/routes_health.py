"""Health and budget endpoints."""

from fastapi import APIRouter, Depends, Request, Response

from argus.broker.router import SearchBroker
from argus.models import ProviderName

router = APIRouter()


def get_broker(request: Request) -> SearchBroker:
    return request.app.state.get_broker()


@router.get("/health")
async def health(broker: SearchBroker = Depends(get_broker)):
    from argus import __version__

    all_providers = {}
    for pname in ProviderName:
        status = broker.get_provider_status(pname)
        all_providers[pname.value] = status

    healthy = any(
        s["effective_status"] in ("enabled", "healthy")
        for s in all_providers.values()
    )

    return {
        "status": "ok" if healthy else "degraded",
        "version": __version__,
    }


@router.get("/admin/health/detail")
async def health_detail(broker: SearchBroker = Depends(get_broker)):
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
