from __future__ import annotations

from collections import Counter

from alembic.migration import MigrationContext
from alembic.autogenerate import compare_metadata
from sqlalchemy import MetaData, create_engine


LEGACY_TABLES = {
    "search_queries",
    "search_runs",
    "search_results",
    "provider_usage",
    "search_evidence",
    "corpus_sources",
    "corpus_snapshots",
    "workflow_runs",
    "crawl_runs",
    "corpus_documents",
    "workflow_artifacts",
    "workflow_citations",
    "domain_policies",
}


def _canonical_source_metadata() -> MetaData:
    """Copy every current canonical source base into one comparison target."""
    from argus.persistence.evidence import LedgerBase
    from argus.persistence.provider_spend import SpendBase
    from argus.persistence.readiness import ReadinessBase

    metadata = MetaData()
    for source in (LedgerBase.metadata, SpendBase.metadata, ReadinessBase.metadata):
        for table in source.tables.values():
            table.to_metadata(metadata)
    return metadata


def test_production_inventory_is_complete_deterministic_and_sorted():
    from argus.persistence.registry import production_model_inventory, production_metadata

    assert production_model_inventory == tuple(
        sorted(production_model_inventory, key=lambda item: item["table_name"])
    )
    assert production_model_inventory
    assert {
        "table_name",
        "owner_module",
        "migration_revision",
        "compatibility_class",
        "runtime_critical",
    } == set(production_model_inventory[0])
    production_items = [
        item
        for item in production_model_inventory
        if item["compatibility_class"] == "production"
    ]
    assert {item["table_name"] for item in production_items} == set(
        production_metadata.tables
    )
    assert len(production_items) == len(production_metadata.tables)


def test_each_production_table_has_one_owner_and_revision():
    from argus.persistence.registry import production_model_inventory

    production_items = [
        item
        for item in production_model_inventory
        if item["compatibility_class"] == "production"
    ]
    owners = Counter(item["table_name"] for item in production_items)
    assert owners
    assert all(count == 1 for count in owners.values())
    assert all(item["owner_module"] for item in production_items)
    assert all(item["migration_revision"] for item in production_items)


def test_every_runtime_critical_table_is_registered():
    from argus.persistence.registry import (
        production_model_inventory,
        runtime_critical_tables,
    )

    registered = {
        item["table_name"]
        for item in production_model_inventory
        if item["runtime_critical"]
        and item["compatibility_class"] == "production"
    }
    assert runtime_critical_tables <= registered


def test_legacy_base_rows_are_inventory_only_and_excluded_from_production_metadata():
    from argus.persistence.models import Base
    from argus.persistence.registry import production_model_inventory, production_metadata

    inventory_by_table = {
        item["table_name"]: item for item in production_model_inventory
    }
    assert set(Base.metadata.tables) == LEGACY_TABLES
    assert set(inventory_by_table) >= LEGACY_TABLES
    assert sum(
        item["compatibility_class"] == "legacy-only"
        for item in production_model_inventory
    ) == len(LEGACY_TABLES)
    for table_name in LEGACY_TABLES:
        item = inventory_by_table[table_name]
        assert item["compatibility_class"] == "legacy-only"
        assert item["runtime_critical"] is False
        assert table_name not in production_metadata.tables


def test_alembic_comparison_does_not_propose_removing_spend_readiness_or_evidence():
    from argus.persistence.registry import production_metadata

    engine = create_engine("sqlite://")
    source_metadata = _canonical_source_metadata()
    source_metadata.create_all(engine)
    context = MigrationContext.configure(engine.connect())

    diffs = compare_metadata(context, production_metadata)

    removed_tables = {
        diff[1].name
        for diff in diffs
        if diff and diff[0] == "remove_table"
    }
    protected_tables = {
        "provider_spend_attempts",
        "provider_balance_snapshots",
        "provider_spend_audit",
        "provider_readiness_observations",
        "provider_readiness_snapshots",
        "provider_readiness_evidence_refs",
        "provider_readiness_leases",
        "provider_readiness_alert_dedupe",
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
    assert not removed_tables & protected_tables


def test_alembic_env_uses_canonical_production_metadata():
    from pathlib import Path

    source = Path(__file__).parents[1].joinpath("migrations", "env.py").read_text()
    assert "from argus.persistence.registry import production_metadata" in source
    assert "target_metadata = production_metadata" in source
