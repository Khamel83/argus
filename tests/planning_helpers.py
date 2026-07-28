"""Test helpers for direct provider-executor calls."""

from datetime import datetime, timezone
from typing import Any, Sequence

from argus.broker.execution import caller_tier_cap
from argus.broker.planning import (
    ExecutionPolicySnapshot,
    RetrievalControls,
    resolve_plan,
)
from argus.models import ProviderName, SearchQuery

_UTC_NOW = datetime(2026, 7, 27, tzinfo=timezone.utc)


def execution_context(
    query: SearchQuery,
    *,
    effective_max_provider_tier: int = 3,
) -> dict:
    """Resolve a valid plan and deadline pair for a direct executor test."""
    plan = resolve_plan(
        query,
        RetrievalControls(),
        False,
        ExecutionPolicySnapshot(
            effective_max_provider_tier=effective_max_provider_tier
        ),
        _UTC_NOW,
    )
    return {
        "plan": plan,
        "operation_deadline": 130.0,
        "provider_phase_deadline": 125.0,
    }


async def execute_with_plan(
    executor: Any,
    query: SearchQuery,
    provider_order: Sequence[ProviderName],
):
    """Invoke a ProviderExecutor with the policy cap it owns."""
    cap = caller_tier_cap(
        query.caller,
        getattr(executor, "_caller_tier_caps", {}),
    )
    return await executor.execute(
        query,
        provider_order,
        **execution_context(
            query,
            effective_max_provider_tier=3 if cap is None else cap,
        ),
    )
