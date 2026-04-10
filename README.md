# Argus

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/argus-search)](https://pypi.org/project/argus-search/)
[![MCP Server](https://img.shields.io/badge/MCP-server-purple)](https://modelcontextprotocol.io/)

Search companies give you free web searches — thousands per month across 10 providers. Argus puts them all in one place and routes every query to the right one at the right time.

Zero-config free search with SearXNG and DuckDuckGo (no API keys, unlimited). Add API keys for Brave, Tavily, Exa, Linkup, and others to get thousands more free monthly credits. Argus tracks budgets, skips exhausted providers, and falls back automatically. You can do a thousand searches a day and only burn API credits when the free options don't cover it.

**Search → Extract → Answer.** Find URLs, then extract clean text from any page using an 8-step fallback chain. Multi-turn sessions remember prior queries so follow-up searches get smarter. Connect via HTTP, CLI, MCP, or Python import.

## Why Argus

You don't need one search API. You need all of them — and you need them free.

| What you get | Monthly free capacity |
|---|---|
| SearXNG (self-hosted) | Unlimited |
| DuckDuckGo (scraped) | Unlimited |
| Brave Search | 2,000 queries |
| Tavily | 1,000 queries |
| Exa | 1,000 queries |
| Linkup | 1,000 queries |
| Serper | 2,500 one-time credits |
| Parallel AI | 16,000 one-time credits |
| You.com | $100 credit (~500/mo paced) |

**That's 8,000+ free monthly queries before you touch a single paid credit** — and two providers with no limit at all. Argus routes to free providers first, then monthly recurring credits, then one-time credits. Budget-exhausted providers are skipped until they reset. When credits refresh, they come back online automatically.

## What It Does

You pass Argus a search query. It routes to providers in tier order — free/unlimited first (SearXNG, DuckDuckGo), then monthly recurring credits (Brave, Tavily, Linkup, Exa), then one-time credits (Serper, Parallel, You.com) — stopping early when enough useful results are found. Budget-exhausted providers are skipped automatically. Results are ranked, deduplicated, and returned as one clean list.

**Tier-based credit routing** — Providers are sorted by credit type: Tier 0 (free, unlimited) → Tier 1 (monthly recurring) → Tier 3 (one-time credits). Query-type routing is preserved within each tier — e.g., in research mode, Tavily and Exa still go before Brave within the monthly tier. Budget enforcement tracks query counts per provider on a 30-day rolling window.

**Content extraction** — 8-step fallback chain with quality gates: trafilatura → Crawl4AI → Playwright → Jina Reader → You.com Contents → Wayback Machine → archive.is. Each step is checked for paywall stubs, soft 404s, and minimum quality before falling back. SSRF protection blocks private IPs. Results cached in memory (168h TTL).

**Multi-turn sessions** — Pass a `session_id` with your searches and Argus remembers prior queries. Follow-up searches get context-enriched automatically. Sessions persist to SQLite.

## Quick Start

### Zero-config (free only, no API keys)

```bash
pip install argus-search
argus search -q "python web frameworks"
# Works immediately — uses DuckDuckGo, no keys needed
```

### With SearXNG (recommended for unlimited free search)

```bash
# Start SearXNG
docker run -d --name searxng -p 8080:8080 searxng/searxng:latest

# Point Argus at it
export ARGUS_SEARXNG_BASE_URL=http://127.0.0.1:8080
argus search -q "python web frameworks"
```

### With API keys (unlock 8,000+ more free monthly queries)

```bash
# Add your keys to .env
cp .env.example .env
# Edit .env — set whichever provider keys you have

# Or from PyPI with MCP support
pip install "argus-search[mcp]"
argus serve
```

### Docker (everything in one stack)

```bash
cp .env.example .env
docker compose up -d
curl http://localhost:8000/api/health
```

> **Note:** The PyPI package is `argus-search` (the name `argus` is taken). The CLI command is still `argus`.

## Providers

10 providers across 3 credit tiers. Two are free with no API key — the rest have generous free tiers.

### Search Providers

| Provider | Tier | Free tier | Setup |
|----------|------|----------|-------|
| [SearXNG](https://github.com/searxng/searxng) | 0 (free) | Unlimited (self-hosted) | Docker, no key |
| [DuckDuckGo](https://duckduckgo.com) | 0 (free) | Unlimited (scraped) | No setup |
| [Brave Search](https://brave.com/search/api/) | 1 (monthly) | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| [Tavily](https://tavily.com) | 1 (monthly) | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| [Exa](https://exa.ai) | 1 (monthly) | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |
| [Linkup](https://linkup.so) | 1 (monthly) | 1,000 queries/month | [signup](https://linkup.so) |
| [Serper](https://serper.dev) | 3 (one-time) | 2,500 credits (signup) | [signup](https://serper.dev/signup) |
| [Parallel AI](https://parallel.ai) | 3 (one-time) | 16,000 credits (signup) | [signup](https://parallel.ai) |
| [You.com](https://you.com) | 3 (one-time) | $100 credit (signup) | [platform](https://you.com/platform) |
| SearchAPI | 3 (one-time) | Placeholder | Not yet configured |

### Content Extractors

| Extractor | Type | Cost | Notes |
|-----------|------|------|-------|
| trafilatura | Local | Free | Primary, fast |
| Crawl4AI | Local | Free | JS rendering, optional dep |
| Playwright | Local | Free | Headless browser fallback |
| Jina Reader | API | Token-based | External fallback |
| You.com Contents | API | $1/1k pages | Uses You.com search key |
| Wayback Machine | External | Free | Dead page recovery |
| archive.is | External | Free | Dead page recovery |

Unset or blank API keys are silently skipped. You can run Argus with zero keys — it'll use DuckDuckGo for everything.

## How Routing Works

Two factors determine which provider handles each query: **credit tier** (primary) and **query type** (secondary).

```
┌──────────────────────────────────────────────────────┐
│  Tier 0: FREE (SearXNG, DuckDuckGo)                  │  ← always first, unlimited
├──────────────────────────────────────────────────────┤
│  Tier 1: MONTHLY RECURRING                           │
│    Brave · Tavily · Exa · Linkup                     │  ← 5,000+ free queries/mo
├──────────────────────────────────────────────────────┤
│  Tier 3: ONE-TIME CREDITS                            │
│    Serper · Parallel · You.com · SearchAPI           │  ← budget-enforced, last resort
└──────────────────────────────────────────────────────┘
```

Free providers always go first. If SearXNG returns enough results, the query stops there — no credits used. When free providers don't have enough, monthly-credit providers kick in. One-time credits are held in reserve. When any provider's budget is exhausted, it's skipped until the 30-day window resets.

### Search Modes

Each mode picks the best providers for that query type. Tier sorting always applies first.

| Mode | When to use | Runtime order |
|------|------------|---------------|
| `discovery` | Related pages, canonical sources | SearXNG → DuckDuckGo → Brave → Exa → Tavily → Linkup → Serper → Parallel → You |
| `recovery` | Dead/moved URL recovery | SearXNG → DuckDuckGo → Brave → Tavily → Exa → Linkup → Serper → Parallel → You |
| `grounding` | Few live sources for fact-checking | SearXNG → DuckDuckGo → Brave → Linkup → Serper → Parallel → You |
| `research` | Broad exploratory retrieval | SearXNG → DuckDuckGo → Tavily → Exa → Brave → Linkup → Serper → Parallel → You |

## Integration

### HTTP API

All endpoints prefixed with `/api`. OpenAPI docs at `http://localhost:8000/docs`.

```bash
# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "mode": "discovery", "max_results": 5}'

# Multi-turn search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "what about async?", "session_id": "my-session"}'

# Extract content
curl -X POST http://localhost:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'

# Recover a dead URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Health & budgets
curl http://localhost:8000/api/health/detail
curl http://localhost:8000/api/budgets
```

### CLI

```bash
argus search -q "python web framework"              # zero-config, uses DuckDuckGo
argus search -q "python web framework" --mode research -n 20
argus search -q "fastapi" --session my-session       # multi-turn context
argus extract -u "https://example.com/article"       # extract clean text
argus extract -u "https://example.com/article" -d nytimes.com  # auth extraction
argus recover-url -u "https://dead.link" -t "Title"
argus health                                         # provider status
argus budgets                                        # budget + token balances
argus set-balance -s jina -b 9833638                 # track token balance
argus test-provider -p brave                         # smoke-test a provider
argus serve                                          # start API server
argus mcp serve                                      # start MCP server
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

response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY, max_results=10)
)
for r in response.results:
    print(f"{r.title}: {r.url} (score: {r.score:.3f})")

content = await extract_url(response.results[0].url)
print(content.title)
print(content.text)
```

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
| `argus/providers/` | 10 provider adapters (one per search API) |
| `argus/extraction/` | 8-step URL extraction fallback chain with quality gates |
| `argus/sessions/` | Multi-turn session store and query refinement |
| `argus/api/` | FastAPI HTTP endpoints |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | PostgreSQL query/result storage |

## Configuration

All config via environment variables. See `.env.example` for the full list. Missing keys degrade gracefully — providers are skipped, not errors.

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
