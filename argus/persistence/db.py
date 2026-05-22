"""
Database engine, session, and migration helpers.
"""

import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from argus.config import get_config
from argus.logging import get_logger
from argus.persistence.models import (
    Base,
    CorpusDocumentRow,
    CorpusSnapshotRow,
    CrawlRunRow,
    ProviderUsageRow,
    SearchQueryRow,
    SearchResultRow,
    SearchRunRow,
    WorkflowArtifactRow,
    WorkflowCitationRow,
    WorkflowRunRow,
)
from argus.models import SearchQuery, SearchResponse

logger = get_logger("persistence.db")

_engine = None
_session_factory = None


def init_db(db_url: Optional[str] = None):
    """Initialize the database engine and create tables."""
    global _engine, _session_factory
    if db_url is None:
        db_url = get_config().db_url

    _engine = create_engine(db_url, pool_pre_ping=True)
    _session_factory = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)
    _ensure_schema_compat(_engine)
    logger.info("Database initialized and tables created")


def get_engine():
    if _engine is None:
        init_db()
    return _engine


def _ensure_schema_compat(engine) -> None:
    """Apply lightweight additive migrations for pre-migration installs.

    SQLAlchemy's create_all creates missing tables but does not add columns to
    existing tables. Argus keeps persistence best-effort, so additive column
    upgrades live here until the project adopts a formal migration tool.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    additive_columns = {
        "search_results": {
            "egress": "VARCHAR(50)",
            "machine": "VARCHAR(100)",
            "metadata_json": "TEXT",
        },
        "corpus_documents": {
            "egress": "VARCHAR(50)",
            "machine": "VARCHAR(100)",
            "metadata_json": "TEXT",
        },
    }

    with engine.begin() as conn:
        for table_name, columns in additive_columns.items():
            if table_name not in existing_tables:
                continue
            existing_columns = {
                column["name"]
                for column in inspector.get_columns(table_name)
            }
            for column_name, column_type in columns.items():
                if column_name in existing_columns:
                    continue
                conn.execute(
                    text(
                        f"ALTER TABLE {table_name} "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
                )
                logger.info("Added missing column %s.%s", table_name, column_name)


def get_session_factory():
    if _session_factory is None:
        init_db()
    return _session_factory


@contextmanager
def get_session() -> Generator[Session, None, None]:
    """Provide a transactional scope around a series of operations."""
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def persist_search(query_text: str, mode: str, response: SearchResponse) -> Optional[str]:
    """Persist a complete search query, run, results, and traces."""
    run_id = response.search_run_id or uuid.uuid4().hex[:16]

    with get_session() as session:
        # Upsert query
        q_row = SearchQueryRow(query_text=query_text, mode=mode, max_results=response.total_results)
        session.add(q_row)
        session.flush()

        # Create run
        run_row = SearchRunRow(
            query_id=q_row.id,
            search_run_id=run_id,
            status="completed",
            total_results=len(response.results),
            cached=response.cached,
            finished_at=datetime.now(tz=None),
        )
        session.add(run_row)
        session.flush()

        # Persist results
        for rank, r in enumerate(response.results):
            result_row = SearchResultRow(
                run_id=run_row.id,
                url=r.url,
                title=r.title,
                snippet=r.snippet,
                domain=r.domain or "",
                provider=r.provider.value if r.provider else "",
                score=r.score,
                final_rank=rank,
                egress=r.metadata.get("egress") if r.metadata else None,
                machine=r.metadata.get("machine") if r.metadata else None,
                metadata_json=json.dumps(r.metadata) if r.metadata else None,
            )
            session.add(result_row)

        # Persist traces
        for trace in response.traces:
            usage_row = ProviderUsageRow(
                run_id=run_row.id,
                provider=trace.provider.value,
                status=trace.status,
                results_count=trace.results_count,
                latency_ms=trace.latency_ms,
                error=trace.error,
                budget_remaining=trace.budget_remaining,
            )
            session.add(usage_row)

        logger.debug("Persisted search run %s with %d results", run_id, len(response.results))
        return run_id


class SearchPersistenceGateway:
    """Non-fatal persistence boundary for completed search responses."""

    def record_completed_search(self, query: SearchQuery, response: SearchResponse) -> Optional[str]:
        try:
            return persist_search(query.query, query.mode.value, response)
        except Exception as exc:
            logger.warning("Failed to persist search: %s", exc)
            return None


class WorkflowPersistenceGateway:
    """Best-effort persistence for workflow run metadata."""

    def record_run_state(self, payload: dict) -> Optional[str]:
        try:
            with get_session() as session:
                run_id = payload["run_id"]
                session.query(WorkflowRunRow).filter_by(workflow_run_id=run_id).delete()
                session.query(CorpusSnapshotRow).filter_by(workflow_run_id=run_id).delete()
                session.query(CrawlRunRow).filter_by(workflow_run_id=run_id).delete()
                session.query(CorpusDocumentRow).filter_by(workflow_run_id=run_id).delete()
                session.query(WorkflowArtifactRow).filter_by(workflow_run_id=run_id).delete()
                session.query(WorkflowCitationRow).filter_by(workflow_run_id=run_id).delete()

                session.add(
                    WorkflowRunRow(
                        workflow_run_id=run_id,
                        workflow_kind=payload["kind"],
                        target=payload["target"],
                        status=payload["status"],
                        snapshot_dir=payload.get("snapshot_dir", ""),
                        report_path=payload.get("report_path"),
                        manifest_path=payload.get("manifest_path"),
                        error=payload.get("error"),
                        metadata_json=json.dumps(payload.get("metadata", {})),
                        created_at=_parse_dt(payload.get("created_at")),
                        started_at=_parse_dt(payload.get("started_at")),
                        finished_at=_parse_dt(payload.get("finished_at")),
                    )
                )
                session.add(
                    CorpusSnapshotRow(
                        workflow_run_id=run_id,
                        snapshot_dir=payload.get("snapshot_dir", ""),
                        is_current=payload["status"] == "completed",
                        metadata_json=json.dumps(payload.get("metadata", {})),
                    )
                )

                documents = payload.get("documents", [])
                session.add(
                    CrawlRunRow(
                        workflow_run_id=run_id,
                        root_url=payload["target"],
                        candidate_count=payload.get("metadata", {}).get("candidate_urls", len(documents)),
                        captured_count=len(documents),
                        metadata_json=json.dumps(payload.get("metadata", {})),
                    )
                )

                for document in documents:
                    session.add(
                        CorpusDocumentRow(
                            workflow_run_id=run_id,
                            citation_id=document["id"],
                            source_type=document.get("source_type", "web"),
                            role=document.get("role", "source"),
                            title=document.get("title", ""),
                            url=document["url"],
                            domain=document.get("domain", ""),
                            artifact_path=document["artifact_path"],
                            extractor=document.get("extractor"),
                            word_count=document.get("word_count", 0),
                            egress=document.get("egress"),
                            machine=document.get("machine"),
                            metadata_json=json.dumps(document.get("metadata", {})),
                        )
                    )

                for artifact in payload.get("artifacts", []):
                    session.add(
                        WorkflowArtifactRow(
                            workflow_run_id=run_id,
                            artifact_kind=artifact["kind"],
                            path=artifact["path"],
                            description=artifact.get("description", ""),
                        )
                    )

                for citation in payload.get("citations", []):
                    session.add(
                        WorkflowCitationRow(
                            workflow_run_id=run_id,
                            citation_id=citation["id"],
                            title=citation.get("title", ""),
                            url=citation.get("url", ""),
                            artifact_path=citation["artifact_path"],
                            note=citation.get("note", ""),
                        )
                    )
                return run_id
        except Exception as exc:
            logger.warning("Failed to persist workflow state: %s", exc)
            return None


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
