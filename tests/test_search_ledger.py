from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from io import StringIO
import json
from threading import Barrier

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import DataError

from argus.models import (
    ProviderName,
    ProviderTrace,
    SearchMode,
    SearchQuery,
    SearchResponse,
    SearchResult,
)


def _response(run_id: str = "run-1", *, url: str = "https://example.com/article"):
    return SearchResponse(
        query="atomic search",
        mode=SearchMode.DISCOVERY,
        results=[
            SearchResult(
                url=url,
                title="Article",
                snippet="Normalized result",
                domain="example.com",
                provider=ProviderName.DUCKDUCKGO,
                score=0.75,
                metadata={
                    "egress": "residential",
                    "machine": "test-node",
                    "source_type": "search",
                },
            )
        ],
        traces=[
            ProviderTrace(
                provider=ProviderName.DUCKDUCKGO,
                status="success",
                results_count=1,
                latency_ms=12,
                egress="local",
            )
        ],
        total_results=1,
        search_run_id=run_id,
    )


def _sqlite_repository(tmp_path):
    from argus.persistence.search_ledger import create_search_ledger_repository

    return create_search_ledger_repository(
        f"sqlite:///{tmp_path / 'ledger.db'}",
        create_schema=True,
    )


def _table_counts(repository):
    from argus.persistence.search_ledger import (
        ContentIdentityRow,
        DeliveryIntentRow,
        NormalizedResultRow,
        ProviderAttemptRow,
        ResultProvenanceRow,
        RetrievalRequestRow,
        RetrievalRunRow,
    )

    tables = (
        RetrievalRequestRow,
        RetrievalRunRow,
        ProviderAttemptRow,
        NormalizedResultRow,
        ResultProvenanceRow,
        ContentIdentityRow,
        DeliveryIntentRow,
    )
    with repository.session_factory() as session:
        return {
            table.__tablename__: session.scalar(select(func.count()).select_from(table))
            for table in tables
        }


def _migrate_postgresql_ledger(url):
    from alembic import command
    from alembic.config import Config

    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")


def test_sqlite_repository_commits_complete_accepted_retrieval(tmp_path):
    from argus.persistence.search_ledger import RetrievalRequestRow

    repository = _sqlite_repository(tmp_path)
    query = SearchQuery(
        query="atomic search",
        mode=SearchMode.DISCOVERY,
        max_results=5,
        providers=[ProviderName.BRAVE, ProviderName.DUCKDUCKGO],
        free_only=True,
        caller="http",
    )

    receipt = repository.accept(query, _response())

    assert receipt.run_id == "run-1"
    assert receipt.delivery_intent_id
    assert _table_counts(repository) == {
        "retrieval_requests": 1,
        "retrieval_runs": 1,
        "provider_attempts": 1,
        "normalized_results": 1,
        "result_provenance": 1,
        "content_identities": 1,
        "delivery_intents": 1,
    }
    with repository.session_factory() as session:
        request = session.scalar(select(RetrievalRequestRow))
        assert json.loads(request.providers_json) == ["brave", "duckduckgo"]
        assert request.free_only is True
    assert repository.load_acceptance_snapshot("run-1").state["request"] == {
        "query_text": "atomic search",
        "mode": "discovery",
        "max_results": 5,
        "providers": ["brave", "duckduckgo"],
        "free_only": True,
        "caller": "http",
    }


def test_repository_flushes_ledger_parents_before_core_identity_upsert(tmp_path):
    from sqlalchemy import event

    repository = _sqlite_repository(tmp_path)
    engine = repository.session_factory.kw["bind"]

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(dbapi_connection, connection_record):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    engine.dispose()

    receipt = repository.accept(
        SearchQuery(query="atomic search"),
        _response(run_id="parent-before-child"),
    )

    assert receipt.run_id == "parent-before-child"
    assert _table_counts(repository)["retrieval_runs"] == 1


def test_sqlite_repository_rolls_back_every_row_when_result_is_invalid(tmp_path):
    repository = _sqlite_repository(tmp_path)
    invalid = _response(url=None)

    with pytest.raises(Exception):
        repository.accept(SearchQuery(query="atomic search"), invalid)

    assert all(count == 0 for count in _table_counts(repository).values())


def test_sqlite_repository_is_idempotent_for_same_run(tmp_path):
    repository = _sqlite_repository(tmp_path)
    query = SearchQuery(query="atomic search")

    first = repository.accept(query, _response())
    second = repository.accept(query, _response())

    assert second == first
    assert _table_counts(repository)["retrieval_runs"] == 1


