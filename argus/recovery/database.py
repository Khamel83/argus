"""Read-only verification of an explicitly disposable restored Argus database."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from argus.recovery.operator import (
    validate_database_name,
    validate_scratch_database,
)


EXPECTED_SCHEMA_HEAD = "0006_maya_outbox"
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
    "alembic_version",
}
COUNTED_TABLES = sorted(REQUIRED_TABLES - {"alembic_version"})
_ORPHAN_CHECKS = (
    "SELECT count(*) FROM retrieval_runs child "
    "LEFT JOIN retrieval_requests parent ON parent.id = child.request_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM provider_attempts child "
    "LEFT JOIN retrieval_runs parent ON parent.id = child.run_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM normalized_results child "
    "LEFT JOIN retrieval_runs parent ON parent.id = child.run_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM result_provenance child "
    "LEFT JOIN normalized_results parent ON parent.id = child.result_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM extractor_attempts child "
    "LEFT JOIN extraction_runs parent ON parent.id = child.run_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM session_queries child "
    "LEFT JOIN retrieval_sessions parent ON parent.id = child.session_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM session_extracted_urls child "
    "LEFT JOIN session_queries parent ON parent.id = child.query_id "
    "WHERE parent.id IS NULL",
)


def verify_argus_database(
    database: str,
    *,
    connect: Callable[..., Any] | None = None,
    repository_factory: Callable[[str], Any] | None = None,
    expected_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify schema, row accounting, relationships, and a basic Argus read path."""
    validated = validate_scratch_database(database)
    if connect is None:
        import psycopg2

        connect = psycopg2.connect
    connection = connect(dbname=validated)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            actual_database = cursor.fetchone()[0]
            if actual_database != validated:
                raise RuntimeError("connected database does not match scratch target")

            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            missing = sorted(REQUIRED_TABLES - tables)
            if missing:
                raise RuntimeError(
                    "missing required tables: " + ", ".join(missing)
                )

            cursor.execute("SELECT version_num FROM alembic_version")
            schema_head = cursor.fetchone()[0]
            if schema_head != EXPECTED_SCHEMA_HEAD:
                raise RuntimeError(
                    f"schema head {schema_head!r} is not {EXPECTED_SCHEMA_HEAD!r}"
                )

            row_counts = {}
            for table in COUNTED_TABLES:
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                row_counts[table] = int(cursor.fetchone()[0])
            inventory = _inventory(cursor, tables, row_counts)
            _compare_inventory(inventory, expected_inventory)

            for query in _ORPHAN_CHECKS:
                cursor.execute(query)
                if int(cursor.fetchone()[0]) != 0:
                    raise RuntimeError("referential integrity check failed")

            cursor.execute(
                "SELECT search_run_id FROM retrieval_runs "
                "WHERE status = 'accepted' ORDER BY committed_at LIMIT 1"
            )
            accepted_row = cursor.fetchone()
    finally:
        connection.close()

    if repository_factory is None:
        from argus.persistence.search_ledger import create_search_ledger_repository

        def repository_factory(name):
            return create_search_ledger_repository(
                f"postgresql+psycopg2:///{name}",
                create_schema=False,
            )
    repository = repository_factory(validated)
    repository.list_session_ids()
    if accepted_row is not None:
        snapshot = repository.load_acceptance_snapshot(accepted_row[0])
        if snapshot is None:
            raise RuntimeError("Argus repository could not read an accepted run")

    return {
        "database": validated,
        "schema_head": schema_head,
        "row_counts": row_counts,
        "inventory": inventory,
        "checks": {
            "schema": True,
            "row_counts": True,
            "integrity": True,
            "argus_read_path": True,
            "migration_compatible": True,
        },
    }


def verify_atlas_database(
    database: str,
    *,
    connect: Callable[..., Any] | None = None,
    expected_inventory: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify Atlas schema, all source row counts, and validated constraints."""
    validated = validate_scratch_database(database, tenant="atlas")
    if connect is None:
        import psycopg2

        connect = psycopg2.connect
    connection = connect(dbname=validated)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != validated:
                raise RuntimeError("connected database does not match scratch target")
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            if not tables:
                raise RuntimeError("Atlas restore has no public base tables")
            row_counts = {}
            for table in sorted(tables):
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                row_counts[table] = int(cursor.fetchone()[0])
            inventory = _inventory(cursor, tables, row_counts)
            _compare_inventory(inventory, expected_inventory)
    finally:
        connection.close()
    return {
        "database": validated,
        "inventory": inventory,
        "checks": {
            "schema": True,
            "row_counts": True,
            "integrity": True,
        },
    }


def collect_source_inventory(
    database: str,
    *,
    connect: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Collect schema and exact table counts from an approved source tenant."""
    validated = validate_database_name(database, allowed={"atlas", "argus"})
    if connect is None:
        import psycopg2

        connect = psycopg2.connect
    connection = connect(dbname=validated)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != validated:
                raise RuntimeError("connected database does not match source target")
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            if not tables:
                raise RuntimeError(f"{validated} source has no public base tables")
            if validated == "argus":
                missing = sorted(
                    {"alembic_version", "retrieval_requests", "retrieval_runs"}
                    - tables
                )
                if missing:
                    raise RuntimeError(
                        "Argus source is missing required tables: " + ", ".join(missing)
                    )
            counts = {}
            counted_tables = (
                sorted(tables - {"alembic_version"})
                if validated == "argus"
                else sorted(tables)
            )
            for table in counted_tables:
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                counts[table] = int(cursor.fetchone()[0])
            return _inventory(cursor, tables, counts)
    finally:
        connection.close()


def verify_restored_source_inventory(
    database: str,
    *,
    tenant: str,
    expected_inventory: dict[str, Any],
    connect: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Verify the raw restored snapshot before any candidate migration runs."""
    validated = validate_scratch_database(database, tenant=tenant)
    if connect is None:
        import psycopg2

        connect = psycopg2.connect
    connection = connect(dbname=validated)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            if cursor.fetchone()[0] != validated:
                raise RuntimeError("connected database does not match scratch target")
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
            )
            tables = {row[0] for row in cursor.fetchall()}
            counted = (
                sorted(tables - {"alembic_version"})
                if tenant == "argus"
                else sorted(tables)
            )
            counts = {}
            for table in counted:
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                counts[table] = int(cursor.fetchone()[0])
            inventory = _inventory(cursor, tables, counts)
            _compare_inventory(inventory, expected_inventory)
            return inventory
    finally:
        connection.close()


def _inventory(cursor, tables: set[str], row_counts: dict[str, int]) -> dict[str, Any]:
    cursor.execute(
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' "
        "ORDER BY table_name, ordinal_position"
    )
    columns = [list(row) for row in cursor.fetchall()]
    cursor.execute(
        "SELECT count(*) FROM pg_constraint constraint_row "
        "JOIN pg_namespace namespace ON namespace.oid = constraint_row.connamespace "
        "WHERE namespace.nspname = 'public' "
        "AND constraint_row.contype = 'f' AND NOT constraint_row.convalidated"
    )
    if int(cursor.fetchone()[0]) != 0:
        raise RuntimeError("database contains unvalidated foreign-key constraints")
    schema_state = {
        "tables": sorted(tables),
        "columns": columns,
    }
    return {
        "tables": dict(sorted(row_counts.items())),
        "schema_sha256": hashlib.sha256(
            json.dumps(
                schema_state,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest(),
        "constraints_validated": True,
    }


def _compare_inventory(
    actual: dict[str, Any],
    expected: dict[str, Any] | None,
) -> None:
    if expected is not None and actual != expected:
        raise RuntimeError("restored database does not match source inventory")
