"""Durable provider spend reservations, settlement, and reconciliation."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from argus.broker.budgets import PROVIDER_TIERS
from argus.config import get_config
from argus.models import ProviderName


class SpendBase(DeclarativeBase):
    pass


class ProviderSpendAttemptRow(SpendBase):
    __tablename__ = "provider_spend_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    is_paid: Mapped[bool] = mapped_column(Boolean, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(100), nullable=True)
    reserved_charge: Mapped[float] = mapped_column(Float, nullable=False)
    actual_charge: Mapped[float | None] = mapped_column(Float, nullable=True)
    usage: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    caller_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    caller_label: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    resolution_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    resolution_reference: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProviderBalanceSnapshotRow(SpendBase):
    __tablename__ = "provider_balance_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "provider_reference",
            name="uq_provider_snapshot_reference",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    balance: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    actor_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    provider_reference: Mapped[str] = mapped_column(String(255), nullable=False)
    related_attempt_id: Mapped[str] = mapped_column(String(32), nullable=False)
    authoritative_charge: Mapped[float] = mapped_column(Float, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SpendAuditRow(SpendBase):
    __tablename__ = "provider_spend_audit"
    __table_args__ = (
        UniqueConstraint("action", "idempotency_key", name="uq_spend_audit_action_key"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    attempt_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    actor_identity: Mapped[str] = mapped_column(String(100), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    before_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    after_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


@dataclass(frozen=True)
class SpendAttempt:
    attempt_id: str
    provider: str
    is_paid: bool
    status: str
    outcome: str | None
    reserved_charge: float
    actual_charge: float | None
    usage: float
    caller_identity: str
    caller_label: str
    resolution_source: str | None
    created_at: datetime


@dataclass(frozen=True)
class ProviderSnapshot:
    snapshot_id: str
    provider: str
    balance: float
    observed_at: datetime
    provider_reference: str
    related_attempt_id: str
    authoritative_charge: float


class BudgetExhaustedError(RuntimeError):
    pass


class SpendConflictError(RuntimeError):
    pass


def _canonical(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: dict) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _naive_utc(value: datetime) -> datetime:
    """Normalize API timestamps for storage and arithmetic."""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _attempt(row: ProviderSpendAttemptRow) -> SpendAttempt:
    return SpendAttempt(
        attempt_id=row.id,
        provider=row.provider,
        is_paid=row.is_paid,
        status=row.status,
        outcome=row.outcome,
        reserved_charge=row.reserved_charge,
        actual_charge=row.actual_charge,
        usage=row.usage,
        caller_identity=row.caller_identity,
        caller_label=row.caller_label,
        resolution_source=row.resolution_source,
        created_at=row.created_at,
    )


def _attempt_state(row: ProviderSpendAttemptRow) -> dict:
    return {
        "status": row.status,
        "outcome": row.outcome,
        "reserved_charge": row.reserved_charge,
        "actual_charge": row.actual_charge,
        "usage": row.usage,
        "resolution_source": row.resolution_source,
        "resolution_reference": row.resolution_reference,
    }


class ProviderSpendRepository:
    """SQLAlchemy repository; every public mutation is its own transaction."""

    def __init__(self, factory: sessionmaker):
        self.session_factory = factory

    def reserve(
        self,
        *,
        provider: ProviderName,
        conservative_charge: float,
        budget_limit: float,
        caller_identity: str,
        caller_label: str,
        idempotency_key: str,
        created_at: datetime | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> SpendAttempt:
        if PROVIDER_TIERS.get(provider, 0) <= 0:
            raise ValueError("free providers do not create monetary reservations")
        if conservative_charge < 0:
            raise ValueError("conservative charge must be non-negative")
        payload = {
            "provider": provider.value,
            "conservative_charge": conservative_charge,
            "budget_limit": budget_limit,
            "caller_identity": caller_identity,
            "caller_label": caller_label,
            "idempotency_key": idempotency_key,
        }
        request_hash = _fingerprint(payload)
        now = created_at or datetime.now(tz=None)
        try:
            with self._transaction() as session:
                existing = session.scalar(
                    select(ProviderSpendAttemptRow).where(
                        ProviderSpendAttemptRow.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    self._verify_hash(existing.request_hash, request_hash)
                    return _attempt(existing)

                obligation = self._obligation(
                    session,
                    provider,
                    lock=True,
                )
                if budget_limit > 0 and obligation + conservative_charge > budget_limit:
                    raise BudgetExhaustedError(f"{provider.value} budget exhausted")

                row = ProviderSpendAttemptRow(
                    id=uuid.uuid4().hex,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    provider=provider.value,
                    tier=PROVIDER_TIERS[provider],
                    is_paid=True,
                    status="uncertain",
                    outcome=None,
                    reserved_charge=conservative_charge,
                    actual_charge=None,
                    usage=0.0,
                    caller_identity=caller_identity,
                    caller_label=caller_label,
                    resolution_source=None,
                    resolution_reference=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                self._audit(
                    session,
                    row=row,
                    action="reserve",
                    actor=caller_identity,
                    key=idempotency_key,
                    request_hash=request_hash,
                    before=None,
                )
                if fault_hook:
                    fault_hook("after_reservation_write")
                result = _attempt(row)
            return result
        except IntegrityError:
            return self._load_idempotent_attempt(idempotency_key, request_hash)

    def settle(
        self,
        attempt_id: str,
        *,
        actual_charge: float,
        outcome: str,
        fault_hook: Callable[[str], None] | None = None,
    ) -> SpendAttempt:
        if actual_charge < 0:
            raise ValueError("actual charge must be non-negative")
        payload = {
            "attempt_id": attempt_id,
            "actual_charge": actual_charge,
            "outcome": outcome,
            "source": "argus",
        }
        request_hash = _fingerprint(payload)
        with self._transaction() as session:
            row = self._locked_attempt(session, attempt_id)
            if row.status == "settled":
                self._verify_settlement(row, actual_charge, outcome, "argus")
                return _attempt(row)
            if row.status != "uncertain":
                raise SpendConflictError(f"attempt {attempt_id!r} is already resolved")
            before = _attempt_state(row)
            row.status = "settled"
            row.outcome = outcome
            row.actual_charge = actual_charge
            row.usage = 1.0
            row.resolution_source = "argus"
            row.resolution_reference = None
            row.updated_at = datetime.now(tz=None)
            self._audit(
                session,
                row=row,
                action="settle",
                actor=row.caller_identity,
                key=attempt_id,
                request_hash=request_hash,
                before=before,
            )
            if fault_hook:
                fault_hook("after_settlement_update")
            result = _attempt(row)
        return result

    def resolve(
        self,
        attempt_id: str,
        *,
        actual_charge: float,
        outcome: str,
        source: str,
        actor_identity: str,
        idempotency_key: str,
        provider_snapshot_id: str | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> SpendAttempt:
        if source not in {"operator", "provider"}:
            raise ValueError("resolution source must be operator or provider")
        if actual_charge < 0:
            raise ValueError("actual charge must be non-negative")
        payload = {
            "attempt_id": attempt_id,
            "actual_charge": actual_charge,
            "outcome": outcome,
            "source": source,
            "actor_identity": actor_identity,
            "idempotency_key": idempotency_key,
            "provider_snapshot_id": provider_snapshot_id,
        }
        request_hash = _fingerprint(payload)
        with self._transaction() as session:
            row = self._locked_attempt(session, attempt_id)
            prior = session.scalar(
                select(SpendAuditRow).where(
                    SpendAuditRow.action == "resolve",
                    SpendAuditRow.idempotency_key == idempotency_key,
                )
            )
            if prior is not None:
                self._verify_hash(prior.request_hash, request_hash)
                return _attempt(row)

            if row.status != "uncertain":
                raise SpendConflictError(f"attempt {attempt_id!r} is not uncertain")
            if source == "provider":
                snapshot = (
                    session.get(ProviderBalanceSnapshotRow, provider_snapshot_id)
                    if provider_snapshot_id
                    else None
                )
                if snapshot is None or snapshot.provider != row.provider:
                    raise ValueError(
                        "provider reconciliation requires a matching provider snapshot"
                    )
                if snapshot.related_attempt_id != row.id:
                    raise ValueError(
                        "provider reconciliation requires evidence linked to this attempt"
                    )
                if not math.isclose(
                    snapshot.authoritative_charge,
                    actual_charge,
                    rel_tol=0.0,
                    abs_tol=1e-9,
                ):
                    raise ValueError(
                        "provider reconciliation charge must match authoritative evidence"
                    )
                if datetime.now(tz=None) - snapshot.observed_at > timedelta(minutes=15):
                    raise ValueError(
                        "provider reconciliation requires fresh provider evidence"
                    )
            before = _attempt_state(row)
            row.status = "resolved"
            row.outcome = outcome
            row.actual_charge = actual_charge
            row.usage = 1.0
            row.resolution_source = source
            row.resolution_reference = provider_snapshot_id
            row.updated_at = datetime.now(tz=None)
            self._audit(
                session,
                row=row,
                action="resolve",
                actor=actor_identity,
                key=idempotency_key,
                request_hash=request_hash,
                before=before,
            )
            if fault_hook:
                fault_hook("after_reconciliation_update")
            result = _attempt(row)
        return result

    def record_provider_snapshot(
        self,
        *,
        provider: ProviderName,
        balance: float,
        observed_at: datetime,
        actor_identity: str,
        idempotency_key: str,
        provider_reference: str,
        related_attempt_id: str,
        authoritative_charge: float,
    ) -> ProviderSnapshot:
        now = datetime.now(tz=None)
        observed_at = _naive_utc(observed_at)
        if actor_identity != f"provider:{provider.value}":
            raise ValueError("provider snapshot requires provider-scoped identity")
        if not provider_reference.strip():
            raise ValueError("provider reference is required")
        if authoritative_charge < 0:
            raise ValueError("authoritative charge must be non-negative")
        payload = {
            "provider": provider.value,
            "balance": balance,
            "observed_at": observed_at,
            "actor_identity": actor_identity,
            "idempotency_key": idempotency_key,
            "provider_reference": provider_reference,
            "related_attempt_id": related_attempt_id,
            "authoritative_charge": authoritative_charge,
        }
        request_hash = _fingerprint(payload)
        try:
            with self._transaction() as session:
                existing = session.scalar(
                    select(ProviderBalanceSnapshotRow).where(
                        ProviderBalanceSnapshotRow.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    self._verify_hash(existing.request_hash, request_hash)
                    return ProviderSnapshot(
                        existing.id,
                        existing.provider,
                        existing.balance,
                        existing.observed_at,
                        existing.provider_reference,
                        existing.related_attempt_id,
                        existing.authoritative_charge,
                    )
                age = now - observed_at
                if age > timedelta(minutes=15) or age < -timedelta(minutes=1):
                    raise ValueError("provider snapshot requires fresh evidence")
                attempt = self._locked_attempt(session, related_attempt_id)
                if attempt.provider != provider.value:
                    raise ValueError(
                        "provider snapshot attempt must match the provider"
                    )
                if attempt.status != "uncertain":
                    raise ValueError(
                        "provider snapshot attempt must still be uncertain"
                    )
                if authoritative_charge > attempt.reserved_charge:
                    raise ValueError(
                        "authoritative charge exceeds the reserved charge"
                    )
                row = ProviderBalanceSnapshotRow(
                    id=uuid.uuid4().hex,
                    provider=provider.value,
                    balance=balance,
                    source="provider",
                    observed_at=observed_at,
                    actor_identity=actor_identity,
                    provider_reference=provider_reference,
                    related_attempt_id=related_attempt_id,
                    authoritative_charge=authoritative_charge,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    created_at=now,
                )
                session.add(row)
                session.flush()
                self._audit_snapshot(
                    session,
                    row=row,
                    actor=actor_identity,
                    request_hash=request_hash,
                )
                result = ProviderSnapshot(
                    row.id,
                    row.provider,
                    row.balance,
                    row.observed_at,
                    row.provider_reference,
                    row.related_attempt_id,
                    row.authoritative_charge,
                )
            return result
        except IntegrityError:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(ProviderBalanceSnapshotRow).where(
                        ProviderBalanceSnapshotRow.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    self._verify_hash(existing.request_hash, request_hash)
                    return ProviderSnapshot(
                        existing.id,
                        existing.provider,
                        existing.balance,
                        existing.observed_at,
                        existing.provider_reference,
                        existing.related_attempt_id,
                        existing.authoritative_charge,
                    )
                replayed = session.scalar(
                    select(ProviderBalanceSnapshotRow).where(
                        ProviderBalanceSnapshotRow.provider == provider.value,
                        ProviderBalanceSnapshotRow.provider_reference
                        == provider_reference,
                    )
                )
                if replayed is not None:
                    raise SpendConflictError(
                        "provider reference already used for another obligation"
                    )
                raise RuntimeError("snapshot integrity conflict was not visible")

    def record_free_attempt(
        self,
        *,
        provider: ProviderName,
        outcome: str,
        usage: float,
        caller_identity: str,
        caller_label: str,
        idempotency_key: str,
    ) -> SpendAttempt:
        payload = {
            "provider": provider.value,
            "outcome": outcome,
            "usage": usage,
            "caller_identity": caller_identity,
            "caller_label": caller_label,
            "idempotency_key": idempotency_key,
        }
        request_hash = _fingerprint(payload)
        now = datetime.now(tz=None)
        try:
            with self._transaction() as session:
                existing = session.scalar(
                    select(ProviderSpendAttemptRow).where(
                        ProviderSpendAttemptRow.idempotency_key == idempotency_key
                    )
                )
                if existing is not None:
                    self._verify_hash(existing.request_hash, request_hash)
                    return _attempt(existing)
                row = ProviderSpendAttemptRow(
                    id=uuid.uuid4().hex,
                    idempotency_key=idempotency_key,
                    request_hash=request_hash,
                    provider=provider.value,
                    tier=PROVIDER_TIERS.get(provider, 0),
                    is_paid=False,
                    status="settled",
                    outcome=outcome,
                    reserved_charge=0.0,
                    actual_charge=0.0,
                    usage=usage,
                    caller_identity=caller_identity,
                    caller_label=caller_label,
                    resolution_source="argus",
                    resolution_reference=None,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                session.flush()
                self._audit(
                    session,
                    row=row,
                    action="record_free",
                    actor=caller_identity,
                    key=idempotency_key,
                    request_hash=request_hash,
                    before=None,
                )
                result = _attempt(row)
            return result
        except IntegrityError:
            return self._load_idempotent_attempt(idempotency_key, request_hash)

    def get_attempt(self, attempt_id: str) -> SpendAttempt:
        with self.session_factory() as session:
            row = session.get(ProviderSpendAttemptRow, attempt_id)
            if row is None:
                raise KeyError(attempt_id)
            return _attempt(row)

    def list_attempts(
        self,
        *,
        status: str | None = None,
        provider: ProviderName | None = None,
    ) -> list[SpendAttempt]:
        statement = select(ProviderSpendAttemptRow).order_by(
            ProviderSpendAttemptRow.created_at.desc()
        )
        if status is not None:
            statement = statement.where(ProviderSpendAttemptRow.status == status)
        if provider is not None:
            statement = statement.where(
                ProviderSpendAttemptRow.provider == provider.value
            )
        with self.session_factory() as session:
            return [_attempt(row) for row in session.scalars(statement)]

    def provider_summary(
        self, provider: ProviderName, *, budget_limit: float
    ) -> dict:
        with self.session_factory() as session:
            estimated = self._settled_charge(session, provider)
            uncertain = self._uncertain_charge(session, provider)
            snapshot = session.scalar(
                select(ProviderBalanceSnapshotRow)
                .where(ProviderBalanceSnapshotRow.provider == provider.value)
                .order_by(
                    ProviderBalanceSnapshotRow.observed_at.desc(),
                    ProviderBalanceSnapshotRow.created_at.desc(),
                )
                .limit(1)
            )
        remaining = None if budget_limit <= 0 else max(
            0.0, budget_limit - estimated - uncertain
        )
        return {
            "provider": provider.value,
            "budget_limit": budget_limit,
            "argus_estimated_charge": estimated,
            "uncertain_charge": uncertain,
            "remaining": remaining,
            "estimate_source": "argus",
            "provider_snapshot": (
                {
                    "balance": snapshot.balance,
                    "source": snapshot.source,
                    "observed_at": snapshot.observed_at.isoformat(),
                    "provider_reference": snapshot.provider_reference,
                    "related_attempt_id": snapshot.related_attempt_id,
                    "authoritative_charge": snapshot.authoritative_charge,
                }
                if snapshot is not None
                else None
            ),
        }

    def _obligation(self, session, provider: ProviderName, *, lock: bool) -> float:
        # PostgreSQL needs a lock even when a provider has no attempt rows yet;
        # the transaction-scoped advisory lock supplies that stable mutex.
        if lock and session.get_bind().dialect.name == "postgresql":
            from sqlalchemy import text

            session.execute(
                text("SELECT pg_advisory_xact_lock(hashtext(:provider))"),
                {"provider": provider.value},
            )
        # Lock existing rows as an additional guard on databases that support
        # it. SQLite's BEGIN IMMEDIATE serializes the whole read/write section.
        statement = select(ProviderSpendAttemptRow.id).where(
            ProviderSpendAttemptRow.provider == provider.value
        )
        if lock:
            statement = statement.with_for_update()
        list(session.scalars(statement))
        return self._settled_charge(session, provider) + self._uncertain_charge(
            session, provider
        )

    @staticmethod
    def _active_filter(provider: ProviderName):
        if PROVIDER_TIERS.get(provider) == 1:
            return ProviderSpendAttemptRow.created_at >= (
                datetime.now(tz=None) - timedelta(days=30)
            )
        return True

    def _settled_charge(self, session, provider: ProviderName) -> float:
        value = session.scalar(
            select(func.coalesce(func.sum(ProviderSpendAttemptRow.actual_charge), 0.0))
            .where(
                ProviderSpendAttemptRow.provider == provider.value,
                ProviderSpendAttemptRow.is_paid.is_(True),
                ProviderSpendAttemptRow.status.in_(("settled", "resolved")),
                self._active_filter(provider),
            )
        )
        return float(value or 0.0)

    def _uncertain_charge(self, session, provider: ProviderName) -> float:
        value = session.scalar(
            select(func.coalesce(func.sum(ProviderSpendAttemptRow.reserved_charge), 0.0))
            .where(
                ProviderSpendAttemptRow.provider == provider.value,
                ProviderSpendAttemptRow.is_paid.is_(True),
                ProviderSpendAttemptRow.status == "uncertain",
                # Uncertain charges intentionally never age out.
            )
        )
        return float(value or 0.0)

    def _locked_attempt(self, session, attempt_id: str) -> ProviderSpendAttemptRow:
        row = session.scalar(
            select(ProviderSpendAttemptRow)
            .where(ProviderSpendAttemptRow.id == attempt_id)
            .with_for_update()
        )
        if row is None:
            raise KeyError(attempt_id)
        return row

    def _load_idempotent_attempt(self, key: str, request_hash: str) -> SpendAttempt:
        with self.session_factory() as session:
            row = session.scalar(
                select(ProviderSpendAttemptRow).where(
                    ProviderSpendAttemptRow.idempotency_key == key
                )
            )
            if row is None:
                raise RuntimeError("attempt idempotency race was not visible")
            self._verify_hash(row.request_hash, request_hash)
            return _attempt(row)

    @staticmethod
    def _verify_hash(stored: str, requested: str) -> None:
        if stored != requested:
            raise SpendConflictError("idempotency key reused with different payload")

    @staticmethod
    def _verify_settlement(
        row: ProviderSpendAttemptRow,
        actual_charge: float,
        outcome: str,
        source: str,
    ) -> None:
        if (
            row.actual_charge != actual_charge
            or row.outcome != outcome
            or row.resolution_source != source
        ):
            raise SpendConflictError("attempt already settled with different outcome")

    @staticmethod
    def _audit(
        session,
        *,
        row: ProviderSpendAttemptRow,
        action: str,
        actor: str,
        key: str,
        request_hash: str,
        before: dict | None,
    ) -> None:
        session.add(
            SpendAuditRow(
                id=uuid.uuid4().hex,
                attempt_id=row.id,
                provider=row.provider,
                action=action,
                actor_identity=actor,
                idempotency_key=key,
                request_hash=request_hash,
                before_json=_canonical(before) if before is not None else None,
                after_json=_canonical(_attempt_state(row)),
                created_at=datetime.now(tz=None),
            )
        )

    @staticmethod
    def _audit_snapshot(
        session,
        *,
        row: ProviderBalanceSnapshotRow,
        actor: str,
        request_hash: str,
    ) -> None:
        session.add(
            SpendAuditRow(
                id=uuid.uuid4().hex,
                attempt_id=row.related_attempt_id,
                provider=row.provider,
                action="provider_snapshot",
                actor_identity=actor,
                idempotency_key=row.idempotency_key,
                request_hash=request_hash,
                before_json=None,
                after_json=_canonical(
                    {
                        "balance": row.balance,
                        "source": row.source,
                        "observed_at": row.observed_at,
                        "provider_reference": row.provider_reference,
                        "related_attempt_id": row.related_attempt_id,
                        "authoritative_charge": row.authoritative_charge,
                    }
                ),
                created_at=datetime.now(tz=None),
            )
        )

    def _transaction(self):
        return self._write_transaction()

    @contextmanager
    def _write_transaction(self):
        session = self.session_factory()
        try:
            if session.get_bind().dialect.name == "sqlite":
                # SQLite has no row-level locks. Acquire its database write
                # lock before reading the available balance.
                session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            else:
                session.begin()
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


def create_provider_spend_repository(
    db_url: str | None = None,
    *,
    create_schema: bool | None = None,
) -> ProviderSpendRepository:
    url = db_url or get_config().db_url
    is_sqlite = url.startswith("sqlite:")
    if is_sqlite and url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, pool_pre_ping=True)
    should_create = is_sqlite if create_schema is None else create_schema
    if should_create:
        if not is_sqlite:
            raise ValueError("runtime schema creation is only supported for SQLite")
        SpendBase.metadata.create_all(engine)
    return ProviderSpendRepository(sessionmaker(bind=engine, expire_on_commit=False))
