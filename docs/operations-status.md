# Operational status

Argus separates process liveness from dependency readiness. This keeps
PostgreSQL, provider, Maya, browser, and recovery failures visible without
turning them into container restart storms.

## Interfaces

| Route | Authentication | HTTP status | Semantics |
|---|---|---|---|
| `GET /api/live` | Public | Always `200` while the event loop can serve | Constant process liveness. It performs no broker, database, provider, Maya, browser, or network I/O. Docker health uses this route. |
| `GET /api/startup` | Public | `200` | Minimal cached initialization state and loaded package version. |
| `GET /api/ready` | Public | `200` when ready or degraded; `503` when unready | Minimal cached readiness. It never probes a dependency in the request. |
| `GET /api/admin/status` | Admin token | `200` | Full operator view: build/deployment/instance identity, authority and schema identity, capabilities, typed observations, promotion state, and bounded metrics. |
| `GET /api/health` | Public | `200` | Liveness-only compatibility surface. It is deliberately safe for old health checks but new deployments should use `/api/live`. |

`/api/admin/status` uses the existing admin boundary:
`Authorization: Bearer $ARGUS_ADMIN_API_KEY` or
`X-Admin-API-Key: $ARGUS_ADMIN_API_KEY`.

## Observation semantics

Every dependency and provider observation contains:

- `state`: `healthy`, `degraded`, `unready`, `unknown`, or `disabled`;
- `source`: a bounded provenance identifier such as `authority_probe`,
  `reachability_probe`, `accounting_ledger`, `process_memory`, or
  `recovery_evidence`;
- `observed_at` and `expires_at`;
- a bounded sanitized `reason`;
- `last_transition`, preserved while the state remains unchanged; and
- `stale`.

An expired observation renders as `unknown` with
`reason=observation_expired`. In-memory provider health, cooldown, and browser
state use `source=process_memory`; a new service instance does not invent the
previous process's state.

Provider detail has separate `capability`, `reachability`, `health`,
`cooldown`, and `balance` observations. A `null` remaining balance means
unlimited and is distinct from numeric zero.

## Readiness classification

- In production, PostgreSQL connectivity, the expected Alembic schema head,
  and the durable outbox authority are required. Loss of any is `unready`.
- Maya delivery loss is `degraded` while the durable outbox remains
  available. Outbox authority loss is `unready`.
- Missing or unadmitted browser capability is `degraded`.
- Missing, stale, or failed recovery evidence is `degraded` and
  `promotion_allowed=false`; it does not claim that production recovery is
  complete.
- A partial provider failure is `degraded`. If no admitted retrieval path
  remains usable, status is `unready`.
- Stale or unknown evidence is never reported as healthy.

The authority refreshes this cached evidence in the background every
15 seconds. Dependency restoration therefore becomes visible without a
process restart and without request-time probes.

## Identity and correlation

Operator status reports the installed package version, admitted source
revision and lock identity when a runtime manifest is available, bounded
deployment/release identifiers, the authority role/backend/machine/egress,
schema head, capability inventory, process start time, and a unique
`service_instance_id`.

Set `ARGUS_DEPLOYMENT_ID` and optionally `ARGUS_RELEASE` to bounded identifiers
made only of letters, digits, `.`, `_`, and `-` (maximum 64 characters).
Invalid caller `X-Request-ID` values are replaced by a generated 16-character
identifier. Responses return safe request correlation and, when configured,
`X-Argus-Deployment-ID`.

## Bounded evidence

Request metrics use only route templates, method, status class, and a bounded
outcome. Provider metrics use the fixed provider inventory. Request IDs,
deployment/instance IDs, callers, queries, raw paths, URLs, exception text,
and payload values are never metric labels.

The bounded in-process snapshot covers request count/outcome/latency,
in-flight work, outbox pending/dead-letter counts, browser memory/process
observations, process restart knowledge, and accounting reconciliation.
Unavailable process-local measurements remain explicitly `unknown`.
