"""Canonical, durable domain-routing policy state.

The extraction layer talks to this module through detached immutable values.
The ORM declarations are deliberately kept in their own metadata base so the
production registry can own them explicitly while the older SQLite models
remain a compatibility-only surface.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

from sqlalchemy import (
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    case,
    create_engine,
    false,
    func,
    select,
    update,
)
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from argus.config import get_config


UTC = timezone.utc
DOMAIN_POLICY_PREFERENCE_THRESHOLD = 3
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 0.01
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9-]+$")
_POLICY_COLUMNS = (
    "domain",
    "prefer_residential_search",
    "prefer_residential_extraction",
    "datacenter_failure_count",
    "residential_success_count",
    "last_datacenter_failure",
    "last_residential_success",
    "failure_reason",
    "updated_at",
    "version",
)


class DomainPolicyBase(DeclarativeBase):
    """Metadata owned by the canonical domain-policy repository."""


class DomainPolicyRow(DomainPolicyBase):
    """The current policy for one normalized domain."""

    __tablename__ = "domain_policies"
    __table_args__ = (Index("ix_domain_policies_domain", "domain"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    domain: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    prefer_residential_search: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    prefer_residential_extraction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    datacenter_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    residential_success_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_datacenter_failure: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    last_residential_success: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.now()
    )
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )


class DomainPolicyEvent(DomainPolicyBase):
    """Durable idempotency record for one domain-policy command.

    ``result_json`` is nullable only while a transaction is in progress.  The
    event and its result are committed together, so a committed row always
    contains a replayable value.
    """

    __tablename__ = "domain_policy_events"
    __table_args__ = (
        UniqueConstraint(
            "event_identity",
            name="uq_domain_policy_event_identity",
        ),
        Index("ix_domain_policy_events_domain", "domain"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    event_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    domain: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


# Compatibility names make the ownership boundary easy to discover without
# exposing a second production declaration.
CanonicalDomainPolicyRow = DomainPolicyRow
DomainPolicyEventRow = DomainPolicyEvent


@dataclass(frozen=True, slots=True)
class DomainPolicyValue:
    """Detached immutable policy state returned to extraction callers."""

    domain: str
    prefer_residential_search: bool
    prefer_residential_extraction: bool
    datacenter_failure_count: int
    residential_success_count: int
    last_datacenter_failure: datetime | None
    last_residential_success: datetime | None
    failure_reason: str | None
    updated_at: datetime
    version: int

    def __post_init__(self) -> None:
        if self.last_datacenter_failure is not None:
            object.__setattr__(
                self,
                "last_datacenter_failure",
                _as_utc(self.last_datacenter_failure),
            )
        if self.last_residential_success is not None:
            object.__setattr__(
                self,
                "last_residential_success",
                _as_utc(self.last_residential_success),
            )
        object.__setattr__(self, "updated_at", _as_utc(self.updated_at))

    def as_dict(self) -> dict[str, Any]:
        """Return a canonical JSON-safe representation for event replay."""
        return {
            "domain": self.domain,
            "prefer_residential_search": self.prefer_residential_search,
            "prefer_residential_extraction": self.prefer_residential_extraction,
            "datacenter_failure_count": self.datacenter_failure_count,
            "residential_success_count": self.residential_success_count,
            "last_datacenter_failure": _iso(self.last_datacenter_failure),
            "last_residential_success": _iso(self.last_residential_success),
            "failure_reason": self.failure_reason,
            "updated_at": _iso(self.updated_at),
            "version": self.version,
        }


@dataclass(frozen=True, slots=True)
class DomainPolicyEventValue:
    """Detached event metadata useful for reconciliation and diagnostics."""

    event_identity: str
    domain: str
    event_type: str
    request_hash: str
    result: DomainPolicyValue
    created_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "created_at", _as_utc(self.created_at))


class DomainPolicyConflictError(RuntimeError):
    """An event identity was reused with a different request payload."""


# Friendly aliases for callers that used the terminology from the ADR.
DomainPolicyEventConflict = DomainPolicyConflictError
DomainPolicyRequestConflict = DomainPolicyConflictError


class DomainPolicyPersistenceError(RuntimeError):
    """A durable policy event could not be completed or reconciled."""


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        raise TypeError("domain policy timestamps must be datetime values")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _storage_datetime(value: datetime) -> datetime:
    return _as_utc(value).replace(tzinfo=None)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _as_utc(parsed)


def normalize_domain(value: object) -> str | None:
    """Normalize a hostname once at the persistence boundary.

    Invalid, local, credential-bearing, or non-host inputs return ``None``.
    This makes routing decisions fail safe without turning malformed telemetry
    into a persistence exception.
    """
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    try:
        if "://" in candidate:
            parsed = urlsplit(candidate)
            if parsed.scheme.lower() not in {"http", "https"}:
                return None
            if parsed.username is not None or parsed.password is not None:
                return None
            host = parsed.hostname
        else:
            if any(character in candidate for character in "/?#"):
                return None
            host = candidate
            # ``urlsplit`` treats an unbracketed colon as a port delimiter only
            # when a scheme is present.  Accept a plain hostname:port safely.
            if host.count(":") == 1:
                possible_host, possible_port = host.rsplit(":", 1)
                if possible_port.isdigit():
                    host = possible_host
        if not host:
            return None
        host = host.rstrip(".").lower()
        if not host or host == "localhost":
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            return None
        ascii_host = host.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return None

    if len(ascii_host) > 253 or "." not in ascii_host:
        return None
    labels = ascii_host.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or _DOMAIN_LABEL_RE.fullmatch(label) is None
        for label in labels
    ):
        return None
    return ascii_host


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _request_fingerprint(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _value_from_row(row: Any) -> DomainPolicyValue:
    return DomainPolicyValue(
        domain=row.domain,
        prefer_residential_search=bool(row.prefer_residential_search),
        prefer_residential_extraction=bool(row.prefer_residential_extraction),
        datacenter_failure_count=int(row.datacenter_failure_count),
        residential_success_count=int(row.residential_success_count),
        last_datacenter_failure=(
            _as_utc(row.last_datacenter_failure)
            if row.last_datacenter_failure is not None
            else None
        ),
        last_residential_success=(
            _as_utc(row.last_residential_success)
            if row.last_residential_success is not None
            else None
        ),
        failure_reason=row.failure_reason,
        updated_at=_as_utc(row.updated_at),
        version=int(row.version),
    )


def _value_from_mapping(mapping: Any) -> DomainPolicyValue:
    def get(name: str) -> Any:
        try:
            return mapping[name]
        except (KeyError, TypeError):
            return getattr(mapping, name)

    return DomainPolicyValue(
        domain=str(get("domain")),
        prefer_residential_search=bool(get("prefer_residential_search")),
        prefer_residential_extraction=bool(get("prefer_residential_extraction")),
        datacenter_failure_count=int(get("datacenter_failure_count")),
        residential_success_count=int(get("residential_success_count")),
        last_datacenter_failure=(
            _as_utc(get("last_datacenter_failure"))
            if get("last_datacenter_failure") is not None
            else None
        ),
        last_residential_success=(
            _as_utc(get("last_residential_success"))
            if get("last_residential_success") is not None
            else None
        ),
        failure_reason=get("failure_reason"),
        updated_at=_as_utc(get("updated_at")),
        version=int(get("version")),
    )


def _value_from_json(payload: str) -> DomainPolicyValue:
    try:
        data = json.loads(payload)
        return DomainPolicyValue(
            domain=str(data["domain"]),
            prefer_residential_search=bool(data["prefer_residential_search"]),
            prefer_residential_extraction=bool(data["prefer_residential_extraction"]),
            datacenter_failure_count=int(data["datacenter_failure_count"]),
            residential_success_count=int(data["residential_success_count"]),
            last_datacenter_failure=_parse_iso(data["last_datacenter_failure"]),
            last_residential_success=_parse_iso(data["last_residential_success"]),
            failure_reason=data["failure_reason"],
            updated_at=_parse_iso(data["updated_at"]),
            version=int(data["version"]),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DomainPolicyPersistenceError(
            "durable domain-policy event has an invalid replay value"
        ) from exc


class DomainPolicyRepository:
    """SQL repository with atomic, idempotent domain-policy commands."""

    def __init__(
        self,
        session_factory: sessionmaker | None = None,
        *,
        factory: sessionmaker | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        self.session_factory = session_factory or factory
        if self.session_factory is None:
            raise TypeError("session_factory is required")
        if max_retries < 0:
            raise ValueError("max_retries must be non-negative")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")
        self.clock = clock
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds

    def get_policy(self, domain: object) -> DomainPolicyValue | None:
        normalized = normalize_domain(domain)
        if normalized is None:
            return None
        with self.session_factory() as session:
            row = session.scalar(
                select(DomainPolicyRow).where(DomainPolicyRow.domain == normalized)
            )
            return _value_from_row(row) if row is not None else None

    def get_event(self, event_identity: object) -> DomainPolicyEventValue | None:
        if not isinstance(event_identity, str) or not event_identity:
            return None
        with self.session_factory() as session:
            row = session.scalar(
                select(DomainPolicyEvent).where(
                    DomainPolicyEvent.event_identity == event_identity
                )
            )
            if row is None or row.result_json is None:
                return None
            return DomainPolicyEventValue(
                event_identity=row.event_identity,
                domain=row.domain,
                event_type=row.event_type,
                request_hash=row.request_hash,
                result=_value_from_json(row.result_json),
                created_at=_as_utc(row.created_at),
            )

    def count_events(self, event_identity: str | None = None) -> int:
        from sqlalchemy import func

        with self.session_factory() as session:
            statement = select(func.count()).select_from(DomainPolicyEvent)
            if event_identity is not None:
                statement = statement.where(
                    DomainPolicyEvent.event_identity == event_identity
                )
            return int(session.scalar(statement) or 0)

    def record_datacenter_failure(
        self,
        domain: object,
        reason: str | None = None,
        *,
        event_identity: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        occurred_at: datetime | None = None,
        event_at: datetime | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> DomainPolicyValue:
        if event_at is not None:
            if occurred_at is not None:
                raise ValueError("occurred_at and event_at are mutually exclusive")
            occurred_at = event_at
        return self._record(
            domain,
            event_type="datacenter_failure",
            reason=reason,
            event_identity=event_identity,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            fault_hook=fault_hook,
        )

    def record_residential_success(
        self,
        domain: object,
        *,
        event_identity: str | None = None,
        idempotency_key: str | None = None,
        request_hash: str | None = None,
        occurred_at: datetime | None = None,
        event_at: datetime | None = None,
        fault_hook: Callable[[str], None] | None = None,
    ) -> DomainPolicyValue:
        if event_at is not None:
            if occurred_at is not None:
                raise ValueError("occurred_at and event_at are mutually exclusive")
            occurred_at = event_at
        return self._record(
            domain,
            event_type="residential_success",
            reason=None,
            event_identity=event_identity,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            occurred_at=occurred_at,
            fault_hook=fault_hook,
        )

    def _record(
        self,
        domain: object,
        *,
        event_type: str,
        reason: str | None,
        event_identity: str | None,
        idempotency_key: str | None,
        request_hash: str | None,
        occurred_at: datetime | None,
        fault_hook: Callable[[str], None] | None,
    ) -> DomainPolicyValue:
        normalized = normalize_domain(domain)
        if normalized is None:
            raise ValueError("domain must be a valid public hostname")
        if event_identity is not None and idempotency_key is not None:
            if event_identity != idempotency_key:
                raise ValueError("event_identity and idempotency_key must match")
        identity = event_identity or idempotency_key or uuid.uuid4().hex
        if not isinstance(identity, str) or not identity or len(identity) > 255:
            raise ValueError("event_identity must be a non-empty bounded string")
        payload = {
            "domain": normalized,
            "event_type": event_type,
            "reason": reason,
        }
        fingerprint = request_hash or _request_fingerprint(payload)
        if not isinstance(fingerprint, str) or not fingerprint or len(fingerprint) > 64:
            raise ValueError("request_hash must be a non-empty bounded string")
        now = _as_utc(occurred_at) if occurred_at is not None else None

        for attempt in range(self.max_retries + 1):
            try:
                return self._record_once(
                    normalized,
                    event_type=event_type,
                    reason=reason,
                    event_identity=identity,
                    request_hash=fingerprint,
                    occurred_at=now,
                    fault_hook=fault_hook,
                )
            except Exception as exc:
                if (
                    self._is_retryable_database_error(exc)
                    and attempt < self.max_retries
                ):
                    if self.retry_delay_seconds:
                        time.sleep(
                            min(
                                self.retry_delay_seconds * (2**attempt),
                                0.25,
                            )
                        )
                    continue
                if self._is_ambiguous_database_error(exc):
                    reconciled = self._reconcile_event(identity, fingerprint)
                    if reconciled is not None:
                        return reconciled
                raise
        raise AssertionError("bounded domain-policy retry loop exhausted")

    def _record_once(
        self,
        domain: str,
        *,
        event_type: str,
        reason: str | None,
        event_identity: str,
        request_hash: str,
        occurred_at: datetime | None,
        fault_hook: Callable[[str], None] | None,
    ) -> DomainPolicyValue:
        with self._write_transaction() as session:
            now = occurred_at or _as_utc(self.clock())
            stored_now = _storage_datetime(now)
            event_insert = self._event_insert_statement(
                session,
                event_identity=event_identity,
                domain=domain,
                event_type=event_type,
                request_hash=request_hash,
                created_at=stored_now,
            )
            inserted = session.execute(event_insert).first()
            if inserted is None:
                event = session.scalar(
                    select(DomainPolicyEvent)
                    .where(DomainPolicyEvent.event_identity == event_identity)
                    .with_for_update()
                )
                if event is None:
                    raise DomainPolicyPersistenceError(
                        "domain-policy event conflict was not visible"
                    )
                self._verify_event(event, domain, event_type, request_hash)
                if event.result_json is None:
                    raise DomainPolicyPersistenceError(
                        "domain-policy event has no committed replay value"
                    )
                return _value_from_json(event.result_json)

            policy_statement = self._policy_upsert_statement(
                session,
                domain=domain,
                event_type=event_type,
                reason=reason,
                now=stored_now,
            )
            result_row = session.execute(policy_statement).first()
            if result_row is None:
                raise DomainPolicyPersistenceError(
                    "domain-policy upsert returned no policy value"
                )
            value = _value_from_mapping(result_row._mapping)
            if fault_hook is not None:
                fault_hook("after_policy_upsert")
            session.execute(
                update(DomainPolicyEvent)
                .where(DomainPolicyEvent.event_identity == event_identity)
                .values(result_json=_canonical_json(value.as_dict()))
            )
            if fault_hook is not None:
                fault_hook("after_event_result")
            return value

    def _event_insert_statement(
        self,
        session: Any,
        *,
        event_identity: str,
        domain: str,
        event_type: str,
        request_hash: str,
        created_at: datetime,
    ) -> Any:
        values = {
            "id": uuid.uuid4().hex,
            "event_identity": event_identity,
            "domain": domain,
            "event_type": event_type,
            "request_hash": request_hash,
            "result_json": None,
            "created_at": created_at,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise RuntimeError(
                "domain-policy persistence requires PostgreSQL or SQLite"
            )
        return (
            insert(DomainPolicyEvent)
            .values(**values)
            .on_conflict_do_nothing(index_elements=["event_identity"])
            .returning(DomainPolicyEvent.event_identity)
        )

    def _policy_upsert_statement(
        self,
        session: Any,
        *,
        domain: str,
        event_type: str,
        reason: str | None,
        now: datetime,
    ) -> Any:
        """Build the one database-native policy mutation statement."""
        is_failure = event_type == "datacenter_failure"
        is_success = event_type == "residential_success"
        if not is_failure and not is_success:
            raise ValueError(f"unknown domain-policy event type: {event_type}")
        values = {
            "domain": domain,
            "prefer_residential_search": is_success,
            "prefer_residential_extraction": is_success,
            "datacenter_failure_count": 1 if is_failure else 0,
            "residential_success_count": 1 if is_success else 0,
            "last_datacenter_failure": now if is_failure else None,
            "last_residential_success": now if is_success else None,
            "failure_reason": reason if is_failure else None,
            "updated_at": now,
            "version": 1,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            raise RuntimeError(
                "domain-policy persistence requires PostgreSQL or SQLite"
            )
        statement = insert(DomainPolicyRow).values(**values)
        current_failure_count = DomainPolicyRow.datacenter_failure_count
        current_search_preference = DomainPolicyRow.prefer_residential_search
        current_extraction_preference = DomainPolicyRow.prefer_residential_extraction
        update_values = {
            "datacenter_failure_count": (
                current_failure_count + 1 if is_failure else current_failure_count
            ),
            "residential_success_count": (
                DomainPolicyRow.residential_success_count + 1
                if is_success
                else DomainPolicyRow.residential_success_count
            ),
            "prefer_residential_search": (
                case(
                    (
                        current_failure_count + 1 >= DOMAIN_POLICY_PREFERENCE_THRESHOLD,
                        True,
                    ),
                    else_=current_search_preference,
                )
                if is_failure
                else True
            ),
            "prefer_residential_extraction": (
                case(
                    (
                        current_failure_count + 1 >= DOMAIN_POLICY_PREFERENCE_THRESHOLD,
                        True,
                    ),
                    else_=current_extraction_preference,
                )
                if is_failure
                else True
            ),
            "last_datacenter_failure": (
                statement.excluded.last_datacenter_failure
                if is_failure
                else DomainPolicyRow.last_datacenter_failure
            ),
            "last_residential_success": (
                statement.excluded.last_residential_success
                if is_success
                else DomainPolicyRow.last_residential_success
            ),
            "failure_reason": (
                statement.excluded.failure_reason
                if is_failure
                else DomainPolicyRow.failure_reason
            ),
            "updated_at": statement.excluded.updated_at,
            "version": DomainPolicyRow.version + 1,
        }
        return statement.on_conflict_do_update(
            index_elements=[DomainPolicyRow.domain],
            set_=update_values,
        ).returning(*[getattr(DomainPolicyRow, name) for name in _POLICY_COLUMNS])

    @staticmethod
    def _verify_event(
        event: DomainPolicyEvent,
        domain: str,
        event_type: str,
        request_hash: str,
    ) -> None:
        if event.request_hash != request_hash:
            raise DomainPolicyConflictError(
                "event identity reused with a different request hash"
            )
        if event.domain != domain or event.event_type != event_type:
            raise DomainPolicyConflictError(
                "event identity reused for a different domain-policy command"
            )

    def _reconcile_event(
        self,
        event_identity: str,
        request_hash: str,
    ) -> DomainPolicyValue | None:
        try:
            with self.session_factory() as session:
                event = session.scalar(
                    select(DomainPolicyEvent).where(
                        DomainPolicyEvent.event_identity == event_identity
                    )
                )
                if event is None:
                    return None
                if event.request_hash != request_hash:
                    raise DomainPolicyConflictError(
                        "event identity reused with a different request hash"
                    )
                return (
                    _value_from_json(event.result_json)
                    if event.result_json is not None
                    else None
                )
        except DomainPolicyConflictError:
            raise
        except Exception:
            return None

    @staticmethod
    def _is_retryable_database_error(error: BaseException) -> bool:
        current: BaseException | None = error
        while current is not None:
            code = getattr(current, "pgcode", None) or getattr(
                current, "sqlstate", None
            )
            if code in {"40001", "40P01"}:
                return True
            message = str(current).lower()
            if any(
                marker in message
                for marker in (
                    "serialization failure",
                    "could not serialize access",
                    "deadlock detected",
                    "database is locked",
                )
            ):
                return True
            current = current.__cause__ or current.__context__
        return False

    @staticmethod
    def _is_ambiguous_database_error(error: BaseException) -> bool:
        if isinstance(error, DBAPIError) and error.connection_invalidated:
            return True
        message = str(error).lower()
        return any(
            marker in message
            for marker in (
                "connection is closed",
                "server closed the connection",
                "connection reset",
                "lost connection",
                "commit status unknown",
            )
        )

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


def create_domain_policy_repository(
    db_url: str | None = None,
    *,
    create_schema: bool | None = None,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> DomainPolicyRepository:
    """Build the canonical repository.

    SQLite is a standalone development adapter and may create its local
    schema. PostgreSQL schema changes remain Alembic-only.
    """
    url = db_url or get_config().db_url
    is_sqlite = url.startswith("sqlite:")
    if is_sqlite and url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    from argus.persistence.search_ledger import _bounded_engine_options

    engine = create_engine(
        url,
        pool_pre_ping=True,
        **_bounded_engine_options(url),
    )
    should_create = is_sqlite if create_schema is None else create_schema
    if should_create:
        if not is_sqlite:
            raise ValueError("runtime schema creation is only supported for SQLite")
        DomainPolicyBase.metadata.create_all(engine)
    return DomainPolicyRepository(
        sessionmaker(bind=engine, expire_on_commit=False),
        clock=clock,
        max_retries=max_retries,
        retry_delay_seconds=retry_delay_seconds,
    )


__all__ = [
    "CanonicalDomainPolicyRow",
    "DOMAIN_POLICY_PREFERENCE_THRESHOLD",
    "DomainPolicyBase",
    "DomainPolicyConflictError",
    "DomainPolicyEvent",
    "DomainPolicyEventConflict",
    "DomainPolicyEventRow",
    "DomainPolicyEventValue",
    "DomainPolicyPersistenceError",
    "DomainPolicyRepository",
    "DomainPolicyRequestConflict",
    "DomainPolicyRow",
    "DomainPolicyValue",
    "create_domain_policy_repository",
    "normalize_domain",
]
