import pytest
from sqlalchemy import event, func, select

from argus.extraction.models import ExtractedContent, ExtractionAttempt, ExtractorName
from argus.sessions.models import QueryRecord


def _repository(tmp_path):
    from argus.persistence.search_ledger import create_search_ledger_repository

    return create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'operations.db'}",
        create_schema=True,
    )


def test_extraction_is_committed_with_attempts_artifact_and_provenance(tmp_path):
    from argus.persistence.search_ledger import (
        ExtractionArtifactRow,
        ExtractionRunRow,
        ExtractorAttemptRow,
    )

    repository = _repository(tmp_path)
    result = ExtractedContent(
        url="https://example.com/article",
        title="An article",
        text="durable normalized content",
        author="Ada",
        date="2026-07-22",
        word_count=3,
        extractor=ExtractorName.JINA,
        source_type="paid_api",
        egress="datacenter",
        machine="homelab",
        cost=0.01,
        extractors_tried=["trafilatura", "jina"],
        attempts=[
            ExtractionAttempt(
                extractor="trafilatura",
                status="failed",
                latency_ms=12,
                failure_summary="no_content",
            ),
            ExtractionAttempt(
                extractor="jina",
                status="success",
                latency_ms=34,
            ),
        ],
    )

    receipt = repository.record_extraction(
        url="https://example.com/article",
        domain="example.com",
        mode="default",
        caller="maya",
        result=result,
        latency_ms=51,
        extraction_run_id="extract-1",
    )

    assert receipt.extraction_run_id == "extract-1"
    with repository.session_factory() as session:
        run = session.scalar(select(ExtractionRunRow))
        attempts = list(
            session.scalars(
                select(ExtractorAttemptRow).order_by(ExtractorAttemptRow.ordinal)
            )
        )
        artifact = session.scalar(select(ExtractionArtifactRow))

    assert run.status == "succeeded"
    assert run.latency_ms == 51
    assert run.selected_extractor == "jina"
    assert run.content_hash
    assert [(row.extractor, row.status, row.latency_ms) for row in attempts] == [
        ("trafilatura", "failed", 12),
        ("jina", "success", 34),
    ]
    assert artifact.content_hash == run.content_hash
    assert artifact.source_type == "paid_api"
    assert artifact.egress == "datacenter"
    assert artifact.machine == "homelab"


def test_failed_extraction_persists_only_a_safe_bounded_summary(tmp_path):
    from argus.persistence.search_ledger import ExtractionRunRow, ExtractorAttemptRow

    repository = _repository(tmp_path)
    secret = "Bearer should-never-be-stored"
    result = ExtractedContent(
        url="https://example.com/article?token=sensitive",
        error=f"HTTP 401 {secret} " + ("x" * 500),
        quality_passed=False,
        extractors_tried=["jina"],
        attempts=[
            ExtractionAttempt(
                extractor="jina",
                status="failed",
                latency_ms=10,
                failure_summary=f"HTTP 401 {secret}",
            )
        ],
    )

    repository.record_extraction(
        url="https://example.com/article?token=sensitive",
        domain=None,
        mode="default",
        caller="maya",
        result=result,
        latency_ms=11,
        extraction_run_id="extract-failed",
    )

    with repository.session_factory() as session:
        run = session.scalar(select(ExtractionRunRow))
        attempt = session.scalar(select(ExtractorAttemptRow))

    assert run.status == "failed"
    assert len(run.error_summary) <= 256
    assert "should-never-be-stored" not in run.error_summary
    assert "should-never-be-stored" not in attempt.failure_summary


