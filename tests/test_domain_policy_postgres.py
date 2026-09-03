from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql


@pytest.fixture
def migrated_domain_policy_postgres(postgres_ledger_url):
    """Reset only the explicitly supplied disposable PostgreSQL database."""
    config = Config("alembic.ini")
    config.set_main_option(
        "sqlalchemy.url",
        postgres_ledger_url.replace("%", "%%"),
    )
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    from argus.persistence.domain_policy import create_domain_policy_repository

    return create_domain_policy_repository(postgres_ledger_url, create_schema=False)


def test_postgres_migration_contains_canonical_domain_policy_tables(
    migrated_domain_policy_postgres,
):
    engine = migrated_domain_policy_postgres.session_factory.kw["bind"]
    assert {
        "domain_policies",
        "domain_policy_events",
    } <= set(inspect(engine).get_table_names())


def test_postgres_concurrent_distinct_events_preserve_both_updates(
    migrated_domain_policy_postgres,
):
    url = os.environ["ARGUS_TEST_POSTGRES_URL"]

    def write(event_identity: str):
        from argus.persistence.domain_policy import create_domain_policy_repository

        return create_domain_policy_repository(
            url, create_schema=False
        ).record_datacenter_failure(
            "concurrent.example.com",
            event_identity=event_identity,
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        values = list(workers.map(write, ["concurrent-1", "concurrent-2"]))

    final = migrated_domain_policy_postgres.get_policy("concurrent.example.com")
    assert final is not None
    assert final.datacenter_failure_count == 2
    assert {value.version for value in values} == {1, 2}
    assert migrated_domain_policy_postgres.count_events() == 2


def test_postgres_concurrent_same_event_identity_replays_once(
    migrated_domain_policy_postgres,
):
    url = os.environ["ARGUS_TEST_POSTGRES_URL"]

    def write():
        from argus.persistence.domain_policy import create_domain_policy_repository

        return create_domain_policy_repository(
            url, create_schema=False
        ).record_residential_success(
            "replay.example.com",
            event_identity="concurrent-same-event",
        )

    with ThreadPoolExecutor(max_workers=2) as workers:
        values = list(workers.map(lambda _: write(), range(2)))

    assert values[0] == values[1]
    final = migrated_domain_policy_postgres.get_policy("replay.example.com")
    assert final is not None
    assert final.residential_success_count == 1
    assert migrated_domain_policy_postgres.count_events("concurrent-same-event") == 1


def test_postgres_policy_mutation_compiles_to_one_on_conflict_update_statement(
    migrated_domain_policy_postgres,
):
    repository = migrated_domain_policy_postgres
    session = repository.session_factory()
    try:
        statement = repository._policy_upsert_statement(
            session,
            domain="compiled.example.com",
            event_type="datacenter_failure",
            reason="blocked",
            now=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
        )
        rendered = str(statement.compile(dialect=postgresql.dialect())).upper()
    finally:
        session.close()

    assert rendered.count("INSERT INTO") == 1
    assert "ON CONFLICT" in rendered
    assert "DO UPDATE" in rendered
    assert "DATACENTER_FAILURE_COUNT" in rendered
