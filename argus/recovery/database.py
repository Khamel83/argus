"""Read-only verification of an explicitly disposable restored Argus database."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from argus.recovery.operator import (
    validate_database_name,
    validate_scratch_database,
)


EXPECTED_SCHEMA_HEAD = "0007_extraction_outcomes"
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
COUNTED_TABLES = sorted(REQUIRED_TABLES - {"alembic_version"})
REQUIRED_S3_COLUMNS = {
    "extraction_artifact_identities": {
        "artifact_ref",
        "content_identity",
        "content_text",
        "evaluation_json",
    },
    "extraction_outcome_plans": {
        "id",
        "plan_ref",
        "extraction_run_id",
        "request_id",
        "normalized_url",
        "access_scope",
        "mode",
        "plan_json",
        "source_fingerprint",
        "created_at",
    },
    "extraction_outcome_steps": {
        "id",
        "plan_id",
        "ordinal",
        "extractor",
        "decision",
        "attempt_outcome",
        "latency_ms",
        "provenance_json",
        "spend_json",
        "policy_rule_ref",
    },
    "extraction_outcome_artifacts": {
        "id",
        "plan_id",
        "artifact_ref",
        "content_identity",
        "content_text",
        "disposition",
        "quality_passed",
        "is_complete",
        "evaluation_json",
    },
    "extraction_outcome_rejections": {
        "id",
        "plan_id",
        "rejection_ref",
        "code",
        "provider",
        "recommended_action",
        "projection_json",
    },
    "extraction_outcome_acceptances": {
        "receipt_ref",
        "plan_id",
        "outcome",
        "artifact_disposition",
        "outcome_policy_version",
        "projection_json",
        "acceptance_fingerprint",
        "accepted_at",
        "scope",
    },
    "retrieval_compositions": {
        "receipt_ref",
        "retrieval_acceptance_ref",
        "requirement_ref",
        "retrieval_outcome",
        "artifact_outcome",
        "composite_outcome",
        "projection_json",
        "source_fingerprint",
        "accepted_at",
    },
    "result_extraction_links": {
        "id",
        "composition_ref",
        "result_cluster_ref",
        "extraction_acceptance_ref",
        "extraction_plan_id",
        "artifact_row_id",
        "artifact_plan_id",
        "rejection_row_id",
        "rejection_plan_id",
        "reuse_origin",
    },
    "extraction_outcome_activations": {"receipt_ref", "activated_at"},
}
REQUIRED_S3_CONSTRAINTS = {
    "fk_extraction_outcome_artifacts_identity",
    "fk_result_extraction_links_acceptance_plan",
    "fk_result_extraction_links_artifact_plan",
    "fk_result_extraction_links_rejection_plan",
    "ck_result_extraction_links_acceptance_pair",
    "ck_result_extraction_links_artifact_pair",
    "ck_result_extraction_links_rejection_pair",
    "ck_result_extraction_links_artifact_same_plan",
    "ck_result_extraction_links_rejection_same_plan",
    "ck_result_extraction_links_artifact_requires_acceptance",
    "ck_result_extraction_links_rejection_requires_acceptance",
    "uq_result_extraction_links_composition_cluster",
}
REQUIRED_S3_INDEXES = {
    "ix_extraction_outcome_artifacts_artifact_ref",
}

_REQUIRED_S3_CONSTRAINT_DEFINITIONS = {
    "fk_extraction_outcome_artifacts_identity": (
        "FOREIGN KEY (artifact_ref) REFERENCES "
        "extraction_artifact_identities(artifact_ref)"
    ),
    "fk_result_extraction_links_acceptance_plan": (
        "FOREIGN KEY (extraction_acceptance_ref, extraction_plan_id) "
        "REFERENCES extraction_outcome_acceptances(receipt_ref, plan_id) "
        "MATCH FULL"
    ),
    "fk_result_extraction_links_artifact_plan": (
        "FOREIGN KEY (artifact_row_id, artifact_plan_id) REFERENCES "
        "extraction_outcome_artifacts(id, plan_id) MATCH FULL"
    ),
    "fk_result_extraction_links_rejection_plan": (
        "FOREIGN KEY (rejection_row_id, rejection_plan_id) REFERENCES "
        "extraction_outcome_rejections(id, plan_id) MATCH FULL"
    ),
    "ck_result_extraction_links_acceptance_pair": (
        "(extraction_acceptance_ref IS NULL) = (extraction_plan_id IS NULL)"
    ),
    "ck_result_extraction_links_artifact_pair": (
        "(artifact_row_id IS NULL) = (artifact_plan_id IS NULL)"
    ),
    "ck_result_extraction_links_rejection_pair": (
        "(rejection_row_id IS NULL) = (rejection_plan_id IS NULL)"
    ),
    "ck_result_extraction_links_artifact_same_plan": (
        "artifact_plan_id IS NULL OR artifact_plan_id = extraction_plan_id"
    ),
    "ck_result_extraction_links_rejection_same_plan": (
        "rejection_plan_id IS NULL OR rejection_plan_id = extraction_plan_id"
    ),
    "ck_result_extraction_links_artifact_requires_acceptance": (
        "artifact_row_id IS NULL OR (artifact_plan_id IS NOT NULL AND "
        "extraction_plan_id IS NOT NULL AND extraction_acceptance_ref IS NOT "
        "NULL AND artifact_plan_id = extraction_plan_id)"
    ),
    "ck_result_extraction_links_rejection_requires_acceptance": (
        "rejection_row_id IS NULL OR (rejection_plan_id IS NOT NULL AND "
        "extraction_plan_id IS NOT NULL AND extraction_acceptance_ref IS NOT "
        "NULL AND rejection_plan_id = extraction_plan_id)"
    ),
    "uq_result_extraction_links_composition_cluster": (
        "UNIQUE (composition_ref, result_cluster_ref)"
    ),
}
_REQUIRED_S3_INDEX_DEFINITIONS = {
    "ix_extraction_outcome_artifacts_artifact_ref": (
        "CREATE INDEX ix_extraction_outcome_artifacts_artifact_ref ON "
        "public.extraction_outcome_artifacts USING btree (artifact_ref)"
    ),
}


def expected_argus_schema_manifest() -> dict[str, Any]:
    """Trusted complete schema contract required after the 0007 migration."""
    from sqlalchemy import (
        BigInteger,
        Boolean,
        DateTime,
        Float,
        Integer,
        Numeric,
        String,
        Text,
    )

    from argus.persistence.provider_spend import SpendBase
    from argus.persistence.search_ledger import LedgerBase

    def normalized_type(column_type: Any) -> str:
        if isinstance(column_type, BigInteger):
            return "bigint"
        if isinstance(column_type, Integer):
            return "integer"
        if isinstance(column_type, Boolean):
            return "boolean"
        if isinstance(column_type, DateTime):
            return "timestamp without time zone"
        if isinstance(column_type, Text):
            return "text"
        if isinstance(column_type, String):
            return "character varying"
        if isinstance(column_type, (Numeric, Float)):
            return "numeric"
        raise RuntimeError(f"unrecognized schema contract type: {column_type!r}")

    tables = {
        **LedgerBase.metadata.tables,
        **SpendBase.metadata.tables,
    }
    columns = {
        table_name: {
            column.name: {
                "type": normalized_type(column.type),
                "nullable": bool(column.nullable),
            }
            for column in table.columns
        }
        for table_name, table in sorted(tables.items())
        if table_name in REQUIRED_TABLES
    }
    columns["alembic_version"] = {
        "version_num": {
            "type": "character varying",
            "nullable": False,
        }
    }
    manifest = {
        "schema_head": EXPECTED_SCHEMA_HEAD,
        "tables": sorted(REQUIRED_TABLES),
        "columns": columns,
        "constraints": deepcopy(_REQUIRED_S3_CONSTRAINT_DEFINITIONS),
        "indexes": deepcopy(_REQUIRED_S3_INDEX_DEFINITIONS),
    }
    manifest["contract_sha256"] = _manifest_sha256(manifest)
    return manifest


def _manifest_sha256(manifest: dict[str, Any]) -> str:
    contract = {
        key: value
        for key, value in manifest.items()
        if key != "contract_sha256"
    }
    return hashlib.sha256(
        json.dumps(
            contract,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _definition_tokens(definition: str) -> tuple[str, ...]:
    return tuple(
        re.findall(r"[A-Za-z_][A-Za-z0-9_]*", definition.upper())
    )


def _is_token_subsequence(
    expected: tuple[str, ...],
    actual: tuple[str, ...],
) -> bool:
    expected_index = 0
    for token in actual:
        if expected_index < len(expected) and token == expected[expected_index]:
            expected_index += 1
    return expected_index == len(expected)


def _verify_schema_manifest(
    *,
    expected: dict[str, Any],
    schema_head: str,
    tables: set[str],
    columns: list[tuple[Any, ...]],
    constraints: list[list[Any]],
    indexes: list[list[Any]],
) -> None:
    if (
        expected.get("contract_sha256") != _manifest_sha256(expected)
        or expected.get("schema_head") != schema_head
        or expected.get("tables") != sorted(tables)
    ):
        raise RuntimeError("database schema manifest does not match")

    actual_columns = {
        table: {
            str(row[1]): {
                "type": str(row[2]).lower(),
                "nullable": str(row[3]).upper() == "YES",
            }
            for row in columns
            if str(row[0]) == table
        }
        for table in sorted(tables)
    }
    if expected.get("columns") != actual_columns:
        raise RuntimeError("database schema manifest columns do not match")

    actual_constraints = {
        str(name): str(definition) for name, definition in constraints
    }
    actual_indexes = {str(name): str(definition) for name, definition in indexes}
    for kind, expected_definitions, actual_definitions in (
        ("constraint", expected.get("constraints"), actual_constraints),
        ("index", expected.get("indexes"), actual_indexes),
    ):
        if not isinstance(expected_definitions, dict):
            raise RuntimeError(f"database schema manifest {kind}s are invalid")
        for name, definition in expected_definitions.items():
            if (
                name not in actual_definitions
                or not _is_token_subsequence(
                    _definition_tokens(str(definition)),
                    _definition_tokens(actual_definitions[name]),
                )
            ):
                raise RuntimeError(
                    f"database schema manifest {kind} {name!r} does not match"
                )
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
    "SELECT count(*) FROM extraction_outcome_steps child "
    "LEFT JOIN extraction_outcome_plans parent ON parent.id = child.plan_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM extraction_outcome_artifacts child "
    "LEFT JOIN extraction_outcome_plans parent ON parent.id = child.plan_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM extraction_outcome_artifacts child "
    "LEFT JOIN extraction_artifact_identities parent "
    "ON parent.artifact_ref = child.artifact_ref "
    "WHERE parent.artifact_ref IS NULL",
    "SELECT count(*) FROM extraction_outcome_rejections child "
    "LEFT JOIN extraction_outcome_plans parent ON parent.id = child.plan_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM extraction_outcome_acceptances child "
    "LEFT JOIN extraction_outcome_plans parent ON parent.id = child.plan_id "
    "WHERE parent.id IS NULL",
    "SELECT count(*) FROM result_extraction_links child "
    "LEFT JOIN retrieval_compositions parent "
    "ON parent.receipt_ref = child.composition_ref "
    "WHERE parent.receipt_ref IS NULL",
    "SELECT count(*) FROM result_extraction_links child "
    "LEFT JOIN extraction_outcome_plans parent "
    "ON parent.id = child.extraction_plan_id "
    "WHERE child.extraction_plan_id IS NOT NULL AND parent.id IS NULL",
    "SELECT count(*) FROM result_extraction_links child "
    "LEFT JOIN extraction_outcome_acceptances acceptance "
    "ON acceptance.receipt_ref = child.extraction_acceptance_ref "
    "WHERE child.extraction_acceptance_ref IS NOT NULL "
    "AND (acceptance.receipt_ref IS NULL "
    "OR acceptance.plan_id <> child.extraction_plan_id)",
    "SELECT count(*) FROM result_extraction_links child "
    "LEFT JOIN extraction_outcome_artifacts artifact "
    "ON artifact.id = child.artifact_row_id "
    "WHERE child.artifact_row_id IS NOT NULL "
    "AND (artifact.id IS NULL OR artifact.plan_id <> child.artifact_plan_id)",
    "SELECT count(*) FROM result_extraction_links "
    "WHERE artifact_row_id IS NOT NULL AND "
    "(artifact_plan_id IS DISTINCT FROM extraction_plan_id "
    "OR extraction_plan_id IS NULL "
    "OR extraction_acceptance_ref IS NULL)",
    "SELECT count(*) FROM result_extraction_links child "
    "LEFT JOIN extraction_outcome_rejections rejection "
    "ON rejection.id = child.rejection_row_id "
    "WHERE child.rejection_row_id IS NOT NULL "
    "AND (rejection.id IS NULL OR rejection.plan_id <> child.rejection_plan_id)",
    "SELECT count(*) FROM result_extraction_links "
    "WHERE rejection_row_id IS NOT NULL AND "
    "(rejection_plan_id IS DISTINCT FROM extraction_plan_id "
    "OR extraction_plan_id IS NULL "
    "OR extraction_acceptance_ref IS NULL)",
)


def verify_argus_database(
    database: str,
    *,
    connect: Callable[..., Any] | None = None,
    repository_factory: Callable[[str], Any] | None = None,
    expected_inventory: dict[str, Any] | None = None,
    expected_schema_manifest: dict[str, Any] | None = None,
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
            cursor.execute(
                "SELECT table_name, column_name, data_type, is_nullable, "
                "column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public'"
            )
            columns_by_table: dict[str, set[str]] = {}
            schema_columns = cursor.fetchall()
            for table_name, column_name, *_ in schema_columns:
                columns_by_table.setdefault(table_name, set()).add(column_name)
            missing_columns = {
                table: sorted(required - columns_by_table.get(table, set()))
                for table, required in REQUIRED_S3_COLUMNS.items()
                if required - columns_by_table.get(table, set())
            }
            if missing_columns:
                raise RuntimeError(
                    "missing required S3 columns: "
                    + json.dumps(missing_columns, sort_keys=True)
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
            missing_schema_objects = {
                "constraints": sorted(
                    REQUIRED_S3_CONSTRAINTS
                    - {item[0] for item in inventory["constraints"]}
                ),
                "indexes": sorted(
                    REQUIRED_S3_INDEXES
                    - {item[0] for item in inventory["indexes"]}
                ),
            }
            missing_schema_objects = {
                kind: names
                for kind, names in missing_schema_objects.items()
                if names
            }
            if missing_schema_objects:
                raise RuntimeError(
                    "missing required S3 schema objects: "
                    + json.dumps(missing_schema_objects, sort_keys=True)
                )
            if expected_schema_manifest is not None:
                _verify_schema_manifest(
                    expected=expected_schema_manifest,
                    schema_head=schema_head,
                    tables=tables,
                    columns=schema_columns,
                    constraints=inventory["constraints"],
                    indexes=inventory["indexes"],
                )
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
    cursor.execute(
        "SELECT constraint_row.conname, "
        "pg_get_constraintdef(constraint_row.oid) "
        "FROM pg_constraint constraint_row "
        "JOIN pg_namespace namespace ON namespace.oid = constraint_row.connamespace "
        "WHERE namespace.nspname = 'public' ORDER BY constraint_row.conname"
    )
    constraints = [list(row) for row in cursor.fetchall()]
    cursor.execute(
        "SELECT indexname, indexdef FROM pg_indexes "
        "WHERE schemaname = 'public' ORDER BY indexname"
    )
    indexes = [list(row) for row in cursor.fetchall()]
    schema_state = {
        "tables": sorted(tables),
        "columns": columns,
        "constraints": constraints,
        "indexes": indexes,
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
        "constraints": constraints,
        "indexes": indexes,
    }


def _compare_inventory(
    actual: dict[str, Any],
    expected: dict[str, Any] | None,
) -> None:
    if expected is not None and actual != expected:
        raise RuntimeError("restored database does not match source inventory")
