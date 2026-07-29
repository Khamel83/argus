# Argus stability and competitive evidence scorecard

Status: accepted decision record

Scope: scorecard contract, not implementation

Wayfinder ticket: [Define the Argus stability and competitive evidence scorecard](https://github.com/Khamel83/argus/issues/59)

## Purpose

This scorecard decides two different questions:

1. Is an Argus release/profile **stable** enough to use?
2. Is that stable release/profile **competitive** enough to promote or port?

Argus is scored on the evidence package it returns, not on prose written by a
downstream model or agent. The scorecard must run without making the owner
research topics, label routine results, or operate a benchmark by hand.

This record defines the target contract. It does not claim that the current
implementation already satisfies every field or gate.

## Verdicts

Verdicts apply to an exact release and evaluation profile.

| Verdict | Meaning |
|---|---|
| `stable` | Every hard contract, safety, persistence, and bounded-completion gate passed. |
| `unstable` | At least one hard gate failed. |
| `competitive` | Stable, plus statistically convincing evidence-quality improvement over the frozen baseline. |
| `not_competitive` | Stable, but the baseline is statistically better, a search mode regressed, or a catastrophic evidence regression occurred. |
| `inconclusive` | Stable, but the automated comparison lacks enough consistent evidence for either competitive conclusion. |

Stability and competitiveness are independent:

- A reliability-only change may ship when stable without being called
  competitive.
- A mechanical port may proceed only for the exact release/profile that is both
  stable and competitive.
- An inconclusive competitive run never blocks an unrelated reliability fix.

## Evaluation profiles

The scorecard produces separate verdicts for:

| Profile | Provider-credit rule |
|---|---|
| `free` | An explicit `--free` or `free_only=true` operation initiates no billable provider call. Tier-0 recurring free quota and otherwise-eligible cached evidence remain compatible with the existing contract. A new billable-provider attempt is a hard failure. |
| `budgeted` | Enabled providers may consume available credits within configured policy and caller caps. Consumption is recorded but does not improve or reduce the competitive score. |

Strong budgeted results cannot conceal a broken free path. A shared-code
promotion must keep both profiles stable even when only one profile is the
competitive candidate.

## Execution authority and surfaces

HTTP is the canonical execution authority.

- The complete live search and extraction benchmark runs once through HTTP.
- MCP, CLI, and Python prove semantic equivalence with hermetic fixtures.
- Production adds one bounded, authenticated MCP-to-HTTP smoke test.
- Repeating the live corpus through every adapter is forbidden because it
  wastes provider calls and introduces web drift.

Surface equivalence is semantic, not byte-for-byte. Equivalent requests must
preserve:

- normalized results and extraction artifacts;
- evidence and provenance;
- caller and profile policy;
- provider outcome and error classification;
- cache eligibility;
- durable persistence outcome.

Transport envelopes, terminal presentation, and adapter-specific metadata may
differ.

## Hard stability gates

Every applicable gate must pass. There is no aggregate score that can offset a
hard failure.

| Gate | Pass condition |
|---|---|
| Authentication | Production HTTP and MCP reject missing or invalid credentials before provider execution or persistence. |
| Caller attribution | Every accepted production operation has a durable caller identity; `unknown` is not accepted by the scorecard. MCP preserves the authenticated identity when delegating to HTTP. |
| Surface equivalence | HTTP, MCP, CLI, and Python preserve the semantics listed above for representative success, degraded, empty, timeout, policy-rejection, authentication, and total-failure fixtures. |
| Normalized result integrity | Results have valid canonical URLs, non-empty titles when the source supplies one, deterministic de-duplication, provider-independent shapes, and no secret/provider-native payload leakage. |
| Universal provenance | Every result or artifact reports provider/extractor, egress, machine, and source type where applicable. Missing required provenance is a hard failure. |
| Provider traces | Every eligible provider is represented as succeeded, failed, or policy-skipped with reason, result count, and bounded latency. A partial execution cannot appear as ordinary success. |
| Partial search | At least one provider succeeded, results clear the quality floor, and the response is explicitly `degraded` with complete traces. Otherwise the request hard-fails. |
| Empty search | A successful provider returning no matches is a valid empty success. Every eligible provider failing is not an empty success; it is a hard failure. |
| Search evidence floor | Evidence is relevant to the declared intent, contains no catastrophic misleading result promoted above sound evidence, and satisfies mode-specific source requirements. |
| Extraction success | Content is quality-passing and complete, with extractor, provenance, completeness signals, and extraction-run identity. |
| Degraded extraction | Useful partial text is explicitly degraded and includes completeness signals, quality reason, attempted chain, and recommended next action. |
| Extraction failure | Empty, misleading, or sub-floor content hard-fails with the attempted extraction chain preserved. |
| Durable acceptance | Production reports success or degraded success only after request, outcome, traces, evidence, and artifacts are durably accepted into PostgreSQL. |
| Persistence isolation | Explicit development/test profiles may use isolated SQLite; production never silently falls back to or skips persistence. |
| Provider readiness | Every configured provider has fresh, truthful eligibility and reachability state. Configuration presence alone is not health. |
| Mode availability | Each required mode/profile has at least one eligible working path. No eligible path means `unready`. |
| Policy truth | Missing keys, cooldowns, exhaustion, caller caps, free-profile skips, and provider failures are explicit and match observed execution. |
| Cache eligibility | A hit remains eligible for the request's mode, profile, provider policy, and freshness window, and exposes cache age and original evidence. |
| Cache isolation | A free-profile cache hit may reuse eligible evidence regardless of its original provider because it initiates no new billable call, but it must preserve origin/spend provenance. Provider-restricted or otherwise policy-ineligible evidence cannot cross request boundaries. |
| Bounded completion | Every individual HTTP, MCP, CLI, or Python operation completes or returns explicit timeout evidence within 120 wall-clock seconds. |
| Recovery authority | Production identifies PostgreSQL as authority and reports fresh backup/restore/recovery evidence when a schema-changing promotion requires it. |
| Evidence bundle | The run emits the complete secret-free, checksummed bundle defined below. A verdict without its bundle is invalid. |

Latency below 120 seconds is diagnostic only. Faster responses earn no
competitive advantage.

### Canonical outcomes

Every surface preserves one normalized outcome code:

| Outcome | Meaning | Surface behavior |
|---|---|---|
| `success` | Quality-passing evidence and durable acceptance. | HTTP 200, MCP result, CLI exit 0, Python response. |
| `degraded` | Usable above-floor evidence with explicit partial-provider or partial-extraction evidence. | HTTP 200 with outcome, MCP result with outcome, visible CLI `DEGRADED` with exit 0, Python response with outcome. |
| `empty` | Eligible provider execution succeeded but found no matches. | HTTP 200 with outcome, MCP result, CLI exit 0, Python response. |
| `invalid_request` | Request shape or value is invalid. | HTTP 422, MCP tool error, nonzero CLI exit, typed Python error. |
| `authentication_rejected` | Production authentication failed before execution. | HTTP 401, MCP authorization/tool error, nonzero CLI exit, typed Python error. |
| `policy_rejected` | Caller, profile, provider, or promotion policy forbids execution. | HTTP 403, MCP tool error, nonzero CLI exit, typed Python error. |
| `timeout` | The operation exceeded its bounded deadline. | HTTP 504, MCP tool error, nonzero CLI exit, typed Python error. |
| `persistence_failed` | Evidence could not be durably accepted. | HTTP 503, MCP tool error, nonzero CLI exit, typed Python error. |
| `providers_failed` | Every eligible provider failed. | HTTP 502, MCP tool error, nonzero CLI exit, typed Python error. |
| `extraction_failed` | The complete extraction chain produced no above-floor artifact. | HTTP 502, MCP tool error, nonzero CLI exit, typed Python error. |
| `unready` | No eligible execution path or required production dependency is ready. | HTTP 503, MCP tool error, nonzero CLI exit, typed Python error. |

Exact envelope and exception class design remains with the HTTP/MCP
compatibility ticket. The hard gate is that every adapter exposes the canonical
outcome without collapsing degraded, empty, policy, timeout, persistence, and
total-provider-failure cases into one another.

## Search golden corpus

The first benchmark generation contains 24 frozen query intents: six for each
mode.

| Mode | Required intent coverage |
|---|---|
| `discovery` | canonical-source finding, related-source breadth, ambiguous terminology, niche topic, primary-source preference, and duplicate-heavy results |
| `grounding` | stable fact, computed fact, current fact, technical claim, disputed claim, and explicit no-result/unknown case |
| `recovery` | moved canonical page, dead URL, renamed project, archived content, redirect chain, and unrecoverable target |
| `research` | multi-source technical topic, time-sensitive topic, competing claims, primary-plus-secondary evidence, long-tail topic, and explicit evidence gap |

Each machine-readable corpus entry contains:

- stable query id and mode;
- user intent and forbidden interpretation;
- required source characteristics;
- forbidden failure patterns;
- whether freshness matters and its acceptable age window;
- minimum evidence shape;
- profile applicability.

Live scoring judges intent satisfaction, not exact URLs. Exact URL/text
expectations belong only to hermetic compatibility fixtures.

### Mode-specific evidence floor

- `research`: at least three relevant sources across at least two independent
  domains.
- `grounding`: one authoritative source may be sufficient.
- `recovery`: one proven canonical replacement may be sufficient.
- `discovery`: diversity is reported diagnostically, not required.
- Mirrors, repeated URLs, and syndicated copies count once.

Freshness is query-specific. Evidence outside a time-sensitive query's declared
window fails that query's evidence floor. It becomes a catastrophic regression
only when the candidate materially replaces sound baseline evidence with stale,
misleading, or untrustworthy evidence. A newer but weaker source receives no
automatic advantage over a correct canonical source.

## Extraction corpus

The initial extraction corpus contains 12 cases:

- Eight hermetic fixtures:
  1. complete static article;
  2. JavaScript-rendered article;
  3. truncated or paywalled content;
  4. malformed content;
  5. redirect and canonical URL handling;
  6. duplicate or mirrored content;
  7. explicit timeout/total failure;
  8. non-HTML or unsupported content.
- Four synchronized live pages:
  1. canonical primary documentation;
  2. long-form article;
  3. JavaScript-dependent page;
  4. moved or archived page.

Hermetic fixtures hard-gate exact behavior. Live pages measure real-world
quality and provenance without exact-text expectations. A vanished live page is
an inconclusive case, not an Argus failure. Baseline and candidate use the same
captured live snapshot.

## Automated competitive evaluation

No routine human labeling or manual result grading is required.

For each live search intent and live extraction case:

1. Run baseline and candidate close together against the same corpus,
   topology, profile, and eligible-provider snapshot.
2. Remove candidate/baseline identity from the evidence packages.
3. Evaluate the pair with a frozen rubric and pinned evaluator model/settings.
4. Repeat with A/B order reversed.
5. Classify the pair:
   - `candidate_win`;
   - `baseline_win`;
   - `tie`;
   - `ordering_conflict` when the two orderings conflict, which is an
     inconclusive pair rather than an invalid run;
   - `catastrophic_regression` when sound baseline evidence becomes irrelevant,
     misleading, or untrustworthy.

The evaluator scores the Argus evidence package only. A downstream agent's
written answer is outside the verdict and may be observed diagnostically.

### Competitive verdict procedure

Apply these rules in order; the first matching rule is final:

1. If any candidate stability gate fails, return `unstable`.
2. If any pair is a `catastrophic_regression`, return `not_competitive`.
3. If any search mode has more `baseline_win` than `candidate_win` pairs,
   return `not_competitive`.
4. If fewer than 20 of the 28 live search/extraction pairs are consistent
   (`candidate_win`, `baseline_win`, or `tie`), return `inconclusive`.
   `ordering_conflict`, unavailable-evaluator, and malformed-evaluator pairs do
   not count as consistent.
5. If fewer than eight pairs are decisive (`candidate_win` or
   `baseline_win`), return `inconclusive`.
6. Apply a one-sided exact sign test to candidate wins versus baseline wins,
   excluding ties and all inconclusive pairs. If the candidate-improvement
   direction has `p < 0.05`, return `competitive`.
7. Apply the same one-sided exact sign test in the reverse direction. If the
   baseline-improvement direction has `p < 0.05`, return `not_competitive`.
8. Otherwise return `inconclusive`.

An evaluator outage or invalid output is captured as pair-level evidence; it
does not silently invalidate otherwise consistent pairs. It produces an
inconclusive run when the coverage rule is no longer met.

Speed and ordinary budget consumption do not contribute to this calculation.

## Frozen baseline and benchmark generations

A baseline is not just a commit. Each immutable benchmark generation records:

- Argus commit and container image digest;
- sanitized configuration/profile hash;
- corpus version and hashes;
- evaluator model, prompt, and settings version;
- egress/topology identity;
- eligible-provider snapshot;
- run timestamps;
- raw normalized evidence artifacts.

Baseline and candidate run close together to control web drift. Changing the
corpus, evaluator, prompt, settings, topology class, or other fixed component
starts a new benchmark generation. Results from different generations are not
presented as one comparable score series. The sanitized configuration hash is
itself a generation dimension, not merely an identity annotation. Baseline and
candidate must start within 15 minutes and the synchronized comparison window
must also finish within 15 minutes.

The competitive input is closed: it must contain exactly the frozen 24 search
ids plus four live-extraction ids, with each id appearing once under its
declared mode. Missing, duplicate, extra, or mode-mismatched pairs invalidate
the run before scoring.

## Evidence bundle

Every scorecard lane publishes a secret-free, checksummed bundle outside
transient CI logs. The manifest declares the lane and marks each section
`required`, `not_run`, or `not_applicable`; lane-inapplicable sections are
omitted rather than fabricated. A full competitive run uses:

```text
manifest.json
identities/
  baseline.json
  candidate.json
corpus/
  manifest.json
stability/
  gates.json
  surface-equivalence.json
competitive/
  deterministic-metrics.json
  blinded-comparisons.json
  verdict.json
artifacts/
  searches/
  extractions/
checksums.sha256
```

Every lane manifest records:

- schema version and run id;
- every identity applicable to the lane, including baseline, candidate, corpus,
  evaluator, profile, and topology for a competitive run;
- provider eligibility snapshot;
- per-gate pass/fail/inconclusive reasons;
- timing and persistence receipts;
- artifact paths and SHA-256 hashes;
- final stability and competitive verdicts.

Secrets, authorization headers, raw credentials, and provider-native secret
payloads are forbidden. A missing required artifact for the declared lane,
checksum mismatch, or identity mismatch invalidates the verdict.

### Required diagnostic evidence

Diagnostics do not change the verdict unless they also violate a hard policy
gate, but they are never optional:

| Diagnostic | Required evidence |
|---|---|
| Latency | End-to-end wall time, per-provider/per-extractor time, timeout source, and cache time for every operation. |
| Provider use | Eligible, attempted, succeeded, failed, and skipped providers with reasons and result counts. |
| Spend | Provider call count; reserved/estimated and actual charge or credit units when available; accounting source; caller cap; and reconciliation state (`confirmed`, `estimated`, `uncertain`, or `not_applicable`). |
| Cache | Hit/miss, cache age, original provider/spend provenance, and eligibility decision. |
| Freshness | Evidence publication/observation time when available, evaluated age, declared query window, and pass/fail reason. |
| Persistence | Acceptance receipt/run ids and artifact counts, without credentials or connection secrets. |

## Trigger policy

| Lane | Trigger | Effect |
|---|---|---|
| Hermetic stability | Every pull request | Required code-review gate. |
| Live competitive | Weekly and for explicit promotion/port candidates | Controls only the competitive/port verdict. |
| Production canary | After deployment and recurrently before the current canary evidence expires | Confirms HTTP, MCP, authentication, PostgreSQL persistence, provider truth, and recovery state. |

Provider readiness and recurring canaries use the existing operational evidence
contract: `observed_at <= now < expires_at`. A missing or expired observation is
not fresh. Each canary schedule records its interval and evidence TTL; the next
run must occur before expiry, so “recurrently” never becomes an unbounded
operator promise.

## Hermetic implementation boundary

`scripts/run-scorecard.py --lane hermetic` is the pull-request lane. It reads
frozen raw corpus inputs and separately frozen expected observations, executes
the raw inputs through pure production contracts and the hermetic provider
adapter harness, evaluates both `free` and `budgeted` profiles independently,
and writes a checksummed secret-free diagnostic bundle. All transport is
stubbed; it has no live network, persistence, spend, deployment, or promotion
authority.

`scripts/run-scorecard.py --lane live-config` emits the secret-free interface
for a future live run: the exact 28 cases, all 24 literal live search queries,
the four extraction URLs with distinct `url_sha256` and `snapshot_id` fields,
synchronized identity requirements, evaluator requirements, automatic free-only
policy, and immutable
budgeted receipt fields (`schema`, `receipt_id`, `run_id`, `generation`,
`permitted_providers`, `maximum_tier`, `call_count_cap`,
`cost_or_credit_cap`, `one_time_credit_providers`, and `issued_at`). It does
not search, extract, evaluate, reserve, consume a receipt, or contact an
authority.

`scripts/run-scorecard.py --lane competitive --input SEALED.json
--stability-bundle HERMETIC_BUNDLE` is a network-free compiler. The supplied
execution document must already be sealed by the live authority. The compiler
requires exact 24-search plus 4-extraction coverage; immutable baseline and
candidate commits and image digests; the shared frozen extraction captures;
bounded timing; complete attempt, cache, freshness, spend, and PostgreSQL
persistence diagnostics for both identities; ready tier-zero provider
selection; and reconciled zero spend. Missing HTTP diagnostics fail closed.
It copies normalized evidence into a checksummed competitive bundle and derives
the verdict from the two serialized evaluator orders. It never calls a
provider, extractor, evaluator, HTTP authority, or database.

The hermetic bundle used by that compiler is not an arbitrary green bundle.
After the candidate image exists, Homelab generates a fresh proof with
`--lane hermetic --candidate-image-digest sha256:...`. The compiler verifies
the proof bundle and requires the sealed `stability_binding` to match its
manifest hash, hermetic generation, semantic corpus hash, sanitized
configuration hash, candidate commit, and candidate image digest. Those proof
dimensions are incorporated into the live generation. Ordinary pull-request
hermetic bundles with `image_digest: null` remain valid diagnostic evidence but
are explicitly rejected as live certification inputs. No digest is required or
guessed before the candidate image has been built.

Every extraction request names the shared `capture_sha256` and an exact local
captured-replay chain. Both baseline and candidate normalized evidence repeat
that capture identity, and each capture hash is part of the live generation.
Search results and provider attempts are closed to the requested ready
tier-zero providers; every requested provider must be represented. Extraction
attempts must be exactly the declared local extractor chain, with no external
provider calls.

Provider `result_count` has one exact meaning: the number of normalized results
from that attempt retained after within-provider and retry deduplication.
Duplicates discarded before emission are not counted. For each requested
provider, the sum across `success` or `cache` attempts must equal the number of
emitted normalized results attributed to that provider. Every other attempt
status must report zero and cannot supply a result. Provider status is closed
to the runtime trace vocabulary: `success`, `error`, `skipped`, or `cache`.

The canonical search outcome is derived from the complete provider trace, not
trusted as a free-standing claim. Emitted results with only successful or cache
attempts are `success`; emitted results alongside a skipped or error attempt are
`degraded`. A successful attempt with zero retained results yields `empty`, as
does a zero-result cache projection. An entirely skipped trace yields
`policy_rejected`; other complete no-success traces yield `providers_failed`.

Captured replay uses the same reconciliation rule for content. If content is
present, exactly one `success` local extractor attempt contributes one retained
document, and normalized `source_type` must name that extractor in the declared
replay chain. Extractor status is closed to the runtime attempt vocabulary:
`success`, `failed`, or `quality_failed`. Paid API, external reader, phantom, or
multiply claimed source provenance is rejected.

Freshness observations are side-specific: `observed_at` must fall within the
corresponding baseline or candidate execution window, and `age_seconds` must be
derivable from that identity's finish time. Serialized timing or age claims
from an unrelated execution are rejected.

The evaluator identity is explicit. `status: pinned` requires a non-empty model
and immutable prompt/settings hashes. When no genuinely pinned evaluator is
configured, `status: unavailable` requires `model: null`, a reason code, and
`unavailable` in both orders for every case. The latter truthfully derives an
inconclusive competitive verdict; it does not substitute a model or fabricate
judgments.

`scripts/run-scorecard.py --lane residual --attempt-one BUNDLE_ONE
--attempt-two BUNDLE_TWO` accepts only two distinct, stable, free-profile,
inconclusive competitive bundles with the same generation and immutable
baseline/candidate identities. It writes a closed, checksummed
`scorecard-bounded-inconclusive-residual-v1` receipt. The receipt records the
two source manifest/checksum hashes and explicitly sets
`can_authorize_deployment: false`; it is bounded residual-risk evidence, never a
deployment authorization. The two source runs must also have disjoint timing
operation IDs and durable persistence receipt IDs; a renamed run ID is not
independent evidence.

The separately defined `.github/workflows/scorecard-live.yml` remains a
diagnostic-only weekly/manual live configuration publication. It needs
no authority URL, token, provider credential, evaluator, receipt, or protected
environment. External Task 16/P1 execution owns canonical-HTTP retrieval,
both evaluator orders, provider reservation and cap enforcement, and receipt
consumption. The repository compiler only verifies and packages the resulting
sealed document; it has no execution or protected promotion/deployment
authority.

The hermetic lane executes a distinct frozen raw contract for every hard gate
and compares it with separate expected evidence. A mutation to one gate cannot
be fanned across the others. Local production contracts directly exercise
authentication-before-I/O, caller propagation, accepted-operation durability,
persist-before-cache publication, cache freshness/isolation, and production
authority isolation. Gates whose live dependency belongs to P1 use an exact
typed raw contract with an independent evaluator rather than a copied status.
The lane retains normalized evidence for every gate and corpus case and reads
the same whole route/presenter inventory, including direct repository-call
detection, as the architecture boundary tests. Bundle preparation and
verification both
recompute stability, blinded-pair classification, deterministic counts, exact
sign-test value, ordered verdict, and stability dependency; serialized claims
are never treated as authority. It writes to a private sibling staging
directory only after
all typed identities, synchronized generation dimensions, normalized document
schemas, safe paths, and secret/native-payload checks pass. Verification
requires exact agreement between the manifest file set, regular files, and
unique canonical checksum entries, including a checksum for `manifest.json`,
before the staged directory is atomically published. A future competitive
bundle must include normalized baseline and candidate artifacts for all 24
search cases and all four synchronized live extraction cases. Search
artifacts retain normalized results and extraction artifacts retain normalized
content; provider-native payloads remain forbidden.

## Explicit non-goals

- No external-search-engine parity claim.
- No speed optimization score below the two-minute ceiling.
- No manual benchmark operation or routine result labeling.
- No production LLM dependency.
- No downstream prose-quality gate.
- No provider-credit purchase or automatic budget expansion.
- No exact live-URL ranking contract.
- No destructive cleanup or retention deletion.

## Handoff boundaries

This record fixes what must be measured. It intentionally leaves these design
mechanics to their existing Wayfinder tickets:

- bounded retrieval planning and cache identity;
- provider-aware freshness, provenance, and ranking policy;
- structured extraction rejection integration;
- the canonical evidence envelope and HTTP/MCP compatibility target;
- provider drift inventory and no-spend health implementation;
- mechanical port sequencing.
