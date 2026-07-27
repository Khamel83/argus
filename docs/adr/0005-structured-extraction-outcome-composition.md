# ADR 0005: Structured extraction outcome composition

- Status: Accepted
- Date: 2026-07-27
- Issue: [#63](https://github.com/Khamel83/argus/issues/63)
- Parent: [#58](https://github.com/Khamel83/argus/issues/58)
- Depends on:
  [the stability scorecard](../scorecards/stability-competitive.md),
  [the provider/extraction drift inventory](../research/2026-07-26-provider-extraction-contract-drift.md),
  [the extraction compatibility matrix](../research/2026-07-27-extraction-outcome-compatibility-matrix.md),
  [ADR 0002](0002-bounded-retrieval-plan-cache-identity.md),
  [ADR 0003](0003-provider-aware-freshness-provenance-ranking.md),
  and
  [issue #57 / PR #74](https://github.com/Khamel83/argus/pull/74)

## Decision

Argus will finalize every extraction exactly once into a typed,
privacy-bounded `AcceptedExtractionOutcome`. The finalization owns:

1. the normalized extraction plan and step trace;
2. the selected artifact and its quality/completeness evidence;
3. the stable rejection object owned by issue #57;
4. one canonical outcome and artifact disposition;
5. cache origin and eligibility;
6. extractor/provider attribution and provenance; and
7. durable acceptance before any success or degraded result is returned.

Issue #57 remains the only owner of public rejection codes and
`recommended_action`. This decision does not create a second classifier.
Instead, one deep `ExtractionFinalizer` module invokes the #57 mapper once with
typed facts and orchestration context, persists that exact projection with the
source facts, and returns the same accepted object to callers.

Search ranking and extraction remain separate stages. A search result is never
removed, re-ranked, or rewritten because extraction later failed. When a
caller or workflow explicitly asks for extracted artifacts, Argus records a
stable link from each selected result cluster to its extraction outcome. A
pure `RetrievalEvidenceComposer` then applies the declared artifact
requirement and decides whether the composite operation is successful,
degraded, or failed. Search alone never triggers hidden extraction.

Rejected or quality-unproven text is diagnostic evidence, not usable content.
It cannot become a stored source document, a citation, a summary input, a
ranking signal, or a positive cache entry. Quality-passing but proven
incomplete text is usable only when the caller's immutable plan explicitly
allows partial artifacts; it stays visibly `degraded`.

The exact versioned HTTP status/envelope and MCP structured-content format are
owned by [issue #66](https://github.com/Khamel83/argus/issues/66). The exact
combined JSON/test-vector shape is owned by
[issue #65](https://github.com/Khamel83/argus/issues/65). This decision fixes
the semantics those tickets must preserve.

## Why this approach

Three approaches were considered.

### A. Let every surface interpret `ExtractedContent`

HTTP, MCP, CLI, workflows, and persistence could each inspect `error`,
`quality_passed`, completeness, and attempts and choose their own response.

This matches the current shape, but it already produces drift: HTTP returns a
large response, MCP converts errors to prose, CLI has separate local and HTTP
branches, persistence derives its own status, and workflows silently skip or
store different subsets. Recomputing issue #57's rejection in both the route
and repository can also produce a returned object that differs from the
durable one.

Rejected. It is shallow duplication at every caller.

### B. Put mutable rejection state on search results

Extraction could mutate each `SearchResult`, replace snippets with extracted
text, and let ranking/workflows infer the final state from that combined
object.

This tangles acquisition, ranking, extraction, caching, and citation policy.
It would make a later extraction failure rewrite otherwise valid retrieval
evidence and could introduce hidden network calls into ranking.

Rejected. Search evidence remains immutable and extraction is linked.

### C. Finalize once, link outcomes, compose against a declared floor

Use one finalizer to normalize and durably accept each extraction. Link those
accepted outcomes to immutable result clusters. Use one pure composer to
evaluate an explicit artifact requirement without reclassifying attempts or
mutating search.

Accepted. It gives all surfaces the same facts, keeps #57 authoritative,
preserves locality, and makes partial or rejected evidence visible without
contaminating usable content.

## Deep modules and seams

### Extraction finalization

Callers cross one interface:

```text
finalize_extraction(
  extraction_request,
  extraction_plan,
  raw_extractor_result,
  outcome_policy,
) -> AcceptedExtractionOutcome
```

`ExtractionFinalizer` receives an injected #57 rejection adapter, durable
repository, and authority clock. Its implementation:

1. validates the typed step trace and bounds;
2. projects quality and completeness without truthiness coercion;
3. invokes the #57 rejection mapper exactly once when needed;
4. chooses the canonical outcome and artifact disposition;
5. validates cache/provenance/spend evidence;
6. persists request, plan, facts, projection, trace, and artifact atomically;
   and
7. returns the accepted object with its receipt.

Production has no adapter that skips durable acceptance. An isolated
development repository may implement the same interface, but its acceptance
scope is visibly non-production.

The route, MCP adapter, CLI, Python caller, workflow, and persistence code do
not call `classify_extraction_rejection()` or reconstruct final status.

### Retrieval evidence composition

Callers cross a second, pure interface:

```text
compose_retrieval_evidence(
  accepted_retrieval,
  result_extraction_links,
  artifact_requirement,
) -> RetrievalComposition
```

`RetrievalEvidenceComposer` does not inspect raw errors or classify provider
facts. It counts accepted artifact dispositions, preserves every link, applies
the declared floor, and returns:

```text
retrieval_outcome
artifact_outcome
composite_outcome
accepted_artifact_refs[]
degraded_artifact_refs[]
rejected_extraction_refs[]
composition_trace
```

The composer is used only when a plan declares an artifact requirement. Plain
search returns the accepted retrieval unchanged.

## Stable semantic projection

The target semantic projection contains these required facts. Issue #65 may
choose a smaller/deeper physical nesting but cannot omit them:

```text
contract_version
outcome
artifact_disposition
extraction_run_id
plan_ref
artifact?
rejection?
attempt_summary
terminal_cause_ref?
trace_ref
cache_evidence
provenance
spend_evidence
acceptance_receipt
```

The projection is immutable after acceptance. A display renderer may hide
optional detail, but cannot change `outcome`, `artifact_disposition`,
rejection code/action, run identity, or acceptance state.

### Canonical outcomes

Extraction uses the scorecard vocabulary:

```text
success
degraded
invalid_request
authentication_rejected
policy_rejected
timeout
persistence_failed
extraction_failed
unready
```

`empty` is a valid search outcome, not a successful extraction outcome. Empty
extraction is `extraction_failed` plus `rejection.code=empty_result`.
`providers_failed` belongs to search. Authentication and request validation
that fail before an extraction run do not manufacture a rejection object or
run ID.

### Artifact disposition

```text
usable
partial
diagnostic_only
none
```

- `usable` content passed quality and is proven complete.
- `partial` content passed quality, is proven incomplete, and the immutable
  plan permits partial use.
- `diagnostic_only` content exists but did not prove the required quality or
  completeness contract.
- `none` means there is no retained text artifact.

Only `usable`, and explicitly allowed `partial`, may cross the source-document
or citation seam.

## Quality and completeness truth table

Quality and completeness are tri-state evidence. `None` means unproven and is
never coerced into success.

| Text/evidence | Plan | Outcome | Disposition | Rejection |
|---|---|---|---|---|
| non-empty, `quality_passed is True`, `is_complete is True` | any eligible | `success` | `usable` | null |
| non-empty, `quality_passed is True`, `is_complete is False` | partial allowed | `degraded` | `partial` | `incomplete_content` |
| non-empty, `quality_passed is True`, `is_complete is False` | partial forbidden | `extraction_failed` | `diagnostic_only` | `incomplete_content` |
| non-empty, `quality_passed is True`, completeness missing | any | `extraction_failed` | `diagnostic_only` | `incomplete_content`, `is_complete=null` |
| non-empty, quality false or missing | any | `extraction_failed` | `diagnostic_only` | `quality_gate_failed` |
| empty after an otherwise valid attempt | any | `extraction_failed` | `none` | `empty_result` |
| operation deadline caused final failure | any | `timeout` | `none` or `diagnostic_only` | `timeout` |
| unsafe/unsupported preflight | any | `policy_rejected` | `none` | `unsupported_source` |
| no eligible extractor path before invocation | any | `unready` | `none` | `provider_unavailable` |
| eligible chain ran but produced no accepted artifact | any | `extraction_failed` | `none` or `diagnostic_only` | causative #57 code |
| durable acceptance failed | any | `persistence_failed` | not returned as accepted | no fabricated accepted rejection |

For compatibility, `incomplete_content` means "completeness was not proven":
`is_complete=false` means proven incomplete; `is_complete=null` means missing
required evidence. `quality_gate_failed` likewise means quality was not proven:
the `quality_passed` field distinguishes false from missing.

Completeness evidence retains assessment version, confidence, truncation type,
bounded signals, and the legacy assessment hint. The old top-level
`recommended_action` (`use_as_is` or `try_full_fetch`) remains a deprecated
completeness alias. It is not the same vocabulary or authority as
`rejection.recommended_action`.

## Rejection ownership and classification

Issue #57's stable codes remain:

```text
quality_gate_failed
incomplete_content
provider_unavailable
timeout
parse_error
unsupported_source
rate_limited
empty_result
```

The mapper consumes typed normalized facts from the provider/extractor seam.
It does not search raw exception strings for marker text. The current
marker-based implementation in PR #74 is a compatibility step, not the target
classifier.

Classification order is explicit:

1. if an accepted complete artifact exists, rejection is null regardless of
   earlier fallback failures;
2. if an accepted partial artifact exists, rejection is
   `incomplete_content`;
3. unsafe/unsupported preflight is `unsupported_source`;
4. an operation-wide deadline is `timeout`;
5. a retained but quality-unproven artifact is `quality_gate_failed`;
6. a quality-passing artifact without proven completeness is
   `incomplete_content`;
7. otherwise choose the typed causative terminal attempt category:
   `rate_limited`, `timeout`, `parse_error`, `empty_result`, or
   `provider_unavailable`; and
8. an unknown terminal provider failure fails closed as
   `provider_unavailable`, with its unknown category retained privately.

An early timeout, empty result, or parse failure followed by a complete
fallback is visible in the step trace but never becomes the final rejection.

The orchestrator supplies `terminal_cause_ref`; the mapper never guesses it by
searching all failures. Preflight policy and the operation deadline are
intrinsic terminal causes. Otherwise the cause is the plan-stopping attempt
after which no eligible bounded fallback remained. If the chain merely
exhausted heterogeneous independent failures and no single attempt stopped it,
the terminal fact is `chain_exhausted`, which maps to
`provider_unavailable`; every individual category remains in the trace.

### Recommended actions

The #57 action is bounded guidance:

```text
retry_later
terminal
fallback_provider
manual_review
```

The mapper receives whether another eligible fallback remains under the
current plan, deadline, privacy, and spend policy. It may emit
`fallback_provider` only when that fallback can actually be attempted.
Transport adapters never initiate a retry. `retry_later` schedules work only
inside a caller-owned bounded retry policy with an idempotency key and
authoritative retry/reset evidence. `terminal` forbids automatic retry.

`manual_review` remains readable for wire compatibility, but Argus does not
emit it in the autonomous profile and does not create an owner research queue.
If an old artifact contains it and no explicit external human workflow is
configured, composition treats it as terminal.

## Extraction plan and step trace

The immutable extraction plan declares:

- normalized URL identity and access scope;
- mode;
- ordered extractor candidates;
- per-candidate eligibility and spend class;
- cache policy;
- quality policy version;
- completeness policy version;
- whether partial artifacts are allowed;
- maximum 16 evaluated steps;
- operation deadline; and
- caller/profile/privacy identity.

Each evaluated step records:

```text
ordinal
extractor
decision = invoked | policy_skipped | cache_hit
status?
latency_ms?
normalized_failure_category?
egress?
machine?
source_type?
spend_attempt_ref?
```

Steps after a terminal success are implicit in the versioned plan and are not
materialized as `not_reached` rows. `policy_skipped` explains an evaluated
ineligible candidate but is not counted as an invocation. A cache decision is
not misrepresented as an extractor call.

The public rejection keeps only bounded `attempt_count`, causative provider,
last status, and total attempt latency. The durable trace keeps the typed step
facts. The target also distinguishes wall-clock operation latency from summed
attempt latency; PR #74's `total_latency_ms` remains the compatibility field
until issue #66 versions the richer projection.

## Attribution and provenance

Three identities remain separate:

- `selected_extractor` produced the retained artifact;
- `rejection.provider` identifies the causative attempt selected by #57; and
- every step has its own extractor/provider and egress provenance.

A fallback success can therefore identify Jina as selected while retaining a
Trafilatura quality rejection and Playwright timeout in the trace. No surface
may label the selected extractor as the cause of a different failure.

Artifact provenance retains source type, egress, machine, authentication,
cookie, archive, and spend evidence. Provider-native payloads, credentials,
raw bodies, and raw errors remain private to adapters.

## Composition with search results

Extraction is opt-in. A retrieval plan that needs content declares:

```text
ArtifactRequirement:
  selected_result_cluster_refs[]
  minimum_usable_artifacts
  required_cluster_refs[]
  allow_partial
  max_extractions
  deadline
  spend_policy_ref
```

Each selected result receives one stable link:

```text
ResultExtractionLink:
  result_cluster_ref
  extraction_run_id?
  extraction_outcome
  artifact_disposition
  artifact_ref?
  rejection_ref?
```

The link does not copy content or provider payloads into the search result.
Duplicate result clusters may share one eligible artifact; the link records
that reuse.

The composite decision is:

| Evidence | Composite result |
|---|---|
| no artifact requirement | accepted retrieval outcome unchanged |
| retrieval is `empty` or a terminal retrieval failure before any result selection | retrieval outcome unchanged; no extraction failure is invented |
| every selected extraction is usable and the required floor is met | retrieval outcome, unless it was already degraded |
| floor met using allowed partial artifacts | `degraded` |
| floor met but any selected candidate ended rejected | `degraded`, with every rejection link |
| artifact floor not met after eligible attempts | `extraction_failed`; search results remain retrieval evidence |
| no eligible extraction path | `unready` |
| any required durable acceptance failed | `persistence_failed` |

Internal fallback failures followed by one accepted final extraction do not
degrade that extraction. Cross-result selected failures do degrade the
composite operation because partial execution must remain visible.

Rejected and diagnostic-only content cannot enter `StoredDocument`,
`CitationRef`, summarizer input, or evidence scoring. Allowed partial content
must carry `artifact_disposition=partial` into its document, citation, report,
and summary input. Current workflow `continue` paths that silently discard
errors are migration targets; every planned selection gets a durable link.

## Cache semantics

Extraction cache identity includes:

```text
normalized_url
mode
public_or_private_access_scope
authentication/account scope fingerprint
extraction_plan_version
quality_policy_version
completeness_policy_version
partial-artifact policy
```

URL alone is insufficient. Authenticated or cookie-derived content never
crosses its access scope. Egress is retained as origin provenance rather than
rewritten as the current machine.

Only `usable` and policy-compatible `partial` artifacts can be positive cache
entries. `diagnostic_only`, `none`, and rejection-only outcomes are not
negative cache entries in version 1. A future negative cache requires a
separate bounded policy and cannot infer health.

A cache hit preserves:

- origin extraction run and artifact identity;
- original outcome, rejection, step trace, provenance, and spend;
- cache creation time, age, and policy versions; and
- the new logical request and durable acceptance receipt.

The cache check is a plan decision, not a synthetic successful extractor
attempt. If current policy is stricter than the origin evidence, the hit is
ineligible and normal extraction continues. Cache reuse never upgrades partial
or unproven content.

## Persistence and idempotency

The finalizer atomically persists:

- request and extraction-plan identity;
- source facts and typed step trace;
- selected artifact and disposition;
- quality/completeness evidence;
- the exact #57 rejection or null;
- canonical outcome;
- cache/provenance/spend evidence; and
- acceptance receipt.

The accepted object returned to HTTP is the same object passed to MCP, CLI,
Python, workflows, and persistence renderers. Persistence does not recompute
the rejection.

PR #74 correctly keeps its newly derived rejection out of the existing
acceptance fingerprint so old successful and failed run IDs retain their
identity. The target keeps that compatibility rule:

- legacy runs remain under implicit projection version 1;
- new runs record `extraction_outcome_policy_version`;
- source facts and policy version, not a duplicate rendered projection, define
  new acceptance identity; and
- old rows are never replayed, rewritten, or reclassified automatically.

A retry with an existing run ID returns the durably stored projection or a
conflict. It does not invoke extraction merely because a newer mapper version
exists.

## Privacy boundary

The public rejection contains only the #57 bounded fields. It never copies:

- requested or canonical URL;
- title or extracted content;
- raw provider/extractor error;
- request or response body;
- credential, token, cookie, or authorization data;
- arbitrary provider status text; or
- unbounded timing or attempt data.

The wider extraction response may already contain an authorized URL and
artifact; that does not make the rejection object a second copy. Logs use run
ID, outcome, rejection code, disposition, bounded latency, and evidence
references. They do not log raw errors or content.

Persistence may store the authorized artifact and redacted request URL under
the existing private-data policy. Diagnostic-only content follows the same
private retention controls but is excluded from caller evidence and delivery.

No part of this decision authorizes replaying historical Atlas failures,
bulk-extracting old URLs, or creating a manual-review queue.

## Surface compatibility

All surfaces preserve the same semantic projection:

| Surface | Required behavior |
|---|---|
| Python | returns the typed accepted outcome or typed operation failure |
| HTTP | renders the accepted outcome; no independent classification |
| MCP | delegates to HTTP in production and preserves outcome, rejection, action, disposition, and run ID |
| CLI | delegates to HTTP in production and visibly renders the same semantic fields |
| persistence | stores the accepted projection and full bounded trace |
| logs | emit safe references and stable categories only |
| workflows | link every planned result and enforce the declared artifact floor |

PR #74's additive `rejection` field and `"rejection": null` on complete success
remain compatible. Existing top-level fields stay readable. The legacy HTTP
200-with-`error` behavior and prose MCP/CLI rendering remain temporary
compatibility projections until issue #66 chooses versioning, statuses, tool
errors, and structured content. No adapter may use that temporary transport
shape to change the semantic outcome.

## Boundaries with downstream tickets

### Issue #65

Issue #65 must build fixture-only test vectors for this decision's semantic
fields and the retrieval plan/ranking/readiness decisions. It chooses the
throwaway envelope nesting and demonstrates success, fallback success,
degraded partial content, rejected extraction, stale/ineligible cache,
no-provider/unready, and persistence failure. It does not choose new rejection
codes.

### Issue #66

Issue #66 owns:

- HTTP status and error-body mapping;
- additive/versioned response rules;
- MCP structured-content and tool-error mapping;
- CLI exit and display compatibility as constrained by HTTP authority;
- session/auth/origin/DNS-rebinding behavior; and
- old-client compatibility.

It cannot collapse `degraded`, `timeout`, `policy_rejected`,
`persistence_failed`, `extraction_failed`, or `unready`.

### Mechanical implementation

The later AFK port should first land PR #74, then introduce the finalizer and
composer, then migrate persistence/HTTP, then MCP/CLI, and finally workflows.
It should replace the old classification paths rather than layering another
parallel status system.

## Verification obligations

Hermetic fixtures must prove:

- final rejection is classified exactly once and returned/persisted identically;
- heterogeneous chain exhaustion uses the orchestrator's terminal cause rather
  than whichever error happens to be scanned first;
- complete fallback success has no final rejection while retaining failed
  earlier steps;
- missing quality or completeness evidence fails visibly;
- incomplete content is degraded only under an explicit partial policy;
- rejected/diagnostic content cannot become a document, citation, summary
  input, delivery, or positive cache hit;
- every selected search result has a durable extraction link, including
  rejection and exception paths;
- the artifact floor deterministically produces success, degraded,
  extraction-failed, unready, and persistence-failed compositions;
- cache identity isolates auth/access and policy versions and preserves origin
  evidence;
- retrying a run ID returns the stored projection without a new call;
- legacy acceptance fingerprints and successful response fields remain
  compatible;
- HTTP, MCP, CLI, Python, persistence, logs, and workflows preserve the same
  semantic projection;
- `fallback_provider` is emitted only when an eligible bounded fallback remains;
- `manual_review` is never emitted or queued by the autonomous profile;
- raw errors, URLs, secrets, content, and untrusted labels do not enter the
  rejection object; and
- all traces, step counts, latencies, signals, and identifiers obey fixed
  bounds.

No live URL, paid extractor, historical replay, or owner labeling is required
to verify this contract.

## Consequences

- Existing callers gain stable rejection evidence additively before transport
  versioning is complete.
- Some current "successful" text becomes diagnostic-only because unknown
  quality/completeness no longer passes by default.
- Current workflows will report more degraded or failed runs because they can
  no longer silently discard selected extraction failures.
- Cache hits become less permissive but cannot promote stale, private,
  incomplete, or rejected content.
- The design adds no provider call, extraction wave, paid credit use, schema
  migration, production mutation, or deployment.
