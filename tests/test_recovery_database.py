import pytest


REQUIRED_TABLES = {
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
    "alembic_version",
}


class FakeCursor:
    def __init__(self, tables, database="argus_restore_issue40_test"):
        self.tables = tables
        self.database = database
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query):
        self.query = query

    def fetchone(self):
        if "current_database" in self.query:
            return (self.database,)
        if "alembic_version" in self.query:
            return ("0004_operation_ledger",)
        if "search_run_id" in self.query:
            return None
        return (0,)

    def fetchall(self):
        if "information_schema.columns" in self.query:
            return [
                (table, "id", "text", "NO", None)
                for table in sorted(self.tables)
            ]
        return [(table,) for table in self.tables]


class FakeConnection:
    def __init__(self, tables, database="argus_restore_issue40_test"):
        self.cursor_instance = FakeCursor(tables, database)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def test_restore_verifier_checks_schema_counts_integrity_and_read_path():
    from argus.recovery.database import verify_argus_database

    connection = FakeConnection(REQUIRED_TABLES)
    repository = type(
        "Repository",
        (),
        {
            "list_session_ids": lambda self: [],
            "load_acceptance_snapshot": lambda self, run_id: None,
        },
    )()

    report = verify_argus_database(
        "argus_restore_issue40_test",
        connect=lambda **kwargs: connection,
        repository_factory=lambda database: repository,
    )

    assert report["database"] == "argus_restore_issue40_test"
    assert report["schema_head"] == "0004_operation_ledger"
    assert report["checks"] == {
        "schema": True,
        "row_counts": True,
        "integrity": True,
        "argus_read_path": True,
        "migration_compatible": True,
    }
    assert set(report["row_counts"]) == REQUIRED_TABLES - {"alembic_version"}
    assert report["inventory"]["constraints_validated"] is True
    assert connection.closed is True


def test_restore_verifier_refuses_missing_schema_table():
    from argus.recovery.database import verify_argus_database

    connection = FakeConnection(REQUIRED_TABLES - {"extraction_runs"})

    with pytest.raises(RuntimeError, match="missing required tables"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
            repository_factory=lambda database: None,
        )


def test_restore_verifier_rejects_production_target_before_connecting():
    from argus.recovery.database import verify_argus_database

    called = False

    def connect(**kwargs):
        nonlocal called
        called = True

    with pytest.raises(ValueError):
        verify_argus_database("argus", connect=connect)
    assert called is False


def test_argus_restore_compares_source_counts_and_uses_repository_read_path():
    from argus.recovery.database import verify_argus_database

    repository = type(
        "Repository",
        (),
        {
            "list_session_ids": lambda self: ["session-1"],
            "load_acceptance_snapshot": lambda self, run_id: object(),
        },
    )()
    first = verify_argus_database(
        "argus_restore_issue40_counts",
        connect=lambda **kwargs: FakeConnection(
            REQUIRED_TABLES,
            "argus_restore_issue40_counts",
        ),
        repository_factory=lambda database: repository,
    )
    expected = first["inventory"]
    report = verify_argus_database(
        "argus_restore_issue40_counts",
        connect=lambda **kwargs: FakeConnection(
            REQUIRED_TABLES,
            "argus_restore_issue40_counts",
        ),
        repository_factory=lambda database: repository,
        expected_inventory=expected,
    )

    assert report["inventory"] == expected
    mismatched = {**expected, "tables": {**expected["tables"], "retrieval_runs": 2}}
    with pytest.raises(RuntimeError, match="source inventory"):
        verify_argus_database(
            "argus_restore_issue40_counts",
            connect=lambda **kwargs: FakeConnection(
                REQUIRED_TABLES,
                "argus_restore_issue40_counts",
            ),
            repository_factory=lambda database: repository,
            expected_inventory=mismatched,
        )


def test_atlas_restore_requires_matching_schema_counts_and_valid_constraints():
    from argus.recovery.database import verify_atlas_database

    first = verify_atlas_database(
        "atlas_restore_issue40_inventory",
        connect=lambda **kwargs: FakeConnection(
            {"atlas_items"},
            "atlas_restore_issue40_inventory",
        ),
    )
    report = verify_atlas_database(
        "atlas_restore_issue40_inventory",
        connect=lambda **kwargs: FakeConnection(
            {"atlas_items"},
            "atlas_restore_issue40_inventory",
        ),
        expected_inventory=first["inventory"],
    )

    assert report["checks"] == {
        "schema": True,
        "row_counts": True,
        "integrity": True,
    }
    wrong = {
        **first["inventory"],
        "schema_sha256": "f" * 64,
    }
    with pytest.raises(RuntimeError, match="source inventory"):
        verify_atlas_database(
            "atlas_restore_issue40_inventory",
            connect=lambda **kwargs: FakeConnection(
                {"atlas_items"},
                "atlas_restore_issue40_inventory",
            ),
            expected_inventory=wrong,
        )


