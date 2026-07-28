"""Read-only verification of an explicitly disposable restored Argus database."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from argus.recovery.operator import (
    validate_database_name,
    validate_scratch_database,
)


EXPECTED_SCHEMA_HEAD = "0009_retrieval_evidence"
COMPATIBLE_SCHEMA_HEADS = frozenset(
    {
        "0007_extraction_outcomes",
        "0008_provider_readiness",
        EXPECTED_SCHEMA_HEAD,
    }
)
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
READINESS_TABLES = {
    "provider_readiness_observations",
    "provider_readiness_snapshots",
    "provider_readiness_evidence_refs",
    "provider_readiness_leases",
    "provider_readiness_alert_dedupe",
}
RETRIEVAL_EVIDENCE_TABLES = {
    "retrieval_evidence_plans",
    "retrieval_evidence_provider_batches",
    "retrieval_evidence_provider_attempts",
    "retrieval_evidence_observations",
    "retrieval_evidence_clusters",
    "retrieval_evidence_contributions",
    "retrieval_evidence_readiness_decisions",
    "retrieval_evidence_cache_lineage",
    "retrieval_evidence_accounting",
    "retrieval_evidence_trace_refs",
    "accepted_retrieval_operations",
    "retrieval_cache_publications",
}
REQUIRED_TABLES_BY_HEAD = {
    "0007_extraction_outcomes": REQUIRED_TABLES,
    "0008_provider_readiness": REQUIRED_TABLES | READINESS_TABLES,
    EXPECTED_SCHEMA_HEAD: (
        REQUIRED_TABLES | READINESS_TABLES | RETRIEVAL_EVIDENCE_TABLES
    ),
}
COUNTED_TABLES = sorted(REQUIRED_TABLES - {"alembic_version"})
REQUIRED_S3_COLUMNS = {
    "authentication_scope_authority_receipts": {
        "receipt_ref",
        "scope",
        "access_scope",
        "privacy_scope",
        "authentication_scope_fingerprint",
        "issued_at",
    },
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
SCHEMA_CONTRACT_PATHS = {
    "0007_extraction_outcomes": Path(__file__).with_name("argus_schema_0007.json"),
    "0008_provider_readiness": Path(__file__).with_name("argus_schema_0008.json"),
    EXPECTED_SCHEMA_HEAD: Path(__file__).with_name("argus_schema_0009.json"),
}
# Compatibility alias for recovery tooling that still targets the 0007 source
# inventory. Verification itself selects the contract from the restored head.
SCHEMA_CONTRACT_PATH = SCHEMA_CONTRACT_PATHS["0007_extraction_outcomes"]


def _postgresql_type_name(column_type: Any) -> str:
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
    if isinstance(column_type, Float):
        return "double precision"
    if isinstance(column_type, Numeric):
        return "numeric"
    raise RuntimeError(f"unrecognized schema contract type: {column_type!r}")


def _normalize_pg_definition(definition: str) -> str:
    normalized: list[str] = []
    pending_space = False
    index = 0
    length = len(definition)

    while index < length:
        character = definition[index]
        if character.isspace():
            pending_space = True
            index += 1
            continue
        if pending_space:
            if normalized:
                normalized.append(" ")
            pending_space = False

        if character == "'":
            start = index
            escape_string = (
                index > 0
                and definition[index - 1] in {"e", "E"}
                and (
                    index == 1
                    or not (
                        definition[index - 2].isalnum()
                        or definition[index - 2] in {"_", "$"}
                    )
                )
            )
            index += 1
            while index < length:
                if escape_string and definition[index] == "\\":
                    index = min(index + 2, length)
                elif definition[index] == "'":
                    if index + 1 < length and definition[index + 1] == "'":
                        index += 2
                    else:
                        index += 1
                        break
                else:
                    index += 1
            normalized.append(definition[start:index])
            continue

        if character == '"':
            start = index
            index += 1
            while index < length:
                if definition[index] == '"':
                    if index + 1 < length and definition[index + 1] == '"':
                        index += 2
                    else:
                        index += 1
                        break
                else:
                    index += 1
            normalized.append(definition[start:index])
            continue

        if character == "$":
            delimiter_end = index + 1
            while (
                delimiter_end < length
                and (
                    definition[delimiter_end].isalnum()
                    or definition[delimiter_end] == "_"
                )
            ):
                delimiter_end += 1
            tag = definition[index + 1 : delimiter_end]
            valid_tag = not tag or tag[0].isalpha() or tag[0] == "_"
            if (
                valid_tag
                and delimiter_end < length
                and definition[delimiter_end] == "$"
            ):
                delimiter = definition[index : delimiter_end + 1]
                body_end = definition.find(delimiter, delimiter_end + 1)
                if body_end >= 0:
                    literal_end = body_end + len(delimiter)
                    normalized.append(definition[index:literal_end])
                    index = literal_end
                    continue

        normalized.append(character)
        index += 1

    return "".join(normalized)


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


_SCHEMA_COLUMN_QUERY = (
    "SELECT table_name, column_name, data_type, is_nullable, "
    "column_default, character_maximum_length, "
    "numeric_precision, numeric_scale, datetime_precision, "
    "is_identity, identity_generation, is_generated, "
    "generation_expression "
    "FROM information_schema.columns "
    "WHERE table_schema = 'public' "
    "ORDER BY table_name, ordinal_position"
)
_SCHEMA_CONSTRAINT_QUERY = (
    "SELECT constraint_row.conname, relation.relname, "
    "pg_get_constraintdef(constraint_row.oid) "
    "FROM pg_constraint constraint_row "
    "JOIN pg_namespace namespace "
    "ON namespace.oid = constraint_row.connamespace "
    "JOIN pg_class relation "
    "ON relation.oid = constraint_row.conrelid "
    "WHERE namespace.nspname = 'public' "
    "ORDER BY constraint_row.conname"
)
_SCHEMA_INDEX_QUERY = (
    "SELECT index_relation.relname, table_relation.relname, "
    "pg_get_indexdef(index_relation.oid) "
    "FROM pg_index index_row "
    "JOIN pg_class index_relation "
    "ON index_relation.oid = index_row.indexrelid "
    "JOIN pg_class table_relation "
    "ON table_relation.oid = index_row.indrelid "
    "JOIN pg_namespace namespace "
    "ON namespace.oid = table_relation.relnamespace "
    "WHERE namespace.nspname = 'public' "
    "ORDER BY index_relation.relname"
)


def _column_manifest(
    tables: set[str],
    rows: list[tuple[Any, ...]],
) -> dict[str, dict[str, dict[str, Any]]]:
    return {
        table: {
            str(row[1]): {
                "type": str(row[2]).lower(),
                "character_maximum_length": row[5],
                "numeric_precision": row[6],
                "numeric_scale": row[7],
                "datetime_precision": row[8],
                "default": (
                    _normalize_pg_definition(str(row[4]))
                    if row[4] is not None
                    else None
                ),
                "identity": {
                    "is_identity": str(row[9]).upper() == "YES",
                    "generation": row[10],
                },
                "generated": {
                    "is_generated": str(row[11]).upper() != "NEVER",
                    "expression": (
                        _normalize_pg_definition(str(row[12]))
                        if row[12] is not None
                        else None
                    ),
                },
                "nullable": str(row[3]).upper() == "YES",
            }
            for row in rows
            if str(row[0]) == table
        }
        for table in sorted(tables)
    }


def _definition_manifest(
    rows: list[tuple[Any, ...]] | list[list[Any]],
) -> dict[str, dict[str, str]]:
    return {
        str(name): {
            "table": str(table),
            "definition": _normalize_pg_definition(str(definition)),
        }
        for name, table, definition in rows
    }


def build_argus_schema_contract(
    *,
    connection: Any | None = None,
) -> dict[str, Any]:
    """Capture the exact contract from an Alembic-migrated PostgreSQL schema."""
    if connection is None:
        raise RuntimeError(
            "PostgreSQL source is required to regenerate the schema contract"
        )
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'"
        )
        tables = {str(row[0]) for row in cursor.fetchall()}
        cursor.execute("SELECT version_num FROM alembic_version")
        schema_head = str(cursor.fetchone()[0])
        cursor.execute(_SCHEMA_COLUMN_QUERY)
        column_rows = cursor.fetchall()
        cursor.execute(_SCHEMA_CONSTRAINT_QUERY)
        constraint_rows = cursor.fetchall()
        cursor.execute(_SCHEMA_INDEX_QUERY)
        index_rows = cursor.fetchall()

    required_tables = REQUIRED_TABLES_BY_HEAD.get(schema_head)
    if required_tables is None or tables != required_tables:
        raise RuntimeError("PostgreSQL source does not have the exact Argus tables")
    if schema_head not in COMPATIBLE_SCHEMA_HEADS:
        raise RuntimeError(
            "PostgreSQL source is not migrated to the expected schema head"
        )
    manifest = {
        "format_version": 1,
        "schema_head": schema_head,
        "tables": sorted(tables),
        "columns": _column_manifest(tables, column_rows),
        "constraints": dict(
            sorted(_definition_manifest(constraint_rows).items())
        ),
        "indexes": dict(sorted(_definition_manifest(index_rows).items())),
    }
    manifest["contract_sha256"] = _manifest_sha256(manifest)
    return manifest


def expected_argus_schema_manifest(
    schema_head: str = "0007_extraction_outcomes",
) -> dict[str, Any]:
    """Load the checked-in complete PostgreSQL schema contract."""
    try:
        path = SCHEMA_CONTRACT_PATHS[schema_head]
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("checked-in Argus schema contract is invalid") from error
    if manifest.get("contract_sha256") != _manifest_sha256(manifest):
        raise RuntimeError("checked-in Argus schema contract hash is invalid")
    return manifest


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

    actual_columns = _column_manifest(tables, columns)
    if expected.get("columns") != actual_columns:
        raise RuntimeError("database schema manifest columns do not match")

    actual_constraints = _definition_manifest(constraints)
    actual_indexes = _definition_manifest(indexes)
    if expected.get("constraints") != actual_constraints:
        raise RuntimeError("database schema manifest constraints do not match")
    if expected.get("indexes") != actual_indexes:
        raise RuntimeError("database schema manifest indexes do not match")
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
            cursor.execute("SELECT version_num FROM alembic_version")
            schema_head = cursor.fetchone()[0]
            if schema_head not in COMPATIBLE_SCHEMA_HEADS:
                raise RuntimeError(
                    f"schema head {schema_head!r} is not {EXPECTED_SCHEMA_HEAD!r}"
                )
            required_tables = REQUIRED_TABLES_BY_HEAD[schema_head]
            missing = sorted(required_tables - tables)
            unexpected = sorted(tables - required_tables)
            if missing or unexpected:
                if missing and not unexpected:
                    raise RuntimeError(
                        "missing required tables: " + ", ".join(missing)
                    )
                raise RuntimeError(
                    "database schema manifest table mismatch: "
                    + json.dumps(
                        {"missing": missing, "unexpected": unexpected},
                        sort_keys=True,
                    )
                )
            schema_manifest = expected_argus_schema_manifest(schema_head)
            if (
                expected_schema_manifest is not None
                and expected_schema_manifest != schema_manifest
            ):
                raise RuntimeError(
                    "caller schema manifest does not match checked-in schema contract"
                )
            cursor.execute(_SCHEMA_COLUMN_QUERY)
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

            row_counts = {}
            for table in sorted(required_tables - {"alembic_version"}):
                cursor.execute(f'SELECT count(*) FROM "{table}"')
                row_counts[table] = int(cursor.fetchone()[0])
            inventory = _inventory(cursor, tables, row_counts)
            _verify_schema_manifest(
                expected=schema_manifest,
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
    cursor.execute(_SCHEMA_CONSTRAINT_QUERY)
    constraints = [list(row) for row in cursor.fetchall()]
    cursor.execute(_SCHEMA_INDEX_QUERY)
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