def test_concurrent_same_payload_same_run_is_idempotent(tmp_path):
    repository = _sqlite_repository(tmp_path)
    query = SearchQuery(query="atomic search")

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda _: repository.accept(query, _response()),
                range(2),
            )
        )

    assert receipts[0] == receipts[1]
    counts = _table_counts(repository)
    assert counts["retrieval_runs"] == 1
    assert counts["normalized_results"] == 1
    assert counts["delivery_intents"] == 1


def test_same_run_rejects_a_different_payload(tmp_path):
    from argus.persistence.search_ledger import AcceptanceConflictError

    repository = _sqlite_repository(tmp_path)
    query = SearchQuery(query="atomic search")
    repository.accept(query, _response())
    changed = _response()
    changed.results[0].title = "Different title"

    with pytest.raises(AcceptanceConflictError):
        repository.accept(query, changed)


@pytest.mark.parametrize(
    "changed_query",
    [
        SearchQuery(
            query="atomic search",
            providers=[ProviderName.DUCKDUCKGO],
        ),
        SearchQuery(
            query="atomic search",
            free_only=True,
        ),
    ],
    ids=["providers", "free-only"],
)
def test_same_run_rejects_different_routing_fields(tmp_path, changed_query):
    from argus.persistence.search_ledger import AcceptanceConflictError

    repository = _sqlite_repository(tmp_path)
    repository.accept(SearchQuery(query="atomic search"), _response())

    with pytest.raises(AcceptanceConflictError):
        repository.accept(changed_query, _response())


def test_concurrent_same_run_accepts_one_payload_and_rejects_the_other(tmp_path):
    from argus.persistence.search_ledger import (
        AcceptanceConflictError,
        AcceptanceReceipt,
    )

    repository = _sqlite_repository(tmp_path)
    query = SearchQuery(query="atomic search")
    first = _response()
    second = _response(url="https://example.com/different")
    barrier = Barrier(2)

    def accept(response):
        barrier.wait()
        try:
            return repository.accept(query, response)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, (first, second)))

    assert sum(isinstance(outcome, AcceptanceReceipt) for outcome in outcomes) == 1
    assert sum(
        isinstance(outcome, AcceptanceConflictError) for outcome in outcomes
    ) == 1
    assert _table_counts(repository)["retrieval_runs"] == 1


def test_postgresql_repository_concurrent_acceptance_is_complete(postgres_ledger_url):
    from argus.persistence.search_ledger import create_search_ledger_repository

    _migrate_postgresql_ledger(postgres_ledger_url)
    repository = create_search_ledger_repository(postgres_ledger_url)
    query = SearchQuery(query="atomic search")

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(
            pool.map(
                lambda run_id: repository.accept(query, _response(run_id=run_id)),
                ("concurrent-1", "concurrent-2"),
            )
        )

    assert {receipt.run_id for receipt in receipts} == {"concurrent-1", "concurrent-2"}
    counts = _table_counts(repository)
    assert counts["retrieval_runs"] == 2
    assert counts["normalized_results"] == 2
    assert counts["delivery_intents"] == 2
    assert counts["content_identities"] == 1


def test_postgresql_concurrent_same_payload_same_run_is_idempotent(
    postgres_ledger_url,
):
    from argus.persistence.search_ledger import create_search_ledger_repository

    _migrate_postgresql_ledger(postgres_ledger_url)
    repository = create_search_ledger_repository(postgres_ledger_url)
    query = SearchQuery(query="atomic search")

    with ThreadPoolExecutor(max_workers=2) as pool:
        receipts = list(pool.map(lambda _: repository.accept(query, _response()), range(2)))

    assert receipts[0] == receipts[1]
    assert _table_counts(repository)["retrieval_runs"] == 1


def test_postgresql_concurrent_different_payload_same_run_conflicts(
    postgres_ledger_url,
):
    from argus.persistence.search_ledger import (
        AcceptanceConflictError,
        AcceptanceReceipt,
        create_search_ledger_repository,
    )

    _migrate_postgresql_ledger(postgres_ledger_url)
    repository = create_search_ledger_repository(postgres_ledger_url)
    query = SearchQuery(query="atomic search")
    barrier = Barrier(2)

    def accept(response):
        barrier.wait()
        try:
            return repository.accept(query, response)
        except Exception as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                accept,
                (_response(), _response(url="https://example.com/different")),
            )
        )

    assert sum(isinstance(outcome, AcceptanceReceipt) for outcome in outcomes) == 1
    assert sum(
        isinstance(outcome, AcceptanceConflictError) for outcome in outcomes
    ) == 1


