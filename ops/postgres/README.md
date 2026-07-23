# Shared PostgreSQL recovery operator toolkit

These artifacts prepare issue #40 without authorizing production changes.
Never run these scripts on the Mac development workstation. Run them only from
an approved homelab operator context after resolving every gate below.

Authentication comes from normal libpq mechanisms such as a root-owned
`PGPASSFILE` or `PGSERVICE`; scripts do not accept passwords or credentialed
database URLs. Database arguments are plain names only; URLs used for import
must omit userinfo and query parameters. PostgreSQL remains private.
`homelab-postgres` is canonical;
validate the temporary `atlas-postgres` compatibility alias before migration.

## Safe sequence

1. Review `provision_shared_postgres.sql`, then execute it twice in a disposable
   PostgreSQL 16 instance and verify tenant isolation.
   Validate naming separately with
   `python3 postgres_recovery.py alias-check --primary homelab-postgres
   --compatibility atlas-postgres`; remove the compatibility alias only after
   Atlas no longer resolves it.
2. Provision credentials outside this repository and apply Alembic migrations
   with the `argus_migration` identity.
3. Run `import_argus_legacy.sh` without `--apply`, review the report, take a
   verified backup, then explicitly apply and repeat the dry run. The rollback
   boundary is before cutover: the legacy source stays read-only and
   authoritative until the post-import report has zero imports/conflicts.
   Before cutover, rollback means dropping only the newly provisioned Argus
   tenant and returning to the untouched legacy source. After cutover, recovery
   requires the verified logical backup or a forward repair—never an automatic
   down migration.
4. Create a dedicated, existing, operator-owned backup directory outside
   PGDATA, then initialize it once:
   `python3 postgres_recovery.py initialize-backup-root --root /backup/argus
   --live-data /var/lib/postgresql/data`. Initialization rejects equal,
   ancestor, descendant, missing, symlinked, or group/world-writable paths and
   writes the ownership marker required by backup and pruning.
5. Schedule `backup_shared_postgres.sh` on the homelab. The destination must be
   covered by separate storage protection. Each set binds dump checksums to
   source schema fingerprints and per-table counts for both tenants. Retention
   considers only owned sets and is the union of 7 daily, 5 weekly, and
   12 monthly sets.
6. Run `verify_restore.sh` with operator-named `argus_restore_*` and
   `atlas_restore_*` targets. It verifies the backup manifest, refuses
   production database names, compares every restored table count and schema
   fingerprint to backup-time source inventories, exercises the Argus
   repository read path, validates Atlas constraints, and always drops both
   validated scratch targets on exit.
7. Mount the evidence JSON read-only into Argus and configure
   `ARGUS_RECOVERY_EVIDENCE_PATH`. Schema-changing promotion fails closed when
   backup or restore evidence is absent, stale, incomplete, or failed.

## Code-complete acceptance

- [x] Provisioning is idempotent in disposable PostgreSQL 16
- [x] Atlas and Argus runtime roles are denied cross-tenant database access
- [x] Backup and restore artifacts contain no role password verifiers
- [x] Backup roots and snapshot sets carry validated ownership markers
- [x] Destructive retention rejects symlink, unowned, and changed targets
- [x] Retention selects 7 daily, 5 weekly, and 12 monthly restore sets
- [x] Restore targets require explicit validated Atlas/Argus scratch names
- [x] Import is dry-run-first, idempotent, and reports before/after counts
- [x] Admin evidence omits paths, scratch names, hosts, users, and credentials
- [x] Evidence is checksum-bound to the current backup and rejects replay
- [x] Source and restored schema/count inventories must match for both tenants
- [x] Schema-changing promotion fails closed on stale evidence

## Production-only acceptance gates

- [ ] Production tenant and role provisioning approved and completed
- [ ] Production `homelab-postgres` identity and `atlas-postgres` alias approved
- [ ] Production import reconciliation approved and completed
- [ ] Production backup schedule approved and enabled
- [ ] Production backup destination verified outside live PGDATA
- [ ] Production isolated restore approved and verified
- [ ] Production schema-promotion evidence reviewed
- [ ] Production cutover approved and completed

This branch intentionally leaves every production-only box unchecked.
