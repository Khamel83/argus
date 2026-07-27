# ADR 0003: Provider-aware freshness, provenance, and ranking

- Status: Accepted
- Date: 2026-07-27
- Issue: [#62](https://github.com/Khamel83/argus/issues/62)
- Parent: [#58](https://github.com/Khamel83/argus/issues/58)
- Depends on:
  [the stability scorecard](../scorecards/stability-competitive.md),
  [the provider/extraction drift inventory](../research/2026-07-26-provider-extraction-contract-drift.md),
  [the provider signal research matrix](../research/2026-07-27-provider-ranking-signals.md),
  and
  [ADR 0002](0002-bounded-retrieval-plan-cache-identity.md)

## Decision

Argus will normalize every provider response into one typed, bounded
`ProviderSearchBatch` before freshness evaluation, duplicate clustering, or
ranking. A batch contains ordered `ResultObservation` values plus response-level
provider evidence. One internal `EvidenceFusion` deep module then:

1. validates and normalizes provider ranks and evidence;
2. applies declared freshness and domain eligibility;
3. clusters only results proven to represent the same document;
4. performs deterministic unweighted reciprocal-rank fusion (RRF);
5. applies a bounded research-mode site-diversity pass;
6. checks the mode-specific evidence floor; and
7. returns ranked evidence plus a complete, renderable fusion trace.

The external HTTP, MCP, CLI, and Python shapes do not change in this decision.
The current `SearchResult.score` remains the deterministic RRF score.
`SearchResult.provider` remains the representative contributor for
compatibility. Contributor identities, ranks, and canonical contributions are
always retained internally, even when outward attribution is not requested.

Provider-native scores, highlights, answers, citations, rewrites, source
types, and publication signals are preserved when their contracts are known,
but they do not receive blanket authority merely because a provider supplied
them. No mandatory model-backed ranker, vector database, extra paid request,
or hidden query rewrite is introduced.

## Why this approach

Three bounded approaches were considered.

### A. Normalize provider scores into a weighted fusion

Use provider-native relevance scores, static provider weights, freshness
boosts, and source-type boosts to produce a single weighted score.

This has no necessary extra network latency, but current providers expose
incompatible or undocumented scales. Some return a score, some only a
position, and some scores describe a provider-private retrieval stage rather
than comparable relevance. Static provider or source weights would encode
untested assumptions and could make a newer but weaker source outrank a
canonical one.

Rejected for version 1. Native scores remain diagnostic evidence and may be
evaluated offline against the frozen corpus.

### B. Evidence-gated unweighted RRF

Treat each provider's returned ordering as its only production ranking vote.
Normalize evidence, fail closed on declared freshness, fuse proven duplicate
documents with equal-provider RRF, and apply only the minimum deterministic
site-diversity rule required by the research evidence floor.

This preserves the current ranking model, adds no provider call, does not
reward speed, and creates a reproducible baseline for later measured changes.

Accepted.

### C. Mandatory semantic or model reranking

Send a bounded candidate set to embeddings, a cross-encoder, or an LLM and
replace the deterministic ordering.

This may improve some relevance judgments, but adds latency, cost, model
availability, privacy review, nondeterminism, and a new failure path before the
provider contracts and evidence corpus are stable.

Rejected as a production dependency. A future experiment may emit a separate
candidate ordering for scorecard comparison, but cannot change production
results, cache entries, or the ranking policy version until it passes the
promotion procedure below.

| Approach | Quality evidence | Added latency | Added credits/cost | Scorecard disposition |
|---|---|---|---|---|
| Native-score/static weighted fusion | Unproven across incompatible provider scales; risks opaque regressions | bounded local only | none when using returned fields | reject until frozen-corpus evidence proves weights |
| Evidence-gated unweighted RRF | Preserves current consensus behavior; adds truthful freshness, duplicate, and diversity gates | bounded local only | none | accepted deterministic baseline |
| Mandatory semantic/model reranker | Plausible relevance gains but new nondeterminism and catastrophic-regression risk | model/embedding call plus queue time | model or compute spend | reject as production dependency; offline experiment only |

## Deep module and seam

Callers and tests cross one interface:

```text
fuse_evidence(
  retrieval_plan,
  provider_batches,
  fusion_policy,
  utc_clock,
) -> FusionOutcome
```

Provider adapters satisfy the existing provider seam but return a private
normalized batch:

```text
ProviderSearchBatch:
  provider
  provider_contract_version
  request_evidence
  response_evidence
  observations[]

ResultObservation:
  provider
  provider_rank
  url
  title
  snippet_evidence
  publication_evidence?
  source_evidence
  native_score_evidence?
  provider_result_ref?
```

`FusionOutcome` contains:

```text
outcome
ranked_result_clusters[]
filtered_observations[]
duplicate_relations[]
site_diversity_trace
evidence_floor_trace
ranking_trace
```

Provider adapters own native-shape translation. `EvidenceFusion` owns
cross-provider semantics. HTTP/MCP/CLI adapters only render the accepted
outcome and never re-rank or reinterpret evidence.

## Normalized provider evidence

Only allowlisted, typed, bounded evidence crosses the provider seam. Unknown
fields remain inside the adapter and raw payloads are never persisted.

### Result-level evidence

Every accepted observation records:

- provider identity and zero-based returned rank;
- validated HTTP(S) URL and normalized hostname;
- title and snippet with their source kind;
- provider result reference when documented;
- evidence kind and provider-native source type when documented;
- bounded upstream-engine identities for aggregators when documented;
- normalized publication time, precision, source, and contract confidence;
- native score value, semantics, and scale only when documented;
- bounded author, language, country, and section labels when supplied;
- egress, machine, and observation time through the existing provenance path.

The normalized evidence kinds are:

```text
web_page
news
repository
computed_answer
sourced_answer
paper
proprietary
archive
unknown
```

Provider-native source labels are retained separately. An unknown label does
not become a guessed evidence kind.

Snippet evidence records one of:

```text
provider_description
provider_snippet
provider_highlight
provider_text_excerpt
empty
```

Highlights may improve the displayed snippet when they are already returned
by the authorized search call. Argus retains at most three ordered highlights
of at most 500 characters each and does not concatenate unrelated fragments
into purported source prose.

### Publication evidence

Publication evidence is:

```text
published_at_utc?
published_date?
precision = timestamp | date | month | year | provider_age | unknown
source = provider_field | provider_age | result_text | none
contract_confidence = contracted | fixture_backed | unverified
raw_field_name?
```

Only a documented provider field or fixture-backed migration alias can
populate `provider_field`. Dates guessed from snippets, URLs, or titles are
not authoritative. Provider `page_age` or human-readable age text is retained
as `provider_age`; it becomes a publication date only when the adapter has a
versioned, deterministic parser and the provider contract defines its
semantics.

The retrieval observation time is never substituted for publication time.
Timestamps without a timezone are invalid for strict freshness. Date, month,
and year precision represent UTC intervals. A coarse claim proves freshness
only when its entire interval lies inside the requested window. Multiple
approved publication claims are consistent when their intervals overlap; a
disjoint conflict excludes the result as `conflicting_publication` rather than
choosing the newest claim.

### Native scores and rank

The provider's returned array order is authoritative for that provider.
Adapters assign a unique zero-based `provider_rank` after dropping structurally
invalid rows. A provider-native position or rank field is retained separately
as bounded diagnostic evidence; it never replaces array order or creates
gaps in RRF ranks.

Native scores are retained only as:

```text
value
semantics = relevance | provider_rank_score | quality | unknown
scale_min?
scale_max?
contract_confidence
```

NaN, infinity, negative values where forbidden, and inconsistent duplicate
positions reject the field, not the whole result. EvidenceFusion never
compares native scores across providers and never silently reorders a
provider's results by a raw score.

### Response-level evidence

A batch may retain bounded:

- provider request, search, session, or transaction references;
- resolved provider search mode/depth and applied controls;
- warnings and recognized suggestions;
- query rewrite or interpreted-query text;
- response-reported usage, cost, and latency;
- rate-limit and reset evidence;
- provider section counts and result-count hints.

Identifiers are bounded opaque references, not credentials. Warnings and
suggestions are scrubbed and bounded. Account payloads and raw response bodies
are forbidden.

Query rewrites and suggestions are evidence only. Version 1 does not execute a
rewritten query, add a follow-up request, or replace the effective query from
ADR 0002.

## Answers and citations

Computed or synthesized answers are not ordinary web pages.

- Wolfram output is `computed_answer`. Its synthetic query URL identifies the
  provider interaction; it is not a citation proving every returned sentence
  and never becomes a web-result RRF contribution.
- A provider `sourced_answer` is retained only when the resolved typed plan
  explicitly authorized that provider feature and the response includes it.
  It is a separate evidence artifact with ordered source references, not a
  replacement for the source result observations.
- Provider citations are normalized as ordered source references. Citation
  count does not boost rank, and a citation is not treated as an independent
  endorsement by the same provider.
- Query rewrites, answer synthesis, live crawl, deep search, and content
  retrieval modes that change credit cost are never enabled implicitly to
  acquire these signals.

Answer artifacts are evaluated separately from URL-backed result clusters.
`computed_answer` may satisfy the grounding runtime floor when its provider is
in the resolved plan, but does not satisfy the research URL-backed source
floor. The temporary rendering of the current Wolfram synthetic
`SearchResult`, `sourced_answer` presentation, and the final public envelope
remain owned by issues #65 and #66; the internal target never pretends an
answer is a web citation.

## Freshness policy

ADR 0002 defines the inclusive UTC windows. This decision defines proof.

### Translation evidence

Each adapter declares a versioned translation capability:

```text
none
relative_only
date_range
relative_and_date_range
query_qualifier
```

For every attempt, the trace records:

- requested and resolved window;
- provider control and bounded value actually sent;
- translation precision (`exact`, `widened`, or `unsupported`);
- provider filter strength (`strict_contract`, `best_effort`, or `unknown`);
- post-filter counts and reasons.

A widened provider request is allowed only when broker post-filtering can
enforce the exact window. Unsupported translation does not make an adapter
eligible when returned results lack per-result publication proof.

### Per-result eligibility

When no freshness constraint is declared, publication evidence is diagnostic
and never boosts or penalizes rank.

When freshness is declared:

1. every approved claim becomes its UTC precision interval;
2. at least one claim interval must lie wholly inside the inclusive window;
3. disjoint approved claims reject the result as conflicting;
4. an in-window result is eligible;
5. an out-of-window or unproven result is excluded with a stable internal
   reason; and
6. newer eligible evidence receives no additional ranking boost.

This implements the scorecard rule that newer but weaker evidence has no
automatic advantage over a correct canonical source.

### Empty and failure semantics

A successful empty provider response can prove `empty` for a freshness plan
only when:

- the adapter contract version marks the exact applied filter
  `strict_contract`;
- the trace proves the requested window was translated exactly; and
- the response passed the provider's successful-empty fixture contract.

Otherwise an empty response is useful provider evidence but does not by itself
prove a freshness-scoped empty result. If all returned observations are
out-of-window or unproven and no strict empty proof exists, the internal reason
is `freshness_unproven`, mapped to canonical `providers_failed`.

Changing translation, parser, strictness, or date-comparison semantics bumps
`freshness_policy_version` and invalidates incompatible cache entries.

## Proven duplicate clustering

Fusion operates on document clusters, not raw provider rows. A relation is
strong enough to merge only when at least one is true:

1. the URLs have the same conservative document key;
2. a provider supplies the same documented canonical URL for both;
3. extraction proves the same safe redirect/canonical chain; or
4. extraction produces the same versioned normalized-content fingerprint.

Title similarity, snippet similarity, shared hostname, `www` variation,
trailing-slash variation, syndicated appearance, and tracking-parameter
variation are weak hints only. They may be reported diagnostically but never
merge evidence or contributions without one of the proofs above.

### Conservative document key

The version 1 key:

- accepts only HTTP(S);
- lowercases and IDNA-normalizes the hostname;
- rejects user information and removes default ports;
- normalizes dot segments and unreserved percent encoding;
- removes the fragment, which is not sent in the HTTP request;
- preserves scheme, path case, trailing slash, query parameter order,
  duplicate parameters, empty values, key-only parameters, and all parameter
  names and values.

This deliberately does not repeat the current unsafe assumptions that
`www`, trailing slash, arbitrary `ref`, or query ordering are always
non-semantic. A separate weak-alias diagnostic may remove an exact allowlist
of tracking fields (`utm_*`, `fbclid`, and `gclid`), but that key never drives
fusion.

Invalid URLs are excluded with evidence; they are not grouped under an empty
key.

### Cluster contributions and representative

One provider contributes at most once to a cluster, using its best returned
rank. All provider observations remain in the cluster evidence.

Each cluster has a deterministic `cluster_sort_key`: the verified canonical
document key when present, otherwise the lexicographically smallest member
document key. The key is evidence for ordering and identity, not a caller URL.

The outward representative is selected deterministically:

1. a verified canonical URL, when one exists;
2. otherwise the observation with the best provider rank;
3. then the earliest provider in the resolved plan;
4. then canonical URL byte order.

Title and snippet come from the same representative observation. Argus does
not splice fields from different sources into synthetic source text.

Verified duplicate and syndicated clusters count once toward the scorecard
evidence floor. Weak hints remain separate and therefore cannot hide
potentially distinct evidence.

## Deterministic fusion

Version 1 retains unweighted RRF with `k=60`:

```text
cluster_score =
  sum(1 / (60 + provider_rank + 1) for each contributing provider)
```

Every eligible provider has weight 1. Provider tier, cost, latency, native
score, source type, freshness age, snippet length, and provider health do not
change the score. Those facts control planning, eligibility, or diagnostics,
not evidence relevance.

The final base order is:

1. descending RRF score;
2. ascending best contributing provider rank;
3. descending contributor count;
4. ascending earliest resolved-provider position;
5. ascending `cluster_sort_key` bytes.

All five values and every provider contribution are retained, so the order can
be reproduced exactly. A change to `k`, eligibility, tie-breaking, or
contribution semantics bumps `ranking_policy_version`.

## Site diversity and evidence floors

`site_key` is the registrable domain derived from a pinned Public Suffix List
snapshot. Subdomains of one registrable domain share a site key. IP literals
and non-public special hosts use the exact normalized host and can appear only
in explicitly permitted test/development plans. The PSL snapshot identifier is
part of `domain_policy_version`.

Mode behavior:

- `discovery`: preserve base order; report site diversity diagnostically.
- `grounding`: preserve base order; one eligible URL-backed cluster or computed
  answer may satisfy the runtime floor.
- `recovery`: preserve base order; one proven canonical replacement may
  satisfy the floor.
- `research`: apply a two-per-site cap while an eligible result from another
  site remains, then backfill in base order.

A distinct site key is a deterministic diversity proxy, not proof of editorial
independence or authority. Argus does not guess either from source type,
hostname, or provider. The frozen competitive evaluator judges whether the
returned source characteristics satisfy the corpus intent.

The research pass does not change RRF scores. It emits base rank, output rank,
site key, skip/backfill reason, and the candidate-site counts.

The selection algorithm is exact:

```text
required_clusters = min(3, result_limit)
required_sites = min(2, required_clusters)
selected = []
deferred = []

# Coverage pass: take the earliest cluster for each new site.
for cluster in base_order:
  if cluster.site_key not in selected_sites:
    selected.append(cluster)
  if selected_site_count == required_sites:
    break

# Fill pass: retain base order with a two-per-site soft cap.
for cluster in base_order excluding selected:
  if selected_count_for_site(cluster.site_key) < 2:
    selected.append(cluster)
  else:
    deferred.append(cluster)
  stop when selected reaches result_limit

# Relax only when available site diversity cannot fill the request.
if selected has fewer than result_limit:
  append deferred in base order until full
```

Coverage can move only the earliest result from the second required site ahead
of same-site results. During fill, no deferred item can jump ahead of an
eligible base-order item from another site.

After diversification, the runtime research floor is:

```text
required_clusters = min(3, result_limit)
required_sites = min(2, required_clusters)
```

The frozen competitive corpus uses `result_limit >= 3`, so its full floor
remains three URL-backed clusters across at least two site keys. This preserves
the existing valid `1..50` result-limit contract for callers that explicitly
request a smaller package without pretending the benchmark floor was met.
Proven mirrors and syndicated clusters count once. If eligible providers
returned evidence but the effective floor cannot be met, Argus produces a
visible internal rejection `research_evidence_floor_unmet`; it does not label
a thin package `success`, `degraded`, `empty`, or `providers_failed`. Issue #66
must assign the truthful stable HTTP/MCP/CLI/Python outcome before this policy
can ship. A genuine strict successful empty remains `empty`.

## Latency and credit policy

The accepted path adds no network request. Normalization, clustering, RRF, and
diversification are bounded local work over the results already allowed by the
retrieval plan. Implementations must remain within the operation deadline and
the persistence reserve from ADR 0002.

Signal policy:

- response fields already returned by an authorized call: normalize;
- provider options that do not change billed mode and remain within the same
  call: may be requested when fixture-backed;
- highlights, contents, deep search, live crawl, sourced answers, or
  auto-parameters that can add credits or latency: disabled unless a future
  typed plan explicitly authorizes their known cost;
- query rewrite execution or follow-up retrieval: disabled;
- provider balance/account endpoints: health policy, not ranking;
- model or embedding reranking: experimental offline lane only.

Provider-reported latency is evidence, never a ranking advantage. Completion
below 120 seconds earns no competitive score.

## Optional late reranking

No late reranker is active in version 1. A future candidate must:

- consume only the deterministic top 20 clusters or four times
  `max_results`, whichever is smaller;
- introduce no URL and remove no required evidence;
- receive only the privacy-approved normalized evidence projection;
- retain the full deterministic base order and contributor trace;
- have an explicit cost/deadline budget;
- run in the live competitive lane with frozen model/settings and reversed
  A/B order;
- show no stability failure, catastrophic regression, or per-mode loss under
  the scorecard; and
- be promoted by an explicit ranking-policy version change.

An experimental result never populates the production cache. Failure of an
optional offline experiment leaves the deterministic result unchanged and is
recorded as experiment evidence, not hidden fallback.

## Provider-signal application matrix

The detailed source facts live in the provider signal research matrix. The
cross-provider policy is:

| Signal | Normalize | Eligibility effect | Ranking effect |
|---|---|---|---|
| returned array order | yes, mandatory | native position is diagnostic; array order defines rank | RRF contribution |
| native relevance score | when contracted | none in v1 | diagnostic only |
| publication timestamp/date | when proven | strict freshness post-filter | none after eligibility |
| human-readable age/page age | bounded | only with versioned parser/semantics | diagnostic by default |
| highlights/text excerpt | bounded | snippet may remain empty | representative snippet only |
| evidence/source type | typed plus raw label | mode/evidence-floor classification | no generic boost |
| provider answer | separate typed artifact | grounding floor only when eligible | never participates in web RRF |
| citations/source refs | ordered and bounded | provenance completeness | no endorsement boost |
| query rewrite/suggestion | response-level evidence | none | none; never auto-executed |
| author/language/country | bounded | declared plan controls when proven | diagnostic |
| request/run IDs | bounded opaque refs | trace completeness | none |
| warnings/errors/usage/cost | typed response evidence | policy/outcome classification | none |
| latency | yes | deadline only | none |

## Compatibility

Existing public request and response fields remain available:

| Current field | Version 1 meaning |
|---|---|
| `SearchResult.url/title/snippet` | representative observation fields |
| `SearchResult.domain` | representative normalized hostname |
| `SearchResult.provider` | representative contributing provider |
| `SearchResult.score` | deterministic cluster RRF score |
| `SearchResult.raw_rank` | representative provider rank |
| `SearchResult.score_attribution` | optionally rendered from always-retained contributions |
| provider-specific `metadata` | transitional input; typed internal evidence is authoritative |

During a mechanical port, adapters may dual-read intentional old aliases, but
new provider-native fields must not spread into callers. The public evidence
envelope, structured contents, and additive versioning remain with issues #65
and #66. Persistence migrations remain with the implementation sequence in
#67.

## Contract tests

Hermetic tests must prove:

### Normalization and privacy

- each provider fixture maps documented ranks, dates, source types, request
  references, warnings, usage, and native scores into typed fields;
- aliases exist only for intentional staged migrations;
- NaN/infinite scores, malformed dates, invalid URLs, and duplicate positions
  cannot corrupt fusion;
- raw bodies, credentials, authorization headers, signed URLs, and unbounded
  provider messages never cross the adapter seam.

### Freshness

- exact day/week/month/year and explicit-date boundaries from ADR 0002;
- timestamp and date precision at both inclusive edges;
- out-of-window and undated results are excluded;
- widened provider translation requires exact broker post-filtering;
- strict exact-filter empty can produce `empty`;
- best-effort/unknown empty cannot prove a freshness-scoped empty;
- all-unproven evidence maps to `providers_failed/freshness_unproven`;
- freshness age never changes order among eligible results;
- freshness policy version changes invalidate the cache fingerprint.

### Duplicate evidence

- guaranteed URL normalizations produce one document key;
- HTTP/HTTPS, path case, trailing slash, query order, and non-tracking
  parameters remain distinct without proof;
- `www`, tracking, title, and snippet similarity alone never merge;
- verified canonical, redirect, or content-fingerprint relations merge;
- one provider contributes only its best rank once per cluster;
- all observations and provider ranks survive representative selection.

### Ranking and attribution

- current single-provider order is preserved;
- equal-provider RRF with `k=60` matches exact expected values;
- native-score scale changes do not alter order;
- duplicate clusters accumulate all provider contributions;
- every tie-break is deterministic across provider mapping insertion order;
- optional outward attribution exactly reconstructs the stored score;
- ranking policy version changes alter plan/cache identity.

### Diversity and floors

- discovery, grounding, and recovery preserve base order;
- research takes no more than two per site while another site remains;
- research backfills deterministically when diversity is exhausted;
- subdomains share the pinned registrable site key;
- proven duplicate/syndicated clusters count once;
- runtime research floor scales only when the caller explicitly requests fewer
  than three results;
- three URL-backed clusters across two sites pass the full research floor;
- thin research evidence fails visibly with
  `research_evidence_floor_unmet` and awaits #66's truthful surface mapping;
- computed and sourced answers do not masquerade as independent URL-backed
  sources.

### Cost and latency

- fusion performs no network operation;
- already-returned signals add no provider attempt;
- billable/deep/live-crawl/answer options remain disabled without an explicit
  typed authorization;
- ranking never depends on provider latency;
- bounded local fusion and slow-fusion fakes respect the operation deadline
  and persistence reserve;
- experimental reranker output cannot alter production response or cache.

## Explicit limits

Version 1 does not:

- infer intent or rewrite the effective query;
- issue a follow-up search for suggestions or rewrites;
- compare undocumented provider-native score scales;
- boost newer evidence merely for being newer;
- assign generic authority weights by provider or source type;
- fuzzy-merge titles, snippets, hosts, or syndicated pages without proof;
- use citation count as an endorsement score;
- enable provider deep search, live crawl, contents, or synthesized answers
  implicitly;
- require a model, embedding service, vector store, or manual result labeling;
- alter public transport schemas; or
- authorize paid probes, credit purchases, production deployment, or a
  persistence migration.

## Consequences

Positive:

- declared freshness fails closed without turning recency into a relevance
  shortcut;
- provider-native evidence becomes durable and inspectable without leaking raw
  payloads;
- ranking is deterministic, reproducible, and cost-neutral;
- duplicate contributions and attribution survive cache and presentation
  choices;
- research breadth is enforced without distorting other modes;
- future weighting or reranking has a stable baseline and promotion gate.

Costs:

- conservative duplicate proof leaves some apparent duplicates separate until
  extraction establishes identity;
- some freshness queries will fail visibly when providers omit publication
  evidence;
- a pinned Public Suffix List becomes versioned ranking/domain data;
- typed evidence requires adapter and persistence work in issue #67;
- provider-native relevance scores do not improve production ordering until
  the corpus proves a safe normalization or weighting policy.
