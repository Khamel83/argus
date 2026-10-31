## Scorecard admission #154 — source reconciliation, October 31, 2026

- [x] Reconcile the retained candidates with the existing Infra-owned host guard;
  verify its current admission and runner contracts: 80 isolated tests passed.
  [Immutable source and criterion map](docs/evidence/2026-10-31-scorecard-admission-reconciliation.md).
- [ ] Publish this documentation continuation through required native CI,
  exact-head trusted review and normal merge. Native worker ownership is unknown.
- [ ] Infra/Homelab operator: verify installed source/config parity and complete
  the existing bounded real-path five-second Baywatch/cleanup qualification.
  [#154](https://github.com/Khamel83/argus/issues/154) and
  [#151](https://github.com/Khamel83/argus/issues/151) remain open; do not install
  a retained parallel coordinator or infer runtime acceptance from source tests.

## Glama catalog startup follow-up — October 22, 2026

- [x] Reproduce the reported catalog startup failure, correct the saved adapter
  settings, and publish Glama 1.6.5 after a passing native discovery test.
  [Successful test](https://glama.ai/mcp/servers/Khamel83/argus/admin/dockerfile/tests/01a1650d-da73-725e-926d-0b3cabecf580).
- [x] Merge the checked Glama settings, corrected metadata and release-contract version guard after trusted review and native CI. https://github.com/Khamel83/argus/pull/221 Done: https://github.com/Khamel83/argus/pull/221

## Legacy worker security patch — October 22, 2026

- [x] Confirm and repair missing-secret authentication bypass, default to loopback,
  require explicit development opt-in and preserve paid-provider denial.
  [PR217](https://github.com/Khamel83/argus/pull/217), trusted exact-head PASS and
  [all eight native checks](https://github.com/Khamel83/argus/actions/runs/39737027592).
- [x] Enable private vulnerability reporting and verify GitHub `enabled=true`.
- [x] Publish1.6.5 to PyPI/MCP Registry and verify the installed PyPI package rejects
  the reported PoC. [Release](https://github.com/Khamel83/argus/releases/tag/v1.6.5),
  [successful publication](https://github.com/Khamel83/argus/actions/runs/39737816197).
- [x] Send the verified release acknowledgment with reporter credit and request
  disclosure coordination; independent original-thread readback confirms SENT.

## Shared PostgreSQL backup consistency — October 20 UTC

- [x] Preserve the completed isolated-restore migration opt-in and the bounded
  Infra verifier. The first real drills cleaned up exactly and left canonical
  evidence unchanged when they refused ENOSPC and then an inventory mismatch.
- [x] Prepare manifest v2 and per-database exported-snapshot capture so each
  dump and its source inventory use the same PostgreSQL snapshot and bind the
  tenant, method and exact archive SHA. Legacy v1 sets remain readable but are
  explicitly unproven; old manifests are never rewritten.
- [x] Require exact-head review and native PostgreSQL CI for the concurrent
  writer proof. PR [209](https://github.com/Khamel83/argus/pull/209) merged as
  `175a7a39aefdc43e3c08a1885974f571bef0cccd`; trusted review
  `5580007693` and required CI run `39357846393` passed. The native PostgreSQL
  ledger completed 323 tests, including the concurrent-writer snapshot test.
- [x] Adopt the reviewed source through the separately guarded immutable
  backup-tool capsule. The previous `175a7a39` source remains a rollback anchor;
  the current capsule is source `66365a661abcfa6ccb4a10dfe5e4b251eaf339ff`
  with 225 files and its sealed manifest. The fixed Infra wrapper from merge
  `13c7f97` is installed and the protected 01:45 cron row remains unchanged.
- [x] Complete the first fixed producer run. The bounded manual run completed
  02:30:52–02:34:20 UTC, published schema-2 set `20261020T023056Z`, and bound
  both tenants to their exported-snapshot provenance. This was a functional
  run, not a natural recurrence.
- [x] Publish and validate the restore-reader v2 projection repair. PR
  [211](https://github.com/Khamel83/argus/pull/211) merged as
  `e7ee75a9193615beb12137467aa9b4104164fdb0`; trusted review `5580171742`,
  required CI `39360416459`, and the native 323-test ledger passed. The repair
  strips only the known, already-validated `snapshot_provenance` metadata key;
  unknown fields and count/schema/constraint drift remain rejected.
- [x] Canonicalize the inventory function order found by the second restore.
  PR [213](https://github.com/Khamel83/argus/pull/213) merged as
  `66365a661abcfa6ccb4a10dfe5e4b251eaf339ff`; trusted review `5580366828`,
  required CI `39363477632`, and the native 324-test ledger passed. The
  diagnosis matched all 36 tables, 350 columns, 115 constraints, 106 indexes
  and 46 functions; only Atlas function ordering differed.
- [x] Complete the matching bounded isolated restore and downstream readback.
  The first strict restore of the previous set `20261020T023056Z` failed at
  02:36:52 UTC; its inner log was not retained, cleanup completed and
  canonical state stayed unchanged. A subsequent bounded reproduction
  identified the reader projection defect. The
  second attempt of that same set failed at 02:52:27 UTC after the independent observer matched both database
  table sets, row counts and constraints; Argus schema matched, but Atlas had a
  function-order-only schema-fingerprint mismatch. A fresh source-`66365a6`
  producer run published schema-2 set `20261020T032858Z`; the strict isolated
  restore verified integrity, row counts, schema, the Argus read path and
  migration compatibility at 03:34:47 UTC. Its source and backup identity
  matched. This is a bounded manual acceptance, not a natural 01:45 recurrence.

The first test of the new installed producer was deliberately run as root and
failed its backup-root identity preflight before any dump was made. Its failure
receipt and logs are retained. The corrected test runs as ordinary
  `khamel83` with the same caps; do not count the root preflight failure as a
  backup attempt. The corrected run and strict restore are now accepted under
  the bounded gates above; the root preflight remains historical evidence.

## Public share extraction / issue159


# Argus operational follow-up

## Now

Fleet limit: 10 items. Only the owner adds here ([rules](https://github.com/Khamel83/engage/blob/main/docs/design/2026-10-31-backlog-reset.md)).


## Someday

Dated lines; unpromoted lines archive after 30 days.

Archived October 31, 2026: [backlog](docs/archive/backlog-2026-10-31.md).

## Issue177/179 development environment

- [x] Accept the retained mise/uv foundation through [PR198](https://github.com/Khamel83/argus/pull/198):
  normal merge `c1016bf`, exact trusted PASS and all eight native jobs. Actual
  3.11/3.12/3.13 lanes each completed2995 tests/31 skips; locks and rollback preserved.
- [x] Reassess nonexistent generic lint/typecheck/format/clean wrappers explicitly:
  no tools/no-ops invented; the separate existing governance TODO remains open.
- [x] Complete native [179](https://github.com/Khamel83/argus/issues/179#issuecomment-6227508234)
  and [177](https://github.com/Khamel83/argus/issues/177#issuecomment-6227511571)
  at00:44:30/00:44:43 UTC, October17. Independent readback verified acceptance,
  unchanged bodies/labels and22 retained state/link identities; no attempt,
  grant, retained candidate or stop label was reset.

## Homelab H26 — bounded credential observation

The former generated projection is preserved byte for byte with its expanded
PR173/source notes in [the publisher prerequisite evidence](docs/evidence/2026-10-10-publisher-todo-pointer.md).
Source eligibility does not close the release or natural-consumer gates below.

- [x] Implement and independently review the Tavily-only opt-in observer,
  including filesystem/input and integer-attempt admission regressions.
  69 combined observer/configuration/API lifecycle tests and focused Ruff pass.
- [ ] Homelab separately owns protected source binding, credential-free reader
  acceptance, durable health-log admission and notification evidence. No raw
  credential is exported and overall readiness remains unknown.
  [Contract](docs/operations/tavily-credential-observation.md).

The remaining Argus application backlog below is separate from this scoped
Homelab credential-health change.

- [x] Repair PR #166's terminal spend test clock race. Local Python 3.12
  provider-spend tests: 56 passed, 6 skipped.
- [x] Confirm required CI on source/test commit `b8642be`: all eight checks
  passed, including Python 3.11, 3.12, and 3.13. This test-only change has no
  deployment or provider-effect claim.

- [x] Retire the old `oci-ts` AI Review workflow. PR #163 merged as `bdca7fb`.
  Keep public/fork CI on GitHub-hosted runners. Its separate image failed
  exact scorecard admission; issue triage from the old workflow has no
  replacement proven here.

Updated September 7, 2026. The current dated capability evidence belongs in
[public status](docs/STATUS.md); the private audit preserves raw receipts.
A checked source task does not imply every provider has passed a live call.

- [x] Bound authenticated admin provider canaries to one uncached result and one consumed authorization, with no fallback.
- [x] Restore canonical vault projection, Wolfram naming, explicit account registration and retained scoped caller/Maya configuration.
- [x] Repair native HTTP POST framing and test actual socket bytes.
- [x] Bind new paid/free probe spend records to the validated baked release identity.
- [x] Repair DDG's framing ownership and credential-free redirect policy without weakening public-address checks.
- [x] Verify disposable PostgreSQL migrations, all production metadata, no drift and canonical restore.
- [x] Repair temporary scorecard network allocation, evidence schema and current accepted-operation promotion accounting.
- [x] Complete authenticated MCP, extraction and one-attempt Maya capture checks on the corrected image.
- [x] Finish the automatic corrected-image promotion soak and restore Valyu disablement.
- [x] Correct extraction release configuration/binding and preserve full source URL; existing receipts remain unchanged. Done (model-matched): https://github.com/Khamel83/argus/pull/138
- [ ] Reconcile uncertain provider charges only with authoritative provider evidence. Never convert an HTTP rejection into assumed zero spend.

SearchAPI has no configured key. Valyu must remain account-blocked if it rejects
the bounded request; this checklist does not authorize billing changes, quota
resets, credential rotation or repeated provider tests.
## Publisher source prerequisite — October 10, 2026

- [x] Reconcile the managed TODO pointer after G2K High; original generated
  bytes and all task intents are preserved in the dated evidence above.

<!-- janitor:begin:todo -->
The project-owned task list in this TODO.md is authoritative. Janitor does not duplicate its checkboxes.
<!-- janitor:end:todo -->

## Owning repository plan

Use [the ordered reconciliation](docs/REPO_PLAN.md) and [coordinator #191](https://github.com/Khamel83/argus/issues/191). It maps every observed open source record. Existing incomplete acceptance and holds remain in force.
## Fleet CI cancellation - source checkpoint (October 16, 2026)

- [x] `.github/workflows/ci.yml` has a prepared PR-only concurrency cancellation change on `feat/fleet-ci-cancellation-20261016`; native CI, review, and normal merge remain pending. No runtime change is claimed. Done (model-matched): https://github.com/Khamel83/argus/pull/195
