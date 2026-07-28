"""Transactional persistence for the provider-readiness authority."""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from argus.models import ProviderName

UTC = timezone.utc
MAX_EVIDENCE_REFS = 32


class ReadinessBase(DeclarativeBase):
    pass


class ProviderReadinessObservationRow(ReadinessBase):
    __tablename__ = "provider_readiness_observations"
    __table_args__ = (
        UniqueConstraint(
            "provider", "scope_key", "dimension",
            name="uq_provider_readiness_current_dimension",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(64), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    scope_json: Mapped[str] = mapped_column(Text, nullable=False)
    producer_observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    evidence_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    safe_reason: Mapped[str | None] = mapped_column(String(160), nullable=True)
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class ProviderReadinessSnapshotRow(ReadinessBase):
    __tablename__ = "provider_readiness_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "provider", "scope_key", name="uq_provider_readiness_snapshot_scope"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    scope_key: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_json: Mapped[str] = mapped_column(Text, nullable=False)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    materialized_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProviderReadinessEvidenceRefRow(ReadinessBase):
    __tablename__ = "provider_readiness_evidence_refs"
    __table_args__ = (
        UniqueConstraint(
            "provider", "evidence_ref", name="uq_provider_readiness_evidence_ref"
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    observation_id: Mapped[str] = mapped_column(String(32), nullable=False)
    evidence_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    protected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProviderReadinessLeaseRow(ReadinessBase):
    __tablename__ = "provider_readiness_leases"

    scope_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner: Mapped[str] = mapped_column(String(64), nullable=False)
    fencing_token: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_id: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uncertain_charge: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProviderReadinessAlertRow(ReadinessBase):
    __tablename__ = "provider_readiness_alert_dedupe"
    __table_args__ = (
        UniqueConstraint(
            "provider", "account_fingerprint", "alert_kind",
            name="uq_provider_readiness_alert",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    account_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    alert_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    emitted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


@dataclass(frozen=True, slots=True)
class StoredObservation:
    id: str
    provider: str
    dimension: str
    state: str
    source: str
    scope: Mapping[str, str]
    producer_observed_at: datetime
    ingested_at: datetime
    expires_at: datetime | None
    evidence_ref: str | None
    safe_reason: str | None
    protected: bool


@dataclass(frozen=True, slots=True)
class HalfOpenClaim:
    scope_key: str
    owner: str
    fencing_token: int
    attempt_id: str
    execution_deadline: datetime


@dataclass(frozen=True, slots=True)
class HalfOpenCompletion:
    scope_key: str
    owner: str
    fencing_token: int
    attempt_id: str
    outcome: str
    uncertain_charge: bool
    completed_at: datetime


class StaleFencingToken(RuntimeError):
    """The caller no longer owns the durable execution claim."""


class ReadinessConflict(RuntimeError):
    """An idempotent replay disagreed with durable state."""


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _naive(value: datetime) -> datetime:
    return _aware(value).replace(tzinfo=None)


def _stored(row: ProviderReadinessObservationRow) -> StoredObservation:
    return StoredObservation(
        id=row.id,
        provider=row.provider,
        dimension=row.dimension,
        state=row.state,
        source=row.source,
        scope=json.loads(row.scope_json),
        producer_observed_at=_aware(row.producer_observed_at),
        ingested_at=_aware(row.ingested_at),
        expires_at=_aware(row.expires_at) if row.expires_at else None,
        evidence_ref=row.evidence_ref,
        safe_reason=row.safe_reason,
        protected=row.protected,
    )


class ProviderReadinessRepository:
    """Shared SQL authority for current evidence, snapshots, leases and alerts."""

    def __init__(self, factory: sessionmaker):
        self.session_factory = factory
        self._test_clock_offset = timedelta()

    def authority_now(self, session=None) -> datetime:
        owns = session is None
        if owns:
            session = self.session_factory()
        try:
            value = session.execute(text("SELECT CURRENT_TIMESTAMP")).scalar_one()
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            return _aware(value) + self._test_clock_offset
        finally:
            if owns:
                session.close()

    def advance_authority_clock_for_test(self, delta: timedelta) -> None:
        if delta.total_seconds() < 0:
            raise ValueError("authority test clock cannot move backwards")
        self._test_clock_offset += delta

    def _current_rows(self, session, provider: ProviderName, scope_key: str):
        return list(
            session.scalars(
                select(ProviderReadinessObservationRow)
                .where(
                    ProviderReadinessObservationRow.provider == provider.value,
                    ProviderReadinessObservationRow.scope_key == scope_key,
                )
                .order_by(ProviderReadinessObservationRow.dimension)
            )
        )

    def record_and_materialize(
        self,
        observation: Any,
        fold: Callable[[list[StoredObservation], datetime, int], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Replace one current dimension and fold the bounded seven-row state."""
        scope_key = observation.scope.fingerprint()
        with self._write_transaction() as session:
            now = self.authority_now(session)
            producer = _aware(observation.observed_at)
            if producer > now + timedelta(seconds=30):
                raise ValueError("producer observed_at is too far in the future")
            old = session.scalar(
                select(ProviderReadinessObservationRow)
                .where(
                    ProviderReadinessObservationRow.provider == observation.provider.value,
                    ProviderReadinessObservationRow.scope_key == scope_key,
                    ProviderReadinessObservationRow.dimension == observation.dimension,
                )
                .with_for_update()
            )
            if old is not None:
                session.delete(old)
                session.flush()
            expires = (
                None
                if observation.ttl_seconds is None
                else now + timedelta(seconds=observation.ttl_seconds)
            )
            row = ProviderReadinessObservationRow(
                id=uuid.uuid4().hex,
                provider=observation.provider.value,
                dimension=observation.dimension,
                state=observation.state,
                source=observation.source,
                scope_key=scope_key,
                scope_json=json.dumps(
                    observation.scope.as_dict(), sort_keys=True, separators=(",", ":")
                ),
                producer_observed_at=_naive(producer),
                ingested_at=_naive(now),
                expires_at=_naive(expires) if expires else None,
                evidence_ref=observation.evidence_ref,
                safe_reason=observation.safe_reason,
                protected=observation.protected,
            )
            session.add(row)
            session.flush()
            if observation.evidence_ref:
                existing = session.scalar(
                    select(ProviderReadinessEvidenceRefRow).where(
                        ProviderReadinessEvidenceRefRow.provider
                        == observation.provider.value,
                        ProviderReadinessEvidenceRefRow.evidence_ref
                        == observation.evidence_ref,
                    )
                )
                if existing is None:
                    session.add(
                        ProviderReadinessEvidenceRefRow(
                            id=uuid.uuid4().hex,
                            provider=observation.provider.value,
                            observation_id=row.id,
                            evidence_ref=observation.evidence_ref,
                            protected=observation.protected,
                            created_at=_naive(now),
                        )
                    )
                else:
                    existing.observation_id = row.id
                    existing.protected = observation.protected
                    existing.created_at = _naive(now)
            self._compact_evidence(session, observation.provider)
            current = [_stored(item) for item in self._current_rows(
                session, observation.provider, scope_key
            )]
            generation = self._next_generation(
                session, observation.provider, scope_key
            )
            payload = dict(fold(current, now, generation))
            self._put_snapshot(
                session, observation.provider, scope_key, payload, generation, now
            )
            return payload

    def record_observation(self, observation: Any) -> StoredObservation:
        """Compatibility write. New authority callers use record_and_materialize."""
        captured: StoredObservation | None = None

        def fold(rows, _now, _generation):
            nonlocal captured
            captured = next(row for row in rows if row.dimension == observation.dimension)
            return {"compatibility_write": True}

        self.record_and_materialize(observation, fold)
        assert captured is not None
        return captured

    def read_snapshot(self, provider: ProviderName, scope_key: str) -> Mapping[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ProviderReadinessSnapshotRow).where(
                    ProviderReadinessSnapshotRow.provider == provider.value,
                    ProviderReadinessSnapshotRow.scope_key == scope_key,
                )
            )
            return json.loads(row.snapshot_json) if row is not None else None

    def refresh_expired(
        self,
        provider: ProviderName,
        scope_key: str,
        fold: Callable[[list[StoredObservation], datetime, int], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        with self._write_transaction() as session:
            now = self.authority_now(session)
            rows = self._current_rows(session, provider, scope_key)
            for row in rows:
                if row.expires_at is not None and now >= _aware(row.expires_at):
                    session.delete(row)
            session.flush()
            rows = self._current_rows(session, provider, scope_key)
            generation = self._next_generation(session, provider, scope_key)
            payload = dict(fold([_stored(row) for row in rows], now, generation))
            self._put_snapshot(session, provider, scope_key, payload, generation, now)
            return payload

    def observations(self, provider: ProviderName) -> list[StoredObservation]:
        """Bounded current rows only; retained for diagnostics and migrations."""
        with self.session_factory() as session:
            rows = list(session.scalars(
                select(ProviderReadinessObservationRow)
                .where(ProviderReadinessObservationRow.provider == provider.value)
                .order_by(ProviderReadinessObservationRow.ingested_at)
            ))
        return [_stored(row) for row in rows]

    def put_registration(self, provider: ProviderName, payload: Mapping[str, Any]) -> None:
        with self._write_transaction() as session:
            now = self.authority_now(session)
            scope_key = f"registry:{provider.value}"
            generation = self._next_generation(session, provider, scope_key)
            self._put_snapshot(session, provider, scope_key, payload, generation, now)

    def get_registration(self, provider: ProviderName) -> Mapping[str, Any] | None:
        return self.read_snapshot(provider, f"registry:{provider.value}")

    def _next_generation(self, session, provider, scope_key) -> int:
        row = session.scalar(
            select(ProviderReadinessSnapshotRow)
            .where(
                ProviderReadinessSnapshotRow.provider == provider.value,
                ProviderReadinessSnapshotRow.scope_key == scope_key,
            )
            .with_for_update()
        )
        return 1 if row is None else row.generation + 1

    def _put_snapshot(self, session, provider, scope_key, payload, generation, now):
        row = session.scalar(
            select(ProviderReadinessSnapshotRow).where(
                ProviderReadinessSnapshotRow.provider == provider.value,
                ProviderReadinessSnapshotRow.scope_key == scope_key,
            )
        )
        valid = payload.get("valid_until")
        valid_until = datetime.fromisoformat(valid) if valid else None
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        if row is None:
            session.add(ProviderReadinessSnapshotRow(
                id=uuid.uuid4().hex,
                provider=provider.value,
                scope_key=scope_key,
                generation=generation,
                snapshot_json=encoded,
                valid_until=_naive(valid_until) if valid_until else None,
                materialized_at=_naive(now),
            ))
        else:
            row.generation = generation
            row.snapshot_json = encoded
            row.valid_until = _naive(valid_until) if valid_until else None
            row.materialized_at = _naive(now)

    def _compact_evidence(self, session, provider: ProviderName) -> None:
        current = {
            row.evidence_ref: row.protected
            for row in session.scalars(
                select(ProviderReadinessObservationRow).where(
                    ProviderReadinessObservationRow.provider == provider.value,
                    ProviderReadinessObservationRow.evidence_ref.is_not(None),
                )
            )
        }
        refs = list(session.scalars(
            select(ProviderReadinessEvidenceRefRow)
            .where(ProviderReadinessEvidenceRefRow.provider == provider.value)
            .order_by(
                ProviderReadinessEvidenceRefRow.protected.desc(),
                ProviderReadinessEvidenceRefRow.created_at.desc(),
            )
        ))
        keep = set(current)
        for row in refs:
            if len(keep) >= MAX_EVIDENCE_REFS:
                break
            keep.add(row.evidence_ref)
        for row in refs:
            row.protected = bool(current.get(row.evidence_ref, False))
            if row.evidence_ref not in keep:
                session.delete(row)

    def evidence_ref_count(self, provider: ProviderName) -> int:
        with self.session_factory() as session:
            return int(session.scalar(
                select(func.count()).select_from(ProviderReadinessEvidenceRefRow).where(
                    ProviderReadinessEvidenceRefRow.provider == provider.value
                )
            ) or 0)

    def claim_half_open(
        self, *, scope_key: str, owner: str, execution_timeout_seconds: int,
        attempt_id: str,
    ) -> HalfOpenClaim | None:
        if not 1 <= execution_timeout_seconds <= 300:
            raise ValueError("execution timeout must be from 1 through 300 seconds")
        with self._write_transaction() as session:
            now = self.authority_now(session)
            row = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(ProviderReadinessLeaseRow.scope_key == scope_key)
                .with_for_update()
            )
            if row is None:
                # BEGIN IMMEDIATE serializes SQLite; PostgreSQL's insert is the
                # conflict-safe missing-row election.
                deadline = now + timedelta(seconds=execution_timeout_seconds)
                if session.get_bind().dialect.name == "postgresql":
                    from sqlalchemy.dialects.postgresql import insert
                    inserted = session.execute(
                        insert(ProviderReadinessLeaseRow)
                        .values(
                            scope_key=scope_key, owner=owner, fencing_token=1,
                            attempt_id=attempt_id, state="claimed",
                            execution_deadline=_naive(deadline), outcome=None,
                            uncertain_charge=False, created_at=_naive(now),
                            completed_at=None,
                        )
                        .on_conflict_do_nothing(index_elements=["scope_key"])
                        .returning(ProviderReadinessLeaseRow.scope_key)
                    ).scalar_one_or_none()
                    if inserted is None:
                        return None
                    token = 1
                else:
                    row = ProviderReadinessLeaseRow(
                        scope_key=scope_key, owner=owner, fencing_token=1,
                        attempt_id=attempt_id, state="claimed",
                        execution_deadline=_naive(deadline),
                        outcome=None, uncertain_charge=False,
                        created_at=_naive(now), completed_at=None,
                    )
                    session.add(row)
                    session.flush()
                    token = row.fencing_token
            elif row.state in {"claimed", "unresolved"}:
                return None
            else:
                row.owner = owner
                row.fencing_token += 1
                row.attempt_id = attempt_id
                row.state = "claimed"
                row.execution_deadline = _naive(
                    now + timedelta(seconds=execution_timeout_seconds)
                )
                row.outcome = None
                row.uncertain_charge = False
                row.created_at = _naive(now)
                row.completed_at = None
                token = row.fencing_token
            return HalfOpenClaim(
                scope_key, owner, token, attempt_id,
                now + timedelta(seconds=execution_timeout_seconds),
            )

    def complete_half_open(
        self, *, scope_key: str, owner: str, fencing_token: int,
        outcome: str, uncertain_charge: bool,
    ) -> HalfOpenCompletion:
        with self._write_transaction() as session:
            now = self.authority_now(session)
            row = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(ProviderReadinessLeaseRow.scope_key == scope_key)
                .with_for_update()
            )
            if row is None or row.owner != owner or row.fencing_token != fencing_token:
                raise StaleFencingToken("execution fencing token is stale")
            if row.state in {"completed", "unresolved"}:
                if row.outcome != outcome or row.uncertain_charge != uncertain_charge:
                    raise ReadinessConflict("completion replay has a different outcome")
                completed_at = _aware(row.completed_at)
            elif row.state != "claimed":
                raise StaleFencingToken("execution claim is no longer active")
            else:
                row.state = "unresolved" if uncertain_charge else "completed"
                row.outcome = outcome
                row.uncertain_charge = uncertain_charge
                row.completed_at = _naive(now)
                completed_at = now
            return HalfOpenCompletion(
                scope_key, owner, fencing_token, row.attempt_id, outcome,
                uncertain_charge, completed_at,
            )

    def authorize_execution(
        self, *, context: Any, owner: str, conservative_charge: float,
        execution_timeout_seconds: int,
        decide: Callable[[Mapping[str, Any], Any], Any],
    ) -> Mapping[str, Any]:
        """Atomically recheck readiness/policy, reserve spend, and fence execution."""
        from argus.persistence.provider_spend import ProviderSpendAttemptRow

        with self._write_transaction() as session:
            now = self.authority_now(session)
            snapshot_row = session.scalar(
                select(ProviderReadinessSnapshotRow)
                .where(
                    ProviderReadinessSnapshotRow.provider == context.provider.value,
                    ProviderReadinessSnapshotRow.scope_key == context.scope.fingerprint(),
                )
                .with_for_update()
            )
            payload = json.loads(snapshot_row.snapshot_json) if snapshot_row else {}
            decision = decide(payload, context)
            if decision.code != "eligible":
                return {"allowed": False, "decision": decision}
            lease_key = f"{context.provider.value}:{context.scope.fingerprint()}"
            lease = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(ProviderReadinessLeaseRow.scope_key == lease_key)
                .with_for_update()
            )
            if lease is not None and lease.state in {"claimed", "unresolved"}:
                return {"allowed": False, "decision": type(decision)(
                    "unavailable", "attempt_in_flight", ("lease",)
                )}
            attempt_id = uuid.uuid4().hex
            token = 1 if lease is None else lease.fencing_token + 1
            deadline = now + timedelta(seconds=execution_timeout_seconds)
            if context.tier > 0:
                duplicate = session.scalar(select(ProviderSpendAttemptRow).where(
                    ProviderSpendAttemptRow.idempotency_key == context.idempotency_key
                ))
                if duplicate is not None:
                    return {"allowed": False, "decision": type(decision)(
                        "unavailable", "attempt_in_flight", ("idempotency",)
                    )}
                unresolved = session.scalar(
                    select(func.coalesce(func.sum(
                        ProviderSpendAttemptRow.reserved_charge
                        + ProviderSpendAttemptRow.reservation_overrun
                    ), 0.0)).where(
                        ProviderSpendAttemptRow.provider == context.provider.value,
                        ProviderSpendAttemptRow.status.in_(("reserved", "uncertain")),
                    )
                ) or 0.0
                settled_filters = [
                    ProviderSpendAttemptRow.provider == context.provider.value,
                    ProviderSpendAttemptRow.status.in_(("settled", "resolved")),
                ]
                if context.tier == 1:
                    settled_filters.append(
                        ProviderSpendAttemptRow.created_at
                        >= _naive(now - timedelta(days=30))
                    )
                settled = session.scalar(
                    select(func.coalesce(func.sum(
                        ProviderSpendAttemptRow.actual_charge
                    ), 0.0)).where(*settled_filters)
                ) or 0.0
                obligation = Decimal(str(unresolved)) + Decimal(str(settled))
                registration = self.get_registration_in_session(session, context.provider)
                limit = float(registration.get("budget_limit") or 0)
                requested = obligation + Decimal(str(conservative_charge))
                if limit <= 0 or requested > Decimal(str(limit)):
                    return {"allowed": False, "decision": type(decision)(
                        "spend_blocked", "exhausted", ("spend",)
                    )}
                session.add(ProviderSpendAttemptRow(
                    id=attempt_id,
                    idempotency_key=context.idempotency_key,
                    request_hash=context.plan_id,
                    provider=context.provider.value,
                    tier=context.tier,
                    is_paid=True,
                    status="reserved",
                    outcome=None,
                    reserved_charge=conservative_charge,
                    estimator_violation=False,
                    reservation_overrun=0.0,
                    actual_charge=None,
                    usage=0.0,
                    caller_identity=context.caller_identity,
                    caller_label=context.caller_identity,
                    resolution_source=None,
                    resolution_reference=None,
                    created_at=_naive(now),
                    updated_at=_naive(now),
                ))
            if lease is None:
                session.add(ProviderReadinessLeaseRow(
                    scope_key=lease_key, owner=owner, fencing_token=token,
                    attempt_id=attempt_id, state="claimed",
                    execution_deadline=_naive(deadline), outcome=None,
                    uncertain_charge=False, created_at=_naive(now), completed_at=None,
                ))
            else:
                lease.owner = owner
                lease.fencing_token = token
                lease.attempt_id = attempt_id
                lease.state = "claimed"
                lease.execution_deadline = _naive(deadline)
                lease.outcome = None
                lease.uncertain_charge = False
                lease.created_at = _naive(now)
                lease.completed_at = None
            return {
                "allowed": True, "decision": type(decision)(
                    "eligible", "authorized", ("readiness", "policy", "lease")
                ),
                "scope_key": lease_key, "fencing_token": token,
                "attempt_id": attempt_id, "owner": owner,
            }

    def settle_execution(
        self, *, authorization: Any, category: str, actual_charge: float | None,
        charge_known: bool, evidence_ref: str,
        fold: Callable[[list[StoredObservation], datetime, int], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Settle reservation, lease, terminal evidence and snapshot atomically."""
        from argus.persistence.provider_spend import ProviderSpendAttemptRow

        with self._write_transaction() as session:
            now = self.authority_now(session)
            lease = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(ProviderReadinessLeaseRow.scope_key == authorization.scope_key)
                .with_for_update()
            )
            if (
                lease is None or lease.owner != authorization.owner
                or lease.fencing_token != authorization.fencing_token
            ):
                # A stale owner may be relevant to charge reconciliation, but
                # must never settle the current readiness lease/snapshot.
                raise StaleFencingToken("execution fencing token is stale")
            uncertain = not charge_known
            outcome = category or "success"
            if lease.state in {"completed", "unresolved"}:
                if lease.outcome != outcome or lease.uncertain_charge != uncertain:
                    raise ReadinessConflict("completion replay has a different outcome")
            elif lease.state != "claimed":
                raise StaleFencingToken("execution claim is no longer active")
            else:
                lease.state = "unresolved" if uncertain else "completed"
                lease.outcome = outcome
                lease.uncertain_charge = uncertain
                lease.completed_at = _naive(now)
            attempt = session.get(ProviderSpendAttemptRow, authorization.attempt_id)
            if attempt is not None and attempt.status == "reserved":
                if uncertain:
                    attempt.status = "uncertain"
                    attempt.outcome = outcome
                else:
                    attempt.status = "settled"
                    attempt.outcome = outcome
                    attempt.actual_charge = float(actual_charge or 0.0)
                    attempt.usage = float(actual_charge or 0.0)
                    if attempt.actual_charge > attempt.reserved_charge:
                        attempt.estimator_violation = True
                        attempt.reservation_overrun = float(
                            Decimal(str(attempt.actual_charge))
                            - Decimal(str(attempt.reserved_charge))
                        )
                attempt.updated_at = _naive(now)
            from argus.broker.budgets import PROVIDER_TIERS
            state = (
                "not_applicable"
                if PROVIDER_TIERS[authorization.provider] == 0
                else "uncertain" if uncertain
                else "exhausted" if category == "balance_exhausted"
                else "available"
            )
            scope_key = authorization.scope.fingerprint()
            old = session.scalar(
                select(ProviderReadinessObservationRow)
                .where(
                    ProviderReadinessObservationRow.provider
                    == authorization.provider.value,
                    ProviderReadinessObservationRow.scope_key == scope_key,
                    ProviderReadinessObservationRow.dimension == "spend",
                )
                .with_for_update()
            )
            if old is not None:
                session.delete(old)
                session.flush()
            observation_id = uuid.uuid4().hex
            session.add(ProviderReadinessObservationRow(
                id=observation_id, provider=authorization.provider.value,
                dimension="spend", state=state, source="execution_settlement",
                scope_key=scope_key,
                scope_json=json.dumps(
                    authorization.scope.as_dict(),
                    sort_keys=True, separators=(",", ":"),
                ),
                producer_observed_at=_naive(now), ingested_at=_naive(now),
                expires_at=None, evidence_ref=evidence_ref,
                safe_reason=outcome, protected=state in {"uncertain", "exhausted"},
            ))
            evidence = session.scalar(
                select(ProviderReadinessEvidenceRefRow).where(
                    ProviderReadinessEvidenceRefRow.provider
                    == authorization.provider.value,
                    ProviderReadinessEvidenceRefRow.evidence_ref == evidence_ref,
                )
            )
            if evidence is None:
                session.add(ProviderReadinessEvidenceRefRow(
                    id=uuid.uuid4().hex, provider=authorization.provider.value,
                    observation_id=observation_id, evidence_ref=evidence_ref,
                    protected=state in {"uncertain", "exhausted"},
                    created_at=_naive(now),
                ))
            else:
                evidence.observation_id = observation_id
                evidence.protected = state in {"uncertain", "exhausted"}
                evidence.created_at = _naive(now)
            self._compact_evidence(session, authorization.provider)
            session.flush()
            rows = self._current_rows(session, authorization.provider, scope_key)
            generation = self._next_generation(
                session, authorization.provider, scope_key
            )
            payload = dict(fold([_stored(row) for row in rows], now, generation))
            self._put_snapshot(
                session, authorization.provider, scope_key, payload, generation, now
            )
            if state in {"uncertain", "exhausted"}:
                existing_alert = session.scalar(select(ProviderReadinessAlertRow).where(
                    ProviderReadinessAlertRow.provider == authorization.provider.value,
                    ProviderReadinessAlertRow.account_fingerprint
                    == authorization.scope.account_fingerprint,
                    ProviderReadinessAlertRow.alert_kind
                    == "exhaustion_without_refresh",
                ))
                if existing_alert is None:
                    session.add(ProviderReadinessAlertRow(
                        id=uuid.uuid4().hex,
                        provider=authorization.provider.value,
                        account_fingerprint=authorization.scope.account_fingerprint,
                        alert_kind="exhaustion_without_refresh",
                        emitted_at=_naive(now),
                    ))
            return payload

    def record_spend_in_session(
        self, session, *, provider: ProviderName, state: str,
        evidence_ref: str, outcome: str | None, protected: bool,
    ) -> None:
        """Fold spend reconciliation into readiness in the caller transaction."""
        from argus.broker.readiness import (
            ProviderReadinessService,
            ReadinessScope,
        )

        registration = self.get_registration_in_session(session, provider)
        if not registration.get("scope"):
            return
        scope = ReadinessScope(**registration["scope"])
        scope_key = scope.fingerprint()
        now = self.authority_now(session)
        current = session.scalar(
            select(ProviderReadinessObservationRow)
            .where(
                ProviderReadinessObservationRow.provider == provider.value,
                ProviderReadinessObservationRow.scope_key == scope_key,
                ProviderReadinessObservationRow.dimension == "spend",
            )
            .with_for_update()
        )
        if current is not None:
            # A compatibility refresh cannot erase a still-unresolved terminal
            # observation. Authoritative reconciliation may do so explicitly.
            if (
                current.protected and state == "available"
                and not (outcome or "").startswith("reconciled")
            ):
                return
            session.delete(current)
            session.flush()
        observation_id = uuid.uuid4().hex
        session.add(ProviderReadinessObservationRow(
            id=observation_id, provider=provider.value, dimension="spend",
            state=state, source="provider_spend_transaction",
            scope_key=scope_key,
            scope_json=json.dumps(
                scope.as_dict(), sort_keys=True, separators=(",", ":")
            ),
            producer_observed_at=_naive(now), ingested_at=_naive(now),
            expires_at=None, evidence_ref=evidence_ref,
            safe_reason=outcome, protected=protected,
        ))
        existing = session.scalar(select(ProviderReadinessEvidenceRefRow).where(
            ProviderReadinessEvidenceRefRow.provider == provider.value,
            ProviderReadinessEvidenceRefRow.evidence_ref == evidence_ref,
        ))
        if existing is None:
            session.add(ProviderReadinessEvidenceRefRow(
                id=uuid.uuid4().hex, provider=provider.value,
                observation_id=observation_id, evidence_ref=evidence_ref,
                protected=protected, created_at=_naive(now),
            ))
        else:
            existing.observation_id = observation_id
            existing.protected = protected
            existing.created_at = _naive(now)
        self._compact_evidence(session, provider)
        session.flush()
        rows = self._current_rows(session, provider, scope_key)
        generation = self._next_generation(session, provider, scope_key)
        service = ProviderReadinessService(repository=self)
        payload = service._fold(
            provider, scope, [_stored(row) for row in rows], now, generation
        ).as_dict()
        self._put_snapshot(session, provider, scope_key, payload, generation, now)

    def append_stale_charge_evidence(
        self, *, authorization: Any, actual_charge: float | None,
        charge_known: bool, evidence_ref: str,
    ) -> None:
        """Append stale-owner charge evidence without touching readiness."""
        from argus.persistence.provider_spend import SpendAuditRow

        with self._write_transaction() as session:
            key = f"stale:{authorization.attempt_id}:{evidence_ref}"
            if session.scalar(select(SpendAuditRow).where(
                SpendAuditRow.action == "stale_charge_evidence",
                SpendAuditRow.idempotency_key == key,
            )) is not None:
                return
            now = self.authority_now(session)
            session.add(SpendAuditRow(
                id=uuid.uuid4().hex,
                attempt_id=authorization.attempt_id or None,
                provider=authorization.provider.value,
                action="stale_charge_evidence",
                actor_identity=authorization.owner,
                idempotency_key=key,
                request_hash=authorization.scope.fingerprint(),
                before_json=None,
                after_json=json.dumps({
                    "actual_charge": actual_charge,
                    "charge_known": charge_known,
                    "evidence_ref": evidence_ref,
                    "readiness_settled": False,
                }, sort_keys=True, separators=(",", ":")),
                created_at=_naive(now),
            ))

    def get_registration_in_session(self, session, provider):
        row = session.scalar(select(ProviderReadinessSnapshotRow).where(
            ProviderReadinessSnapshotRow.provider == provider.value,
            ProviderReadinessSnapshotRow.scope_key == f"registry:{provider.value}",
        ))
        return json.loads(row.snapshot_json) if row else {}

    def paid_attempt_count(self, provider: ProviderName) -> int:
        from argus.persistence.provider_spend import ProviderSpendAttemptRow
        with self.session_factory() as session:
            return int(session.scalar(select(func.count()).select_from(
                ProviderSpendAttemptRow
            ).where(ProviderSpendAttemptRow.provider == provider.value)) or 0)

    def provider_spend_projection(
        self, provider: ProviderName, *, budget_limit: float,
    ) -> dict[str, float | None]:
        """Project authoritative settled and unresolved obligations using DB time."""
        from argus.broker.budgets import PROVIDER_TIERS
        from argus.persistence.provider_spend import ProviderSpendAttemptRow

        with self.session_factory() as session:
            now = self.authority_now(session)
            settled_filters = [
                ProviderSpendAttemptRow.provider == provider.value,
                ProviderSpendAttemptRow.status.in_(("settled", "resolved")),
            ]
            if PROVIDER_TIERS[provider] == 1:
                settled_filters.append(
                    ProviderSpendAttemptRow.created_at
                    >= _naive(now - timedelta(days=30))
                )
            settled = session.scalar(
                select(func.coalesce(func.sum(
                    ProviderSpendAttemptRow.actual_charge
                ), 0.0)).where(*settled_filters)
            ) or 0.0
            uncertain = session.scalar(
                select(func.coalesce(func.sum(
                    ProviderSpendAttemptRow.reserved_charge
                    + ProviderSpendAttemptRow.reservation_overrun
                ), 0.0)).where(
                    ProviderSpendAttemptRow.provider == provider.value,
                    ProviderSpendAttemptRow.status.in_(("reserved", "uncertain")),
                )
            ) or 0.0
        settled_decimal = Decimal(str(settled))
        uncertain_decimal = Decimal(str(uncertain))
        remaining = (
            None
            if budget_limit <= 0
            else max(
                Decimal("0"),
                Decimal(str(budget_limit)) - settled_decimal - uncertain_decimal,
            )
        )
        return {
            "argus_estimated_charge": float(settled_decimal),
            "uncertain_charge": float(uncertain_decimal),
            "remaining": float(remaining) if remaining is not None else None,
        }

    def record_terminal_exhaustion(
        self, *, provider: ProviderName, account_fingerprint: str,
        recurring: bool, reset_at: datetime | None, evidence_ref: str,
    ) -> None:
        from argus.broker.readiness import ProviderObservation, ReadinessScope
        registration = self.get_registration(provider) or {}
        scope_data = registration.get("scope") or {}
        scope_data["account_fingerprint"] = account_fingerprint
        scope = ReadinessScope(**scope_data)
        now = self.authority_now()
        ttl = None
        if recurring:
            if reset_at is None or _aware(reset_at) <= now:
                raise ValueError("recurring exhaustion requires a future reset")
            ttl = max(1, int((_aware(reset_at) - now).total_seconds()))
        elif reset_at is not None:
            raise ValueError("one-time exhaustion cannot have an automatic reset")
        self.record_observation(ProviderObservation(
            provider=provider, dimension="spend", state="exhausted",
            source="provider_authoritative", scope=scope, observed_at=now,
            ttl_seconds=ttl, evidence_ref=evidence_ref, protected=True,
            safe_reason="recurring_quota_exhausted" if recurring
            else "one_time_credit_exhausted",
        ))

    def spend_state(self, provider: ProviderName, *, account_fingerprint: str) -> str:
        now = self.authority_now()
        rows = [
            row for row in self.observations(provider)
            if row.dimension == "spend"
            and row.scope["account_fingerprint"] == account_fingerprint
        ]
        if not rows:
            return "unknown"
        row = rows[-1]
        return "unknown" if row.expires_at and now >= row.expires_at else row.state

    def emit_operator_alert_once(
        self, provider: ProviderName, *, account_fingerprint: str, alert_kind: str,
    ) -> bool:
        try:
            with self._write_transaction() as session:
                session.add(ProviderReadinessAlertRow(
                    id=uuid.uuid4().hex, provider=provider.value,
                    account_fingerprint=account_fingerprint, alert_kind=alert_kind,
                    emitted_at=_naive(self.authority_now(session)),
                ))
                session.flush()
            return True
        except IntegrityError:
            return False

    @contextmanager
    def _write_transaction(self):
        session = self.session_factory()
        try:
            if session.get_bind().dialect.name == "sqlite":
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


def create_readiness_repository(
    db_url: str, *, create_schema: bool | None = None,
) -> ProviderReadinessRepository:
    if db_url.startswith("sqlite:///"):
        path = db_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    from argus.persistence.search_ledger import _bounded_engine_options
    engine = create_engine(
        db_url, pool_pre_ping=True, **_bounded_engine_options(db_url)
    )
    is_sqlite = engine.dialect.name == "sqlite"
    should_create = is_sqlite if create_schema is None else create_schema
    if should_create:
        if not is_sqlite:
            raise ValueError("runtime schema creation is only supported for SQLite")
        ReadinessBase.metadata.create_all(engine)
        from argus.persistence.provider_spend import SpendBase
        SpendBase.metadata.create_all(engine)
    return ProviderReadinessRepository(sessionmaker(bind=engine, expire_on_commit=False))


def readiness_repository_from_session_factory(factory: sessionmaker):
    engine = factory.kw["bind"]
    if engine.dialect.name == "sqlite":
        ReadinessBase.metadata.create_all(engine)
    return ProviderReadinessRepository(factory)
