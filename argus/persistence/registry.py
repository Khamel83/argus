"""Canonical SQLAlchemy metadata for the production persistence authority.

The project still has a legacy ``argus.persistence.models.Base`` for the
standalone SQLite workflow.  That base is deliberately not copied into
``production_metadata``.  Production metadata is assembled from the runtime
ledger, evidence, spend, and readiness bases in one place so Alembic and
schema admission cannot silently select only one of those bases.
"""

from __future__ import annotations

from importlib import import_module
from typing import Any

from sqlalchemy import MetaData


# These are the migration revisions that introduced the currently supported
# production tables.  Keeping the mapping explicit makes the inventory useful
# to migration and recovery tooling without inspecting migration source at
# import time.
_LEDGER_REVISION = "0001_search_ledger"
_OPERATION_LEDGER_REVISION = "0004_operation_ledger"
_SPEND_REVISION = "0005_provider_spend"
_OUTCOME_REVISION = "0007_extraction_outcomes"
_READINESS_REVISION = "0008_provider_readiness"
_EVIDENCE_REVISION = "0009_retrieval_evidence"
_DOMAIN_POLICY_REVISION = "0010_domain_policies"

_LEDGER_TABLES = frozenset(
    {
        "retrieval_requests",
        "retrieval_runs",
        "provider_attempts",
        "content_identities",
        "normalized_results",
        "result_provenance",
        "delivery_intents",
    }
)
_OPERATION_LEDGER_TABLES = frozenset(
    {
        "extraction_runs",
        "extractor_attempts",
        "extraction_artifacts",
        "retrieval_sessions",
        "session_queries",
        "session_extracted_urls",
    }
)
_OUTCOME_TABLES = frozenset(
    {
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
    }
)
_SPEND_TABLES = frozenset(
    {
        "provider_spend_attempts",
        "provider_balance_snapshots",
        "provider_spend_audit",
    }
)
_READINESS_TABLES = frozenset(
    {
        "provider_readiness_observations",
        "provider_readiness_snapshots",
        "provider_readiness_evidence_refs",
        "provider_readiness_leases",
        "provider_readiness_alert_dedupe",
    }
)
_EVIDENCE_TABLES = frozenset(
    {
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
)

_TABLE_REVISIONS = {
    **{table: _LEDGER_REVISION for table in _LEDGER_TABLES},
    **{table: _OPERATION_LEDGER_REVISION for table in _OPERATION_LEDGER_TABLES},
    **{table: _OUTCOME_REVISION for table in _OUTCOME_TABLES},
    **{table: _SPEND_REVISION for table in _SPEND_TABLES},
    **{table: _READINESS_REVISION for table in _READINESS_TABLES},
    **{table: _EVIDENCE_REVISION for table in _EVIDENCE_TABLES},
}

_TABLE_OWNERS = {
    **{table: "argus.persistence.search_ledger" for table in _LEDGER_TABLES},
    **{table: "argus.persistence.search_ledger" for table in _OPERATION_LEDGER_TABLES},
    **{table: "argus.persistence.search_ledger" for table in _OUTCOME_TABLES},
    **{table: "argus.persistence.provider_spend" for table in _SPEND_TABLES},
    **{table: "argus.persistence.readiness" for table in _READINESS_TABLES},
    **{table: "argus.persistence.evidence" for table in _EVIDENCE_TABLES},
}

# Every current canonical persistence table is runtime-critical.  A future
# optional canonical domain-policy declaration is added to this set when its
# module is available; until then the old domain-policy row remains
# legacy-only and no production table is invented by this module.
runtime_critical_tables = frozenset(_TABLE_REVISIONS)

# These tables were previously omitted when Alembic loaded only LedgerBase.
# Keep the set explicit so a future edit cannot silently reintroduce a
# destructive autogenerate diff for spend, readiness, or evidence state.
protected_runtime_tables = frozenset(
    _SPEND_TABLES | _READINESS_TABLES | _EVIDENCE_TABLES
)


def _canonical_domain_policy_tables() -> tuple[tuple[Any, str], ...]:
    """Return separately declared canonical domain-policy tables if present.

    Phase 1 is intentionally usable before the reviewed domain-policy module
    lands.  Importing it lazily keeps this foundation compatible with that
    later module while retaining the current legacy-only boundary.
    """

    try:
        module = import_module("argus.persistence.domain_policy")
    except ModuleNotFoundError as exc:
        if exc.name == "argus.persistence.domain_policy":
            return ()
        raise

    tables: list[tuple[Any, str]] = []
    seen: set[str] = set()
    for name in ("DomainPolicyRow", "CanonicalDomainPolicyRow", "DomainPolicyEvent"):
        model = getattr(module, name, None)
        table = getattr(model, "__table__", None)
        if table is None or table.name in seen:
            continue
        tables.append((table, "argus.persistence.domain_policy"))
        seen.add(table.name)
    if not tables:
        raise RuntimeError(
            "argus.persistence.domain_policy must expose canonical policy tables"
        )
    return tuple(tables)


def _source_metadata() -> tuple[MetaData, ...]:
    """Load every current production model base before copying its tables."""

    # Importing evidence is important: its rows share LedgerBase but are not
    # imported by search_ledger itself.
    from argus.persistence.evidence import LedgerBase
    from argus.persistence.provider_spend import SpendBase
    from argus.persistence.readiness import ReadinessBase

    return LedgerBase.metadata, SpendBase.metadata, ReadinessBase.metadata


def _legacy_model_inventory() -> list[dict[str, object]]:
    from argus.persistence.models import Base

    return [
        {
            "table_name": table.name,
            "owner_module": "argus.persistence.models",
            "migration_revision": None,
            "compatibility_class": "legacy-only",
            "runtime_critical": False,
        }
        for table in sorted(Base.metadata.tables.values(), key=lambda item: item.name)
    ]


class ProductionMetadataRegistry:
    """One metadata authority and ownership inventory for production SQL."""

    def __init__(self) -> None:
        metadata = MetaData()
        table_revisions = dict(_TABLE_REVISIONS)
        table_owners = dict(_TABLE_OWNERS)
        critical_tables = set(runtime_critical_tables)
        for source in _source_metadata():
            for table in source.tables.values():
                table.to_metadata(metadata)

        for table, owner in _canonical_domain_policy_tables():
            table.to_metadata(metadata)
            table_revisions.setdefault(table.name, _DOMAIN_POLICY_REVISION)
            table_owners.setdefault(table.name, owner)
            critical_tables.add(table.name)

        source_tables = set(metadata.tables)
        missing_revision = source_tables - set(table_revisions)
        if missing_revision:
            raise RuntimeError(
                "canonical metadata tables have no migration revision: "
                + ", ".join(sorted(missing_revision))
            )
        missing_owner = source_tables - set(table_owners)
        if missing_owner:
            raise RuntimeError(
                "canonical metadata tables have no owner: "
                + ", ".join(sorted(missing_owner))
            )
        missing_protected = protected_runtime_tables - source_tables
        if missing_protected:
            raise RuntimeError(
                "protected production tables are absent from canonical metadata: "
                + ", ".join(sorted(missing_protected))
            )

        self.metadata = metadata
        self.runtime_critical_tables = frozenset(critical_tables & source_tables)
        production_inventory = [
            {
                "table_name": table.name,
                "owner_module": table_owners[table.name],
                "migration_revision": table_revisions[table.name],
                "compatibility_class": "production",
                "runtime_critical": table.name in self.runtime_critical_tables,
            }
            for table in sorted(metadata.tables.values(), key=lambda item: item.name)
        ]
        self.production_model_inventory = tuple(
            sorted(
                production_inventory + _legacy_model_inventory(),
                key=lambda item: (item["table_name"], item["compatibility_class"]),
            )
        )

    @property
    def model_inventory(self) -> tuple[dict[str, object], ...]:
        """Return the complete production and explicit legacy-only inventory."""

        return self.production_model_inventory

    @property
    def production_tables(self) -> frozenset[str]:
        return frozenset(self.metadata.tables)

    @property
    def legacy_tables(self) -> frozenset[str]:
        return frozenset(
            item["table_name"]
            for item in self.production_model_inventory
            if item["compatibility_class"] == "legacy-only"
        )


_registry = ProductionMetadataRegistry()
production_metadata = _registry.metadata
production_model_inventory = _registry.production_model_inventory


__all__ = [
    "ProductionMetadataRegistry",
    "production_metadata",
    "production_model_inventory",
    "protected_runtime_tables",
    "runtime_critical_tables",
]
