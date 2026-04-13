# Additional Search Providers & Content Extractors for Argus

**Research date:** 2026-04-09
**Status:** Active providers already in Argus: SearXNG, Brave, Serper, Tavily, Exa (active); SearchAPI, You.com (stubs)
**Active extractors already in Argus:** trafilatura (local), Jina Reader (API), Playwright (JS rendering), auth_extractor (cookie-based), archive_extractor, wayback_extractor

---

## Executive Summary

The highest-impact additions to Argus fall into two tiers. **Tier 1 (add soon):** Google Programmable Search Engine as a free/cheap foundational provider ($5/1K queries, 100 free/day), Perplexity Sonar API for answer-synthesis mode ($5-12/1K + tokens, useful when the caller LLM is weak), and Crawl4AI as a self-hosted extraction fallback (free, open-source, 50K+ GitHub stars, LLM-aware chunking). **Tier 2 (add when needed):** SerpAPI for multi-engine SERP access ($75+/mo, enterprise-grade), Firecrawl for integrated search+extraction ($83/100K credits), Kagi API for high-quality ad-free results ($10/mo plan with API access), and Diffbot for structured knowledge-graph extraction (10K free calls/mo). **Skip:** DuckDuckGo (no official API), Bing Search API (retired August 2025), Readwise Reader (consumer product, no API), ScrapingBee (HTML-only, expensive for what Argus already does with Playwright).

---

## Search Providers

### Google Programmable Search Engine (Custom Search JSON API)

- **URL:** https://developers.google.com/custom-search
- **Type:** Search provider
- **Pricing:** 100 queries/day free, $5 per 1,000 queries beyond that
- **API:** REST JSON API, official Google client libraries
- **Unique value:** Google's index is the largest in the world. Argus already has Serper (Google SERP wrapper), but having Google's official API as a direct integration provides a free floor of 3,000 searches/month without any API key cost. Serper's pricing is $0.30-1.00/1K, so Google CSE at $5/1K is more expensive at volume but the 100/day free tier is unmatched for light usage. Also supports "Custom Search Engines" scoped to specific sites/domains.
- **Self-hostable:** No (Google cloud service)
- **Recommendation:** **Add.** The free 100 queries/day is a no-brainer as a fallback tier. Implementation is simple REST. Gives Argus a direct Google integration that doesn't depend on a third-party SERP scraper.

### Perplexity Sonar API

- **URL:** https://docs.perplexity.ai/docs/getting-started/pricing
- **Type:** Search + answer synthesis (returns grounded answers, not just links)
- **Pricing:** $5/1K search requests + $1/M input tokens, $1/M output tokens (Sonar base). Sonar Pro: $3/M input, $15/M output. Pro Search (multi-step): additional costs per step.
- **API:** REST API, OpenAI-compatible SDK format
- **Unique value:** Unlike every other provider Argus has, Perplexity returns a *fully synthesized answer* with inline citations, not raw search results. This is useful for a new "grounding" mode where the broker returns a ready-made answer instead of URLs. Average latency ~11 seconds (much slower than raw search). Also offers an Agent API for multi-step research.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Adds a fundamentally new capability (answer synthesis) but only makes sense if Argus callers sometimes lack their own LLM. If all callers have their own synthesis LLM (which is the typical Argus use case), Perplexity's value is marginal. Could be useful as a premium mode option.

### SerpAPI

- **URL:** https://serpapi.com
- **Type:** Search provider (multi-engine SERP scraper)
- **Pricing:** $75/month for 5,000 searches, scaling to $275/month for 30,000. 250 free/month. Enterprise pricing available.
- **API:** REST JSON API, Python/Node/Ruby/Java clients, LangChain integration
- **Unique value:** Access to 40+ search engines (Google, Bing, YouTube, Amazon, Yelp, Google Maps, etc.) through a single API. 99.9% uptime SLA. Enterprise-grade reliability with up to 100 req/s. Argus already has Serper (Google-only) and Brave (independent index). SerpAPI would add multi-engine breadth (YouTube, Amazon, image search, news-specific, etc.).
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Expensive compared to alternatives (10-50x more than Serper per query), but the multi-engine coverage is unique. Worth adding if users need non-Google search engines (YouTube, Amazon, maps) programmatically. Low priority since Serper covers the main Google use case.

### Kagi Search API

