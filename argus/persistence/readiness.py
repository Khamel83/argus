"""Durable persistence for the provider-readiness decision authority.

The repository deliberately exposes normalized rows rather than provider
objects.  Database transaction time is the freshness and fencing authority.
"""

from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import (
    Boolean,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from argus.models import ProviderName


UTC = timezone.utc


class ReadinessBase(DeclarativeBase):
    pass


class ProviderReadinessObservationRow(ReadinessBase):
    __tablename__ = "provider_readiness_observations"

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
            "provider",
            "evidence_ref",
            name="uq_provider_readiness_evidence_ref",
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
    uncertain_charge: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ProviderReadinessAlertRow(ReadinessBase):
    __tablename__ = "provider_readiness_alert_dedupe"
    __table_args__ = (
        UniqueConstraint(
            "provider",
            "account_fingerprint",
            "alert_kind",
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
    scope: Mapping[str, str | None]
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
    """Idempotent replay disagreed with the durable payload."""


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _naive(value: datetime) -> datetime:
    return _aware(value).replace(tzinfo=None)


def _row_observation(row: ProviderReadinessObservationRow) -> StoredObservation:
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
    """Shared SQL authority for observations, snapshots, fencing and alerts."""

    def __init__(self, factory: sessionmaker):
        self.session_factory = factory
        self._test_clock_offset = timedelta()

    def authority_now(self, session=None) -> datetime:
        owns_session = session is None
        if owns_session:
            session = self.session_factory()
        try:
            dialect = session.get_bind().dialect.name
            statement = (
                text("SELECT CURRENT_TIMESTAMP")
                if dialect == "sqlite"
                else text("SELECT CURRENT_TIMESTAMP")
            )
            value = session.execute(statement).scalar_one()
            if isinstance(value, str):
                value = datetime.fromisoformat(value)
            return _aware(value) + self._test_clock_offset
        finally:
            if owns_session:
                session.close()

    def advance_authority_clock_for_test(self, delta: timedelta) -> None:
        """Test seam; production authority remains the database clock."""
        if delta.total_seconds() < 0:
            raise ValueError("authority test clock cannot move backwards")
        self._test_clock_offset += delta

    def record_observation(self, observation: Any) -> StoredObservation:
        scope_payload = observation.scope.as_dict()
        scope_json = json.dumps(scope_payload, sort_keys=True, separators=(",", ":"))
        with self._write_transaction() as session:
            now = self.authority_now(session)
            producer_time = _aware(observation.observed_at)
            if producer_time > now + timedelta(seconds=30):
                raise ValueError("producer observed_at is too far in the future")
            expires_at = (
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
                scope_key=observation.scope.fingerprint(),
                scope_json=scope_json,
                producer_observed_at=_naive(producer_time),
                ingested_at=_naive(now),
                expires_at=_naive(expires_at) if expires_at else None,
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
                elif observation.protected and not existing.protected:
                    existing.protected = True
            return _row_observation(row)

    def observations(self, provider: ProviderName) -> list[StoredObservation]:
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(ProviderReadinessObservationRow)
                    .where(
                        ProviderReadinessObservationRow.provider == provider.value
                    )
                    .order_by(
                        ProviderReadinessObservationRow.ingested_at.asc(),
                        ProviderReadinessObservationRow.producer_observed_at.asc(),
                        ProviderReadinessObservationRow.id.asc(),
                    )
                )
            )
        return [_row_observation(row) for row in rows]

    def materialize_snapshot(
        self,
        *,
        provider: ProviderName,
        scope_key: str,
        snapshot: Mapping[str, Any],
        valid_until: datetime | None,
    ) -> int:
        with self._write_transaction() as session:
            now = self.authority_now(session)
            row = session.scalar(
                select(ProviderReadinessSnapshotRow)
                .where(
                    ProviderReadinessSnapshotRow.provider == provider.value,
                    ProviderReadinessSnapshotRow.scope_key == scope_key,
                )
                .with_for_update()
            )
            payload = json.dumps(snapshot, sort_keys=True, separators=(",", ":"))
            if row is None:
                row = ProviderReadinessSnapshotRow(
                    id=uuid.uuid4().hex,
                    provider=provider.value,
                    scope_key=scope_key,
                    generation=1,
                    snapshot_json=payload,
                    valid_until=_naive(valid_until) if valid_until else None,
                    materialized_at=_naive(now),
                )
                session.add(row)
            else:
                row.generation += 1
                row.snapshot_json = payload
                row.valid_until = _naive(valid_until) if valid_until else None
                row.materialized_at = _naive(now)
            session.flush()
            return row.generation

    def claim_half_open(
        self,
        *,
        scope_key: str,
        owner: str,
        execution_timeout_seconds: int,
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
            if row is not None and row.state in {"claimed", "unresolved"}:
                return None
            token = 1 if row is None else row.fencing_token + 1
            deadline = now + timedelta(seconds=execution_timeout_seconds)
            if row is None:
                row = ProviderReadinessLeaseRow(
                    scope_key=scope_key,
                    owner=owner,
                    fencing_token=token,
                    attempt_id=attempt_id,
                    state="claimed",
                    execution_deadline=_naive(deadline),
                    outcome=None,
                    uncertain_charge=False,
                    created_at=_naive(now),
                    completed_at=None,
                )
                session.add(row)
            else:
                row.owner = owner
                row.fencing_token = token
                row.attempt_id = attempt_id
                row.state = "claimed"
                row.execution_deadline = _naive(deadline)
                row.outcome = None
                row.uncertain_charge = False
                row.created_at = _naive(now)
                row.completed_at = None
            session.flush()
            return HalfOpenClaim(scope_key, owner, token, attempt_id, deadline)

    def complete_half_open(
        self,
        *,
        scope_key: str,
        owner: str,
        fencing_token: int,
        outcome: str,
        uncertain_charge: bool,
    ) -> HalfOpenCompletion:
        with self._write_transaction() as session:
            now = self.authority_now(session)
            row = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(ProviderReadinessLeaseRow.scope_key == scope_key)
                .with_for_update()
            )
            if (
                row is None
                or row.owner != owner
                or row.fencing_token != fencing_token
            ):
                raise StaleFencingToken("half-open claim fencing token is stale")
            if row.state == "completed":
                if (
                    row.outcome != outcome
                    or row.uncertain_charge is not uncertain_charge
                ):
                    raise ReadinessConflict(
                        "half-open completion replay has a different outcome"
                    )
                return HalfOpenCompletion(
                    scope_key,
                    owner,
                    fencing_token,
                    row.attempt_id,
                    row.outcome,
                    row.uncertain_charge,
                    _aware(row.completed_at),
                )
            if row.state != "claimed":
                raise StaleFencingToken("half-open claim is no longer active")
            row.state = "unresolved" if uncertain_charge else "completed"
            row.outcome = outcome
            row.uncertain_charge = uncertain_charge
            row.completed_at = _naive(now)
            session.flush()
            return HalfOpenCompletion(
                scope_key,
                owner,
                fencing_token,
                row.attempt_id,
                outcome,
                uncertain_charge,
                now,
            )

    def record_terminal_exhaustion(
        self,
        *,
        provider: ProviderName,
        account_fingerprint: str,
        recurring: bool,
        reset_at: datetime | None,
        evidence_ref: str,
    ) -> None:
        from argus.broker.readiness import ProviderObservation, ReadinessScope

        now = self.authority_now()
        if recurring:
            if reset_at is None or _aware(reset_at) <= now:
                raise ValueError("recurring exhaustion requires a future reset")
            ttl = max(1, int((_aware(reset_at) - now).total_seconds()))
        elif reset_at is not None:
            raise ValueError("one-time exhaustion cannot have an automatic reset")
        else:
            ttl = None
        self.record_observation(
            ProviderObservation(
                provider=provider,
                dimension="spend",
                state="exhausted",
                source="provider_authoritative",
                scope=ReadinessScope(account_fingerprint=account_fingerprint),
                observed_at=now,
                ttl_seconds=ttl,
                evidence_ref=evidence_ref,
                protected=True,
                safe_reason=(
                    "recurring_quota_exhausted"
                    if recurring
                    else "one_time_credit_exhausted"
                ),
            )
        )

    def spend_state(
        self, provider: ProviderName, *, account_fingerprint: str
    ) -> str:
        now = self.authority_now()
        rows = [
            row
            for row in self.observations(provider)
            if row.dimension == "spend"
            and row.scope.get("account_fingerprint") == account_fingerprint
        ]
        if not rows:
            return "unknown"
        latest = rows[-1]
        if latest.expires_at is not None and now >= latest.expires_at:
            return "unknown"
        return latest.state

    def emit_operator_alert_once(
        self,
        provider: ProviderName,
        *,
        account_fingerprint: str,
        alert_kind: str,
    ) -> bool:
        try:
            with self._write_transaction() as session:
                session.add(
                    ProviderReadinessAlertRow(
                        id=uuid.uuid4().hex,
                        provider=provider.value,
                        account_fingerprint=account_fingerprint,
                        alert_kind=alert_kind,
                        emitted_at=_naive(self.authority_now(session)),
                    )
                )
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
    db_url: str,
    *,
    create_schema: bool | None = None,
) -> ProviderReadinessRepository:
    is_sqlite = db_url.startswith("sqlite:")
    if db_url.startswith("sqlite:///"):
        path = db_url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    from argus.persistence.search_ledger import _bounded_engine_options

    engine = create_engine(
        db_url,
        pool_pre_ping=True,
        **_bounded_engine_options(db_url),
    )
    should_create = is_sqlite if create_schema is None else create_schema
    if should_create:
        if not is_sqlite:
            raise ValueError("runtime schema creation is only supported for SQLite")
        ReadinessBase.metadata.create_all(engine)
    return ProviderReadinessRepository(
        sessionmaker(bind=engine, expire_on_commit=False)
    )


def readiness_repository_from_session_factory(
    factory: sessionmaker,
) -> ProviderReadinessRepository:
    """Share the spend database without creating production schema at runtime."""
    engine = factory.kw["bind"]
    if engine.dialect.name == "sqlite":
        ReadinessBase.metadata.create_all(engine)
    return ProviderReadinessRepository(factory)