def test_extraction_transaction_rolls_back_every_row(tmp_path):
    from argus.persistence.search_ledger import (
        ContentIdentityRow,
        ExtractionArtifactRow,
        ExtractionRunRow,
        ExtractorAttemptRow,
    )

    repository = _repository(tmp_path)

    @event.listens_for(ExtractionArtifactRow, "before_insert")
    def fail_artifact_insert(mapper, connection, target):
        raise RuntimeError("injected artifact failure")

    with pytest.raises(RuntimeError, match="injected artifact failure"):
        repository.record_extraction(
            url="https://example.com/rollback",
            domain=None,
            mode="default",
            caller="maya",
            result=ExtractedContent(
                url="https://example.com/rollback",
                text="must roll back",
                word_count=3,
                extractor=ExtractorName.TRAFILATURA,
                attempts=[
                    ExtractionAttempt(
                        extractor="trafilatura",
                        status="success",
                        latency_ms=1,
                    )
                ],
            ),
            latency_ms=2,
            extraction_run_id="extract-rollback",
        )

    event.remove(ExtractionArtifactRow, "before_insert", fail_artifact_insert)
    with repository.session_factory() as session:
        for table in (
            ExtractionRunRow,
            ExtractorAttemptRow,
            ExtractionArtifactRow,
            ContentIdentityRow,
        ):
            assert session.scalar(select(func.count()).select_from(table)) == 0


def test_sessions_continue_across_repository_and_store_restart(tmp_path):
    from argus.sessions.store import SessionStore

    database_url = f"sqlite:///{tmp_path / 'sessions.db'}"
    first_repository = _repository_for_url(database_url)
    first = SessionStore(repository=first_repository)
    first.create_session("restart")
    first.add_query(
        "restart",
        query="python web frameworks",
        mode="discovery",
        results_count=5,
    )
    first.add_extracted_url("restart", 0, "https://example.com/article")

    second_repository = _repository_for_url(database_url)
    second = SessionStore(repository=second_repository)
    loaded = second.get_session("restart")

    assert loaded is not None
    assert loaded.queries == [
        QueryRecord(
            query="python web frameworks",
            mode="discovery",
            timestamp=loaded.queries[0].timestamp,
            results_count=5,
            extracted_urls=["https://example.com/article"],
        )
    ]


def test_session_cache_does_not_advance_when_durable_append_fails():
    from argus.sessions.store import SessionStore

    class FailingSessionRepository:
        def create_session(self, session_id, created_at):
            pass

        def session_exists(self, session_id):
            return False

        def append_session_query(self, *args, **kwargs):
            raise RuntimeError("database unavailable")

    store = SessionStore(repository=FailingSessionRepository())
    session = store.create_session("atomic-session")

    with pytest.raises(RuntimeError, match="database unavailable"):
        store.add_query("atomic-session", query="must not leak into cache")

    assert session.queries == []


def test_session_cache_does_not_create_when_durable_create_fails():
    from argus.sessions.store import SessionStore

    class FailingSessionRepository:
        def session_exists(self, session_id):
            return False

        def create_session(self, session_id, created_at):
            raise RuntimeError("database unavailable")

        def load_session(self, session_id):
            return None

    store = SessionStore(repository=FailingSessionRepository())

    with pytest.raises(RuntimeError, match="database unavailable"):
        store.create_session("atomic-create")

    assert store.get_session("atomic-create") is None


def _repository_for_url(database_url):
    from argus.persistence.search_ledger import create_search_ledger_repository

    return create_search_ledger_repository(database_url, create_schema=True)


def test_usage_counts_search_and_extraction_runs_not_result_rows(tmp_path):
    from argus.models import SearchQuery
    from argus.persistence.usage import SqlAlchemyUsageRepository
    from tests.test_search_ledger import _response

    repository = _repository(tmp_path)
    repository.accept(SearchQuery(query="one operation", caller="maya"), _response())
    repository.record_extraction(
        url="https://example.com/extracted",
        domain=None,
        mode="default",
        caller="maya",
        result=ExtractedContent(
            url="https://example.com/extracted",
            text="content",
            word_count=1,
            extractor=ExtractorName.TRAFILATURA,
            egress="residential",
            machine="homelab",
            attempts=[
                ExtractionAttempt(
                    extractor="trafilatura",
                    status="success",
                    latency_ms=2,
                )
            ],
        ),
        latency_ms=3,
        extraction_run_id="usage-extraction",
    )

    usage = SqlAlchemyUsageRepository(repository.session_factory)

    assert sum(row["count"] for row in usage.get_daily_operation_counts()) == 2
    maya = next(row for row in usage.get_caller_activity() if row["caller"] == "maya")
    assert maya["attempted"] == 2
    assert maya["successes"] == 2


