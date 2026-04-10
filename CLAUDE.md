# Project Instructions

## Overview

Search broker that puts free search APIs in one place with intelligent credit-aware routing. 10 provider adapters: SearXNG and DuckDuckGo (free, unlimited, no API keys), Brave, Tavily, Exa, Linkup (monthly free tiers: 5,000 queries/month combined), Serper, Parallel AI, You.com (one-time signup credits: ~6,500 + $20 combined), SearchAPI (stub). Tier-based routing: free providers first, monthly recurring next, one-time credits last. Budget enforcement skips exhausted providers automatically. 8-step content extraction fallback chain. Multi-turn sessions (SQLite). Connect via HTTP, CLI, MCP, or Python import.

## Two Deployment Tiers

### Tier 1: No server (API keys only)
- `pip install argus-search` — works immediately with DuckDuckGo
- Add API keys for 5,000+ more free monthly queries
- Extraction via external APIs only (Jina, You.com Contents, Wayback)
- Runs on any machine with Python 3.11+ (laptop, Mac Mini, Pi, cloud VM)
- No Docker, no database server, no API keys required to start

### Tier 2: Full install on hardware you already have
- Raspberry Pi 3 (1GB): SearXNG + all search providers. Fits alongside Pi-hole (SearXNG ~512MB, Pi-hole ~100MB).
- Raspberry Pi 4 (4GB): Everything — SearXNG, all providers, Crawl4AI local JS extraction.
- Mac Mini M1+ (8GB): Full stack with headroom for other services.
- Any old laptop (4GB+): Full stack via Docker.
- Free cloud VM (1GB): SearXNG + search. No Crawl4AI (use external APIs for extraction).
- `docker compose up -d` for one-command setup

## Key Commands

```bash
# Setup
cp .env.example .env                    # configure providers and DB
pip install "argus-search[mcp]"         # install from PyPI (with MCP support)
pip install "argus-search[mcp,crawl4ai]" # with Crawl4AI extractor
# or from source: pip install -e ".[mcp]"

# Zero-config search (no API keys needed)
argus search -q "python web frameworks"  # uses DuckDuckGo automatically

# Run
argus serve                   # HTTP API on :8000
argus mcp serve               # MCP server (stdio)
argus mcp serve --transport sse --port 8001

# Search
argus search -q "query" --mode discovery
argus search -q "follow up" --session abc123   # multi-turn context

# Content Extraction
argus extract -u "https://example.com/article"

# Admin
argus health                  # provider status
argus budgets                 # budget status + token balances
argus set-balance -s jina -b 9833638   # set token balance for a service
argus test-provider -p brave

# Test
pytest tests/
```

## Architecture

```
Caller (CLI/HTTP/MCP/Python)
  → SearchBroker
    → routing policy (tier-sorted, mode-specific within tiers)
      → provider executor (budget check → health check → search → early stop)
    → result pipeline (cache → dedupe → RRF ranking → response)
  → SessionStore (optional, per-request)
    → query refinement from prior context
  → Extractor (on demand)
    → trafilatura → crawl4ai → playwright → jina →
      you_contents → wayback → archive.is
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

## Provider Tiers

| Tier | Providers | Credits |
|------|-----------|---------|
| 0 (free) | SearXNG, DuckDuckGo | Unlimited, no API keys |
| 1 (monthly) | Brave (2k/mo), Tavily (1k/mo), Exa (1k/mo), Linkup (1k/mo) | Recurring monthly |
| 3 (one-time) | Serper (2.5k), Parallel (4k), You.com ($20), SearchAPI | Don't come back |

Routing sorts by tier first (free → monthly → one-time), then preserves mode-specific ordering within each tier. Budget enforcement skips exhausted providers automatically.

## Interfaces

| Interface | How to use |
|-----------|-----------|
| HTTP API | `POST /api/search`, `POST /api/extract`, `POST /api/recover-url`, `POST /api/expand` — OpenAPI at `/docs` |
| CLI | `argus search`, `argus extract`, `argus recover-url`, `argus health`, `argus budgets`, `argus set-balance` |
| MCP | `argus mcp serve` — tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider` |
| Python | `from argus.broker.router import create_broker`, `from argus.extraction import extract_url` |

## Search Modes

Each mode defines which providers are best suited for that query type. Routing sorts by credit tier first, then preserves mode-specific ordering within each tier. Budget-exhausted providers are skipped.

| Mode | Use case | Actual runtime order |
|------|----------|---------------------|
| `discovery` | Related pages, canonical sources | SearXNG → DuckDuckGo → Brave → Exa → Tavily → Linkup → Serper → Parallel → You |
| `recovery` | Dead/moved URL | SearXNG → DuckDuckGo → Brave → Tavily → Exa → Linkup → Serper → Parallel → You |
| `grounding` | Few sources for fact-checking | SearXNG → DuckDuckGo → Brave → Linkup → Serper → Parallel → You |
| `research` | Broad exploratory | SearXNG → DuckDuckGo → Tavily → Exa → Brave → Linkup → Serper → Parallel → You |

Free providers (SearXNG, DuckDuckGo) always lead. Within-tier ordering reflects provider strengths per query type.

## Content Extraction

8-step fallback chain with quality gates between every step:

```
trafilatura (local, fast) → Crawl4AI (local, JS rendering) →
Playwright (local, headless browser) → Jina Reader (external API) →
You.com Contents ($1/1k pages) → Wayback Machine → archive.is
```

First 3 extractors need local hosting. Last 4 are external APIs that work anywhere. SSRF protection blocks private IPs. Results cached in memory (168h TTL). Domain rate limiting (10 req/min/domain). Authenticated extraction via cookies for paywall domains (NYT, Bloomberg, etc.).

## Multi-Turn Sessions

Pass `session_id` to search to enable conversational refinement. The broker remembers prior queries and uses them to context-enrich follow-up searches. Sessions persist to SQLite across restarts.

## Configuration

All config via env vars (see `.env.example`). Missing API keys degrade gracefully — providers are skipped, not errors. Budget values are query counts (not USD): 0 = unlimited, set to enforce credit tracking.

## Conventions

- Provider adapters must never leak provider-specific shapes outside `argus/providers/`
- All search results are `SearchResult`: url, title, snippet, domain, provider, score
- Extracted content is `ExtractedContent`: url, title, text, author, date, word_count
- Routes prefixed with `/api`
- Free/self-hosted-first: SearXNG and DuckDuckGo are the fallback floor
- Token balances persist in SQLite alongside budget tracking
- Budget env var is named `MONTHLY_BUDGET_USD` but values are query counts (legacy naming)
