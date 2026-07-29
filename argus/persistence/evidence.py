"""Normalized, transactionally accepted retrieval-evidence storage."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from collections.abc import Mapping
from typing import Callable

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column, sessionmaker

from argus.broker.accepted import AcceptanceReceipt, AcceptedRetrieval
from argus.persistence.search_ledger import LedgerBase, _system_now


class RetrievalEvidencePlanRow(LedgerBase):
    __tablename__ = "retrieval_evidence_plans"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    cache_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_cohort: Mapped[str] = mapped_column(String(128), nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RetrievalEvidenceBatchRow(LedgerBase):
    __tablename__ = "retrieval_evidence_provider_batches"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_plans.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "plan_id", "provider", "ordinal", name="uq_retrieval_evidence_batch"
        ),
    )


class RetrievalEvidenceAttemptRow(LedgerBase):
    __tablename__ = "retrieval_evidence_provider_attempts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    batch_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_provider_batches.id"), nullable=False
    )
    attempt_ref: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    attempt_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint("batch_id", "ordinal", name="uq_retrieval_evidence_attempt"),
    )


class RetrievalEvidenceObservationRow(LedgerBase):
    __tablename__ = "retrieval_evidence_observations"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_plans.id"), nullable=False
    )
    observation_ref: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    observation_json: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalEvidenceClusterRow(LedgerBase):
    __tablename__ = "retrieval_evidence_clusters"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_plans.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    cluster_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint("plan_id", "ordinal", name="uq_retrieval_evidence_cluster"),
    )


class RetrievalEvidenceContributionRow(LedgerBase):
    __tablename__ = "retrieval_evidence_contributions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    cluster_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_clusters.id"), nullable=False
    )
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_provider_attempts.id"), nullable=False
    )
    contribution_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "cluster_id", "attempt_id", name="uq_retrieval_evidence_contribution"
        ),
    )


class RetrievalEvidenceReadinessRow(LedgerBase):
    __tablename__ = "retrieval_evidence_readiness_decisions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_plans.id"), nullable=False
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_json: Mapped[str] = mapped_column(Text, nullable=False)
    __table_args__ = (
        UniqueConstraint("plan_id", "provider", name="uq_retrieval_evidence_readiness"),
    )


class RetrievalEvidenceCacheLineageRow(LedgerBase):
    __tablename__ = "retrieval_evidence_cache_lineage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True
    )
    cache_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_cohort: Mapped[str] = mapped_column(String(128), nullable=False)
    lineage_json: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalEvidenceAccountingRow(LedgerBase):
    __tablename__ = "retrieval_evidence_accounting"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True
    )
    accounting_json: Mapped[str] = mapped_column(Text, nullable=False)


class RetrievalEvidenceTraceRow(LedgerBase):
    __tablename__ = "retrieval_evidence_trace_refs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    attempt_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_provider_attempts.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    trace_ref: Mapped[str] = mapped_column(String(256), nullable=False)
    __table_args__ = (
        UniqueConstraint("attempt_id", "ordinal", name="uq_retrieval_evidence_trace"),
    )


class AcceptedOperationRow(LedgerBase):
    __tablename__ = "accepted_retrieval_operations"
    receipt_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_evidence_plans.id"), nullable=False, unique=True
    )
    operation_id: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    acceptance_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "receipt_ref", "plan_id", name="uq_accepted_retrieval_receipt_plan"
        ),
    )


class RetrievalCachePublicationRow(LedgerBase):
    __tablename__ = "retrieval_cache_publications"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    receipt_ref: Mapped[str] = mapped_column(
        ForeignKey("accepted_retrieval_operations.receipt_ref"),
        nullable=False,
        unique=True,
    )
    cache_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    execution_cohort: Mapped[str] = mapped_column(String(128), nullable=False)
    published_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    __table_args__ = (
        UniqueConstraint(
            "cache_fingerprint",
            "execution_cohort",
            name="uq_retrieval_cache_publication",
        ),
    )


def _canonical(value: object) -> str:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
    )


def _jsonable(value):
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(child) for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(child) for child in value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    accepted: AcceptedRetrieval
    plan: object | None = None
    provider_batches: tuple[object, ...] = ()
    fusion: object | None = None
    readiness: tuple[Mapping[str, object], ...] = ()
    traces: tuple[Mapping[str, object], ...] = ()
    cache_decision: str = "miss"
    origin_receipt_ref: str | None = None
    cache_published: bool = False
    invoked_attempts: tuple[object, ...] = ()

    @classmethod
    def from_accepted(cls, accepted: AcceptedRetrieval) -> "RetrievalEvidence":
        return cls(
            accepted=accepted,
            cache_published=accepted.outcome.value
            in {"success", "degraded", "proven_empty"}
            and bool(accepted.contributor_attempt_refs),
        )

    @property
    def source_fingerprint(self) -> str:
        payload = {
            "operation_id": self.accepted.operation_id,
            "cache_fingerprint": self.accepted.cache_fingerprint,
            "execution_cohort": self.accepted.execution_cohort,
            "outcome": self.accepted.outcome.value,
            "attempts": self.accepted.contributor_attempt_refs,
            "receipt": self.accepted.acceptance_receipt.acceptance_fingerprint,
            "plan": self.plan,
            "provider_batches": self.provider_batches,
            "fusion": self.fusion,
            "readiness": self.readiness,
            "traces": self.traces,
            "cache_decision": self.cache_decision,
            "origin_receipt_ref": self.origin_receipt_ref,
            "cache_published": self.cache_published,
            "invoked_attempts": self.invoked_attempts,
        }
        return hashlib.sha256(_canonical(payload).encode()).hexdigest()


class SqlAlchemyEvidenceRepository:
    """Commit complete evidence, its receipt, and its publication identity atomically."""

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        clock: Callable[[], datetime] = _system_now,
    ):
        self.session_factory = session_factory
        self.clock = clock

    def accept(self, evidence: RetrievalEvidence) -> AcceptanceReceipt:
        accepted = evidence.accepted
        try:
            with self.session_factory.begin() as session:
                if (
                    evidence.cache_published
                    and session.bind is not None
                    and session.bind.dialect.name == "postgresql"
                ):
                    lock_key = int.from_bytes(
                        hashlib.sha256(
                            (
                                f"{accepted.cache_fingerprint}\0"
                                f"{accepted.execution_cohort}"
                            ).encode()
                        ).digest()[:8],
                        "big",
                    )
                    if lock_key >= 2**63:
                        lock_key -= 2**64
                    session.execute(
                        text("SELECT pg_advisory_xact_lock(:lock_key)"),
                        {"lock_key": lock_key},
                    )
                existing = session.scalar(
                    select(AcceptedOperationRow).where(
                        AcceptedOperationRow.operation_id == accepted.operation_id
                    )
                )
                if existing is not None:
                    if (
                        existing.acceptance_fingerprint
                        != accepted.acceptance_receipt.acceptance_fingerprint
                    ):
                        raise ValueError(
                            "operation already accepted with different evidence"
                        )
                    return accepted.acceptance_receipt

                plan_id = uuid.uuid4().hex
                accepted_results = _jsonable(accepted.results)
                accepted_results_fingerprint = hashlib.sha256(
                    _canonical(accepted_results).encode()
                ).hexdigest()
                session.add(
                    RetrievalEvidencePlanRow(
                        id=plan_id,
                        operation_id=accepted.operation_id,
                        cache_fingerprint=accepted.cache_fingerprint,
                        execution_cohort=accepted.execution_cohort,
                        plan_json=_canonical(
                            {
                                "evidence_plan": (
                                    evidence.plan
                                    if evidence.plan is not None
                                    else {"plan_id": accepted.plan_id}
                                ),
                                "accepted_results": accepted_results,
                                "accepted_results_fingerprint": (
                                    accepted_results_fingerprint
                                ),
                                "binding_version": "accepted-results-v1",
                                "invoked_attempts": evidence.invoked_attempts,
                            }
                        ),
                        source_fingerprint=evidence.source_fingerprint,
                        created_at=self.clock(),
                    )
                )
                session.flush()
                attempt_rows_by_provider: dict[str, str] = {}
                for batch_ordinal, batch in enumerate(evidence.provider_batches):
                    batch_id = uuid.uuid4().hex
                    provider = batch.provider.value
                    session.add(
                        RetrievalEvidenceBatchRow(
                            id=batch_id,
                            plan_id=plan_id,
                            provider=provider,
                            ordinal=batch_ordinal,
                            batch_json=_canonical(batch),
                        )
                    )
                    session.flush()
                    attempt_ref = batch.request_evidence.attempt_id
                    if attempt_ref is None:
                        raise ValueError(
                            "provider evidence is missing its accepted attempt identity"
                        )
                    attempt_id = uuid.uuid4().hex
                    session.add(
                        RetrievalEvidenceAttemptRow(
                            id=attempt_id,
                            batch_id=batch_id,
                            attempt_ref=attempt_ref,
                            ordinal=0,
                            attempt_json=_canonical(batch.request_evidence),
                        )
                    )
                    attempt_rows_by_provider[provider] = attempt_id
                    for observation_ordinal, observation in enumerate(
                        batch.observations
                    ):
                        observation_json = _canonical(observation)
                        observation_ref = (
                            "observation:"
                            + hashlib.sha256(
                                (
                                    f"{accepted.operation_id}:{provider}:"
                                    f"{observation_ordinal}:{observation_json}"
                                ).encode()
                            ).hexdigest()[:48]
                        )
                        session.add(
                            RetrievalEvidenceObservationRow(
                                id=uuid.uuid4().hex,
                                plan_id=plan_id,
                                observation_ref=observation_ref,
                                observation_json=observation_json,
                            )
                        )
                if (
                    not evidence.provider_batches
                    and accepted.contributor_attempt_refs
                    and evidence.origin_receipt_ref is None
                ):
                    batch_id = uuid.uuid4().hex
                    session.add(
                        RetrievalEvidenceBatchRow(
                            id=batch_id,
                            plan_id=plan_id,
                            provider="accepted",
                            ordinal=0,
                            batch_json="{}",
                        )
                    )
                    session.flush()
                    for ordinal, attempt_ref in enumerate(
                        accepted.contributor_attempt_refs
                    ):
                        session.add(
                            RetrievalEvidenceAttemptRow(
                                id=uuid.uuid4().hex,
                                batch_id=batch_id,
                                attempt_ref=attempt_ref,
                                ordinal=ordinal,
                                attempt_json="{}",
                            )
                        )
                if evidence.fusion is not None:
                    for cluster in evidence.fusion.ranked_result_clusters:
                        cluster_id = uuid.uuid4().hex
                        session.add(
                            RetrievalEvidenceClusterRow(
                                id=cluster_id,
                                plan_id=plan_id,
                                ordinal=cluster.output_rank,
                                cluster_json=_canonical(cluster),
                            )
                        )
                        session.flush()
                        for contribution in cluster.contributions:
                            attempt_id = attempt_rows_by_provider.get(
                                contribution.provider.value
                            )
                            if attempt_id is None:
                                raise ValueError(
                                    "fusion contribution lacks provider attempt"
                                )
                            session.add(
                                RetrievalEvidenceContributionRow(
                                    id=uuid.uuid4().hex,
                                    cluster_id=cluster_id,
                                    attempt_id=attempt_id,
                                    contribution_json=_canonical(contribution),
                                )
                            )
                for readiness in evidence.readiness:
                    session.add(
                        RetrievalEvidenceReadinessRow(
                            id=uuid.uuid4().hex,
                            plan_id=plan_id,
                            provider=str(readiness["provider"]),
                            decision_json=_canonical(readiness),
                        )
                    )
                for trace_ordinal, trace in enumerate(evidence.traces):
                    provider = str(trace.get("provider", ""))
                    attempt_id = attempt_rows_by_provider.get(provider)
                    if attempt_id is None:
                        continue
                    session.add(
                        RetrievalEvidenceTraceRow(
                            id=uuid.uuid4().hex,
                            attempt_id=attempt_id,
                            ordinal=trace_ordinal,
                            trace_ref=(
                                f"trace:{provider}:"
                                f"{str(trace.get('status', 'unknown'))}"
                            ),
                        )
                    )
                session.add(
                    RetrievalEvidenceCacheLineageRow(
                        id=uuid.uuid4().hex,
                        plan_id=plan_id,
                        cache_fingerprint=accepted.cache_fingerprint,
                        execution_cohort=accepted.execution_cohort,
                        lineage_json=_canonical(
                            {
                                "cache_decision": evidence.cache_decision,
                                "origin_receipt": evidence.origin_receipt_ref,
                                "contributor_attempt_refs": (
                                    accepted.contributor_attempt_refs
                                ),
                            }
                        ),
                    )
                )
                session.add(
                    RetrievalEvidenceAccountingRow(
                        id=uuid.uuid4().hex,
                        plan_id=plan_id,
                        accounting_json=_canonical(
                            {"origin_spend_usd": accepted.origin_spend_usd}
                        ),
                    )
                )
                session.add(
                    AcceptedOperationRow(
                        receipt_ref=accepted.acceptance_receipt.receipt_ref,
                        plan_id=plan_id,
                        operation_id=accepted.operation_id,
                        acceptance_fingerprint=accepted.acceptance_receipt.acceptance_fingerprint,
                        outcome=accepted.outcome.value,
                        accepted_at=accepted.acceptance_receipt.accepted_at,
                    )
                )
                session.flush()
                if evidence.cache_published:
                    prior_publication = session.scalar(
                        select(RetrievalCachePublicationRow).where(
                            RetrievalCachePublicationRow.cache_fingerprint
                            == accepted.cache_fingerprint,
                            RetrievalCachePublicationRow.execution_cohort
                            == accepted.execution_cohort,
                        )
                    )
                    if prior_publication is not None:
                        accepted_at = accepted.acceptance_receipt.accepted_at.replace(
                            tzinfo=None
                        )
                        if prior_publication.published_at < accepted_at:
                            session.delete(prior_publication)
                            session.flush()
                    session.add(
                        RetrievalCachePublicationRow(
                            id=uuid.uuid4().hex,
                            receipt_ref=accepted.acceptance_receipt.receipt_ref,
                            cache_fingerprint=accepted.cache_fingerprint,
                            execution_cohort=accepted.execution_cohort,
                            published_at=accepted.acceptance_receipt.accepted_at,
                        )
                    )
            return accepted.acceptance_receipt
        except IntegrityError:
            with self.session_factory() as session:
                existing = session.scalar(
                    select(AcceptedOperationRow).where(
                        AcceptedOperationRow.operation_id == accepted.operation_id
                    )
                )
                if (
                    existing is not None
                    and existing.acceptance_fingerprint
                    == accepted.acceptance_receipt.acceptance_fingerprint
                ):
                    return accepted.acceptance_receipt
            raise

    def accepted_count(self) -> int:
        with self.session_factory() as session:
            return len(session.scalars(select(AcceptedOperationRow)).all())

    def publication_count(self) -> int:
        with self.session_factory() as session:
            return len(session.scalars(select(RetrievalCachePublicationRow)).all())
