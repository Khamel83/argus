# PostgreSQL 16 CI client selection — November 2, 2026

PR229 removes only nine TODO lines. Its head36e560802161cc6c6e6dddf9ac2cf4b216c9e55e and main164ecc18544d3ea2551e88f10d2eab81af3c001f share workflow blob39c2447fd1ffb08258673c4fe39caeec498df0fb and recovery-test blob946f8d23414184c57b86c6e6a4c156c6318812bd.

[PR229 run41400127333](https://github.com/Khamel83/argus/actions/runs/41400127333), attempts1/2, used Ubuntu26.04 and PostgreSQL client18.6 against the pinned server16. Each produced1 failed/323 passed. The concurrent snapshot test fails at pg_restore --exit-on-error: server16 rejects SET transaction_timeout = 0. [Main run41246462740](https://github.com/Khamel83/argus/actions/runs/41246462740) used Ubuntu24.04/client16.15 and passed324 tests. These logs support client/server drift; a same-environment failing main baseline was not run.

Only postgres-ledger now uses Ubuntu24.04 and installs postgresql-client-16. ARGUS_TEST_PSQL uses its absolute binary. GITHUB_PATH selects the same bin directory for pg_dump and pg_restore. Installation verifies all three executables and major16 versions; the next step verifies PATH selection and versions before migrations/tests. Server semantics, restore error handling, production recovery behavior and snapshot assertions remain unchanged.

Local validation: actionlint with available ShellCheck passed; PyYAML parsing and structural comparison confirmed every other job, the server service and existing test/migration steps unchanged. Local integration tests NOT RUN: no PostgreSQL clients/server were found; earlier mise commands stopped before pytest on a sandbox trusted-config write restriction. No local install or blocked command was repeated. Owner explicitly authorized ready publication with native CI proving the repair. Required acceptance is all eight CI jobs and trusted PASS for the current commit before normal merge.

PR229 remains owner-owned. PR226 exhausted review, runtime, private data, credentials and provider/model services were untouched. Existing dirty checkout files remain unchanged. PR230 head d56696b4a9f899316dec5a4e9238390b78b7ef4c has trusted reviewer PASS but its postgres-ledger job125159830813 failed; it cannot merge on that evidence.

Budget starts81% used/19% remaining, reset1793774124; owner allocation2percentagepoints for this repository. Native subscription tools only, no paid/API/provider fallback. Meter ordinaryUsageAllowed is unavailable; no free-route claim is inferred from credit availability.
