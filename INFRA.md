# Infrastructure

This file contains repository-scoped infrastructure facts.
<!-- janitor:begin:catalog -->
## Repository identity
Infrastructure authority: `Khamel83/infra:config/project-catalog.yml`.
This INFRA.md is this project's projection of that authority. The owning
project declares its deploy, config, data and health evidence here;
shared machine/tool/environment policy stays in infra's registries.
- project_id: argus
- name: Argus
- state: managed
- lifecycle: active
- classification: unclassified
- repository: https://github.com/Khamel83/argus
<!-- janitor:end:catalog -->

## Shared PostgreSQL backup runtime — October 20, 2026 UTC

The project-owned backup tool is sealed under the Homelab root-owned namespace
`/opt/argus-backup-tooling/`. Its `current` link points to the immutable release
for source `66365a661abcfa6ccb4a10dfe5e4b251eaf339ff`; the prior `175a7a39`
release remains the rollback anchor. The installed producer
entry point is `/usr/local/libexec/argus-backup/backup-argus-pg.sh`, called
through the existing `with-duration` wrapper. The producer reuses the existing
primary virtual environment and compares its dependency lock before running; it
does not auto-upgrade source or dependencies.
The current capsule contains 225 files totaling 4,715,988 bytes with manifest
`7dc33f100a0a2189967ada284f98b6a45b2e9eaa906fa0829498b3014cc3b056`; its
private installation receipt SHA is
`13c922258b60f62161f1bcadcff99c7f7e0a6934c191fa231e1a8af11e2c52e4`.

The protected Homelab cron schedule remains daily at 01:45 UTC with job ID
`backup.argus.daily`, its existing log path and its existing receipt path. The
fixed wrapper and the one-row CAS update preserve those identities. A bounded
corrected ordinary-user run completed 03:28:54–03:32:07 UTC in 193 seconds
with an 82.984 CPU-second charge, 768 MiB peak and zero swap. It published
schema-2 set `20261020T032858Z` with exported-snapshot provenance for both
tenants. This is functional runtime evidence, not proof of a future natural
recurrence.

The backup producer source identity is separate from the Argus application
source and image. The temporary isolated restore reader uses the repaired source
`66365a661abcfa6ccb4a10dfe5e4b251eaf339ff` and restore-only image
`sha256:01f59e0040f12ff1c51b1636e089b99396685b1469a29a482e66408a3406e3c3`;
it is not an application deployment or cutover. The prior second restore
of the previous set `20261020T023056Z` failed at 02:52:27 UTC after the
independent observer matched both database
table sets, row counts and constraints; Argus's schema matched, while Atlas
had a function-order-only schema-fingerprint mismatch. PR #213 supplies the
bounded canonicalization repair. The fresh strict isolated restore then passed
integrity, row counts, schema, the Argus read path and migration compatibility
at 03:34:47 UTC against the matching backup identity. This is not an
application deployment or cutover; `metadata_registry_complete`,
`schema_contract_clean`, `forward_compatible` and
`rollback_path_human_approved` remain false. See
[`docs/evidence/2026-10-20-argus-backup-snapshot-acceptance.md`](docs/evidence/2026-10-20-argus-backup-snapshot-acceptance.md)
for the source, receipts and acceptance boundary.


## Security reporting configuration — October 21, 2026

GitHub private vulnerability reporting was disabled when the worker report was
received. It is now enabled on `Khamel83/argus`; independent API readback returned
`enabled=true` (`gh api repos/Khamel83/argus/private-vulnerability-reporting`).
No application runtime or credential location changed in this configuration step.

## Glama catalog configuration — October 22, 2026

Glama's Argus adapter listing requires only `ARGUS_AUTHORITY_URL` and
`ARGUS_AUTHORITY_TOKEN`; actual users supply their own reachable authority and
scoped credential. Saved test values are deliberately unusable discovery
placeholders. Python 3.12/frozen-lock build settings and the normalized environment
schema are recorded under `deploy/glama-*.json`. A successful native test and
Glama 1.6.5 publication were independently read back; see the dated Glama evidence.
No real authority/provider credentials, private networking grant or production
HTTP deployment changed.
