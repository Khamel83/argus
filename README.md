# Argus

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![PyPI](https://img.shields.io/pypi/v/argus-search)](https://pypi.org/project/argus-search/)
[![CI](https://github.com/Khamel83/argus/actions/workflows/ci.yml/badge.svg)](https://github.com/Khamel83/argus/actions/workflows/ci.yml)
[![MCP Server](https://img.shields.io/badge/MCP-server-purple)](https://modelcontextprotocol.io/)

Search companies give you free web searches — thousands per month across every major provider, and two of them are unlimited with no API key at all. Argus puts them all in one place and automatically picks the right one for each query so you don't waste credits.

## Two Ways to Use Argus

### 1. No server, no Docker, no API keys

```bash
pip install argus-search && argus search -q "python web frameworks"
```

That's it. DuckDuckGo handles the search — no accounts, no keys, no containers. You get unlimited free search from your laptop right now. Add API keys whenever you want more providers, or don't.

**Works on any machine with Python 3.11+:** your laptop, a Mac Mini, a Raspberry Pi, a cloud VM. Nothing to host.

**For MCP (Claude Code, Cursor, VS Code):**

```bash
pipx install argus-search[mcp] && argus mcp serve
```

Then add to your MCP config:

```json
{"mcpServers": {"argus": {"command": "argus", "args": ["mcp", "serve"]}}}
```

One command to install, one JSON block to connect. No server to run, no keys to configure.

### 2. Full install on hardware you already have

Got a Raspberry Pi running Pi-hole? A Mac Mini on your desk? An old laptop? That's enough to run the full stack — SearXNG (your own private search engine) plus local JS-rendering content extraction that doesn't depend on anyone's API.

```bash
docker compose up -d    # SearXNG + Argus
```

**What runs where:**

| What you have | What you get | How |
|--------------|-------------|-----|
| **Raspberry Pi 3** (1GB, probably running Pi-hole) | SearXNG + search via all providers | Pi-hole uses ~100MB, SearXNG needs ~512MB ([confirmed by maintainers](https://github.com/searxng/searxng/discussions/3884)). They fit together. |
| **Raspberry Pi 4** (4GB) | Everything — SearXNG, all providers, Crawl4AI | Same as above plus local JS-rendering extraction. Crawl4AI basic mode runs on Pi 4 ([per their docs](https://pypi.org/project/Crawl4AI/)). |
| **Mac Mini M1+** (8GB+) | Everything, plus headroom | Full stack with room for other services. Runs alongside whatever else is on there. |
| **Any old laptop** (4GB+) | Everything | Same as Pi 4. Docker + Python = full Argus. |
| **Free cloud VM** (1GB, e.g. OCI/AWS free tier) | SearXNG + search providers | Enough for search. Skip Crawl4AI — external APIs (Jina, You.com, Wayback) handle extraction. |
| **No machine at all** | DuckDuckGo + API providers | `pip install argus-search` on your laptop. No server needed. |

SearXNG is the most useful thing you're not running. It takes 512MB of RAM and gives you a private Google-style search engine that nobody can rate-limit, block, or charge for. It's the cheapest infrastructure upgrade you can make — it runs alongside Pi-hole on hardware millions of people already own.

## Why Argus

You don't need one search API. You need all of them — and you need them free.

| Provider | Credit type | Free capacity | Setup |
|----------|------------|---------------|-------|
| DuckDuckGo | Free (scraped) | Unlimited | None |
| SearXNG | Free (self-hosted) | Unlimited | Docker |
| GitHub | Free (API) | Unlimited | None (token for higher rate limit) |
| Brave Search | Monthly recurring | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| Tavily | Monthly recurring | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| Exa | Monthly recurring | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |
| Linkup | Monthly recurring | 1,000 queries/month | [signup](https://linkup.so) |
| Serper | One-time signup | 2,500 credits | [signup](https://serper.dev/signup) |
| Parallel AI | One-time signup | 4,000 credits | [signup](https://parallel.ai) |
| You.com | One-time signup | $20 credit | [platform](https://you.com/platform) |
| Valyu | One-time signup | $10 credit | [platform](https://platform.valyu.ai) |

**5,000 free queries per month** from the four recurring providers. Three providers need no API key at all (unlimited). Four more give you one-time credits for signing up. Argus routes to free providers first, monthly recurring next, one-time credits last. Budget-exhausted providers are skipped until they reset. When credits refresh, they come back online automatically.

## What It Does

You ask Argus a question. It checks the free providers first — if DuckDuckGo or SearXNG returns enough good results, it stops there. No credits touched. If you need more, it moves on to the monthly-credit providers (Brave, Tavily, Exa, Linkup), and only reaches for the one-time signup credits as a last resort. When a provider runs out of budget, Argus skips it and tries the next one. You get back one ranked, deduplicated list of results — no idea which provider(s) actually answered, unless you look at the traces.

**Content extraction** — Found a result but need the full text? Argus tries up to nine different ways to get it: first the fast local methods (trafilatura, Crawl4AI, Playwright), then external APIs if those fail (Jina, Valyu Contents, Firecrawl, You.com, Wayback Machine, archive.is). It checks each attempt for garbage output — paywall stubs, blank pages, error messages — and moves on if the quality isn't good enough.

**Multi-turn sessions** — Searching for something and want to refine? Pass a `session_id` and Argus remembers what you asked before. Your follow-up searches get context from prior queries automatically. Sessions persist to SQLite so they survive restarts.

## Content Extractors

| Extractor | Needs hosting? | Cost | Notes |
|-----------|---------------|------|-------|
| trafilatura | No (pure Python) | Free | Primary, fast |
| Crawl4AI | Yes (4GB RAM min) | Free | JS rendering, optional dep |
| Playwright | Yes (512MB per instance) | Free | Headless browser fallback |
| Jina Reader | No (external API) | Token-based | Works without any server |
| Valyu Contents | No (external API) | $0.001/URL | Cheapest external option |
| Firecrawl | No (external API) | 1 credit/page | Best Markdown quality |
| You.com Contents | No (external API) | $1/1k pages | Works without any server |
| Wayback Machine | No (external) | Free | Dead page recovery |
| archive.is | No (external) | Free | Dead page recovery |

The first three require a local machine. The last six are external APIs that work in any deployment — including serverless.

## How Routing Works

Argus picks which provider to ask based on two things: how much the provider costs (tier) and how well it handles that type of query (mode).

```
┌──────────────────────────────────────────────────────┐
│  Tier 0: FREE (SearXNG, DuckDuckGo, GitHub)         │  ← always first, unlimited
├──────────────────────────────────────────────────────┤
│  Tier 1: MONTHLY RECURRING                           │
│    Brave · Tavily · Exa · Linkup                     │  ← 5,000 free queries/mo
├──────────────────────────────────────────────────────┤
│  Tier 3: ONE-TIME CREDITS                            │
│    Serper · Parallel · You.com · Valyu · SearchAPI   │  ← budget-enforced, last resort
└──────────────────────────────────────────────────────┘
```

The idea is simple: burn through the free stuff first. If that's not enough, dip into the monthly credits. Save the one-time signup credits for when you really need them. When a provider runs out, it gets skipped — and it comes back automatically when its budget resets.

### Search Modes

Not all search providers are equally good at everything. Discovery mode favors Exa and Brave for finding related pages. Research mode leads with Tavily for broad retrieval. Recovery mode prioritizes Brave and Tavily for finding moved content. Tier sorting always applies first — within each tier, the mode picks who goes first.

| Mode | When to use | Runtime order |
|------|------------|---------------|
| `discovery` | Related pages, canonical sources | SearXNG → DuckDuckGo → GitHub → Brave → Exa → Tavily → Linkup → Serper → Parallel → You → Valyu |
| `recovery` | Dead/moved URL recovery | SearXNG → DuckDuckGo → Brave → Tavily → Exa → Linkup → Serper → Parallel → You → Valyu |
| `grounding` | Few live sources for fact-checking | SearXNG → DuckDuckGo → Brave → Linkup → Serper → Parallel → You → Valyu |
| `research` | Broad exploratory retrieval | SearXNG → DuckDuckGo → GitHub → Tavily → Exa → Brave → Linkup → Serper → Parallel → You → Valyu |

Free providers (SearXNG, DuckDuckGo) always lead. Within each tier, the order reflects which provider handles that query type best.

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

Available tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`, `cookie_health`, `valyu_answer`

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
| `argus/providers/` | Provider adapters (one per search API) |
| `argus/extraction/` | 9-step URL extraction fallback chain with quality gates |
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
| `ARGUS_VALYU_API_KEY` | — | Valyu API key (search, contents, answer) |
| `ARGUS_FIRECRAWL_API_KEY` | — | Firecrawl API key (content extraction) |
| `ARGUS_GITHUB_API_KEY` | — | GitHub token (higher rate limit) |
| `ARGUS_*_MONTHLY_BUDGET_USD` | 0 (unlimited) | Query-count budget per provider |
| `ARGUS_CRAWL4AI_ENABLED` | false | Enable Crawl4AI extraction step |
| `ARGUS_YOU_CONTENTS_ENABLED` | false | Enable You.com Contents API extraction |
| `ARGUS_CACHE_TTL_HOURS` | 168 | Result cache TTL |

## License

MIT
