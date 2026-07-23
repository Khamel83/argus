# Authoritative State and Observability Research

Date: 2026-07-22

## Decision

Argus production on homelab uses PostgreSQL as its one authoritative
operational store. SQLite remains a supported adapter for isolated development
and Tier 1 standalone installs, but it is not the homelab production database.

The HTTP service is the only production execution authority. MCP, CLI,
dashboards, Maya, Hermes, and fleet monitors call the authenticated HTTP API;
they do not construct their own production brokers or write Argus tables
directly.

Production data belongs in a separate `argus` database with separate migration,
runtime read/write, dashboard read-only, and backup roles on the existing
PostgreSQL 16 server. The server becomes a formally managed shared homelab
PostgreSQL service; `atlas-postgres` remains a compatibility alias while Atlas
clients migrate to its new platform identity. This does not place Argus tables
in Atlas's database or give either application access to the other's data.

## Why the tentative SQLite design was rejected

SQLite is appropriate for an embedded application database and low-to-medium
traffic websites. WAL allows readers and one writer to proceed concurrently,
but all processes must be on one host, there is still only one writer, and
applications must manage checkpoint behavior and `SQLITE_BUSY`.

More importantly, SQLite disclosed a rare WAL reset corruption race affecting
multi-process writers through 3.51.2; it is fixed in 3.51.3 and selected
backports. The live Argus image uses SQLite 3.46.1, and the current HTTP and MCP
containers both open shared SQLite files. A single-writer architecture and a
fixed SQLite build could make this safe, but PostgreSQL already matches the
production concurrency and durability model without preserving that risk.

PostgreSQL MVCC gives concurrent sessions consistent snapshots while reads do
not block writes and writes do not block reads. It also gives Argus one
transaction boundary for retrieval records, provider attempts, budget usage,
and Maya outbox insertion.

Sources:

