# Provider and extraction contract drift inventory

Date: 2026-07-26

Issue: [#60 — Inventory Argus provider and extraction contract drift](https://github.com/Khamel83/argus/issues/60)

Decision boundary: [accepted stability and competitive evidence scorecard](../scorecards/stability-competitive.md)

## Executive conclusion

Argus's provider-neutral `SearchResult` and `ExtractedContent` boundaries are
the right compatibility seams, but the adapters currently normalize too
little. The highest-confidence static breakages are:

1. **Parallel is on a legacy contract.** The adapter calls
   `/v1beta/search`, sends a legacy beta header and top-level `max_results`,
   and reads a scalar `excerpt`. The current API is `/v1/search`, returns
   `excerpts[]`, `publish_date`, `search_id`, `session_id`, `warnings`, and
   `usage`, and places result-count/freshness controls under
   `advanced_settings`. The official reference explicitly labels
   `/v1beta/search` legacy and links a migration guide
   ([Parallel search reference](https://docs.parallel.ai/api-reference/search/search),
   [migration guide](https://docs.parallel.ai/search/search-migration-guide)).
2. **Exa request and response casing is stale and content retrieval is not
   requested.** The adapter sends `num_results` and reads `published_date`;
   the current contract uses `numResults` and `publishedDate`. Search results
   only carry `text` when content is requested under `contents`; the adapter
   requests no contents, so its normalized snippets will ordinarily be empty.
   It also drops `requestId` and `costDollars`
   ([Exa search reference](https://exa.ai/docs/reference/search)).
3. **Crawl4AI's result shape is not normalized.** The lock resolves Crawl4AI
   0.8.6, while the adapter treats `result.markdown` as a string and never
   checks `result.success` or `result.error_message`. Current Crawl4AI exposes
   a `MarkdownGenerationResult` (`raw_markdown`,
   `markdown_with_citations`, `references_markdown`, optional
   `fit_markdown`) and explicit success/error fields
   ([CrawlResult reference](https://docs.crawl4ai.com/core/crawler-result/)).
4. **Firecrawl's declared and called versions disagree.** The adapter says it
   uses v2 but calls `/v1/scrape`. Current v2 is `/v2/scrape`, supports
   explicit formats, cache age, timeout, proxy behavior, change tracking,
   metadata status/error, and distinct `402`, `429`, and `500` responses
   ([Firecrawl scrape reference](https://docs.firecrawl.dev/api-reference/endpoint/scrape)).
5. **Freshness and billing evidence are widely discarded.** The accepted
   scorecard requires query-specific freshness and truthful policy/spend
   evidence, yet `SearchQuery` has no typed freshness window and no adapter
   consistently translates metadata into provider filters. Tavily `usage` and
   `request_id`, Exa `costDollars` and `requestId`, Parallel `usage` and
   `warnings`, You `page_age` and search UUID, and most retry/reset signals
   never reach normalized traces.
6. **Errors collapse into opaque strings.** Most adapters turn every HTTP,
   timeout, authentication, exhaustion, parse, and provider failure into
   `ProviderTrace(status="error", error=str(exc))`. This cannot preserve the
   scorecard's distinctions among invalid request, authentication rejection,
   policy rejection, timeout, provider failure, and empty success.

The smallest safe response is a shared, provider-private compatibility layer:
dual-read old/new response aliases where needed, explicit request translation,
an allowlisted usage/provenance projection, and one HTTP/provider-error
classifier. Existing caller-facing result fields need not change.

## Method and safety boundary

This inventory compared:

- all 14 adapters in `argus/providers/`;
- `argus/models.py`, extraction models, the complete fallback orchestrator,
  extractor implementations, and the relevant fixtures;
- the accepted scorecard's hard gates and canonical outcomes;
- official provider documentation, first-party API references, first-party
  source repositories, and first-party library documentation.

No provider API key was read, printed, or used. No paid provider request was
made. Static verification included:

```text
uv run pytest -q tests/test_providers.py tests/test_extraction.py \
  tests/test_trafilatura_normalization.py tests/test_playwright_lifecycle.py \
  tests/test_youtube_extractor.py
114 passed
```

Those tests prove the repository's current fixtures, not current live provider
compatibility. In particular, `tests/test_providers.py` has response fixtures
for SearXNG, Brave, Serper, Tavily, Exa, SearchAPI, Valyu, and GitHub, but no
normalization fixtures for DuckDuckGo, Yahoo, WolframAlpha, You.com, Linkup, or
Parallel. Its common `BaseProvider` contract test also omits those last five
adapters plus DuckDuckGo and Yahoo.

### Bounded production canaries

Three authenticated calls were made through the canonical homelab HTTP
authority. They exercised only the already-configured free path and local
extraction; they did not expose provider credentials or initiate a billable
provider call.

- A `free_only=true` grounding search returned HTTP 200 with five SearXNG
  results. SearXNG, DuckDuckGo, and Yahoo traces were successful, Wolfram was
  empty, and the non-free providers were policy-skipped. The response had a
  durable `search_run_id`, but no canonical overall `outcome`.
- `/api/provider-health` returned all 14 registered providers and overall
  `degraded`. SearXNG, DuckDuckGo, Yahoo, GitHub, and Wolfram had health
  evidence; several enabled credentialed providers had no health evidence.
  This proves registration is not the same as recent usability.
- Extraction of the public PostgreSQL continuous-archiving documentation
  returned HTTP 200 through Trafilatura, 7,031 words, `quality_passed=true`,
  `is_complete=true`, provenance, and an `extraction_run_id`. The HTTP response
  omitted the internally recorded attempts and any canonical outcome or
  rejection object.

These are unretained, point-in-time observations from 2026-07-26, not an
accepted scorecard evidence package: no `observed_at`/`expires_at`, release
identity, eligibility snapshot, run artifact, or redacted run IDs were
retained. They support the static inventory but do not establish a promotable
health verdict or prove the live compatibility of skipped credentialed
providers.

## Cross-provider static findings

### Normalized request is too narrow for the scorecard

`SearchQuery` exposes `query`, `mode`, `max_results`, provider selection,
`free_only`, caller fields, and an untyped `metadata` bag. None of the
adapters maps a canonical freshness window. Current provider contracts expose
the following safe controls:

- SearXNG `time_range` (`day`, `month`, `year`) and `safesearch`
  ([official Search API](https://docs.searxng.org/dev/search_api.html)).
- Brave `freshness` (`pd`, `pw`, `pm`, `py`, or a date range), country,
  language, safe search, extra snippets, and pagination
  ([official web search guide](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)).
- Tavily `time_range`, `start_date`, `end_date`, topic, domains, and country
  ([official search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search)).
- Exa `startPublishedDate` and `endPublishedDate`
  ([official search reference](https://exa.ai/docs/reference/search)).
- SearchAPI `time_period`, `time_period_min`, and `time_period_max`
  ([official Google engine reference](https://www.searchapi.io/docs/google)).
- You `freshness` (`day`, `week`, `month`, `year`, or date range)
  ([official search reference](https://you.com/docs/api-reference/search/v1-search)).
- Linkup `fromDate` and `toDate`
  ([official search reference](https://docs.linkup.so/pages/documentation/endpoints/search/reference)).
- Valyu `start_date` and `end_date`
  ([official search reference](https://docs.valyu.ai/api-reference/endpoint/search)).
- Parallel `advanced_settings.source_policy.after_date` and
  `fetch_policy.max_age_seconds`
  ([official advanced settings](https://docs.parallel.ai/search/advanced-search-settings)).
- GitHub query qualifiers such as `pushed:`/`created:` are part of the search
  query rather than a separate repository-search date parameter
  ([official repository-search endpoint](https://docs.github.com/en/rest/search/search#search-repositories)).

The current implementation sends none of these from a common contract. A
time-sensitive scorecard query can therefore declare an acceptable age window
that the provider adapters cannot enforce or report.

### Native evidence is discarded

`SearchResult.metadata` and `ProviderTrace.credit_info` are untyped catch-alls,
but adapters still discard useful first-party evidence:

- provider request/run IDs;
- resolved search mode and warnings;
- response-reported cost/usage;
- publication/page age;
- retry/reset information;
- provider-native error code/tag and safe request reference.

This is a normalization omission, not a reason to expose raw provider payloads.
Only an allowlist of scalar or bounded values should cross the adapter boundary.

The HTTP schemas discard even normalized evidence that already exists in the
internal models and ledgers:

- `SearchResultSchema` omits `source_type`, normalized publication/freshness
  evidence, `raw_rank`, and bounded provider metadata.
- `ProviderTraceSchema` omits the internal trace's `credit_info` and `egress`.
- `SearchResponse` has no canonical outcome, cache origin/age, or normalized
  failure summary.
- `ExtractResponse` omits the extraction attempt records already persisted by
  the ledger. It also has no canonical outcome or structured rejection
  boundary. Normal extractor/provider failures are caught by the orchestrator
  and returned as HTTP 200 content with `error`/quality fields. The route maps
  uncaught authority, persistence, or unexpected failures to the same HTTP 503
  "could not be durably recorded" response. The contract therefore needs both
  canonical returned outcomes and a narrower durable-recording error boundary;
  the 503 is not the normal provider-failure path.

This is an additive projection problem; it does not require exposing raw
provider payloads or replacing current response fields.

### Raw exception strings can carry secrets

The shared error normalizer is also a confidentiality repair. SearchAPI and
Wolfram put `api_key`/`appid` in request query parameters, call
`raise_for_status()`, and then retain `str(HTTPStatusError)` as trace error
text. HTTP client exception strings can include the request URL, so these two
adapters have a concrete credential-leak path into logs or persisted traces.
Other adapters similarly retain raw exception text even when credentials are
in headers or bodies.

The minimum safe rule is to construct errors from allowlisted status,
provider code, retry metadata, and a bounded scrubbed summary. Never persist
the raw exception, request URL with query string, response body, or
authorization material.

### Error classification is not compatible with canonical outcomes

Except for Wolfram's explicit `501 -> empty`, Yahoo's parse-empty result, a
generic Valyu `success:false`, and GitHub's blanket `403 -> rate limited`,
adapters rely on `raise_for_status()` and stringify exceptions. Current
first-party contracts show why status alone is insufficient:

- GitHub primary or secondary rate limiting can be `403` **or** `429`; clients
  must consider `Retry-After`, `x-ratelimit-remaining`, and
  `x-ratelimit-reset`
  ([GitHub troubleshooting](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#rate-limit-errors)).
- Exa distinguishes invalid input (`400`), auth (`401`), exhausted credits or
  API-key/team budget (`402`), permission/policy (`403`), validation (`422`),
  rate limit (`429`), and crawl timeout (`504`) with stable tags
  ([Exa error codes](https://exa.ai/docs/reference/error-codes)).
- Parallel distinguishes `401`, exhausted balance `402`, validation `422`,
  timeout `408`, rate limit `429`, and retriable `500`/`502`/`503`
  ([Parallel errors](https://docs.parallel.ai/resources/warnings-and-errors)).
- You distinguishes `401`, exhausted credits `402`, missing scope `403`,
  invalid combinations `422`, and rate limit `429`
  ([You error reference](https://you.com/docs/error-handling/error-code-reference)).
- Tavily rate limiting is `429` with `retry-after`
  ([Tavily rate limits](https://docs.tavily.com/documentation/rate-limits)).
- Brave rate limiting is `429`, with limit, policy, remaining, and reset
  headers; only successful requests count against quota
  ([Brave rate-limit guide](https://api-dashboard.search.brave.com/documentation/guides/rate-limiting)).

The compatibility layer should retain `http_status`, bounded
`provider_code`, `retry_after_seconds`, `rate_limit_reset`, and a normalized
failure category without persisting response bodies or credentials.

## Search-provider inventory

### 1. SearXNG

**Current adapter.** `GET {base}/search` with `q`, `format=json`, and
`pageno=1`; parses `results[].url/title/content/score/engines/engine`,
`publishedDate`, author, and category.

**Official contract and drift.** GET/POST `/` and `/search` are supported.
JSON must be enabled by the instance; otherwise format requests return `403`.
The API also supports categories, language, page, time range, and safe search
([SearXNG Search API](https://docs.searxng.org/dev/search_api.html)). The
adapter ignores `max_results`, language, safe search, category/mode, and
freshness, and does not distinguish instance policy `403` from provider
failure. It places egress/machine in result metadata but not on
`ProviderTrace.egress`.

**Smallest shim.** Translate only supported canonical controls; truncate after
normalization to `max_results`; classify JSON-disabled `403` as unready or
policy/configuration failure; set trace egress consistently.

**Live validation.** A configured self-hosted instance is required to prove
JSON enablement, engine readiness, actual time-range support, and result
fields. This is free but environment-dependent.

### 2. DuckDuckGo / `ddgs`

**Current adapter.** Calls synchronous `DDGS().text(query,
max_results=...)` directly inside an async method and parses
`href/title/body`.

**Owned library contract and drift.** `ddgs` is a third-party metasearch
library, not a DuckDuckGo API. Its current owner documentation confirms
`title/href/body`, `region`, `safesearch`, `timelimit`, pagination, and a
multi-engine `backend="auto"` default
([ddgs source](https://github.com/deedy5/ddgs)). The lock currently resolves
9.14.1. Because Argus does not set `backend="duckduckgo"`, the provider name
`duckduckgo` can misstate the actual upstream engine. The blocking call also
runs on the event loop despite `probe_capability=BLOCKING_UNSUPPORTED`.

**Smallest shim.** Either rename provenance to `ddgs` and retain a bounded
upstream-engine field, or force the DuckDuckGo backend. Run the blocking call
in a worker thread with the configured timeout; map canonical safe-search and
relative freshness to `timelimit`.

**Live validation.** A no-key probe can verify installed-version behavior, but
it is scraping/metasearch and can be rate-limited or blocked. No stable
DuckDuckGo-owned search API contract exists for this adapter.

### 3. Yahoo

**Current adapter.** Scrapes `https://search.yahoo.com/search`, parses
`.algo-sr`, and falls back to a regex. No result is returned as `empty` plus
the error text “HTML may have changed.”

**Official capability and drift.** Yahoo documents end-user category and
timeframe filtering, not a supported HTML search API
([Yahoo Help](https://help.yahoo.com/kb/filter-refine-search-results-yahoo-sln2206.html)).
Selectors, redirect wrappers, and error/rate semantics are therefore
uncontracted. A zero parse count cannot safely distinguish a genuine empty
search from markup, consent, bot-block, or localization drift.

**Smallest shim.** Keep a captured first-party HTML fixture and explicit parse
signature/version. Treat “HTTP 200, parser matched zero result containers” as
`parse_error` unless an independently recognized Yahoo empty-state marker is
present. Preserve status and final URL, never raw HTML.

**Live validation.** A bounded, no-key residential and datacenter probe is
needed to refresh selectors and identify consent/bot pages. It should not be
part of routine hermetic tests.

### 4. GitHub

**Current adapter.** Calls repository search with `q`, `per_page`, and
`sort=stars`; maps repository URL/name/description/stars/language/forks/topics
and `updated_at`.

**Official contract and drift.** The response shape is broadly compatible,
but sorting every query by stars changes relevance semantics. GitHub's search
bucket is separate from general REST limits; response headers include limit,
remaining, used, reset, and resource
([rate-limit endpoint](https://docs.github.com/en/rest/rate-limit/rate-limit)).
The adapter drops reset/resource, omits the recommended API-version header,
and assumes every `403` is rate limiting even though rate limiting can also be
`429` and `403` can mean other authorization/policy failures
([troubleshooting](https://docs.github.com/en/rest/using-the-rest-api/troubleshooting-the-rest-api#rate-limit-errors)).

**Smallest shim.** Default to best-match ordering; request stars only for an
explicit ranking mode. Capture reset/resource and classify by status, body
code/message, remaining quota, and `Retry-After`.

**Live validation.** An unauthenticated public repository search is free but
changes IP quota; authenticated limits and private visibility require a token.
No live call is needed to add contract fixtures.

### 5. WolframAlpha

**Current adapter.** Calls the LLM API with `input`, `appid`, and
`maxchars=1000`; maps plain text to a synthetic Wolfram URL. It correctly
treats `501` as empty.

**Official contract and drift.** The LLM API is GET, accepts `maxchars` and
several locale/timeout controls, returns `501` when input cannot be interpreted
(sometimes with suggestions), `400` for missing input, and `403` for invalid
or missing AppID
([Wolfram LLM API](https://products.wolframalpha.com/llm-api/documentation)).
The adapter discards the useful bounded `501` suggestion body and does not
distinguish auth from provider failure. The response is a computed answer, not
a web citation; the synthetic query URL is provenance, not proof that every
line of the returned text appeared on that page.

**Smallest shim.** Keep `501` as empty with an optional sanitized suggestion;
classify `400` and `403`; mark metadata `evidence_kind=computed_answer`.

**Live validation.** Requires an AppID and consumes quota. Do not call during
this research.

### 6. Brave

**Current adapter.** Correct endpoint/auth and core `web.results`
`url/title/description` parsing. It collects `X-RateLimit-Limit`,
`Remaining`, and a presumed `Used`.

**Official contract and drift.** Brave supports `freshness`, languages,
country, safe search, extra snippets, count up to 20, and
`query.more_results_available`
([web search guide](https://api-dashboard.search.brave.com/app/documentation/web-search/get-started)).
Current rate headers are Limit, Policy, Remaining, and Reset
([rate-limit guide](https://api-dashboard.search.brave.com/documentation/guides/rate-limiting));
`Used` is not the documented replacement for Policy/Reset. The adapter passes
unbounded `max_results`, drops freshness and extra snippets, and collapses
`429`.

**Smallest shim.** Clamp count to 20; map freshness and safe-search; preserve
bounded extra snippets only if requested; record Policy/Reset and classify
`429`.

**Live validation.** Requires a key and consumes quota.

### 7. Tavily

**Current adapter.** Core endpoint and `query/max_results` are compatible; the
key is sent in the JSON body. It maps result URL/title/content/score and
`published_date`.

**Official contract and drift.** Current docs prefer
`Authorization: Bearer`; support search depth, topic, freshness/date ranges,
domains, raw content, and auto-parameters; and return `usage.credits`,
`request_id`, and `response_time`
([search reference](https://docs.tavily.com/documentation/api-reference/endpoint/search),
[authentication](https://docs.tavily.com/documentation/api-reference/introduction)).
Basic costs one credit and advanced costs two; `auto_parameters` can select
advanced unless depth is pinned. The adapter drops all usage/request evidence
and freshness controls.

**Smallest shim.** Move auth to the header while accepting body-auth fixtures
during migration; pin `search_depth=basic` unless policy authorizes more;
capture usage/request ID/response time; map freshness.

**Live validation.** Requires a key and consumes a credit, even on the free
monthly plan.

### 8. Exa

**Current adapter.** Uses the correct `/search` endpoint but sends
`num_results`, reads `published_date`, and expects top-level result `text` and
`score`.

**Official contract and drift.** Current request fields are camelCase,
including `numResults`, `startPublishedDate`, and `endPublishedDate`.
Contents must be requested under `contents`; results expose
`publishedDate`, author, text/highlights when requested, and the response
includes `requestId` and `costDollars`
([search reference](https://exa.ai/docs/reference/search)).
The official guide explicitly warns that content parameters must be under
`contents`
([coding-agent guide](https://exa.ai/docs/reference/search-api-guide-for-coding-agents)).
The current fixture mirrors the stale snake_case adapter rather than the
official response.

**Smallest shim.** Send `numResults`; request bounded
`contents.highlights` or `contents.text`; dual-read
`publishedDate/published_date` during migration; derive snippet from ordered
highlights then text; retain request ID and reported cost. Do not treat
`costDollars` as authoritative account billing—the docs say billing is based
on usage counters.

**Live validation.** Requires a key and incurs cost/credits. A provider console
or service-key usage endpoint is needed for authoritative billed usage
([API-key usage](https://exa.ai/docs/reference/team-management/get-api-key-usage)).

### 9. Linkup

**Current adapter.** Endpoint, Bearer auth, `q`, `depth=standard`,
`outputType=searchResults`, and result `name/url/content` match the current
contract.

**Official contract and drift.** Linkup supports `fast`, `standard`, and
`deep`; `searchResults`, cited `sourcedAnswer`, or structured output; domain
and date filters; and `maxResults`
([overview](https://docs.linkup.so/pages/documentation/endpoints/search/overview),
[reference](https://docs.linkup.so/pages/documentation/endpoints/search/reference)).
The adapter never sends `maxResults`, so `SearchQuery.max_results` is not
honored, and it probes undocumented `X-RateLimit-*`/`X-Credits-Remaining`
headers without an accessible first-party header contract. `standard`
`searchResults` is billed per call under current pricing.

**Smallest shim.** Send `maxResults`; map from/to dates and domain filters;
preserve result `type`; accept rate/credit headers only as optional,
non-authoritative signals until fixture-backed.

**Live validation.** Requires a key and paid/free-credit consumption. Validate
actual headers and whether successful empty results retain a `results` array.

### 10. Parallel

**Current adapter.** Calls legacy `/v1beta/search`, sends a dated beta header,
top-level `max_results`, and reads `excerpt` or `snippet`.

**Official contract and drift.** The current contract is `/v1/search`; the
reference explicitly calls `/v1beta/search` legacy. Results contain
`excerpts[]` and `publish_date`; response evidence includes `search_id`,
`session_id`, warnings, and usage
([current search reference](https://docs.parallel.ai/api-reference/search/search)).
Current result-count/freshness controls belong under `advanced_settings`;
`fetch_policy.max_age_seconds` controls live-vs-index freshness
([advanced settings](https://docs.parallel.ai/search/advanced-search-settings)).

**Smallest shim.** Move to `/v1/search`; remove the beta header; map
`advanced_settings.max_results`; join bounded `excerpts[]` for the snippet;
preserve `publish_date`, IDs, warnings, and usage. Keep a dual-reader for
legacy `excerpt/snippet` fixtures only if a staged rollout needs it.

**Live validation.** Requires a key and consumes credits. Validate `usage`
shape, balance-exhausted `402`, and migration behavior without parallel old/new
paid calls.

### 11. Serper

**Current adapter.** `POST https://google.serper.dev/search` with
`X-API-KEY`, `q`, and `num`; parses `organic[].link/title/snippet/position/date`.
It also looks for response `credits`, `credit_limit`, and guessed credit/rate
headers.

**First-party evidence and drift.** Serper's public first-party page shows the
organic result shape and describes real-time Google results, per-second plan
limits, and top-up credits
([Serper](https://serper.dev/)). The detailed authenticated API documentation
was not publicly accessible during this research, so `credits`,
`credit_limit`, and response headers could not be confirmed from an accessible
first-party spec. No freshness parameter is sent.

**Smallest shim.** Keep the proven organic aliases; treat guessed credit fields
as optional/non-authoritative; add fixture capture from a redacted provider
console sample before relying on them. Normalize HTTP status/retry evidence
centrally.

**Live validation.** Requires a key and has unknown charging behavior for
invalid requests because detailed billing/error documentation was
inaccessible. Use one bounded valid search only after authorization; obtain
error fixtures from first-party documentation or a provider-supplied sample
rather than assuming an invalid request is free. Do not probe exhaustion by
spending.

### 12. You.com

**Current adapter.** Calls `https://api.you.com/v1/search` with `query`,
`count`, and `safesearch=off`; parses only `results.web` and the first snippet.

**Official contract and drift.** The current reference's example host is
`https://ydc-index.io/v1/search`; it returns both web and news sections plus
`metadata.search_uuid/query/latency`, supports freshness, pagination,
safe-search, domains, and optional billable live crawl
([search reference](https://you.com/docs/api-reference/search/v1-search)).
The response fields used by Argus remain compatible, but the adapter drops
`page_age`, description fallback richness, news, request UUID/latency, and all
rate signals. The host difference is compatibility risk, not proof that the
`api.you.com` alias is dead. You exposes a separate account-balance endpoint,
with balance denominated in cents
([account balance](https://you.com/docs/api-reference/billing/get-account-balance)).

**Smallest shim.** Do not default safe search off; map canonical freshness;
preserve `page_age` and search UUID; classify sections or explicitly document
web-only behavior; capture documented rate headers. Make the base URL
configurable and validate the current alias before changing it.

**Live validation.** Requires a key and consumes credits. Validate host alias,
search response, and balance endpoint separately; never print the key or full
account payload.

### 13. Valyu

**Current adapter.** Endpoint/auth, `query`, `max_num_results`,
`search_type=web`, `fast_mode=true`, result fields, transaction ID, total
characters, and `total_deduction_dollars` align well with current docs.

**Official contract and drift.** Valyu supports source selection, relevance
threshold, source biases, response length, dates, and country; successful
responses return source counts, transaction ID, total cost, total characters,
and an `error` that may still contain a warning on success
([search reference](https://docs.valyu.ai/api-reference/endpoint/search)).
The adapter discards `results_by_source`, query, and success warnings, and
never maps freshness. Its fixed `fast_mode=true` bypasses instructions and
reranking and forces web-only behavior, which may be intentional for cost but
must be explicit policy.

**Smallest shim.** Preserve source counts and success warnings; map dates;
record fast-mode as resolved mode; keep the existing important distinction
between absent cost and explicit zero cost.

**Live validation.** Requires a key and incurs cost. Validate only after spend
authorization; do not manufacture an insufficient-credit state.

### 14. SearchAPI

**Current adapter.** The Google engine endpoint plus `q` and `api_key` are
current. The adapter also sends `num=max_results`, dual-reads
`organic_results` and `organic`, then maps link/title/snippet, position,
displayed link, and date.

**Official contract and drift.** SearchAPI supports date presets and custom
date ranges, safe search, localization, and verbatim search
([Google engine reference](https://www.searchapi.io/docs/google)). The adapter
does not send any. The same reference says Google phased out variable `num` in
September 2025 and it is now effectively fixed at 10, so sending up to 50 does
not honor `SearchQuery.max_results`; additional pages require explicit
pagination. It puts
`search_metadata.id/status/created_at/processed_at` in `credit_info`, although
those are request lifecycle metadata, not credit balance. SearchAPI exposes a
separate `/api/v1/me` account endpoint with monthly usage, allowance,
remaining credits, and hourly rate limit
([Account API](https://www.searchapi.io/docs/account-api)).

**Smallest shim.** Move `search_metadata` to bounded provider metadata; map
freshness/safe-search/localization; clamp one request to 10 and paginate only
when the retrieval plan authorizes it; reserve `credit_info` for actual
allowance/remaining signals and use the account endpoint only in explicit
readiness/budget probes.

**Live validation.** Requires a key and consumes a search credit; the account
endpoint can validate balance without a search but is still credentialed.

## Extraction-chain inventory

The implemented chain is longer than the abbreviated project prose:
YouTube is special-cased before authenticated extraction, Trafilatura,
Crawl4AI, Obscura, Playwright, residential extraction, Jina, Valyu Contents,
Firecrawl, You Contents, Wayback, and archive.today. Generic-chain attempts are
recorded, but failure summaries are free text and the final response does not
yet implement #57's stable rejection object.

### Local and self-hosted extractors

| Extractor | Safe static finding | Smallest compatibility action | Live proof still needed |
|---|---|---|---|
| YouTube | YouTube URLs and video IDs are routed before the generic chain through local `yt-dlp` 2026.7.4. The adapter consumes mutable `extract_info()` fields, chooses JSON3 subtitle URLs, parses caption `events[].segs[].utf8`, and treats caption failure as metadata-only quality success. Hermetic tests cover routing, manual/automatic English preference, metadata, and JSON3 text, but not unavailable/private/age-gated/live-stream responses or upstream field drift ([yt-dlp first-party source](https://github.com/yt-dlp/yt-dlp)). | Freeze redacted `yt-dlp` metadata and JSON3 fixtures for unavailable, metadata-only, captioned, redirected, and malformed-caption cases; classify metadata and caption failures separately; retain resolved video identity without cookies. | A public no-cookie video probe has no provider fee but is network- and YouTube-policy-dependent. Keep it in the live-readiness lane, never generic hermetic CI. |
| Authenticated/residential | Argus-owned transports already return `ExtractedContent`; contract drift is internal rather than an external API issue. | Route every failure through the same structured rejection classifier; preserve final URL, timeout class, and bounded transport status. | Requires explicitly authorized caller fixtures/cookies or a configured residential worker; do not use private URLs for generic validation. |
| Trafilatura | The lock uses 2.0.0. Trafilatura 2.x `bare_extraction()` returns a `Document` by default or a dict when requested; Argus's new `normalize_trafilatura_result()` safely accepts both and allowlists text/title/author/date ([official core API](https://trafilatura.readthedocs.io/en/latest/corefunctions.html)). | Keep the shim. Add final URL as input to `bare_extraction` so URL-aware metadata can work; retain the allowlist. | Hermetic fixtures already cover Document/mapping/malformed shapes. Live pages are scorecard corpus work, not contract discovery. |
| Crawl4AI | The lock uses 0.8.6. Current result has `success`, `error_message`, status/headers, URL, metadata, and a markdown result object rather than a guaranteed string ([official CrawlResult](https://docs.crawl4ai.com/core/crawler-result/)). | Normalize `raw_markdown`/`fit_markdown`/legacy string, check `success`, retain sanitized error/status and final URL. | A local JS fixture with the locked extra installed; no external provider spend. |
| Obscura | Current CLI flags used by Argus (`fetch URL --dump text --stealth --quiet`) remain documented. Obscura also supports markdown, wait strategy, and timeout flags ([first-party source](https://github.com/h4ckf0r0day/obscura#obscura-fetch-url)). Argus applies only its outer subprocess timeout and loses final URL/title/status. | Pass a bounded CLI `--timeout`; prefer `--dump markdown` if quality fixtures prove it; record exit/timeout as stable categories. Pin/test a known Obscura release before production. | Local binary and hermetic JS fixture. No paid call. |
| Playwright | Argus waits for `domcontentloaded`, checks redirect safety, then uses a custom `LP.getMarkdown` path or DOM text fallback. Playwright exposes navigation response/final URL and navigation timeout semantics ([official Page API](https://playwright.dev/python/docs/api/class-page#page-goto)). | Preserve response status/final URL; distinguish context startup, navigation timeout, blocked redirect, markdown conversion, and too-short content. | Existing lifecycle tests are strong; add one hermetic JS and redirect fixture through the locked browser image. |

### External and archive extractors

| Extractor | Safe static finding | Smallest compatibility action | Live proof still needed |
|---|---|---|---|
| Jina Reader | `https://r.jina.ai/{url}` and optional Bearer auth are current. Reader supports GET/POST and publishes per-tier RPM/TPM/concurrency plus response rate headers ([Jina Reader](https://jina.ai/en-US/reader/)). Argus asks for plain text, derives title from line 1, and drops rate/token evidence and status-specific errors. | Capture documented rate headers and classify 401/403/429/504. Prefer a structured/JSON return format if official fixtures establish it; otherwise keep the current text reader. | A no-key public probe is possible but rate-limited by IP; keyed behavior consumes token allowance. |
| Valyu Contents | Current `/v1/contents` accepts 1–50 deduplicated URLs, `extract_effort` (`auto`, `normal`, or `high`), response length, a max-dollar guard, and optional paid summarization ([official Contents API](https://docs.valyu.ai/api-reference/endpoint/contents)). Argus's implementation is currently unreachable by design because durable spend reservation is missing. Its dormant payload uses `extract_effort=auto`, assumes a fixed `$0.001` cost, and does not use response-reported total cost. | Keep disabled until the spend gateway exists; then set `max_price_dollars`, make extraction effort explicit policy, use response-reported total cost/status, and normalize documented per-URL errors. | Credentialed paid call only after spend-gateway implementation and explicit authorization. |
| Firecrawl | Current v2 endpoint is `/v2/scrape`, with explicit output formats, `maxAge` cache freshness, timeout, proxy behavior, `metadata.statusCode/error`, and `402/429/500` outcomes ([official scrape reference](https://docs.firecrawl.dev/api-reference/endpoint/scrape)). Argus says v2 but calls `/v1/scrape`; code is unreachable behind the spend-reservation gate. | Keep disabled; when re-enabled, change to `/v2/scrape`, request `formats=["markdown"]`, set freshness/cache and cost policy explicitly, and preserve metadata status/error. | Credentialed call costs credits; do not validate until spend authorization. |
| You Contents | Current `/v1/contents` remains an official endpoint and requires scoped key access; the search docs and error reference establish `402` exhaustion, `403` missing scope, and `429` rate limits ([You Contents reference](https://you.com/docs/api-reference/contents), [errors](https://you.com/docs/error-handling/error-code-reference)). Argus calls `ydc-index.io/v1/contents`, expects a list containing URL, title, HTML/Markdown, and optional metadata, but has no dedicated fixtures and no spend-reservation gate despite documented per-page billing. The official response contract does not expose per-page status or cost. | Put behind the same durable spend gateway as Valyu/Firecrawl; capture a redacted official response fixture; normalize only documented URL/title/HTML/Markdown/metadata fields. Treat spend as estimated or ledger-derived unless authoritative usage evidence is separately documented. | Credentialed paid/free-credit call and scope validation. |
| Wayback | Argus queries the first-party `archive.org/wayback/available` endpoint, accepts only closest status `200`, then fetches and runs Trafilatura. It drops snapshot timestamp/availability and labels both Wayback and archive.today generically as `archive`. The official availability documentation was not accessible in this research environment. | Preserve snapshot URL/timestamp/status as bounded archive provenance, final fetch status, and distinguish “not archived” from network/parse failure. Do not create a fresh capture as part of extraction. | A public archived fixture is no-cost but network-dependent; freeze the availability and archived HTML responses for hermetic tests. |
| archive.today / archive.is | There is no accessible stable first-party public API specification. Argus first checks `/newest/{url}` and, when absent, posts the caller URL to `/submit`, which is an external write and may create a public archive. | Split lookup from submission. Default extraction must be lookup-only; archive creation requires explicit caller policy/authority and its own outcome. Treat all selectors/redirects as fixture-backed scraping, not a provider contract. | A public lookup fixture can be refreshed manually. Do not submit user URLs during compatibility validation. |

### Completeness, quality, and provenance

Argus already records `quality_passed`, `quality_reason`,
`completeness_result`, `extractors_tried`, and bounded attempts. That is useful
raw material, but several scorecard requirements remain contract gaps:

- A quality-passing but incomplete best result is returned as ordinary content;
  there is no canonical `degraded` outcome at the extraction model boundary.
- `attempts[].status` mixes execution and quality states, while
  `failure_summary` is unstructured.
- External extractors are all labeled `source_type="paid_api"` even if an
  anonymous Jina call consumes only IP-limited free allowance; spend provenance
  should describe the actual attempt, not only extractor class.
- `source_type="archive"` loses Wayback vs archive.today capture identity and
  timestamp.
- `ExtractedContent.cost` is usually left at `0.0`, even where a response can
  report actual cost.

The compatibility shim should enrich, not replace, the existing fields:
canonical outcome, stable rejection code, attempt class, original/final URL
identity (safely persisted according to caller policy), freshness/cache age,
and actual spend provenance.

## Safe static and fixture work

The following can be implemented and reviewed with no credentials and no
provider spend:

1. Add provider fixture cases for all 14 adapters, covering success, empty,
   malformed success, auth, rate limit, exhaustion, timeout, provider failure,
   and old/new response aliases where migration requires them.
2. Correct Parallel to `/v1/search` and current request/response shapes.
3. Correct Exa casing and explicitly request bounded contents.
4. Normalize Crawl4AI's locked result object.
5. Correct dormant Firecrawl v2 code while leaving it disabled behind the spend
   gate.
6. Add a typed internal freshness object and per-provider translators.
7. Add a provider-private error classifier with stable categories and
   allowlisted retry/reference fields.
8. Preserve bounded request IDs, warnings, publication ages, response-reported
   usage/cost, and rate reset signals.
9. Split archive lookup from archive creation; keep the default chain
   lookup-only.
10. Expand `BaseProvider` and normalization fixtures to every registered
    provider.

These are compatibility facts, not live-health claims.

## Credentialed or spend-bearing validation matrix

No item below was executed.

| Validation | Why static fixtures are insufficient | Cost/authority guard |
|---|---|---|
| Brave, Tavily, Exa, Linkup, Parallel, Serper, You, Valyu, SearchAPI search | Endpoint aliases, current headers, account-specific feature flags, empty behavior, and real usage fields depend on a key/plan. | One bounded request per changed adapter only after explicit budgeted-profile authorization; record redacted shape and request ID, never payload secrets. |
| Wolfram LLM API | AppID validity, quota, and subject restrictions are account-specific. | One grounding query after quota authorization; never probe errors by burning quota. |
| GitHub authenticated search | Private visibility and token-specific search bucket differ from anonymous IP quota. | Prefer anonymous fixture/live check; use a scoped token only if private behavior is in scope. |
| SearXNG | JSON format, engines, and time filters are instance configuration. | No provider fee, but use the declared Argus instance and bounded timeout. |
| DDGS/Yahoo | Scraper behavior depends on IP, locale, consent, installed package, and upstream markup. | No paid call; run bounded residential/datacenter probes only in a live-readiness lane. |
| Jina | Anonymous and keyed rate/token behavior differ. | Anonymous public URL first; keyed probe only if token allowance is approved. |
| Valyu Contents, Firecrawl, You Contents | Response shape, scopes, actual charge, and extraction status require enabled paid APIs. | Must remain disabled until durable spend reservation exists; one public fixture URL after authorization. |
| Wayback/archive.today | Network behavior and selectors drift, but archive submission changes external state. | Lookup-only public fixture. Never create an archive as a compatibility probe. |

Live validation should run once through canonical HTTP, consistent with the
scorecard. CLI, MCP, and Python should reuse captured fixtures to prove semantic
equivalence rather than repeat provider calls.

## Smallest compatibility design

### 1. Typed internal request controls

Add an internal adapter-only control object (or a validated allowlist in
`SearchQuery.metadata`) with:

```text
freshness.relative = day|week|month|year
freshness.start_date = YYYY-MM-DD
freshness.end_date = YYYY-MM-DD
safe_search = off|moderate|strict
country
language
include_domains[]
exclude_domains[]
```

Each adapter translates only supported fields. Unsupported required controls
must become `policy_rejected` or `unready`, not silently disappear. The
normalized request stored in the evidence bundle should record which controls
were applied.

### 2. Dual-read, single-write response normalization

During a short migration window, readers may accept known aliases:

- Exa: `publishedDate` then legacy `published_date`.
- Parallel: `excerpts[]` then legacy `excerpt`/`snippet`.
- SearchAPI: `organic_results` then `organic`.
- Trafilatura: `Document.as_dict()` or mapping.
- Crawl4AI: markdown result object then legacy string.

Requests should emit only the current documented contract. Provider-native
payloads remain inside adapters.

### 3. Stable provider failure projection

Project provider responses into:

```text
category:
  invalid_request | authentication_rejected | policy_rejected |
  rate_limited | balance_exhausted | timeout | provider_unavailable |
  parse_error | empty
http_status?
provider_code?
retry_after_seconds?
rate_limit_reset?
request_ref?
safe_summary?
```

The broker can then map those categories to scorecard outcomes without
guessing from exception strings. `safe_summary` must be bounded and scrubbed;
raw response bodies must not be persisted.

### 4. Allowlisted evidence projection

Extend normalized trace metadata with bounded, typed fields:

- provider request/run ID;
- resolved search mode/depth;
- provider-reported usage and cost;
- authoritative-vs-estimated flag;
- rate limit/remaining/reset;
- warnings;
- publication/page age;
- freshness controls applied;
- balance observation source and timestamp.

Do not overload `credit_info` with request lifecycle metadata, and do not call
a request's estimated cost an account balance.

### 5. Fixture contract

Every adapter needs first-party-shape fixtures for:

- current success and empty success;
- old response alias only where a migration reader is intentional;
- `401`, `402`, `403`, `408/504`, `422`, `429`, and `5xx` where the provider
  documents them;
- missing/malformed expected fields;
- rate/usage/balance metadata;
- freshness request translation;
- secret and raw-payload non-leakage.

### 6. Cache boundary delegated to issue #61

The current in-memory key contains only normalized query, mode, and attribution
flag. It does not identify `free_only`, explicit provider selection,
`max_results`, caller tier policy, or future freshness/domain controls. A
cached budgeted response can therefore be semantically ineligible for a
no-paid-call request even though no new provider call occurs.

This inventory establishes that contract fact. [Issue #61](https://github.com/Khamel83/argus/issues/61)
owns the deterministic retrieval-plan fingerprint, invalidation rules, cache
age/origin evidence, and compatibility mapping.

## #57 integration boundary: structured extraction rejection

[#57 — Persist structured extraction rejection reasons for Atlas callers](https://github.com/Khamel83/argus/issues/57)
owns the caller-visible and durable extraction rejection object. Issue #60
should not implement a parallel rejection schema.

The boundary should be:

```text
provider/extractor adapter
  -> normalized private failure category + bounded evidence
  -> extraction orchestrator/quality/completeness decision
  -> #57 rejection mapper
  -> API/log/persistence representation
```

Issue #60 supplies normalized facts:

- HTTP/provider code;
- timeout/rate/exhaustion/unavailable/parse classification;
- extractor and attempt count;
- quality/completeness signals;
- bounded timing/status;
- safe next-action hint where the provider explicitly supplies one.

Issue #57 owns:

- stable public `rejection_code`;
- `recommended_action`;
- privacy-safe API shape;
- durable log/database representation;
- successful-response compatibility;
- Atlas-facing documentation and tests.

Suggested mapping inputs, not duplicate public enums:

| #60 normalized fact | #57 public rejection candidate |
|---|---|
| `rate_limited` | `rate_limited` / `retry_later` |
| `balance_exhausted`, key missing, spend policy denied | `provider_unavailable` or a policy-specific code chosen by #57 |
| `timeout` | `timeout` / `retry_later` |
| `parse_error` | `parse_error` / `fallback_provider` |
| unsupported URL/source | `unsupported_source` / `terminal` |
| empty successful extraction | `empty_result` / `fallback_provider` |
| quality gate failed | `quality_gate_failed` |
| completeness failed | `incomplete_content` |

This keeps provider volatility private while giving Atlas stable evidence.
Nothing in either issue authorizes replay of historical Atlas failures or
public extraction waves.

## Inaccessible or non-contractual official documentation

- **Serper:** the public first-party page and response examples were
  accessible, but detailed authenticated API/error/header documentation was
  not publicly accessible. Credit/rate header assumptions therefore remain
  unverified.
- **Yahoo Search HTML:** Yahoo provides end-user help, not a supported HTML
  scraping schema or error contract. DOM selectors are fixture-backed only.
- **DuckDuckGo adapter:** Argus uses the independently maintained `ddgs`
  metasearch package, not a DuckDuckGo-owned API. The package owner's source is
  the relevant callable contract.
- **Wayback availability API:** the first-party endpoint is in current Argus
  source and is reachable in principle, but an accessible official
  specification page was not available in this research environment. Do not
  infer more than the frozen response fixture.
- **archive.today/archive.is:** no accessible stable first-party public API or
  submission contract was found. Lookup/submission behavior is scraping and
  must be treated accordingly.
- **Linkup credit/rate headers:** accessible official search docs did not
  document the headers guessed by the adapter.

## Recommended implementation order

1. Land fixture coverage and the shared error/evidence normalizer.
2. Fix Parallel, Exa, and Crawl4AI against current documented shapes.
3. Add typed freshness translation and provider request caps.
4. Correct Firecrawl's dormant v2 code, but keep every paid extractor behind
   durable spend reservation.
5. Integrate normalized extraction facts into #57's single public rejection
   contract.
6. Run one authorized HTTP validation lane, capture secret-free fixtures, and
   prove MCP/CLI/Python semantics hermetically.

This order repairs known static breakage before spending credits and preserves
the accepted scorecard's authority, policy-truth, and evidence-bundle rules.
