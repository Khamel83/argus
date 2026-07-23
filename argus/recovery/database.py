"""Read-only verification of an explicitly disposable restored Argus database."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from argus.recovery.operator import validate_scratch_database


EXPECTED_SCHEMA_HEAD = "0004_operation_ledger"
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

            for query in _ORPHAN_CHECKS:
                cursor.execute(query)
                if int(cursor.fetchone()[0]) != 0:
                    raise RuntimeError("referential integrity check failed")

            cursor.execute(
                "SELECT count(*) FROM retrieval_runs WHERE status = 'accepted'"
            )
            int(cursor.fetchone()[0])
    finally:
        connection.close()

    return {
        "database": validated,
        "schema_head": schema_head,
        "row_counts": row_counts,
        "checks": {
            "schema": True,
            "row_counts": True,
            "integrity": True,
            "argus_read_path": True,
            "migration_compatible": True,
        },
    }