- [SQLite: Appropriate Uses](https://sqlite.org/whentouse.html)
- [SQLite: Write-Ahead Logging](https://sqlite.org/wal.html)
- [PostgreSQL: MVCC introduction](https://www.postgresql.org/docs/current/mvcc-intro.html)

## Current-state findings

- `argus` HTTP and `argus-mcp` each construct a local broker.
- They share `/data` and report the same logical deployment identity, while
  provider health, cooldowns, reachability, caches, and tracker objects are
  independent in memory.
- Main history is in `argus.db`; budgets and sessions use separate direct
  `sqlite3` stores; usage endpoints bypass SQLAlchemy and return empty results
  for PostgreSQL.
- The main live database contains roughly 72,552 search runs, 236,313 result
  rows, and 927,415 provider-usage rows. Extraction attempts are not recorded
  with equivalent durability.
- Provider health, cooldowns, and reachability reset on process restart.
- Search persistence is best effort, so an acknowledged response can exist
  without its required durable run or Maya outbox record.
- The homelab has no active Prometheus, Grafana, Loki, Tempo, or OpenTelemetry
  stack to reuse.
- The live `atlas-postgres` PostgreSQL 16 container stores its data under
  `/mnt/fast-storage/appdata/atlas-postgres`.
- A later cross-repository check found the evidence missed by the initial
  Argus-only audit: Atlas runs a daily custom-format `pg_dump`, retains seven
  daily archives, structurally validates new archives, and successfully
  restored the July 22 archive into an isolated scratch database. This proves
  the server is reusable; the job remains Atlas-database-specific and must be
  generalized to include Argus, cluster globals, off-live-directory copies,
  and recurring restore verification.

## State model

### Durable authoritative records

One PostgreSQL transaction records the accepted unit of work:

- request, caller, mode, timestamps, sanitized input identity;
- search run or extraction run;
- provider/extractor attempts, normalized outcome, latency, tier and egress;
- result or extracted-artifact metadata and content hash;
- budget usage charged from actual attempts;
- session changes when a session is used;
- a Maya outbox item for a user-visible response.

If that transaction fails, Argus must not claim the retrieval is durably
accepted. Delivery from the outbox is at least once, ordered per parent capture
where needed, and idempotent at Maya. AWS's transactional-outbox guidance
confirms that the business row and outbox row must commit together and that the
consumer must tolerate duplicates.

Source:
[AWS Prescriptive Guidance: transactional outbox](https://docs.aws.amazon.com/prescriptive-guidance/latest/cloud-design-patterns/transactional-outbox.html)

### Durable snapshots, not eternal truth

Provider reachability, health, cooldowns, balance observations, and runtime
capabilities are snapshots with:

- observed state;
- observed and expiry timestamps;
- source (`probe`, `request`, `provider_api`, or `estimate`);
- reason and last transition;
- deployment and service-instance identity.

They survive restart so operators can explain recent behavior, but stale
snapshots never masquerade as current truth. Provider-reported balances remain
distinct from Argus usage estimates.

### Disposable state

Response caches, warmed clients, browser contexts, locks, and in-flight gauges
are process-local and disposable. Their loss may reduce performance but cannot
lose acknowledged work, reset a budget, or change the audit record.

### User-visible artifacts

Maya owns durable user-visible retrieval artifacts after acknowledgement.
Argus owns the pending/dead-letter delivery record plus useful operational
evidence. Raw secrets, cookies, authentication headers, and unbounded provider
payloads are never retained.

## Persistence interface

All production state passes through repository/service interfaces backed by
SQLAlchemy and versioned Alembic migrations. Direct `sqlite3` queries in the
usage dashboard, budget tracker, and session store are replaced. Both adapters
must pass the same contract tests, but production-only concurrency and
backup/restore gates run against PostgreSQL.

Schema evolution is a deployment gate:

1. backup and verify the artifact;
2. run backward-compatible migrations;
3. start the candidate;
4. verify startup, readiness, schema version, and canaries;
5. promote or roll back application code;
6. use a forward repair migration rather than assuming every data migration is
   mechanically reversible.

## Backup and recovery contract

A database is not production-ready merely because PostgreSQL is running.

- Extend the existing nightly custom-format `pg_dump` automation to back up the
  separate `argus` database as well as `atlas`.
- Cluster globals/roles backed up separately without embedding credentials.
- Backups copied to storage outside the live PostgreSQL data directory and
  covered by the homelab backup policy.
- Retention: 7 daily, 5 weekly, and 12 monthly logical backups initially.
- Monthly automated restore into a disposable database, followed by schema,
  row-count, integrity, and Argus read-path checks.
- Deployment readiness reports backup freshness and last verified restore as
  administrative evidence; a stale backup degrades operations and blocks
  schema-changing promotion.

`pg_dump` produces an internally consistent snapshot without blocking normal
database work. PostgreSQL documents logical dumps, filesystem backups, and
continuous archiving as different techniques; logical dumps are the simplest
initial fit for this personal-production service.

Sources:

- [PostgreSQL: Backup and Restore](https://www.postgresql.org/docs/current/backup.html)
- [PostgreSQL: SQL Dump](https://www.postgresql.org/docs/current/backup-dump.html)
- [PostgreSQL: Privileges](https://www.postgresql.org/docs/current/ddl-priv.html)

## Health and observability

Expose separate endpoints:

- `/health/live`: event loop/process can make progress; no provider or database
  dependency checks that would create restart storms.
- `/health/startup`: configuration, migrations, and required local
  capabilities initialized.
- `/health/ready`: authenticated request path and PostgreSQL transaction path
  work; required minimum search/extraction path is available.
- `/api/admin/status`: authenticated detailed state, deployment identity,
  capabilities, provider snapshots, outbox age/depth, backup freshness, and
  recent failure summaries.
- `/metrics`: scrape-compatible bounded metrics; safe to expose only within the
  trusted monitoring boundary.

Kubernetes documents startup, liveness, and readiness as different decisions:
liveness controls restart while readiness controls traffic acceptance. The
same semantics apply to Docker health checks and Baywatch even though homelab
does not run Kubernetes.

Source:
[Kubernetes: container probes](https://kubernetes.io/docs/concepts/workloads/pods/pod-lifecycle/#container-probes)

Metrics use bounded labels such as operation, outcome, provider, extractor,
mode, capability, and caller class. Query text, URL, request ID, session ID,
capture ID, and raw caller names belong in the operational ledger or structured
logs, not metric labels. Prometheus explicitly recommends avoiding high-cardinality
labels and measuring online services by request count, errors, latency, and
in-progress work.

Source:
[Prometheus instrumentation practices](https://prometheus.io/docs/practices/instrumentation/)

Structured logs carry trace/request IDs, deployment identity, severity, event
name, and safe attributes. OpenTelemetry's service resource conventions provide
the fleet vocabulary: `service.namespace`, `service.name`, `service.version`,
and a unique `service.instance.id`. Argus should emit compatible fields now
without requiring a new telemetry backend in the first deployment.

Sources:

- [OpenTelemetry service resource conventions](https://opentelemetry.io/docs/specs/semconv/resource/service/)
- [OpenTelemetry log model](https://opentelemetry.io/docs/concepts/signals/logs/)

## Retention

- Search/extraction runs and provider attempts: 90 days of detail.
- Daily provider/caller/outcome aggregates: 13 months.
- Budget charges, balance snapshots, deployment records, and Maya delivery
  audit: 24 months.
- Acknowledged Maya outbox payloads: compact after 7 days; retain delivery
  metadata for 90 days.
- Dead-letter payloads: retain until resolved, with age and size alerts.
- Response caches: maximum 7 days and always disposable.
- Container logs: bounded rotation, initially 7 days; the PostgreSQL ledger is
  the durable diagnostic source, not an exhaustive log archive.

Retention jobs run in small bounded batches and publish their last success,
duration, and deleted-row counts.

## Rejected alternatives

### Shared SQLite as production authority

Rejected for the current two-process topology and reliability target. It remains
the correct low-administration default for isolated standalone installs.

### PostgreSQL for every piece of state

Rejected. PostgreSQL owns durable operational truth; caches, locks, browser
contexts, and in-flight work stay ephemeral. User-visible artifacts move to
Maya after acknowledgement.

### Add a complete Prometheus/Grafana/Loki stack immediately

Rejected as a prerequisite. Argus first exposes correct health, status,
structured logs, and bounded scrape metrics. The fleet can add or reuse a
telemetry backend later without changing Argus's state ownership.

### Reuse `atlas-postgres` without changing its identity or contract

Rejected, but reusing the server is accepted. Promote it into a shared homelab
PostgreSQL service, preserve `atlas-postgres` as a compatibility alias during
migration, isolate applications with separate databases and roles, and expand
the proven Atlas backup/restore path to cover Argus and cluster globals.
