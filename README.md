# Argus

<!-- mcp-name: io.github.Khamel83/argus -->

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![PyPI Version](https://img.shields.io/pypi/v/argus-search)](https://pypi.org/project/argus-search/)
[![PyPI Downloads](https://img.shields.io/pepy/dt/argus-search)](https://pepy.tech/projects/argus-search)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/Khamel83/argus/actions/workflows/ci.yml/badge.svg)](https://github.com/Khamel83/argus/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP-Registry-blue)](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus)
[![Docker](https://img.shields.io/badge/ghcr.io-khamel83%2Fargus-blue)](https://github.com/Khamel83/argus/pkgs/container/argus)

Multi-provider web search broker for AI agents. Routes across SearXNG, DuckDuckGo, GitHub, Brave, Tavily, Exa, and more — using RRF fusion, content extraction, and budget-aware routing so you don't waste your free search credits.

**Features at a glance:**

- **Multi-provider search** — 11 providers, one API, free-first tier routing
- **5,000+ free queries/month** — automatic budget tracking, exhausted providers skipped
- **Content extraction** — 9-step fallback chain with quality gates (local + external)
- **Multi-turn sessions** — pass `session_id` for conversational search refinement
- **4 search modes** — discovery, research, recovery, grounding
- **Dead URL recovery** — first-class `/recover-url` endpoint with archive fallbacks
- **4 integration paths** — HTTP API, CLI, MCP server, Python SDK

_Built for AI agent builders, RAG infra, and ops teams who don't want to hand-wire search APIs._

## Contents

- [Quickstart](#quickstart)
- [Providers](#providers)
- [HTTP API](#http-api)
- [Integration](#integration)
  - [CLI](#cli)
  - [MCP](#mcp)
  - [Python](#python)
- [Content Extraction](#content-extraction)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [FAQ](#faq)

## Quickstart

### Mode 1: Local CLI (zero config)

```bash
pip install argus-search && argus search -q "python web frameworks"
```

That's it. DuckDuckGo handles the search — no accounts, no keys, no containers. You get unlimited free search from your laptop right now. Add API keys whenever you want more providers, or don't.

```bash
argus extract -u "https://example.com/article"       # extract clean text from any URL
```

Works on any machine with Python 3.11+ — laptop, Mac Mini, Raspberry Pi, cloud VM. Nothing to host.

**For MCP (Claude Code, Cursor, VS Code):**

```bash
pipx install argus-search[mcp] && argus mcp serve
```

Then add to your MCP config:

```json
{"mcpServers": {"argus": {"command": "argus", "args": ["mcp", "serve"]}}}
```

Or install from the [MCP Registry](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus):

```json
{
  "mcpServers": {
    "argus": {
      "registryType": "pypi",
      "identifier": "argus-search",
      "runtimeHint": "uvx"
    }
  }
}
```

One command to install, one JSON block to connect. No server to run, no keys to configure.

### Mode 2: Full Stack Server

Got a Raspberry Pi running Pi-hole? A Mac Mini on your desk? An old laptop? That's enough to run the full stack — SearXNG (your own private search engine) plus local JS-rendering content extraction.

```bash
docker compose up -d    # SearXNG + Argus
```

| What you have | What you get |
|--------------|-------------|
| **Any machine with Python 3.11+** | DuckDuckGo + API providers (no server) |
| **Raspberry Pi 4 / old laptop** (4GB+) | Everything — SearXNG, all providers, Crawl4AI |
| **Mac Mini M1+** (8GB+) | Full stack with headroom |
| **Free cloud VM** (1GB) | SearXNG + search providers (skip Crawl4AI) |

SearXNG takes 512MB of RAM and gives you a private Google-style search engine that nobody can rate-limit, block, or charge for. It runs alongside Pi-hole on hardware millions of people already own.

## Providers

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

**5,000 free queries/month** from the four recurring providers. Three providers need no API key at all. Routing priority: **Tier 0** (free: SearXNG, DuckDuckGo, GitHub) → **Tier 1** (monthly: Brave, Tavily, Exa, Linkup) → **Tier 2** (one-time: Serper, Parallel, You.com, Valyu, SearchAPI). Budget-exhausted providers are skipped automatically.

## HTTP API

All endpoints prefixed with `/api`. OpenAPI docs at `http://localhost:8000/docs`.

```bash
# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "mode": "discovery", "max_results": 5}'

# Multi-turn search (conversational refinement)
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "what about async?", "session_id": "my-session"}'

# Extract content from a working URL
curl -X POST http://localhost:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'

# Recover a dead or moved URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Health & budgets
curl http://localhost:8000/api/health/detail
curl http://localhost:8000/api/budgets
```

#### Search modes

| Mode | Use for | Example |
|------|---------|---------|
| `discovery` | Related pages, canonical sources | "Find the official docs for X" |
| `research` | Broad exploratory retrieval | "Latest approaches to Y?" |
| `recovery` | Finding moved/dead content | "This URL is 404" |
| `grounding` | Fact-checking with live sources | "Verify this claim about Z" |

Tier-based routing always applies first. Within each tier, the mode selects provider order.

#### Response format

```json
{
  "query": "python web frameworks",
  "mode": "discovery",
  "results": [
    {"url": "https://fastapi.tiangolo.com", "title": "FastAPI", "snippet": "Modern Python web framework", "score": 0.942}
  ],
  "total_results": 1,
  "cached": false,
  "traces": [
    {"provider": "duckduckgo", "status": "success", "results_count": 5, "latency_ms": 312}
  ]
}
```

Each result includes `url`, `title`, `snippet`, `domain`, `provider`, and `score`. The `traces` array shows which providers were called and their outcomes.

#### Budgets

```json
{
  "budgets": {
    "brave": {"remaining": 1847, "monthly_usage": 153, "usage_count": 153, "exhausted": false},
    "duckduckgo": {"remaining": 0, "monthly_usage": 0, "usage_count": 42, "exhausted": false}
  },
  "token_balances": {"jina": 9833638}
}
```

Each provider tracks usage per calendar month. When a provider hits its budget, Argus skips it and moves to the next tier. Free providers (SearXNG, DuckDuckGo, GitHub) have no limit. Set `ARGUS_*_MONTHLY_BUDGET_USD` to enforce custom limits per provider.

## Integration

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

<details>
<summary>How sessions work</summary>

Pass `session_id` to any search call. Argus stores each query and extracted URL in a SQLite-backed session. Reusing the same `session_id` gives the broker context from prior queries — follow-up searches are automatically refined using earlier conversation context. Sessions persist across restarts. Omit `session_id` for stateless, one-shot searches.

</details>

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

## Content Extraction

Argus tries up to nine methods to extract content from any URL: first local (trafilatura, Crawl4AI, Playwright), then external APIs (Jina, Valyu Contents, Firecrawl, You.com, Wayback, archive.is). Each attempt is quality-checked for garbage output. See [docs/providers.md](docs/providers.md) for the full extractor comparison.

**Extract** gets the full text of a working URL. **Recover-URL** finds alternatives when a URL is dead, paywalled, or radically changed by querying archival sources (Wayback, archive.is) and running a question-guided extraction loop.

## Architecture

```
Caller (CLI/HTTP/MCP/Python) → SearchBroker → tier-sorted providers → RRF ranking → response
                                     ↕ SessionStore (optional)
                            Extractor (on demand) → 9-step fallback chain with quality gates
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

Add new providers or extractors with a single adapter file. See [CONTRIBUTING.md](CONTRIBUTING.md) for the interface.

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

## FAQ

**How is this different from calling Tavily/Serper directly?**
Argus calls them for you — plus 9 other providers. You get one ranked, deduplicated result set instead of managing multiple API keys and stitching results together. Free providers are tried first, so you only burn credits when needed.

**Can I run only one provider?**
Yes. Set only the API key for the provider you want. All others are silently skipped. For zero-config, just install and go — DuckDuckGo handles everything with no keys.

**Do I need Docker?**
No. `pip install argus-search` works immediately on any machine with Python 3.11+. Docker is only needed for SearXNG (self-hosted search) or Crawl4AI (local JS rendering).

## License

MIT — see [CHANGELOG.md](CHANGELOG.md) for release history.
