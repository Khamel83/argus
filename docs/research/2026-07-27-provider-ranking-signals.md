# Provider-aware freshness, provenance, and ranking policy

Status: proposed decision record

Scope: research and policy selection, not implementation

Issue: [#62 — Select provider-aware freshness, provenance, and ranking policy](https://github.com/Khamel83/argus/issues/62)

Inputs:

- [accepted stability and competitive evidence scorecard](../scorecards/stability-competitive.md);
- [provider and extraction contract-drift inventory](2026-07-26-provider-extraction-contract-drift.md);
- [bounded retrieval plan and cache identity ADR](../adr/0002-bounded-retrieval-plan-cache-identity.md);
- current adapters, models, ranking, deduplication, and hermetic fixtures at this
  branch.

No live provider call was made. “Code-observed” below means the checked-in
adapter or fixture accepts the field; it is not a claim that the provider
returned that field today. “Official” means a first-party provider contract,
or the maintained `ddgs` source for that library. Those two evidence classes
stay separate because several checked-in fixtures reproduce a stale adapter
shape rather than the current provider contract.

## Decision

Retain more provider-native evidence, but keep the stable ordering policy
small and deterministic:

1. Normalize typed time, content, source, contribution, and request-evidence
   fields. Preserve a bounded native value only when its meaning is known; do
   not preserve arbitrary provider payloads.
2. Apply request eligibility, URL/domain validation, and strict freshness
   filtering before duplicate fusion.
3. Fuse only provider response order with reciprocal-rank fusion (RRF). Native
   numeric scores are not comparable across providers and never enter the
   cross-provider score.
4. Merge only duplicates with a conservative document-identity proof, without
   losing any contributing provider, raw rank, publication claim, or request
   trace. Do not automatically collapse `www`/bare-host variants, trailing
   slashes, reordered queries, merely similar text, mirrors, or syndicated
   copies.
5. Make site diversity a deterministic result-selection/evidence-floor rule,
   not a hidden relevance boost. Latency and credit use remain diagnostics and
   policy constraints, never ranking rewards.
6. Do not add a vector database, mandatory model planner, or production
   model-backed reranker. An optional reranker can be evaluated later only as
   an explicit, versioned benchmark profile against the deterministic result
   set.

This is intentionally stricter than “the provider accepted a freshness
parameter.” A provider-side filter is acquisition evidence; a result needs a
usable result-level time claim to pass the ADR's freshness post-filter. Missing
or ambiguous evidence fails visibly instead of being treated as probably
fresh.

## Normalized signal contract

The adapter boundary should produce bounded typed evidence rather than add
more keys to `SearchResult.metadata`.

### Per-result evidence

```text
ResultEvidence
  document_key
  title
  summary
    primary_text
    excerpts[]                 # ordered, bounded, plain text
    kind                       # snippet | highlight | excerpt | description
  time_claims[]
    kind                       # published | modified | indexed | page_age
    value_utc?
    source_value               # bounded original string
    precision                  # instant | day | month | year | unknown
    contract_confidence        # official_contract | owned_library_contract
                               # | fixture_backed | unverified
  source
    evidence_kind              # web_page | repository | computed_answer
                                # | news | paper | proprietary | unknown
    provider_source_type?
    upstream_engines[]         # important for SearXNG and ddgs
  contribution
    provider
    provider_rank              # zero-based response order
    provider_score?            # finite native value, diagnostic only
    provider_score_semantics   # bounded label or unknown
```

Rules:

- `observed_at` belongs to the provider attempt, not the page. It proves when
  Argus observed the response, never when the content was published.
- `published` may satisfy a strict publication window. `modified`, `indexed`,
  and `page_age` are retained separately and cannot be silently relabeled as
  publication time.
- Version 1 may accept `page_age` for a freshness window only through an
  explicit provider mapping whose official contract defines it as content age.
  The current You.com example alone is insufficient, so it remains diagnostic
  until fixture-backed semantics are approved.
- An absolute timestamp/date is parsed once in the adapter. Relative or
  display-only strings remain diagnostic unless a versioned provider parser
  resolves them against the attempt's injected UTC time.
- Native scores are finite values only. NaN, infinity, malformed values, and
  unknown score semantics are discarded and recorded as normalization
  rejections.
- Summaries are plain text with per-field and total character limits. HTML,
  credentials, request URLs containing keys, and opaque native objects never
  cross the adapter.
- `source_type` is a provider claim, not an authority or trust score.

### Per-attempt evidence

```text
ProviderAttemptEvidence
  provider
  effective_query_hash
  provider_query_hash?
  query_relation              # exact | provider_rewrite | unknown
  request_id?
  provider_session_id?
  resolved_search_mode?
  freshness_translation
  source/domain translation
  safe-search/localization translation
  result_count
  warnings[]
  observed_at
  latency_ms
  call_count
  usage/charge evidence
  normalized outcome/error evidence
```

Provider-returned rewrites, corrections, input interpretations, and executed
queries are evidence. They never replace the retrieval plan's effective query,
change cache identity after execution, or receive a ranking bonus. A material
rewrite with no typed relationship to the requested query is visible in the
trace; a future policy may reject it, but version 1 does not guess semantic
equivalence.

The durable request ledger owns any caller-authorized plaintext query.
Per-provider evidence stores hashes and the typed relation by default; a
bounded plaintext provider interpretation is retained only when the caller's
existing privacy policy explicitly permits it. This avoids multiplying a
private query across traces.

Provider-generated answers are separate evidence artifacts:

- WolframAlpha output is `computed_answer`, not a web page pretending to cite
  the synthetic query URL.
- Tavily answers, Linkup sourced answers, Exa output grounding, Serper answer
  boxes, and SearchAPI answer boxes are retained only when the resolved plan
  explicitly requested that provider feature and citations/source lineage are
  complete.
- An answer never contributes another pseudo-URL to RRF. Its cited source URLs
  may enter ordinary result normalization only if the response supplies the
  same minimum URL/title/provenance evidence as a search result.

## Signal authority matrix

Policy labels:

- **G** — may gate result or request eligibility.
- **R** — affects deterministic ranking/selection.
- **D** — retained diagnostic/provenance only.
- **N/A** — not available from the documented/current shape.

`Rank` in the table always means the provider's response order. A provider
score is never a cross-provider weight.

| Provider | Code-observed signals | Official-contract signals and confidence | Policy use | Latency and credit implications |
|---|---|---|---|---|
| SearXNG | `content`, numeric `score`, `engine`/`engines`, `publishedDate`, author, category; `raw_rank` is response order ([adapter](../../argus/providers/searxng.py)). | The official API documents aggregating configured engines plus `time_range`, categories, language, and safe search, but the public API page does not make every JSON result field a stable cross-engine guarantee ([Search API](https://docs.searxng.org/dev/search_api.html)). **Confidence: medium** and engine-dependent. | Parseable `publishedDate`: **G** only with a versioned engine-aware mapping; response order: **R**; score, engines, author, category: **D**. Missing upstream engines makes aggregator provenance incomplete. | No provider credit, but self-hosted engine fan-out and pagination consume local/upstream capacity. Retaining returned fields adds no call. |
| DuckDuckGo / `ddgs` | `href`, `title`, `body`; no time, score, answer, citation, rewrite, request ID, or upstream engine is retained ([adapter](../../argus/providers/duckduckgo.py)). | `ddgs` is an owned third-party metasearch library. `text()` documents `title/href/body`, response order, `timelimit`, safe search, pagination, and `backend="auto"` across multiple engines ([source](https://github.com/deedy5/ddgs)). It is not a DuckDuckGo-owned API. **Confidence: medium for the library, low for any upstream engine claim.** | Response order: **R**. Snippet and actual backend: **D**. Strict freshness: **G-fail** because text results have no documented result time. | No monetary API credit, but blocking metasearch has network latency and rate/block risk. Forcing a single backend improves provenance without another call. |
| Yahoo | Parsed title/URL/snippet from mutable HTML selectors; no time, score, source type, rewrite, or request ID ([adapter](../../argus/providers/yahoo.py)). | Yahoo documents end-user search refinement, not an HTML search API ([Yahoo Help](https://help.yahoo.com/kb/filter-refine-search-results-yahoo-sln2206.html)). Selectors, ordering semantics, empty state, and fields are uncontracted. **Confidence: low.** | Response order: **R** only after a recognized parse signature. Strict freshness and unrecognized zero-result pages: **G-fail**. Everything else: **D**. | No monetary credit; residential/datacenter behavior, bot defenses, and parser drift affect latency/readiness. Retention adds no call. |
| GitHub | Repository name/URL/description, stars, language, forks, topics, and `updated_at`; response order is forced to `sort=stars`; API score is dropped ([adapter](../../argus/providers/github.py)). | Repository search officially supports best-match or selected sort modes, exposes repository timestamps and search response state, and uses a separate rate-limit resource ([repository search](https://docs.github.com/en/rest/search/search), [rate limits](https://docs.github.com/en/rest/rate-limit/rate-limit)). `updated_at` is repository modification, not publication. **Confidence: high.** | Best-match response order: **R**. Stars and native score: **D**, unless an explicit future ranking plan asks GitHub to sort by stars. `updated_at`: **D**, not strict-publication **G**. `incomplete_results` and rate state: request **G/D**. | Public search spends rate-limit quota, not provider credits. Keeping best-match and returned metadata adds no call; pagination would consume another attempt and is outside plan v1. |
| WolframAlpha | Plain-text computed answer, fixed native score `1.0`, synthetic query URL, raw answer/query metadata; `501` becomes empty ([adapter](../../argus/providers/wolfram.py)). | The LLM API returns bounded computed text, input interpretation and a result link; `501` may include suggestions ([LLM API](https://products.wolframalpha.com/llm-api/documentation)). It is not a ranked web result or page citation. **Confidence: high.** | Evidence kind and successful interpretation: **G** for computed-answer eligibility; provider rank is trivial and must not compete with web RRF; answer, interpretation, suggestion: **D/artifact**. Strict publication freshness: **G-fail**. | Consumes Wolfram quota. Raising `maxchars` or requesting additional API products may add payload/latency; retaining the existing bounded response adds no call. |
| Brave | `url/title/description`; response order; fixture exposes `age` but adapter drops it; no native score, source type, request ID, answer, citation, or rewrite is retained ([adapter](../../argus/providers/brave.py)). | Official Web Search supports freshness, bounded `extra_snippets`, safe search, and `query.original`/`more_results_available` ([web guide](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)). The guide does not establish the checked-in fixture's `age` as a strict publication timestamp. **Confidence: high for controls/snippets, medium for result time.** | Response order: **R**. Description/extra snippets and query evidence: **D**. Strict freshness: **G-fail** until a documented parseable result time is retained; provider filter alone is not proof. | Search consumes quota. Extra snippets are same-response payload but remain opt-in; no extra request is needed. Pagination is another call and is outside plan v1. |
| Tavily | `content`, native `score`, `published_date`; response order. Request ID, response time, usage, answer, query, and auto-parameters are dropped ([adapter](../../argus/providers/tavily.py)). | Official search returns relevance-ordered results with score/content, executed `query`, optional generated answer, `request_id`, `response_time`, auto-parameters, and `usage.credits`; date filters may use publish or last-updated dates ([search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)). The current public result schema does not clearly guarantee `published_date` on every result. **Confidence: high for rank/request evidence, medium for result time.** | Response order: **R**; native score: **D**. A clearly typed `published_date`: **G**; ambiguous publish-or-update evidence: **G-fail**. Request/usage evidence: acceptance **G/D**. Optional answer: separate artifact. | Basic search is one credit and advanced is two; auto-parameters may choose advanced. Pinning basic bounds cost. Raw content and generated answers may add latency/payload; neither is required for ranking. |
| Exa | Stale snake_case `published_date`, top-level `text` and numeric `score`; provider ID and response order ([adapter](../../argus/providers/exa.py)). | Current search uses `publishedDate`, optional `contents.text/highlights/highlightScores`, `requestId`, `resolvedSearchType`, and `costDollars`; optional output can include grounding citations ([search reference](https://exa.ai/docs/reference/search)). The reference does not define a universal comparable result score. **Confidence: high.** | Parseable `publishedDate`: **G**; response order: **R**; highlights and highlight scores, resolved type, cost, request ID: **D**. Do not read the stale fixture score into fusion. Grounded output is a separate artifact. | Search consumes credits/cost. Requesting contents/highlights or generated output increases payload and may increase charge/latency; default ranking needs neither. |
| Linkup | `name/url/content`, response order; no result date, source type, score, answer/citation, query rewrite, or request ID ([adapter](../../argus/providers/linkup.py)). | Official `searchResults` include `content` and `type`; request controls include dates/domains/count. `sourcedAnswer` with inline citations is a distinct output; depth is `fast`, `standard`, or `deep` ([search reference](https://docs.linkup.so/pages/documentation/endpoints/search/reference)). Search-result examples do not expose a publication date. **Confidence: high.** | Response order: **R**; content/type: **D**. Strict freshness: **G-fail** because request filtering cannot be verified per result. Sourced answers remain separate artifacts. | Every search consumes plan credits. Standard/deep and sourced answers can cost more time/credits than fast search results; keep the resolved depth/output explicit and bounded. |
| Parallel | Legacy `excerpt`/`snippet`, response order; no time, IDs, warnings, usage, source type, or rewrite is retained ([adapter](../../argus/providers/parallel.py)). | Current `/v1/search` returns relevance-ordered results with `publish_date`, `excerpts[]`, `search_id`, `session_id`, warnings, and usage. Advanced settings control result count, source policy, excerpt size, and indexed-vs-live freshness ([search reference](https://docs.parallel.ai/api-reference/search/search), [advanced settings](https://docs.parallel.ai/search/advanced-search-settings)). **Confidence: high.** | Parseable `publish_date`: **G**; response order: **R**; excerpts: **D**; IDs/warnings/usage/fetch policy: acceptance **G/D**. No native cross-provider score. | Search consumes credits. Live fetch significantly increases latency; modes trade speed for retrieval/compression quality. Keep one bounded mode and fetch policy in the resolved plan; latency earns no rank. |
| Serper | Organic `link/title/snippet/position/date`; response order; guessed credit/rate fields; no answer boxes, query evidence, or request ID retained ([adapter](../../argus/providers/serper.py)). | The accessible first-party site demonstrates organic result shape and real-time Google results, but detailed authenticated field/error documentation is not publicly verifiable ([Serper](https://serper.dev/)). **Confidence: medium for organic fields, low for date/credit semantics.** | `position`/response order: **R**. `date`, snippets, guessed headers, answer/knowledge fields: **D** until redacted first-party fixtures define them. Strict freshness: **G-fail**. | A request consumes credits with incompletely documented edge-case charging. Retention adds no call; pagination or answer-specific calls are not authorized. |
| You.com | Web result description, first `snippets[]`, response order; adapter drops news, `page_age`, metadata query/search UUID/latency and defaults safe search off ([adapter](../../argus/providers/you.py)). | Official search returns web/news sections, snippets, `page_age`, and metadata `search_uuid`, executed query, and latency; it supports freshness and optional live crawl ([search reference](https://you.com/docs/api-reference/search/v1-search)). The page shows but does not precisely define `page_age` as publication time. **Confidence: high for shape, medium for time semantics.** | Per-section response order: **R**; section/source kind, snippets, UUID/query/latency: **D**. `page_age`: **D** pending a versioned semantic mapping; strict publication freshness otherwise **G-fail**. | Search consumes credits. Live crawling is optional, billable, and adds latency; keep it off for ranking. Retaining same-response metadata adds no call. |
| Valyu | Description/content, `relevance_score`, `source_type`, `publication_date`, response order, transaction ID, total characters/cost; query/source counts/warnings are dropped ([adapter](../../argus/providers/valyu.py)). | Official search returns relevance-ordered results with `relevance_score`, `source_type`, `publication_date`, source counts, original query, transaction ID, cost, and warnings. `fast_mode` bypasses LLM query rewriting and reranking and forces web-only search ([search reference](https://docs.valyu.ai/api-reference/endpoint/search)). **Confidence: high.** | Parseable `publication_date`: **G**; response order: **R**; native score/source type: **D**; transaction/cost/warnings and resolved fast mode: acceptance **G/D**. | Every call may incur reported dollar cost. Turning off fast mode changes source coverage and enables provider rewrite/rerank, with possible latency/cost effects; keep current fast mode explicit rather than silently changing it. |
| SearchAPI | Organic snippet, `position`, display link and `date`; search lifecycle metadata is mislabeled as credit info; answer boxes/query display are dropped ([adapter](../../argus/providers/searchapi.py)). | Official Google response documents organic positions, request lifecycle/latency, search parameters, displayed query, answer/knowledge sections, date controls, and sometimes organic date strings ([Google engine](https://www.searchapi.io/docs/google)). **Confidence: high for shape, medium for organic date semantics.** | `position`/response order: **R**. Only a versioned parser for an absolute, publication-semantic organic date may be **G**; other date strings: **D/G-fail**. Query display, lifecycle, and answer boxes: **D/artifact**. | Each search page consumes credit. Google result count is effectively one page of ten; extra pages would be extra calls and are outside plan v1. Same-response answer/lifecycle retention adds no call. |

## Eligibility and freshness

Freshness evaluation happens in this order:

1. Resolve the plan's inclusive UTC date range as specified by ADR 0002.
2. Record the exact provider translation. If a provider cannot express the
   requested range, it may still execute only when enough result-level evidence
   exists for Argus to post-filter safely.
3. Normalize every returned time claim without overwriting its kind.
4. Keep a result only when at least one policy-approved `published` claim is
   parseable and within the window. Conflicting publication claims are retained
   and the result is rejected until a deterministic conflict policy exists.
5. Record every rejection (`missing_time`, `unparseable_time`,
   `wrong_time_kind`, `out_of_range`, or `conflicting_time`) and the provider
   translation.

Version 1 strict-freshness capability is therefore:

| Capability | Providers |
|---|---|
| Official result publication field can gate now | Exa, Parallel, Valyu |
| Can gate after a fixture-backed, versioned field/semantics migration | SearXNG, Tavily, SearchAPI |
| Provider offers filtering or an age-like field, but result publication is not proven | Brave, Linkup, You.com |
| No suitable current result-level publication evidence | ddgs text, Yahoo HTML, GitHub repository search, WolframAlpha computed answers, Serper public contract |

This does not permanently disable the second and third groups. It prevents
provider request filters, repository modification timestamps, indexed times,
or page-age labels from being silently promoted to publication facts.

A freshness-scoped empty response is a proven `empty` only when:

- the provider successfully applied the exact declared window under a strict
  contract;
- the contract/fixture distinguishes a successful empty response from parser,
  policy, or provider failure; and
- the trace retains that translation and success evidence.

Otherwise, zero retained results after freshness rejection is
`freshness_unproven` and maps through the ADR's fail-closed outcome rule.

## Deterministic duplicate and ranking policy

### Stage 1: normalize and reject

Before ranking:

- validate and canonicalize each HTTP(S) URL;
- apply explicit domain and strict freshness policy;
- require the scorecard's minimum provider/egress/machine/source provenance;
- record provider response order as `provider_rank`;
- reject malformed results with structured reasons.

Provider-native scores never determine eligibility unless a future explicit
quality threshold is provider-specific, versioned, and fixture-backed. There
is no such threshold in version 1.

### Stage 2: proven-document fusion

Group results by a versioned, conservative document key. Version 1 accepts
HTTP(S), lowercases and IDNA-normalizes the host, rejects user information,
removes default ports, normalizes dot segments and unreserved percent
encoding, and removes fragments. It preserves scheme, path case, trailing
slash, query order, duplicate query parameters, empty values, key-only
parameters, and every parameter name and value.

Only stronger evidence may join different document keys: a provider-supplied
documented canonical URL, a proven safe redirect/canonical chain, or a
versioned normalized-content fingerprint from extraction. `www` variation,
tracking parameters, title/snippet similarity, and syndication are weak
diagnostics only. They do not drive fusion. This replaces the current split
behavior where RRF keys raw URLs and a later dedupe step applies unsafe,
different assumptions.

For each group:

```text
rrf_score = sum(1 / (60 + provider_rank + 1))
```

There is at most one contribution per provider per document cluster. All
contributor identities, ranks, canonical contributions, time claims, and
request traces remain attached. Select the outward representative
deterministically: verified canonical URL first, then best provider rank,
provider enum value in lexical order, then canonical URL byte order. Title
and summary come from that same observation; Argus does not splice fields from
different sources into synthetic source text. Other bounded excerpts remain
provenance. A longer snippet does not earn a relevance bonus.

Sort fused groups by:

1. descending RRF score;
2. ascending best provider rank;
3. descending contributor count;
4. ascending lexicographically smallest contributor provider enum value;
5. ascending cluster sort key (verified canonical key, otherwise the smallest
   member document key).

The extra keys make ties independent of dictionary completion order. Native
scores, latency, provider tier, cost, and snippet length are excluded.

Near-duplicate text, mirrors, and syndication remain separate in version 1.
Their suspected relationship is diagnostic. Collapsing them without a stable
content identity risks deleting independent evidence and would overlap the
extraction/evidence-envelope work.

### Stage 3: bounded site diversity

Use a registrable-domain site key from a pinned Public Suffix List snapshot;
subdomains of one registrable domain are one site. The snapshot identifier
belongs to `domain_policy_version`. A different site key is useful coverage
evidence but does not by itself prove corporate or editorial independence.

- `grounding` and `recovery`: preserve fused order. One eligible result may be
  sufficient at runtime; the frozen evaluator judges authority and intent
  satisfaction.
- `discovery`: preserve fused order and report unique-site counts; diversity is
  diagnostic under the accepted scorecard.
- `research`: first take the earliest result from each new site until the
  effective two-site floor is met, then fill in fused order with a two-per-site
  soft cap, and finally backfill deferred results only when available diversity
  cannot fill the request. Emit base rank, output rank, site key, and every
  coverage/skip/backfill reason.

The runtime research floor is `min(3, result_limit)` URL-backed document
clusters across `min(2, required_clusters)` site keys. The frozen corpus uses a
result limit of at least three, so its full floor remains three sources across
two sites. Site diversity helps produce candidates but does not prove
relevance or independence. A result that misses its effective floor fails
visibly as internal `research_evidence_floor_unmet`; it is not rescued by a
diversity statistic or mislabeled as provider failure. Issue #66 owns the
stable surface outcome mapping.

The diversity algorithm affects selection and is therefore part of
`ranking_policy_version` and the cache fingerprint.

## Answers, citations, rewrites, and source types

| Signal | Eligibility | Deterministic ranking | Durable diagnostic/evidence |
|---|---|---|---|
| Publication claim | Required for a declared strict freshness window. | No recency bonus. Newer is not automatically better than canonical/correct. | Value, kind, precision, original bounded value, parser/policy version, pass/fail reason. |
| Provider response rank | Valid only after result normalization. | Sole provider-native input to RRF. | Raw rank and canonical contribution for every contributor. |
| Provider numeric score | Finite-value validation only. | Never cross-provider; no default same-provider re-sort. | Value plus provider-defined semantics or `unknown`. |
| Snippet/highlight/excerpt | Minimum non-empty evidence checks when the source supplies text. | No length/highlight-score bonus. | Typed, ordered, bounded excerpts and chosen-display reason. |
| Generated/computed answer | Must be explicitly requested and correctly typed; generated answers require complete citations to be evidence artifacts. | Never a pseudo-result in web RRF. | Answer kind, provider, cited URLs, request ID, spend, and generation settings when returned. |
| Citation | Missing citations reject a generated answer that claims grounded evidence. | Cited URL may rank only as an independently normalized result. | Answer-to-source edges. |
| Query rewrite/correction | A material unclassified rewrite may fail request-evidence completeness; it never mutates the plan. | No bonus or penalty in v1. | Requested query, provider query, relation, correction/input interpretation. |
| Source type/evidence kind | Prevents a computed answer, repository, paper, news item, or proprietary record from masquerading as a generic web page. Missing applicable source provenance fails acceptance. | No provider-type or “authoritative” bonus. | Provider claim and normalized kind; benchmark rubric assesses required source characteristics. |
| Request ID/session ID | Required when the provider supplies it and the evidence schema marks it applicable. | None. | Secret-free support/correlation evidence; provider session IDs are not Argus session identity. |
| Latency/usage/cost | Deadline, spend, and durable-accounting gates. | None. | Attempt and end-to-end latency; reserved/estimated/actual spend and reconciliation. |

## Bounded alternatives considered

### A. Native-score weighted fusion

Rejected. Tavily and Valyu expose relevance-like scores, SearXNG aggregates
engine scores, Exa's current checked-in fixture contains a stale score, and
most providers expose only ordering. Their scales and meanings are not
commensurate. Calibration would require provider-specific live datasets and
would make a missing score change provider weight silently.

Quality potential is uncertain; deterministic behavior and contract confidence
are poor. It adds no provider call but creates a high evidence burden.

### B. Provider-order RRF plus deterministic diversity

Accepted. It works for every provider, preserves multi-provider agreement,
keeps attribution exact, and can be reproduced from immutable cache evidence.
The only result-order inputs are provider order and the versioned host
selection policy.

It adds no provider call, model call, or material latency. Richer provider
fields improve evidence quality and eligibility without being mistaken for
comparable relevance scores.

### C. Mandatory semantic/model reranking

Rejected. It would add a production model dependency, latency, evaluator/model
drift, new failure modes, and potentially usage cost. It also conflicts with
the scorecard's no-production-LLM requirement and ADR 0002's version-1 limits.

An optional future experiment may rerank only the top 20 deterministic
candidates within the existing operation deadline. It must:

- be an explicit versioned evaluation profile, never an implicit fallback;
- retain base RRF rank and every signal used;
- make no provider call and obey the same persistence gate;
- win the frozen scorecard without a mode regression before promotion.

Until then, the stable production policy ends after deterministic diversity.
An offline experiment failure is recorded visibly as experiment evidence and
cannot alter the deterministic production response or cache.

## Implementation slices and proof

This decision can land without a rewrite:

1. Add typed result/attempt evidence and bounded serializers while
   dual-reading current fixture aliases.
2. Migrate provider request/response contracts in the order already selected
   by the drift inventory. Do not enable paid rich-content or answer options by
   default.
3. Move proven document clustering before RRF; retain every contribution; add
   stable tie-breaks.
4. Add strict freshness rejection and the research site-selection pass.
5. Persist complete ranking/freshness/request evidence through the canonical
   evidence-envelope ticket and bump `ranking_policy_version`,
   `freshness_policy_version`, or `result_normalization_version` narrowly.

Hermetic contract fixtures must prove:

- every provider matrix field above is either normalized or explicitly absent;
- provider-native scores cannot alter cross-provider order;
- raw response order, not async completion order, supplies `provider_rank`;
- proven document duplicates fuse before RRF and retain all contributors;
- `www`, trailing slash, query order, and tracking hints alone never fuse;
- RRF ties have identical order under provider dictionary permutations;
- missing, ambiguous, out-of-window, and conflicting dates fail closed;
- request filtering alone cannot manufacture a result publication date;
- research diversity is deterministic and visibly reports relaxation;
- answer artifacts never become synthetic web citations;
- provider rewrites never mutate plan/cache identity;
- latency, tier, and cost never change result order;
- no provider-native payload, secret, or authorization-bearing URL survives
  serialization;
- persisted evidence can recompute the exact returned order and attribution.

The accepted 24-query corpus then compares the unchanged baseline with this
candidate under the same provider snapshot. Speed and ordinary spend are
reported but do not contribute to competitiveness. A provider-rich option
(Exa contents, Linkup sourced answer/depth, Parallel live fetch, You live
crawl, Tavily answer/advanced, or Valyu non-fast mode) is a separate resolved
profile and may not be promoted merely because it returns more text.

## Open contract proofs, not design questions

The policy is settled, but these provider facts still need authorized,
redacted fixtures or official clarification before implementation can mark
them high confidence:

- SearXNG JSON result fields by enabled engine and strict-empty behavior;
- Tavily's current per-result publication field and whether it is publish or
  modified time;
- Brave result-time semantics;
- You.com `page_age` semantics;
- SearchAPI organic `date` formats/semantics;
- Serper detailed date, request, error, and usage fields;
- Linkup successful empty response and any result-level date;
- actual `ddgs` backend provenance when `backend="auto"`.

Those gaps fail closed for the affected capability. They do not justify live
paid calls during research, and they do not block implementing deterministic
fusion for already-proven signals.
