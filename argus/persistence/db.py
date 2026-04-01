"""
Database engine, session, and migration helpers.
"""

import json
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from argus.config import get_config
from argus.logging import get_logger
from argus.persistence.models import Base, ProviderUsageRow, SearchEvidenceRow, SearchQueryRow, SearchResultRow, SearchRunRow
from argus.models import ProviderTrace, SearchQuery, SearchResponse

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
    logger.info("Database initialized and tables created")


def get_engine():
    if _engine is None:
        init_db()
    return _engine


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
