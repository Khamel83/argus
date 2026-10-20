# Argus backup snapshot source acceptance — 2026-10-20 UTC

## Source and test acceptance

The shared PostgreSQL backup repair merged in PR [209](https://github.com/Khamel83/argus/pull/209)
as `175a7a39aefdc43e3c08a1885974f571bef0cccd`. Trusted review
`5580007693` and required CI run `39357846393` passed. The native PostgreSQL
ledger completed 323 tests, including the concurrent-writer test that exports a
snapshot, writes after export, and verifies that the dump and inventory retain
the exported view.

Manifest v2 records per-database snapshot provenance and binds the tenant,
capture method and archive SHA. The exporter and inventory collector use the
same read-only repeatable-read snapshot for each database. Argus and Atlas are
captured independently; this repair does not claim cross-database atomicity.
Legacy v1 manifests remain readable and immutable, with no retroactive
consistency claim.

## Runtime transport and producer proof

The previous producer source `175a7a39aefdc43e3c08a1885974f571bef0cccd`
remains a rollback anchor. The current exact source
`66365a661abcfa6ccb4a10dfe5e4b251eaf339ff` was sealed into the private
Homelab immutable backup-tool capsule. The new capsule contains 225 files
totaling 4,715,988 bytes and manifest
`7dc33f100a0a2189967ada284f98b6a45b2e9eaa906fa0829498b3014cc3b056`.
The private installation receipt records the exact source identity and
installation time. The source capsule's sealed hashes and imports passed
read-only validation.

The fixed Infra wrapper from merge `13c7f97` is installed. Its one-row cron CAS
preserved the 01:45 schedule, job identity and log path. A bounded manual run
completed 02:30:52–02:34:20 UTC in 208 seconds with the client unit capped at
50% CPU, 768 MiB memory and no swap. It published schema-2 set
`20261020T023056Z`, manifest SHA
`6159fa92cc9e367af008d56f467b98919b429a759f302f5ae88dc8a3c8da3dfc`, and
bound both tenants to `pg-exported-snapshot-v1` provenance. The job receipt SHA
was `856b8d3b7ca3186a072c695c7b595b042a3608166603b649a0ba9621967f6e99`.
This was a manual functional run, not a natural recurrence.

The first test of the new installed producer was incorrectly run as root and
failed its backup-root identity preflight before creating a dump. Its failure
receipt and logs are retained; the receipt SHA is
`dda10b445a06fd6828de3d29e8d543998da5db366a820df9e638f28eda3284ed`.
The corrected test runs as ordinary `khamel83` with the same caps. It completed
03:28:54–03:32:07 UTC in 193 seconds with an 82.984 CPU-second charge, 768 MiB
peak and zero swap. It published schema-2 set `20261020T032858Z` with manifest
`e69d7b6dfb15c75e40ce53dc0109e992dc9d6cd2fd28cd024e62ebab9dacae02` and job
receipt SHA `3d6323556831998c85331b8e40207ed4da15c3893aafa3e6416277f1c2dcf88e`.
The post-backup canonical record SHA was
`0ad78243be9b8ed46a08b8ea7f1fa24442973831e9928ba5017561134a753a65`.

The first strict restore of the previous set `20261020T023056Z` failed at
02:36:52 UTC; its inner log was not retained, cleanup completed and canonical
state stayed unchanged. A
subsequent bounded reproduction identified the v2 reader defect that compared
the already-validated `snapshot_provenance` metadata as restored database
inventory. PR
[211](https://github.com/Khamel83/argus/pull/211) merged as
`e7ee75a9193615beb12137467aa9b4104164fdb0` after trusted review
`5580171742` and required CI `39360416459`; its native ledger completed 323
tests with the full v2 entry. The second restore attempt failed at 02:52:27 UTC;
helpers were cleaned and canonical state remained unchanged. An independent
observer matched both database table sets, all row counts and all constraints.
Argus's schema fingerprint matched. Atlas alone differed because function order
was not canonicalized: expected
`90b7374e2cfc21723b0f282db0bc343cad9535f3c82d953030967a67a0156b4d` versus
actual `19f824669aa6e35c5eecedd49f2b898a6121bf50c5c547241391916a70b4b711`.
PR [213](https://github.com/Khamel83/argus/pull/213) merged as
`66365a661abcfa6ccb4a10dfe5e4b251eaf339ff` after trusted review
`5580366828` and required CI `39363477632`; its native ledger completed 324
tests. The diagnosis matched all 36 tables, 350 columns, 115 constraints, 106
indexes and 46 functions. A fresh strict isolated restore completed at
03:34:47 UTC using source `66365a6`, restore-only image
`sha256:01f59e0040f12ff1c51b1636e089b99396685b1469a29a482e66408a3406e3c3`,
and the matching schema-2 backup identity. Integrity, row counts, schema, the
Argus read path and migration compatibility all passed. The accepted canonical
restore record SHA is
`4bc1adb82b19ebcbf11d932b0f3ea4b4264471b1fbbdb2805c3e4580a8f6a5d1`; the
root accepted receipt SHA is
`ac5be8fe3b4e5d893c06cc18849e03e28938c31f1775f4d367f9b463f316d390`.
No bad-backup or sole-cause claim is made, and no application cutover is claimed. The private
observer receipt is recorded by SHA
`37a68ade509af4c45477b7fbe17fd3438e1ff58f4976662b9944c17312c75038`; the
failure receipt SHA is
`880b3fef40b7a326b6b7f34d8786657df3685b0aa5cf13d1ef62ec35c0950265`.

## Restore acceptance and remaining boundaries

The October 20 01:45 UTC v1 backup, earlier v1 inventory mismatch, first v2
reader failure, second Atlas function-order mismatch and root-preflight producer
failure remain preserved. The flags `metadata_registry_complete`,
`schema_contract_clean`, `forward_compatible` and
`rollback_path_human_approved` remain
false. No application cutover or admission override occurred. A future natural
01:45 recurrence remains a separate observation from this manual acceptance.
