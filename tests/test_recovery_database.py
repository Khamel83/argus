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


def _manifest_column_rows(manifest):
    return [
        (
            table,
            column,
            definition["type"],
            "YES" if definition["nullable"] else "NO",
            definition["default"],
            definition["character_maximum_length"],
            definition["numeric_precision"],
            definition["numeric_scale"],
            definition["datetime_precision"],
            "YES" if definition["identity"]["is_identity"] else "NO",
            definition["identity"]["generation"],
            (
                "ALWAYS"
                if definition["generated"]["is_generated"]
                else "NEVER"
            ),
            definition["generated"]["expression"],
        )
        for table, columns in manifest["columns"].items()
        for column, definition in columns.items()
    ]


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
                row
                for row in _manifest_column_rows(
                    expected_argus_schema_manifest()
                )
                if row[0] in self.tables
            ]
        if "pg_get_constraintdef" in self.query:
            from argus.recovery.database import expected_argus_schema_manifest

            return [
                (name, value["table"], value["definition"])
                for name, value in expected_argus_schema_manifest()[
                    "constraints"
                ].items()
            ]
        if "pg_get_indexdef" in self.query:
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
            if (
                "pg_constraint" in self.query
                or "pg_get_indexdef" in self.query
            ):
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
    with pytest.raises(RuntimeError, match="PostgreSQL source"):
        build_argus_schema_contract()
    assert len(manifest["constraints"]) == 83
    assert len(manifest["indexes"]) == 57
    latency = manifest["columns"]["extraction_outcome_steps"]["latency_ms"]
    assert latency["type"] == "bigint"
    assert latency["nullable"] is True
    assert manifest["columns"]["provider_attempts"]["budget_remaining"][
        "type"
    ] == "double precision"
    assert _postgresql_type_name(Float()) == "double precision"
    assert _postgresql_type_name(Numeric()) == "numeric"
    assert "MATCH FULL" in manifest["constraints"][
        "fk_result_extraction_links_acceptance_plan"
    ]["definition"]
    assert "artifact_ref" in manifest["indexes"][
        "ix_extraction_outcome_artifacts_artifact_ref"
    ]["definition"]


def test_0008_contract_contains_all_readiness_authority_tables():
    from argus.recovery.database import expected_argus_schema_manifest

    manifest = expected_argus_schema_manifest("0008_provider_readiness")

    assert manifest["schema_head"] == "0008_provider_readiness"
    assert {
        "provider_readiness_observations",
        "provider_readiness_snapshots",
        "provider_readiness_evidence_refs",
        "provider_readiness_leases",
        "provider_readiness_alert_dedupe",
    } <= set(manifest["columns"])
    assert len(manifest["contract_sha256"]) == 64