def test_argus_source_inventory_matches_restore_count_scope():
    from argus.recovery.database import (
        COUNTED_TABLES,
        REQUIRED_TABLES,
        collect_source_inventory,
    )

    inventory = collect_source_inventory(
        "argus",
        connect=lambda **kwargs: FakeConnection(REQUIRED_TABLES, "argus"),
    )

    assert set(inventory["tables"]) == set(COUNTED_TABLES)
    assert "alembic_version" not in inventory["tables"]


def test_recovery_schema_head_tracks_alembic_head():
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from argus.recovery.database import EXPECTED_SCHEMA_HEAD

    assert (
        ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
        == EXPECTED_SCHEMA_HEAD
    )


def test_postgresql_restore_verifier_uses_disposable_database(
    postgres_ledger_url,
):
    import uuid

    import psycopg2
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.engine import make_url

    from argus.recovery.database import (
        verify_argus_database,
        verify_restored_source_inventory,
    )
    from argus.recovery.operator import validate_scratch_database
    from argus.persistence.search_ledger import create_search_ledger_repository

    parsed = make_url(postgres_ledger_url)
    scratch = validate_scratch_database(f"argus_restore_ci_{uuid.uuid4().hex[:12]}")
    connect_kwargs = {
        "host": parsed.host,
        "port": parsed.port,
        "user": parsed.username,
        "password": parsed.password,
    }
    admin = psycopg2.connect(dbname=parsed.database, **connect_kwargs)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{scratch}"')
        scratch_url = parsed.set(database=scratch)
        config = Config("alembic.ini")
        config.set_main_option(
            "sqlalchemy.url",
            scratch_url.render_as_string(hide_password=False).replace("%", "%%"),
        )
        command.upgrade(config, "head")

        report = verify_argus_database(
            scratch,
            connect=lambda **kwargs: psycopg2.connect(
                dbname=kwargs["dbname"],
                **connect_kwargs,
            ),
            repository_factory=lambda database: create_search_ledger_repository(
                scratch_url.render_as_string(hide_password=False),
                create_schema=False,
            ),
        )

        assert report["schema_head"] == "0004_operation_ledger"
        assert report["checks"]["argus_read_path"] is True
        assert verify_restored_source_inventory(
            scratch,
            tenant="argus",
            expected_inventory=report["inventory"],
            connect=lambda **kwargs: psycopg2.connect(
                dbname=kwargs["dbname"],
                **connect_kwargs,
            ),
        ) == report["inventory"]
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s",
                (scratch,),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        admin.close()


def test_postgresql_atlas_restore_inventory_detects_count_drift(
    postgres_ledger_url,
):
    import uuid

    import psycopg2
    from sqlalchemy.engine import make_url

    from argus.recovery.database import (
        verify_atlas_database,
        verify_restored_source_inventory,
    )
    from argus.recovery.operator import validate_scratch_database

    parsed = make_url(postgres_ledger_url)
    scratch = validate_scratch_database(
        f"atlas_restore_ci_{uuid.uuid4().hex[:12]}",
        tenant="atlas",
    )
    connect_kwargs = {
        "host": parsed.host,
        "port": parsed.port,
        "user": parsed.username,
        "password": parsed.password,
    }
    admin = psycopg2.connect(dbname=parsed.database, **connect_kwargs)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(f'CREATE DATABASE "{scratch}"')
        atlas = psycopg2.connect(dbname=scratch, **connect_kwargs)
        try:
            with atlas:
                with atlas.cursor() as cursor:
                    cursor.execute("CREATE TABLE parent (id integer PRIMARY KEY)")
                    cursor.execute(
                        "CREATE TABLE child ("
                        "id integer PRIMARY KEY, "
                        "parent_id integer NOT NULL REFERENCES parent(id))"
                    )
                    cursor.execute("INSERT INTO parent VALUES (1)")
                    cursor.execute("INSERT INTO child VALUES (1, 1)")
        finally:
            atlas.close()
        def connect(**kwargs):
            return psycopg2.connect(
                dbname=kwargs["dbname"],
                **connect_kwargs,
            )
        baseline = verify_atlas_database(scratch, connect=connect)["inventory"]
        assert verify_restored_source_inventory(
            scratch,
            tenant="atlas",
            expected_inventory=baseline,
            connect=connect,
        ) == baseline
        assert verify_atlas_database(
            scratch,
            connect=connect,
            expected_inventory=baseline,
        )["checks"]["integrity"] is True
        atlas = psycopg2.connect(dbname=scratch, **connect_kwargs)
        try:
            with atlas:
                with atlas.cursor() as cursor:
                    cursor.execute("INSERT INTO parent VALUES (2)")
        finally:
            atlas.close()
        with pytest.raises(RuntimeError, match="source inventory"):
            verify_atlas_database(
                scratch,
                connect=connect,
                expected_inventory=baseline,
            )
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s",
                (scratch,),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        admin.close()
