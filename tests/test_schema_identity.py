import json

import pytest


def _migration(path, revision, down_revision):
    path.write_text(
        "\n".join(
            (
                f"revision = {revision!r}",
                f"down_revision = {down_revision!r}",
                "depends_on = None",
            )
        )
        + "\n",
        encoding="utf-8",
    )


def test_migration_chain_hash_is_deterministic_and_ordered(tmp_path):
    from argus.recovery.database import (
        migration_chain_manifest,
        migration_chain_sha256,
    )

    _migration(tmp_path / "0001.py", "0001", None)
    _migration(tmp_path / "0002.py", "0002", "0001")

    first = migration_chain_manifest("0002", migrations_path=tmp_path)
    second = migration_chain_manifest("0002", migrations_path=tmp_path)

    assert first == second
    assert [entry["revision"] for entry in first["migrations"]] == [
        "0001",
        "0002",
    ]
    assert migration_chain_sha256("0002", migrations_path=tmp_path) == (
        migration_chain_sha256("0002", migrations_path=tmp_path)
    )


def test_migration_file_content_changes_chain_hash(tmp_path):
    from argus.recovery.database import migration_chain_sha256

    _migration(tmp_path / "0001.py", "0001", None)
    original = migration_chain_sha256("0001", migrations_path=tmp_path)
    (tmp_path / "0001.py").write_text(
        "revision = '0001'\n"
        "down_revision = None\n"
        "depends_on = None\n"
        "# migration source changed\n",
        encoding="utf-8",
    )
    assert migration_chain_sha256("0001", migrations_path=tmp_path) != original


def test_catalog_hash_is_order_independent_and_covers_each_component():
    from argus.recovery.database import canonical_postgresql_schema_sha256

    catalog = {
        "tables": ["b", "a"],
        "columns": {
            "b": {"id": {"type": "integer", "nullable": False}},
            "a": {"id": {"type": "integer", "nullable": False}},
        },
        "constraints": {
            "b_pk": {"table": "b", "definition": "PRIMARY KEY (id)"}
        },
        "indexes": {
            "a_idx": {"table": "a", "definition": "INDEX (id)"}
        },
        "functions": {
            "public.f()": {"schema": "public", "definition": "f()"}
        },
    }
    reordered = json.loads(json.dumps(catalog))
    reordered["tables"] = ["a", "b"]

    assert canonical_postgresql_schema_sha256(catalog) == (
        canonical_postgresql_schema_sha256(reordered)
    )
    for component in ("tables", "columns", "constraints", "indexes", "functions"):
        changed = json.loads(json.dumps(catalog))
        if component == "tables":
            changed[component].append("c")
        elif component == "columns":
            changed[component]["a"]["name"] = {
                "type": "text",
                "nullable": True,
            }
        else:
            changed[component][f"changed_{component}"] = {
                "table": "a",
                "definition": "changed",
            }
        assert canonical_postgresql_schema_sha256(changed) != (
            canonical_postgresql_schema_sha256(catalog)
        )


def test_current_contract_contains_the_four_part_identity():
    from argus.recovery.database import (
        EXPECTED_SCHEMA_HEAD,
        SCHEMA_CONTRACT_FORMAT,
        expected_argus_schema_manifest,
        schema_identity_from_manifest,
    )

    manifest = expected_argus_schema_manifest(EXPECTED_SCHEMA_HEAD)
    identity = schema_identity_from_manifest(manifest)

    assert identity is not None
    assert identity["schema_head"] == EXPECTED_SCHEMA_HEAD
    assert identity["schema_contract_format"] == SCHEMA_CONTRACT_FORMAT
    assert len(identity["migration_chain_sha256"]) == 64
    assert len(identity["canonical_postgresql_schema_sha256"]) == 64
    assert len(identity["schema_id"]) == 64


def test_schema_identity_comparison_accepts_the_four_field_tuple():
    from argus.recovery.database import (
        EXPECTED_SCHEMA_HEAD,
        _schema_identity_matches,
        expected_argus_schema_manifest,
        schema_identity_from_manifest,
    )

    identity = schema_identity_from_manifest(
        expected_argus_schema_manifest(EXPECTED_SCHEMA_HEAD)
    )
    tuple_identity = {
        key: identity[key]
        for key in (
            "schema_head",
            "migration_chain_sha256",
            "canonical_postgresql_schema_sha256",
            "schema_contract_format",
        )
    }

    assert _schema_identity_matches(identity, tuple_identity)


def test_schema_identity_tuple_member_changes_digest():
    from argus.recovery.database import EXPECTED_SCHEMA_HEAD, build_schema_identity

    values = {
        "schema_head": EXPECTED_SCHEMA_HEAD,
        "migration_chain_sha256": "a" * 64,
        "canonical_postgresql_schema_sha256": "b" * 64,
        "schema_contract_format": "argus-postgresql-catalog.v1",
    }
    identity = build_schema_identity(**values)
    for key in values:
        changed = dict(values)
        if key.endswith("_sha256"):
            changed[key] = "c" * 64
        elif key == "schema_head":
            changed[key] = "0012_next"
        else:
            changed[key] = "argus-postgresql-catalog.v2"
        assert build_schema_identity(**changed)["schema_id"] != identity["schema_id"]


def test_unknown_migration_head_fails_closed(tmp_path):
    from argus.recovery.database import migration_chain_manifest

    _migration(tmp_path / "0001.py", "0001", None)
    with pytest.raises(RuntimeError, match="not present"):
        migration_chain_manifest("unknown", migrations_path=tmp_path)


def test_migration_chain_hash_includes_reachable_dependency_sources(tmp_path):
    from argus.recovery.database import migration_chain_sha256

    _migration(tmp_path / "0000.py", "0000", None)
    _migration(tmp_path / "0001.py", "0001", None)
    _migration(tmp_path / "0002.py", "0002", "0001")
    (tmp_path / "0003.py").write_text(
        "revision = '0003'\n"
        "down_revision = '0002'\n"
        "depends_on = '0000'\n",
        encoding="utf-8",
    )
    original = migration_chain_sha256("0003", migrations_path=tmp_path)
    (tmp_path / "0000.py").write_text(
        "revision = '0000'\n"
        "down_revision = None\n"
        "depends_on = None\n"
        "# dependency source changed\n",
        encoding="utf-8",
    )
    assert migration_chain_sha256("0003", migrations_path=tmp_path) != original
