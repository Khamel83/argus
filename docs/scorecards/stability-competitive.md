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
presented as one comparable score series.

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
