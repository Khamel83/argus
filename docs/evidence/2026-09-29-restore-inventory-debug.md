# Argus shared PostgreSQL restore inventory — 2026-09-29

## Symptom

An admitted Argus image passed candidate gates. The guarded Homelab promotion
stopped before cutover at `recovery-proof_failed`.

## Expected vs actual

- Expected: the latest immutable shared PostgreSQL backup restores into two
  disposable databases and matches its recorded source inventories.
- Actual: archive checksums and both table-count inventories passed. Atlas
  schema SHA differed after restore; Argus schema SHA matched.

```text
atlas.dump: OK
argus.dump: OK
globals.sql: OK
error: restored database does not match source inventory
```

## Root cause

The `20260929T014506Z` backup restored into disposable Argus and Atlas
databases. Source and restored Atlas had 34 tables, 335 columns, 109
constraints, 103 indexes, and 46 functions. Only two `dispatch_receipts`
CHECK constraint definition strings differed. PostgreSQL moved an equivalent
varchar-to-text cast from the whole literal `ANY` array onto each literal
during dump/restore. The raw text hash treated that equivalent deparse as a
schema difference. Both disposable databases were dropped after diagnosis.

## Repair and proof

Normalize only literal-only `CHECK ... ANY` text-array casts before hashing
the inventory. Keep all other schema fields and definitions in the hash.
Take a new immutable backup with the corrected fingerprint, verify it from a
disposable restore, and use the guarded promotion path. Existing manifests
remain unchanged. A changed literal, count, column, other constraint, index,
or function must still change the hash.

Focused recovery tests: 85 passed, 20 skipped under Python 3.12; Ruff `F`
checks and `git diff --check` passed. The full Ruff profile has unrelated
pre-existing findings in these files; it was not made a new promotion gate.
