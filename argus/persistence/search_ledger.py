"""Atomic persistence for accepted search retrievals."""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Protocol

from sqlalchemy import (
    Boolean,
    BigInteger,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from argus.config import get_config
from argus.api.lifecycle import LifecycleCapability
from argus.models import SearchQuery, SearchResponse
from argus.persistence.maya_outbox import (
    excludes_capture,
    extraction_capture_payload,
    maya_payload_json,
    safe_failure_summary,
    search_capture_payload,
)


class LedgerBase(DeclarativeBase):
    pass


_DATABASE_OPERATION_TIMEOUT_SECONDS = 5


def _system_now() -> datetime:
    return datetime.now(tz=None)


def _bounded_engine_options(url: str) -> dict:
    """Driver deadlines backing the cooperative lifecycle declaration."""
    if url.startswith("sqlite:"):
        return {"connect_args": {"timeout": _DATABASE_OPERATION_TIMEOUT_SECONDS}}
    if url.startswith(("postgresql:", "postgresql+")):
        timeout_ms = _DATABASE_OPERATION_TIMEOUT_SECONDS * 1000
        return {
            "connect_args": {
                "connect_timeout": _DATABASE_OPERATION_TIMEOUT_SECONDS,
                "options": (
                    f"-c statement_timeout={timeout_ms} -c lock_timeout={timeout_ms}"
                ),
                "keepalives": 1,
                "keepalives_idle": _DATABASE_OPERATION_TIMEOUT_SECONDS,
                "keepalives_interval": 2,
                "keepalives_count": 2,
                "tcp_user_timeout": timeout_ms * 2,
            },
            "pool_timeout": _DATABASE_OPERATION_TIMEOUT_SECONDS,
        }
    return {"pool_timeout": _DATABASE_OPERATION_TIMEOUT_SECONDS}


class RetrievalRequestRow(LedgerBase):
    __tablename__ = "retrieval_requests"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    max_results: Mapped[int] = mapped_column(Integer, nullable=False)
    providers_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    free_only: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    caller: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RetrievalRunRow(LedgerBase):
    __tablename__ = "retrieval_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    request_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_requests.id"), nullable=False, unique=True
    )
    search_run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    acceptance_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    total_results: Mapped[int] = mapped_column(Integer, nullable=False)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ProviderAttemptRow(LedgerBase):
    __tablename__ = "provider_attempts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    egress: Mapped[str] = mapped_column(String(50), nullable=False, default="local")


