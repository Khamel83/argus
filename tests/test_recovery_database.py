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
    "provider_spend_attempts",
    "provider_balance_snapshots",
    "provider_spend_audit",
    "authentication_scope_authority_receipts",
    "extraction_outcome_plans",
    "extraction_outcome_steps",
    "extraction_artifact_identities",
    "extraction_outcome_artifacts",
    "extraction_outcome_rejections",
    "extraction_outcome_acceptances",
    "retrieval_compositions",
    "result_extraction_links",
    "extraction_outcome_activations",
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
            return ("0007_extraction_outcomes",)
        if "search_run_id" in self.query:
            return None
        return (0,)

    def fetchall(self):
        if "information_schema.columns" in self.query:
            from argus.recovery.database import expected_argus_schema_manifest

            return [
                (
                    table,
                    column,
                    definition["type"],
                    "YES" if definition["nullable"] else "NO",
                    None,
                )
                for table, columns in expected_argus_schema_manifest()[
                    "columns"
                ].items()
                if table in self.tables
                for column, definition in columns.items()
            ]
        if "pg_get_constraintdef" in self.query:
            from argus.recovery.database import expected_argus_schema_manifest

            return [
                (name, value["table"], value["definition"])
                for name, value in expected_argus_schema_manifest()[
                    "constraints"
                ].items()
            ]
        if "pg_indexes" in self.query:
            from argus.recovery.database import expected_argus_schema_manifest

            return [
                (name, value["table"], value["definition"])
                for name, value in expected_argus_schema_manifest()[
                    "indexes"
                ].items()
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
    assert report["schema_head"] == "0007_extraction_outcomes"
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


def test_restore_verifier_rejects_stamped_schema_missing_required_constraints():
    from argus.recovery.database import verify_argus_database

    class MissingConstraintCursor(FakeCursor):
        def fetchall(self):
            if "pg_constraint" in self.query or "pg_indexes" in self.query:
                return []
            return super().fetchall()

    connection = FakeConnection(REQUIRED_TABLES)
    connection.cursor_instance = MissingConstraintCursor(REQUIRED_TABLES)

    with pytest.raises(RuntimeError, match="database schema manifest"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
            repository_factory=lambda database: None,
        )


def test_expected_schema_manifest_describes_types_nullability_and_definitions():
    from sqlalchemy import Float, Numeric

    from argus.recovery.database import (
        SCHEMA_CONTRACT_PATH,
        _postgresql_type_name,
        build_argus_schema_contract,
        expected_argus_schema_manifest,
    )

    manifest = expected_argus_schema_manifest()

    assert SCHEMA_CONTRACT_PATH.is_file()
    assert manifest == build_argus_schema_contract()
    assert len(manifest["constraints"]) == 83
    assert len(manifest["indexes"]) == 57
    assert manifest["columns"]["extraction_outcome_steps"]["latency_ms"] == {
        "type": "bigint",
        "nullable": True,
    }
    assert manifest["columns"]["provider_attempts"]["budget_remaining"][
        "type"
    ] == "double precision"
    assert _postgresql_type_name(Float()) == "double precision"
    assert _postgresql_type_name(Numeric()) == "numeric"
    assert "match full" in manifest["constraints"][
        "fk_result_extraction_links_acceptance_plan"
    ]["definition"]
    assert "artifact_ref" in manifest["indexes"][
        "ix_extraction_outcome_artifacts_artifact_ref"
    ]["definition"]


def test_restore_compares_database_to_supplied_schema_manifest():
    import hashlib
    import json

    from argus.recovery.database import (
        expected_argus_schema_manifest,
        verify_argus_database,
    )

    trusted = expected_argus_schema_manifest()

    class ManifestCursor(FakeCursor):
        def fetchall(self):
            if "information_schema.columns" in self.query:
                return [
                    (
                        table,
                        column,
                        definition["type"],
                        "YES" if definition["nullable"] else "NO",
                        None,
                    )
                    for table, columns in trusted["columns"].items()
                    for column, definition in columns.items()
                ]
            if "pg_get_constraintdef" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in trusted["constraints"].items()
                ]
            if "pg_indexes" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in trusted["indexes"].items()
                ]
            return super().fetchall()

    connection = FakeConnection(REQUIRED_TABLES)
    connection.cursor_instance = ManifestCursor(REQUIRED_TABLES)
    report = verify_argus_database(
        "argus_restore_issue40_test",
        connect=lambda **kwargs: connection,
        repository_factory=lambda database: type(
            "Repository",
            (),
            {"list_session_ids": lambda self: []},
        )(),
        expected_schema_manifest=trusted,
    )
    assert report["checks"]["schema"] is True

    corrupted = expected_argus_schema_manifest()
    corrupted["columns"]["extraction_outcome_steps"]["latency_ms"][
        "type"
    ] = "integer"
    corrupted["contract_sha256"] = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in corrupted.items()
                if key != "contract_sha256"
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    connection = FakeConnection(REQUIRED_TABLES)
    connection.cursor_instance = ManifestCursor(REQUIRED_TABLES)

    with pytest.raises(RuntimeError, match="database schema manifest"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
            repository_factory=lambda database: None,
            expected_schema_manifest=corrupted,
        )


@pytest.mark.parametrize("mutation", ["missing", "extra", "altered"])
def test_restore_requires_exact_schema_object_sets_and_definitions(mutation):
    from argus.recovery.database import (
        expected_argus_schema_manifest,
        verify_argus_database,
    )

    trusted = expected_argus_schema_manifest()

    class ExactManifestCursor(FakeCursor):
        def fetchall(self):
            if "information_schema.columns" in self.query:
                return [
                    (
                        table,
                        column,
                        definition["type"],
                        "YES" if definition["nullable"] else "NO",
                        None,
                    )
                    for table, columns in trusted["columns"].items()
                    for column, definition in columns.items()
                ]
            if "pg_get_constraintdef" in self.query:
                constraints = [
                    (name, value["table"], value["definition"])
                    for name, value in trusted["constraints"].items()
                ]
                if mutation == "missing":
                    return constraints[1:]
                if mutation == "extra":
                    return constraints + [
                        (
                            "unexpected_constraint",
                            "retrieval_requests",
                            "check id is not null",
                        )
                    ]
                name, table, definition = constraints[0]
                return [
                    (
                        name,
                        table,
                        definition.replace("=", "<>")
                        if "=" in definition
                        else definition + " DEFERRABLE",
                    ),
                    *constraints[1:],
                ]
            if "pg_indexes" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in trusted["indexes"].items()
                ]
            return super().fetchall()

    connection = FakeConnection(set(trusted["tables"]))
    connection.cursor_instance = ExactManifestCursor(set(trusted["tables"]))

    with pytest.raises(RuntimeError, match="database schema manifest"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
            repository_factory=lambda database: None,
            expected_schema_manifest=trusted,
        )


def test_migrated_postgres_uses_double_precision_for_float_columns(
    migrated_postgres_ledger,
):
    from sqlalchemy import text

    engine = migrated_postgres_ledger.session_factory.kw["bind"]
    with engine.connect() as connection:
        data_type = connection.execute(
            text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'provider_attempts' "
                "AND column_name = 'budget_remaining'"
            )
        ).scalar_one()

    assert data_type == "double precision"


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

        assert report["schema_head"] == "0007_extraction_outcomes"
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
