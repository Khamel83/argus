# ADR 0002: Bounded retrieval plan and cache identity

- Status: Accepted
- Date: 2026-07-26
- Issue: [#61](https://github.com/Khamel83/argus/issues/61)
- Parent: [#58](https://github.com/Khamel83/argus/issues/58)
- Depends on:
  [the stability scorecard](../scorecards/stability-competitive.md) and
  [the provider/extraction drift inventory](../research/2026-07-26-provider-extraction-contract-drift.md)

## Decision

Argus will resolve every effective `SearchQuery` into one immutable,
deterministic `RetrievalPlan` before cache lookup or provider execution.
`RetrievalPlan` is an internal deep module: HTTP, MCP, CLI, and Python callers
continue to use their existing interfaces, while planning, cache identity,
policy resolution, deadlines, and evidence move behind one internal seam.

The module exposes two hashes for different purposes:

1. `plan_id` identifies the complete resolved execution plan, including
   profile, tier cap, deadline, and revalidation policy.
2. `cache_fingerprint` identifies only constraints that affect whether cached
   results mean the same thing. Request-specific execution policy is checked
   separately by a fail-closed cache admission decision.

This separation is required by the accepted scorecard. Hashing too little
allows an ineligible result to cross request boundaries. Hashing every request
field prevents a free-profile request from reusing otherwise-eligible cached
evidence merely because the original request was budgeted, even though a cache
hit initiates no billable call.

The planner is pure and deterministic. It uses no model, network call, vector
store, or semantic query rewrite.

## Context

The current cache key is:

```text
lower(trim(query)) + mode + include_attribution
```

It omits `max_results`, explicit providers, `free_only`, caller tier policy,
and all future freshness/domain controls. The cache stores a mutable
`SearchResponse`; a hit mutates that same object with a new run ID and cached
flag. Cache storage also occurs before the canonical HTTP path durably accepts
the retrieval.

Consequences include:

- semantically different requests can collide;
- policy eligibility cannot be proven on a hit;
- original run and provider/spend evidence are not represented as cache
  evidence;
- a persistence failure can leave a readable cache entry;
- cache hits can mutate the original response object;
- relative freshness and provider contract changes have no deterministic
  invalidation boundary.

The provider drift inventory confirms that current adapters need typed
freshness and domain controls. The scorecard additionally requires:

- cache eligibility for mode, profile, provider policy, and freshness;
- original provider/spend provenance and cache age;
- free-profile reuse of otherwise-eligible cached evidence without a new
  billable call;
- fail-closed handling of provider-restricted or policy-ineligible evidence;
- an explicit timeout within 120 wall-clock seconds;
- no speed reward below that ceiling.

## Considered approaches

### A. Hash the entire request

Hash the serialized `SearchQuery`, all metadata, caller, profile, budget
snapshot, and runtime provider state.

This is collision-safe but wrong for reuse. Volatile balances, health, caller
labels, idempotency scopes, and profile would fragment equivalent evidence.
Unknown metadata would accidentally become public cache behavior. Free and
budgeted requests could not share a valid cached result.

Rejected.

### B. Semantic fingerprint plus admission policy

Hash only normalized result semantics, store immutable origin evidence, and
evaluate current request policy on every hit.

This has a slightly deeper cache implementation, but it matches the
scorecard's distinction between “may this request make a new call?” and “may
this request reuse this already-acquired evidence?” It also keeps runtime
health and budget changes out of content identity while retaining them in the
execution trace.

Accepted.

### C. Cache and fuse provider shards

Cache each provider response independently, then rebuild fusion from whatever
provider shards are eligible for each request.

This offers maximum reuse and re-ranking flexibility. It also changes ranking,
deduplication, persistence, evidence, and cache architecture at once. It is a
broad rewrite and overlaps the ranking/evidence-envelope tickets.

Rejected for version 1.

## Internal interface

The external `SearchQuery` remains unchanged. Internally:

```text
resolve_plan(
  effective_query,
  controls,
  include_attribution,
  policy_snapshot,
  operation_clock,
) -> RetrievalPlan

lookup(plan, cache_policy_snapshot, operation_clock) -> CacheDecision

execute(plan, runtime_health_and_budget_snapshot) -> RetrievalCandidate

accept(plan, request_context, accepted_response) -> AcceptanceReceipt

commit(plan, accepted_response, acceptance_receipt) -> CacheEntry
```

Callers and tests cross the same seam. Provider adapters receive validated
plan controls translated back into their private request shape; they never
receive caller-native HTTP/MCP structures.

`controls` is a typed internal `RetrievalControls` value. Existing callers
receive the defaults below. Benchmark and corpus callers may populate the
typed fields directly. Issue #66 may later map versioned public fields into
this value without creating a second planning path.

`request_context` owns evidence that must be durable but is not plan identity:
the original query, session/refinement lineage, caller and delivery policy,
and the secret-free request metadata allowlist. This keeps the original and
effective queries explicit at the acceptance seam.

`operation_clock` is captured exactly once before planning as an immutable
pair: an aware UTC start instant and a monotonic start value from the same
authority. Planning resolves relative dates from that pair. Later deadline,
age, expiry, and admission checks derive their effective UTC instant as
`utc_started_at + (monotonic_now - monotonic_started_at)`; they never sample a
second wall clock. This preserves elapsed-time correctness across
single-flight waits and prevents wall-clock skew or rollback from making
fingerprinting and admission disagree.

`RetrievalPlan` version 1 contains:

```text
plan_schema_version = 1

semantic:
  normalized_query
  intent
  result_limit
  explicit_providers[]
  freshness:
    requested_relative?
    start_date?
    end_date?
    max_cache_age_seconds
  domains:
    include[]
    exclude[]
  safe_search
  country?
  language?

execution:
  profile
  effective_max_provider_tier
  candidate_providers[]
  deadline_ms
  revalidation
  egress_preference

presentation:
  include_attribution

versions:
  cache_identity_schema_version
  query_normalization_version
  routing_policy_version
  spend_policy_version
  freshness_policy_version
  domain_policy_version
  ranking_policy_version
  result_normalization_version
```

Only typed, allowlisted fields enter the plan. Runtime provider health,
remaining balance, reservation IDs, attempt scope, caller label, and unknown
metadata are evidence inputs, not plan controls.

Every version is a narrow semantic gate, not a release or catch-all policy
counter:

| Version | May change only when |
|---|---|
| `plan_schema_version` | The complete plan shape changes; it affects `plan_id` only |
| `cache_identity_schema_version` | The cache-fingerprint projection shape or scalar encoding changes |
| `query_normalization_version` | Query Unicode/whitespace normalization changes |
| `routing_policy_version` | Static provider eligibility/order or declared control translation changes in a way that can change acquired evidence |
| `spend_policy_version` | Reservation/tier execution eligibility changes; it does not enter cache content identity |
| `freshness_policy_version` | Window resolution, date proof, or freshness admission semantics change |
| `domain_policy_version` | Host normalization, matching, or domain admission semantics change |
| `ranking_policy_version` | Fusion, contribution, tie-break, or diversity semantics change |
| `result_normalization_version` | Stored result field normalization or contributor projection changes |

A version bump requires a named old/new contract vector proving the affected
identity and admission behavior. Provider enablement, credentials, balance,
cooldown, deployment SHA, unrelated configuration, or code cleanup never
bumps these fields. There is no omnibus “policy version.” A semantic version
included in `cache_fingerprint` intentionally invalidates entries produced
under the changed meaning; unchanged component versions preserve them.

Planning validates before hashing: the normalized query must be non-empty,
`result_limit` must be between 1 and 50 inclusive, explicit providers are
deduplicated while preserving order, and the synthetic `cache` provider is
never a valid explicit provider. Any invalid typed value fails as
`invalid_request`; it is never coerced into a broader plan.

Bounds are checked before normalization or deduplication:

- the effective query is at most 16,384 UTF-8 bytes before and after
  normalization;
- the raw explicit-provider list has at most 32 entries, then resolves to at
  most the 14 catalog providers;
- include and exclude domains have at most 64 raw entries combined, including
  duplicates;
- each raw domain is at most 1,024 UTF-8 bytes before IDNA processing and its
  normalized DNS name is at most 253 ASCII bytes;
- each policy/version identifier is printable ASCII and at most 64 bytes.

These are semantic validation limits, not truncation points. Overflow is
`invalid_request` before sorting, hashing, cache lookup, reservation, or
provider execution.

### Effective query

Session refinement happens before planning. The cache fingerprint uses the
effective refined query. The original user query and session identity remain
separate evidence and must not silently replace the effective query in the
durable request record.

### Intent

`SearchMode` maps directly:

| Existing mode | Internal intent |
|---|---|
| `discovery` | `discovery` |
| `grounding` | `grounding` |
| `recovery` | `recovery` |
| `research` | `research` |

Version 1 performs no inferred intent, query decomposition, synthetic follow-up
query, or automatic mode change.

### Freshness

Freshness is either:

- no constraint;
- one relative window: `day`, `week`, `month`, or `year`; or
- inclusive `start_date`/`end_date` ISO dates.

A relative window resolves against `operation_clock` into inclusive UTC dates.
`day` is `[today, today]`, `week` is `[today - 6 days, today]`,
`month` is `[today - 29 days, today]`, and `year` is
`[today - 364 days, today]`. Both the requested relative value and resolved
dates enter the plan. The resolved dates enter the cache fingerprint, so a
relative window naturally rolls forward without a background invalidator.

“Today” is deliberately the UTC calendar date, not the caller, host, or
provider local timezone. Midnight UTC therefore creates a new fingerprint for
relative windows; that churn is required invalidation for a different declared
date range, not premature expiry. Existing callers promise no local-calendar
interpretation. A future caller-timezone control would be a new typed semantic
input with its own compatibility/version decision rather than an ambient locale
lookup.

Invalid dates, `start_date > end_date`, or a relative window combined with
explicit dates are `invalid_request`. Adapters translate supported controls,
but a provider-applied filter is acquisition evidence, not proof about an
individual result. The broker always post-filters on each returned result's
normalized publication date. Every returned result must have a parseable date
inside the inclusive resolved range. An undated or out-of-range result is
removed and recorded. `freshness_unproven` is an internal rejection reason,
never a new surface outcome; absent proof that the provider returned a
genuinely empty freshness-scoped result, it maps to canonical
`providers_failed`. Issue #62 owns the versioned provider-signal rules that
distinguish a strictly filtered empty response from unproven returned evidence.

Default cache age:

- no freshness constraint: at most 604,800 seconds (the existing seven days);
- relative window: the lesser of 604,800 seconds and the window length;
- explicit window ending before today: at most 604,800 seconds;
- explicit window including today or the future: at most 86,400 seconds.

The entry must also remain inside the process/configured cache TTL. A caller
may request a shorter age. It may not exceed these limits.

### Domain constraints

Include and exclude domains are set semantics:

- lowercase;
- IDNA ASCII;
- no scheme, port, path, query, wildcard, or trailing dot;
- duplicates removed;
- sorted for canonicalization;
- overlap is invalid.

For both constraints and results, normalization uses the parsed URL
`hostname`, not `netloc`: lowercase, remove the trailing root dot, then encode
each DNS label with IDNA. Ports and user information therefore never enter the
host. A constraint matches the exact host or a label-boundary descendant:
`host == constraint` or `host.endswith("." + constraint)`. Thus
`example.com` matches `www.example.com` but not `badexample.com`. The broker
does not remove `www`; it follows from the descendant rule. IPv4 and IPv6
literals are invalid domain constraints in version 1. The broker
post-filters every normalized result host, so lack of a provider-native domain
filter is not silent loss. An invalid or missing result host cannot satisfy an
include constraint and is recorded. Provider translation is only an
optimization and must be recorded in the trace.

### Provider and budget policy

`profile` is `free` when `free_only=true`, otherwise `budgeted`.

- A free-plan cache miss exposes only tier-0 execution candidates and may not
  reserve or initiate a billable attempt.
- A budgeted cache miss uses registered providers within configured budget and
  caller caps.
- An explicit provider list is a restriction with current compatibility
  ordering. The planner removes later duplicates, then applies the existing
  stable tier sort: tier 0 before tier 1 before tier 3, preserving caller order
  within each tier. It remains subject to profile, caller-cap, availability,
  and spend gates.
- Current health, cooldown, balance, and reservation state are evaluated at
  execution time and recorded in evidence. They are not cache identity.

The plan records the effective caller tier cap and configured spend-policy
version, not volatile remaining balances.

`candidate_providers` is the static ordered set remaining after explicit
provider restrictions, profile/tier limits, configured organization policy,
and routing policy are resolved. It is fixed before hashing. Volatile
credential presence, health, cooldown, balance, and reservation state only
determine which candidates can execute and are recorded in the run evidence.
They never change `plan_id` after resolution.

### Deadline

The default and maximum operation deadline is 120,000 milliseconds for every
mode and surface. It starts before cache lookup and bounds single-flight wait,
provider execution, normalization/ranking, durable persistence, and response
assembly. A caller may request a shorter internal deadline once a typed
control exists, but never a longer one.

The executor enforces one monotonic outer cancellation scope and a distinct
provider-phase deadline. It reserves the last 5,000 milliseconds for
normalization, durable acceptance, and response assembly; no provider starts
after the provider-phase deadline. Each async adapter must be
cancellation-safe and accept the computed attempt timeout. A blocking adapter
may run only behind a killable worker boundary or a provider-native timeout
proven not to outlive cancellation. Otherwise it is `unready` and is skipped
rather than allowed to escape the operation deadline. Normalization,
persistence timeout, and assembly must fit strictly inside the remaining
reserve. Persistence that cannot finish there returns `persistence_failed`;
no cache entry is published.

“Skipped” is never silent. Every candidate not invoked receives one bounded
`not_attempted` trace entry with a closed reason such as
`unready_unbounded_adapter`, `provider_phase_expired`, `policy_ineligible`, or
`readiness_rejected`. The final outcome and degraded/unready diagnostics derive
from candidate, attempted, succeeded, failed, and not-attempted sets, so callers
cannot mistake an omitted blocking adapter for a provider that responded.

`not_attempted` is an internal trace classification, not a new public status.
Legacy `ProviderTrace` projects it as the existing `status="skipped"` with the
closed, sanitized reason in the existing `error` field. HTTP, MCP, CLI, Python,
and session serializers preserve that projection exactly. Issue #66 may expose
a typed reason in an additive versioned contract; version 1 does not add an
outward enum or field.

Each provider receives:

```text
min(provider configured timeout, provider-phase deadline - monotonic now)
```

The planner permits at most one invocation per provider in version 1.
Provider-private redirects or transport retries must fit inside the same
attempt deadline and be reported. When the operation deadline expires, no new
provider starts and the caller receives explicit timeout evidence.

Latency below 120 seconds does not influence ranking or competitive score.

### Revalidation

Version 1 has two modes:

- `normal`: use an eligible fresh entry, otherwise execute and replace it after
  durable acceptance;
- `force`: bypass lookup, execute, and replace after durable acceptance.

There is no implicit `stale-if-error`, background refresh, or stale-while-
revalidate. The accepted owner preference is visible failure rather than
silently serving evidence that failed the declared freshness policy.

Single-flight is keyed by `(cache_fingerprint, execution_cohort)`, not the
cache fingerprint alone. `execution_cohort` hashes profile, effective tier
cap, candidate providers, organization/spend-policy versions, and egress
policy. Volatile health and balances remain excluded. This prevents a free or
cap-0 caller from joining a paid cap-3 fill. Every follower waits only within
its own remaining deadline and reruns cache admission under its own plan after
the leader publishes. If admission rejects the entry, the follower may start
or join its own policy-valid cohort while time remains. A failed leader does
not publish an entry.

The cohort owns one generation-scoped completion primitive. In a `finally`
path the leader publishes exactly one terminal `FillOutcome`: `published`,
`failed`, `cancelled`, or `deadline_expired`, with the fingerprint and
generation but no reusable transient response. Followers wait on that
primitive only until their own deadline. A non-`published` outcome or a
published outcome with no admissible entry causes the follower to rerun cache
admission exactly once, then atomically become or join a newer generation if
time remains. Generation compare-and-set and the outer deadline prevent a
follower from rejoining the failed generation or spinning indefinitely.

## Evidence trace

Every logical retrieval records one bounded, secret-free planning trace,
whether it hits cache, executes providers, or fails before execution:

```text
plan_id
cache_fingerprint
plan_schema_version
cache_identity_schema_version
effective query hash and declared intent
resolved semantic controls
profile and effective tier cap
candidate providers in order
policy/config version identifiers
deadline_ms and elapsed_ms
cache decision, reason, age, and original run ID
eligible, attempted, succeeded, failed, and skipped providers
per-provider applied controls and bounded latency
configured budget authority
reservation, estimated/actual charge, and reconciliation state
final normalized outcome
durable acceptance receipt
```

The trace contains a hash or bounded normalized value where the raw value is
private. It never contains credentials, authorization material, raw provider
payloads, unsanitized exceptions, or balance/account payloads.

Runtime health, cooldown, and remaining balance appear in the execution
evidence observed for that run; they do not change either deterministic
identity. A cache rejection means “this entry cannot satisfy this plan.” In
`normal` mode the request may continue to live execution if policy and
deadline permit; it is not itself a caller-level `policy_rejected` outcome.

## Deterministic identities

### Canonical scalar rules

- Query: Unicode NFC, strip leading/trailing whitespace, collapse internal
  Unicode whitespace to one ASCII space, preserve case and punctuation.
- Enum: lowercase declared value.
- Date: `YYYY-MM-DD`.
- Boolean and integer: JSON native value; no floats in identity.
- Explicit providers: remove later duplicates, then stable-sort by effective
  tier while preserving caller order within each tier.
- Domain sets: normalize and sort.
- Missing optional value: JSON `null`; never omit conditionally.
- Unknown metadata: excluded.

NFC is deliberately conservative. Case-folding or NFKC can collapse queries
whose operators, quoting, symbols, or identifiers are meaningfully different.

### `plan_id`

`plan_id` is:

```text
sha256(
  UTF8("argus-plan-v1\0") +
  canonical_json(complete RetrievalPlan)
)
```

Canonical JSON sorts object keys, uses compact separators, rejects NaN and
Infinity, and serializes no implementation-specific object strings.

The complete plan includes profile, tier cap, candidate providers, deadline,
revalidation, and egress preference. It excludes wall-clock `deadline_at`,
health/balance snapshots, reservation IDs, caller label, and attempt scope.

Canonicalization happens once inside `resolve_plan`. The immutable plan retains
its precomputed `plan_id`, `cache_fingerprint`, and private canonical semantic
bytes; lookup consumes those values and never re-sorts controls or re-hashes
the plan. Normalized provider/domain collections have strict input caps, so
sorting is bounded. Both SHA-256 digests are derived from the same normalized
object graph, not two independently normalized copies that could drift.

### `cache_fingerprint`

`cache_fingerprint` is:

```text
sha256(
  UTF8("argus-cache-v1\0") +
  canonical_json({
    cache_identity_schema_version,
    normalized_query,
    intent,
    result_limit,
    explicit_providers,
    freshness_resolved_dates,
    include_domains,
    exclude_domains,
    safe_search,
    country,
    language,
    query_normalization_version,
    routing_policy_version,
    freshness_policy_version,
    domain_policy_version,
    ranking_policy_version,
    result_normalization_version
  })
)
```

It intentionally excludes:

- `free` versus `budgeted`;
- caller identity and caller label;
- caller tier cap;
- request-specific maximum cache age;
- outward attribution presentation;
- current provider health, configuration presence, and balance;
- deadline and revalidation mode;
- egress preference;
- attempt/idempotency scope.

Those fields control execution or admission but do not change the meaning of
already-acquired evidence.

`expires_at` uses the configured cache ceiling bounded by the resolved
freshness policy, not a requesting caller's shorter age preference. Lookup
then applies the caller's `max_cache_age_seconds`. This permits a young entry
to satisfy both strict and ordinary callers without weakening either.

The implementation adds `scripts/benchmark-retrieval-plan.py` and checks in
`benchmarks/retrieval-plan-identity-v1.json`. The fixture records the exact
source revision, Python version, runner image/architecture, default case, and
maximum valid case (32 raw provider entries, 64 raw domain entries, and the
maximum query/version sizes). The script performs 1,000 untimed warmups, then
30 independently timed batches of 1,000 plans and reports median and p95
nanoseconds per plan plus allocated bytes.

Blocking deterministic tests assert one normalization pass, two canonical
serializations, two SHA-256 digests during planning, and zero sorting,
canonicalization, or hashing during lookup. Timing comparison is blocking only
when runner image, architecture, and Python minor version match the checked
baseline; a greater-than-two-times median or p95 regression fails that job.
Mismatched runners publish informational measurements but cannot fail on noisy
wall time. Raw string concatenation is not an acceptable fallback because it
loses typed identity.

## Cache entry

A cache entry is an immutable snapshot, never the live `SearchResponse`
instance. It contains:

```text
cache_fingerprint
plan_schema_version
cache_identity_schema_version
stored_at
expires_at
original_search_run_id
original_plan_id
original_profile
original_result_limit
original outcome
normalized results and ranking evidence
per-result contributing provider identities, raw ranks, and canonical contributions
complete original provider traces
provider tiers at acquisition
spend provenance and reconciliation state
freshness controls applied
publication/observation times
domain filtering evidence
acceptance receipt identity
```

Secrets, raw provider payloads, request URLs containing credentials,
authorization headers, and unsanitized exception text are forbidden.

A hit returns a new per-request response projection with:

- a new search run ID;
- `cached=true`;
- original run/plan IDs;
- cache age and decision;
- unchanged original provider/spend/provenance evidence;
- a current zero-new-call accounting record.

The stored results, contributor ranks, and provider traces are frozen values
(or immutable serialized bytes) and may be structurally shared by the
per-request projection. Argus does not deep-copy the complete evidence graph on
every hit. A compatibility surface that requires mutable result objects copies
only its bounded outward result projection, never the full trace. Serializers
are read-only and receive the new run/receipt/accounting overlay separately.
No caller can mutate the stored entry, shared evidence, or original response.

## Fail-closed cache admission

Lookup returns a typed decision:

```text
hit | miss | bypassed | rejected
reason
cache_age_ms?
original_search_run_id?
```

An entry is admitted only when all applicable checks pass:

1. Fingerprint, cache-identity schema, and included semantic component
   versions match. The complete-plan schema is retained for lineage but an
   execution- or presentation-only shape change does not reject reusable
   evidence.
2. The entry is durably accepted and its acceptance identity is present.
3. `now < expires_at` and age is within the plan's maximum cache age.
4. Every result has complete contributing-provider identities, raw ranks,
   canonical contribution values, tier, spend, and provenance evidence; full
   attempted/failed/skipped traces remain available as run evidence but do not
   make those providers result contributors.
5. Explicit provider restrictions match.
6. Organization/provider deny policy and caller tier cap permit every
   contributing provider.
7. Every result has a normalized publication date inside the current resolved
   range when freshness is required.
8. Every result satisfies include/exclude domain constraints.
9. Mandatory contributor evidence can render requested outward attribution.
10. The cached outcome is eligible for caching.

Missing evidence rejects the hit. It never degrades into “probably eligible.”
Contributor mappings are mandatory internal provenance even when outward
attribution is not requested; `include_attribution` controls presentation, not
whether Argus retains evidence.

For a zero-result entry, per-result checks are not treated as vacuous proof.
Admission additionally requires at least one eligible provider with a
successful terminal trace, complete request/response/control evidence for
every successful provider, and an explicit proven-empty classification.
Providers whose rows were all removed as undated or out of range remain
attempted/successful provider evidence but are not result contributors; without
provider-native or post-filter proof that the eligible set is genuinely empty,
the outcome is `freshness_unproven`/`providers_failed` and is not cacheable.

### Free-profile rule

A free-profile request may reuse a paid-origin entry because the hit initiates
no billable call. It must still pass explicit provider, caller-cap,
organization policy, freshness, domain, and provenance checks. The returned
evidence preserves the original paid provenance and records zero new spend.

This is why profile is in `plan_id` but not `cache_fingerprint`.

### Cacheable outcomes

- `success` and `degraded` with above-floor results: cacheable.
- Proven `empty` with at least one eligible successful provider and complete
  traces: cacheable for at most 300 seconds.
- `timeout`, `policy_rejected`, `providers_failed`, `unready`,
  `persistence_failed`, invalid request, and authentication rejection: never
  cacheable.

Until the canonical outcome object lands, the compatibility implementation may
derive these distinctions from complete traces, but it must fail closed when
the distinction is ambiguous.

## Commit ordering and persistence

An entry becomes readable only after acceptance by the configured durable
authority: PostgreSQL in production, or SQLite in standalone/dev/test mode.
An in-memory or disabled persistence configuration may execute but does not
publish reusable cache entries. The required order is:

```text
resolve plan
-> cache decision
-> execute on miss
-> normalize/rank
-> durable authority acceptance
-> atomically publish immutable cache entry
-> acknowledge caller
```

If durable persistence is configured and fails, no cache entry is published
and the retrieval fails visibly. A cache hit is a new logical retrieval and
must itself be durably accepted with its new run ID and cache lineage before
acknowledgment.

Durable acceptance and cache publication have separate explicit commits. The
acceptance transaction commits the run/evidence/receipt first. A publication
transaction then inserts one immutable cache row referencing that receipt and
becomes lookup-visible only at commit; it never exposes a pre-commit row. If a
backend uses a staging row, lookup filters to `publication_state=published`,
and failed/crashed staging is unreachable and reclaimable. Database
transaction isolation and WAL recovery must prove that concurrent readers and
restart recovery see either the complete published row or no row, never a
partial entry. Failure after acceptance therefore retains truthful accepted-run
evidence but no reusable cache entry or in-memory index record.

This changes the current `SearchResultPipeline` ordering without changing the
public transport interfaces. One accepted-operation orchestrator owns the
state machine:

```text
planned -> decided -> executed_or_hit -> durably_accepted
        -> cache_published_or_not_applicable -> acknowledged
```

It completes one request-scoped future and consumes one acknowledgment token.
HTTP, MCP, CLI, Python, and session adapters only present the finalized
accepted operation; they cannot acknowledge a raw candidate, persist again, or
retry after an ambiguous completion. Presenter failure after durable acceptance
records delivery failure against the same run and never creates a second
acceptance or provider execution.

## Invalidation

An entry is rejected or naturally replaced when:

- its expiry or request-specific age limit passes;
- any identity version in the fingerprint changes;
- semantic constraints produce a different fingerprint;
- an explicit provider/caller/organization policy no longer permits its
  evidence;
- required lineage, provenance, spend, freshness, or acceptance evidence is
  missing or invalid;
- the caller forces revalidation.

Do not include release SHA in the key. Unrelated deployments should not flush
valid evidence. Instead, bump the narrow normalization, routing, freshness,
domain, ranking, or result-normalization version whose semantics changed.

Current in-memory legacy entries have no versioned fingerprint or complete
lineage. They are discarded on rollout; no migration is required.

Budget exhaustion, transient health failure, missing current credentials, or a
cooldown do not by themselves invalidate an otherwise-permitted cache hit,
because the hit performs no provider call. A deliberate provider deny policy
does invalidate it.

## Compatibility mapping

Existing callers resolve as follows:

| Current input | Version 1 plan |
|---|---|
| `SearchQuery.query` | NFC/whitespace-normalized query |
| `SearchQuery.mode` | same intent |
| `SearchQuery.max_results` | exact result limit |
| `SearchQuery.providers` | ordered explicit restriction or `null` |
| `SearchQuery.free_only` | `free` or `budgeted` profile |
| `SearchQuery.caller` | tier/spend policy lookup; excluded from cache identity |
| `SearchQuery.user_visible` | delivery/evidence only; excluded from plan and cache identity |
| `compute_attribution` | outward attribution presentation policy |
| `metadata.caller_label` | evidence only |
| `metadata.attempt_scope` | spend idempotency only |
| `metadata.prefer_residential` | resolved execution egress preference before identity/lookup |
| unknown metadata | ignored by planning and identity |

Defaults preserve current public request shapes:

```text
freshness = none
domains = empty
safe_search = unspecified
country = null
language = null
deadline_ms = 120000
revalidation = normal
max_cache_age_seconds = 604800
```

`unspecified` is a canonical no-declared-constraint value that preserves
existing callers. Adapters record their actual provider behavior. If a future
typed control requests `moderate`, `strict`, or `off`, every candidate adapter
must explicitly map that value and record the mapping; an adapter unable to
provide the declared behavior is ineligible. A change to behavior relevant to
reuse bumps the narrow routing policy version.

No new HTTP, MCP, or CLI field is promised here. [Issue
#66](https://github.com/Khamel83/argus/issues/66) owns additive external
versioning and compatibility. Internal benchmark/corpus callers may construct
typed controls first.

## Contract test vectors

The implementation plan must include hermetic tests proving:

| Change | Plan ID | Cache fingerprint |
|---|---|---|
| whitespace-only query difference | same | same |
| query case difference | different | different |
| caller/caller-label difference | different only if policy resolves differently | same |
| attempt scope difference | same | same |
| free versus budgeted profile | different | same |
| caller tier-cap difference | different | same, admission may differ |
| provider health/balance difference | same | same |
| result limit difference | different | different |
| cross-tier explicit-provider permutation | same after stable tier sort | same |
| same-tier explicit-provider order difference | different | different |
| cross-tier explicit providers | tier 0, then 1, then 3 | same resolved order |
| same-tier explicit providers | caller order | same caller order |
| domain input order difference | same | same |
| freshness resolved date difference | different | different |
| attribution presentation difference | different | same |
| shorter deadline | different | same |
| force revalidation | different | same, lookup bypassed |
| execution/presentation-only complete-plan schema bump | different | same |
| cache-identity schema bump | different | different |
| query-normalization version bump | different | different; query semantics changed |
| routing version bump | different | different; acquired evidence semantics changed |
| freshness version bump | different | different; date proof semantics changed |
| domain version bump | different | different; host admission semantics changed |
| ranking version bump | different | different; contribution/order semantics changed |
| result-normalization version bump | different | different; stored projection semantics changed |
| spend-policy version bump | different | same |
| deployment SHA or unrelated config change | same | same |
| unknown metadata difference | same | same |

Admission tests must additionally prove:

- raw provider/domain/query/version bounds reject before normalization,
  deduplication, hashing, cache, reservation, or execution;
- one operation-clock pair drives relative-date resolution, deadline elapsed
  time, and admission age even if the host wall clock jumps;
- relative `day` rolls at midnight UTC and is unaffected by caller/host
  timezone;
- free miss never initiates a billable attempt;
- free hit may reuse paid-origin evidence and reports zero new spend;
- an entry created without outward attribution renders identical attribution
  from stored contributor ranks/contributions on a later attributed hit;
- caller/provider restrictions reject incompatible origin evidence;
- missing provenance or acceptance identity rejects the hit;
- freshness/domain failures reject the hit;
- expired and forced entries are not returned;
- stale evidence is not served after execution failure;
- cached empty is short-lived, requires at least one eligible successful
  provider with complete terminal traces, and cannot pass vacuously after all
  rows are removed as freshness-unproven;
- persistence failure publishes no entry, and concurrent readers/WAL restart
  never observe a staged or partially committed row;
- a hit creates a new immutable response/run without mutating the entry or
  deep-copying the complete immutable evidence graph;
- every cache miss and hit produces exactly one durable acceptance and one
  caller acknowledgment across HTTP, MCP, CLI, Python, and session adapters;
- same-cohort concurrent misses produce at most one live fill;
- policy-divergent cohorts never share an in-flight execution and each
  follower reruns admission;
- failed/cancelled/deadline leaders complete their generation; followers do
  not spin or reuse transient output and may re-admit/join only a newer
  generation within their own deadline;
- a spend-policy version bump changes the execution cohort;
- hanging async, blocking, and slow-persistence fakes terminate with typed
  evidence inside the operation deadline and publish no unsafe cache entry;
- every uninvoked blocking adapter has a bounded visible `not_attempted`
  reason, affects degraded/unready evidence, and serializes through every
  legacy surface as `status="skipped"` plus the existing sanitized error;
- plan canonicalization occurs once, cache lookup does not re-hash, and the
  declared benchmark fixture/protocol distinguishes blocking same-runner
  regressions from informational mismatched-runner results;
- default-off or absent model-proposal support preserves byte-identical plan
  identities and execution behavior;
- all callers receive timeout evidence within 120 seconds.

## Explicit limits

Version 1 does not include:

- an LLM or model-backed planner;
- query decomposition, rewriting, or inferred intent;
- vector retrieval or a vector database;
- provider-shard cache fusion;
- dynamic semantic reranking;
- unbounded retries, pagination, polling, or background refresh;
- automatic budget expansion or credit purchase;
- silent stale fallback;
- public transport changes owned by #66;
- the canonical evidence envelope owned by #65.

Version 1 exposes no runtime model-proposal entry point. A future optional
model may propose typed controls only outside this module after a separate
review adds an explicit default-off feature flag (for example,
`ARGUS_MODEL_PLAN_PROPOSALS=off|allow`). Enabling it may not bypass caller
policy and cannot be inferred from model availability. Proposed controls are
untrusted input: they pass the same deterministic validator,
budget/deadline/spend gates, versioning, and fingerprinting before a plan
exists. The deterministic planner remains required and authoritative; disabled
or unavailable model support preserves identical retrieval behavior.

## Consequences

### Positive

- One internal interface hides planning complexity from all transports.
- Cache identity becomes deterministic and testable.
- Free/budgeted cache reuse follows the scorecard without policy leakage.
- Freshness, domains, budgets, and deadlines become explicit.
- Missing cache evidence fails visibly instead of returning a plausible but
  ineligible answer.
- Version bumps invalidate only changed semantics, not every release.
- The design can be implemented in small, independently testable slices.

### Cost

- Cache entries need more lineage/evidence than `SearchResponse` currently
  carries.
- Durable-acceptance-before-cache ordering requires a narrow orchestration
  change.
- Cache admission is a real policy check rather than a dictionary lookup.
- Legacy in-memory entries are intentionally discarded on rollout.

These costs are localized behind the planner/cache seam and do not require a
public interface change or broad rewrite.
