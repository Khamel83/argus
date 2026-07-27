# Provider health and compatibility probe matrix

- Date: 2026-07-27
- Issue: [#64](https://github.com/Khamel83/argus/issues/64)
- Decision:
  [ADR 0004](../adr/0004-no-spend-provider-readiness.md)
- Static contract source:
  [2026-07-26 provider and extraction drift inventory](2026-07-26-provider-extraction-contract-drift.md)

## Scope and method

This matrix converts the provider-contract inventory into a no-spend
validation policy. It introduces no new live provider observation. No key,
balance endpoint, search endpoint, or extraction endpoint was called while
resolving issue #64.

The matrix separates five kinds of evidence:

| Evidence | Proves | Does not prove |
|---|---|---|
| Constructor/config test | enabled flag and required values have safe shape | key validity, reachability, credit, compatibility, health |
| Mocked request test | adapter emits the expected method, URL, headers, and bounded body | provider accepts it |
| Recorded first-party-shape fixture | adapter normalizes a known response and errors safely | current live shape or account access |
| Contract test | all adapters obey Argus invariants and normalized semantics | current provider availability |
| Explicit live probe | only the scoped live observation named by the probe | other egresses, accounts, request classes, or future health |

All live evidence needs `observed_at`, `expires_at` where applicable,
release/contract identity, egress/machine, the applicable non-secret
configuration, credential-version, and account fingerprints, a safe outcome,
and a durable receipt. Missing fields prevent promotion to health.

## Common fixture contract

Every provider adapter must have hermetic cases for:

1. current successful result shape;
2. valid empty response;
3. malformed HTTP 200 response;
4. invalid request;
5. authentication rejection;
6. permission or policy rejection;
7. rate limit with and without retry/reset metadata;
8. balance/quota exhaustion where the provider documents it;
9. timeout;
10. transient `5xx`;
11. intentional old/new alias migration;
12. bounded request/result references and usage fields; and
13. proof that secrets, raw request URLs, headers, and bodies do not enter
    traces or logs.

Unsupported status cases remain explicit `not_documented`; tests must not
invent provider semantics from another adapter.

The shared adapter contract also checks:

- `is_available()` is a configuration predicate only;
- `max_results` is enforced;
- provider-native shapes do not escape the adapter;
- successful rows have valid HTTP(S) URLs and bounded text;
- HTTP 200 malformed content is `parse_error`, not `empty`;
- an error never returns a successful trace;
- rate/exhaustion classification uses documented status/code fields;
- no fixture test accesses the network; and
- a provider call can occur only after the executor's final policy and spend
  gate.

## Search-provider matrix

`Routine live` means a bounded health refresh, not an ordinary user search.
`Explicit canary` is a separate authorized validation workflow.

| Provider | Required hermetic evidence | Routine live | Explicit canary | Spend/terminal rule |
|---|---|---|---|---|
| SearXNG | JSON success/empty; JSON-disabled or policy `403`; malformed HTML/JSON; engine metadata; timeout/5xx | allow only a declared local component health/metadata endpoint with bounded timeout; never `/search` | bounded public query on declared instance to prove JSON/search capability | no provider fee; cooldown for reachability; JSON-disabled is configuration/compatibility failure |
| DuckDuckGo (`ddgs`) | library result objects; empty; library/transport error; unexpected object fields | deny routine search because it consumes upstream rate allowance and is egress-sensitive | bounded public query on named egress | rate/block evidence enters cooldown; parser drift is compatibility failure |
| Yahoo | representative result markup; explicit no-results markup; consent/block/challenge pages; selector drift; timeout/5xx | deny routine search | bounded public query on named egress | HTTP 200 with unfamiliar markup is `parse_error`, never valid empty |
| GitHub | result success/empty; `401`; primary/secondary `403`/`429`; retry/reset headers; malformed items | allow `/rate_limit` only if current GitHub contract and token scope confirm no search request is consumed; otherwise cached evidence only | anonymous public search by default; scoped token only if private visibility is in scope | rate bucket/cooldown is scoped to auth identity and egress; no money spend |
| WolframAlpha | successful pods; valid no-result `501`; auth/quota error; malformed pods | deny; no routine query is spent to validate AppID or quota | one bounded grounding query after named recurring quota authorization | quota exhaustion lasts to authoritative/documented reset; missing AppID prevents registration |
| Brave | success/empty; `400/401/403/422/429/5xx`; rate headers; malformed web results | only a documented no-search account/rate endpoint on the versioned allowlist; otherwise deny | one bounded search after reservation | `429` cooldown from reset; provider-reported quota exhaustion persists to reset |
| Tavily | success/empty; auth/validation; `429` plus `retry-after`; 5xx; malformed results | deny unless an official no-search account endpoint is allowlisted | one bounded search after reservation | rate limit cooldown; balance/quota evidence must be authoritative |
| Exa | success/empty; `400/401/402/403/422/429/504`; stable tags; usage fields | deny unless an official no-search account endpoint is allowlisted | one bounded search after reservation | documented `402` is terminal exhaustion; `504` is timeout |
| Linkup | current success/empty; auth; validation; rate; exhaustion if documented; malformed results | deny unless an official no-search account endpoint is allowlisted | one bounded search after reservation | unknown charging/error semantics fail closed; do not infer exhaustion from text alone |
| Parallel | current `/v1/search` success/empty; `401/402/408/422/429/5xx`; warnings/usage; old-shape rejection | deny unless an official no-search balance endpoint is allowlisted | one bounded search after reservation and adapter contract repair | documented `402` is terminal exhaustion; the 2026-07-26 insufficient-credit observation is historical until imported with scope |
| Serper | success/empty; auth; documented not-enough-credit response; rate/5xx; malformed organic results | deny unless an official no-search account endpoint is allowlisted | one bounded search after reservation | not-enough-credit is terminal exhaustion; one-time state has no automatic expiry |
| You.com | success/empty; `401/402/403/422/429/5xx`; scopes; malformed hits | deny unless an official no-search account endpoint is allowlisted | one bounded search after reservation | documented `402` is terminal exhaustion; `403` scope failure is policy/configuration, not exhaustion |
| SearchAPI | success/empty; missing key; auth; rate/exhaustion if documented; `organic_results` migration; malformed payload | deny unless an official no-search account endpoint is allowlisted | only after a key, finite budget, reservation, and named authorization exist | current missing-key state prevents registration and any live call |
| Valyu | success/empty; `success:false`; auth; documented insufficient-credit shape; actual/estimated result cost; malformed results | deny unless an official no-search balance endpoint is allowlisted | one bounded search with result-count worst-case reservation | insufficient credit is terminal exhaustion; unknown per-result charge settles uncertain |

The allowlist for a no-spend account endpoint is code-reviewed data, not a
runtime guess. Its entry includes the official contract reference, method and
path template, permitted response fields, charging statement, credential
scope, timeout, and review/expiry date. Redirects to an unlisted path fail
closed.

## Surface matrix

| Surface | Network behavior | Provider invocation | Truthful result |
|---|---|---|---|
| `/live`, `/api/live`, container health | none | none | process event loop responds |
| startup/readiness | local dependencies and cached evidence only | none | required dependency/profile readiness with unknowns preserved |
| `argus doctor` | authority/local checks; optional allowlisted no-spend refresh | never a search | config, compatibility-manifest, reachability/usability/spend observations and expiry |
| `argus health`, `/api/provider-health` | none by default | none | immutable readiness snapshot |
| admin status/dashboard | none by default | none | same snapshot, no reinterpretation |
| default admin smoke | canonical HTTP plus fixture | none | pipeline/transport proof, not universal provider health |
| explicit tier-0 canary | named provider and quota authorization where applicable | one tier-0 call at most | scoped live receipt, not universal provider health |
| paid validation workflow | named provider only | one reserved call at most | durable canary receipt and known/uncertain charge |
| `argus mcp check` | MCP transport and fixture-backed HTTP round trip | none | startup/auth/schema/translation proof |
| explicit MCP live validation | canonical HTTP with immutable `free_only=true` plan, one named tier-0 provider, no fallback, quota authorization, idempotency key, and durable receipt | one invocation at most | scoped MCP-to-HTTP-to-provider proof |
| CLI/Python search | ordinary retrieval policy | only eligible plan entries | real usability evidence as a side effect of caller work |
| logs/traces | none | none | stable normalized category plus evidence reference |
| budget display/API | none by default; optional allowlisted account refresh elsewhere | none | authority, age, reset, estimated and uncertain values separated |

## State derivation examples

| Observations | Derived display | Execution |
|---|---|---|
| enabled + key present, no fixture manifest | `configured; compatibility unknown` | blocked |
| compatible fixture + no live observation | `compatible; usability unknown` | ordinary caller request may be eligible |
| reachable endpoint + no parsed result evidence | `reachable; usability unknown` | not healthy |
| valid live empty response | `usable empty` | eligible; quality/breadth layer decides sufficiency |
| HTTP 200 challenge or unrecognized markup | `unusable parse_error` | blocked/cooldown, compatibility incident |
| recent success, expired TTL | `usability unknown (expired)` | may be caller-probed; not healthy |
| paid `402` exhaustion | `spend exhausted` | terminal skip before reservation |
| rejected key is rotated for the same account | authentication becomes `unknown`; account spend remains unchanged | no call until ordinary eligibility and authorization gates pass |
| key rotates while the same account is exhausted | `spend exhausted`; new credential version is visible separately | terminal skip; rotation never resets balance |
| paid key and budget exist but stable account binding is absent | `missing account binding` | not registered |
| transport lost after paid request | `spend uncertain` | fail closed to configured exposure limit |
| missing SearchAPI key | `missing credential` | not registered |
| paid provider under `free_only` | `policy skipped: free_only` | no reservation, no invocation |

## Cooldown and exhaustion transitions

```text
transient failure
  -> record bounded failure
  -> cooldown
  -> deadline passes
  -> one eligible caller claims half-open
  -> success clears cooldown OR failure reapplies cooldown

balance_exhausted
  -> settle attempt as known or uncertain
  -> persist provider/account exhaustion
  -> block all new reservations
  -> authoritative balance/reset/account change
  -> reconcile durable state
  -> normal eligibility evaluation
```

Background diagnostics do not traverse either half-open arrow. A provider
cannot escape exhaustion through cooldown expiry.

The half-open claim is a database compare-and-set lease scoped to provider,
account/configuration identity, egress, and versioned request class. Database
transaction time governs the lease and each owner receives a monotonically
increasing fencing token. Process-local locks may reduce contention but cannot
authorize a call. Deadline expiry without proven invocation termination becomes
unresolved and blocks replacement; a late stale token cannot settle readiness
or clear cooldown. If exhaustion has no authoritative refresh endpoint, the
provider remains blocked and emits one deduplicated reconciliation alert;
diagnostics never spend a search to clear it.

## Current-gap mapping

The target design deliberately names the present seams:

| Current seam | Gap | Target owner |
|---|---|---|
| `BaseProvider.status()` / `is_available()` | configuration is displayed like readiness | catalog/profile builder plus readiness service |
| `HealthTracker` | process-local success/failure and cooldown have no durable evidence scope | readiness observation repository; tracker remains an invocation primitive during migration |
| `ReachabilityMatrix` | reachability can be inferred separately from adapter compatibility | scoped reachability observation |
| `BudgetTracker` | configured counters can look like provider balance | spend snapshot with explicit authority |
| `ProviderSpendRepository` | durable attempts exist, but terminal provider exhaustion needs a first-class transition | transactional spend/exhaustion state |
| `SearchBroker.refresh_provider_evidence()` | refresh probes free providers and promotes reachability into health | explicit probe authorizer; routine refresh cannot use a search, `billable_search`, or `no_money_quota` |
| operational snapshot rendering | repeated composition can scan growing evidence and disagree across surfaces | transactionally materialized, generation-keyed snapshot with bounded receipt references |
| `/api/provider-health` | effective status collapses dimensions | render readiness snapshot |
| CLI `health`/`doctor` | local path reconstructs and summarizes state independently | canonical HTTP semantics and shared renderer |
| admin smoke/provider smoke | search is easy to mistake for a harmless diagnostic | split fixture/tier-0 smoke from named paid validation workflow |
| `mcp check` | transport validation must not imply provider health | fixture-backed MCP/HTTP check; separate named-provider live workflow |
| adapter exception strings | may leak query-string credentials and erase error categories | provider-private safe classifier |

## Promotion evidence

A no-spend health implementation is promotable only when a frozen test run
proves:

- all 14 adapters satisfy the common fixture contract;
- no routine diagnostic invokes an adapter search method;
- every interface renders the same snapshot semantics;
- `free_only` cannot reserve or invoke tier greater than zero under explicit
  selection, fallback, cache reuse, concurrency, or restart;
- exhaustion survives restart and blocks concurrent attempts;
- cooldown permits at most one caller-owned half-open attempt across processes;
- unknown charge cannot be rendered as zero;
- fixture and live evidence expiry is deterministic under an injected clock;
- repository time defeats producer clock skew and implausible reset times fail
  closed;
- `valid_until` expires every materialized observation, including
  non-decision evidence, without an intervening write;
- lease expiry cannot overlap an unproved original invocation, and stale
  fencing tokens cannot settle readiness;
- repeated observations compact into bounded snapshots and receipt references;
- more than eight egresses, four executable request classes, 32 active scopes,
  or 32 protected receipt references fails closed with explicit overflow;
- exhaustion without an account endpoint remains blocked and emits one
  deduplicated operator alert;
- no migration stage permits legacy and readiness stores to authorize
  independently;
- malformed HTTP 200 responses cannot become valid empty/healthy;
- error output contains no fixture secrets; and
- the evidence bundle identifies release, policy, contract fixtures, and
  test results.

Live paid canaries are not a prerequisite for merging the no-spend machinery.
They are a separately authorized deployment-validation artifact.
