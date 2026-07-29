# Task 16 recovery inventory projection fix

## Scope

Fixed the confirmed restore-verification mismatch only. A verified backup
manifest intentionally retains the sanitized inventory projection
`tables`, `schema_sha256`, and `constraints_validated`; a restored database
rebuilds that projection plus internal constraint and index details.

## TDD evidence

RED:

```sh
uv run pytest -q tests/test_recovery_database.py -k manifest_projection
```

Result: failed as expected with `RuntimeError: restored database does not
match source inventory` when the exact sanitized projection was supplied to
`verify_restored_source_inventory`.

GREEN:

```sh
uv run pytest -q tests/test_recovery_database.py -k manifest_projection
```

Result: `1 passed, 51 deselected`.

Broader recovery verification:

```sh
uv run pytest -q tests/test_recovery_database.py tests/test_recovery_records.py tests/test_recovery_policy.py tests/test_postgres_recovery_cli.py tests/test_recovery_import.py tests/test_postgres_recovery_artifacts.py
uv run ruff check argus/recovery/database.py tests/test_recovery_database.py
git diff --check
```

Result: `97 passed, 20 skipped`; Ruff reported `All checks passed!`; the
whitespace check was clean. The skips are pre-existing environment-gated
PostgreSQL coverage, not test failures.

## Change

- `argus/recovery/database.py`: `_compare_inventory` now accepts an expected
  value only when it exactly equals the actual sanitized manifest projection;
  otherwise it retains exact full-inventory comparison.
- `tests/test_recovery_database.py`: regression covers accepted exact
  projection and rejection of count drift, schema drift, malformed projection
  data, unknown or missing projection keys, and a changed full-inventory index
  payload.

## Commit

Implementation commit: `9b4417b6e7e8fb4b6a59ae978aac7501babd89ca`
(`fix: accept sanitized recovery inventory projections`).

## Risks and boundary

The exception is deliberately limited to a value exactly matching the three
manifest fields reconstructed from the restored inventory. Any malformed,
partial, expanded, or drifted projection remains rejected, as do all unequal
full inventories. No live database, backup artifact, Homelab checkout, or
production configuration was changed.
