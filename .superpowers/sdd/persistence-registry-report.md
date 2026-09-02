# Persistence registry implementation report

## Scope and result

Implemented Phase 1's canonical metadata-registry foundation only. The
production metadata now combines the 23 retrieval/operation/outcome ledger
tables, 12 retrieval-evidence tables, 3 provider-spend tables, and 5
provider-readiness tables. The 13 rows declared by the legacy
`argus.persistence.models.Base` remain visible in the inventory as
`legacy-only` and are not copied into production metadata. No migration,
database, deployment, provider, or legacy standalone write path was changed.

## Changed files

- `argus/persistence/registry.py`
  - Added `ProductionMetadataRegistry`.
  - Added module-level `production_metadata` and
    `production_model_inventory`.
  - Imports all current production metadata bases, including evidence rows
    that share `LedgerBase`.
  - Provides deterministic table sorting, explicit migration revisions and
    owner modules, runtime-critical flags, protected spend/readiness/evidence
    table checks, and the explicit legacy-only inventory.
  - Includes a lazy compatibility hook for the separately delivered canonical
    domain-policy module. Until that module exists, the old `DomainPolicyRow`
    is legacy-only and no `domain_policies` table is added to production
    metadata.
- `migrations/env.py`
  - Sets Alembic `target_metadata` to `production_metadata`.
- `tests/test_metadata_registry.py`
  - Added focused coverage for deterministic sorted inventory, unique
    production ownership, runtime-critical registration, all 13 legacy rows,
    Alembic spend/readiness/evidence removal protection, and Alembic wiring.

## Requirements mapped to tests

| Requirement | Test |
|---|---|
| Complete deterministic sorted inventory | `test_production_inventory_is_complete_deterministic_and_sorted` |
| One owner per production table | `test_each_production_table_has_one_owner_and_revision` |
| Runtime-critical coverage | `test_every_runtime_critical_table_is_registered` |
| Legacy-only declarations excluded from production metadata | `test_legacy_base_rows_are_inventory_only_and_excluded_from_production_metadata` |
| Every `Base` row has an explicit inventory decision | `test_legacy_base_rows_are_inventory_only_and_excluded_from_production_metadata` |
| Spend/readiness/evidence tables are not proposed for removal | `test_alembic_comparison_does_not_propose_removing_spend_readiness_or_evidence` |
| Alembic uses the canonical registry | `test_alembic_env_uses_canonical_production_metadata` |

## Verification

Commands were run from
`/Users/macmini/.codex/worktrees/argus-persistence-foundation`:

| Command | Exit | Result |
|---|---:|---|
| `/Library/Frameworks/Python.framework/Versions/3.13/bin/pytest tests/test_metadata_registry.py -q` (before implementation) | 1 | Expected RED: 6 failures because the registry and Alembic wiring were absent. |
| `/Library/Frameworks/Python.framework/Versions/3.13/bin/pytest tests/test_metadata_registry.py -q` | 0 | 6 passed. |
| `/Library/Frameworks/Python.framework/Versions/3.13/bin/pytest tests/test_metadata_registry.py tests/test_persistence.py tests/test_recovery_database.py tests/test_schema_contract_generator.py -q` | 0 | 58 passed, 4 skipped. |
| `git diff --check` | 0 | Clean. |
| `/Library/Frameworks/Python.framework/Versions/3.13/bin/pytest -q` | 1 | Collection stopped at two pre-existing optional-MCP failures: `ModuleNotFoundError: No module named 'mcp'` in `tests/test_mcp_pack_tools.py` and `tests/test_mcp_research_report.py`. |

## Commit

- Implementation commit: `8f34e4c47f6b44b9f01d46e9e2914741bc5aef19`
  (`test: define canonical production metadata registry`)

## Concerns

- The reviewed domain-policy ORM module and its approved `0010` migration are
  intentionally outside this foundation commit. The legacy domain-policy row
  therefore remains inventory-only until that later phase supplies the
  canonical declaration and migration.
- The full suite requires the optional `mcp` dependency; the focused and
  relevant persistence/recovery/Alembic tests pass without it.