def test_current_schema_head_has_complete_contract_registration():
    from argus.recovery.database import (
        COMPATIBLE_SCHEMA_HEADS,
        EXPECTED_SCHEMA_HEAD,
        REQUIRED_TABLES_BY_HEAD,
        SCHEMA_CONTRACT_PATHS,
        expected_argus_schema_manifest,
    )

    retrieval_evidence_tables = {
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

    assert EXPECTED_SCHEMA_HEAD in COMPATIBLE_SCHEMA_HEADS
    assert retrieval_evidence_tables <= REQUIRED_TABLES_BY_HEAD[
        EXPECTED_SCHEMA_HEAD
    ]
    assert SCHEMA_CONTRACT_PATHS[EXPECTED_SCHEMA_HEAD].name == (
        "argus_schema_0009.json"
    )
    manifest = expected_argus_schema_manifest(EXPECTED_SCHEMA_HEAD)
    assert manifest["schema_head"] == EXPECTED_SCHEMA_HEAD
    assert retrieval_evidence_tables <= set(manifest["columns"])
    assert len(manifest["contract_sha256"]) == 64


def test_checked_contract_retains_migrated_server_defaults_and_deparser_output():
    from argus.recovery.database import expected_argus_schema_manifest

    manifest = expected_argus_schema_manifest()

    assert manifest["columns"]["retrieval_requests"]["free_only"][
        "default"
    ] == "false"
    assert manifest["columns"]["provider_spend_attempts"][
        "estimator_violation"
    ]["default"] == "false"
    assert manifest["columns"]["provider_spend_attempts"][
        "reservation_overrun"
    ]["default"] == "'0'::double precision"
    assert manifest["columns"]["delivery_intents"]["attempt_count"][
        "default"
    ] == "0"
    assert manifest["columns"]["delivery_intents"]["max_attempts"][
        "default"
    ] == "8"
    assert "::text" in manifest["constraints"][
        "ck_result_extraction_links_artifact_same_plan"
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
                return _manifest_column_rows(trusted)
            if "pg_get_constraintdef" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in trusted["constraints"].items()
                ]
            if "pg_get_indexdef" in self.query:
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

    with pytest.raises(RuntimeError, match="checked-in.*schema contract"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
            repository_factory=lambda database: None,
            expected_schema_manifest=corrupted,
        )


def test_self_hashed_caller_manifest_cannot_replace_checked_in_trust():
    import hashlib
    import json

    from argus.recovery.database import (
        expected_argus_schema_manifest,
        verify_argus_database,
    )

    untrusted = expected_argus_schema_manifest()
    untrusted["columns"]["extraction_outcome_steps"]["latency_ms"][
        "type"
    ] = "integer"
    untrusted["contract_sha256"] = hashlib.sha256(
        json.dumps(
            {
                key: value
                for key, value in untrusted.items()
                if key != "contract_sha256"
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()

    class UntrustedManifestCursor(FakeCursor):
        def fetchall(self):
            if "information_schema.columns" in self.query:
                return _manifest_column_rows(untrusted)
            if "pg_get_constraintdef" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in untrusted["constraints"].items()
                ]
            if "pg_get_indexdef" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in untrusted["indexes"].items()
                ]
            return super().fetchall()

    connection = FakeConnection(set(untrusted["tables"]))
    connection.cursor_instance = UntrustedManifestCursor(
        set(untrusted["tables"])
    )
    repository = type(
        "Repository",
        (),
        {"list_session_ids": lambda self: []},
    )()

    with pytest.raises(RuntimeError, match="checked-in.*schema contract"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
            repository_factory=lambda database: repository,
            expected_schema_manifest=untrusted,
        )


def test_definition_normalization_preserves_boolean_grouping():
    from argus.recovery.database import _normalize_pg_definition

    left_grouped = "CHECK ((a AND (b OR c)))"
    right_grouped = "CHECK (((a AND b) OR c))"

    assert _normalize_pg_definition(left_grouped) != _normalize_pg_definition(
        right_grouped
    )


def test_definition_normalization_distinguishes_text_array_from_text():
    from argus.recovery.database import _normalize_pg_definition

    text_array = "CHECK (value::text[] IS NOT NULL)"
    scalar_text = "CHECK (value::text IS NOT NULL)"

    assert _normalize_pg_definition(
        text_array
    ) != _normalize_pg_definition(scalar_text)


def test_definition_normalization_distinguishes_quoted_identifier_from_keyword():
    from argus.recovery.database import _normalize_pg_definition

    quoted_identifier = 'CHECK ("current_user" IS NOT NULL)'
    keyword = "CHECK (CURRENT_USER IS NOT NULL)"

    assert _normalize_pg_definition(
        quoted_identifier
    ) != _normalize_pg_definition(keyword)


def test_definition_normalization_preserves_dollar_quote_tag_body_and_case():
    from argus.recovery.database import _normalize_pg_definition

    assert _normalize_pg_definition(
        "CHECK ($$Mixed Case$$ IS NOT NULL)"
    ) != _normalize_pg_definition(
        "CHECK ($$mixed case$$ IS NOT NULL)"
    )
    assert _normalize_pg_definition(
        "CHECK ($Body$Mixed Case$Body$ IS NOT NULL)"
    ) != _normalize_pg_definition(
        "CHECK ($body$Mixed Case$body$ IS NOT NULL)"
    )


def test_definition_normalization_preserves_escape_string_as_one_token():
    from argus.recovery.database import _normalize_pg_definition

    definition = r"CHECK (value = E'Mixed\'Case')"

    assert r"E'Mixed\'Case'" in _normalize_pg_definition(definition)


def test_definition_normalization_only_collapses_external_whitespace():
    from argus.recovery.database import _normalize_pg_definition

    definition = '  CHECK  ( "Case Sensitive" = \'A  B\' )  '

    assert _normalize_pg_definition(definition) == (
        'CHECK ( "Case Sensitive" = \'A  B\' )'
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            "CHECK (items[1:2] IS NULL)",
            "CHECK (items[12] IS NULL)",
        ),
        (
            "CHECK (value::text[] IS NOT NULL)",
            "CHECK (value::text IS NOT NULL)",
        ),
        (
            "CHECK (payload ?| ARRAY['a'])",
            "CHECK (payload | ARRAY['a'])",
        ),
        (
            "CHECK (payload ?& ARRAY['a'])",
            "CHECK (payload & ARRAY['a'])",
        ),
        (
            "CHECK (payload @? '$.a')",
            "CHECK (payload @ '$.a')",
        ),
        (
            "CHECK (payload -> 'a' IS NOT NULL)",
            "CHECK (payload ->> 'a' IS NOT NULL)",
        ),
        (
            "CHECK (label = 'Mixed Case')",
            "CHECK (label = 'mixed case')",
        ),
        (
            'CHECK ("MixedCase" IS NOT NULL)',
            "CHECK (mixedcase IS NOT NULL)",
        ),
        (
            r"CHECK (value = E'Mixed\'Case')",
            r"CHECK (value = E'mixed\'case')",
        ),
        (
            "CHECK ($tag$Mixed Case$tag$ IS NOT NULL)",
            "CHECK ($tag$mixed case$tag$ IS NOT NULL)",
        ),
        (
            'CHECK ("A""B" IS NOT NULL)',
            'CHECK ("a""b" IS NOT NULL)',
        ),
    ],
)
def test_definition_normalization_has_no_significant_token_collisions(
    left,
    right,
):
    from argus.recovery.database import _normalize_pg_definition

    assert _normalize_pg_definition(left) != _normalize_pg_definition(right)


