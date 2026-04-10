# Argus

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/argus-search)](https://pypi.org/project/argus-search/)
[![MCP Server](https://img.shields.io/badge/MCP-server-purple)](https://modelcontextprotocol.io/)

Search companies give you free web searches — 5,000+ per month across 10 providers, plus two unlimited with no API key at all. Argus puts them all in one place and routes every query to the right one at the right time.

## Two Ways to Use Argus

### 1. No server, no Docker, no API keys

```bash
pip install argus-search
argus search -q "python web frameworks"
```

That's it. DuckDuckGo handles the search. No accounts, no keys, no containers. You get unlimited free search immediately. Add API keys whenever you want to unlock more providers — or don't, and DuckDuckGo handles everything.

**What you get with zero setup:**
- Unlimited search via DuckDuckGo (no API key)
- Content extraction via external APIs (Jina, You.com Contents, Wayback Machine)
- Multi-turn sessions (SQLite, local file)
- CLI, MCP server, HTTP API, Python import

**Add API keys to unlock:**
- 5,000 more free queries/month (Brave, Tavily, Exa, Linkup)
- ~6,500 one-time signup credits (Serper, Parallel, You.com)
- Full 8-step extraction chain (trafilatura, Crawl4AI, Playwright)

### 2. Full install (Docker, self-hosted)

Add SearXNG and you get a second unlimited free search engine with better structured results than DuckDuckGo. Add Crawl4AI and Playwright for local JS-rendering extraction that doesn't depend on any external API.

```bash
docker compose up -d    # SearXNG + Argus
```

**Hardware requirements:**

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| SearXNG | 1 vCPU, 512MB RAM ([confirmed by maintainers](https://github.com/searxng/searxng/discussions/3884)) | 1 vCPU, 1GB RAM |
| Argus (search only) | Any machine with Python 3.11+ | — |
| Argus + Crawl4AI | 4GB RAM ([per Crawl4AI docs](https://pypi.org/project/Crawl4AI/)) | 8GB RAM |
| Full stack (SearXNG + Argus + extraction) | 4GB RAM | 8GB RAM, any modern CPU |

Runs on a Raspberry Pi 4 (basic mode), an old laptop, a free cloud VM, or a Mac Mini. SearXNG is extremely lightweight — confirmed running on 512MB VMs with zero issues. Crawl4AI is the heaviest component at 4GB minimum (it runs Chromium under the hood). If you don't need JS-rendered extraction, skip it and save the RAM.

## Why Argus

You don't need one search API. You need all of them — and you need them free.

| Provider | Credit type | Free capacity | Setup |
|----------|------------|---------------|-------|
| DuckDuckGo | Free (scraped) | Unlimited | None |
| SearXNG | Free (self-hosted) | Unlimited | Docker |
| Brave Search | Monthly recurring | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| Tavily | Monthly recurring | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| Exa | Monthly recurring | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |
| Linkup | Monthly recurring | 1,000 queries/month | [signup](https://linkup.so) |
| Serper | One-time signup | 2,500 credits | [signup](https://serper.dev/signup) |
| Parallel AI | One-time signup | 4,000 credits | [signup](https://parallel.ai) |
| You.com | One-time signup | $20 credit | [platform](https://you.com/platform) |

**5,000 free queries per month** from the four recurring providers. Two providers need no API key at all (unlimited). Three more give you ~6,500 one-time credits for signing up. Argus routes to free providers first, monthly recurring next, one-time credits last. Budget-exhausted providers are skipped until they reset. When credits refresh, they come back online automatically.

## What It Does

You pass Argus a search query. It routes to providers in tier order — free/unlimited first (SearXNG, DuckDuckGo), then monthly recurring credits (Brave, Tavily, Linkup, Exa), then one-time credits (Serper, Parallel, You.com) — stopping early when enough useful results are found. Budget-exhausted providers are skipped automatically. Results are ranked, deduplicated, and returned as one clean list.

**Tier-based credit routing** — Providers are sorted by credit type: Tier 0 (free, unlimited) → Tier 1 (monthly recurring) → Tier 3 (one-time credits). Query-type routing is preserved within each tier. Budget enforcement tracks query counts per provider on a 30-day rolling window.

**Content extraction** — 8-step fallback chain: trafilatura → Crawl4AI → Playwright → Jina Reader → You.com Contents → Wayback Machine → archive.is. Each step is checked for paywall stubs, soft 404s, and minimum quality before falling back. SSRF protection blocks private IPs. Results cached in memory (168h TTL).

**Multi-turn sessions** — Pass a `session_id` with your searches and Argus remembers prior queries. Follow-up searches get context-enriched automatically. Sessions persist to SQLite.

## Content Extractors

| Extractor | Needs hosting? | Cost | Notes |
|-----------|---------------|------|-------|
| trafilatura | No (pure Python) | Free | Primary, fast |
| Crawl4AI | Yes (4GB RAM min) | Free | JS rendering, optional dep |
| Playwright | Yes (512MB per instance) | Free | Headless browser fallback |
| Jina Reader | No (external API) | Token-based | Works without any server |
| You.com Contents | No (external API) | $1/1k pages | Works without any server |
| Wayback Machine | No (external) | Free | Dead page recovery |
| archive.is | No (external) | Free | Dead page recovery |

The first three require a local machine. The last four are external APIs that work in any deployment — including serverless.

## How Routing Works

Two factors determine which provider handles each query: **credit tier** (primary) and **query type** (secondary).

```
┌──────────────────────────────────────────────────────┐
│  Tier 0: FREE (SearXNG, DuckDuckGo)                  │  ← always first, unlimited
├──────────────────────────────────────────────────────┤
│  Tier 1: MONTHLY RECURRING                           │
│    Brave · Tavily · Exa · Linkup                     │  ← 5,000 free queries/mo
├──────────────────────────────────────────────────────┤
│  Tier 3: ONE-TIME CREDITS                            │
│    Serper · Parallel · You.com · SearchAPI           │  ← budget-enforced, last resort
└──────────────────────────────────────────────────────┘
```

Free providers always go first. If a free provider returns enough results, the query stops there — no credits used. When free providers don't have enough, monthly-credit providers kick in. One-time credits are held in reserve. When any provider's budget is exhausted, it's skipped until the 30-day window resets.

### Search Modes

Each mode picks the best providers for that query type. Tier sorting always applies first.

| Mode | When to use | Runtime order |
|------|------------|---------------|
| `discovery` | Related pages, canonical sources | SearXNG → DuckDuckGo → Brave → Exa → Tavily → Linkup → Serper → Parallel → You |
| `recovery` | Dead/moved URL recovery | SearXNG → DuckDuckGo → Brave → Tavily → Exa → Linkup → Serper → Parallel → You |
| `grounding` | Few live sources for fact-checking | SearXNG → DuckDuckGo → Brave → Linkup → Serper → Parallel → You |
| `research` | Broad exploratory retrieval | SearXNG → DuckDuckGo → Tavily → Exa → Brave → Linkup → Serper → Parallel → You |

Free providers always lead. Within-tier ordering reflects which provider is strongest for each query type.

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