class ContentIdentityRow(LedgerBase):
    __tablename__ = "content_identities"

    content_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class NormalizedResultRow(LedgerBase):
    __tablename__ = "normalized_results"
    __table_args__ = (UniqueConstraint("run_id", "final_rank"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("retrieval_runs.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(
        ForeignKey("content_identities.content_hash"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    snippet: Mapped[str] = mapped_column(Text, nullable=False, default="")
    domain: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    final_rank: Mapped[int] = mapped_column(Integer, nullable=False)


class ResultProvenanceRow(LedgerBase):
    __tablename__ = "result_provenance"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    result_id: Mapped[str] = mapped_column(
        ForeignKey("normalized_results.id"), nullable=False, unique=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    egress: Mapped[str | None] = mapped_column(String(50), nullable=True)
    machine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, default="search"
    )
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class DeliveryIntentRow(LedgerBase):
    __tablename__ = "delivery_intents"
    __table_args__ = (
        CheckConstraint(
            "(run_id IS NOT NULL AND extraction_run_id IS NULL) OR "
            "(run_id IS NULL AND extraction_run_id IS NOT NULL)",
            name="ck_delivery_intents_one_parent",
        ),
        Index(
            "ix_delivery_intents_dispatch",
            "destination",
            "status",
            "next_attempt_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        ForeignKey("retrieval_runs.id"), nullable=True, unique=True
    )
    extraction_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=True, unique=True
    )
    destination: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=8)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    lease_token: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_error_summary: Mapped[str | None] = mapped_column(String(256), nullable=True)
    response_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_compacted_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ExtractionRunRow(LedgerBase):
    __tablename__ = "extraction_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    extraction_run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    request_url: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    caller: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    selected_extractor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_hash: Mapped[str | None] = mapped_column(
        ForeignKey("content_identities.content_hash"), nullable=True
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, default="")
    author: Mapped[str] = mapped_column(Text, nullable=False, default="")
    published_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    word_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quality_passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quality_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(256), nullable=True)
    acceptance_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    committed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ExtractorAttemptRow(LedgerBase):
    __tablename__ = "extractor_attempts"
    __table_args__ = (UniqueConstraint("run_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failure_summary: Mapped[str | None] = mapped_column(String(256), nullable=True)


class ExtractionArtifactRow(LedgerBase):
    __tablename__ = "extraction_artifacts"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_runs.id"), nullable=False, unique=True
    )
    canonical_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(
        ForeignKey("content_identities.content_hash"), nullable=True
    )
    source_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    egress: Mapped[str | None] = mapped_column(String(50), nullable=True)
    machine: Mapped[str | None] = mapped_column(String(100), nullable=True)
    auth_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cookies_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    archive_used: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    cost: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    metadata_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")


class ExtractionOutcomePlanRow(LedgerBase):
    __tablename__ = "extraction_outcome_plans"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_run_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    normalized_url: Mapped[str] = mapped_column(Text, nullable=False)
    access_scope: Mapped[str] = mapped_column(String(128), nullable=False)
    mode: Mapped[str] = mapped_column(String(64), nullable=False)
    plan_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ExtractionOutcomeStepRow(LedgerBase):
    __tablename__ = "extraction_outcome_steps"
    __table_args__ = (UniqueConstraint("plan_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_outcome_plans.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    extractor: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(32), nullable=False)
    attempt_outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    spend_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    policy_rule_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ExtractionArtifactIdentityRow(LedgerBase):
    __tablename__ = "extraction_artifact_identities"

    artifact_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    content_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    evaluation_json: Mapped[str] = mapped_column(Text, nullable=False)


class ExtractionOutcomeArtifactRow(LedgerBase):
    __tablename__ = "extraction_outcome_artifacts"
    __table_args__ = (
        UniqueConstraint("id", "plan_id"),
        Index(
            "ix_extraction_outcome_artifacts_artifact_ref",
            "artifact_ref",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_outcome_plans.id"), nullable=False, unique=True
    )
    artifact_ref: Mapped[str] = mapped_column(
        ForeignKey(
            "extraction_artifact_identities.artifact_ref",
            name="fk_extraction_outcome_artifacts_identity",
        ),
        nullable=False,
    )
    content_identity: Mapped[str] = mapped_column(String(128), nullable=False)
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_passed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_complete: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    evaluation_json: Mapped[str] = mapped_column(Text, nullable=False)


class ExtractionOutcomeRejectionRow(LedgerBase):
    __tablename__ = "extraction_outcome_rejections"
    __table_args__ = (UniqueConstraint("id", "plan_id"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_outcome_plans.id"), nullable=False, unique=True
    )
    rejection_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    recommended_action: Mapped[str] = mapped_column(String(64), nullable=False)
    projection_json: Mapped[str] = mapped_column(Text, nullable=False)


class ExtractionOutcomeAcceptanceRow(LedgerBase):
    __tablename__ = "extraction_outcome_acceptances"
    __table_args__ = (UniqueConstraint("receipt_ref", "plan_id"),)

    receipt_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    plan_id: Mapped[str] = mapped_column(
        ForeignKey("extraction_outcome_plans.id"), nullable=False, unique=True
    )
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome_policy_version: Mapped[str] = mapped_column(String(128), nullable=False)
    projection_json: Mapped[str] = mapped_column(Text, nullable=False)
    acceptance_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    scope: Mapped[str] = mapped_column(String(64), nullable=False)


class RetrievalCompositionRow(LedgerBase):
    __tablename__ = "retrieval_compositions"

    receipt_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    retrieval_acceptance_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    requirement_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    retrieval_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    artifact_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)
    composite_outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    projection_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_fingerprint: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True
    )
    accepted_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class ResultExtractionLinkRow(LedgerBase):
    __tablename__ = "result_extraction_links"
    __table_args__ = (
        UniqueConstraint(
            "composition_ref",
            "result_cluster_ref",
            name="uq_result_extraction_links_composition_cluster",
        ),
        CheckConstraint(
            "(extraction_acceptance_ref IS NULL) = "
            "(extraction_plan_id IS NULL)",
            name="ck_result_extraction_links_acceptance_pair",
        ),
        CheckConstraint(
            "(artifact_row_id IS NULL) = (artifact_plan_id IS NULL)",
            name="ck_result_extraction_links_artifact_pair",
        ),
        CheckConstraint(
            "(rejection_row_id IS NULL) = (rejection_plan_id IS NULL)",
            name="ck_result_extraction_links_rejection_pair",
        ),
        CheckConstraint(
            "artifact_plan_id IS NULL OR "
            "artifact_plan_id = extraction_plan_id",
            name="ck_result_extraction_links_artifact_same_plan",
        ),
        CheckConstraint(
            "rejection_plan_id IS NULL OR "
            "rejection_plan_id = extraction_plan_id",
            name="ck_result_extraction_links_rejection_same_plan",
        ),
        ForeignKeyConstraint(
            ["extraction_acceptance_ref", "extraction_plan_id"],
            [
                "extraction_outcome_acceptances.receipt_ref",
                "extraction_outcome_acceptances.plan_id",
            ],
            match="FULL",
            name="fk_result_extraction_links_acceptance_plan",
        ),
        ForeignKeyConstraint(
            ["artifact_row_id", "artifact_plan_id"],
            [
                "extraction_outcome_artifacts.id",
                "extraction_outcome_artifacts.plan_id",
            ],
            match="FULL",
            name="fk_result_extraction_links_artifact_plan",
        ),
        ForeignKeyConstraint(
            ["rejection_row_id", "rejection_plan_id"],
            [
                "extraction_outcome_rejections.id",
                "extraction_outcome_rejections.plan_id",
            ],
            match="FULL",
            name="fk_result_extraction_links_rejection_plan",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    composition_ref: Mapped[str] = mapped_column(
        ForeignKey("retrieval_compositions.receipt_ref"), nullable=False
    )
    result_cluster_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    extraction_acceptance_ref: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    extraction_plan_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    artifact_row_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    artifact_plan_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    rejection_row_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    rejection_plan_id: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    reuse_origin: Mapped[str | None] = mapped_column(String(128), nullable=True)


class ExtractionOutcomeActivationRow(LedgerBase):
    __tablename__ = "extraction_outcome_activations"

    receipt_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    activated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class RetrievalSessionRow(LedgerBase):
    __tablename__ = "retrieval_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class SessionQueryRow(LedgerBase):
    __tablename__ = "session_queries"
    __table_args__ = (UniqueConstraint("session_id", "ordinal"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("retrieval_sessions.id"), nullable=False
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    query_text: Mapped[str] = mapped_column(Text, nullable=False)
    mode: Mapped[str] = mapped_column(String(50), nullable=False)
    queried_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SessionExtractedUrlRow(LedgerBase):
    __tablename__ = "session_extracted_urls"
    __table_args__ = (UniqueConstraint("query_id", "url"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    query_id: Mapped[str] = mapped_column(
        ForeignKey("session_queries.id"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)


@dataclass(frozen=True)
class AcceptanceReceipt:
    run_id: str
    delivery_intent_id: str | None


@dataclass(frozen=True)
class DurableAcceptanceSnapshot:
    stored_fingerprint: str | None
    state: dict


@dataclass(frozen=True)
class SerializedAcceptance:
    """Canonical state shared by fingerprinting and durable row writes."""

    state: dict
    fingerprint: str


@dataclass(frozen=True)
class ExtractionReceipt:
    extraction_run_id: str
    delivery_intent_id: str | None = None


class AcceptanceConflictError(RuntimeError):
    """A public run ID was already committed with a different payload."""


def _build_acceptance_state(query: SearchQuery, response: SearchResponse) -> dict:
    run_id = response.search_run_id
    if not run_id:
        raise ValueError("accepted retrieval requires a search_run_id")

    attempts = [
        {
            "provider": trace.provider.value,
            "status": trace.status,
            "results_count": trace.results_count,
            "latency_ms": trace.latency_ms,
            "error": trace.error,
            "budget_remaining": trace.budget_remaining,
            "egress": trace.egress,
        }
        for trace in response.traces
    ]
    attempts.sort(key=_canonical_json)

    results = []
    for rank, result in enumerate(response.results):
        if not result.url:
            raise ValueError("normalized search results require a URL")
        canonical_url = result.url.strip()
        content_hash = hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()
        provider = result.provider.value if result.provider else ""
        metadata = _normalize_json_value(dict(result.metadata))
        results.append(
            {
                "normalized": {
                    "url": result.url,
                    "title": result.title,
                    "snippet": result.snippet,
                    "domain": result.domain,
                    "provider": provider,
                    "score": result.score,
                    "final_rank": rank,
                },
                "content_identity": {
                    "content_hash": content_hash,
                    "canonical_url": canonical_url,
                },
                "provenance": {
                    "provider": provider,
                    "egress": metadata.get("egress"),
                    "machine": metadata.get("machine"),
                    "source_type": metadata.get("source_type", "search"),
                    "metadata": metadata,
                },
            }
        )

    return {
        "request": {
            "query_text": query.query,
            "mode": query.mode.value,
            "max_results": query.max_results,
            "providers": (
                [provider.value for provider in query.providers]
                if query.providers is not None
                else None
            ),
            "free_only": query.free_only,
            "caller": query.caller,
        },
        "run": {
            "search_run_id": run_id,
            "status": "accepted",
            "total_results": len(response.results),
            "cached": response.cached,
        },
        "attempts": attempts,
        "results": results,
        "delivery_intent": {
            "destination": "maya",
            "status": (
                "suppressed"
                if excludes_capture(
                    query.caller,
                    user_visible=query.user_visible,
                )
                else "pending"
            ),
            "payload": (
                None
                if excludes_capture(
                    query.caller,
                    user_visible=query.user_visible,
                )
                else search_capture_payload(
                    query,
                    response,
                    completed_at=response.created_at,
                )
            ),
        },
    }


def serialize_acceptance(
    query: SearchQuery,
    response: SearchResponse,
) -> SerializedAcceptance:
    state = _build_acceptance_state(query, response)
    return SerializedAcceptance(
        state=state,
        fingerprint=acceptance_fingerprint(state),
    )


def acceptance_state(query: SearchQuery, response: SearchResponse) -> dict:
    """Return the canonical immutable state written by ``accept``."""
    return serialize_acceptance(query, response).state


def acceptance_fingerprint(state: dict) -> str:
    fingerprint_state = _normalize_json_value(state)
    delivery = fingerprint_state.get("delivery_intent")
    if isinstance(delivery, dict):
        # Issue 34's placeholder intent was part of the acceptance fingerprint.
        # Preserve that exact historical shape while allowing issue 36's
        # transport body and lifecycle status to change and compact.
        status = delivery.get("status")
        if status in {
            "pending",
            "delivering",
            "retry",
            "dead_letter",
            "acknowledged",
            "suppressed",
            "superseded",
        }:
            run = fingerprint_state.get("run") or {}
            fingerprint_state["delivery_intent"] = {
                "destination": delivery.get("destination"),
                "status": "pending",
                "payload": {
                    "search_run_id": run.get("search_run_id"),
                    "result_count": run.get("total_results"),
                },
            }
    return hashlib.sha256(
        _canonical_json(fingerprint_state).encode("utf-8")
    ).hexdigest()


def _canonical_json(value) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _normalize_json_value(value):
    return json.loads(_canonical_json(value))


def _parse_json_value(value: str | None):
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {"__invalid_json__": value}


def _parse_optional_json_list(value: str | None):
    if value is None:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {"__invalid_json__": value}
    return parsed if isinstance(parsed, list) else {"__invalid_json__": value}


def _extraction_projection_state(projection) -> dict:
    """Canonical source facts and projection for S3 durable identity."""
    from dataclasses import asdict

    state = asdict(projection)
    raw_url = state["plan"]["normalized_url"]
    state["plan"]["normalized_url"] = _safe_extraction_source_url(raw_url)
    state["normalized_url_identity"] = (
        getattr(projection, "normalized_url_identity", None)
        or "sha256:" + hashlib.sha256(raw_url.encode("utf-8")).hexdigest()
    )
    return _normalize_json_value(state)


def _extraction_claim_state(claim) -> dict:
    from dataclasses import asdict

    state = asdict(claim)
    state["plan_ref"] = claim.plan.plan_ref
    raw_url = state["plan"]["normalized_url"]
    state["plan"]["normalized_url"] = _safe_extraction_source_url(raw_url)
    state["normalized_url_identity"] = (
        "sha256:" + hashlib.sha256(raw_url.encode("utf-8")).hexdigest()
    )
    return _normalize_json_value(state)


def _extraction_source_fingerprint(state: dict) -> str:
    source = dict(state)
    source.pop("outcome", None)
    source.pop("artifact_disposition", None)
    source.pop("rejection", None)
    return acceptance_fingerprint(source)


def _deserialize_extraction_projection(state: dict):
    from decimal import Decimal

    from argus.contracts import CanonicalOutcome
    from argus.extraction.cache import ExtractionCacheIdentity
    from argus.extraction.outcomes import (
        ArtifactDisposition,
        ArtifactEvaluation,
        AttemptOutcome,
        CacheDecision,
        CacheOriginEvidence,
        CacheOutcome,
        ExtractionAcceptanceReceipt,
        ExtractionCandidate,
        ExtractionPlan,
        ExtractionProvenance,
        ExtractorDecision,
        ExtractorExecutionDecision,
        FinalizedExtractionProjection,
        SpendEvidence,
        TerminalCause,
        TerminalCauseKind,
    )
    from argus.extraction.rejection import (
        ExtractionRejection,
        RejectionAction,
        RejectionCode,
    )

    def provenance(value):
        return ExtractionProvenance(**value)

    def parse_artifact(artifact_state):
        if artifact_state is None:
            return None
        return ArtifactEvaluation(
            **{
                **artifact_state,
                "completeness_confidence": (
                    Decimal(artifact_state["completeness_confidence"])
                    if artifact_state["completeness_confidence"] is not None
                    else None
                ),
                "completeness_signals": tuple(
                    artifact_state["completeness_signals"]
                ),
                "provenance": provenance(artifact_state["provenance"]),
            }
        )

    def parse_steps(step_states):
        parsed = []
        for step_state in step_states:
            spend_state = step_state["spend"]
            parsed.append(
                ExtractorDecision(
                    **{
                        **step_state,
                        "decision": ExtractorExecutionDecision(
                            step_state["decision"]
                        ),
                        "attempt_outcome": (
                            AttemptOutcome(step_state["attempt_outcome"])
                            if step_state["attempt_outcome"] is not None
                            else None
                        ),
                        "provenance": (
                            provenance(step_state["provenance"])
                            if step_state["provenance"] is not None
                            else None
                        ),
                        "spend": (
                            SpendEvidence(
                                actual_usd=Decimal(
                                    spend_state["actual_usd"]
                                ),
                                reserved_usd=Decimal(
                                    spend_state["reserved_usd"]
                                ),
                                spend_attempt_ref=(
                                    spend_state["spend_attempt_ref"]
                                ),
                            )
                            if spend_state is not None
                            else None
                        ),
                    }
                )
            )
        return tuple(parsed)

    def parse_rejection(rejection_state):
        if rejection_state is None:
            return None
        return ExtractionRejection(
            **{
                **rejection_state,
                "code": RejectionCode(rejection_state["code"]),
                "recommended_action": RejectionAction(
                    rejection_state["recommended_action"]
                ),
            }
        )

    plan_state = state["plan"]
    plan = ExtractionPlan(
        **{
            **plan_state,
            "candidates": tuple(
                ExtractionCandidate(**candidate)
                for candidate in plan_state["candidates"]
            ),
        }
    )
    artifact = parse_artifact(state["artifact"])
    steps = parse_steps(state["steps"])
    terminal_state = state["terminal_cause"]
    terminal = None
    if terminal_state is not None:
        terminal = TerminalCause(
            **{
                **terminal_state,
                "kind": TerminalCauseKind(terminal_state["kind"]),
                "preflight_outcome": (
                    CanonicalOutcome(terminal_state["preflight_outcome"])
                    if terminal_state["preflight_outcome"] is not None
                    else None
                ),
                "invoked_ordinals": tuple(terminal_state["invoked_ordinals"]),
                "distinct_attempt_outcomes": tuple(
                    AttemptOutcome(value)
                    for value in terminal_state["distinct_attempt_outcomes"]
                ),
            }
        )
    rejection = parse_rejection(state["rejection"])
    cache_state = state["cache_decision"]
    origin_state = cache_state.get("origin_evidence")
    origin = None
    if origin_state is not None:
        receipt_state = origin_state["acceptance_receipt"]
        origin = CacheOriginEvidence(
            **{
                **origin_state,
                "outcome": CanonicalOutcome(origin_state["outcome"]),
                "artifact_disposition": ArtifactDisposition(
                    origin_state["artifact_disposition"]
                ),
                "artifact": parse_artifact(origin_state["artifact"]),
                "rejection": parse_rejection(origin_state["rejection"]),
                "steps": parse_steps(origin_state["steps"]),
                "acceptance_receipt": ExtractionAcceptanceReceipt(
                    **receipt_state
                ),
            }
        )
    projection_state = dict(state)
    return FinalizedExtractionProjection(
        **{
            **projection_state,
            "outcome": CanonicalOutcome(state["outcome"]),
            "artifact_disposition": ArtifactDisposition(
                state["artifact_disposition"]
            ),
            "plan": plan,
            "artifact": artifact,
            "rejection": rejection,
            "steps": steps,
            "terminal_cause": terminal,
            "cache_decision": CacheDecision(
                **{
                    **cache_state,
                    "outcome": CacheOutcome(cache_state["outcome"]),
                    "origin_evidence": origin,
                    "current_identity": (
                        ExtractionCacheIdentity(
                            **cache_state["current_identity"]
                        )
                        if cache_state.get("current_identity") is not None
                        else None
                    ),
                }
            ),
        }
    )


def _safe_extraction_source_url(value: str) -> str:
    """Persist only a credential-free origin; identity is stored separately."""
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(value)
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, "/", "", ""))


def _safe_persisted_url(value: str) -> str:
    """Legacy safe URL projection for non-S3 records that retain page paths."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(value)
    query = []
    sensitive_markers = (
        "token",
        "key",
        "secret",
        "auth",
        "signature",
        "credential",
        "password",
        "session",
        "code",
        "jwt",
        "sig",
    )
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        if any(marker in key.lower() for marker in sensitive_markers):
            item = "[redacted]"
        query.append((key, item))
    hostname = parts.hostname or ""
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parts.port is not None:
        netloc = f"{netloc}:{parts.port}"
    fragment = (
        "[redacted]"
        if parts.fragment
        and any(
            marker in parts.fragment.lower()
            for marker in sensitive_markers
        )
        else parts.fragment
    )
    return urlunsplit(
        (parts.scheme, netloc, parts.path, urlencode(query), fragment)
    )


def _persist_extraction_projection_rows(
    session,
    *,
    projection,
    state: dict,
    plan_id: str,
    now: datetime,
) -> tuple[str, str]:
    for step, step_state in zip(projection.steps, state["steps"]):
        session.add(
            ExtractionOutcomeStepRow(
                id=uuid.uuid4().hex,
                plan_id=plan_id,
                ordinal=step.ordinal,
                extractor=step.extractor,
                decision=step.decision.value,
                attempt_outcome=(
                    step.attempt_outcome.value
                    if step.attempt_outcome is not None
                    else None
                ),
                latency_ms=step.latency_ms,
                provenance_json=(
                    _canonical_json(step_state["provenance"])
                    if step.provenance is not None
                    else None
                ),
                spend_json=(
                    _canonical_json(step_state["spend"])
                    if step.spend is not None
                    else None
                ),
                policy_rule_ref=step.policy_rule_ref,
            )
        )
    artifact = projection.artifact
    if artifact is not None:
        from argus.extraction.outcomes import ExtractionAcceptanceConflict

        artifact_json = _canonical_json(state["artifact"])
        identity_values = {
            "artifact_ref": artifact.artifact_ref,
            "content_identity": artifact.content_identity,
            "content_text": artifact.text,
            "evaluation_json": artifact_json,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert as dialect_insert
        elif dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            dialect_insert = None
        if dialect_insert is not None:
            session.execute(
                dialect_insert(ExtractionArtifactIdentityRow)
                .values(**identity_values)
                .on_conflict_do_nothing(index_elements=["artifact_ref"])
            )
        else:
            if session.get(
                ExtractionArtifactIdentityRow,
                artifact.artifact_ref,
            ) is None:
                session.add(ExtractionArtifactIdentityRow(**identity_values))
        canonical = session.get(
            ExtractionArtifactIdentityRow,
            artifact.artifact_ref,
        )
        if canonical is None:
            session.flush()
            canonical = session.get(
                ExtractionArtifactIdentityRow,
                artifact.artifact_ref,
            )
        if (
            canonical.content_identity != artifact.content_identity
            or canonical.content_text != artifact.text
            or canonical.evaluation_json != artifact_json
        ):
            raise ExtractionAcceptanceConflict()
        session.add(
            ExtractionOutcomeArtifactRow(
                id=uuid.uuid4().hex,
                plan_id=plan_id,
                artifact_ref=artifact.artifact_ref,
                content_identity=artifact.content_identity,
                content_text=artifact.text,
                disposition=projection.artifact_disposition.value,
                quality_passed=artifact.quality_passed,
                is_complete=artifact.is_complete,
                evaluation_json=artifact_json,
            )
        )
    rejection = projection.rejection
    if rejection is not None:
        from argus.extraction.outcomes import ExtractionAcceptanceConflict

        rejection_json = _canonical_json(state["rejection"])
        rejection_ref = rejection.rejection_ref
        reused_rejections = session.scalars(
            select(ExtractionOutcomeRejectionRow).where(
                ExtractionOutcomeRejectionRow.rejection_ref == rejection_ref
            )
        ).all()
        if any(
            existing.projection_json != rejection_json
            for existing in reused_rejections
        ):
            raise ExtractionAcceptanceConflict()
        session.add(
            ExtractionOutcomeRejectionRow(
                id=uuid.uuid4().hex,
                plan_id=plan_id,
                rejection_ref=rejection_ref,
                code=rejection.code.value,
                provider=rejection.provider,
                recommended_action=rejection.recommended_action.value,
                projection_json=rejection_json,
            )
        )
    receipt_ref = uuid.uuid4().hex
    scope = (
        "sqlite_development"
        if session.get_bind().dialect.name == "sqlite"
        else "postgresql_authority"
    )
    session.add(
        ExtractionOutcomeAcceptanceRow(
            receipt_ref=receipt_ref,
            plan_id=plan_id,
            outcome=projection.outcome.value,
            artifact_disposition=projection.artifact_disposition.value,
            outcome_policy_version=projection.extraction_outcome_policy_version,
            projection_json=_canonical_json(state),
            acceptance_fingerprint=acceptance_fingerprint(state),
            accepted_at=now,
            scope=scope,
        )
    )
    return receipt_ref, scope


class SearchLedgerRepository(Protocol):
    lifecycle_capability: object

    def accept(
        self, query: SearchQuery, response: SearchResponse
    ) -> AcceptanceReceipt: ...

    def operational_status(
        self,
        *,
        now: datetime | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict: ...

    def close(self) -> None: ...


class SqlAlchemySearchLedgerRepository:
    """Store one accepted retrieval in one database transaction."""

    lifecycle_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    operational_status_capability = LifecycleCapability.COOPERATIVE_BOUNDED
    compaction_capability = LifecycleCapability.COOPERATIVE_BOUNDED

    def __init__(
        self,
        session_factory: sessionmaker,
        *,
        clock: Callable[[], datetime] = _system_now,
    ):
        self.session_factory = session_factory
        self.clock = clock
        self._extraction_finalization_locks: dict[str, threading.Lock] = {}
        self._extraction_finalization_locks_guard = threading.Lock()

    def accept(self, query: SearchQuery, response: SearchResponse) -> AcceptanceReceipt:
        serialized = serialize_acceptance(query, response)
        try:
            return self._accept_once(
                serialized,
                started_at=response.created_at,
            )
        except IntegrityError:
            # A concurrent request with the same public run ID may win the
            # unique-key race after our initial lookup. Acknowledge only after
            # its complete transaction, including delivery intent, is visible.
            for _ in range(100):
                receipt = self._existing_receipt(
                    response.search_run_id,
                    serialized.fingerprint,
                )
                if receipt is not None:
                    return receipt
                time.sleep(0.01)
            raise

    def _accept_once(
        self,
        serialized: SerializedAcceptance,
        *,
        started_at: datetime,
    ) -> AcceptanceReceipt:
        state = serialized.state
        request_state = state["request"]
        run_state = state["run"]
        run_id = run_state["search_run_id"]

        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(RetrievalRunRow).where(RetrievalRunRow.search_run_id == run_id)
            )
            if existing is not None:
                return self._receipt_for_existing(
                    session,
                    existing,
                    serialized.fingerprint,
                )

            now = self.clock()
            request_id = uuid.uuid4().hex
            ledger_run_id = uuid.uuid4().hex
            session.add(
                RetrievalRequestRow(
                    id=request_id,
                    query_text=request_state["query_text"],
                    mode=request_state["mode"],
                    max_results=request_state["max_results"],
                    providers_json=(
                        _canonical_json(request_state["providers"])
                        if request_state["providers"] is not None
                        else None
                    ),
                    free_only=request_state["free_only"],
                    caller=request_state["caller"],
                    created_at=now,
                )
            )
            session.add(
                RetrievalRunRow(
                    id=ledger_run_id,
                    request_id=request_id,
                    search_run_id=run_id,
                    acceptance_fingerprint=serialized.fingerprint,
                    status=run_state["status"],
                    total_results=run_state["total_results"],
                    cached=run_state["cached"],
                    started_at=started_at,
                    committed_at=now,
                )
            )
            # Core upserts below trigger ORM autoflush. Flush the ledger
            # parents deliberately before any child rows enter the unit of
            # work so every dialect observes the same foreign-key order.
            session.flush()

            for attempt in state["attempts"]:
                session.add(
                    ProviderAttemptRow(
                        id=uuid.uuid4().hex,
                        run_id=ledger_run_id,
                        provider=attempt["provider"],
                        status=attempt["status"],
                        results_count=attempt["results_count"],
                        latency_ms=attempt["latency_ms"],
                        error=attempt["error"],
                        budget_remaining=attempt["budget_remaining"],
                        egress=attempt["egress"],
                    )
                )

            for result_state in state["results"]:
                normalized = result_state["normalized"]
                identity = result_state["content_identity"]
                provenance = result_state["provenance"]
                content_hash = identity["content_hash"]
                self._ensure_content_identity(
                    session, content_hash, identity["canonical_url"], now
                )
                result_id = uuid.uuid4().hex
                session.add(
                    NormalizedResultRow(
                        id=result_id,
                        run_id=ledger_run_id,
                        content_hash=content_hash,
                        **normalized,
                    )
                )
                session.add(
                    ResultProvenanceRow(
                        id=uuid.uuid4().hex,
                        result_id=result_id,
                        provider=provenance["provider"],
                        egress=provenance["egress"],
                        machine=provenance["machine"],
                        source_type=provenance["source_type"],
                        metadata_json=_canonical_json(provenance["metadata"]),
                    )
                )

            delivery_state = state["delivery_intent"]
            delivery_id = None
            if delivery_state is not None:
                delivery_id = uuid.uuid4().hex
                payload_json = (
                    maya_payload_json(delivery_state["payload"])
                    if delivery_state["payload"] is not None
                    else None
                )
                session.add(
                    DeliveryIntentRow(
                        id=delivery_id,
                        run_id=ledger_run_id,
                        extraction_run_id=None,
                        destination=delivery_state["destination"],
                        status=delivery_state["status"],
                        payload_json=payload_json,
                        payload_sha256=hashlib.sha256(
                            (payload_json or "").encode("utf-8")
                        ).hexdigest(),
                        content_sha256=None,
                        attempt_count=0,
                        max_attempts=8,
                        next_attempt_at=now,
                        updated_at=now,
                        created_at=now,
                    )
                )

        return AcceptanceReceipt(run_id=run_id, delivery_intent_id=delivery_id)

    def _existing_receipt(
        self,
        run_id: str | None,
        fingerprint: str,
    ) -> AcceptanceReceipt | None:
        if not run_id:
            return None
        with self.session_factory() as session:
            row = session.scalar(
                select(RetrievalRunRow).where(RetrievalRunRow.search_run_id == run_id)
            )
            if row is None:
                return None
            return self._receipt_for_existing(
                session,
                row,
                fingerprint,
            )

    @staticmethod
    def _receipt_for_existing(
        session,
        row: RetrievalRunRow,
        fingerprint: str,
    ) -> AcceptanceReceipt:
        if row.acceptance_fingerprint != fingerprint:
            raise AcceptanceConflictError(
                f"retrieval {row.search_run_id!r} has a different durable payload"
            )
        delivery_id = session.scalar(
            select(DeliveryIntentRow.id).where(DeliveryIntentRow.run_id == row.id)
        )
        if delivery_id is None:
            raise AcceptanceConflictError(
                f"retrieval {row.search_run_id!r} is incomplete"
            )
        return AcceptanceReceipt(
            run_id=row.search_run_id,
            delivery_intent_id=delivery_id,
        )

    def load_acceptance_snapshot(
        self,
        search_run_id: str,
    ) -> DurableAcceptanceSnapshot | None:
        """Read the complete durable state for reconciliation."""
        with self.session_factory() as session:
            if not inspect(session.get_bind()).has_table(RetrievalRunRow.__tablename__):
                return None
            pair = session.execute(
                select(RetrievalRequestRow, RetrievalRunRow)
                .join(
                    RetrievalRunRow,
                    RetrievalRunRow.request_id == RetrievalRequestRow.id,
                )
                .where(RetrievalRunRow.search_run_id == search_run_id)
            ).one_or_none()
            if pair is None:
                return None
            request, run = pair

            attempts = [
                {
                    "provider": row.provider,
                    "status": row.status,
                    "results_count": row.results_count,
                    "latency_ms": row.latency_ms,
                    "error": row.error,
                    "budget_remaining": row.budget_remaining,
                    "egress": row.egress,
                }
                for row in session.scalars(
                    select(ProviderAttemptRow).where(
                        ProviderAttemptRow.run_id == run.id
                    )
                )
            ]
            attempts.sort(key=_canonical_json)

            result_rows = session.execute(
                select(
                    NormalizedResultRow,
                    ContentIdentityRow,
                    ResultProvenanceRow,
                )
                .outerjoin(
                    ContentIdentityRow,
                    ContentIdentityRow.content_hash == NormalizedResultRow.content_hash,
                )
                .outerjoin(
                    ResultProvenanceRow,
                    ResultProvenanceRow.result_id == NormalizedResultRow.id,
                )
                .where(NormalizedResultRow.run_id == run.id)
                .order_by(NormalizedResultRow.final_rank)
            ).all()
            results = []
            for normalized, identity, provenance in result_rows:
                results.append(
                    {
                        "normalized": {
                            "url": normalized.url,
                            "title": normalized.title,
                            "snippet": normalized.snippet,
                            "domain": normalized.domain,
                            "provider": normalized.provider,
                            "score": normalized.score,
                            "final_rank": normalized.final_rank,
                        },
                        "content_identity": (
                            {
                                "content_hash": identity.content_hash,
                                "canonical_url": identity.canonical_url,
                            }
                            if identity is not None
                            else None
                        ),
                        "provenance": (
                            {
                                "provider": provenance.provider,
                                "egress": provenance.egress,
                                "machine": provenance.machine,
                                "source_type": provenance.source_type,
                                "metadata": _parse_json_value(provenance.metadata_json),
                            }
                            if provenance is not None
                            else None
                        ),
                    }
                )

            delivery = session.scalar(
                select(DeliveryIntentRow).where(DeliveryIntentRow.run_id == run.id)
            )
            state = {
                "request": {
                    "query_text": request.query_text,
                    "mode": request.mode,
                    "max_results": request.max_results,
                    "providers": _parse_optional_json_list(request.providers_json),
                    "free_only": request.free_only,
                    "caller": request.caller,
                },
                "run": {
                    "search_run_id": run.search_run_id,
                    "status": run.status,
                    "total_results": run.total_results,
                    "cached": run.cached,
                },
                "attempts": attempts,
                "results": results,
                "delivery_intent": (
                    {
                        "destination": delivery.destination,
                        "status": delivery.status,
                        "payload": _parse_json_value(delivery.payload_json),
                    }
                    if delivery is not None
                    else None
                ),
            }
            return DurableAcceptanceSnapshot(
                stored_fingerprint=run.acceptance_fingerprint,
                state=state,
            )

    def _extraction_finalization_lock(self, run_id: str) -> threading.Lock:
        with self._extraction_finalization_locks_guard:
            return self._extraction_finalization_locks.setdefault(
                run_id,
                threading.Lock(),
            )

    def finalize_extraction_once(self, claim, build_projection):
        """Claim one run durably before invoking the #57 classifier."""
        from argus.extraction.outcomes import (
            AcceptedExtractionOutcome,
            ExtractionAcceptanceConflict,
            ExtractionAcceptanceReceipt,
            FinalizedExtractionProjection,
        )

        claim_state = _extraction_claim_state(claim)
        source_fingerprint = acceptance_fingerprint(claim_state)
        run_lock = self._extraction_finalization_lock(claim.extraction_run_id)
        with run_lock:
            try:
                with self.session_factory.begin() as session:
                    existing = session.scalar(
                        select(ExtractionOutcomePlanRow).where(
                            ExtractionOutcomePlanRow.extraction_run_id
                            == claim.extraction_run_id
                        )
                    )
                    if existing is not None:
                        if existing.source_fingerprint != source_fingerprint:
                            raise ExtractionAcceptanceConflict()
                        acceptance = session.scalar(
                            select(ExtractionOutcomeAcceptanceRow).where(
                                ExtractionOutcomeAcceptanceRow.plan_id
                                == existing.id
                            )
                        )
                        if acceptance is None:
                            raise AcceptanceConflictError(
                                "claimed extraction lacks committed acceptance"
                            )
                        projection = _deserialize_extraction_projection(
                            _parse_json_value(acceptance.projection_json)
                        )
                        receipt = ExtractionAcceptanceReceipt(
                            receipt_ref=acceptance.receipt_ref,
                            accepted_at=acceptance.accepted_at.isoformat() + "Z",
                            scope=acceptance.scope,
                        )
                        return AcceptedExtractionOutcome.accepted(
                            projection,
                            receipt,
                        )

                    now = self.clock()
                    plan_id = uuid.uuid4().hex
                    plan_state = claim_state["plan"]
                    session.add(
                        ExtractionOutcomePlanRow(
                            id=plan_id,
                            plan_ref=claim.plan.plan_ref,
                            extraction_run_id=claim.extraction_run_id,
                            request_id=claim.request_id,
                            normalized_url=plan_state["normalized_url"],
                            access_scope=claim.plan.access_scope,
                            mode=claim.plan.mode,
                            plan_json=_canonical_json(plan_state),
                            source_fingerprint=source_fingerprint,
                            created_at=now,
                        )
                    )
                    session.flush()
                    raw_projection = build_projection()
                    raw_state = _extraction_projection_state(raw_projection)
                    projection = _deserialize_extraction_projection(raw_state)
                    state = _extraction_projection_state(projection)
                    if (
                        _extraction_source_fingerprint(state)
                        != _extraction_source_fingerprint(claim_state)
                    ):
                        raise ExtractionAcceptanceConflict()
                    receipt_ref, scope = _persist_extraction_projection_rows(
                        session,
                        projection=projection,
                        state=state,
                        plan_id=plan_id,
                        now=now,
                    )
                receipt = ExtractionAcceptanceReceipt(
                    receipt_ref=receipt_ref,
                    accepted_at=now.isoformat() + "Z",
                    scope=scope,
                )
                return AcceptedExtractionOutcome.accepted(projection, receipt)
            except IntegrityError:
                for _ in range(100):
                    existing = self.load_extraction_outcome(
                        claim.extraction_run_id
                    )
                    if existing is not None:
                        existing_state = _extraction_projection_state(
                            FinalizedExtractionProjection(
                                extraction_outcome_policy_version=(
                                    existing.extraction_outcome_policy_version
                                ),
                                outcome=existing.outcome,
                                artifact_disposition=(
                                    existing.artifact_disposition
                                ),
                                extraction_run_id=existing.extraction_run_id,
                                request_id=existing.request_id,
                                plan_ref=existing.plan_ref,
                                plan=existing.plan,
                                artifact=existing.artifact,
                                rejection=existing.rejection,
                                steps=existing.steps,
                                terminal_cause=existing.terminal_cause,
                                selected_extractor=existing.selected_extractor,
                                cache_decision=existing.cache_decision,
                                operation_latency_ms=(
                                    existing.operation_latency_ms
                                ),
                                normalized_url_identity=(
                                    existing.normalized_url_identity
                                ),
                            )
                        )
                        if (
                            _extraction_source_fingerprint(existing_state)
                            != _extraction_source_fingerprint(claim_state)
                        ):
                            raise ExtractionAcceptanceConflict()
                        return existing
                    time.sleep(0.01)
                raise

    def accept_extraction_outcome(self, projection):
        """Atomically accept one immutable S3 extraction projection."""
        from argus.extraction.outcomes import ExtractionAcceptanceReceipt

        state = _extraction_projection_state(projection)
        source_fingerprint = _extraction_source_fingerprint(state)
        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(ExtractionOutcomePlanRow).where(
                    ExtractionOutcomePlanRow.extraction_run_id
                    == projection.extraction_run_id
                )
            )
            if existing is not None:
                if existing.source_fingerprint != source_fingerprint:
                    from argus.extraction.outcomes import (
                        ExtractionAcceptanceConflict,
                    )

                    raise ExtractionAcceptanceConflict()
                acceptance = session.scalar(
                    select(ExtractionOutcomeAcceptanceRow).where(
                        ExtractionOutcomeAcceptanceRow.plan_id == existing.id
                    )
                )
                if acceptance is None:
                    raise AcceptanceConflictError(
                        f"extraction {projection.extraction_run_id!r} "
                        "has incomplete durable outcome state"
                    )
                return ExtractionAcceptanceReceipt(
                    receipt_ref=acceptance.receipt_ref,
                    accepted_at=acceptance.accepted_at.isoformat() + "Z",
                    scope=acceptance.scope,
                )

            now = self.clock()
            plan_id = uuid.uuid4().hex
            plan_state = state["plan"]
            session.add(
                ExtractionOutcomePlanRow(
                    id=plan_id,
                    plan_ref=projection.plan_ref,
                    extraction_run_id=projection.extraction_run_id,
                    request_id=projection.request_id,
                    normalized_url=_safe_persisted_url(
                        projection.plan.normalized_url
                    ),
                    access_scope=projection.plan.access_scope,
                    mode=projection.plan.mode,
                    plan_json=_canonical_json(plan_state),
                    source_fingerprint=source_fingerprint,
                    created_at=now,
                )
            )
            session.flush()
            receipt_ref, scope = _persist_extraction_projection_rows(
                session,
                projection=projection,
                state=state,
                plan_id=plan_id,
                now=now,
            )
        return ExtractionAcceptanceReceipt(
            receipt_ref=receipt_ref,
            accepted_at=now.isoformat() + "Z",
            scope=scope,
        )

    def load_extraction_outcome(self, extraction_run_id: str):
        """Load the exact accepted S3 projection for an idempotent retry."""
        from argus.extraction.outcomes import (
            AcceptedExtractionOutcome,
            ExtractionAcceptanceReceipt,
        )

        with self.session_factory() as session:
            pair = session.execute(
                select(
                    ExtractionOutcomePlanRow,
                    ExtractionOutcomeAcceptanceRow,
                )
                .join(
                    ExtractionOutcomeAcceptanceRow,
                    ExtractionOutcomeAcceptanceRow.plan_id
                    == ExtractionOutcomePlanRow.id,
                )
                .where(
                    ExtractionOutcomePlanRow.extraction_run_id
                    == extraction_run_id
                )
            ).one_or_none()
            if pair is None:
                return None
            _, acceptance = pair
            state = _parse_json_value(acceptance.projection_json)
            projection = _deserialize_extraction_projection(state)
            receipt = ExtractionAcceptanceReceipt(
                receipt_ref=acceptance.receipt_ref,
                accepted_at=acceptance.accepted_at.isoformat() + "Z",
                scope=acceptance.scope,
            )
            return AcceptedExtractionOutcome.accepted(projection, receipt)

    def load_extraction_outcome_by_receipt(self, receipt_ref: str):
        """Reload the exact durable accepted projection for cache/composition."""
        with self.session_factory() as session:
            return self._load_extraction_outcome_by_receipt_in_session(
                session,
                receipt_ref,
            )

    @staticmethod
    def _load_extraction_outcome_by_receipt_in_session(session, receipt_ref):
        from argus.extraction.outcomes import (
            AcceptedExtractionOutcome,
            ExtractionAcceptanceReceipt,
        )

        acceptance = session.get(ExtractionOutcomeAcceptanceRow, receipt_ref)
        if acceptance is None:
            return None
        projection = _deserialize_extraction_projection(
            _parse_json_value(acceptance.projection_json)
        )
        receipt = ExtractionAcceptanceReceipt(
            receipt_ref=acceptance.receipt_ref,
            accepted_at=acceptance.accepted_at.isoformat() + "Z",
            scope=acceptance.scope,
        )
        return AcceptedExtractionOutcome.accepted(projection, receipt)

    def accept_retrieval_composition(
        self,
        accepted_retrieval,
        composition,
        artifact_requirement,
    ):
        """Atomically persist one pure S3 composition and all selected links."""
        from dataclasses import asdict

        from argus.contracts import CanonicalOutcome
        from argus.extraction.composition import (
            CompositionAcceptanceReceipt,
            InvalidArtifactRequirement,
            RetrievalComposition,
            compose_retrieval_evidence,
        )

        if (
            not isinstance(composition, RetrievalComposition)
            or composition.composite_outcome
            is CanonicalOutcome.PERSISTENCE_FAILED
        ):
            raise ValueError("persistence-failed composition cannot be accepted")
        if (
            compose_retrieval_evidence(
                accepted_retrieval,
                composition.links,
                artifact_requirement,
            )
            != composition
        ):
            raise InvalidArtifactRequirement(
                "composition does not match its typed source evidence"
            )
        retrieval_receipt = accepted_retrieval.acceptance_receipt
        retrieval_receipt_ref = getattr(
            retrieval_receipt,
            "receipt_ref",
            retrieval_receipt,
        )
        if not isinstance(retrieval_receipt_ref, str):
            raise ValueError("accepted retrieval requires a bounded receipt")
        composition_state = asdict(composition)
        for link_state in composition_state["links"]:
            link_state.pop("accepted_outcome", None)
        source_state = {
            "retrieval_outcome": accepted_retrieval.outcome,
            "result_cluster_refs": tuple(
                accepted_retrieval.result_cluster_refs
            ),
            "retrieval_acceptance_ref": retrieval_receipt_ref,
            "artifact_requirement": (
                asdict(artifact_requirement)
                if artifact_requirement is not None
                else None
            ),
            "composition": composition_state,
        }
        source_state = _normalize_json_value(source_state)
        fingerprint = acceptance_fingerprint(source_state)
        with self.session_factory.begin() as session:
            for link in composition.links:
                if link.extraction_run_id is None:
                    continue
                extraction_receipt = getattr(
                    link.acceptance_receipt,
                    "receipt_ref",
                    link.acceptance_receipt,
                )
                durable = (
                    self._load_extraction_outcome_by_receipt_in_session(
                        session,
                        extraction_receipt,
                    )
                    if isinstance(extraction_receipt, str)
                    else None
                )
                if durable != link.accepted_outcome:
                    raise InvalidArtifactRequirement(
                        "link does not match durable accepted extraction"
                    )
            existing = session.scalar(
                select(RetrievalCompositionRow).where(
                    RetrievalCompositionRow.source_fingerprint == fingerprint
                )
            )
            if existing is not None:
                return CompositionAcceptanceReceipt(
                    receipt_ref=existing.receipt_ref,
                    accepted_at=existing.accepted_at.isoformat() + "Z",
                    scope="sqlite_development"
                    if session.get_bind().dialect.name == "sqlite"
                    else "postgresql_authority",
                )
            now = self.clock()
            receipt_ref = uuid.uuid4().hex
            composition_values = {
                "receipt_ref": receipt_ref,
                "retrieval_acceptance_ref": retrieval_receipt_ref,
                "requirement_ref": (
                    artifact_requirement.requirement_ref
                    if artifact_requirement is not None
                    else None
                ),
                "retrieval_outcome": composition.retrieval_outcome.value,
                "artifact_outcome": (
                    composition.artifact_outcome.value
                    if composition.artifact_outcome is not None
                    else None
                ),
                "composite_outcome": composition.composite_outcome.value,
                "projection_json": _canonical_json(source_state),
                "source_fingerprint": fingerprint,
                "accepted_at": now,
            }
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert as dialect_insert
            elif dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert as dialect_insert
            else:
                dialect_insert = None
            if dialect_insert is not None:
                inserted = session.execute(
                    dialect_insert(RetrievalCompositionRow)
                    .values(**composition_values)
                    .on_conflict_do_nothing(
                        index_elements=["source_fingerprint"]
                    )
                )
                if inserted.rowcount == 0:
                    existing = session.scalar(
                        select(RetrievalCompositionRow).where(
                            RetrievalCompositionRow.source_fingerprint
                            == fingerprint
                        )
                    )
                    if existing is None:
                        raise AcceptanceConflictError(
                            "composition uniqueness winner is not readable"
                        )
                    return CompositionAcceptanceReceipt(
                        receipt_ref=existing.receipt_ref,
                        accepted_at=existing.accepted_at.isoformat() + "Z",
                        scope=(
                            "sqlite_development"
                            if dialect == "sqlite"
                            else "postgresql_authority"
                        ),
                    )
            else:
                session.add(RetrievalCompositionRow(**composition_values))
                session.flush()
            for link in composition.links:
                extraction_receipt = getattr(
                    link.acceptance_receipt,
                    "receipt_ref",
                    link.acceptance_receipt,
                )
                extraction_plan_id = None
                artifact_row_id = None
                rejection_row_id = None
                if link.extraction_run_id is None:
                    extraction_receipt = None
                elif not isinstance(extraction_receipt, str):
                    raise ValueError(
                        "extraction link requires its durable acceptance receipt"
                    )
                else:
                    accepted_pair = session.execute(
                        select(
                            ExtractionOutcomeAcceptanceRow,
                            ExtractionOutcomePlanRow,
                        )
                        .join(
                            ExtractionOutcomePlanRow,
                            ExtractionOutcomePlanRow.id
                            == ExtractionOutcomeAcceptanceRow.plan_id,
                        )
                        .where(
                            ExtractionOutcomeAcceptanceRow.receipt_ref
                            == extraction_receipt
                        )
                    ).one_or_none()
                    if (
                        accepted_pair is None
                        or accepted_pair[1].extraction_run_id
                        != link.extraction_run_id
                    ):
                        raise InvalidArtifactRequirement(
                            "extraction receipt is not bound to the linked run"
                        )
                    from argus.extraction.outcomes import (
                        AcceptedExtractionOutcome,
                        ExtractionAcceptanceReceipt,
                    )

                    durable_projection = _deserialize_extraction_projection(
                        _parse_json_value(accepted_pair[0].projection_json)
                    )
                    durable_accepted = AcceptedExtractionOutcome.accepted(
                        durable_projection,
                        ExtractionAcceptanceReceipt(
                            receipt_ref=accepted_pair[0].receipt_ref,
                            accepted_at=(
                                accepted_pair[0].accepted_at.isoformat() + "Z"
                            ),
                            scope=accepted_pair[0].scope,
                        ),
                    )
                    if link.accepted_outcome != durable_accepted:
                        raise InvalidArtifactRequirement(
                            "link does not match durable accepted extraction"
                        )
                    extraction_plan_id = accepted_pair[1].id
                    if link.artifact_ref is not None:
                        artifact_row = session.scalar(
                            select(ExtractionOutcomeArtifactRow).where(
                                ExtractionOutcomeArtifactRow.plan_id
                                == extraction_plan_id,
                                ExtractionOutcomeArtifactRow.artifact_ref
                                == link.artifact_ref,
                            )
                        )
                        if (
                            artifact_row is None
                            or artifact_row.content_identity
                            != link.artifact_identity
                            or accepted_pair[1].access_scope
                            != link.access_scope
                        ):
                            raise InvalidArtifactRequirement(
                                "artifact is not bound to the accepted plan"
                            )
                        artifact_row_id = artifact_row.id
                    if link.rejection_ref is not None:
                        rejection_row = session.scalar(
                            select(ExtractionOutcomeRejectionRow).where(
                                ExtractionOutcomeRejectionRow.plan_id
                                == extraction_plan_id,
                                ExtractionOutcomeRejectionRow.rejection_ref
                                == link.rejection_ref,
                            )
                        )
                        if rejection_row is None:
                            raise InvalidArtifactRequirement(
                                "rejection is not bound to the accepted plan"
                            )
                        rejection_row_id = rejection_row.id
                session.add(
                    ResultExtractionLinkRow(
                        id=uuid.uuid4().hex,
                        composition_ref=receipt_ref,
                        result_cluster_ref=link.result_cluster_ref,
                        extraction_acceptance_ref=extraction_receipt,
                        extraction_plan_id=extraction_plan_id,
                        artifact_row_id=artifact_row_id,
                        artifact_plan_id=(
                            extraction_plan_id
                            if artifact_row_id is not None
                            else None
                        ),
                        rejection_row_id=rejection_row_id,
                        rejection_plan_id=(
                            extraction_plan_id
                            if rejection_row_id is not None
                            else None
                        ),
                        reuse_origin=link.reuse_origin,
                    )
                )
        scope = (
            "sqlite_development"
            if self.session_factory.kw["bind"].dialect.name == "sqlite"
            else "postgresql_authority"
        )
        return CompositionAcceptanceReceipt(
            receipt_ref=receipt_ref,
            accepted_at=now.isoformat() + "Z",
            scope=scope,
        )

    def record_extraction(
        self,
        *,
        url: str,
        domain: str | None,
        mode: str,
        caller: str,
        result,
        latency_ms: int,
        extraction_run_id: str | None = None,
    ) -> ExtractionReceipt:
        """Atomically store a normalized extraction and its attempt history."""
        from argus.extraction.rejection import classify_extraction_rejection

        public_id = extraction_run_id or uuid.uuid4().hex
        selected = result.extractor.value if result.extractor else None
        rejection = classify_extraction_rejection(result)
        content_hash = (
            hashlib.sha256(result.text.encode("utf-8")).hexdigest()
            if result.text
            else None
        )
        attempts = list(result.attempts)
        if not attempts:
            from argus.extraction.models import ExtractionAttempt

            attempts = [
                ExtractionAttempt(
                    extractor=name,
                    status=(
                        "success" if name == selected and not result.error else "failed"
                    ),
                    latency_ms=0,
                    failure_summary=result.error
                    if name == result.extractors_tried[-1]
                    else None,
                )
                for name in result.extractors_tried
            ]
        artifact_state = {
            "canonical_url": _safe_persisted_url(result.url),
            "content_hash": content_hash,
            "source_type": result.source_type,
            "egress": result.egress,
            "machine": result.machine,
            "auth_used": result.auth_used,
            "cookies_used": result.cookies_used,
            "archive_used": result.archive_used,
            "cost": result.cost,
        }
        state = {
            "request_url": _safe_persisted_url(url),
            "domain": domain,
            "mode": mode,
            "caller": caller,
            "status": "failed" if result.error and not result.text else "succeeded",
            "selected_extractor": selected,
            "content_hash": content_hash,
            "title": result.title,
            "author": result.author,
            "published_date": result.date,
            "word_count": result.word_count,
            "latency_ms": max(0, int(latency_ms)),
            "quality_passed": bool(result.quality_passed),
            "quality_reason": result.quality_reason,
            "error_summary": safe_failure_summary(result.error),
            "attempts": [
                {
                    "extractor": attempt.extractor,
                    "status": attempt.status,
                    "latency_ms": max(0, int(attempt.latency_ms)),
                    "failure_summary": safe_failure_summary(attempt.failure_summary),
                }
                for attempt in attempts
            ],
            "artifact": artifact_state,
        }
        fingerprint = acceptance_fingerprint(state)

        with self.session_factory.begin() as session:
            existing = session.scalar(
                select(ExtractionRunRow).where(
                    ExtractionRunRow.extraction_run_id == public_id
                )
            )
            if existing is not None:
                if existing.acceptance_fingerprint != fingerprint:
                    raise AcceptanceConflictError(
                        f"extraction {public_id!r} has a different durable payload"
                    )
                delivery_id = session.scalar(
                    select(DeliveryIntentRow.id).where(
                        DeliveryIntentRow.extraction_run_id == existing.id
                    )
                )
                if delivery_id is None:
                    raise AcceptanceConflictError(
                        f"extraction {public_id!r} is incomplete"
                    )
                return ExtractionReceipt(
                    extraction_run_id=public_id,
                    delivery_intent_id=delivery_id,
                )

            now = self.clock()
            ledger_id = uuid.uuid4().hex
            if content_hash:
                self._ensure_content_identity(
                    session, content_hash, state["artifact"]["canonical_url"], now
                )
            session.add(
                ExtractionRunRow(
                    id=ledger_id,
                    extraction_run_id=public_id,
                    request_url=state["request_url"],
                    domain=domain,
                    mode=mode,
                    caller=caller,
                    status=state["status"],
                    selected_extractor=selected,
                    content_hash=content_hash,
                    title=result.title,
                    author=result.author,
                    published_date=result.date,
                    word_count=result.word_count,
                    latency_ms=state["latency_ms"],
                    quality_passed=state["quality_passed"],
                    quality_reason=result.quality_reason,
                    error_summary=state["error_summary"],
                    acceptance_fingerprint=fingerprint,
                    started_at=now,
                    committed_at=now,
                )
            )
            session.flush()
            for ordinal, attempt in enumerate(state["attempts"]):
                session.add(
                    ExtractorAttemptRow(
                        id=uuid.uuid4().hex,
                        run_id=ledger_id,
                        ordinal=ordinal,
                        **attempt,
                    )
                )
            artifact = state["artifact"]
            session.add(
                ExtractionArtifactRow(
                    id=uuid.uuid4().hex,
                    run_id=ledger_id,
                    canonical_url=artifact["canonical_url"],
                    content_hash=content_hash,
                    source_type=artifact["source_type"],
                    egress=artifact["egress"],
                    machine=artifact["machine"],
                    auth_used=artifact["auth_used"],
                    cookies_used=artifact["cookies_used"],
                    archive_used=artifact["archive_used"],
                    cost=artifact["cost"],
                    metadata_json=_canonical_json(
                        {
                            "quality_reason": result.quality_reason,
                            "extractors_tried": list(result.extractors_tried),
                            "cache_hit": bool(result.cache_hit),
                            "source_extractor": result.cache_source_extractor,
                            "rejection": (
                                rejection.to_dict() if rejection is not None else None
                            ),
                        }
                    ),
                )
            )
            delivery_id = None
            if excludes_capture(caller):
                payload_json = None
                maya_content_hash = None
                delivery_status = "suppressed"
            else:
                payload, maya_content_hash = extraction_capture_payload(
                    public_id=public_id,
                    mode=mode,
                    result=result,
                    completed_at=now,
                )
                payload_json = maya_payload_json(payload)
                delivery_status = "pending"
            delivery_id = uuid.uuid4().hex
            session.add(
                DeliveryIntentRow(
                    id=delivery_id,
                    run_id=None,
                    extraction_run_id=ledger_id,
                    destination="maya",
                    status=delivery_status,
                    payload_json=payload_json,
                    payload_sha256=hashlib.sha256(
                        (payload_json or "").encode("utf-8")
                    ).hexdigest(),
                    content_sha256=maya_content_hash,
                    attempt_count=0,
                    max_attempts=8,
                    next_attempt_at=now,
                    updated_at=now,
                    created_at=now,
                )
            )
        return ExtractionReceipt(
            extraction_run_id=public_id,
            delivery_intent_id=delivery_id,
        )

    def claim_maya_outbox(
        self,
        *,
        now: datetime,
        limit: int,
        lease_seconds: int = 60,
    ) -> list[dict]:
        """Claim a bounded delivery batch, including leases left by a restart."""
        bounded_limit = max(1, min(int(limit), 100))
        with self.session_factory.begin() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                from sqlalchemy import text

                session.execute(text("BEGIN IMMEDIATE"))
            exhausted_statement = (
                select(DeliveryIntentRow)
                .where(
                    DeliveryIntentRow.status == "delivering",
                    DeliveryIntentRow.lease_expires_at <= now,
                    DeliveryIntentRow.attempt_count >= DeliveryIntentRow.max_attempts,
                )
                .order_by(DeliveryIntentRow.lease_expires_at, DeliveryIntentRow.id)
                .limit(100)
            )
            if dialect == "postgresql":
                exhausted_statement = exhausted_statement.with_for_update(
                    skip_locked=True
                )
            for exhausted in session.scalars(exhausted_statement):
                exhausted.status = "dead_letter"
                exhausted.updated_at = now
                exhausted.lease_expires_at = None
                exhausted.lease_token = None
                exhausted.last_error_code = "retry_exhausted"
                exhausted.last_error_summary = (
                    "Delivery lease expired after the final bounded attempt"
                )
            statement = (
                select(DeliveryIntentRow)
                .where(
                    DeliveryIntentRow.payload_json.is_not(None),
                    DeliveryIntentRow.attempt_count < DeliveryIntentRow.max_attempts,
                    (
                        (
                            DeliveryIntentRow.status.in_(("pending", "retry"))
                            & (DeliveryIntentRow.next_attempt_at <= now)
                        )
                        | (
                            (DeliveryIntentRow.status == "delivering")
                            & (DeliveryIntentRow.lease_expires_at <= now)
                        )
                    ),
                )
                .order_by(DeliveryIntentRow.created_at, DeliveryIntentRow.id)
                .limit(bounded_limit)
            )
            if dialect == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list(session.scalars(statement))
            claimed = []
            for row in rows:
                token = uuid.uuid4().hex
                row.status = "delivering"
                row.lease_expires_at = now + timedelta(seconds=lease_seconds)
                row.lease_token = token
                row.updated_at = now
                claimed.append(
                    {
                        "id": row.id,
                        "payload_json": row.payload_json,
                        "attempt_count": row.attempt_count,
                        "max_attempts": row.max_attempts,
                        "lease_token": token,
                        "previous_last_attempt_at": row.last_attempt_at,
                    }
                )
            return claimed

    def begin_maya_outbox_attempt(
        self,
        intent_id: str,
        *,
        lease_token: str,
        now: datetime,
    ) -> dict | None:
        """Count an attempt only immediately before its external HTTP call."""
        with self.session_factory.begin() as session:
            row = self._active_maya_lease(
                session,
                intent_id=intent_id,
                lease_token=lease_token,
                now=now,
            )
            if row is None or row.attempt_count >= row.max_attempts:
                return None
            row.attempt_count += 1
            row.last_attempt_at = now
            row.updated_at = now
            return {
                "attempt_count": row.attempt_count,
                "max_attempts": row.max_attempts,
            }

    def release_maya_outbox_claim(
        self,
        intent_id: str,
        *,
        lease_token: str,
        now: datetime,
        attempt_started: bool = False,
        previous_last_attempt_at: datetime | None = None,
    ) -> bool:
        """CAS-release a lease when shutdown prevents external delivery."""
        with self.session_factory.begin() as session:
            row = self._active_maya_lease(
                session,
                intent_id=intent_id,
                lease_token=lease_token,
                now=now,
            )
            if row is None:
                return False
            if attempt_started:
                row.attempt_count = max(0, row.attempt_count - 1)
                row.last_attempt_at = previous_last_attempt_at
            row.status = "pending" if row.attempt_count == 0 else "retry"
            row.lease_expires_at = None
            row.lease_token = None
            row.updated_at = now
            return True

    def acknowledge_maya_outbox(
        self,
        intent_id: str,
        *,
        lease_token: str,
        response: dict,
        now: datetime,
    ) -> bool:
        with self.session_factory.begin() as session:
            row = self._active_maya_lease(
                session,
                intent_id=intent_id,
                lease_token=lease_token,
                now=now,
            )
            if row is None:
                return False
            response_json = _canonical_json(response)
            if len(response_json.encode("utf-8")) > 4096:
                return False
            row.status = "acknowledged"
            row.delivered_at = now
            row.updated_at = now
            row.lease_expires_at = None
            row.lease_token = None
            row.last_error_code = None
            row.last_error_summary = None
            row.response_json = response_json
            return True

    def fail_maya_outbox(
        self,
        intent_id: str,
        *,
        lease_token: str,
        transient: bool,
        error_code: str,
        error_summary: str,
        now: datetime,
    ) -> str | None:
        with self.session_factory.begin() as session:
            row = self._active_maya_lease(
                session,
                intent_id=intent_id,
                lease_token=lease_token,
                now=now,
            )
            if row is None:
                return None
            exhausted = row.attempt_count >= row.max_attempts
            row.status = "retry" if transient and not exhausted else "dead_letter"
            if row.status == "retry":
                delay = min(3600, 5 * (2 ** max(0, row.attempt_count - 1)))
                row.next_attempt_at = now + timedelta(seconds=delay)
            row.updated_at = now
            row.lease_expires_at = None
            row.lease_token = None
            row.last_error_code = (
                "retry_exhausted" if transient and exhausted else error_code[:64]
            )
            row.last_error_summary = (
                safe_failure_summary(error_summary) or "delivery failed"
            )
            return row.status

    @staticmethod
    def _active_maya_lease(
        session,
        *,
        intent_id: str,
        lease_token: str,
        now: datetime,
    ) -> DeliveryIntentRow | None:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            from sqlalchemy import text

            session.execute(text("BEGIN IMMEDIATE"))
        statement = select(DeliveryIntentRow).where(
            DeliveryIntentRow.id == intent_id,
            DeliveryIntentRow.status == "delivering",
            DeliveryIntentRow.lease_token == lease_token,
            DeliveryIntentRow.lease_expires_at > now,
        )
        if dialect == "postgresql":
            statement = statement.with_for_update()
        return session.scalar(statement)

    def recover_maya_dead_letter(
        self,
        intent_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        recovered_at = now or self.clock()
        with self.session_factory.begin() as session:
            row = session.get(DeliveryIntentRow, intent_id)
            if row is None or row.status != "dead_letter" or row.payload_json is None:
                return False
            row.status = "pending"
            row.attempt_count = 0
            row.next_attempt_at = recovered_at
            row.updated_at = recovered_at
            row.last_error_code = None
            row.last_error_summary = None
            return True

    def list_maya_dead_letters(self, *, limit: int = 50) -> list[dict]:
        """List bounded, payload-free operator evidence for recovery."""
        with self.session_factory() as session:
            rows = list(
                session.scalars(
                    select(DeliveryIntentRow)
                    .where(DeliveryIntentRow.status == "dead_letter")
                    .order_by(DeliveryIntentRow.updated_at, DeliveryIntentRow.id)
                    .limit(max(1, min(int(limit), 100)))
                )
            )
            return [
                {
                    "id": row.id,
                    "attempt_count": row.attempt_count,
                    "last_error_code": row.last_error_code,
                    "last_error_summary": row.last_error_summary,
                    "created_at": row.created_at,
                    "updated_at": row.updated_at,
                }
                for row in rows
            ]

    def compact_maya_outbox(
        self,
        *,
        acknowledged_before: datetime,
        limit: int = 100,
        now: datetime | None = None,
        stop_event: threading.Event | None = None,
    ) -> int:
        if stop_event is not None and stop_event.is_set():
            return 0
        compacted_at = now or self.clock()
        with self.session_factory.begin() as session:
            statement = (
                select(DeliveryIntentRow)
                .where(
                    DeliveryIntentRow.status == "acknowledged",
                    DeliveryIntentRow.delivered_at < acknowledged_before,
                    DeliveryIntentRow.payload_json.is_not(None),
                )
                .order_by(DeliveryIntentRow.delivered_at, DeliveryIntentRow.id)
                .limit(max(1, min(int(limit), 1000)))
            )
            if session.get_bind().dialect.name == "postgresql":
                statement = statement.with_for_update(skip_locked=True)
            rows = list(session.scalars(statement))
            if stop_event is not None and stop_event.is_set():
                return 0
            for row in rows:
                row.payload_json = None
                row.payload_compacted_at = compacted_at
                row.updated_at = compacted_at
            return len(rows)

    def operational_status(
        self,
        *,
        now: datetime | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict:
        """Return bounded authority/schema/outbox truth for cached status refresh."""
        if stop_event is not None and stop_event.is_set():
            return {}
        observed_at = now or self.clock()
        if observed_at.tzinfo is not None:
            observed_at = observed_at.replace(tzinfo=None)
        with self.session_factory() as session:
            backend = session.get_bind().dialect.name
            session.execute(text("SELECT 1"))
            schema_head = "sqlite-managed"
            if backend == "postgresql" and not (
                stop_event is not None and stop_event.is_set()
            ):
                schema_head = session.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one()
        if stop_event is not None and stop_event.is_set():
            return {}
        outbox = self.maya_outbox_status(
            now=observed_at,
            stop_event=stop_event,
        )
        if stop_event is not None and stop_event.is_set():
            return {}
        return {
            "backend": backend,
            "connected": True,
            "schema_head": schema_head,
            "outbox": outbox,
        }

    def close(self) -> None:
        """Dispose idle connections after lifecycle-owned work has joined."""
        engine = self.session_factory.kw.get("bind")
        if engine is not None:
            engine.dispose()

    def maya_outbox_status(
        self,
        *,
        now: datetime | None = None,
        stop_event: threading.Event | None = None,
    ) -> dict:
        if stop_event is not None and stop_event.is_set():
            return {}
        observed_at = now or self.clock()
        with self.session_factory() as session:
            counts = {
                status: count
                for status, count in session.execute(
                    select(
                        DeliveryIntentRow.status,
                        func.count(DeliveryIntentRow.id),
                    ).group_by(DeliveryIntentRow.status)
                )
            }
            if stop_event is not None and stop_event.is_set():
                return {}
            oldest_pending = session.scalar(
                select(func.min(DeliveryIntentRow.created_at)).where(
                    DeliveryIntentRow.status.in_(("pending", "retry", "delivering"))
                )
            )
            if stop_event is not None and stop_event.is_set():
                return {}
            oldest_dead = session.scalar(
                select(func.min(DeliveryIntentRow.created_at)).where(
                    DeliveryIntentRow.status == "dead_letter"
                )
            )
            if stop_event is not None and stop_event.is_set():
                return {}
            dead_letter_payload_bytes = session.scalar(
                select(
                    func.coalesce(
                        func.sum(func.length(DeliveryIntentRow.payload_json)), 0
                    )
                ).where(DeliveryIntentRow.status == "dead_letter")
            )

        def age(value):
            return (
                max(0, int((observed_at - value).total_seconds()))
                if value is not None
                else None
            )

        return {
            "counts": counts,
            "oldest_pending_age_seconds": age(oldest_pending),
            "dead_letter_oldest_age_seconds": age(oldest_dead),
            "dead_letter_payload_bytes": int(dead_letter_payload_bytes or 0),
        }

    def create_session(
        self, session_id: str, created_at: datetime | None = None
    ) -> None:
        """Create a durable multi-turn session, idempotently."""
        with self.session_factory.begin() as session:
            values = {
                "id": session_id,
                "created_at": created_at or self.clock(),
            }
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert

                statement = insert(RetrievalSessionRow).values(**values)
                session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
            elif dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert

                statement = insert(RetrievalSessionRow).values(**values)
                session.execute(statement.on_conflict_do_nothing(index_elements=["id"]))
            elif session.get(RetrievalSessionRow, session_id) is None:
                session.add(RetrievalSessionRow(**values))

    def session_exists(self, session_id: str) -> bool:
        with self.session_factory() as session:
            return session.get(RetrievalSessionRow, session_id) is not None

    def append_session_query(
        self,
        session_id: str,
        *,
        query: str,
        mode: str,
        timestamp: datetime,
        results_count: int,
    ) -> int:
        with self.session_factory.begin() as session:
            dialect = session.get_bind().dialect.name
            if dialect == "sqlite":
                from sqlalchemy import text

                session.execute(text("BEGIN IMMEDIATE"))
            statement = select(RetrievalSessionRow).where(
                RetrievalSessionRow.id == session_id
            )
            if dialect == "postgresql":
                statement = statement.with_for_update()
            if session.scalar(statement) is None:
                raise KeyError(f"unknown session {session_id!r}")
            previous = session.scalar(
                select(func.max(SessionQueryRow.ordinal)).where(
                    SessionQueryRow.session_id == session_id
                )
            )
            ordinal = 0 if previous is None else previous + 1
            session.add(
                SessionQueryRow(
                    id=uuid.uuid4().hex,
                    session_id=session_id,
                    ordinal=ordinal,
                    query_text=query,
                    mode=mode,
                    queried_at=timestamp,
                    results_count=results_count,
                )
            )
        return ordinal

    def append_session_extracted_url(
        self, session_id: str, query_index: int, url: str
    ) -> None:
        sanitized_url = _safe_persisted_url(url)
        with self.session_factory.begin() as session:
            query_row = session.scalar(
                select(SessionQueryRow).where(
                    SessionQueryRow.session_id == session_id,
                    SessionQueryRow.ordinal == query_index,
                )
            )
            if query_row is None:
                raise IndexError("session query index is out of range")
            values = {
                "id": uuid.uuid4().hex,
                "query_id": query_row.id,
                "url": sanitized_url,
            }
            dialect = session.get_bind().dialect.name
            if dialect == "postgresql":
                from sqlalchemy.dialects.postgresql import insert

                statement = insert(SessionExtractedUrlRow).values(**values)
                session.execute(
                    statement.on_conflict_do_nothing(index_elements=["query_id", "url"])
                )
            elif dialect == "sqlite":
                from sqlalchemy.dialects.sqlite import insert

                statement = insert(SessionExtractedUrlRow).values(**values)
                session.execute(
                    statement.on_conflict_do_nothing(index_elements=["query_id", "url"])
                )
            elif (
                session.scalar(
                    select(SessionExtractedUrlRow.id).where(
                        SessionExtractedUrlRow.query_id == query_row.id,
                        SessionExtractedUrlRow.url == sanitized_url,
                    )
                )
                is None
            ):
                session.add(SessionExtractedUrlRow(**values))

    def load_session(self, session_id: str):
        from argus.sessions.models import QueryRecord, Session

        with self.session_factory() as session:
            row = session.get(RetrievalSessionRow, session_id)
            if row is None:
                return None
            query_rows = list(
                session.scalars(
                    select(SessionQueryRow)
                    .where(SessionQueryRow.session_id == session_id)
                    .order_by(SessionQueryRow.ordinal)
                )
            )
            records = []
            for query_row in query_rows:
                urls = list(
                    session.scalars(
                        select(SessionExtractedUrlRow.url)
                        .where(SessionExtractedUrlRow.query_id == query_row.id)
                        .order_by(SessionExtractedUrlRow.id)
                    )
                )
                records.append(
                    QueryRecord(
                        query=query_row.query_text,
                        mode=query_row.mode,
                        timestamp=query_row.queried_at,
                        results_count=query_row.results_count,
                        extracted_urls=urls,
                    )
                )
            return Session(id=row.id, created_at=row.created_at, queries=records)

    def list_session_ids(self) -> list[str]:
        with self.session_factory() as session:
            return list(
                session.scalars(
                    select(RetrievalSessionRow.id).order_by(
                        RetrievalSessionRow.created_at.desc()
                    )
                )
            )

    def import_session(self, snapshot) -> None:
        """Import one complete legacy session in a single transaction."""
        with self.session_factory.begin() as session:
            if session.get(RetrievalSessionRow, snapshot.id) is not None:
                raise AcceptanceConflictError(f"session {snapshot.id!r} already exists")
            session.add(
                RetrievalSessionRow(
                    id=snapshot.id,
                    created_at=snapshot.created_at,
                )
            )
            session.flush()
            for ordinal, query in enumerate(snapshot.queries):
                query_id = uuid.uuid4().hex
                session.add(
                    SessionQueryRow(
                        id=query_id,
                        session_id=snapshot.id,
                        ordinal=ordinal,
                        query_text=query.query,
                        mode=query.mode,
                        queried_at=query.timestamp,
                        results_count=query.results_count,
                    )
                )
                session.flush()
                for url in query.extracted_urls:
                    session.add(
                        SessionExtractedUrlRow(
                            id=uuid.uuid4().hex,
                            query_id=query_id,
                            url=_safe_persisted_url(url),
                        )
                    )

    @staticmethod
    def _ensure_content_identity(session, content_hash, url, created_at):
        values = {
            "content_hash": content_hash,
            "canonical_url": url,
            "created_at": created_at,
        }
        dialect = session.get_bind().dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert

            statement = insert(ContentIdentityRow).values(**values)
            session.execute(
                statement.on_conflict_do_nothing(index_elements=["content_hash"])
            )
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            statement = insert(ContentIdentityRow).values(**values)
            session.execute(
                statement.on_conflict_do_nothing(index_elements=["content_hash"])
            )
        elif session.get(ContentIdentityRow, content_hash) is None:
            session.add(ContentIdentityRow(**values))


def create_search_ledger_repository(
    db_url: str | None = None,
    *,
    create_schema: bool | None = None,
    clock: Callable[[], datetime] = _system_now,
) -> SqlAlchemySearchLedgerRepository:
    """Build the SQLAlchemy adapter.

    SQLite is the development adapter and creates its local schema. PostgreSQL
    schema changes are applied only through Alembic.
    """
    url = db_url or get_config().db_url
    is_sqlite = url.startswith("sqlite:")
    if is_sqlite and url.startswith("sqlite:///"):
        path = url.removeprefix("sqlite:///")
        if path and path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(
        url,
        pool_pre_ping=True,
        **_bounded_engine_options(url),
    )
    should_create = is_sqlite if create_schema is None else create_schema
    if should_create:
        if not is_sqlite:
            raise ValueError("runtime schema creation is only supported for SQLite")
        LedgerBase.metadata.create_all(engine)
    return SqlAlchemySearchLedgerRepository(
        sessionmaker(bind=engine, expire_on_commit=False),
        clock=clock,
    )


def create_read_only_search_ledger_repository(
    db_url: str,
) -> SqlAlchemySearchLedgerRepository:
    """Open an existing SQLite ledger without creating schema or writing files."""
    if not db_url.startswith("sqlite:///"):
        return create_search_ledger_repository(db_url, create_schema=False)

    from urllib.parse import quote

    raw_path = db_url.removeprefix("sqlite:///")
    path = Path(raw_path).expanduser().resolve()
    read_only_url = f"sqlite:///file:{quote(str(path), safe='/')}?mode=ro&uri=true"
    engine = create_engine(
        read_only_url,
        pool_pre_ping=True,
        **_bounded_engine_options(read_only_url),
    )
    return SqlAlchemySearchLedgerRepository(
        sessionmaker(bind=engine, expire_on_commit=False)
    )


create_search_ledger_repository.lifecycle_capability = (
    LifecycleCapability.FINITE_BOUNDED
)