def test_definition_normalization_preserves_postgresql_punctuation_alphabet():
    from argus.recovery.database import _normalize_pg_definition

    operator_characters = r"+-*/<>=~!@#%^&|`?\\"
    syntax_punctuation = "[](){},.;:"

    for token in operator_characters + syntax_punctuation:
        assert token in _normalize_pg_definition(f"left {token} right")


def test_checked_contract_records_complete_column_semantics():
    from argus.recovery.database import expected_argus_schema_manifest

    manifest = expected_argus_schema_manifest()
    varchar = manifest["columns"]["retrieval_requests"]["id"]
    numeric = manifest["columns"]["extraction_outcome_steps"]["latency_ms"]

    assert varchar == {
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
    assert numeric["numeric_precision"] == 64
    assert numeric["numeric_scale"] == 0


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    [
        ("type", "text"),
        ("character_maximum_length", 31),
        ("numeric_precision", 63),
        ("numeric_scale", 1),
        ("datetime_precision", 3),
        ("default", "'drift'::character varying"),
        ("is_identity", True),
        ("identity_generation", "ALWAYS"),
        ("is_generated", True),
        ("generation_expression", "length(id)"),
        ("nullable", True),
    ],
)
def test_restore_rejects_each_column_semantic_drift(attribute, replacement):
    from argus.recovery.database import (
        expected_argus_schema_manifest,
        verify_argus_database,
    )

    trusted = expected_argus_schema_manifest()
    target_column = (
        "latency_ms"
        if attribute in {"numeric_precision", "numeric_scale"}
        else "id"
    )
    target_table = (
        "provider_attempts"
        if target_column == "latency_ms"
        else "retrieval_requests"
    )

    class ColumnDriftCursor(FakeCursor):
        def fetchall(self):
            if "information_schema.columns" in self.query:
                rows = []
                for table, columns in trusted["columns"].items():
                    for column, definition in columns.items():
                        values = {
                            "type": definition["type"],
                            "nullable": definition["nullable"],
                            "default": definition.get("default"),
                            "character_maximum_length": definition.get(
                                "character_maximum_length"
                            ),
                            "numeric_precision": definition.get(
                                "numeric_precision"
                            ),
                            "numeric_scale": definition.get("numeric_scale"),
                            "datetime_precision": definition.get(
                                "datetime_precision"
                            ),
                            "is_identity": definition.get("identity", {}).get(
                                "is_identity", False
                            ),
                            "identity_generation": definition.get(
                                "identity", {}
                            ).get("generation"),
                            "is_generated": definition.get(
                                "generated", {}
                            ).get("is_generated", False),
                            "generation_expression": definition.get(
                                "generated", {}
                            ).get("expression"),
                        }
                        if table == target_table and column == target_column:
                            values[attribute] = replacement
                        rows.append(
                            (
                                table,
                                column,
                                values["type"],
                                "YES" if values["nullable"] else "NO",
                                values["default"],
                                values["character_maximum_length"],
                                values["numeric_precision"],
                                values["numeric_scale"],
                                values["datetime_precision"],
                                "YES" if values["is_identity"] else "NO",
                                values["identity_generation"],
                                "ALWAYS"
                                if values["is_generated"]
                                else "NEVER",
                                values["generation_expression"],
                            )
                        )
                return rows
            if "pg_get_constraintdef" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in trusted["constraints"].items()
                ]
            if "pg_get_indexdef" in self.query:
                return [
                    (name, value["table"], value["definition"])
                    for name, value in trusted["indexes"].items()
                ]
            return super().fetchall()

    connection = FakeConnection(set(trusted["tables"]))
    connection.cursor_instance = ColumnDriftCursor(set(trusted["tables"]))
    repository = type(
        "Repository",
        (),
        {"list_session_ids": lambda self: []},
    )()

    with pytest.raises(RuntimeError, match="schema manifest columns"):
        verify_argus_database(
            "argus_restore_issue40_test",
            connect=lambda **kwargs: connection,
            repository_factory=lambda database: repository,
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
                return _manifest_column_rows(trusted)
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
            if "pg_get_indexdef" in self.query:
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


def test_migrated_postgres_matches_checked_in_schema_contract(
    migrated_postgres_ledger,
):
    from argus.recovery.database import (
        EXPECTED_SCHEMA_HEAD,
        build_argus_schema_contract,
        expected_argus_schema_manifest,
    )

    engine = migrated_postgres_ledger.session_factory.kw["bind"]
    connection = engine.raw_connection()
    try:
        actual = build_argus_schema_contract(connection=connection)
    finally:
        connection.close()

    assert actual == expected_argus_schema_manifest(EXPECTED_SCHEMA_HEAD)


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


def test_restored_source_inventory_accepts_manifest_projection_and_rejects_drift():
    from argus.recovery.database import verify_restored_source_inventory

    database = "argus_restore_issue40_manifest_projection"
    baseline = verify_restored_source_inventory(
        database,
        tenant="argus",
        expected_inventory=None,
        connect=lambda **kwargs: FakeConnection(REQUIRED_TABLES, database),
    )
    projection = {
        key: baseline[key]
        for key in ("tables", "schema_sha256", "constraints_validated")
    }

    assert verify_restored_source_inventory(
        database,
        tenant="argus",
        expected_inventory=projection,
        connect=lambda **kwargs: FakeConnection(REQUIRED_TABLES, database),
    ) == baseline
    assert verify_restored_source_inventory(
        database,
        tenant="argus",
        expected_inventory=baseline,
        connect=lambda **kwargs: FakeConnection(REQUIRED_TABLES, database),
    ) == baseline

    invalid_expected = (
        {**projection, "tables": {**projection["tables"], "retrieval_runs": 1}},
        {**projection, "schema_sha256": "f" * 64},
        {**projection, "tables": []},
        {**projection, "unexpected": True},
        {key: value for key, value in projection.items() if key != "constraints_validated"},
        {**baseline, "indexes": [*baseline["indexes"], ["unexpected"]]},
    )
    for expected in invalid_expected:
        with pytest.raises(RuntimeError, match="source inventory"):
            verify_restored_source_inventory(
                database,
                tenant="argus",
                expected_inventory=expected,
                connect=lambda **kwargs: FakeConnection(REQUIRED_TABLES, database),
            )


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
        EXPECTED_SCHEMA_HEAD,
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

        assert report["schema_head"] == EXPECTED_SCHEMA_HEAD
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


def test_bridge_accepts_additive_forward_schema_heads():
    from argus.recovery.database import (
        COMPATIBLE_SCHEMA_HEADS,
        FORWARD_COMPATIBLE_SCHEMA_HEADS,
    )

    assert FORWARD_COMPATIBLE_SCHEMA_HEADS == {
        "0010_domain_policies",
        "0011_extraction_spend_scope",
    }
    assert FORWARD_COMPATIBLE_SCHEMA_HEADS <= COMPATIBLE_SCHEMA_HEADS


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
