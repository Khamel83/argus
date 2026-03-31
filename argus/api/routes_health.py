"""
Health and budget endpoints.
"""

from typing import Optional

from fastapi import APIRouter, Response

from argus.broker.router import SearchBroker, create_broker
from argus.models import ProviderName

router = APIRouter()

_broker: Optional[SearchBroker] = None


def get_broker() -> SearchBroker:
    global _broker
    if _broker is None:
        _broker = create_broker()
    return _broker


@router.get("/health")
async def health():
    broker = get_broker()

    all_providers = {}
    for pname in ProviderName:
        status = broker.get_provider_status(pname)
        all_providers[pname.value] = status

    healthy = any(
        s["effective_status"] in ("enabled", "healthy")
        for s in all_providers.values()
    )

    return Response(
        content='{"status": "ok"}' if healthy else '{"status": "degraded"}',
        media_type="application/json",
        status_code=200,
    )


@router.get("/health/detail")
async def health_detail():
    broker = get_broker()

    providers = {}
    for pname in ProviderName:
        providers[pname.value] = broker.get_provider_status(pname)

    health_all = broker.health_tracker.get_all_status()

    return {
        "status": "ok",
        "providers": providers,
        "health_tracking": health_all,
    }


@router.get("/budgets")
async def budgets():
    broker = get_broker()

    budget_info = {}
    for pname in ProviderName:
        budget_info[pname.value] = {
            "remaining": broker.budget_tracker.get_remaining_budget(pname),
            "monthly_usage": broker.budget_tracker.get_monthly_usage(pname),
            "usage_count": broker.budget_tracker.get_usage_count(pname),
            "exhausted": broker.budget_tracker.is_budget_exhausted(pname),
        }

    return {"budgets": budget_info}
