# Argus market landscape: replace, shrink, or keep?

**Snapshot:** 2026-08-09

**Scope:** Public, self-serve capabilities and pricing from official product documentation, official pricing pages, and official repositories. Prices are USD unless stated otherwise. Promotional prices and free allowances can change; they are useful for an architectural decision, not a procurement quote.

## Executive conclusion

**Decision:** Do not replace Argus with a single vendor if the full Argus contract matters. No product reviewed documents all of the following in one system: broad and budget-aware multi-index search, deep research, robust extraction, an interactive stealth browser, explicit residential egress, source *and execution* provenance, per-caller policy, durable normalized outcomes, and HTTP/MCP integration.

That does not mean the current implementation should remain intact. The strongest strategy is to **shrink Argus into a thin control plane over two managed services**:

1. **Parallel + Bright Data** is the strongest outcome-oriented stack. Parallel supplies inexpensive search, extraction, agentic research, and claim-level research Basis ([product pricing](https://parallel.ai/pricing), [Basis guide](https://docs.parallel.ai/task-api/guides/access-research-basis)); Bright Data supplies a managed browser, Web Unlocker, CAPTCHA handling, and explicit residential/global egress ([MCP/browser pricing](https://brightdata.com/pricing/mcp-server), [residential network](https://brightdata.com/pricing/proxy-network/residential-proxies)). This is the closest documented two-vendor match.
2. **Linkup + Firecrawl** is the strongest public-free-credit stack. Linkup currently restores a $20 balance monthly and offers search, fetch, and research ([Linkup pricing](https://docs.linkup.so/pages/documentation/platform/pricing)); Firecrawl offers 1,000 monthly credits, crawl/scrape/search, browser interaction, MCP, and an automatic stealth retry ([Firecrawl pricing](https://www.firecrawl.dev/pricing), [Scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)). Firecrawl’s public documentation does **not** establish that the stealth path uses residential egress.
3. **Exa + Browserbase** is the strongest developer-experience stack. Exa supplies search, contents, deep search, research agents, citations, and per-call cost ([Search guide](https://exa.ai/docs/reference/search-api-guide-for-coding-agents), [pricing](https://exa.ai/pricing)); Browserbase supplies Playwright-compatible sessions, CAPTCHA handling, stealth, residential proxies, Search/Fetch, and an official MCP server ([pricing](https://www.browserbase.com/pricing), [MCP repository](https://github.com/browserbase/mcp-server-browserbase)).

**Inference, not a measured benchmark:** those stacks should replace roughly **90–95% of common user-facing retrieval outcomes**, but none replaces 99% of the *governance and evidence contract* without a thin Argus layer. That remaining layer should normalize results, enforce caller and spend policy, record provider/extractor traces and explicit failure states, attach egress/machine/source metadata, and durably store accepted artifacts. It can likely delete most of the 14 provider adapters and much of the 12-step extraction chain.

If the actual requirement is simply “give agents good web answers and readable pages,” rather than “produce auditable, policy-constrained retrieval evidence,” Argus can be retired. A single managed product such as Exa, Firecrawl, or Browserbase will be materially lower maintenance. The decision hinges on whether Argus’s control-plane semantics are a product requirement or accumulated plumbing.

## What “99% of Argus” means

Argus’s repository describes more than search. Its intended evidence package includes normalized results, extracted content, provenance, provider traces, freshness, and failure state; its competitive scorecard also requires caller attribution, surface equivalence, per-provider traces, explicit degraded/empty/failure outcomes, quality/completeness assessment, durable SQL acceptance, and policy truth ([project context](../../CONTEXT.md), [stability scorecard](../scorecards/stability-competitive.md)). The routing layer adds free-first provider tiers, health and budget gates, cache isolation, fusion/ranking, caller tier caps, and topology-aware egress ([README](../../README.md), [operations contract](../operations-status.md)).

This review separates two kinds of provenance:

- **Answer/source provenance:** URLs, excerpts, citations, confidence, and sometimes the cost of producing an answer.
- **Execution provenance:** which provider or extractor ran, which paths were skipped and why, residential versus datacenter egress, machine identity, source type, degraded/failure state, and the policy/budget decision that produced the outcome.

The managed research products are increasingly strong at the first. None of the public APIs reviewed documents Argus’s complete second category across search, extraction, and browser execution. That distinction is the main reason a clean one-vendor replacement falls short.

## Market comparison

The tables report only capabilities explicitly documented in the linked public material. “No documented support” means a feature was not found in the reviewed public product surface, not that an enterprise custom feature is impossible.

### General search and research services

| Service | Retrieval and research surface | Public price/free-credit shape | Evidence, controls, and integration | Replacement assessment |
|---|---|---|---|---|
| **OpenAI Web Search / Deep Research** | Responses Web Search returns inline URL citations and a full consulted-source list; it supports allowed or blocked domain filters and configurable search context ([Web Search guide](https://developers.openai.com/api/docs/guides/tools-web-search)). The Deep Research guide says its specialized models can find, analyze, and synthesize hundreds of sources using web search, remote MCP, and file search ([Deep Research guide](https://developers.openai.com/api/docs/guides/deep-research)). There is a current documentation conflict: the same-date model catalog marks both named models deprecated ([model catalog](https://developers.openai.com/api/docs/models/all)). | Web Search is $10/1,000 calls plus search-content tokens at model rates; the preview non-reasoning path is $25/1,000 with search-content tokens free ([tool pricing](https://developers.openai.com/api/docs/pricing)). The still-published model pages list `o3-deep-research` at $10/M input and $40/M output, and `o4-mini-deep-research` at $2/M and $8/M, before tool charges ([o3 pricing](https://developers.openai.com/api/docs/models/o3-deep-research), [o4-mini pricing](https://developers.openai.com/api/docs/models/o4-mini-deep-research)). | Strong answer citations, long-form synthesis, remote MCP, and native tool use. Web Search is eligible for Zero Data Retention, while Responses otherwise stores application state for 30 days by default ([data controls](https://developers.openai.com/api/docs/guides/your-data)). No documented general crawl, interactive browser, residential egress, or execution provenance. | Compelling synthesis above a retrieval layer, but not a complete Argus replacement. Treat the specialized model path as lifecycle-uncertain until OpenAI reconciles the guide and catalog. |
| **Tavily** | Search supports basic, advanced, fast, and ultra-fast depths, domain/date/country filters, optional raw content, ranked results, and request IDs. The platform also exposes extract, crawl, map, and research ([Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search), [credits](https://docs.tavily.com/documentation/api-credits)). | 1,000 monthly credits free without a card; pay-as-you-go is $0.008/credit. Basic search costs 1 credit, advanced 2; extract costs 1 credit per five successful URLs in basic mode or 2 in advanced. Research Mini costs 4–110 credits and Pro 15–250 ([credits and plans](https://docs.tavily.com/documentation/api-credits)). | Responses disclose `usage.credits`; filters and request IDs are useful controls. Tavily offers official remote and local MCP options ([MCP](https://docs.tavily.com/documentation/mcp)). No documented interactive browser, residential routing, or Argus-style execution provenance. | Strong low-maintenance generalist and plausible single-vendor simplification for ordinary agent research, but not hard-page coverage. |
| **Exa** | Search types span instant/fast through deep and deep-reasoning; contents can return full text, highlights, summaries, subpages, live-crawl freshness, and structured output. Deep search returns grounding citations with confidence ([Search guide](https://exa.ai/docs/reference/search-api-guide-for-coding-agents), [Contents guide](https://exa.ai/docs/reference/contents-api-guide)). | $20 signup credit, then $10 free monthly. Public list prices include Search at $7/1,000, Contents at $1/1,000 pages per content type, Deep Search at $12–$15/1,000, and Agent runs from $0.012 to $1 ([pricing](https://exa.ai/pricing)). | Responses include request IDs and `costDollars.total`; team usage can be queried per API key, and budget exhaustion returns HTTP 402 ([usage](https://exa.ai/docs/reference/team-management/get-api-key-usage), [errors](https://exa.ai/docs/reference/error-codes)). Official hosted MCP exposes search and fetch ([MCP](https://exa.ai/docs/reference/exa-mcp)). No general interactive browser or explicit residential egress. | The strongest one-vendor candidate for search + research + readable content, but it leaves protected-site acquisition and execution provenance uncovered. |
| **Parallel** | Search returns ranked URLs and compressed excerpts; Extract returns page content; Task and Research produce synthesized outputs. Task outputs can expose a field-level **Basis** containing citations, source excerpts, reasoning, and confidence ([Search](https://docs.parallel.ai/search/search-quickstart), [research basis](https://docs.parallel.ai/task-api/guides/access-research-basis)). | Search is $0.001–$0.005/request, Extract $0.001, and Task processors $0.005–$2.40/run ([pricing](https://parallel.ai/pricing)). Eligible organizations with a card receive one shared $5 monthly credit—up to 5,000 Search requests, 5,000 Extract requests, or 1,000 low-cost Task runs if spent on only one category ([official free-tier announcement](https://parallel.ai/blog/free-tier-parallel)). | Source-policy filters, freshness, geography, and excerpt controls are documented ([advanced search](https://docs.parallel.ai/search/advanced-search-settings)). Search MCP can be used anonymously at a free rate or authenticated for higher limits; Task MCP requires authentication ([MCP](https://docs.parallel.ai/integrations/mcp/programmatic-use)). No documented browser, residential egress, or cross-vendor budget routing. | Best low-cost research and evidence service. Basis is unusually close to audit-grade source provenance, but it does not prove how the page was acquired. |
| **Linkup** | Fast/standard/deep search can return raw ranked sources, sourced answers, or structured JSON. Deep mode iterates search and scraping; the platform also exposes Fetch, Research, and Tasks ([Search overview](https://docs.linkup.so/pages/documentation/endpoints/search/overview), [platform overview](https://docs.linkup.so/pages/documentation/get-started/introduction)). | Signup balance is automatically set to $20 and restored to $20 each month. Standard/fast raw search is $0.005, sourced/structured is $0.006; deep raw is $0.05; Fetch is $0.001 without JavaScript or $0.005 with it; Research is $0.25–$2.50. Errors are not charged ([pricing documentation](https://docs.linkup.so/pages/documentation/platform/pricing)). That is up to 4,000 monthly standard raw searches if the balance is used only for those. | JavaScript rendering and sequential deep search are documented ([best practices](https://docs.linkup.so/pages/documentation/endpoints/search/best-practices)); official MCP exposes standard and deep search ([MCP](https://docs.linkup.so/pages/integrations/mcp/mcp)). Enterprise advertises a private environment and ZDR ([pricing](https://www.linkup.so/pricing)). No documented interactive browser, residential egress, or detailed execution provenance. | Strongest recurring public credit and an excellent cheap primary search/research vendor. Pair it with a hard-page service. |
| **Brave Search API** | Independent web index with web/news/image/video endpoints, LLM Context, and optional Goggles reranking ([product](https://brave.com/search/api/)). | Search is $5/1,000 requests. A $5 monthly credit is available with a card on file ([pricing](https://brave.com/search/api/)). Answers adds synthesis at $4/1,000 searches plus model-token charges ([Answers documentation](https://api-dashboard.search.brave.com/app/documentation/ai-grounding/code-samples)). | Independent-index diversity is strategically useful; Answers responses include usage-cost metadata. Enterprise offers Zero Data Retention, and storage rights depend on plan ([product](https://brave.com/search/api/)). No generic content crawler, browser, or residential unblocking. | Best as an independent-index component, not a whole Argus replacement. Pair with an extraction/browser service and caller-owned synthesis. |
| **Perplexity** | Search API returns raw ranked results, supports multi-query, domain/language/region filters, and controls extraction-token volume. Agent API exposes web search and URL fetch; research presets add orchestration ([Search quickstart](https://docs.perplexity.ai/docs/search/quickstart), [Agent web search](https://docs.perplexity.ai/docs/agent-api/tools/web-search), [presets](https://docs.perplexity.ai/docs/agent-api/presets)). | Search API is listed at $5/1,000 requests. Sonar pricing combines input/output tokens with a per-request search fee that varies by context size; Deep Research adds citation, search-query, and reasoning-token charges ([pricing](https://docs.perplexity.ai/docs/getting-started/pricing)). | Citations and rich search-result metadata are supported ([citation example](https://docs.perplexity.ai/docs/cookbook/articles/streaming-citations/README)). No documented crawl, interactive stealth browser, residential egress, or Argus-style routing telemetry. | Credible answer and raw-search service, but less complete for this requirement than Exa, Parallel, Linkup, or Tavily. |

### Extraction, crawling, browsers, and residential egress

| Service | Documented acquisition surface | Public price/free shape | Residential / stealth / self-host | Replacement assessment |
|---|---|---|---|---|
| **Firecrawl Cloud** | Search, scrape, map, crawl, monitor, browser actions, and an isolated Browser Sandbox available through API, SDK, CLI, and MCP ([pricing](https://www.firecrawl.dev/pricing), [browser](https://docs.firecrawl.dev/features/browser)). Scrape actions can click, write, wait, scroll, and execute JavaScript ([Scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)). | 1,000 monthly credits free. Hobby is $16/month billed annually for 5,000 credits; Standard is $83/month billed annually for 100,000. Scrape/crawl/map/monitor are generally 1 credit/page, Search 2 credits/10 results, and Interact 2 credits/browser minute ([pricing](https://www.firecrawl.dev/pricing)). | `proxy: auto` retries a failed basic request with a `stealth` proxy and charges 5 credits only on success ([Scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)). Public docs reviewed do **not** say that `stealth` is residential. Self-hosting is available, but the default stack omits some cloud features and makes the operator responsible for durability, TLS, HA, upgrades, monitoring, security, and recovery ([self-hosting](https://docs.firecrawl.dev/contributing/self-host)). | Broadest single operational replacement. It covers more of the chain than any other one vendor, but lacks explicit residential proof and deep-research/evidence semantics. |
| **Jina Reader / Search** | Reader converts URLs—including PDFs and browser-rendered pages—to LLM-friendly text; Search supplies web results. Official MCP supports read, search, and parallel variants ([Reader](https://jina.ai/reader/), [MCP repository](https://github.com/jina-ai/MCP)). | Unauthenticated Reader is limited to 20 RPM; a free key allows 500 Reader RPM and 100 Search RPM, and new keys receive 10 million tokens ([Reader](https://jina.ai/reader/)). | Jina explicitly says Reader does **not** actively circumvent anti-bot systems or access controls. Its ReaderLM-v2 weights are licensed CC-BY-NC-4.0, not permissive open source for commercial use ([Reader](https://jina.ai/reader/)). | Excellent inexpensive fallback for ordinary pages, not a protected-page solution. |
| **Apify** | Marketplace and runtime for Actors, crawlers, browser automation, datasets, schedules, and proxy-backed scraping. | Free plan provides $5 monthly usage without a card; Starter is $29/month plus usage. Public prices include compute at $0.20/CU, residential proxy at $8/GB, and SERP access at $2.50/1,000 results ([pricing](https://apify.com/pricing)). | Residential proxies support country/state targeting and sticky sessions of roughly 30 minutes ([residential proxy docs](https://docs.apify.com/proxy/residential-proxy)). Workflows can be self-authored, but the managed platform is the principal value. | Powerful platform and explicit residential option, but selecting, assessing, and maintaining Actors recreates adapter/contract work. Not a unified research engine. |
| **Bright Data** | Search, scrape, structured extraction, Web Unlocker, and a full cloud Browser API. Web Unlocker manages proxy rotation, anti-bot systems, and CAPTCHAs for request-style jobs; Browser API is intended for full multi-step automation ([Web Unlocker](https://docs.brightdata.com/scraping-automation/web-unlocker/introduction), [residential FAQ](https://docs.brightdata.com/proxy-networks/residential/faqs)). | MCP/AI pricing includes 5,000 free monthly Search/Scrape/Extract calls and free Browser Navigation; PAYG is $1.50/1,000 Search/Scrape/Extract results and $8/GB Browser traffic ([MCP pricing](https://brightdata.com/pricing/mcp-server)). Residential PAYG is displayed at a promotional $4/GB against an $8/GB list price ([residential pricing](https://brightdata.com/pricing/proxy-network/residential-proxies)). | Explicit 195-country residential network, global IP pool, CAPTCHA handling, geo targeting, spend controls, and browser automation ([residential pricing](https://brightdata.com/pricing/proxy-network/residential-proxies)). MCP is first-party. | Strongest hard-page/residential sidecar reviewed. It replaces the hardest infrastructure, but not broad research orchestration or Argus policy semantics. |
| **Browserbase + Stagehand** | Managed Playwright/Puppeteer/Selenium sessions, Search and Fetch, session replay, and Stagehand browser agents. Stagehand is an MIT-licensed natural-language/code browser framework with action caching and self-healing ([pricing](https://www.browserbase.com/pricing), [Stagehand repository](https://github.com/browserbase/stagehand)). | Free: 1 browser hour, 3 concurrent sessions, 3 agent runs, 1,000 Search and 1,000 Fetch calls, but no stealth or CAPTCHA features. Developer: $20/month for 100 browser hours, 1 GB proxy traffic, 1,000 Search/Fetch calls, auto-CAPTCHA, and basic stealth before overages ([pricing](https://www.browserbase.com/pricing)). | Built-in residential and custom proxies; paid plans include CAPTCHA handling and stealth. No on-premises deployment. Official Apache-2.0 MCP server is available ([MCP repository](https://github.com/browserbase/mcp-server-browserbase)). | Best developer-focused browser platform and now a credible search/fetch complement. It still lacks deep research, broad crawling, and execution-policy semantics. |
| **Browserless** | Hosted Chrome/browser automation compatible with Playwright and Puppeteer, with CAPTCHA handling, extensions, and datacenter/residential traffic options. | Free plan: 1,000 units/month, two concurrent browsers, one-minute sessions; residential traffic is 6 units/MB and datacenter traffic 2 units/MB. Paid Prototyping starts at $25/month when billed annually for 20,000 units and 15-minute sessions ([pricing](https://www.browserless.io/pricing)). | Residential traffic and CAPTCHA handling are documented in cloud plans. A free SSPL-1.0 Docker image provides core Playwright/Puppeteer and REST automation, but stealth, CAPTCHA solving, session recording, and full cloud parity require licensed Enterprise Docker; self-hosted proxy egress is bring-your-own ([open-source Docker](https://docs.browserless.io/enterprise/open-source), [self-hosting editions](https://www.browserless.io/platform/self-hosted)). | Economical browser-as-a-service primitive. The open-source core is useful, but the capabilities that reduce anti-bot maintenance remain managed or licensed; search, research, and normalized outcomes remain caller-owned. |
| **ZenRows** | Universal Scraper API and a scraping browser with JavaScript rendering, geo targeting, anti-bot handling, and residential proxies. | 14-day trial includes a $1 allowance, enough for roughly 1,000 basic or 40 protected requests. Paid plans begin around $65–$70/month depending on term and page shown; JavaScript multiplies request cost by 5, premium proxy by 10, and both by 25 ([pricing](https://www.zenrows.com/pricing), [credit model](https://docs.zenrows.com/first-steps/pricing)). | Premium proxy uses residential IPs and anti-bot handling; charges are success-oriented ([premium proxy](https://docs.zenrows.com/universal-scraper-api/features/premium-proxy)). | Effective hard-page acquisition, but no general search/research layer and weaker integration breadth than Bright Data or Browserbase. |
| **ScrapingBee** | Scraping API with JavaScript rendering, rotating proxies, premium residential routing, stealth, and optional AI extraction. | 1,000 free credits without a card. Freelance is $49/month for 250,000 credits; Startup is $99/month for 1 million. A basic rotating request is 1 credit, JavaScript 5, premium proxy 10, premium+JavaScript 25, and stealth+JavaScript 75 ([pricing](https://www.scrapingbee.com/pricing/), [documentation](https://www.scrapingbee.com/documentation/)). | Explicit premium residential and stealth modes. No self-hosted search/research platform. | Simple, mature extraction fallback, not a search or research replacement. |

### Self-hosted primitives

| Primitive | What it supplies | What the operator still owns | Strategic fit |
|---|---|---|---|
| **SearXNG** | AGPL metasearch over multiple upstream search services with no user tracking ([official repository](https://github.com/searxng/searxng)). | Deployment, upgrades, engine configuration, monitoring, and upstream breakage. Official installation docs describe a fast-moving project and ask admins to review migrations regularly ([installation](https://docs.searxng.org/admin/installation.html)). CAPTCHA/access-denied suspension behavior is part of engine configuration rather than a managed guarantee. | Preserves index diversity and independence, but reproduces a significant part of Argus’s ongoing maintenance. It supplies neither extraction nor research. |
| **Crawl4AI** | Open-source crawling/extraction, Docker deployment, browser strategies, API/MCP, monitoring, and authentication ([self-hosting](https://docs.crawl4ai.com/core/self-hosting/)). Its undetected-browser path layers stealth techniques over Playwright and includes an adapter aimed at Cloudflare/DataDome-style targets ([undetected browser](https://docs.crawl4ai.com/advanced/undetected-browser/)). | Browser lifecycle, security, storage, scaling, proxy sourcing, target-specific breakage, and upgrades. The project itself warns that advanced sites may still detect or block stealth modes; residential egress is not bundled ([undetected browser](https://docs.crawl4ai.com/advanced/undetected-browser/)). | Best self-host extraction primitive, but it does not transfer the high-maintenance risk the owner is trying to shed. |
| **Playwright** | Apache-2.0 cross-browser automation for Chromium, Firefox, and WebKit; proxy configuration is native ([repository](https://github.com/microsoft/playwright), [network docs](https://playwright.dev/docs/network)). | Stealth, CAPTCHA handling, residential proxy procurement, content-quality gates, scheduling, scaling, and target-specific flows. | Foundational component, not a replacement product. Public Playwright documentation supports automation and configurable proxies, not a built-in unblocking network. |
| **Firecrawl self-host** | A substantial crawl/scrape API can be run under the open-source project. | The documented quick start is not a production-complete cloud clone: durability, TLS, HA, monitoring, security, upgrades, and recovery remain local, and some browser/action/agent functionality requires cloud or external services ([self-hosting](https://docs.firecrawl.dev/contributing/self-host)). | Useful for data-local deployments, but a poor choice if the goal is materially lower maintenance. |

## Viable replacement stacks

The fit bands below are **architectural inference from documented surfaces, not measured success rates**. They estimate ordinary search/research/extraction outcomes. They do not imply parity on Argus’s audit controls.

### 1. Parallel + Bright Data — best overall replacement

**Estimated outcome fit:** 90–95% before a thin control layer.

- Parallel provides low-cost search and extraction, agentic Task/Research, claim-level Basis, source policy, and MCP ([pricing](https://parallel.ai/pricing), [Basis](https://docs.parallel.ai/task-api/guides/access-research-basis), [MCP](https://docs.parallel.ai/integrations/mcp/programmatic-use)).
- Bright Data provides managed Web Unlocker, CAPTCHA handling, full browser automation, MCP, geo targeting, and an explicit residential network ([Web Unlocker](https://docs.brightdata.com/scraping-automation/web-unlocker/introduction), [MCP pricing](https://brightdata.com/pricing/mcp-server), [residential network](https://brightdata.com/pricing/proxy-network/residential-proxies)).
- The free/low-use envelope is unusually strong: eligible Parallel organizations receive $5 shared monthly credit, while Bright Data advertises 5,000 monthly Search/Scrape/Extract calls through its AI/MCP plan ([Parallel free tier](https://parallel.ai/blog/free-tier-parallel), [Bright Data pricing](https://brightdata.com/pricing/mcp-server)).

**Remaining gap:** no public cross-vendor policy engine, normalized trace, caller tier cap, durable outcome contract, or common execution-provenance schema. A thin Argus gateway would decide when Parallel is insufficient, send only those targets to Bright Data, and merge both evidence envelopes.

### 2. Linkup + Firecrawl — best recurring-credit value

**Estimated outcome fit:** 85–95%, depending on the protected-site mix.

- Linkup’s $20 restored monthly balance can fund up to 4,000 standard raw searches, or a mix of search, JavaScript fetch, deep search, and research ([pricing](https://docs.linkup.so/pages/documentation/platform/pricing)).
- Firecrawl’s 1,000 free monthly credits cover search, scrape, crawl, map, and browser interaction, with an automatic stealth retry for blocked scrape requests ([pricing](https://www.firecrawl.dev/pricing), [Scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)).
- Both expose MCP, so a caller can integrate without hosting a local browser or search broker ([Linkup MCP](https://docs.linkup.so/pages/integrations/mcp/mcp), [Firecrawl browser](https://docs.firecrawl.dev/features/browser)).

**Remaining gap:** Firecrawl’s public `stealth` documentation does not establish residential egress; Linkup’s citations do not carry Argus-style execution provenance. If explicit residential acquisition is a hard requirement, replace Firecrawl with Bright Data or add it as a third, exception-only service.

### 3. Exa + Browserbase — best developer experience

**Estimated outcome fit:** 90–95%.

- Exa covers search, content, live crawling, deep search/reasoning, research agents, citations, per-call cost, per-key usage, and hosted MCP ([Search guide](https://exa.ai/docs/reference/search-api-guide-for-coding-agents), [pricing](https://exa.ai/pricing), [MCP](https://exa.ai/docs/reference/exa-mcp)).
- Browserbase covers Search/Fetch plus Playwright-compatible, CAPTCHA-aware, stealth, residential browser sessions; Stagehand supplies a maintainable code/agent abstraction over those sessions ([pricing](https://www.browserbase.com/pricing), [Stagehand](https://github.com/browserbase/stagehand)).

**Remaining gap:** deep browser outcomes are not automatically joined to Exa’s citations or budget model. There is no common provider/extractor trace or durable artifact acceptance contract.

### 4. Brave + Firecrawl — independent-index, low-complexity baseline

**Estimated outcome fit:** 75–90%.

Brave supplies a relatively inexpensive independent web index, while Firecrawl supplies raw content, crawling, interaction, and a stealth retry ([Brave](https://brave.com/search/api/), [Firecrawl](https://www.firecrawl.dev/pricing)). This preserves index diversity better than relying only on a synthesis vendor. It is weaker for multi-step deep research unless the caller orchestrates searches and synthesis itself, and it still lacks explicit residential egress.

### Closest one-vendor options

| Choice | Why it is attractive | Decisive missing capability |
|---|---|---|
| **Firecrawl Cloud** | Widest search-to-browser surface, straightforward MCP, useful free tier, and automatic stealth retry. | No documented research-grade Basis or explicit residential identity; weaker governance/provenance. |
| **Browserbase** | Search/Fetch plus real browser sessions, residential proxies, CAPTCHA handling, Stagehand, and MCP. | No broad crawl/deep-research product or Argus evidence contract. |
| **Exa** | Best integrated search, contents, deep search/reasoning, agents, monitors, cost telemetry, and MCP. | No interactive hard-page browser or explicit residential network. |
| **Bright Data** | Best unblocking/residential/browser completeness and generous MCP-oriented entry tier. | Retrieval synthesis/research quality and multi-provider routing are not its core public product. |

**Inference:** Firecrawl is the closest single operational substitute; Exa is the closest single retrieval substitute. Neither reaches 99% of the complete requirement.

## Gaps that remain distinctive to Argus

These are not all necessarily worth keeping. They are, however, the capabilities that disappear when Argus is replaced by a direct vendor integration.

1. **Free-first multi-provider fusion and index diversity.** Argus can query unrelated free, recurring, and one-time-credit providers, deduplicate results, and use reciprocal-rank fusion rather than accepting one vendor’s index and ranking ([README](../../README.md), [provider inventory](../providers.md)). Brave can add a second independent index, but none of the managed generalists publicly documents cross-vendor fusion.

2. **Cross-vendor, per-caller budget policy.** Vendor dashboards expose account spend and, in some cases, API-key usage. Argus applies provider tiers, depletion pacing, health/budget gates, and caller-specific tier caps across the whole queue ([project context](../../CONTEXT.md), [operations contract](../operations-status.md)). That is different from an account-wide spend ceiling.

3. **Universal execution provenance.** Argus’s schema carries provider/extractor identity, egress, machine, and source type, and its scorecard requires traces, skip reasons, and explicit degraded/empty/failure outcomes ([project context](../../CONTEXT.md), [scorecard](../scorecards/stability-competitive.md)). Parallel’s Basis and Exa’s grounding citations are better source provenance, but they do not document network path or machine provenance.

4. **Adaptive domain-to-topology memory.** Argus can learn that a domain fails from datacenter egress but succeeds from residential egress and alter subsequent routing ([project context](../../CONTEXT.md)). Managed unblockers internalize some similar learning, but do not expose it as caller-owned routing evidence.

5. **A normalized outcome contract across HTTP, MCP, CLI, and Python.** Argus aims to produce the same normalized evidence package and durable SQL acceptance regardless of caller surface ([scorecard](../scorecards/stability-competitive.md), [README](../../README.md)). Directly composing two vendor MCP tools does not automatically create that consistency.

6. **Transparent quality-gated fallback and archive recovery.** The documented extraction chain assesses completeness between attempts and can fall through local tools, managed APIs, and archives ([provider/extractor inventory](../providers.md)). A vendor may perform opaque internal retries, but the caller generally receives less evidence about which path succeeded or why earlier paths failed.

7. **Deployment and data-location choice.** Argus can mix local SQLite/PostgreSQL, self-hosted SearXNG/Crawl4AI/Playwright, and managed services. Managed vendors reduce operations but move queries and pages into external systems. Some vendors offer ZDR only on enterprise tiers—Exa, Brave, and Linkup advertise enterprise controls—while Firecrawl’s Scrape API caches by default unless `storeInCache` is disabled and directs ZDR inquiries to sales ([Exa pricing](https://exa.ai/pricing), [Brave](https://brave.com/search/api/), [Linkup](https://www.linkup.so/pricing), [Firecrawl Scrape API](https://docs.firecrawl.dev/api-reference/endpoint/scrape)).

## Build-versus-buy analysis

### Buy the volatile acquisition machinery

Managed services should own the parts most exposed to external breakage:

- web indexing and result acquisition;
- anti-bot changes, CAPTCHA solving, fingerprinting, and browser fleet operations;
- residential proxy sourcing, reputation, rotation, and geo routing;
- JavaScript rendering and routine content extraction;
- deep-research orchestration when its evidence format is sufficient.

Bright Data, Browserbase, Firecrawl, Exa, Parallel, and Linkup all transfer meaningful maintenance in these areas. Rebuilding that capability from SearXNG + Crawl4AI + Playwright does not meet a “low maintenance” goal; it changes the components while keeping upstream scraping failures, browser updates, proxy operations, monitoring, and security local.

### Keep only the control plane that protects an actual requirement

A reduced Argus should contain:

- one normalized search/evidence interface;
- one primary research/search adapter and one hard-page/browser adapter;
- policy that selects the expensive path only when required;
- caller attribution, tier/spend caps, and durable usage accounting;
- a common provenance envelope with source URLs plus provider, extractor, egress, machine, source type, timestamps, and explicit outcome/failure state;
- durable SQL storage and cache isolation where auditability requires it;
- a small live golden-corpus/scorecard runner.

It should delete or de-emphasize:

- most long-tail provider adapters;
- local browser farms in production;
- multiple overlapping managed extractors;
- home-grown deep-research orchestration that a vendor can return with adequate Basis/citations;
- routing complexity that has no measured impact on quality, cost, independence, or protected-target success.

This preserves Argus’s deep-module value—the stable governance boundary—while outsourcing shallow integrations and anti-bot mechanics.

### When full retirement is reasonable

Retire Argus and integrate one managed product directly if all of these are true:

- the caller needs useful answers/pages, not audit-grade execution evidence;
- source citations are sufficient provenance;
- account-wide vendor budgets are sufficient, with no per-caller cross-vendor policy;
- occasional protected-site failure is acceptable, or one browser vendor can be called manually;
- external data processing and vendor lock-in are acceptable;
- self-hosted/offline operation and independent-index diversity are not requirements.

Under that narrower definition, Exa is the most complete retrieval choice, Firecrawl the broadest extraction/browser choice, and Browserbase the strongest automation choice.

### When the current breadth is still justified

Keep a broader Argus only if live evidence shows that multi-provider fusion materially improves recall/diversity, that caller-specific credit pacing prevents meaningful cost or exhaustion incidents, or that topology memory and the quality-gated chain recover required targets that the two-vendor stack cannot. The repository contract alone does not prove those benefits are occurring in production.

## Recommendation and migration shape

1. **Run a shadow benchmark before deleting adapters.** Use the existing search and extraction golden corpus, plus known hard targets, and run Argus beside Parallel + Bright Data and Linkup + Firecrawl for two to four weeks. Capture result utility, source diversity, blocked-target success, content completeness, latency, total and marginal cost, citation quality, and failure transparency.
2. **Choose one default and one exception path.** The leading default is Parallel for research/search, with Bright Data invoked only for protected or interactive targets. Linkup is the stronger choice when maximizing recurring free search volume matters more than Basis quality.
3. **Make the benchmark decide which Argus semantics survive.** If callers use only URL citations, drop machine/egress detail. If audits or incident response actually rely on provider traces, failure reasons, and network provenance, retain them in the thin gateway.
4. **Migrate MCP and HTTP callers to the gateway, not directly to two vendors,** if consistent auth, budgets, and evidence are still required. The gateway can stay stateless at the caller edge while the authority owns durable policy and accounting, preserving the current production role boundary.
5. **Retire self-hosted acquisition by default.** Keep SearXNG/Crawl4AI/Playwright only for privacy, independence, or a benchmark-proven target advantage—not because they are nominally free.

**Recommended end state:** roughly two vendor adapters, one authority API, one normalized evidence model, one SQL usage/outcome repository, and one scorecard. That is a materially smaller product than today’s 14-provider/12-step implementation, but it retains the portion no vendor sells as a coherent service.

## Key uncertainties and validation risks

1. **Documentation is not target success.** “Stealth,” “unlocker,” and “undetected” are not equivalent guarantees, and official feature pages do not prove success against the owner’s exact protected sites. Test those targets live from the intended deployment regions.
2. **Pricing is unusually fluid.** Free credits, promo rates, model names, and credit multipliers can change faster than code. The current Linkup monthly balance, Parallel eligibility rules, Bright Data residential promo, and Browserbase plan allowances should be verified again at procurement.
3. **Residential attribution is not uniform.** Bright Data, Apify, Browserbase, Browserless, ZenRows, and ScrapingBee explicitly document residential paths. Firecrawl documents `basic`, `stealth`, and `auto`, but the reviewed page does not identify `stealth` as residential. Do not infer residential provenance from a successful stealth request.
4. **Research citations are not acquisition provenance.** Parallel Basis, Exa grounding citations, OpenAI URL citations, and Perplexity citations help verify claims. None publicly documents the complete provider/extractor/network/machine decision trace Argus expects.
5. **Self-hosting feature parity is easy to overstate.** Firecrawl’s own documentation lists cloud-only or externally supplied capabilities and substantial production responsibilities. Crawl4AI’s self-hosting page was also internally version-inconsistent on the snapshot date—its header referred to 0.9 while the body called 0.8.0 the latest stable image ([Crawl4AI self-hosting](https://docs.crawl4ai.com/core/self-hosting/)). Pin and test exact versions rather than relying on the landing page.
6. **MCP availability does not imply authority semantics.** A hosted MCP tool reduces wiring, but it does not automatically provide shared auth policy, caller attribution, durable budgets, cache isolation, or cross-surface equivalence.
7. **Percent-fit estimates are hypotheses.** The 75–95% bands above are architecture estimates from documented surfaces. Only a representative shadow run can establish whether a stack replaces 99% of *this* installation’s realized value.

## Bottom line

Argus is still useful **as a governance and evidence layer**, not as a reason to own every search adapter and browser fallback. The market has caught up with most of its acquisition machinery: managed vendors now provide inexpensive search, deep research, extraction, MCP, browser sessions, CAPTCHA handling, and residential egress. What the market still does not sell as one coherent product is Argus’s cross-vendor budget policy and execution-provenance contract.

The least-regret move is therefore **shrink, benchmark, then decide**: reduce Argus to a two-vendor control plane, prove the remaining semantics are used, and retire it entirely if source citations and vendor-level spending controls turn out to be enough.