- **URL:** https://help.kagi.com/kagi/api/search.html
- **Type:** Search provider
- **Pricing:** Requires a Kagi subscription ($5/month Starter for 300 searches, $10/month Professional for unlimited + API access). API is included with Professional and Ultimate plans.
- **API:** REST JSON API (documented in Kagi's help center)
- **Unique value:** Kagi has its own index and is widely regarded as producing higher-quality results than Google with zero ads. Supports "Lenses" for focused search (news, academic, technical). API returns standard search result objects (title, url, snippet). The quality advantage is real but the per-search cost at low volumes is high relative to Google CSE or Serper.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Quality is excellent but the $10/month minimum and the fact that API access requires a paid subscription makes this a niche addition. Could be added as a premium provider for users who already have Kagi subscriptions. Low priority.

### Valyu Search API

- **URL:** https://www.valyu.ai
- **Type:** Search + content extraction (both search and contents/answer APIs)
- **Pricing:** Search API $0.003/result (web), $0.01+/result (proprietary sources like financial data). Contents API $0.001/successful extraction. DeepResearch API $0.10-15/task. 16,000 free requests.
- **API:** REST API, Python SDK, search + content extraction in one platform
- **Unique value:** Benchmarked #1 across 5 domains (FreshQA, SimpleQA, finance, economics, medical) in independent testing against Google, Exa, and Parallel. Has access to proprietary/financial data sources. Combines search and content extraction. The per-result pricing is very competitive at $0.003 for web results.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** Competitive pricing and strong benchmark results make this interesting, but it's a newer provider with less ecosystem integration than Tavily/Exa. The combined search+extraction model mirrors what Argus already does internally. Worth watching.

### You.com Search API

- **URL:** https://you.com/api
- **Type:** Search provider (already exists as stub in Argus)
- **Pricing:** $6.25/1K calls for 1-50 results, $8.00/1K for 51-100 results. $100 in free credits.
- **API:** REST JSON API, OpenAI/Databricks/AWS Marketplace integrations
- **Unique value:** 10B+ page index with 4x better freshness scores than competitors. Citation-backed results. Vertical indexes for News, Healthcare, Legal. SOC 2 Type 2 compliant, zero data retention. Already stubbed in Argus -- completing the implementation would fill the You.com gap.
- **Self-hostable:** No
- **Recommendation:** **Add (complete existing stub).** The stub already exists. You.com offers competitive pricing and unique vertical indexes. Completing this provider is low effort since the interface pattern already exists.

### Firecrawl Search API

- **URL:** https://www.firecrawl.dev
- **Type:** Search + extraction (both in one platform)
- **Pricing:** $83/month for 100K credits (annual). 500 free one-time credits. Search: 2 credits per 10 results. Scrape: 1 credit per page.
- **API:** REST API, Python/Node SDKs, LangChain/LlamaIndex/MCP integrations
- **Unique value:** Only platform that combines search, full content extraction, autonomous agent endpoint, and browser sandbox in one API. Goes from search query to LLM-ready markdown in a single call. 70K+ GitHub stars (open-source core). This is a direct competitor to the search+extraction pipeline that Argus already builds internally.
- **Self-hostable:** Partially (open-source core available, cloud API for managed)
- **Recommendation:** **Maybe.** Feature-rich but the integrated search+extraction model overlaps with Argus's own value proposition. Adding it as a provider would mean Argus can delegate to Firecrawl's pipeline when the caller wants one-call convenience. The autonomous /agent endpoint is genuinely unique. Higher priority than SerpAPI but still depends on whether users need this "all-in-one" mode.

### DuckDuckGo

- **URL:** https://duckduckgo.com
- **Type:** Search provider
- **Pricing:** No official public API. Unofficial scraping via ddgs (Python package) or duckduckgo_search.
- **API:** No official API. Unofficial: `duckduckgo_search` Python package (scrapes DDG HTML), `ddgs` library.
- **Unique value:** Privacy-focused, no tracking. However, no official API means any integration would rely on scraping DDG's HTML, which is fragile and against terms. DDG's search index is also smaller than Google's, making results less comprehensive.
- **Self-hostable:** N/A (no API to self-host)
- **Recommendation:** **Skip.** No official API. Unofficial scraping packages break frequently and violate terms. Argus already has SearXNG which can use DuckDuckGo as a backend engine if users want DDG results.

### Bing Web Search API

- **URL:** https://www.microsoft.com/en-us/bing/apis/bing-web-search-api
- **Type:** Search provider
- **Pricing:** **Retired August 2025.** Microsoft replaced it with "Azure AI Agents - Grounding with Bing Search" at $14-35 per grounded query.
- **API:** Replaced by Azure AI Search grounding
- **Unique value:** None anymore -- the API is dead.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Retired. Replaced by Azure AI Agents grounding at much higher pricing ($14-35/query). Not viable for Argus's pricing model.

---

## Content Extractors

### Crawl4AI

- **URL:** https://github.com/unclecode/crawl4ai
- **Type:** Content extraction (self-hosted)
- **Pricing:** Free and open-source (Apache 2.0). Compute costs only.
- **API:** Python async API, Docker deployment available
- **Unique value:** 50K+ GitHub stars. Purpose-built for RAG and LLM content pipelines. LLM-aware chunking strategies (splits content intelligently for context windows). Async-first architecture for high-throughput concurrent crawling. Configurable noise removal. Unlike trafilatura (which Argus already uses), Crawl4AI has built-in JavaScript rendering, handles SPAs, and includes LLM extraction strategies that work with OpenAI/other providers. Can be self-hosted for zero per-page API costs -- ideal for high-volume or privacy-sensitive workloads where Jina Reader's per-request costs add up.
- **Self-hostable:** Yes (Docker or bare-metal)
- **Recommendation:** **Add.** This is the strongest candidate for a new extractor. Free, self-hostable, purpose-built for exactly Argus's use case (LLM-ready content). Would complement trafilatura as a local extraction option and provide a self-hosted alternative to Jina Reader for users who want zero API costs. LLM-aware chunking is a feature Argus doesn't currently have.

### Firecrawl Extract

- **URL:** https://www.firecrawl.dev
- **Type:** Content extraction (single page and full-site crawl)
- **Pricing:** 1 credit per page. $83/month for 100K credits (annual). 500 free one-time credits.
- **API:** REST API, Python/Node SDKs
- **Unique value:** Best-in-class Markdown output quality (67% fewer tokens than raw HTML). Handles JavaScript-rendered pages, SPAs, and complex sites. Full-site recursive crawling with sitemap support. LLM-powered structured extraction with natural language schema definitions. Sub-1-second response times. This goes beyond what trafilatura can do (better JS rendering) and beyond Jina Reader (structured extraction, crawling).
- **Self-hostable:** Partially (open-source core, but managed API is the primary offering)
- **Recommendation:** **Maybe.** High quality but paid. Argus already has Playwright for JS rendering and Jina Reader for markdown conversion. The value add is structured extraction with schema support and the recursive crawling capability. Worth adding as a premium extraction option for users who want best-quality output and don't mind paying.

### Diffbot Extract API

- **URL:** https://docs.diffbot.com
- **Type:** Content extraction + knowledge graph
- **Pricing:** 10,000 API calls/month free (no credit card). Plus: $299/month. Professional: $999/month.
- **API:** REST API, LangChain integration, OpenAI-compatible LLM API (diffy.chat)
- **Unique value:** AI that automatically classifies web pages into structured entity types (articles, products, people, discussions, events) and returns machine-readable JSON. No prompts required -- Diffbot understands what kind of page it's looking at using computer vision and NLP. Also offers a Knowledge Graph API with 50B+ facts (246M organizations, 1.6B articles, 3M products). The auto-classification is genuinely unique -- no other extractor in Argus's stack can automatically determine that a page is a product page vs an article vs a person profile and extract the appropriate fields.
- **Self-hostable:** No
- **Recommendation:** **Maybe.** The free tier (10K calls/mo) is generous enough to make this worth implementing. The auto-classification capability adds something Argus doesn't have. Enterprise pricing makes it impractical at scale, but for light usage the free tier is excellent. Would be most valuable for users doing knowledge-graph construction or structured data extraction.

### Spider (spider.cloud)

- **URL:** https://spider.cloud
- **Type:** Content extraction + web crawling
- **Pricing:** Free: 200 credits. Basic: $19/month (20K credits). Standard: $49/month (100K credits).
- **API:** REST API, OpenAI-compatible format
- **Unique value:** Extremely fast crawling architecture with clean Markdown output. One of the cheapest per-page options for bulk extraction. Full-site crawling with sitemap support. OpenAI-compatible API format makes integration easy. Fastest page-to-Markdown conversion available per benchmarks.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Overlaps with what Argus already has (Jina Reader for markdown, Playwright for JS rendering). No structured extraction capability. Speed advantage is marginal for Argus's use case since extraction is typically not latency-critical.

### ScrapingBee

- **URL:** https://www.scrapingbee.com
- **Type:** Content extraction (HTML rendering, proxy rotation, CAPTCHA solving)
- **Pricing:** $49/month (150K credits). Free: 1,000 calls.
- **API:** REST API
- **Unique value:** Handles anti-bot measures that simpler extractors can't bypass. Automatic proxy rotation and CAPTCHA solving. JavaScript rendering with Chromium. Returns rendered HTML (not clean Markdown -- you parse it yourself). The anti-bot capability is the main differentiator.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Argus already has Playwright for JS rendering and auth_extractor for cookie-based access. ScrapingBee returns raw HTML (not Markdown), so Argus would need to run trafilatura on the output anyway. The anti-bot/proxy rotation is useful but expensive for what it adds over existing Playwright + trafilatura pipeline. Jina Reader is cheaper and returns clean Markdown directly.

### Olostep

- **URL:** https://www.olostep.com
- **Type:** Content extraction + web crawling + search
- **Pricing:** Custom pricing. Includes free tier with 100 credits.
- **API:** REST API, Python/Node SDKs
- **Unique value:** AI-native API designed for AI agents. Endpoints for /scrapes, /crawls, /searches, /answers, /agents, /parsers, /files, /schedules. Can automate research workflows with natural language prompts. Batch processing with structured extraction. The "agent" endpoint allows no-code automation of multi-step research workflows.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Overlaps heavily with Argus's own value proposition (Argus IS a search broker for AI agents). Adding Olostep as a provider would mean paying for capabilities Argus already implements. The agent/research workflow features are interesting but represent a different product category.

### ScrapeGraphAI

- **URL:** https://scrapegraphai.com
- **Type:** Content extraction + structured data extraction
- **Pricing:** Free: 100 credits. Starter: $19/month (5,000 credits). Growth: $85/month. Pro: $425/month.
- **API:** REST API, Python SDK, LangChain/LangGraph native tools
- **Unique value:** Uses LLMs to extract specific, typed data fields from web pages using natural language prompts. Pydantic schema validation for guaranteed structure. Auto-adapts to website changes (semantic extraction survives redesigns). Markdownify endpoint for clean Markdown (Jina Reader replacement). SmartScraper for structured JSON extraction.
- **Self-hostable:** Partially (open-source Python library available, cloud API for managed)
- **Recommendation:** **Maybe.** The structured extraction with Pydantic schema validation is unique and would add a new capability to Argus (schema-validated extraction). However, it requires an LLM API key to function (additional cost and dependency). Better suited as a user-level tool than a core Argus extractor.

### Readwise Reader

- **URL:** https://readwise.io/read
- **Type:** Content reading/highlighting (consumer product)
- **Pricing:** $7.99/month (consumer subscription)
- **API:** Unofficial/community API only. No official developer API.
- **Unique value:** Excellent read-later service with highlighting and annotation. But it's a consumer product, not a developer API. No programmatic extraction endpoint.
- **Self-hostable:** No
- **Recommendation:** **Skip.** Consumer product with no official API. Not relevant to Argus's architecture.

### Mozilla Readability (via readability-lxml / @mozilla/readability)

- **URL:** https://github.com/mozilla/readability (JavaScript), https://github.com/buriy/python-readability (Python)
- **Type:** Content extraction (library, not API)
- **Pricing:** Free and open-source
- **API:** Python library (readability-lxml), JavaScript library (@mozilla/readability)
- **Unique value:** The algorithm behind Firefox Reader View. Extracts main content from web pages by scoring text density, semantic HTML tags, link ratio penalties, etc. Argus already uses trafilatura which includes readability-inspired heuristics. Adding a dedicated readability pass could improve extraction quality for pages that trafilatura handles poorly.
- **Self-hostable:** Yes (Python package)
- **Recommendation:** **Skip (already covered).** Trafilatura already incorporates readability-style heuristics. Adding a separate readability pass would add marginal improvement at the cost of additional complexity. If extraction quality on specific sites is poor, the better fix is to improve trafilatura's configuration or add Crawl4AI as an alternative local extractor.

---

## Tier List / Priority Matrix

### Tier 1: Add Soon (high impact, low-to-medium effort)

| Provider | Type | Impact | Effort | Why |
|----------|------|--------|--------|-----|
| **Google CSE** | Search | High | Low | 100 free queries/day, Google's index, simple REST API |
| **You.com** (complete stub) | Search | Medium | Low | Stub already exists, $100 free credits, vertical indexes |
| **Crawl4AI** | Extraction | High | Medium | Free, self-hosted, LLM-aware chunking, 50K+ stars |

### Tier 2: Add When Needed (medium impact, medium effort)

| Provider | Type | Impact | Effort | Why |
|----------|------|--------|--------|-----|
| **Perplexity Sonar** | Search+Answer | High | Medium | Adds answer-synthesis mode, but expensive and slow |
| **Firecrawl** | Search+Extraction | High | Medium | Best integrated search+extraction, but overlaps with Argus's core |
| **Diffbot** | Extraction | Medium | Medium | Auto-classification is unique, 10K free calls/mo |
| **Kagi** | Search | Medium | Low | High-quality results, but $10/month minimum subscription |
| **Valyu** | Search+Extraction | Medium | Medium | #1 on benchmarks, competitive pricing, but newer/less proven |

### Tier 3: Skip (low impact or covered by existing tools)

| Provider | Type | Why Skip |
|----------|------|----------|
| DuckDuckGo | Search | No official API, scraping violates terms |
| Bing Web Search | Search | Retired August 2025 |
| SerpAPI | Search | 10-50x more expensive than Serper, only adds multi-engine breadth |
| ScrapingBee | Extraction | HTML-only, expensive, Playwright+trafilatura covers same ground |
| Spider | Extraction | No structured extraction, overlaps with Jina Reader |
| Olostep | Search+Extraction | Overlaps with Argus's own value proposition |
| ScrapeGraphAI | Extraction | Requires LLM API key, better as a user tool than core extractor |
| Readwise Reader | Extraction | Consumer product, no official API |
| Mozilla Readability | Extraction | Already covered by trafilatura |

---

## Sources

### Search providers
- https://o-mega.ai/articles/top-10-ai-search-apis-for-agents-2026 -- Comprehensive pricing/performance comparison of 10 AI search APIs
- https://www.firecrawl.dev/blog/best-web-search-apis -- Firecrawl's comparison of web search APIs for AI
- https://crustdata.com/blog/best-websearch-apis -- 7 best web search APIs for real-time data & AI apps
- https://brightdata.com/blog/web-data/best-research-apis -- Best research APIs in 2026 comparison
- https://composio.dev/content/9-top-ai-search-engine-tools -- 9 top AI search engine tools in 2026
- https://proxies.sx/blog/cheapest-serp-api-comparison-2026 -- Cheapest SERP API pricing comparison
- https://docs.perplexity.ai/docs/getting-started/pricing -- Perplexity API pricing documentation
- https://developers.google.com/custom-search/docs/paid_element -- Google Custom Search pricing
- https://kagi.com/pricing -- Kagi search pricing
- https://help.kagi.com/kagi/api/search.html -- Kagi Search API documentation
- https://serpapi.com/pricing -- SerpAPI pricing
- https://www.valyu.ai/pricing -- Valyu pricing
- https://scrape.do/blog/google-serp-api/ -- Best SERP APIs in 2026 comparison
- https://brave.com/blog/most-powerful-search-api-for-ai/ -- Brave LLM Context API announcement
- https://www.valyu.ai/blogs/benchmarking-search-apis-for-ai-agents -- Valyu benchmarking study

### Content extractors
- https://scrapegraphai.com/blog/jina-alternatives -- 7 best Jina Reader alternatives for AI web scraping in 2026
- https://scrapegraphai.com/blog/firecrawl-alternatives -- 7 best Firecrawl alternatives for AI web scraping in 2026
- https://github.com/unclecode/crawl4ai -- Crawl4AI open-source web crawler for RAG
- https://docs.diffbot.com/reference/introduction-to-diffbot-apis -- Diffbot API documentation
- https://docs.diffbot.com/reference/extract-introduction -- Diffbot Extract API
- https://www.olostep.com/ -- Olostep web data API
- https://www.firecrawl.dev/blog/best-open-source-web-crawler -- Best open-source web crawlers in 2026
- https://prospeo.io/s/firecrawl-alternatives -- Firecrawl alternatives tested & compared
- https://www.digitalapplied.com/blog/ai-web-scraping-tools-firecrawl-guide-2025 -- AI web scraping tools comparison

### Benchmarking and Reddit discussions
- https://www.reddit.com/r/AI_Agents/comments/1rc3nps/ -- Cheapest real-time web search AI API discussion
- https://www.reddit.com/r/LocalLLaMA/comments/1jw4yvq/ -- Best scraper tool discussion (Firecrawl vs alternatives)
- https://medium.com/@unicodeveloper/search-apis-for-ai-agents-we-tested-5-domains-heres-the-gap -- Search API benchmarking across 5,000+ queries
