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
    def __init__(self, tables):
        self.tables = tables
        self.query = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def execute(self, query):
        self.query = query

    def fetchone(self):
        if "current_database" in self.query:
            return ("argus_restore_issue40_test",)
        if "alembic_version" in self.query:
            return ("0004_operation_ledger",)
        return (0,)

    def fetchall(self):
        return [(table,) for table in self.tables]


class FakeConnection:
    def __init__(self, tables):
        self.cursor_instance = FakeCursor(tables)
        self.closed = False

    def cursor(self):
        return self.cursor_instance

    def close(self):
        self.closed = True


def test_restore_verifier_checks_schema_counts_integrity_and_read_path():
    from argus.recovery.database import verify_argus_database

    connection = FakeConnection(REQUIRED_TABLES)

    report = verify_argus_database(
        "argus_restore_issue40_test",
        connect=lambda **kwargs: connection,
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
    assert connection.closed is True


def test_restore_verifier_refuses_missing_schema_table():
    from argus.recovery.database import verify_argus_database

    connection = FakeConnection(REQUIRED_TABLES - {"extraction_runs"})

    with pytest.raises(RuntimeError, match="missing required tables"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
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

    from argus.recovery.database import verify_argus_database
    from argus.recovery.operator import validate_scratch_database

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
        )

        assert report["schema_head"] == "0004_operation_ledger"
        assert report["checks"]["argus_read_path"] is True
    finally:
        with admin.cursor() as cursor:
            cursor.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s",
                (scratch,),
            )
            cursor.execute(f'DROP DATABASE IF EXISTS "{scratch}"')
        admin.close()
