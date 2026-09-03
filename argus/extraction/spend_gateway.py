"""Extraction spend adapter over the canonical search readiness authority."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from argus.broker.budgets import PROVIDER_TIERS
from argus.broker.readiness import ExecutionContext, ProviderReadinessService
from argus.contracts import FailureCode, FailureRecord
from argus.models import ProviderName


MAX_EXTRACTION_EXECUTION_SECONDS = 60


@dataclass(frozen=True, slots=True)
class ExtractionOperationContext:
    """Bounded identity and policy values carried by every extraction call."""

    operation_id: str
    request_id: str
    plan_id: str
    request_hash: str
    caller_identity: str
    caller_label: str = ""
    release_identity: str = "unknown-release"
    free_only: bool = False
    egress: str = "local"
    request_class: str = "discovery"
    caller_tier_cap: int | None = None
    plan_providers: tuple[ProviderName, ...] = ()

    def __post_init__(self) -> None:
        for name in (
            "operation_id",
            "request_id",
            "plan_id",
            "request_hash",
            "caller_identity",
            "release_identity",
            "egress",
            "request_class",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value or len(value) > 255:
                raise ValueError(f"{name} must be a bounded non-empty string")
        if not isinstance(self.caller_label, str) or len(self.caller_label) > 100:
            raise ValueError("caller_label must be bounded text")
        if type(self.free_only) is not bool:
            raise ValueError("free_only must be a boolean")
        if self.caller_tier_cap is not None and (
            isinstance(self.caller_tier_cap, bool)
            or not isinstance(self.caller_tier_cap, int)
            or self.caller_tier_cap < 0
        ):
            raise ValueError("caller_tier_cap must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class SpendReservation:
    """Immutable reservation result.  ``failure`` is typed, never raw SQL."""

    context: ExtractionOperationContext
    provider: ProviderName
    account_fingerprint: str
    reserved_charge: float
    deadline: float | None
    status: str
    attempt_id: str | None = None
    authorization: Any | None = None
    failure: FailureRecord | None = None

    @property
    def allowed(self) -> bool:
        return self.status in {"reserved", "free"}


@dataclass(frozen=True, slots=True)
class SpendSettlement:
    reservation: SpendReservation
    status: str
    charge: float | None
    provider_reference: str | None = None
    failure: FailureRecord | None = None


def _failure(
    code: FailureCode,
    context: ExtractionOperationContext,
    reason: str,
) -> FailureRecord:
    return FailureRecord(
        code=code,
        safe_reason=reason,
        request_id=context.request_id,
        operation_id=context.operation_id,
        release_identity=context.release_identity,
    )


class ExtractionSpendGateway:
    """Authorize paid extraction through the same durable readiness authority."""

    def __init__(
        self,
        readiness_service: ProviderReadinessService | None = None,
        *,
        readiness: ProviderReadinessService | None = None,
        monotonic=time.monotonic,
    ) -> None:
        self.readiness = readiness_service or readiness
        if self.readiness is None:
            raise TypeError("readiness_service is required")
        self._monotonic = monotonic

    def reserve(
        self,
        context: ExtractionOperationContext,
        provider: ProviderName,
        account_fingerprint: str,
        estimate: float | None,
        deadline: float | None,
    ) -> SpendReservation:
        """Return a durable reservation or a typed denial before dispatch."""

        provider = ProviderName(provider)
        if not isinstance(account_fingerprint, str) or not account_fingerprint:
            return SpendReservation(
                context,
                provider,
                "missing-account",
                0.0,
                deadline,
                "denied",
                failure=_failure(
                    FailureCode.SPEND_DENIED,
                    context,
                    "provider account binding is unavailable",
                ),
            )
        tier = PROVIDER_TIERS.get(provider, 99)
        if context.free_only and tier > 0:
            return SpendReservation(
                context,
                provider,
                account_fingerprint,
                0.0,
                deadline,
                "denied",
                failure=_failure(
                    FailureCode.SPEND_DENIED,
                    context,
                    "free-only policy denied paid extraction",
                ),
            )
        if tier == 0:
            return SpendReservation(
                context,
                provider,
                account_fingerprint,
                0.0,
                deadline,
                "free",
            )
        if (
            estimate is None
            or isinstance(estimate, bool)
            or not isinstance(estimate, (int, float))
            or not math.isfinite(float(estimate))
            or float(estimate) <= 0
        ):
            return SpendReservation(
                context,
                provider,
                account_fingerprint,
                0.0,
                deadline,
                "denied",
                failure=_failure(
                    FailureCode.SPEND_DENIED,
                    context,
                    "paid extraction requires a positive known estimate",
                ),
            )
        if deadline is None or not math.isfinite(float(deadline)):
            return SpendReservation(
                context,
                provider,
                account_fingerprint,
                float(estimate),
                deadline,
                "denied",
                failure=_failure(
                    FailureCode.SPEND_DENIED,
                    context,
                    "paid extraction requires a bounded execution deadline",
                ),
            )
        remaining = float(deadline) - self._monotonic()
        if remaining <= 0:
            return SpendReservation(
                context,
                provider,
                account_fingerprint,
                float(estimate),
                deadline,
                "denied",
                failure=_failure(
                    FailureCode.SPEND_DENIED,
                    context,
                    "paid extraction deadline has expired",
                ),
            )
        timeout = min(MAX_EXTRACTION_EXECUTION_SECONDS, max(1, math.ceil(remaining)))
        scope = self.readiness.execution_scope(
            provider,
            egress=context.egress,
            request_class=context.request_class,
        )
        from dataclasses import replace

        scope = replace(scope, account_fingerprint=account_fingerprint)
        execution = ExecutionContext(
            provider=provider,
            tier=tier,
            plan_providers=context.plan_providers or (provider,),
            free_only=False,
            caller_tier_cap=context.caller_tier_cap,
            scope=scope,
            plan_id=context.plan_id,
            caller_identity=context.caller_identity,
            caller_label=context.caller_label,
            idempotency_key=f"{context.operation_id}:{provider.value}",
            egress=context.egress,
            request_class=context.request_class,
            release_revision=context.release_identity,
            operation_id=context.operation_id,
            request_hash=context.request_hash,
            release_identity=context.release_identity,
        )
        try:
            authorization = self.readiness.authorize_execution(
                execution,
                owner=f"extraction:{context.operation_id}",
                conservative_charge=float(estimate),
                execution_timeout_seconds=timeout,
            )
        except Exception:
            return SpendReservation(
                context,
                provider,
                account_fingerprint,
                float(estimate),
                deadline,
                "denied",
                failure=_failure(
                    FailureCode.PROVIDER_UNREADY,
                    context,
                    "spend authority is unavailable",
                ),
            )
        if not authorization.allowed:
            code = (
                FailureCode.PROVIDER_UNREADY
                if authorization.decision.code in {"unavailable", "cooldown"}
                else FailureCode.SPEND_DENIED
            )
            return SpendReservation(
                context,
                provider,
                account_fingerprint,
                float(estimate),
                deadline,
                "denied",
                failure=_failure(code, context, "provider execution was not authorized"),
            )
        return SpendReservation(
            context,
            provider,
            account_fingerprint,
            float(estimate),
            deadline,
            "reserved",
            attempt_id=authorization.attempt_id,
            authorization=authorization,
        )

    def settle(
        self,
        reservation: SpendReservation,
        outcome: str,
        charge: float | None,
        provider_reference: str,
        operation_hash: str,
    ) -> SpendSettlement:
        """Settle one reservation; an identity mismatch never mutates state."""

        if not reservation.allowed:
            return SpendSettlement(
                reservation,
                "denied",
                charge,
                provider_reference,
                reservation.failure,
            )
        if reservation.status == "free":
            return SpendSettlement(reservation, "settled", 0.0, provider_reference)
        if operation_hash != reservation.context.request_hash:
            raise ValueError("operation hash does not match the reservation")
        if not isinstance(provider_reference, str) or not provider_reference:
            raise ValueError("provider reference is required for settlement")
        charge_known = (
            charge is not None
            and not isinstance(charge, bool)
            and isinstance(charge, (int, float))
            and math.isfinite(float(charge))
            and float(charge) >= 0
        )
        if not charge_known:
            return self.mark_uncertain(reservation, "provider charge is unresolved")
        if reservation.authorization is None:
            raise ValueError("paid settlement requires durable authorization")
        failure = None
        if outcome != "success":
            failure = _failure(
                FailureCode.PROVIDERS_FAILED,
                reservation.context,
                "provider returned a non-success outcome",
            )
        self.readiness.complete_execution(
            reservation.authorization,
            failure=failure,
            actual_charge=float(charge),
            charge_known=True,
            evidence_ref=provider_reference,
        )
        return SpendSettlement(
            reservation,
            "settled",
            float(charge),
            provider_reference,
            failure,
        )

    def mark_uncertain(
        self,
        reservation: SpendReservation,
        cause: str,
    ) -> SpendSettlement:
        """Keep provider/account blocked when charge or termination is unknown."""

        if not reservation.allowed or reservation.status == "free":
            return SpendSettlement(reservation, "denied", 0.0, failure=reservation.failure)
        if reservation.authorization is None:
            raise ValueError("uncertainty requires durable authorization")
        failure = _failure(
            FailureCode.CHARGE_UNCERTAIN,
            reservation.context,
            cause if isinstance(cause, str) and cause else "provider effect is unresolved",
        )
        self.readiness.complete_execution(
            reservation.authorization,
            failure=failure,
            actual_charge=None,
            charge_known=False,
            termination_known=False,
            evidence_ref=f"uncertain:{reservation.attempt_id}",
        )
        return SpendSettlement(
            reservation,
            "uncertain",
            None,
            f"uncertain:{reservation.attempt_id}",
            failure,
        )

    def sweep_stale(self, now: float, limit: int) -> tuple[SpendReservation, ...]:
        """Return a bounded list of local reservations whose deadlines elapsed.

        The authority marks these reservations through ``mark_uncertain``.  A
        caller can invoke this method from a scheduler without creating a new
        spend store or retrying any provider call.
        """

        if limit <= 0:
            return ()
        repository = self.readiness.repository
        list_attempts = getattr(repository, "list_stale_execution_attempts", None)
        if not callable(list_attempts):
            return ()
        return tuple(list_attempts(now=now, limit=min(limit, 128)))


__all__ = [
    "ExtractionOperationContext",
    "ExtractionSpendGateway",
    "MAX_EXTRACTION_EXECUTION_SECONDS",
    "SpendReservation",
    "SpendSettlement",
]
