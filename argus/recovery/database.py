"""Read-only verification of an explicitly disposable restored Argus database."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
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
SCHEMA_CONTRACT_PATH = Path(__file__).with_name("argus_schema_0007.json")


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
    tokens = re.findall(
        r"'(?:''|[^'])*'"
        r'|"(?:[^"]|"")*"'
        r"|::|<>|<=|>=|!="
        r"|[(),.;]"
        r"|[-+*/%^<>=~!@#&|]+"
        r"|[A-Za-z_][A-Za-z0-9_$]*"
        r"|\d+(?:\.\d+)?",
        definition,
    )
    normalized = []
    for token in tokens:
        if token.startswith("'"):
            normalized.append(token)
        elif token.startswith('"'):
            identifier = token[1:-1].replace('""', '"')
            normalized.append(
                identifier
                if re.fullmatch(r"[a-z_][a-z0-9_$]*", identifier)
                else token
            )
        elif re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]*", token):
            normalized.append(token.lower())
        else:
            normalized.append(token)
    return " ".join(normalized)


def _numeric_semantics(column_type: Any) -> tuple[int | None, int | None]:
    from sqlalchemy import BigInteger, Float, Integer, Numeric

    if isinstance(column_type, BigInteger):
        return 64, 0
    if isinstance(column_type, Integer):
        return 32, 0
    if isinstance(column_type, Float):
        return column_type.precision or 53, None
    if isinstance(column_type, Numeric):
        return column_type.precision, column_type.scale
    return None, None


def _column_semantics(column: Any) -> dict[str, Any]:
    from sqlalchemy import DateTime, String, Text

    numeric_precision, numeric_scale = _numeric_semantics(column.type)
    server_default = column.server_default
    default = (
        _normalize_pg_definition(str(server_default.arg))
        if server_default is not None
        else None
    )
    identity = column.identity
    computed = column.computed
    return {
        "type": _postgresql_type_name(column.type),
        "character_maximum_length": (
            column.type.length
            if isinstance(column.type, String)
            and not isinstance(column.type, Text)
            else None
        ),
        "numeric_precision": numeric_precision,
        "numeric_scale": numeric_scale,
        "datetime_precision": (
            6 if isinstance(column.type, DateTime) else None
        ),
        "default": default,
        "identity": {
            "is_identity": identity is not None,
            "generation": (
                str(identity.always and "ALWAYS" or "BY DEFAULT")
                if identity is not None
                else None
            ),
        },
        "generated": {
            "is_generated": computed is not None,
            "expression": (
                _normalize_pg_definition(str(computed.sqltext))
                if computed is not None
                else None
            ),
        },
        "nullable": bool(column.nullable),
    }


def _constraint_name(table_name: str, constraint: Any) -> str:
    from sqlalchemy import (
        CheckConstraint,
        ForeignKeyConstraint,
        PrimaryKeyConstraint,
        UniqueConstraint,
    )

    if constraint.name:
        return str(constraint.name)
    columns = [column.name for column in constraint.columns]
    if isinstance(constraint, PrimaryKeyConstraint):
        return f"{table_name}_pkey"
    if isinstance(constraint, UniqueConstraint):
        return f"{table_name}_{'_'.join(columns)}_key"
    if isinstance(constraint, ForeignKeyConstraint):
        return f"{table_name}_{columns[0]}_fkey"
    if isinstance(constraint, CheckConstraint):
        raise RuntimeError(f"check constraint on {table_name} must be named")
    raise RuntimeError(f"unsupported constraint on {table_name}")


def _constraint_definition(constraint: Any) -> str:
    from sqlalchemy import (
        CheckConstraint,
        ForeignKeyConstraint,
        PrimaryKeyConstraint,
        UniqueConstraint,
    )

    columns = ", ".join(column.name for column in constraint.columns)
    if isinstance(constraint, PrimaryKeyConstraint):
        return f"PRIMARY KEY ({columns})"
    if isinstance(constraint, UniqueConstraint):
        return f"UNIQUE ({columns})"
    if isinstance(constraint, ForeignKeyConstraint):
        target_table = constraint.elements[0].column.table.name
        target_columns = ", ".join(
            element.column.name for element in constraint.elements
        )
        match = f" MATCH {constraint.match}" if constraint.match else ""
        return (
            f"FOREIGN KEY ({columns}) REFERENCES {target_table} "
            f"({target_columns}){match}"
        )
    if isinstance(constraint, CheckConstraint):
        return f"CHECK ({constraint.sqltext})"
    raise RuntimeError("unsupported schema constraint")


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


def build_argus_schema_contract() -> dict[str, Any]:
    """Generate the complete normalized PostgreSQL contract from metadata."""
    from sqlalchemy import PrimaryKeyConstraint, UniqueConstraint

    from argus.persistence.provider_spend import SpendBase
    from argus.persistence.search_ledger import LedgerBase

    tables = {
        **LedgerBase.metadata.tables,
        **SpendBase.metadata.tables,
    }
    columns = {
        table_name: {
            column.name: _column_semantics(column)
            for column in table.columns
        }
        for table_name, table in sorted(tables.items())
        if table_name in REQUIRED_TABLES
    }
    columns["alembic_version"] = {
        "version_num": {
            "type": "character varying",
            "character_maximum_length": 32,
            "numeric_precision": None,
            "numeric_scale": None,
            "datetime_precision": None,
            "default": None,
            "identity": {
                "is_identity": False,
                "generation": None,
            },
            "generated": {
                "is_generated": False,
                "expression": None,
            },
            "nullable": False,
        }
    }
    constraints: dict[str, dict[str, str]] = {}
    indexes: dict[str, dict[str, str]] = {}
    for table_name, table in sorted(tables.items()):
        if table_name not in REQUIRED_TABLES:
            continue
        for constraint in table.constraints:
            name = _constraint_name(table_name, constraint)
            constraints[name] = {
                "table": table_name,
                "definition": _normalize_pg_definition(
                    _constraint_definition(constraint)
                ),
            }
            if isinstance(
                constraint,
                (PrimaryKeyConstraint, UniqueConstraint),
            ):
                columns_sql = ", ".join(
                    column.name for column in constraint.columns
                )
                indexes[name] = {
                    "table": table_name,
                    "definition": _normalize_pg_definition(
                        f"CREATE UNIQUE INDEX {name} ON public.{table_name} "
                        f"USING btree ({columns_sql})"
                    ),
                }
        for index in table.indexes:
            unique = "UNIQUE " if index.unique else ""
            columns_sql = ", ".join(column.name for column in index.columns)
            indexes[str(index.name)] = {
                "table": table_name,
                "definition": _normalize_pg_definition(
                    f"CREATE {unique}INDEX {index.name} ON "
                    f"public.{table_name} USING btree ({columns_sql})"
                ),
            }
    constraints["alembic_version_pkc"] = {
        "table": "alembic_version",
        "definition": _normalize_pg_definition(
            "PRIMARY KEY (version_num)"
        ),
    }
    indexes["alembic_version_pkc"] = {
        "table": "alembic_version",
        "definition": _normalize_pg_definition(
            "CREATE UNIQUE INDEX alembic_version_pkc ON "
            "public.alembic_version USING btree (version_num)"
        ),
    }
    manifest = {
        "format_version": 1,
        "schema_head": EXPECTED_SCHEMA_HEAD,
        "tables": sorted(REQUIRED_TABLES),
        "columns": columns,
        "constraints": dict(sorted(constraints.items())),
        "indexes": dict(sorted(indexes.items())),
    }
    manifest["contract_sha256"] = _manifest_sha256(manifest)
    return manifest


def expected_argus_schema_manifest() -> dict[str, Any]:
    """Load the checked-in complete PostgreSQL schema contract."""
    try:
        manifest = json.loads(SCHEMA_CONTRACT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("checked-in Argus schema contract is invalid") from error
    if manifest.get("contract_sha256") != _manifest_sha256(manifest):
        raise RuntimeError("checked-in Argus schema contract hash is invalid")
    return manifest


_GENERATED_SCHEMA_OBJECTS = build_argus_schema_contract()
REQUIRED_S3_CONSTRAINTS = set(_GENERATED_SCHEMA_OBJECTS["constraints"])
REQUIRED_S3_INDEXES = set(_GENERATED_SCHEMA_OBJECTS["indexes"])


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
            for row in columns
            if str(row[0]) == table
        }
        for table in sorted(tables)
    }
    if expected.get("columns") != actual_columns:
        raise RuntimeError("database schema manifest columns do not match")

    actual_constraints = {
        str(name): {
            "table": str(table),
            "definition": _normalize_pg_definition(str(definition)),
        }
        for name, table, definition in constraints
    }
    actual_indexes = {
        str(name): {
            "table": str(table),
            "definition": _normalize_pg_definition(str(definition)),
        }
        for name, table, definition in indexes
    }
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
    schema_manifest = expected_argus_schema_manifest()
    if (
        expected_schema_manifest is not None
        and expected_schema_manifest != schema_manifest
    ):
        raise RuntimeError(
            "caller schema manifest does not match checked-in schema contract"
        )
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
                "column_default, character_maximum_length, "
                "numeric_precision, numeric_scale, datetime_precision, "
                "is_identity, identity_generation, is_generated, "
                "generation_expression "
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
    cursor.execute(
        "SELECT constraint_row.conname, relation.relname, "
        "pg_get_constraintdef(constraint_row.oid) "
        "FROM pg_constraint constraint_row "
        "JOIN pg_namespace namespace ON namespace.oid = constraint_row.connamespace "
        "JOIN pg_class relation ON relation.oid = constraint_row.conrelid "
        "WHERE namespace.nspname = 'public' ORDER BY constraint_row.conname"
    )
    constraints = [list(row) for row in cursor.fetchall()]
    cursor.execute(
        "SELECT indexname, tablename, indexdef FROM pg_indexes "
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
