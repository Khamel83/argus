# Extraction outcome compatibility matrix

- Date: 2026-07-27
- Issue: [#63](https://github.com/Khamel83/argus/issues/63)
- Decision:
  [ADR 0005](../adr/0005-structured-extraction-outcome-composition.md)
- Implementation-adjacent source:
  [issue #57 / PR #74](https://github.com/Khamel83/argus/pull/74)

## Scope and method

This is a static audit of current Argus code, the accepted scorecard and drift
inventory, and PR #74. No URL, provider, extractor, production authority, or
historical Atlas record was called or replayed.

The matrix distinguishes:

- current behavior on the #77 design base;
- the additive #57 behavior in PR #74; and
- the semantic target fixed by ADR 0005.

Issue #66 owns exact transport versioning and status/tool-error shapes. Issue
#65 owns the throwaway combined envelope.

## Current source facts

### Extraction result

`ExtractedContent` currently mixes artifact fields, raw-ish `error`, quality,
completeness, fallback history, cache state, provenance, and cost. Quality
defaults to `True`; completeness is optional. That shape is useful inside the
extractor but is not a canonical accepted outcome.

`ExtractionAttempt` currently has:

```text
extractor
status
latency_ms
failure_summary
```

Status and failure summary are strings. The fallback orchestrator can mark
success, failed, or quality-failed and retains a best quality-passing
incomplete result or a best quality-failing result.

### PR #74

PR #74 adds:

- eight stable rejection codes;
- four recommended actions;
- a bounded public rejection object;
- additive HTTP `rejection`;
- the same projection in extraction artifact metadata; and
- compatibility tests that keep derived rejection out of legacy acceptance
  fingerprints.

It is intentionally implementation-adjacent. The integration target still
needs to remove duplicate route/repository classification, replace fuzzy
error-marker inference with typed facts, make missing evidence fail closed,
and add plan/composition/cache semantics.

### Current callers

| Caller | Current behavior | Integration gap |
|---|---|---|
| HTTP `/api/extract` | returns `ExtractResponse`; uncaught execution/persistence becomes generic 503 | final outcome is absent; PR #74 adds rejection but route classifies separately |
| MCP over HTTP | renders response as Markdown; treats truthy `error` as failure prose | rejection, disposition, action, run ID, and degraded state disappear |
| local MCP | calls extractor directly and renders raw error prose | bypasses HTTP/persistence semantics in development |
| production CLI | calls HTTP; truthy `error` becomes generic `Extraction failed` | stable rejection and degraded state disappear |
| local CLI | calls extractor directly; JSON omits quality/completeness/attempt/cache evidence | diverges from production authority |
| persistence | derives `failed` only from `error and not text`; stores attempts/artifact metadata | quality-failing text can be stored as succeeded; PR #74 recomputes rejection |
| extraction cache | URL-only, seven-day in-memory positive cache; stores any no-error result | quality-failing or policy-ineligible text can be reused and origin contract is incomplete |
| workflows | several paths catch/continue on error or empty text; one path can create an empty document | selected failures disappear; citation eligibility is not tied to quality/completeness |

## Canonical single-extraction matrix

| Final source facts | Canonical outcome | Disposition | #57 rejection | Citation/document eligibility |
|---|---|---|---|---|
| quality true, complete true, durable | `success` | `usable` | null | yes |
| quality true, complete false, partial allowed, durable | `degraded` | `partial` | `incomplete_content` | yes, visibly partial |
| quality true, complete false, partial forbidden | `extraction_failed` | `diagnostic_only` | `incomplete_content` | no |
| quality true, completeness missing | `extraction_failed` | `diagnostic_only` | `incomplete_content`, null completeness | no |
| quality false or missing with text | `extraction_failed` | `diagnostic_only` | `quality_gate_failed` | no |
| valid empty attempt after chain | `extraction_failed` | `none` | `empty_result` | no |
| parse normalization caused final failure | `extraction_failed` | `none` or `diagnostic_only` | `parse_error` | no |
| provider/domain rate limit leaves no currently eligible path | `unready` | `none` or `diagnostic_only` | `rate_limited` | no |
| full operation deadline | `timeout` | `none` or `diagnostic_only` | `timeout` | no |
| unsafe or unsupported preflight | `policy_rejected` | `none` | `unsupported_source` | no |
| no eligible path before invocation | `unready` | `none` | `provider_unavailable` | no |
| eligible paths attempted, none accepted, and no terminal readiness/policy condition applies | `extraction_failed` | `none` or `diagnostic_only` | causative stable code | no |
| accepted projection could not persist | `persistence_failed` | not accepted | none fabricated | no |

An early failed attempt never determines final rejection after a later accepted
artifact. The typed step trace preserves it.

## Attempt and fallback matrix

| Event | Step trace | Final effect |
|---|---|---|
| cache miss | cache decision only | continue; no attempt increment |
| eligible extractor invoked | `decision=invoked` plus one closed `attempt_outcome` | contributes to attempt summary |
| extractor skipped by policy/config | `decision=policy_skipped` | visible, not an invocation |
| complete artifact on first extractor | one `content` attempt plus complete artifact evaluation | `success` |
| timeout then complete fallback | `timeout`, then `content` plus complete artifact evaluation | `success`, rejection null |
| quality failure then complete fallback | first `content` attempt with rejected artifact evaluation, then `content` with accepted evaluation | `success`, rejection null |
| incomplete artifact then complete fallback | first `content` attempt with incomplete artifact evaluation, then accepted complete evaluation | `success` |
| incomplete artifact, fallbacks exhausted, partial allowed | all steps retained | `degraded/incomplete_content` |
| every path empty/failed | all evaluated steps retained | hard failure with causative rejection |
| one attempt is stopped by an explicit terminal policy rule | `terminal_cause=attempt_terminal` plus ordinal and policy ref | map that closed attempt outcome |
| multiple homogeneous failures exhaust the chain | `terminal_cause=chain_exhausted`; all ordinals and one distinct outcome | map the homogeneous code with provider null |
| heterogeneous failures exhaust the chain | `terminal_cause=chain_exhausted`; all ordinals and distinct outcomes | `extraction_failed/provider_unavailable` with provider null; no marker-order guess |
| operation deadline before plan exhausted | evaluated steps retained; deadline fact | `timeout`; no hidden later fallback |
| fallback hint but no eligible step remains | mapper cannot emit `fallback_provider` | terminal or bounded retry guidance |

## Rejection and action matrix

| Rejection | Default action | Automatic behavior |
|---|---|---|
| `quality_gate_failed` | `terminal` | no retry of same artifact |
| `incomplete_content` | `retry_later` only when plan permits another bounded generation; otherwise `terminal` | no unbounded full-fetch loop |
| `provider_unavailable` | `retry_later` when readiness/reset evidence exists; otherwise `terminal` | caller-owned retry only |
| `timeout` | `retry_later` within deadline/retry budget | no transport-owned retry |
| `parse_error` | `fallback_provider` only when an eligible fallback remains | otherwise terminal/bounded retry |
| `unsupported_source` | `terminal` | never bypass SSRF or source policy |
| `rate_limited` | `retry_later` at authoritative reset | no probe to clear it |
| `empty_result` | `fallback_provider` only when an eligible fallback remains | empty is not accepted content |
| old `manual_review` | compatibility-read only | autonomous profile treats as terminal; no owner queue |

## Search/extraction composition matrix

Search evidence remains immutable.

| Retrieval/artifact state | Composite outcome | Search results | Content/citations |
|---|---|---|---|
| search only | retrieval outcome | unchanged | not requested |
| search empty or terminal before result selection | retrieval outcome unchanged | unchanged | no extraction attempted |
| search success, every selected extraction usable and required floor met | `success` | unchanged | accepted |
| search degraded, artifact floor met | `degraded` | unchanged | accepted, retrieval degradation visible |
| artifact floor met with allowed partial | `degraded` | unchanged | partial labels required |
| artifact floor met but a selected candidate rejected | `degraded` | unchanged, rejection link retained | only accepted artifacts |
| artifact floor not met | `extraction_failed` | retained as retrieval evidence | accepted artifact/citation refs may remain diagnostic; no synthesized answer or delivery |
| no eligible extraction path | `unready` | retained | none |
| any selected link or outcome cannot be durably accepted | `persistence_failed` | retained | no unaccepted artifact |
| required cluster has no eligible path, or aggregate floor is impossible after excluding unavailable selections | `unready` | retained | only already accepted diagnostic references |

Rejected text never replaces a search snippet or ranking evidence. The
composer links outcomes; it does not mutate provider fusion.

Per-result requirements and the aggregate floor are independent. Every
required cluster must meet its declared minimum disposition before the
aggregate count is considered. `partial` can satisfy only a floor whose
minimum is `partial`; it never counts as `usable`.
Citation eligibility is evaluated per accepted artifact; aggregate-floor
failure does not relabel a usable artifact as unsafe. Composite delivery is a
separate gate: a below-floor operation cannot produce an accepted synthesized
answer, report, summary, or external delivery.

## Cache matrix

| Candidate | Cache write | Cache reuse |
|---|---|---|
| complete usable public artifact | yes | only while URL/mode/plan/quality/completeness/access policy remains eligible |
| partial artifact | yes only under explicit partial policy | cannot satisfy a plan that requires complete content |
| quality-failed diagnostic text | no | never |
| completeness-unproven text | no | never |
| empty/rejected result | no negative cache in version 1 | never inferred as provider health |
| authenticated/cookie artifact | scoped | same account/access scope only |
| origin policy version changed | retain origin evidence | re-evaluate from stored facts or miss; never silently upgrade |
| cache hit | new acceptance receipt plus origin reference | cache decision is not an extractor attempt |

## Surface semantic equivalence

Until issue #66 selects exact transport shapes, representative fixtures must
preserve:

| Semantic fact | HTTP | MCP | CLI | Python | Persistence |
|---|---|---|---|---|---|
| canonical outcome | required | required | required | typed | stored |
| artifact disposition | required | required | visible | typed | stored |
| run identity | required when created | required | visible | typed | stored |
| #57 rejection/action | object/null | structured + readable | visible + JSON | typed | identical object |
| quality/completeness | additive current fields | preserve | preserve | typed | source facts |
| attempt summary | bounded | preserve | preserve | typed | stored |
| full typed trace | reference | reference | optional reference | typed/ref | authoritative |
| cache origin/age | required on hit | preserve | preserve | typed | stored |
| provenance/spend | required when applicable | preserve | preserve | typed | stored |
| durable acceptance | required before success/degraded | preserve | preserve | receipt | authoritative |

Legacy success fields and PR #74's nullable rejection remain additive. Exact
HTTP statuses, MCP errors, structured content, and CLI exit codes remain #66's
decision.

## Privacy matrix

| Evidence | Public rejection | Accepted artifact/authorized response | Durable private trace |
|---|---|---|---|
| rejection code/action | yes | yes | yes |
| allowlisted causative provider/status | bounded | yes | yes |
| URL/title/content | never | according to authorized artifact response | redacted/private policy |
| raw error/body/header | never | never | never; normalized fact only |
| credential/cookie/token | never | never | never |
| quality/completeness signals | bounded outside rejection | yes | yes |
| per-step normalized category | via trace ref | optional bounded | yes |
| request/provider reference | opaque bounded ref | optional | yes |
| latency/cost | bounded summary | yes | typed authority | yes |

The rejection object's privacy guarantee does not authorize broader exposure
from the extraction artifact. Conversely, an authorized URL in an artifact
does not permit copying it into rejection/log text.

## Compatibility and migration

1. Merge PR #74 as the additive rejection source of truth.
2. Introduce `ExtractionFinalizer` and typed attempt facts; route and
   persistence consume its one accepted projection.
3. Record projection policy version while preserving legacy acceptance
   fingerprints and run IDs.
4. Migrate workflows from silent `continue` paths to durable result links and
   artifact-floor composition.
5. Let issue #65 prototype the combined fixture envelope.
6. Let issue #66 version transport/status/structured-content behavior.
7. Remove marker-based and per-surface status reconstruction.

No migration step replays historical extraction requests or rewrites accepted
legacy rows.

## Required fixture set

The minimum hermetic set includes:

1. direct complete success;
2. timeout then fallback success;
3. quality failure then fallback success;
4. accepted incomplete artifact;
5. incomplete artifact forbidden by policy;
6. quality-unproven diagnostic text;
7. completeness missing;
8. valid empty;
9. parse failure;
10. rate limit;
11. unsafe/unsupported source;
12. no eligible extractor;
13. all eligible extractors failed;
14. operation deadline;
15. persistence failure;
16. public cache hit;
17. authenticated cache isolation;
18. stale/policy-ineligible cache;
19. selected cross-result partial failure with floor met;
20. artifact floor failure;
21. idempotent retry and conflict; and
22. privacy/secret-injection attempts in every bounded label and error source.

The same fixtures drive Python and HTTP semantics. MCP and CLI use captured
HTTP projections; they do not repeat extraction.
