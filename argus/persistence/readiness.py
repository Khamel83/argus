"""Transactional persistence for the provider-readiness authority."""

from __future__ import annotations

import json
import hashlib
import math
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
MAX_EXACT_EXPIRY_SECONDS = 31_536_000
EXACT_EXPIRY_SOURCES = frozenset({
    "provider_authoritative",
    "provider_reconciliation",
})


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
        *,
        replace_existing: bool = True,
        _session: Any | None = None,
        _now: datetime | None = None,
        _allow_exact_expiry: bool = False,
    ) -> Mapping[str, Any]:
        """Replace one current dimension and fold the bounded seven-row state."""
        scope_key = observation.scope.fingerprint()

        def persist(session, now):
            producer_observed_at = observation.observed_at
            if producer_observed_at.tzinfo is None:
                raise ValueError("observed_at must be timezone aware")
            producer = _aware(producer_observed_at)
            exact_expires = getattr(observation, "expires_at", None)
            expires = None
            if exact_expires is not None:
                if exact_expires.tzinfo is None:
                    raise ValueError("expires_at must be timezone aware")
                if observation.ttl_seconds is not None:
                    raise ValueError("observation expiry must use TTL or expires_at")
                expires = _aware(exact_expires)
                if expires <= producer:
                    raise ValueError(
                        "observation expires_at must be after observed_at"
                    )
                if expires > producer + timedelta(seconds=MAX_EXACT_EXPIRY_SECONDS):
                    raise ValueError(
                        "exact expiry exceeds one-year producer bound"
                    )
            if producer > now + timedelta(seconds=30):
                raise ValueError("producer observed_at is too far in the future")
            if exact_expires is not None:
                if expires <= now:
                    raise ValueError("observation expires_at must be after database time")
                exact_shape = (
                    observation.dimension == "spend"
                    and observation.state == "exhausted"
                    and observation.source in EXACT_EXPIRY_SOURCES
                    and observation.protected is True
                )
                if not _allow_exact_expiry or not exact_shape:
                    raise ValueError("exact expiry write is not authorized")
                if expires > now + timedelta(seconds=MAX_EXACT_EXPIRY_SECONDS):
                    raise ValueError(
                        "observation expires_at exceeds one-year database bound"
                    )
            elif observation.ttl_seconds is not None:
                expires = now + timedelta(seconds=observation.ttl_seconds)
            old = session.scalar(
                select(ProviderReadinessObservationRow)
                .where(
                    ProviderReadinessObservationRow.provider == observation.provider.value,
                    ProviderReadinessObservationRow.scope_key == scope_key,
                    ProviderReadinessObservationRow.dimension == observation.dimension,
                )
                .with_for_update()
            )
            if old is not None and not replace_existing:
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
            if old is not None:
                if old.evidence_ref and not old.protected:
                    old_evidence = session.scalar(
                        select(ProviderReadinessEvidenceRefRow).where(
                            ProviderReadinessEvidenceRefRow.provider
                            == observation.provider.value,
                            ProviderReadinessEvidenceRefRow.evidence_ref
                            == old.evidence_ref,
                        )
                    )
                    if old_evidence is not None:
                        old_evidence.protected = False
                session.delete(old)
                session.flush()
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
                            protected=True,
                            created_at=_naive(now),
                        )
                    )
                else:
                    existing.observation_id = row.id
                    existing.protected = True
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
            self._compact_evidence(session, observation.provider)
            materialized = session.scalar(
                select(ProviderReadinessSnapshotRow).where(
                    ProviderReadinessSnapshotRow.provider
                    == observation.provider.value,
                    ProviderReadinessSnapshotRow.scope_key == scope_key,
                )
            )
            return (
                json.loads(materialized.snapshot_json)
                if materialized is not None else payload
            )
        if _session is not None:
            if _now is None:
                raise ValueError("session-bound observation write requires database time")
            return persist(_session, _aware(_now))
        with self._write_transaction() as session:
            return persist(session, self.authority_now(session))

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
        from argus.persistence.provider_spend import SpendAuditRow

        current_rows = list(session.scalars(
            select(ProviderReadinessObservationRow).where(
                ProviderReadinessObservationRow.provider == provider.value,
                ProviderReadinessObservationRow.evidence_ref.is_not(None),
            )
        ))
        current = {
            row.evidence_ref for row in current_rows if row.evidence_ref is not None
        }
        refs = list(session.scalars(
            select(ProviderReadinessEvidenceRefRow)
            .where(ProviderReadinessEvidenceRefRow.provider == provider.value)
        ))
        for row in refs:
            if row.evidence_ref in current:
                row.protected = True
        refs.sort(
            key=lambda row: (
                row.evidence_ref in current,
                row.protected,
                row.created_at,
            ),
            reverse=True,
        )
        protected = current | {
            row.evidence_ref
            for row in refs
            if row.protected and not row.evidence_ref.startswith("overflow:")
        }
        audit_key = f"readiness-overflow:{provider.value}"
        overflow_audit = session.scalar(select(SpendAuditRow).where(
            SpendAuditRow.action == "readiness_overflow",
            SpendAuditRow.idempotency_key == audit_key,
        ))
        overflow = len(protected) > MAX_EVIDENCE_REFS or overflow_audit is not None
        overflow_ref = None
        if overflow:
            now = self.authority_now(session)
            for evidence_ref in protected:
                reference_hash = hashlib.sha256(evidence_ref.encode()).hexdigest()
                archive_key = (
                    f"readiness-evidence:{provider.value}:{reference_hash}"
                )
                archived = session.scalar(select(SpendAuditRow.id).where(
                    SpendAuditRow.action == "readiness_evidence_archive",
                    SpendAuditRow.idempotency_key == archive_key,
                ))
                if archived is None:
                    session.add(SpendAuditRow(
                        id=uuid.uuid4().hex,
                        attempt_id=None,
                        provider=provider.value,
                        action="readiness_evidence_archive",
                        actor_identity="readiness_authority",
                        idempotency_key=archive_key,
                        request_hash=reference_hash,
                        before_json=None,
                        after_json=json.dumps(
                            {"evidence_ref": evidence_ref},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                        created_at=_naive(now),
                    ))
            session.flush()
            archived_refs = {
                json.loads(row.after_json)["evidence_ref"]
                for row in session.scalars(select(SpendAuditRow).where(
                    SpendAuditRow.provider == provider.value,
                    SpendAuditRow.action == "readiness_evidence_archive",
                ))
            }
            ordered_current = sorted(archived_refs)
            selected_current = set(ordered_current[: MAX_EVIDENCE_REFS - 1])
            omitted = ordered_current[MAX_EVIDENCE_REFS - 1 :]
            digest = hashlib.sha256(
                json.dumps(
                    ordered_current, separators=(",", ":")
                ).encode()
            ).hexdigest()
            overflow_ref = f"overflow:{digest[:48]}"
            for row in current_rows:
                row.protected = True
            overflow_payload = {
                "omitted_count": len(omitted),
                "protected_count": len(ordered_current),
                "provider": provider.value,
                "query": {
                    "action": "readiness_evidence_archive",
                    "provider": provider.value,
                    "source": "provider_spend_audit",
                },
                "reference_set_sha256": digest,
            }
            encoded_overflow = json.dumps(
                overflow_payload, sort_keys=True, separators=(",", ":")
            )
            if overflow_audit is None:
                session.add(SpendAuditRow(
                    id=uuid.uuid4().hex,
                    attempt_id=None,
                    provider=provider.value,
                    action="readiness_overflow",
                    actor_identity="readiness_authority",
                    idempotency_key=audit_key,
                    request_hash=digest,
                    before_json=None,
                    after_json=encoded_overflow,
                    created_at=_naive(now),
                ))
            else:
                overflow_audit.before_json = overflow_audit.after_json
                overflow_audit.after_json = encoded_overflow
                overflow_audit.request_hash = digest
                overflow_audit.created_at = _naive(now)
            by_reference = {
                row.evidence_ref: row for row in refs
            }
            observation_ids = {
                row.evidence_ref: row.id for row in current_rows
                if row.evidence_ref is not None
            }
            for evidence_ref in selected_current | {overflow_ref}:
                evidence = by_reference.get(evidence_ref)
                observation_id = (
                    observation_ids.get(evidence_ref)
                    or current_rows[0].id
                )
                if evidence is None:
                    evidence = ProviderReadinessEvidenceRefRow(
                        id=uuid.uuid4().hex,
                        provider=provider.value,
                        observation_id=observation_id,
                        evidence_ref=evidence_ref,
                        protected=True,
                        created_at=_naive(now),
                    )
                    session.add(evidence)
                    refs.append(evidence)
                else:
                    evidence.observation_id = observation_id
                    evidence.protected = True
                    evidence.created_at = _naive(now)
            for row in session.scalars(
                select(ProviderReadinessObservationRow).where(
                    ProviderReadinessObservationRow.provider == provider.value,
                    ProviderReadinessObservationRow.dimension == "configuration",
                )
            ):
                row.state = "evidence_overflow"
                row.safe_reason = "evidence_overflow"
                row.protected = True
            for snapshot in session.scalars(
                select(ProviderReadinessSnapshotRow).where(
                    ProviderReadinessSnapshotRow.provider == provider.value,
                    ProviderReadinessSnapshotRow.scope_key
                    != f"registry:{provider.value}",
                )
            ):
                payload = json.loads(snapshot.snapshot_json)
                if (
                    not isinstance(payload.get("configuration"), dict)
                    or not isinstance(payload.get("execution_decision"), dict)
                ):
                    continue
                payload["configuration"] = {
                    "configured": False,
                    "issues": ["evidence_overflow"],
                }
                payload["healthy"] = False
                payload["state"] = "unready"
                payload["execution_decision"] = {
                    "code": "unavailable",
                    "reason": "evidence_overflow",
                    "contributing_dimensions": ["configuration"],
                }
                receipts = [
                    overflow_ref,
                    *(
                        receipt
                        for receipt in payload.get("evidence_receipts", [])
                        if not str(receipt).startswith("overflow:")
                    ),
                ]
                payload["evidence_receipts"] = receipts[:MAX_EVIDENCE_REFS]
                payload["protected_evidence_count"] = len({
                    row.evidence_ref
                    for row in current_rows
                    if row.scope_key == snapshot.scope_key
                })
                snapshot.generation += 1
                payload["generation"] = snapshot.generation
                snapshot.snapshot_json = json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                )
                snapshot.materialized_at = _naive(now)
            keep = selected_current | {overflow_ref}
        else:
            keep = set(current)
            for row in refs:
                if len(keep) >= MAX_EVIDENCE_REFS:
                    break
                keep.add(row.evidence_ref)
        for row in refs:
            if row.evidence_ref not in keep:
                session.delete(row)

    def get_snapshot(
        self, provider: ProviderName, scope_key: str,
    ) -> Mapping[str, Any] | None:
        with self.session_factory() as session:
            row = session.scalar(select(ProviderReadinessSnapshotRow).where(
                ProviderReadinessSnapshotRow.provider == provider.value,
                ProviderReadinessSnapshotRow.scope_key == scope_key,
            ))
            return json.loads(row.snapshot_json) if row else None

    def latest_lease(self, provider: ProviderName) -> HalfOpenCompletion | None:
        with self.session_factory() as session:
            row = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(
                    ProviderReadinessLeaseRow.scope_key.like(
                        f"{provider.value}:%"
                    )
                )
                .order_by(ProviderReadinessLeaseRow.created_at.desc())
                .limit(1)
            )
            if row is None or row.completed_at is None:
                return None
            return HalfOpenCompletion(
                row.scope_key,
                row.owner,
                row.fencing_token,
                row.attempt_id,
                row.outcome or "unknown",
                row.uncertain_charge,
                _aware(row.completed_at),
            )

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

    def list_stale_execution_attempts(
        self,
        *,
        now: datetime | float | None = None,
        limit: int = 128,
    ) -> tuple[Any, ...]:
        """Atomically fence expired executions and retain their obligations.

        A reservation with an elapsed execution deadline has an unknown
        provider effect.  The lease therefore becomes ``unresolved`` and the
        spend attempt becomes ``uncertain`` in the same locked transaction.
        No charge is settled, refunded, or retried.  The returned values are
        detached ``SpendAttempt`` projections for bounded scheduler evidence.
        """

        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            return ()
        limit = min(limit, 128)
        from argus.persistence.provider_spend import (
            ProviderSpendAttemptRow,
            SpendAuditRow,
            _attempt,
            _attempt_state,
        )

        with self._write_transaction() as session:
            authority_now = self.authority_now(session)
            if now is None:
                cutoff = authority_now
            elif isinstance(now, datetime):
                cutoff = _aware(now)
            elif isinstance(now, (int, float)) and not isinstance(now, bool):
                # A wall-clock epoch is accepted for scheduler integrations.
                # Monotonic values are not comparable with database deadlines;
                # use authoritative database time for those callers.
                cutoff = (
                    datetime.fromtimestamp(float(now), tz=UTC)
                    if float(now) >= 1_000_000_000
                    else authority_now
                )
            else:
                raise ValueError("stale sweep time must be datetime or epoch")

            leases = list(session.scalars(
                select(ProviderReadinessLeaseRow)
                .where(
                    ProviderReadinessLeaseRow.state == "claimed",
                    ProviderReadinessLeaseRow.execution_deadline <= _naive(cutoff),
                )
                .order_by(
                    ProviderReadinessLeaseRow.execution_deadline,
                    ProviderReadinessLeaseRow.attempt_id,
                )
                .limit(limit)
                .with_for_update(skip_locked=True)
            ))
            swept: list[Any] = []
            for lease in leases:
                attempt = session.scalar(
                    select(ProviderSpendAttemptRow)
                    .where(ProviderSpendAttemptRow.id == lease.attempt_id)
                    .with_for_update()
                )
                if attempt is None or attempt.status != "reserved":
                    # Keep the lease and attempt state consistent if an older
                    # compatibility writer left an incomplete pair behind.
                    if attempt is None:
                        lease.state = "unresolved"
                        lease.outcome = "execution_deadline_expired"
                        lease.uncertain_charge = True
                        lease.completed_at = _naive(cutoff)
                    continue

                before = _attempt_state(attempt)
                attempt.status = "uncertain"
                attempt.outcome = "execution_deadline_expired"
                attempt.updated_at = _naive(cutoff)
                lease.state = "unresolved"
                lease.outcome = "execution_deadline_expired"
                lease.uncertain_charge = True
                lease.completed_at = _naive(cutoff)
                evidence_ref = f"stale-execution:{attempt.id}"
                audit_key = f"stale-sweep:{attempt.id}"
                existing_audit = session.scalar(
                    select(SpendAuditRow).where(
                        SpendAuditRow.action == "stale_sweep",
                        SpendAuditRow.idempotency_key == audit_key,
                    )
                )
                if existing_audit is None:
                    session.add(SpendAuditRow(
                        id=uuid.uuid4().hex,
                        attempt_id=attempt.id,
                        provider=attempt.provider,
                        action="stale_sweep",
                        actor_identity="readiness_authority",
                        idempotency_key=audit_key,
                        request_hash=hashlib.sha256(audit_key.encode()).hexdigest(),
                        before_json=json.dumps(
                            before, sort_keys=True, separators=(",", ":"),
                            default=str,
                        ),
                        after_json=json.dumps(
                            _attempt_state(attempt),
                            sort_keys=True, separators=(",", ":"),
                            default=str,
                        ),
                        created_at=_naive(cutoff),
                    ))

                scope_row = session.scalar(
                    select(ProviderReadinessObservationRow)
                    .where(
                        ProviderReadinessObservationRow.provider == attempt.provider,
                        ProviderReadinessObservationRow.scope_key == lease.scope_key,
                    )
                    .limit(1)
                )
                scope = None
                if scope_row is not None:
                    from argus.broker.readiness import ReadinessScope

                    scope = ReadinessScope(**json.loads(scope_row.scope_json))
                self.record_spend_in_session(
                    session,
                    provider=ProviderName(attempt.provider),
                    state="uncertain",
                    evidence_ref=evidence_ref,
                    outcome="execution_deadline_expired",
                    protected=True,
                    scope=scope,
                )
                swept.append(_attempt(attempt))
            session.flush()
            return tuple(swept)

    def sweep_stale_execution_attempts(
        self, *, now: datetime | float | None = None, limit: int = 128,
    ) -> tuple[Any, ...]:
        """Compatibility alias for the bounded stale-execution sweep."""

        return self.list_stale_execution_attempts(now=now, limit=limit)

    @staticmethod
    def _lock_provider_budget(session, provider: ProviderName) -> None:
        """Serialize every provider/account budget mutation on PostgreSQL."""
        if session.get_bind().dialect.name != "postgresql":
            return
        session.execute(
            text(
                "SELECT pg_advisory_xact_lock("
                "hashtextextended(:account_lock, 0))"
            ),
            {"account_lock": f"provider-budget:{provider.value}"},
        )

    def authorize_execution(
        self, *, context: Any, owner: str, conservative_charge: float,
        execution_timeout_seconds: int,
        decide: Callable[[Mapping[str, Any], Any], Any],
        probe_authorization: Any | None = None,
    ) -> Mapping[str, Any]:
        """Atomically recheck readiness/policy, reserve spend, and fence execution."""
        from argus.persistence.provider_spend import (
            ProviderSpendAttemptRow,
            SpendAuditRow,
        )

        with self._write_transaction() as session:
            now = self.authority_now(session)
            self._lock_provider_budget(session, context.provider)
            if probe_authorization is not None:
                existing_probe = session.scalar(select(SpendAuditRow).where(
                    SpendAuditRow.action == "probe_authorize",
                    SpendAuditRow.idempotency_key
                    == probe_authorization.idempotency_key,
                ))
                if existing_probe is not None:
                    contract = json.loads(existing_probe.after_json)
                    if (
                        contract.get("provider") != context.provider.value
                        or contract.get("receipt")
                        != probe_authorization.durable_receipt
                        or not math.isclose(
                            float(contract.get("conservative_charge", -1)),
                            conservative_charge,
                            rel_tol=0.0,
                            abs_tol=1e-12,
                        )
                    ):
                        raise ReadinessConflict(
                            "probe idempotency key reused with different contract"
                        )
                    consumed_probe = session.scalar(select(SpendAuditRow).where(
                        SpendAuditRow.action.in_(
                            ("probe_consume", "probe_result")
                        ),
                        SpendAuditRow.idempotency_key
                        == probe_authorization.idempotency_key,
                    ))
                    if consumed_probe is not None:
                        return {
                            "allowed": False,
                            "decision": type(decide({}, context))(
                                "unavailable",
                                "probe_already_consumed",
                                ("probe",),
                            ),
                        }
                    attempt = session.get(
                        ProviderSpendAttemptRow,
                        contract["attempt_id"],
                    )
                    lease = session.scalar(
                        select(ProviderReadinessLeaseRow).where(
                            ProviderReadinessLeaseRow.attempt_id
                            == contract["attempt_id"]
                        )
                    )
                    if (
                        attempt is None
                        or attempt.status != "reserved"
                        or lease is None
                        or lease.state != "claimed"
                        or now >= _aware(lease.execution_deadline)
                    ):
                        return {
                            "allowed": False,
                            "decision": type(decide({}, context))(
                                "unavailable",
                                "probe_permission_expired",
                                ("probe",),
                            ),
                        }
                    return {
                        "allowed": True,
                        "decision": type(decide({}, context))(
                            "eligible",
                            "authorized",
                            ("readiness", "policy", "lease"),
                        ),
                        "scope_key": contract["scope_key"],
                        "fencing_token": contract["fencing_token"],
                        "attempt_id": contract["attempt_id"],
                        "owner": contract["owner"],
                    }
            lease_key = f"{context.provider.value}:{context.scope.fingerprint()}"
            lease = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(ProviderReadinessLeaseRow.scope_key == lease_key)
                .with_for_update()
            )
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
                        ProviderSpendAttemptRow.account_fingerprint
                        == context.scope.account_fingerprint,
                        ProviderSpendAttemptRow.status.in_(("reserved", "uncertain")),
                    )
                ) or 0.0
                registration = self.get_registration_in_session(session, context.provider)
                settled_filters = [
                    ProviderSpendAttemptRow.provider == context.provider.value,
                    ProviderSpendAttemptRow.account_fingerprint
                    == context.scope.account_fingerprint,
                    ProviderSpendAttemptRow.status.in_(("settled", "resolved")),
                ]
                period_started_at = registration.get("budget_period_started_at")
                if period_started_at:
                    settled_filters.append(
                        ProviderSpendAttemptRow.created_at
                        >= _naive(datetime.fromisoformat(period_started_at))
                    )
                settled = session.scalar(
                    select(func.coalesce(func.sum(
                        ProviderSpendAttemptRow.actual_charge
                    ), 0.0)).where(*settled_filters)
                ) or 0.0
                obligation = Decimal(str(unresolved)) + Decimal(str(settled))
                limit = float(registration.get("budget_limit") or 0)
                requested = obligation + Decimal(str(conservative_charge))
                if limit <= 0 or requested > Decimal(str(limit)):
                    return {"allowed": False, "decision": type(decision)(
                        "spend_blocked", "exhausted", ("spend",)
                    )}
                session.add(ProviderSpendAttemptRow(
                    id=attempt_id,
                    idempotency_key=context.idempotency_key,
                    request_hash=context.request_hash or context.plan_id,
                    operation_id=context.operation_id,
                    account_fingerprint=context.scope.account_fingerprint,
                    release_identity=context.release_identity
                    or context.release_revision,
                    execution_deadline=_naive(deadline),
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
                    caller_label=context.caller_label,
                    resolution_source=None,
                    resolution_reference=None,
                    created_at=_naive(now),
                    updated_at=_naive(now),
                ))
            else:
                session.add(ProviderSpendAttemptRow(
                    id=attempt_id,
                    idempotency_key=context.idempotency_key,
                    request_hash=context.request_hash or context.plan_id,
                    operation_id=context.operation_id,
                    account_fingerprint=context.scope.account_fingerprint,
                    release_identity=context.release_identity
                    or context.release_revision,
                    execution_deadline=_naive(deadline),
                    provider=context.provider.value,
                    tier=context.tier,
                    is_paid=False,
                    status="reserved",
                    outcome=None,
                    reserved_charge=0.0,
                    estimator_violation=False,
                    reservation_overrun=0.0,
                    actual_charge=None,
                    usage=0.0,
                    caller_identity=context.caller_identity,
                    caller_label=context.caller_label,
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
            if probe_authorization is not None:
                probe_contract = {
                    "provider": context.provider.value,
                    "receipt": probe_authorization.durable_receipt,
                    "no_fallback": True,
                    "attempt_id": attempt_id,
                    "scope": context.scope.as_dict(),
                    "scope_key": lease_key,
                    "owner": owner,
                    "fencing_token": token,
                    "conservative_charge": conservative_charge,
                }
                session.add(SpendAuditRow(
                    id=uuid.uuid4().hex,
                    attempt_id=attempt_id,
                    provider=context.provider.value,
                    action="probe_authorize",
                    actor_identity=probe_authorization.workflow,
                    idempotency_key=str(
                        probe_authorization.idempotency_key
                    ),
                    request_hash=hashlib.sha256(json.dumps(
                        probe_contract,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()).hexdigest(),
                    before_json=None,
                    after_json=json.dumps(
                        probe_contract,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    created_at=_naive(now),
                ))
            return {
                "allowed": True, "decision": type(decision)(
                    "eligible", "authorized", ("readiness", "policy", "lease")
                ),
                "scope_key": lease_key, "fencing_token": token,
                "attempt_id": attempt_id, "owner": owner,
            }

    def probe_authorization_id(self, idempotency_key: str) -> str | None:
        from argus.persistence.provider_spend import SpendAuditRow

        with self.session_factory() as session:
            return session.scalar(select(SpendAuditRow.id).where(
                SpendAuditRow.action == "probe_authorize",
                SpendAuditRow.idempotency_key == idempotency_key,
            ))

    def authorize_probe_once(
        self, *, provider: ProviderName, probe_kind: str, authorization: Any,
    ) -> str:
        from argus.persistence.provider_spend import SpendAuditRow

        payload = {
            "provider": provider.value,
            "probe_kind": probe_kind,
            "workflow": authorization.workflow,
            "receipt": authorization.durable_receipt,
            "no_fallback": True,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        request_hash = hashlib.sha256(encoded.encode()).hexdigest()
        key = str(authorization.idempotency_key)
        with self._write_transaction() as session:
            existing = session.scalar(select(SpendAuditRow).where(
                SpendAuditRow.action == "probe_authorize",
                SpendAuditRow.idempotency_key == key,
            ))
            if existing is not None:
                if existing.request_hash != request_hash:
                    raise ReadinessConflict(
                        "probe idempotency key reused with different contract"
                    )
                return existing.id
            now = self.authority_now(session)
            row_id = uuid.uuid4().hex
            session.add(SpendAuditRow(
                id=row_id,
                attempt_id=None,
                provider=provider.value,
                action="probe_authorize",
                actor_identity=authorization.workflow,
                idempotency_key=key,
                request_hash=request_hash,
                before_json=None,
                after_json=encoded,
                created_at=_naive(now),
            ))
            return row_id

    def consume_probe_once(
        self, *, provider: ProviderName, idempotency_key: str,
        durable_receipt: str, attempt_id: str | None = None,
    ) -> Any | None:
        from argus.broker.readiness import (
            ExecutionAuthorization,
            ExecutionDecision,
            ReadinessScope,
        )
        from argus.persistence.provider_spend import (
            ProviderSpendAttemptRow,
            SpendAuditRow,
        )

        with self._write_transaction() as session:
            authorized = session.scalar(select(SpendAuditRow).where(
                SpendAuditRow.action == "probe_authorize",
                SpendAuditRow.idempotency_key == idempotency_key,
                SpendAuditRow.provider == provider.value,
            ).with_for_update())
            if authorized is None:
                raise ReadinessConflict("probe authorization is missing")
            contract = json.loads(authorized.after_json)
            if (
                contract.get("receipt") != durable_receipt
                or contract.get("no_fallback") is not True
                or contract.get("attempt_id") != attempt_id
            ):
                raise ReadinessConflict("probe authorization binding mismatch")
            execution = None
            if attempt_id is not None:
                attempt = session.get(ProviderSpendAttemptRow, attempt_id)
                if attempt is None or attempt.status != "reserved":
                    raise ReadinessConflict(
                        "probe reservation is missing or not active"
                    )
                lease = session.scalar(select(ProviderReadinessLeaseRow).where(
                    ProviderReadinessLeaseRow.attempt_id == attempt_id
                ).with_for_update())
                if (
                    lease is None
                    or lease.state != "claimed"
                    or self.authority_now(session)
                    >= _aware(lease.execution_deadline)
                ):
                    raise ReadinessConflict("probe execution permission is not active")
                execution = ExecutionAuthorization(
                    allowed=True,
                    decision=ExecutionDecision(
                        "eligible",
                        "authorized",
                        ("readiness", "policy", "lease"),
                    ),
                    provider=provider,
                    scope=ReadinessScope(**contract["scope"]),
                    owner=contract["owner"],
                    scope_key=contract["scope_key"],
                    fencing_token=int(contract["fencing_token"]),
                    attempt_id=attempt_id,
                )
            consumed = session.scalar(select(SpendAuditRow).where(
                SpendAuditRow.action == "probe_consume",
                SpendAuditRow.idempotency_key == idempotency_key,
            ))
            if consumed is not None:
                raise ReadinessConflict("probe authorization already consumed")
            now = self.authority_now(session)
            session.add(SpendAuditRow(
                id=uuid.uuid4().hex,
                attempt_id=attempt_id,
                provider=provider.value,
                action="probe_consume",
                actor_identity="executor",
                idempotency_key=idempotency_key,
                request_hash=authorized.request_hash,
                before_json=authorized.after_json,
                after_json=json.dumps(
                    {"consumed": True}, sort_keys=True, separators=(",", ":")
                ),
                created_at=_naive(now),
            ))
            return execution

    def settle_execution(
        self, *, authorization: Any, category: str, actual_charge: float | None,
        charge_known: bool, termination_known: bool, evidence_ref: str,
        probe_idempotency_key: str | None,
        fold: Callable[[list[StoredObservation], datetime, int], Mapping[str, Any]],
    ) -> Mapping[str, Any]:
        """Settle reservation, lease, terminal evidence and snapshot atomically."""
        from argus.persistence.provider_spend import (
            ProviderSpendAttemptRow,
            SpendAuditRow,
        )

        with self._write_transaction() as session:
            now = self.authority_now(session)
            self._lock_provider_budget(session, authorization.provider)
            lease = session.scalar(
                select(ProviderReadinessLeaseRow)
                .where(ProviderReadinessLeaseRow.scope_key == authorization.scope_key)
                .with_for_update()
            )
            if (
                lease is None or lease.owner != authorization.owner
                or lease.fencing_token != authorization.fencing_token
                or lease.attempt_id != authorization.attempt_id
            ):
                # A stale owner may be relevant to charge reconciliation, but
                # must never settle the current readiness lease/snapshot.
                raise StaleFencingToken("execution fencing token is stale")
            uncertain = not charge_known
            unresolved = uncertain or not termination_known
            outcome = (
                (category or "success")
                if termination_known
                else "termination_indeterminate"
            )
            resolving_matching_uncertainty = (
                lease.state == "unresolved" and not unresolved
            )
            if lease.state == "completed":
                if lease.outcome != outcome or lease.uncertain_charge != uncertain:
                    raise ReadinessConflict("completion replay has a different outcome")
            elif lease.state == "unresolved":
                if not resolving_matching_uncertainty and (
                    lease.outcome != outcome
                    or lease.uncertain_charge != uncertain
                ):
                    raise ReadinessConflict("completion replay has a different outcome")
                if resolving_matching_uncertainty:
                    lease.state = "completed"
                    lease.outcome = outcome
                    lease.uncertain_charge = False
                    lease.completed_at = _naive(now)
            elif lease.state != "claimed":
                raise StaleFencingToken("execution claim is no longer active")
            else:
                lease.state = "unresolved" if unresolved else "completed"
                lease.outcome = outcome
                lease.uncertain_charge = uncertain
                lease.completed_at = _naive(now)
            attempt = session.get(ProviderSpendAttemptRow, authorization.attempt_id)
            if attempt is not None and attempt.status in {"reserved", "uncertain"}:
                if (
                    attempt.status == "uncertain"
                    and not resolving_matching_uncertainty
                ):
                    pass
                elif unresolved:
                    attempt.status = "uncertain"
                    attempt.outcome = outcome
                else:
                    attempt.status = "settled"
                    attempt.outcome = outcome
                    attempt.actual_charge = float(actual_charge or 0.0)
                    attempt.usage = (
                        1.0
                        if not attempt.is_paid and outcome == "success"
                        else float(actual_charge or 0.0)
                    )
                    if attempt.actual_charge > attempt.reserved_charge:
                        attempt.estimator_violation = True
                        attempt.reservation_overrun = float(
                            Decimal(str(attempt.actual_charge))
                            - Decimal(str(attempt.reserved_charge))
                        )
                attempt.updated_at = _naive(now)
            if probe_idempotency_key is not None:
                consumed_probe = session.scalar(select(SpendAuditRow).where(
                    SpendAuditRow.action == "probe_consume",
                    SpendAuditRow.idempotency_key == probe_idempotency_key,
                    SpendAuditRow.attempt_id == authorization.attempt_id,
                ).with_for_update())
                if consumed_probe is None:
                    raise ReadinessConflict(
                        "probe result requires the exact consumed attempt"
                    )
                result_payload = json.dumps({
                    "attempt_id": authorization.attempt_id,
                    "outcome": outcome,
                    "termination_known": termination_known,
                    "charge_known": charge_known,
                    "actual_charge": actual_charge,
                    "evidence_ref": evidence_ref,
                }, sort_keys=True, separators=(",", ":"))
                prior_result = session.scalar(select(SpendAuditRow).where(
                    SpendAuditRow.action == "probe_result",
                    SpendAuditRow.idempotency_key == probe_idempotency_key,
                ))
                if prior_result is None:
                    session.add(SpendAuditRow(
                        id=uuid.uuid4().hex,
                        attempt_id=authorization.attempt_id,
                        provider=authorization.provider.value,
                        action="probe_result",
                        actor_identity="executor",
                        idempotency_key=probe_idempotency_key,
                        request_hash=hashlib.sha256(
                            result_payload.encode()
                        ).hexdigest(),
                        before_json=consumed_probe.after_json,
                        after_json=result_payload,
                        created_at=_naive(now),
                    ))
                elif prior_result.after_json != result_payload:
                    raise ReadinessConflict(
                        "probe result replay has a different outcome"
                    )
            from argus.broker.budgets import PROVIDER_TIERS
            state = (
                "not_applicable"
                if PROVIDER_TIERS[authorization.provider] == 0
                else "exhausted" if category == "balance_exhausted"
                else "uncertain" if uncertain
                else "available"
            )
            self.record_spend_in_session(
                session,
                provider=authorization.provider,
                state=state,
                evidence_ref=evidence_ref,
                outcome=(
                    f"reconciled_{outcome}"
                    if resolving_matching_uncertainty
                    else outcome
                ),
                protected=state in {"uncertain", "exhausted"},
                scope=authorization.scope,
                replace_protected_uncertain=resolving_matching_uncertainty,
            )
            scope_key = authorization.scope.fingerprint()
            snapshot_row = session.scalar(
                select(ProviderReadinessSnapshotRow).where(
                    ProviderReadinessSnapshotRow.provider
                    == authorization.provider.value,
                    ProviderReadinessSnapshotRow.scope_key == scope_key,
                )
            )
            payload = json.loads(snapshot_row.snapshot_json) if snapshot_row else {}
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
        scope: Any | None = None,
        replace_protected_uncertain: bool = False,
        replace_protected_exhausted: bool = False,
    ) -> None:
        """Fold spend reconciliation into readiness in the caller transaction."""
        from argus.broker.readiness import (
            ProviderReadinessService,
            ReadinessScope,
        )

        registration = self.get_registration_in_session(session, provider)
        if scope is None and not registration.get("scope"):
            return
        primary_scope = scope or ReadinessScope(**registration["scope"])
        targets = {primary_scope.fingerprint(): primary_scope}
        account_wide = (
            state in {"exhausted", "uncertain"}
            or replace_protected_uncertain
            or replace_protected_exhausted
        )
        if account_wide:
            account = primary_scope.account_fingerprint
            for value in registration.get("scopes") or ():
                candidate = ReadinessScope(**value)
                if candidate.account_fingerprint == account:
                    targets[candidate.fingerprint()] = candidate
            for row in session.scalars(
                select(ProviderReadinessObservationRow).where(
                    ProviderReadinessObservationRow.provider == provider.value
                )
            ):
                candidate = ReadinessScope(**json.loads(row.scope_json))
                if candidate.account_fingerprint == account:
                    targets[candidate.fingerprint()] = candidate
        now = self.authority_now(session)
        service = ProviderReadinessService(repository=self)
        for target_scope in targets.values():
            scope_key = target_scope.fingerprint()
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
                current_is_active = (
                    current.expires_at is None
                    or now < _aware(current.expires_at)
                )
                if (
                    current.protected
                    and current.state == "exhausted"
                    and current_is_active
                    and state != "exhausted"
                    and not replace_protected_exhausted
                ):
                    continue
                if (
                    current.protected
                    and current.state == "uncertain"
                    and state == "available"
                    and not replace_protected_uncertain
                ):
                    continue
                if (
                    current.evidence_ref
                    and (
                        not current.protected
                        or (
                            current.state == "uncertain"
                            and replace_protected_uncertain
                        )
                    )
                ):
                    superseded_evidence = session.scalar(
                        select(ProviderReadinessEvidenceRefRow).where(
                            ProviderReadinessEvidenceRefRow.provider
                            == provider.value,
                            ProviderReadinessEvidenceRefRow.evidence_ref
                            == current.evidence_ref,
                        )
                    )
                    if superseded_evidence is not None:
                        superseded_evidence.protected = False
                session.delete(current)
                session.flush()
            observation_id = uuid.uuid4().hex
            session.add(ProviderReadinessObservationRow(
                id=observation_id, provider=provider.value, dimension="spend",
                state=state, source="provider_spend_transaction",
                scope_key=scope_key,
                scope_json=json.dumps(
                    target_scope.as_dict(),
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                producer_observed_at=_naive(now), ingested_at=_naive(now),
                expires_at=None, evidence_ref=evidence_ref,
                safe_reason=outcome, protected=protected,
            ))
            existing = session.scalar(
                select(ProviderReadinessEvidenceRefRow).where(
                    ProviderReadinessEvidenceRefRow.provider == provider.value,
                    ProviderReadinessEvidenceRefRow.evidence_ref == evidence_ref,
                )
            )
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
            session.flush()
            rows = self._current_rows(session, provider, scope_key)
            generation = self._next_generation(session, provider, scope_key)
            payload = service._fold(
                provider,
                target_scope,
                [_stored(row) for row in rows],
                now,
                generation,
            ).as_dict()
            self._put_snapshot(
                session, provider, scope_key, payload, generation, now
            )
        self._compact_evidence(session, provider)

    def resolve_spend_in_session(
        self, session, *, attempt: Any, outcome: str, evidence_ref: str,
        authoritative_balance: bool = False,
    ) -> None:
        """Resolve attempt, lease, protected evidence and snapshot atomically."""
        lease = session.scalar(
            select(ProviderReadinessLeaseRow)
            .where(ProviderReadinessLeaseRow.attempt_id == attempt.id)
            .with_for_update()
        )
        now = self.authority_now(session)
        scope = None
        if lease is not None:
            lease.state = "completed"
            lease.outcome = outcome
            lease.uncertain_charge = False
            lease.completed_at = _naive(now)
            scope_row = session.scalar(
                select(ProviderReadinessObservationRow).where(
                    ProviderReadinessObservationRow.provider == attempt.provider,
                    ProviderReadinessObservationRow.scope_key == lease.scope_key,
                )
            )
            if scope_row is not None:
                from argus.broker.readiness import ReadinessScope

                scope = ReadinessScope(**json.loads(scope_row.scope_json))
        state = "exhausted" if outcome == "balance_exhausted" else "available"
        self.record_spend_in_session(
            session,
            provider=ProviderName(attempt.provider),
            state=state,
            evidence_ref=evidence_ref,
            outcome=(
                f"authoritative_balance_{outcome}"
                if authoritative_balance
                else f"reconciled_{outcome}"
            ),
            protected=state == "exhausted",
            scope=scope,
            replace_protected_uncertain=True,
            replace_protected_exhausted=authoritative_balance,
        )

    def protected_exhaustion_in_session(
        self,
        session,
        provider: ProviderName,
        *,
        account_fingerprint: str | None = None,
    ) -> bool:
        """Return whether the registered account has current protected exhaustion."""
        registration = self.get_registration_in_session(session, provider)
        scope = registration.get("scope") or {}
        account = account_fingerprint or scope.get("account_fingerprint")
        # Legacy direct callers did not carry account identity. Preserve their
        # historical binding to the registered account while keeping explicit
        # account fingerprints isolated.
        if account == "legacy-account" and scope.get("account_fingerprint"):
            account = scope["account_fingerprint"]
        if not account:
            return False
        now = self.authority_now(session)
        rows = session.scalars(
            select(ProviderReadinessObservationRow)
            .where(
                ProviderReadinessObservationRow.provider == provider.value,
                ProviderReadinessObservationRow.dimension == "spend",
                ProviderReadinessObservationRow.state == "exhausted",
                ProviderReadinessObservationRow.protected.is_(True),
            )
            .with_for_update()
        )
        return any(
            json.loads(row.scope_json).get("account_fingerprint") == account
            and (row.expires_at is None or now < _aware(row.expires_at))
            for row in rows
        )

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
        account_fingerprint: str | None = None,
    ) -> dict[str, float | None]:
        """Project authoritative settled and unresolved obligations using DB time."""
        from argus.persistence.provider_spend import ProviderSpendAttemptRow

        with self.session_factory() as session:
            registration = self.get_registration_in_session(session, provider)
            settled_filters = [
                ProviderSpendAttemptRow.provider == provider.value,
                ProviderSpendAttemptRow.status.in_(("settled", "resolved")),
            ]
            if account_fingerprint is not None:
                settled_filters.append(
                    ProviderSpendAttemptRow.account_fingerprint
                    == account_fingerprint
                )
            period_started_at = registration.get("budget_period_started_at")
            if period_started_at:
                settled_filters.append(
                    ProviderSpendAttemptRow.created_at
                    >= _naive(datetime.fromisoformat(period_started_at))
                )
            settled = session.scalar(
                select(func.coalesce(func.sum(
                    ProviderSpendAttemptRow.actual_charge
                ), 0.0)).where(*settled_filters)
            ) or 0.0
            uncertain_filters = [
                ProviderSpendAttemptRow.provider == provider.value,
                ProviderSpendAttemptRow.status.in_(("reserved", "uncertain")),
            ]
            if account_fingerprint is not None:
                uncertain_filters.append(
                    ProviderSpendAttemptRow.account_fingerprint
                    == account_fingerprint
                )
            uncertain = session.scalar(
                select(func.coalesce(func.sum(
                    ProviderSpendAttemptRow.reserved_charge
                    + ProviderSpendAttemptRow.reservation_overrun
                ), 0.0)).where(*uncertain_filters)
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
        from argus.broker.readiness import (
            ProviderObservation,
            ProviderReadinessService,
            ReadinessScope,
        )

        with self._write_transaction() as session:
            now = self.authority_now(session)
            exact_reset = None
            if recurring:
                if reset_at is None or _aware(reset_at) <= now:
                    raise ValueError(
                        "recurring exhaustion requires a future reset"
                    )
                exact_reset = _aware(reset_at)
                if (
                    exact_reset
                    > now + timedelta(seconds=MAX_EXACT_EXPIRY_SECONDS)
                ):
                    raise ValueError(
                        "recurring exhaustion reset exceeds one-year bound"
                    )
            elif reset_at is not None:
                raise ValueError(
                    "one-time exhaustion cannot have an automatic reset"
                )
            registration = self.get_registration_in_session(
                session,
                provider,
            )
            scope_values = registration.get("scopes") or (
                [registration["scope"]] if registration.get("scope") else []
            )
            if not scope_values:
                scope_values = [
                    ReadinessScope(
                        account_fingerprint=account_fingerprint
                    ).as_dict()
                ]
            observations = []
            for value in scope_values:
                scope_data = dict(value)
                scope_data["account_fingerprint"] = account_fingerprint
                observations.append(ProviderObservation(
                    provider=provider,
                    dimension="spend",
                    state="exhausted",
                    source="provider_authoritative",
                    scope=ReadinessScope(**scope_data),
                    observed_at=now,
                    ttl_seconds=None,
                    evidence_ref=evidence_ref,
                    protected=True,
                    safe_reason=(
                        "recurring_quota_exhausted"
                        if recurring
                        else "one_time_credit_exhausted"
                    ),
                    expires_at=exact_reset,
                ))
            service = ProviderReadinessService(repository=self)
            for observation in observations:
                self.record_and_materialize(
                    observation,
                    lambda rows, fold_now, generation, scope=observation.scope: (
                        service._fold(
                            provider,
                            scope,
                            rows,
                            fold_now,
                            generation,
                        ).as_dict()
                    ),
                    _session=session,
                    _now=now,
                    _allow_exact_expiry=recurring,
                )

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
