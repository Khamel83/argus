from __future__ import annotations

import importlib.util
from pathlib import Path

from sqlalchemy import inspect


ROOT = Path(__file__).parents[1]


def _migration_module():
    path = ROOT / "migrations" / "versions" / "0010_domain_policies.py"
    spec = importlib.util.spec_from_file_location("migration_0010", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_0010_is_the_reviewed_linear_domain_policy_revision():
    migration = _migration_module()

    assert migration.revision == "0010_domain_policies"
    assert migration.down_revision == "0009_retrieval_evidence"


def test_canonical_domain_policy_metadata_contains_current_and_event_tables():
    from argus.persistence.domain_policy import DomainPolicyBase

    assert set(DomainPolicyBase.metadata.tables) == {
        "domain_policies",
        "domain_policy_events",
    }
    policy_columns = set(
        DomainPolicyBase.metadata.tables["domain_policies"].columns.keys()
    )
    assert {
        "domain",
        "prefer_residential_search",
        "prefer_residential_extraction",
        "datacenter_failure_count",
        "residential_success_count",
        "last_datacenter_failure",
        "last_residential_success",
        "failure_reason",
        "updated_at",
        "version",
    } <= policy_columns
    event_columns = set(
        DomainPolicyBase.metadata.tables["domain_policy_events"].columns.keys()
    )
    assert {
        "event_identity",
        "request_hash",
        "domain",
        "event_type",
        "result_json",
        "created_at",
    } <= event_columns


def test_sqlite_standalone_schema_contains_canonical_domain_policy_tables(tmp_path):
    from argus.persistence.domain_policy import create_domain_policy_repository

    repository = create_domain_policy_repository(f"sqlite:///{tmp_path / 'policy.db'}")
    engine = repository.session_factory.kw["bind"]
    assert {
        "domain_policies",
        "domain_policy_events",
    } <= set(inspect(engine).get_table_names())


def test_legacy_sqlite_initializer_creates_canonical_policy_columns(tmp_path):
    from argus.persistence import db

    db.init_db(f"sqlite:///{tmp_path / 'legacy-init.db'}")
    columns = {
        column["name"]
        for column in inspect(db.get_engine()).get_columns("domain_policies")
    }
    assert {"version", "updated_at"} <= columns
    assert "domain_policy_events" in inspect(db.get_engine()).get_table_names()