def test_postgresql_repository_rolls_back_partial_acceptance(postgres_ledger_url):
    from argus.persistence.search_ledger import create_search_ledger_repository

    _migrate_postgresql_ledger(postgres_ledger_url)
    repository = create_search_ledger_repository(postgres_ledger_url)

    with pytest.raises(Exception):
        repository.accept(SearchQuery(query="atomic search"), _response(url=None))

    assert all(count == 0 for count in _table_counts(repository).values())


def test_postgresql_real_constraint_error_rolls_back_partial_acceptance(
    postgres_ledger_url,
):
    from argus.persistence.search_ledger import create_search_ledger_repository

    _migrate_postgresql_ledger(postgres_ledger_url)
    repository = create_search_ledger_repository(postgres_ledger_url)
    response = _response(run_id="db-error")
    response.traces[0].egress = "x" * 51

    with pytest.raises(DataError):
        repository.accept(SearchQuery(query="atomic search"), response)

    assert all(count == 0 for count in _table_counts(repository).values())


def test_alembic_migration_creates_search_ledger(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    db_path = tmp_path / "migrated.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(config, "head")

    inspector = inspect(create_engine(f"sqlite:///{db_path}"))
    tables = set(inspector.get_table_names())
    assert {
        "retrieval_requests",
        "retrieval_runs",
        "provider_attempts",
        "normalized_results",
        "result_provenance",
        "content_identities",
        "delivery_intents",
        "extraction_runs",
        "extractor_attempts",
        "extraction_artifacts",
        "retrieval_sessions",
        "session_queries",
        "session_extracted_urls",
    } <= tables
    request_columns = {
        column["name"] for column in inspector.get_columns("retrieval_requests")
    }
    assert {"providers_json", "free_only"} <= request_columns


def test_routing_field_migration_preserves_existing_requests(tmp_path):
    from alembic import command
    from alembic.config import Config

    db_path = tmp_path / "upgrade-existing-ledger.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(config, "0002_acceptance_fingerprint")
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO retrieval_requests "
                "(id, query_text, mode, max_results, caller, created_at) "
                "VALUES ('request-1', 'existing', 'discovery', 10, '', "
                "'2026-01-01 00:00:00')"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")

    upgraded = create_engine(f"sqlite:///{db_path}")
    with upgraded.connect() as connection:
        row = connection.execute(
            text(
                "SELECT query_text, providers_json, free_only "
                "FROM retrieval_requests WHERE id = 'request-1'"
            )
        ).mappings().one()
    upgraded.dispose()
    assert dict(row) == {
        "query_text": "existing",
        "providers_json": None,
        "free_only": 0,
    }


def test_alembic_offline_mode_accepts_percent_encoded_credentials(monkeypatch):
    from alembic import command
    from alembic.config import Config

    monkeypatch.setenv(
        "ARGUS_DB_URL",
        "postgresql+psycopg2://test:pa%2Fss@invalid.example/argus_test",
    )
    config = Config("alembic.ini")
    output = StringIO()
    config.output_buffer = output

    with redirect_stdout(output):
        command.upgrade(config, "head", sql=True)

    assert "CREATE TABLE retrieval_requests" in output.getvalue()


def _create_legacy_search_database(path):
    engine = create_engine(f"sqlite:///{path}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE search_queries ("
            "id INTEGER PRIMARY KEY, query_text TEXT NOT NULL, mode VARCHAR(50) NOT NULL, "
            "max_results INTEGER NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE search_runs ("
            "id INTEGER PRIMARY KEY, query_id INTEGER NOT NULL, search_run_id VARCHAR(64) NOT NULL, "
            "cached BOOLEAN NOT NULL, finished_at DATETIME)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE search_results ("
            "id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, url TEXT NOT NULL, title TEXT, "
            "snippet TEXT, domain VARCHAR(255), provider VARCHAR(50), score FLOAT, "
            "final_rank INTEGER, egress VARCHAR(50), machine VARCHAR(100), metadata_json TEXT)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE provider_usage ("
            "id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, provider VARCHAR(50), "
            "status VARCHAR(50), results_count INTEGER, latency_ms INTEGER, error TEXT, "
            "budget_remaining FLOAT, egress VARCHAR(50))"
        )
        connection.exec_driver_sql(
            "INSERT INTO search_queries VALUES "
            "(1, 'legacy query', 'discovery', 10)"
        )
        connection.exec_driver_sql(
            "INSERT INTO search_runs VALUES "
            "(1, 1, 'legacy-run', 0, '2026-01-02 03:04:05')"
        )
        connection.exec_driver_sql(
            "INSERT INTO search_results VALUES "
            "(1, 1, 'https://example.com/legacy', 'Legacy', 'old row', "
            "'example.com', 'duckduckgo', 0.5, 0, 'residential', 'old-node', '{}')"
        )
        connection.exec_driver_sql(
            "INSERT INTO provider_usage VALUES "
            "(1, 1, 'duckduckgo', 'success', 1, 10, NULL, NULL, 'local')"
        )


def test_legacy_reconciliation_is_dry_run_by_default_and_idempotent(tmp_path):
    from argus.persistence.reconcile import reconcile_legacy_state

    source = tmp_path / "legacy.db"
    _create_legacy_search_database(source)
    repository = _sqlite_repository(tmp_path)

    dry_run = reconcile_legacy_state(f"sqlite:///{source}", repository)
    assert dry_run == {
        "source": 1,
        "imported": 1,
        "skipped": 0,
        "conflicting": 0,
    }
    assert _table_counts(repository)["retrieval_runs"] == 0

    applied = reconcile_legacy_state(
        f"sqlite:///{source}", repository, apply=True
    )
    repeated = reconcile_legacy_state(
        f"sqlite:///{source}", repository, apply=True
    )

    assert applied["imported"] == 1
    assert repeated == {
        "source": 1,
        "imported": 0,
        "skipped": 1,
        "conflicting": 0,
    }
    assert _table_counts(repository)["retrieval_runs"] == 1


@pytest.mark.parametrize(
    "mutation",
    [
        "UPDATE retrieval_requests SET max_results = 99",
        "UPDATE retrieval_requests SET free_only = 1",
        "UPDATE retrieval_runs SET cached = 1",
        "DELETE FROM provider_attempts",
        "UPDATE normalized_results SET title = 'changed'",
        "DELETE FROM result_provenance",
        "UPDATE content_identities SET canonical_url = 'https://changed.example/'",
        "UPDATE delivery_intents SET status = 'sent'",
    ],
    ids=[
        "request",
        "request-routing",
        "run",
        "attempts",
        "normalized-result",
        "provenance",
        "content-identity",
        "delivery-intent",
    ],
)
def test_legacy_reconciliation_reports_incomplete_or_changed_state_as_conflict(
    tmp_path,
    mutation,
):
    from argus.persistence.reconcile import reconcile_legacy_state

    source = tmp_path / "legacy-conflict.db"
    _create_legacy_search_database(source)
    repository = _sqlite_repository(tmp_path)
    reconcile_legacy_state(f"sqlite:///{source}", repository, apply=True)

    with repository.session_factory.begin() as session:
        session.execute(text(mutation))

    assert reconcile_legacy_state(f"sqlite:///{source}", repository) == {
        "source": 1,
        "imported": 0,
        "skipped": 0,
        "conflicting": 1,
    }


def test_legacy_reconciliation_cli_is_dry_run_by_default(tmp_path):
    from click.testing import CliRunner

    from argus.cli.main import cli

    source = tmp_path / "legacy-cli.db"
    target = tmp_path / "target-cli.db"
    _create_legacy_search_database(source)

    result = CliRunner().invoke(
        cli,
        [
            "ledger",
            "reconcile-legacy",
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


def test_legacy_reconciliation_cli_dry_run_does_not_mutate_existing_target(
    tmp_path,
):
    from click.testing import CliRunner
    from sqlalchemy import inspect

    from argus.cli.main import cli

    source = tmp_path / "legacy-existing-target.db"
    target = tmp_path / "existing-target.db"
    _create_legacy_search_database(source)
    engine = create_engine(f"sqlite:///{target}")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE unrelated (id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO unrelated (id, value) VALUES (1, 'preserve me')"
        )
    before_schema = inspect(engine).get_table_names()
    engine.dispose()
    before_bytes = target.read_bytes()

    result = CliRunner().invoke(
        cli,
        [
            "ledger",
            "reconcile-legacy",
            "--source",
            f"sqlite:///{source}",
            "--target",
            f"sqlite:///{target}",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["imported"] == 1
    assert target.read_bytes() == before_bytes
    after_engine = create_engine(f"sqlite:///{target}")
    assert inspect(after_engine).get_table_names() == before_schema
    after_engine.dispose()
