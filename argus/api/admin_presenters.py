"""Pure HTTP presentation for typed admin application facts."""

from __future__ import annotations

from typing import Any

from argus.api.admin_operations import (
    DeadLettersFacts,
    OutboxStatusFacts,
    ProviderSnapshotFacts,
    RecoveryFacts,
    ResolvedSpendFacts,
    SpendAttemptsFacts,
)


def present_admin_facts(facts: Any) -> Any:
    if isinstance(facts, OutboxStatusFacts):
        return dict(facts.values)
    if isinstance(facts, DeadLettersFacts):
        return {"items": list(facts.items)}
    if isinstance(facts, RecoveryFacts):
        return {"status": facts.status}
    if isinstance(facts, SpendAttemptsFacts):
        return {
            "attempts": [
                {
                    "attempt_id": attempt.attempt_id,
                    "provider": attempt.provider,
                    "is_paid": attempt.is_paid,
                    "status": attempt.status,
                    "outcome": attempt.outcome,
                    "reserved_charge": attempt.reserved_charge,
                    "estimator_violation": attempt.estimator_violation,
                    "reservation_overrun": attempt.reservation_overrun,
                    "actual_charge": attempt.actual_charge,
                    "usage": attempt.usage,
                    "caller_identity": attempt.caller_identity,
                    "caller_label": attempt.caller_label,
                    "resolution_source": attempt.resolution_source,
                    "created_at": attempt.created_at,
                }
                for attempt in facts.attempts
            ]
        }
    if isinstance(facts, ResolvedSpendFacts):
        return {
            "attempt_id": facts.attempt_id,
            "provider": facts.provider,
            "status": facts.status,
            "outcome": facts.outcome,
            "actual_charge": facts.actual_charge,
        }
    if isinstance(facts, ProviderSnapshotFacts):
        return {
            "snapshot_id": facts.snapshot_id,
            "provider": facts.provider,
            "balance": facts.balance,
            "source": "provider",
            "observed_at": facts.observed_at,
        }
    raise TypeError(f"unsupported admin facts: {type(facts).__name__}")