def test_legacy_session_import_is_dry_run_first_and_idempotent(tmp_path):
    import sqlite3

    from argus.persistence.reconcile import reconcile_legacy_sessions

    source = tmp_path / "legacy-sessions.db"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at REAL NOT NULL);
        CREATE TABLE session_queries (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            query TEXT NOT NULL,
            mode TEXT NOT NULL,
            timestamp REAL NOT NULL,
            results_count INTEGER NOT NULL
        );
        CREATE TABLE session_extracted_urls (
            id INTEGER PRIMARY KEY,
            session_id TEXT NOT NULL,
            query_index INTEGER NOT NULL,
            url TEXT NOT NULL
        );
        INSERT INTO sessions VALUES ('legacy', 1000);
        INSERT INTO session_queries VALUES
            (1, 'legacy', 'old query', 'research', 1001, 2);
        INSERT INTO session_extracted_urls VALUES
            (1, 'legacy', 0, 'https://example.com/old');
        """
    )
    connection.commit()
    connection.close()
    repository = _repository(tmp_path)

    assert reconcile_legacy_sessions(
        f"sqlite:///{source}", repository
    ) == {"source": 1, "imported": 1, "skipped": 0, "conflicting": 0}
    assert repository.load_session("legacy") is None

    assert reconcile_legacy_sessions(
        f"sqlite:///{source}", repository, apply=True
    ) == {"source": 1, "imported": 1, "skipped": 0, "conflicting": 0}
    assert reconcile_legacy_sessions(
        f"sqlite:///{source}", repository, apply=True
    ) == {"source": 1, "imported": 0, "skipped": 1, "conflicting": 0}
    assert repository.load_session("legacy").queries[0].extracted_urls == [
        "https://example.com/old"
    ]


def test_session_snapshot_import_rolls_back_as_one_transaction(tmp_path):
    from argus.persistence.search_ledger import (
        RetrievalSessionRow,
        SessionExtractedUrlRow,
        SessionQueryRow,
    )
    from argus.sessions.models import Session

    repository = _repository(tmp_path)

    @event.listens_for(SessionExtractedUrlRow, "before_insert")
    def fail_url_insert(mapper, connection, target):
        raise RuntimeError("injected URL failure")

    snapshot = Session(
        id="atomic-import",
        queries=[
            QueryRecord(
                query="legacy query",
                extracted_urls=["https://example.com/legacy"],
            )
        ],
    )
    with pytest.raises(RuntimeError, match="injected URL failure"):
        repository.import_session(snapshot)

    event.remove(SessionExtractedUrlRow, "before_insert", fail_url_insert)
    with repository.session_factory() as session:
        for table in (RetrievalSessionRow, SessionQueryRow, SessionExtractedUrlRow):
            assert session.scalar(select(func.count()).select_from(table)) == 0


def test_extract_endpoint_durably_records_before_acknowledging(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    repository = _repository(tmp_path)
    broker = MagicMock()
    broker.cache = MagicMock()
    broker.health_tracker = MagicMock()
    broker.budget_tracker = MagicMock()
    monkeypatch.setattr(
        "argus.extraction.extractor._extract_url_unpersisted",
        AsyncMock(
            return_value=ExtractedContent(
                url="https://example.com/api",
                text="api content",
                word_count=2,
                extractor=ExtractorName.TRAFILATURA,
                extractors_tried=["trafilatura"],
            )
        ),
    )
    client = TestClient(create_app(broker=broker, search_repository=repository))

    response = client.post(
        "/api/extract",
        json={"url": "https://example.com/api", "caller": "maya"},
    )

    assert response.status_code == 200
    from argus.persistence.search_ledger import ExtractionRunRow

    with repository.session_factory() as session:
        run = session.scalar(select(ExtractionRunRow))
    assert run is not None
    assert run.caller == "maya"


def test_extract_endpoint_returns_503_when_durable_commit_fails(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    from fastapi.testclient import TestClient

    from argus.api.main import create_app

    class FailingRepository:
        def record_extraction(self, **kwargs):
            raise RuntimeError("database unavailable")

    broker = MagicMock()
    broker.cache = MagicMock()
    broker.health_tracker = MagicMock()
    broker.budget_tracker = MagicMock()
    monkeypatch.setattr(
        "argus.extraction.extractor._extract_url_unpersisted",
        AsyncMock(
            return_value=ExtractedContent(
                url="https://example.com/api",
                text="not acknowledged",
                word_count=2,
                extractor=ExtractorName.TRAFILATURA,
            )
        ),
    )
    client = TestClient(
        create_app(broker=broker, search_repository=FailingRepository())
    )

    response = client.post(
        "/api/extract",
        json={"url": "https://example.com/api", "caller": "maya"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == "Extraction could not be durably recorded"


def test_legacy_session_reconciliation_cli_dry_run_does_not_create_target(tmp_path):
    import json
    import sqlite3

    from click.testing import CliRunner

    from argus.cli.main import cli

    source = tmp_path / "legacy-cli-sessions.db"
    target = tmp_path / "target-cli-sessions.db"
    connection = sqlite3.connect(source)
    connection.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, created_at REAL NOT NULL);
        CREATE TABLE session_queries (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL, query TEXT NOT NULL,
            mode TEXT NOT NULL, timestamp REAL NOT NULL, results_count INTEGER NOT NULL
        );
        CREATE TABLE session_extracted_urls (
            id INTEGER PRIMARY KEY, session_id TEXT NOT NULL,
            query_index INTEGER NOT NULL, url TEXT NOT NULL
        );
        INSERT INTO sessions VALUES ('legacy', 1000);
        """
    )
    connection.commit()
    connection.close()

    result = CliRunner().invoke(
        cli,
        [
            "ledger",
            "reconcile-sessions",
            "--source",
            f"sqlite:///{source}",
            "--target",
            f"sqlite:///{target}",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == {
        "source": 1,
        "imported": 1,
        "skipped": 0,
        "conflicting": 0,
    }
    assert not target.exists()


def test_postgresql_extraction_and_session_contract(migrated_postgres_ledger):
    from argus.persistence.search_ledger import ExtractionRunRow
    from argus.sessions.store import SessionStore

    repository = migrated_postgres_ledger
    repository.record_extraction(
        url="https://example.com/postgres",
        domain="example.com",
        mode="default",
        caller="postgres-test",
        result=ExtractedContent(
            url="https://example.com/postgres",
            text="postgres content",
            word_count=2,
            extractor=ExtractorName.TRAFILATURA,
            attempts=[
                ExtractionAttempt(
                    extractor="trafilatura",
                    status="success",
                    latency_ms=2,
                )
            ],
        ),
        latency_ms=3,
        extraction_run_id="postgres-extraction",
    )
    first = SessionStore(repository=repository)
    first.create_session("postgres-session")
    first.add_query("postgres-session", query="first turn", results_count=1)

    restarted = SessionStore(repository=repository)
    loaded = restarted.get_session("postgres-session")

    assert loaded.queries[0].query == "first turn"
    with repository.session_factory() as session:
        assert session.scalar(select(func.count()).select_from(ExtractionRunRow)) == 1
