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
  include_attribution,
  policy_snapshot,
  utc_clock,
) -> RetrievalPlan

lookup(plan, cache_policy_snapshot, utc_clock) -> CacheDecision

execute(plan, runtime_health_and_budget_snapshot) -> RetrievalCandidate

commit(plan, accepted_response, acceptance_receipt) -> CacheEntry
```

Callers and tests cross the same seam. Provider adapters receive validated
plan controls translated back into their private request shape; they never
receive caller-native HTTP/MCP structures.

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
  include_attribution

execution:
  profile
  effective_max_provider_tier
  candidate_providers[]
  routing_policy_version
  deadline_ms
  revalidation
  egress_preference

versions:
  query_normalization_version
  routing_policy_version
  ranking_policy_version
  result_normalization_version
```

Only typed, allowlisted fields enter the plan. Runtime provider health,
remaining balance, reservation IDs, attempt scope, caller label, and unknown
metadata are evidence inputs, not plan controls.

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

A relative window resolves against an injected UTC clock into explicit dates.
Both the requested relative value and resolved dates enter the plan. The
resolved dates enter the cache fingerprint, so a relative window naturally
rolls forward without a background invalidator.

Invalid dates, `start_date > end_date`, or a relative window combined with
explicit dates are `invalid_request`. Adapters translate supported controls.
The broker always post-filters normalized publication dates when available and
records whether the provider, broker, both, or neither could prove the
constraint. A required freshness constraint with neither provider-applied
evidence nor result-level publication evidence is ineligible, not silently
accepted.

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

The broker post-filters normalized result domains, so lack of a provider-native
domain filter is not silent loss. Provider translation is an optimization and
must be recorded in the trace.

### Provider and budget policy

`profile` is `free` when `free_only=true`, otherwise `budgeted`.

- A free-plan cache miss exposes only tier-0 execution candidates and may not
  reserve or initiate a billable attempt.
- A budgeted cache miss uses registered providers within configured budget and
  caller caps.
- An explicit provider list preserves caller order and remains subject to
  profile, caller-cap, availability, and spend gates.
- Current health, cooldown, balance, and reservation state are evaluated at
  execution time and recorded in evidence. They are not cache identity.

The plan records the effective caller tier cap and configured spend-policy
version, not volatile remaining balances.

### Deadline

The default and maximum operation deadline is 120,000 milliseconds for every
mode and surface. A caller may request a shorter internal deadline once a
typed control exists, but never a longer one.

Each provider receives:

```text
min(provider configured timeout, remaining operation deadline)
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

One single-flight fill per cache fingerprint prevents concurrent misses from
duplicating paid attempts. Followers wait only within their own remaining
deadline. A failed leader does not publish an entry.

## Evidence trace

Every logical retrieval records one bounded, secret-free planning trace,
whether it hits cache, executes providers, or fails before execution:

```text
plan_id
cache_fingerprint
plan_schema_version
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
- Explicit providers: preserve caller order, remove later duplicates.
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

### `cache_fingerprint`

`cache_fingerprint` is:

```text
sha256(
  UTF8("argus-cache-v1\0") +
  canonical_json({
    plan_schema_version,
    normalized_query,
    intent,
    result_limit,
    explicit_providers,
    freshness_resolved_dates,
    freshness_max_cache_age_seconds,
    include_domains,
    exclude_domains,
    safe_search,
    country,
    language,
    include_attribution,
    query_normalization_version,
    routing_policy_version,
    ranking_policy_version,
    result_normalization_version
  })
)
```

It intentionally excludes:

- `free` versus `budgeted`;
- caller identity and caller label;
- caller tier cap;
- current provider health, configuration presence, and balance;
- deadline and revalidation mode;
- egress preference;
- attempt/idempotency scope.

Those fields control execution or admission but do not change the meaning of
already-acquired evidence.

## Cache entry

A cache entry is an immutable snapshot, never the live `SearchResponse`
instance. It contains:

```text
cache_fingerprint
plan_schema_version
stored_at
expires_at
original_search_run_id
original_plan_id
original_profile
original_result_limit
original outcome
normalized results and ranking evidence
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

A hit returns a deep copy/new response with:

- a new search run ID;
- `cached=true`;
- original run/plan IDs;
- cache age and decision;
- unchanged original provider/spend/provenance evidence;
- a current zero-new-call accounting record.

It never mutates the stored entry or original response.

## Fail-closed cache admission

Lookup returns a typed decision:

```text
hit | miss | bypassed | rejected
reason
cache_age_ms?
original_search_run_id?
```

An entry is admitted only when all applicable checks pass:

1. Fingerprint and schema/contract versions match.
2. The entry is durably accepted and its acceptance identity is present.
3. `now < expires_at` and age is within the plan's maximum cache age.
4. Original provider, tier, spend, and provenance evidence is complete.
5. Explicit provider restrictions match.
6. Organization/provider deny policy and caller tier cap permit the original
   provider evidence.
7. Required freshness is proven by provider-applied controls or normalized
   publication evidence evaluated against the current resolved dates.
8. Every result satisfies include/exclude domain constraints.
9. Requested attribution is present.
10. The cached outcome is eligible for caching.

Missing evidence rejects the hit. It never degrades into “probably eligible.”

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

An entry becomes readable only after PostgreSQL durable acceptance succeeds.
The required order is:

```text
resolve plan
-> cache decision
-> execute on miss
-> normalize/rank
-> durable PostgreSQL acceptance
-> atomically publish immutable cache entry
-> acknowledge caller
```

If persistence fails, no cache entry is published. A cache hit is a new logical
retrieval and must itself be durably accepted with its new run ID and cache
lineage before acknowledgment.

This changes the current `SearchResultPipeline` ordering without changing the
public transport interfaces.

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
valid evidence. Instead, bump the narrow normalization/routing/ranking version
whose semantics changed.

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
| `compute_attribution` | semantic attribution requirement |
| `metadata.caller_label` | evidence only |
| `metadata.attempt_scope` | spend idempotency only |
| `metadata.prefer_residential` | execution egress preference only |
| unknown metadata | ignored by planning and identity |

Defaults preserve current public request shapes:

```text
freshness = none
domains = empty
safe_search = provider_default
country = null
language = null
deadline_ms = 120000
revalidation = normal
max_cache_age_seconds = 604800
```

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
| explicit provider order difference | different | different |
| domain input order difference | same | same |
| freshness resolved date difference | different | different |
| attribution difference | different | different |
| shorter deadline | different | same |
| force revalidation | different | same, lookup bypassed |
| routing/ranking/normalization version bump | different | different |
| unknown metadata difference | same | same |

Admission tests must additionally prove:

- free miss never initiates a billable attempt;
- free hit may reuse paid-origin evidence and reports zero new spend;
- caller/provider restrictions reject incompatible origin evidence;
- missing provenance or acceptance identity rejects the hit;
- freshness/domain failures reject the hit;
- expired and forced entries are not returned;
- stale evidence is not served after execution failure;
- cached empty is short-lived and requires a successful-provider trace;
- persistence failure publishes no entry;
- a hit creates a new immutable response/run without mutating the entry;
- concurrent misses produce at most one live fill per fingerprint;
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

A future optional model may propose a typed plan only outside this module. Its
output must pass the same deterministic validator, budget/deadline gates, and
fingerprinting. The deterministic planner remains required and authoritative;
model availability can never be required for retrieval.

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
