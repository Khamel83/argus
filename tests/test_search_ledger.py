from concurrent.futures import ThreadPoolExecutor
import json

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

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
    repository = _sqlite_repository(tmp_path)
    query = SearchQuery(
        query="atomic search",
        mode=SearchMode.DISCOVERY,
        max_results=5,
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


def test_concurrent_same_run_is_acknowledged_only_after_one_complete_commit(tmp_path):
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


def test_postgresql_repository_rolls_back_partial_acceptance(postgres_ledger_url):
    from argus.persistence.search_ledger import create_search_ledger_repository

    _migrate_postgresql_ledger(postgres_ledger_url)
    repository = create_search_ledger_repository(postgres_ledger_url)

    with pytest.raises(Exception):
        repository.accept(SearchQuery(query="atomic search"), _response(url=None))

    assert all(count == 0 for count in _table_counts(repository).values())


def test_alembic_migration_creates_search_ledger(tmp_path):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    db_path = tmp_path / "migrated.db"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

    command.upgrade(config, "head")

    tables = set(inspect(create_engine(f"sqlite:///{db_path}")).get_table_names())
    assert {
        "retrieval_requests",
        "retrieval_runs",
        "provider_attempts",
        "normalized_results",
        "result_provenance",
        "content_identities",
        "delivery_intents",
    } <= tables


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
