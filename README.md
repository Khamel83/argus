# Argus

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/argus-search)](https://pypi.org/project/argus-search/)
[![MCP Server](https://img.shields.io/badge/MCP-server-purple)](https://modelcontextprotocol.io/)

Stop wiring search APIs into every project. Argus is one endpoint that talks to 9 search providers — with tier-based credit-aware routing, automatic fallback, result ranking, health tracking, and budget enforcement. Connect via HTTP, CLI, MCP, or Python import. Add a provider key, it works. Remove it, it degrades gracefully.

**Search → Extract → Answer.** Argus doesn't just find URLs — it can fetch and extract clean text from any page using an 8-step fallback chain, and it remembers your prior queries so follow-up searches get smarter.

## What It Does

You pass Argus a search query. It routes to providers in tier order — free/unlimited first (SearXNG), then monthly recurring credits (Brave, Tavily, Linkup, Exa), then one-time credits (Serper, Parallel, You.com) — stopping early when enough useful results are found. Budget-exhausted providers are skipped automatically. Results are ranked, deduplicated, and returned as one clean list.

**Tier-based credit routing** — Providers are sorted by credit type: Tier 0 (free, unlimited) → Tier 1 (monthly recurring) → Tier 3 (one-time credits). Query-type routing is preserved within each tier — e.g., in research mode, Tavily and Exa still go before Brave within the monthly tier. Budget enforcement tracks query counts per provider on a 30-day rolling window. When credits run out, the provider is skipped until they refresh.

**Content extraction** — 8-step fallback chain with quality gates: trafilatura → Crawl4AI → Playwright → Jina Reader → You.com Contents → Wayback Machine → archive.is. Each result is checked for paywall stubs, soft 404s, and minimum quality before moving on. SSRF protection blocks private IPs. Results are cached in memory (168h TTL). Authenticated extraction via cookies is supported for paywall domains.

**Multi-turn sessions** — Pass a `session_id` with your searches and Argus remembers what you've asked before. Follow-up queries get context-enriched automatically. Sessions persist to SQLite across restarts.

**Token balance tracking** — Track remaining API credits in a local SQLite database. Balances auto-decrement as you extract content. Set balances via CLI, view via API or `argus budgets`.

## Quick Start

### Docker (recommended)

```bash
# 1. Create .env with your provider keys
cp .env.example .env
# Edit .env — at minimum, set provider API keys

# 2. Start Argus + Postgres + SearXNG
docker compose up -d

# 3. Verify
curl http://localhost:8000/api/health

curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi tutorial", "mode": "discovery"}'
```

### Local install

```bash
# From PyPI
pip install "argus-search[mcp]"

# With Crawl4AI (self-hosted JS rendering extractor)
pip install "argus-search[mcp,crawl4ai]"

# Or from source
git clone https://github.com/Khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
cp .env.example .env
pip install -e ".[mcp]"
argus serve
```

> **Note:** The PyPI package is `argus-search` (the name `argus` is taken). The CLI command is still `argus`.

## Providers

9 providers across 3 credit tiers. SearXNG is free and unlimited — everything else has generous free tiers.

### Search Providers

| Provider | Tier | Free tier | API |
|----------|------|----------|-----|
| [SearXNG](https://github.com/searxng/searxng) | 0 (free) | Unlimited (self-hosted) | No key needed |
| [Brave Search](https://brave.com/search/api/) | 1 (monthly) | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| [Tavily](https://tavily.com) | 1 (monthly) | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| [Exa](https://exa.ai) | 1 (monthly) | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |
| [Linkup](https://linkup.so) | 1 (monthly) | 1,000 standard queries/month | [signup](https://linkup.so) |
| [Serper](https://serper.dev) | 3 (one-time) | 2,500 credits (signup) | [signup](https://serper.dev/signup) |
| [Parallel AI](https://parallel.ai) | 3 (one-time) | 16,000 credits (signup) | [signup](https://parallel.ai) |
| [You.com](https://you.com) | 3 (one-time) | $100 credit on signup | [platform](https://you.com/platform) |
| SearchAPI | 3 (one-time) | Placeholder | Not yet configured |

### Content Extractors

| Extractor | Type | Cost | Notes |
|-----------|------|------|-------|
| trafilatura | Local | Free | Primary, fast, no API |
| Crawl4AI | Local | Free | JS rendering, needs `crawl4ai` package |
| Playwright | Local | Free | Headless browser fallback |
| Jina Reader | API | Token-based | External fallback |
| You.com Contents | API | $1/1k pages | Uses You.com search key |
| Wayback Machine | External | Free | Dead page recovery |
| archive.is | External | Free | Dead page recovery |

Set keys in `.env`:
```
ARGUS_BRAVE_API_KEY=BSA...
ARGUS_SERPER_API_KEY=abc...
ARGUS_TAVILY_API_KEY=tvly-...
ARGUS_EXA_API_KEY=...
ARGUS_LINKUP_API_KEY=...
ARGUS_PARALLEL_API_KEY=...
ARGUS_YOU_API_KEY=...
```

Unset or blank keys are silently skipped. You can run Argus with just SearXNG and no paid keys at all.

### SearXNG Setup

The included `docker-compose.yml` starts SearXNG automatically. If running separately:

```bash
docker run -d --name searxng -p 8080:8080 searxng/searxng:latest
curl http://localhost:8080/search?q=test\&format=json
```

## How Routing Works

Providers are selected by two factors: **credit tier** (primary) and **query type** (secondary).

```
┌──────────────────────────────────────────────┐
│  Tier 0: FREE (SearXNG)                      │  ← always first
├──────────────────────────────────────────────┤
│  Tier 1: MONTHLY RECURRING                   │
│    Brave · Tavily · Exa · Linkup             │  ← mode-specific order within tier
├──────────────────────────────────────────────┤
│  Tier 3: ONE-TIME CREDITS                    │
│    Serper · Parallel · You.com · SearchAPI   │  ← last resort, budget-enforced
└──────────────────────────────────────────────┘
```

When a provider's monthly budget is exhausted, it's skipped until the 30-day rolling window resets. Budgets are query-count based, set per provider in `.env`.

## Integration

### HTTP API

All endpoints prefixed with `/api`. OpenAPI docs at `http://localhost:8000/docs`.

```bash
# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "mode": "discovery", "max_results": 5}'

# Multi-turn search (session context)
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "session_id": "my-session"}'

# Extract content from a URL
curl -X POST http://localhost:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'

# Recover a dead URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Expand a query with related links
curl -X POST http://localhost:8000/api/expand \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi", "context": "python web framework"}'

# Health & budgets
curl http://localhost:8000/api/health/detail
curl http://localhost:8000/api/budgets
```

### CLI

```bash
argus search -q "python web framework"
argus search -q "python web framework" --mode research -n 20
argus search -q "fastapi" --session my-session        # multi-turn context
argus extract -u "https://example.com/article"        # extract clean text
argus extract -u "https://example.com/article" -d nytimes.com  # authenticated extraction
argus cookies import                                   # import browser cookies
argus cookies health                                   # check cookie freshness
argus recover-url -u "https://dead.link" -t "Title"
argus health                                          # provider status
argus budgets                                         # budget status + token balances
argus set-balance -s jina -b 9833638                  # track token balance
argus test-provider -p brave                          # smoke-test a provider
argus serve                                           # start API server
argus mcp serve                                       # start MCP server
```

All commands support `--json` for structured output.

### MCP

Add to your MCP client config:

```json
{
  "mcpServers": {
    "argus": {
      "command": "argus",
      "args": ["mcp", "serve"]
    }
  }
}
```

Works with **Claude Code**, **Cursor**, **VS Code**, and any MCP-compatible client. For remote access via SSE:

```json
{
  "mcpServers": {
    "argus": {
      "command": "argus",
      "args": ["mcp", "serve", "--transport", "sse", "--host", "127.0.0.1", "--port", "8001"]
    }
  }
}
```

Available tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`, `cookie_health`

### Python

```python
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode
from argus.extraction import extract_url

broker = create_broker()

# Search
response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY, max_results=10)
)
for r in response.results:
    print(f"{r.title}: {r.url} (score: {r.score:.3f})")

# Extract content from a result
content = await extract_url(response.results[0].url)
print(content.title)
print(content.text)
```

## Search Modes

Each mode defines which providers are best suited for that query type. Routing sorts by credit tier first, then preserves mode-specific ordering within each tier. Budget-exhausted providers are skipped.

| Mode | When to use | Actual runtime order |
|------|------------|---------------------|
| `discovery` | Related pages, canonical sources | SearXNG → Brave → Exa → Tavily → Linkup → Serper → Parallel → You |
| `recovery` | Dead/moved URL recovery | SearXNG → Brave → Tavily → Exa → Linkup → Serper → Parallel → You |
| `grounding` | Few live sources for fact-checking | SearXNG → Brave → Linkup → Serper → Parallel → You |
| `research` | Broad exploratory retrieval | SearXNG → Tavily → Exa → Brave → Linkup → Serper → Parallel → You |

SearXNG (free, unlimited) always leads. The within-tier ordering reflects which provider is strongest for each query type — Brave for general web, Tavily/Exa for structured deep retrieval, Serper for Google-quality results.

## Architecture

```
Caller (CLI / HTTP / MCP / Python)
  → SearchBroker
    → routing policy (tier-sorted, mode-specific within tiers)
      → provider executor (budget check → health check → search → early stop)
    → result pipeline (cache → dedupe → RRF ranking → response)
  → SessionStore (optional, per-request)
    → query refinement from prior context
  → Extractor (on demand)
    → SSRF → cache → rate limit → auth → QG →
      trafilatura → QG → crawl4ai → QG → playwright → QG →
      jina → QG → you_contents → QG → wayback → QG →
      archive.is → QG → return best
```

| Module | Responsibility |
|--------|---------------|
| `argus/broker/` | Tier-based routing, ranking, dedup, caching, health, budgets |
| `argus/providers/` | 9 provider adapters (one per search API) |
| `argus/extraction/` | 8-step URL extraction fallback chain with quality gates |
| `argus/sessions/` | Multi-turn session store and query refinement |
| `argus/api/` | FastAPI HTTP endpoints |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | PostgreSQL query/result storage |

## Configuration

All config via environment variables. See `.env.example` for the full list.

| Variable | Default | Description |
|----------|---------|-------------|
| `ARGUS_DB_URL` | — | PostgreSQL connection string |
| `ARGUS_SEARXNG_BASE_URL` | `http://127.0.0.1:8080` | SearXNG endpoint |
| `ARGUS_BRAVE_API_KEY` | — | Brave Search API key |
| `ARGUS_SERPER_API_KEY` | — | Serper API key |
| `ARGUS_TAVILY_API_KEY` | — | Tavily API key |
| `ARGUS_EXA_API_KEY` | — | Exa API key |
| `ARGUS_LINKUP_API_KEY` | — | Linkup API key |
| `ARGUS_PARALLEL_API_KEY` | — | Parallel AI API key |
| `ARGUS_YOU_API_KEY` | — | You.com API key |
| `ARGUS_*_MONTHLY_BUDGET_USD` | 0 (unlimited) | Query-count budget per provider |
| `ARGUS_CRAWL4AI_ENABLED` | false | Enable Crawl4AI extraction step |
| `ARGUS_YOU_CONTENTS_ENABLED` | false | Enable You.com Contents API extraction |
| `ARGUS_CACHE_TTL_HOURS` | 168 | Result cache TTL |
| `ARGUS_JINA_API_KEY` | — | Jina Reader key (optional) |
| `ARGUS_EXTRACTION_TIMEOUT_SECONDS` | 10 | URL fetch timeout for extraction |
| `ARGUS_EXTRACTION_CACHE_TTL_HOURS` | 168 | Extraction cache TTL |
| `ARGUS_RATE_LIMIT` | 60 | Requests per window per client IP |

## License

MIT

## Publishing

The PyPI package is **`argus-search`** (the name `argus` is taken).

### Release checklist

1. Bump `version` in `pyproject.toml`
2. Commit and push to `main`
3. Build: `python3 -m build`
4. Publish: `PYPI_API_TOKEN=$(secrets get PYPI_API_TOKEN) python3 -m twine upload dist/*`
5. Create GitHub release: `gh release create v<version> --title "v<version>"`
