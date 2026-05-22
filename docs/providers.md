# Providers and Extractors

This page is a fuller reference for the search providers and content extractors
behind Argus. For the short version with budgets and signup links see the
**Providers** section of [../README.md](../README.md).

## Search providers

Search providers live in `argus/providers/` and each implements `BaseProvider`.
Tier ordering is enforced by `argus/broker/policies.py`; tier 0 is tried before
tier 1 before tier 3.

| Tier | Provider | Module | Default | Notes |
|------|----------|--------|---------|-------|
| 0 (free, no key) | DuckDuckGo | `duckduckgo.py` | Enabled | Scraped via `ddgs`. Rate-limited under heavy use. |
| 0 (free, no key) | Yahoo | `yahoo.py` | Enabled | Scraped HTML. Fragile — auto-skipped when broken. |
| 0 (free, self-hosted) | SearXNG | `searxng.py` | **Disabled** | 70+ engines behind one Docker container. Enable with `ARGUS_SEARXNG_ENABLED=true`. |
| 0 (free API) | GitHub | `github.py` | Disabled | Higher rate limit with `ARGUS_GITHUB_API_KEY`. |
| 0 (free API, computed) | WolframAlpha | `wolfram.py` | Disabled | Returns computed answers (math, conversions). Only active in `grounding` and `research` modes. |
| 1 (monthly recurring) | Brave | `brave.py` | Disabled | 2,000 free queries/month. |
| 1 (monthly recurring) | Tavily | `tavily.py` | Disabled | 1,000 free queries/month. |
| 1 (monthly recurring) | Exa | `exa.py` | Disabled | 1,000 free queries/month. |
| 1 (monthly recurring) | Linkup | `linkup.py` | Disabled | 1,000 free queries/month. |
| 3 (one-time credit) | Serper | `serper.py` | Disabled | 2,500 lifetime credits. |
| 3 (one-time credit) | Parallel AI | `parallel.py` | Disabled | 4,000 lifetime credits. |
| 3 (one-time credit) | You.com | `you.py` | Disabled | $20 lifetime credit. |
| 3 (one-time credit) | Valyu | `valyu.py` | Disabled | $10 lifetime credit. USD budget tracking. |
| 3 (one-time credit) | SearchAPI | `searchapi.py` | Disabled | Paid only. |

"Disabled" means the provider is dormant until you set both its API key and
`ARGUS_<PROVIDER>_ENABLED=true`. Missing keys never raise — the provider is
silently skipped.

### Adding a provider

See [../CONTRIBUTING.md](../CONTRIBUTING.md#adding-a-search-provider).

## Content extractors

Extractors live in `argus/extraction/` and run as a fallback chain. After each
attempt, `completeness.py` and `quality_gate.py` decide whether the result is
good enough to return or whether to fall through to the next step.

| Order | Step | Module | Setup | Notes |
|-------|------|--------|-------|-------|
| 1 | Auth extractor (paywall cookies) | `auth_extractor.py` | Cookie jar configured | Used when a `-d <domain>` hint is passed. |
| 2 | trafilatura | `extractor.py` (built in) | None — pip dependency | Fast, no JS. The default success case. |
| 3 | Crawl4AI | `crawl4ai_extractor.py` | `pip install "argus-search[crawl4ai]"` + `ARGUS_CRAWL4AI_ENABLED=true` | Local JS rendering. |
| 4 | Obscura (stealth headless) | `obscura_extractor.py` | Install `obscura` binary on `$PATH` or set `ARGUS_OBSCURA_CDP_URL` | ~70MB binary, 30MB RAM, stealth fingerprinting. |
| 5 | Playwright | `playwright_extractor.py` | `playwright install chromium` | Full Chromium. Can use Obscura as backend via CDP. |
| 6 | Residential worker | `residential_extractor.py` | Run residential service on another machine | Routes through a residential IP for blocked sites. |
| 7 | Jina Reader | external API | None (free token allotment) | URL → Markdown. |
| 8 | Valyu Contents | `valyu_extractor.py` | `ARGUS_VALYU_API_KEY` | Tracks $0.001/URL against Valyu budget. |
| 9 | Firecrawl | `firecrawl_extractor.py` | `ARGUS_FIRECRAWL_API_KEY` | |
| 10 | You.com Contents | `you_extractor.py` | `ARGUS_YOU_CONTENTS_ENABLED=true` + `ARGUS_YOU_API_KEY` | |
| 11 | Wayback Machine | `wayback_extractor.py` | None | Last-known archive. |
| 12 | archive.is | `archive_extractor.py` | None | Final fallback. |

### Completeness assessment

After every successful step, `completeness.py` scores five signals — trailing
ellipsis, feed truncation markers, mid-sentence endings, abrupt final
paragraphs, and suspicious round word counts — and returns `is_complete`,
`completeness_confidence`, and `truncation_type` alongside the text. When
confidence is ≥ 85%, Argus continues trying the next extractor rather than
returning a partial result.

Callers that already have text (e.g. RSS feed items) can call
`POST /api/assess-content` to check completeness without triggering an
extraction.

### Adaptive Domain Memory

`domain_memory.py` records, per domain, whether datacenter extraction has been
failing. Subsequent extractions for that domain skip ahead to a residential
worker, saving the failure cost. See [../CONTEXT.md](../CONTEXT.md) for details.

### SSRF and rate limiting

`ssrf.py` blocks private IP ranges (10.x, 172.16-31.x, 192.168.x, 127.x, ::1)
before any HTTP call. `rate_limit.py` enforces 10 requests/minute per domain.
