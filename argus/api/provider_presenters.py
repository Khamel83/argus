"""Pure HTTP presentation of typed provider application facts."""

from __future__ import annotations

from typing import Any

from argus.api.provider_operations import (
    AdminBudgetsFacts,
    BudgetStateFacts,
    CallerBudgetsFacts,
    FixtureProviderFacts,
    HealthDetailFacts,
    LiveProviderFacts,
    ProviderHealthFacts,
    ProviderSpendFacts,
)


def present_provider_facts(facts: Any) -> Any:
    """Return a detached JSON-compatible projection for FastAPI."""
    if isinstance(facts, FixtureProviderFacts):
        return {
            "provider": facts.provider,
            "mode": "fixture",
            "available": facts.available,
            "status": facts.status,
            "readiness": dict(facts.readiness),
            "sample_results": [],
        }
    if isinstance(facts, LiveProviderFacts):
        return {
            "provider": facts.provider,
            "mode": "live",
            "available": facts.available,
            "status": facts.status,
            "trace": dict(facts.trace),
            "sample_results": [dict(row) for row in facts.sample_results],
        }
    if isinstance(facts, ProviderSpendFacts):
        return {
            "providers": [dict(row) for row in facts.providers],
            "non_authoritative_operational": {
                "providers": [dict(row) for row in facts.operational]
            },
        }
    if isinstance(facts, BudgetStateFacts):
        return [dict(row) for row in facts.rows]
    if isinstance(facts, ProviderHealthFacts):
        return {"status": facts.status, "providers": dict(facts.providers)}
    if isinstance(facts, CallerBudgetsFacts):
        return {"providers": dict(facts.providers)}
    if isinstance(facts, HealthDetailFacts):
        return {
            "status": "ok",
            "providers": dict(facts.providers),
            "health_tracking": dict(facts.health_tracking),
            "runtime": {
                "browser": dict(facts.browser),
                "recovery": dict(facts.recovery),
            },
        }
    if isinstance(facts, AdminBudgetsFacts):
        return {
            "budgets": dict(facts.budgets),
            "non_authoritative_operational": {
                "token_balances": dict(facts.token_balances)
            },
        }
    raise TypeError(f"unsupported provider facts: {type(facts).__name__}")
