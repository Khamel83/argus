"""Typed HTTP application operations for repository-backed admin routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from argus.models import ProviderName
from argus.persistence.provider_spend import SpendConflictError


class AdminNotFoundError(LookupError):
    """The requested durable admin record does not exist."""


class AdminConflictError(RuntimeError):
    """The requested admin mutation conflicts with durable state."""


class AdminUnauthorizedError(PermissionError):
    """The provider reconciliation credential is invalid."""


class AdminInvalidError(ValueError):
    """The requested admin mutation is invalid."""


class UnknownAdminProviderError(ValueError):
    """Only canonical provider-name parsing failed."""


@dataclass(frozen=True)
class OutboxStatusFacts:
    values: Mapping[str, Any]


@dataclass(frozen=True)
class DeadLettersFacts:
    items: tuple[Any, ...]


@dataclass(frozen=True)
class RecoveryFacts:
    status: str


@dataclass(frozen=True)
class SpendAttemptsFacts:
    attempts: tuple[Any, ...]


@dataclass(frozen=True)
class ResolvedSpendFacts:
    attempt_id: str
    provider: str
    status: str
    outcome: str | None
    actual_charge: float | None


@dataclass(frozen=True)
class ProviderSnapshotFacts:
    snapshot_id: str
    provider: str
    balance: float
    observed_at: datetime


class AdminApplicationService:
    """Coordinate repositories and authorization outside route adapters."""

    def __init__(
        self,
        search_repository_provider: Callable[[], Any],
        spend_repository_provider: Callable[[], Any],
        auth_config_provider: Callable[[], Any],
    ) -> None:
        self._search_repository_provider = search_repository_provider
        self._spend_repository_provider = spend_repository_provider
        self._auth_config_provider = auth_config_provider

    def maya_outbox_status(self) -> OutboxStatusFacts:
        return OutboxStatusFacts(
            dict(self._search_repository_provider().maya_outbox_status())
        )

    def list_maya_dead_letters(self, *, limit: int) -> DeadLettersFacts:
        return DeadLettersFacts(
            tuple(
                self._search_repository_provider().list_maya_dead_letters(limit=limit)
            )
        )

    def recover_maya_dead_letter(self, intent_id: str) -> RecoveryFacts:
        if not self._search_repository_provider().recover_maya_dead_letter(intent_id):
            raise AdminConflictError("Maya delivery is not a recoverable dead letter")
        return RecoveryFacts("pending")

    @staticmethod
    def _provider_name(provider: str) -> ProviderName:
        try:
            return ProviderName(provider)
        except ValueError as exc:
            raise UnknownAdminProviderError(provider) from exc

    def list_spend_attempts(
        self,
        *,
        status: str | None,
        provider: str | None,
    ) -> SpendAttemptsFacts:
        provider_name = self._provider_name(provider) if provider is not None else None
        return SpendAttemptsFacts(
            tuple(
                self._spend_repository_provider().list_attempts(
                    status=status,
                    provider=provider_name,
                )
            )
        )

    def resolve_provider_spend(
        self,
        *,
        attempt_id: str,
        payload: Any,
        caller_identity: str,
        reconciliation_token: str | None,
    ) -> ResolvedSpendFacts:
        repository = self._spend_repository_provider()
        try:
            existing = repository.get_attempt(attempt_id)
        except KeyError as exc:
            raise AdminNotFoundError("Unknown provider attempt") from exc
        if (
            payload.source == "provider"
            and not self._auth_config_provider().matches_provider_reconciliation_token(
                existing.provider,
                reconciliation_token,
            )
        ):
            raise AdminUnauthorizedError(
                "Valid provider reconciliation credential required"
            )
        try:
            attempt = repository.resolve(
                attempt_id,
                actual_charge=payload.actual_charge,
                outcome=payload.outcome,
                source=payload.source,
                actor_identity=(
                    f"provider:{existing.provider}"
                    if payload.source == "provider"
                    else caller_identity
                ),
                idempotency_key=payload.idempotency_key,
                provider_snapshot_id=payload.provider_snapshot_id,
            )
        except KeyError as exc:
            raise AdminNotFoundError("Unknown provider attempt") from exc
        except SpendConflictError as exc:
            raise AdminConflictError(str(exc)) from exc
        except ValueError as exc:
            raise AdminInvalidError(str(exc)) from exc
        return ResolvedSpendFacts(
            attempt_id=attempt.attempt_id,
            provider=attempt.provider,
            status=attempt.status,
            outcome=attempt.outcome,
            actual_charge=attempt.actual_charge,
        )

    def record_provider_snapshot(
        self,
        *,
        provider: str,
        payload: Any,
        reconciliation_token: str | None,
    ) -> ProviderSnapshotFacts:
        provider_name = self._provider_name(provider)
        if not self._auth_config_provider().matches_provider_reconciliation_token(
            provider_name.value,
            reconciliation_token,
        ):
            raise AdminUnauthorizedError(
                "Valid provider reconciliation credential required"
            )
        try:
            snapshot = self._spend_repository_provider().record_provider_snapshot(
                provider=provider_name,
                balance=payload.balance,
                observed_at=payload.observed_at,
                actor_identity=f"provider:{provider_name.value}",
                idempotency_key=payload.idempotency_key,
                provider_reference=payload.provider_reference,
                related_attempt_id=payload.related_attempt_id,
                authoritative_charge=payload.authoritative_charge,
            )
        except KeyError as exc:
            raise AdminNotFoundError("Unknown provider attempt") from exc
        except SpendConflictError as exc:
            raise AdminConflictError("provider reference already used") from exc
        except ValueError as exc:
            raise AdminInvalidError(str(exc)) from exc
        return ProviderSnapshotFacts(
            snapshot_id=snapshot.snapshot_id,
            provider=snapshot.provider,
            balance=snapshot.balance,
            observed_at=snapshot.observed_at,
        )
