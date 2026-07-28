# Task 7 / S6 report

Status: implementation and hermetic verification complete; the PostgreSQL
recovery-manifest regeneration remains an explicit CI/approved-infrastructure
gate. S6 is still inactive in the production router pending S7/P1.

## Implemented

- Added accepted retrieval facts, outcome classification, immutable
  fingerprint/cohort cache entries, deep-copy cache-hit projections, and
  persistence-before-publication ordering.
- Added normalized retrieval-evidence storage with operation/receipt and cache
  publication uniqueness plus complete contributor attempt lineage.
- Added Alembic 0009 with additive evidence tables and downgrade refusal when
  accepted evidence exists. A pre-activation downgrade removes only 0009
  objects.
- Preserved legacy `SearchCache` and the live router path. The S6 cache can be
  supplied for inspection but is not read or published by `SearchBroker`.
- Updated packaged migration and schema-head expectations. Recovery continues
  to accept only checked 0007/0008 manifests until a correct PostgreSQL-derived
  0009 manifest is regenerated in CI/approved infrastructure.

## Verification

- RED recorded before implementation:
  `ModuleNotFoundError: argus.broker.accepted`, then
  `ModuleNotFoundError: argus.persistence.evidence`, then missing migration.
- `uv run pytest tests/test_accepted_retrieval.py -q` — passed.
- `uv run pytest tests/test_accepted_retrieval.py tests/test_search_ledger.py
  tests/test_broker.py tests/test_spend_boundaries.py -q` — passed.
- `uv run pytest tests/test_recovery_database.py
  tests/test_distribution_artifacts.py tests/test_accepted_retrieval.py -q` —
  68 passed, 4 skipped.
- `uv run ruff check` on all changed Python files — passed.
- Hermetic SQLite Alembic upgrade to 0009 and pre-activation downgrade to 0008
  — passed.
- `git diff --check` — passed.

## CI gate

`argus/recovery/argus_schema_0009.json` must be generated from a disposable,
Alembic-migrated PostgreSQL database and checked against the exact server
contract before adding 0009 to recovery-compatible manifests. No Docker,
PostgreSQL service, homelab, deployment, paid provider, secret, or production
database action was used for this task.
