# ADR 0004: No-spend provider readiness and compatibility gates

- Status: Accepted
- Date: 2026-07-27
- Issue: [#64](https://github.com/Khamel83/argus/issues/64)
- Parent: [#58](https://github.com/Khamel83/argus/issues/58)
- Depends on:
  [the stability scorecard](../scorecards/stability-competitive.md),
  [the provider/extraction drift inventory](../research/2026-07-26-provider-extraction-contract-drift.md),
  [the no-spend probe matrix](../research/2026-07-27-provider-health-probe-matrix.md),
  and
  [ADR 0002](0002-bounded-retrieval-plan-cache-identity.md)

## Decision

Argus will replace its single overloaded provider status with a typed,
evidence-backed readiness snapshot. Configuration, reachability,
compatibility, recent usability, cooldown, and spend eligibility are
independent observations. Argus may derive an execution decision from them,
but no surface may turn `configured`, `enabled`, an HTTP 200, or an old
success into `healthy`.

Routine health work is no-spend:

- `doctor`, `health`, `mcp check`, readiness endpoints, container health
  checks, dashboards, and background refreshes never initiate a billable or
  quota-consuming provider search;
- fixture and contract suites prove adapter compatibility without credentials;
- explicitly allowlisted account or metadata endpoints may refresh a balance
  or reachability observation only when their provider contract says the call
  consumes no search credit;
- a provider search can be a live canary only in a separate, explicitly
  authorized workflow with a named provider, bounded request, spend
  reservation, and durable receipt; and
- ordinary user searches may recover a provider from cooldown, but diagnostics
  never spend a request merely to make a dashboard green.

The provider catalog contains every supported adapter for diagnostics and
documentation. The executable registry for a primary profile contains only
providers whose configuration predicates are satisfied. A non-free provider
requires an enabled flag, a present credential, an authoritative or
operator-approved stable account binding, a finite configured budget, and a
durable spend repository. Missing or unbudgeted paid providers remain catalog
entries with an explicit ineligible reason; they are not constructed as
executable providers.

`free_only=true` is enforced twice: while constructing the retrieval plan and
immediately before reservation/invocation. A tier greater than zero cannot be
reserved or called even if explicitly selected by a caller, introduced by a
fallback, or present in a cached plan. CLI `--free`, HTTP `free_only=true`,
MCP `free_only=true`, and the Python query field share this invariant.

Provider-declared insufficient credit is a terminal spend state, not an
ordinary health failure. The adapter normalizes documented exhaustion
responses to `balance_exhausted`; the executor transactionally settles any
attempt, persists the exhaustion evidence, and prevents another reservation.
One-time-credit exhaustion persists until an authoritative balance refresh or
account-identity change. Recurring-quota exhaustion expires only at a
documented reset boundary. Neither condition enters an automatic paid
half-open probe.

## Why this approach

Three approaches were considered.

### A. Search every configured provider during health checks

This most directly tests the search path, but consumes quotas or money, can
repeat a terminal credit failure, makes diagnostics mutate the budget ledger,
and can turn monitoring frequency into provider spend.

Rejected. A paid search canary belongs to an explicit validation workflow, not
routine health.

### B. Treat configuration and mock tests as health

This is hermetic and free, but a present key does not prove that the key is
valid, the account has credit, the provider is reachable from this egress, or
the current response is compatible. The current production observation
already showed registered credentialed providers with no recent health
evidence.

Rejected. Configuration and compatibility remain useful observations but do
not become health.

### C. Compose readiness from scoped, expiring evidence

Use fixtures for contract compatibility, no-spend live observations where
allowed, real user-search outcomes for recent usability, and authoritative
account observations for spend. Preserve the missing and expired cases as
`unknown`, and derive eligibility without hiding the underlying evidence.

Accepted. This proves exactly what was observed, prevents diagnostic spend,
and lets failures remain visible without disabling unrelated providers.

## Deep module and seam

One `ProviderReadinessService` owns the state model and decision. Provider
adapters classify private provider responses; transports only render the
service output.

```text
snapshot(provider, retrieval_plan?, now) -> ProviderReadinessSnapshot
authorize_probe(provider, probe_kind, authorization) -> ProbeDecision
record_observation(ProviderObservation) -> ProviderReadinessSnapshot
```

The snapshot is immutable:

```text
ProviderReadinessSnapshot:
  provider
  catalog_status
  registration
  configuration
  reachability
  compatibility
  usability
  cooldown
  spend
  execution_decision?
  observed_at
  evidence_receipts[]
```

Each observation records:

```text
ProviderObservation:
  dimension
  state
  source
  scope
  observed_at
  expires_at?
  release_revision?
  contract_version?
  configuration_fingerprint?
  credential_version_fingerprint?
  account_fingerprint?
  evidence_ref?
  safe_reason?
```

`scope` includes provider, account/configuration fingerprint where relevant,
egress and machine for reachability/usability, and interface/release for
compatibility. The three fingerprints have separate meanings:

- `configuration_fingerprint` identifies non-secret endpoint, profile, and
  adapter configuration;
- `credential_version_fingerprint` identifies a credential version without
  revealing the credential and must be either a secret-manager version or an
  authority-keyed HMAC; and
- `account_fingerprint` identifies the provider account from an authoritative
  account response or operator-approved stable binding.

Unkeyed hashes of credentials are forbidden. Credentials, raw URLs containing
credentials, raw response bodies, and authorization material are never
retained.

The target deep module replaces direct composition of `BaseProvider.status()`,
process-local `HealthTracker`, `ReachabilityMatrix`, `BudgetTracker`, and
`ProviderSpendRepository` values by every surface. Those components can remain
as migration inputs, but only `ProviderReadinessService` defines public
semantics.

### Migration ownership

Migration has one decision owner at every stage:

1. Introduce `ProviderReadinessService` as the only rendering and execution
   decision authority. It may read frozen legacy snapshots, but HTTP, MCP, CLI,
   dashboards, and the executor cannot independently compose legacy status.
2. Write normalized observations and the materialized readiness snapshot in
   the same transactions that record provider attempts, cooldown claims, and
   spend. Legacy trackers remain private invocation primitives; their values
   cannot bypass the service.
3. Move profile construction and all 14 provider classifiers across the seam,
   then delete legacy status composition and its compatibility projection.

Each stage has a test that fails if a transport or executor reads a legacy
tracker for semantics. There is no dual-write period in which two stores can
independently authorize execution.

## Orthogonal observations

### Catalog and registration

```text
catalog_status = supported | unsupported
registration = registered | not_registered
```

`supported` means Argus ships an adapter. `registered` means this process may
consider constructing an invocation for the active profile. Neither is a
health claim.

### Configuration

```text
configuration:
  configured: bool
  issues[]:
    disabled_by_config |
    missing_credential |
    missing_account_binding |
    missing_budget |
    missing_spend_repository
```

Configuration validation checks presence and safe shape only. It never emits a
credential, tests a paid key, or calls a provider. Tier-0 providers that need a
key, including WolframAlpha, still require that key before registration. The
snapshot retains every applicable issue rather than hiding later failures
behind the first one. `configured` is derived as `issues.is_empty()`; it is not
stored independently. A contradictory serialized snapshot is invalid and
fails closed.

### Reachability

```text
reachability = unknown | reachable | unreachable
```

Reachability is scoped to egress and endpoint contract. TCP/TLS/HTTP
reachability is not compatibility or usability. An expired observation becomes
`unknown`.

### Compatibility

```text
compatibility = unknown | compatible | incompatible
```

Compatibility means the versioned adapter passes the current request,
success/empty response, documented error, malformed-response, and
non-disclosure fixtures. Static compatibility is scoped to the release and
provider contract version. It does not prove live service behavior.

### Usability

```text
usability = unknown | usable | empty | unusable
```

Only a real, policy-authorized search outcome can establish recent usability.
A valid empty response is distinct from success with results. Malformed or
unrecognized HTML/JSON is `unusable/parse_error`, never a healthy empty result.
Usability is scoped to the active egress, configuration identity, request
class, and bounded freshness window.

### Cooldown

```text
cooldown = clear | active | half_open_claimed
```

Rate limits use documented `Retry-After` or reset evidence. Transient network
and `5xx` failures use bounded exponential cooldown. Once cooldown expires,
only one real caller request that is otherwise eligible may claim the
half-open attempt. A health refresh never claims it.

The claim is a durable compare-and-set lease, not only a process mutex. Its key
is provider plus account/configuration scope, egress, and request class. The
repository grants it using database transaction time and records an owner,
monotonically increasing fencing token, and authoritative execution deadline.
The spend reservation and half-open lease are obtained transactionally before
any adapter call. The process-local `HealthTracker` lock is only a migration
optimization.

Deadline expiry never authorizes a replacement by itself. The original owner
must durably record response/error completion and release, or an authoritative
provider request-status endpoint must prove termination. If the owner
disappears or the call may still be running, the attempt becomes `unresolved`,
its charge remains `uncertain`, and the provider stays blocked for
reconciliation. A late owner with an old fencing token may append charge
evidence to its attempt but cannot settle current readiness, release a newer
claim, or clear cooldown.

### Spend

```text
spend =
  not_applicable |
  unknown |
  available |
  low |
  exhausted |
  uncertain |
  policy_denied
```

`available`, `low`, and `exhausted` require an authoritative provider balance
observation or a conservative Argus ledger derivation with its authority
identified. A configured limit is not an account balance. An unresolved
reservation is `uncertain` and fails closed for additional paid attempts when
the conservative exposure would exceed the budget.

### Healthy

`healthy` is a scoped, derived presentation state, not a stored observation. A
provider is healthy for a named request class only while all of these are true:

- configuration and registration are satisfied;
- compatibility is proven for the running adapter release;
- reachability is fresh and `reachable` on the selected egress;
- a fresh, policy-authorized live request produced a protocol-valid success or
  valid empty outcome for that request class;
- cooldown is clear; and
- spend is `not_applicable`, `available`, or conservatively `low`.

`low` remains a visible warning. `unknown`, expired, unscoped, or contradictory
evidence cannot produce healthy. A provider can be eligible for an ordinary
caller request without already being healthy; that request can create the
missing live evidence. Result relevance, freshness proof, and cross-provider
breadth are later scorecard decisions and are not implied by provider health.

## Derived execution decision

The readiness service evaluates an immutable retrieval-plan provider entry and
returns one of:

```text
eligible
policy_skipped
unavailable
cooldown
spend_blocked
compatibility_unproven
```

The decision preserves a stable reason and every contributing observation.
The fail-closed order is:

1. provider is in the signed/immutable plan and allowed by caller policy;
2. `free_only` and caller tier cap permit its tier;
3. registration and configuration are satisfied;
4. compatibility is proven for the running adapter release;
5. egress has no fresh `unreachable` observation;
6. cooldown can be claimed;
7. paid spend state permits the conservative reservation; and
8. the executor atomically rechecks policy, cooldown, and spend before the
   adapter call.

An unknown reachability or usability observation may be eligible for an
ordinary caller request: the request itself can generate evidence. It cannot
be rendered as healthy before that outcome. Unknown compatibility is
fail-closed because malformed provider data risks incorrect results.

## Failure classification and retry

Adapters normalize documented provider failures into the private categories
defined by the drift inventory:

```text
invalid_request
authentication_rejected
policy_rejected
rate_limited
balance_exhausted
timeout
provider_unavailable
parse_error
empty
```

The executor applies these transitions:

| Category | Readiness transition | Automatic retry |
|---|---|---|
| `invalid_request` | request rejected; provider health unchanged | never with the same request |
| `authentication_rejected` | credential unusable until credential-version fingerprint changes | none |
| `policy_rejected` | request/provider policy mismatch | none until plan changes |
| `rate_limited` | cooldown until authoritative reset or bounded fallback | one caller-owned half-open attempt |
| `balance_exhausted` | durable spend `exhausted`; settle charge as known or uncertain | none |
| `timeout` | transient failure and bounded exponential cooldown | caller-owned half-open after cooldown |
| `provider_unavailable` | transient failure and bounded exponential cooldown | caller-owned half-open after cooldown |
| `parse_error` | usability `unusable`, compatibility incident, cooldown | none until fixture/contract is repaired or an authorized caller half-open |
| `empty` | valid usability `empty`; not a failure and not proof of result quality | normal future caller searches |

HTTP status alone is insufficient where providers publish stable error codes.
The classifier stores allowlisted status, provider code, retry/reset metadata,
request reference, and a bounded scrubbed summary. It never stores
`str(HTTPStatusError)`, a request URL with query parameters, raw response body,
or headers.

For a paid attempt, reservation, provider invocation, classification, spend
settlement, terminal-state write, and evidence receipt share one durable
attempt identity. If actual charge cannot be proven after a call, the attempt
settles `uncertain`; Argus does not assume zero.

If exhaustion has no authoritative refresh endpoint, Argus remains blocked and
emits one deduplicated operator alert containing the provider, account
fingerprint, observation age, and safe reconciliation action. It does not
probe through search. Recovery requires operator-approved reconciliation or an
explicit account-change workflow; silence and elapsed time never restore
credit.

## Probe authorization

Every probe declares one of:

```text
fixture
local_component
no_spend_account
no_money_quota
billable_search
```

The default allowlist is:

| Probe kind | Routine diagnostics | Explicit validation workflow |
|---|---|---|
| `fixture` | allowed | allowed |
| `local_component` | allowed with bounded timeout | allowed |
| `no_spend_account` | allowed only from a versioned provider allowlist | allowed |
| `no_money_quota` | denied | allowed only when the named quota is authorized |
| `billable_search` | denied | allowed only with named provider, reservation, and durable receipt |

Unknown endpoint charging semantics are treated as spend-bearing. A malformed
request is never used to test a key or balance because providers may charge for
failed requests.

## Surface contract

All surfaces render the same snapshot and observation vocabulary.

### `argus doctor`

`doctor` validates configuration, local dependencies, fixture/contract
manifest age, authority connectivity, and cached readiness. It performs no
provider search. Its optional refresh may run only `local_component` and
allowlisted `no_spend_account` probes. It exits nonzero when the required
profile lacks an eligible retrieval path or compatibility is unproven, and
prints `unknown` separately from failure.

### `argus health` and `/api/provider-health`

These are read-only snapshots by default. They never create health records,
claim cooldown, or invoke adapters. A provider row includes all orthogonal
observations, timestamps, expiry, scope, and the derived execution decision.
Overall status is not `ok` merely because one registered provider is enabled.

The repository transactionally materializes one current snapshot per provider
and policy generation whenever an observation or policy input changes. The
snapshot records `valid_until` as the earliest expiry of any observation that
supports its decision. Every execution read compares `valid_until` with
repository transaction time. At or after expiry, the repository atomically
materializes a new generation with expired dimensions set to `unknown`, or
fails closed to `unknown` while another writer does so; it never scans history
on the request path.

Render surfaces read the same bounded snapshot. An in-process rendering cache
may reuse a generation for at most five monotonic seconds and never beyond the
authority-provided remaining duration observed when it fetched the snapshot.
Execution does not use that cache. Cache loss causes a repository read and
never changes the decision.

### Admin smoke

The default admin smoke validates the canonical HTTP pipeline with a fixture.
It invokes no provider. A live tier-0 or paid validation is a separate
route/command requiring a named provider, explicit authorization context,
idempotency key, and durable result receipt; a paid validation also requires a
budget reservation. It is never scheduled as readiness monitoring.

### `argus mcp check`

`mcp check` validates process/transport startup, initialization, tool schema,
authentication configuration, and a fixture-backed tool round trip. It does
not call a search provider. Live MCP validation is a separate explicit
workflow through canonical HTTP. It requires one named tier-0 provider, an
immutable `free_only=true` plan, no fallback expansion, at most one invocation,
quota authorization where applicable, an idempotency key, and a durable
receipt. MCP does not repeat provider validation already performed by HTTP.

### Logs and traces

Every skip, attempt, and outcome records a stable category and evidence
reference. Logs and traces distinguish policy skip, missing configuration,
compatibility unknown, cooldown, rate limit, exhaustion, uncertain settlement,
valid empty, parse failure, and provider failure. They do not contain raw
provider payloads or secrets.

### Budget ledger

Budget reporting labels values as `provider_authoritative`,
`argus_observed`, `argus_estimated`, or `uncertain`, with observation time,
expiry/reset, account/configuration fingerprint, and evidence reference.
Provider-reported remaining balance is never synthesized from an Argus
configured cap. Unknown is not unlimited.

## Registration profiles

The catalog is static and complete; execution profiles are explicit:

```text
primary:
  tier_0: enabled/configured providers
  non_free: enabled + credentialed + stable account binding + finite budget
            + durable spend repository

free:
  tier_0 only, still subject to required credentials and compatibility

validation:
  named providers only, explicit probe authorization, no fallback expansion
```

Profile construction emits a diagnostic record for every catalog provider,
including why it was not registered. A key added at runtime does not
implicitly authorize spend: a stable account binding, finite budget, and spend
repository are still required. When a provider has no authoritative no-spend
account endpoint, an operator-approved opaque binding identifies the account
independently of the credential. Rotating that account's key changes only the
credential-version fingerprint. Changing the account binding is an explicit
configuration event and does not silently reconcile prior exhaustion.

## Evidence lifetime and invalidation

- Repository/database transaction time is the freshness authority. Producer
  clocks are diagnostic only. Observations more than 30 seconds in the future
  are rejected; TTLs start at the authority's ingestion time. Provider reset
  timestamps remain evidence, but an implausible or backward deadline fails
  closed instead of overriding the authority clock.
- Fixture compatibility expires when adapter code, fixture manifest, request
  shape, response schema, or declared provider contract version changes.
- Reachability and usability observations use provider-specific bounded TTLs;
  expiration becomes `unknown`, not failure or health.
- Authentication rejection remains until the credential-version fingerprint
  changes or an authoritative no-spend account check succeeds.
- One-time exhaustion remains until an authoritative balance observation,
  manual approved reconciliation, or authoritative account identity changes.
  Credential rotation for the same account never clears exhaustion.
- Recurring exhaustion may expire at a documented reset; absent such evidence,
  it remains exhausted.
- Cooldown has its own deadline and does not erase the underlying failure.
- Release promotion snapshots all evidence rather than extending TTLs.

The materialized snapshot contains only the latest observation for each
dimension and bounded scope plus at most 32 evidence receipt references per
provider. A reference is an opaque identifier of at most 128 characters; raw
receipts are never embedded. Superseded non-terminal observations are
compacted after their audit-retention window. Terminal exhaustion, unresolved
charge, active cooldown/lease, and the observation that resolved each are
retained under the durable attempt-ledger policy, not copied indefinitely into
snapshots.

Scopes are bounded inputs: provider is the fixed catalog, a deployment may
declare at most eight egresses, and the executable request-class enum contains
at most four classes. Exactly one account/configuration generation is
executable; one migrating generation may remain diagnostic in the durable
ledger but cannot expand the active Cartesian scope. A provider therefore has
at most 32 executable scopes. A manifest or enum exceeding these limits is
invalid and fails startup readiness.

Receipt-reference retention is deterministic: active terminal/unresolved
evidence first, then evidence supporting the current decision, then the
observation that resolved each terminal state, then newest diagnostics. No
terminal, unresolved, or decision-supporting receipt is silently discarded. If
the protected set itself exceeds 32, the snapshot becomes
`evidence_overflow/unavailable`, reports the protected count and a bounded
ledger query reference, and blocks execution until compaction or repair. The
full audit history remains in the durable ledger.

## Current evidence interpretation

The 2026-07-26 production observations are point-in-time evidence, not current
health:

- SearXNG returned usable results through the free path at that egress.
- DuckDuckGo and Yahoo completed calls, but Yahoo's current parser can turn
  unrecognized markup into an empty success; its compatibility remains
  unproven until a fixture distinguishes valid empty from parse failure.
- Several credentialed providers were registered without current health
  evidence; they remain `usability=unknown`.
- Serper reported not enough credits, and Parallel and Valyu reported
  insufficient credits. Once normalized by the target classifier, these
  observations become durable `balance_exhausted` evidence for their scoped
  accounts rather than generic cooldown failures.
- SearchAPI lacked a key and is `missing_credential`; no call is permitted.

The old observations need retained timestamps, scope, configuration
fingerprints, and receipts before they can seed the target ledger. The design
does not retroactively manufacture those fields.

## Implementation and test obligations

Implementation work must:

1. introduce the readiness snapshot and central service before changing public
   display strings;
2. split the catalog from the executable registry;
3. add provider fixtures for all 14 adapters and every documented failure
   class;
4. normalize exhaustion, rate limits, authentication, parse failures, and
   secret-safe errors inside adapters;
5. make terminal spend state durable and transactionally connected to the
   attempt ledger;
6. use a durable compare-and-set lease for distributed half-open execution;
7. materialize bounded snapshots with authority-clock expiry and compacted
   receipt references;
8. enforce `free_only` at plan and invocation boundaries;
9. make all routine diagnostic paths network-free or restricted to the
   versioned no-spend allowlist;
10. migrate HTTP first, then make CLI and MCP render the HTTP semantics;
11. preserve `/api/live` and container checks as network-free process liveness;
   and
12. add invariant tests proving diagnostics and free-only requests cannot call
    or reserve a tier greater than zero.

The required negative tests include:

- a paid provider explicitly selected under `free_only`;
- a paid provider introduced after planning;
- an exhausted provider across process restart and concurrent callers;
- a rate-limited provider with and without valid reset metadata;
- an unknown-charge transport failure;
- Yahoo/SearXNG HTTP 200 with malformed or policy-blocked content;
- missing and rotated credentials without logging their values;
- missing paid-provider account binding preventing registration;
- credential rotation clearing authentication rejection without clearing
  same-account exhaustion;
- expired compatibility and usability evidence;
- concurrent half-open claims across separate processes;
- lease deadline expiry while the original call remains unproved, including
  rejection of late stale-fencing-token settlement;
- authority/producer clock skew and implausible provider reset timestamps;
- cached health expiring when repository time advances without any write;
- bounded snapshot and receipt-reference compaction under repeated
  observations and scope injection;
- scope/receipt overflow failing startup or provider readiness without silently
  evicting protected evidence;
- exhaustion without a refresh endpoint producing one deduplicated alert while
  remaining blocked;
- every migration stage rejecting legacy status as an independent execution or
  rendering authority;
- MCP live validation naming exactly one tier-0 provider with no fallback and
  at most one invocation;
- CLI, HTTP, MCP, and Python semantic equivalence; and
- every routine health command with adapters instrumented to fail if invoked.

## Consequences

- Provider status becomes more verbose, but it is reviewable and cannot hide
  uncertainty behind one color.
- A newly configured paid provider remains unavailable until budget and
  compatibility gates are satisfied. This is intentional.
- Routine diagnostics no longer prove end-to-end paid search. Explicit
  validation provides that evidence with an auditable cost boundary.
- Historical health may appear less optimistic because missing or expired
  evidence is `unknown`. Visible uncertainty is preferable to bad results.
- This decision adds no provider call, paid credit use, production mutation,
  secret readout, or deployment.
