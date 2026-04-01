# Argus

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![MCP Server](https://img.shields.io/badge/MCP-server-purple)](https://modelcontextprotocol.io/)
[![Zero external DB deps](https://img.shields.io/badge/DB-SQLite%20only-blue)](https://www.sqlite.org/)

One endpoint, five search providers. Argus routes queries across SearXNG, Brave, Serper, Tavily, and Exa with automatic fallback, RRF result ranking, health tracking, and budget enforcement. Extract clean text from any URL. Remember prior queries for smarter follow-ups. Zero external database dependencies — SQLite only.

Connect via HTTP, CLI, MCP, or Python import.

## Why Argus

Without Argus, every agent that needs web search has to wire up individual provider APIs, handle keys and rate limits for each one, write its own fallback logic, deduplicate results from multiple sources, and build its own content extraction pipeline. Each project reimplements the same glue.

Argus replaces that with one endpoint. You add it to your agent once — the same way you'd add a database client or an LLM API wrapper — and it handles the rest:

- **No provider lock-in** — swap Brave for Serper or add Exa without changing your agent code. Missing keys degrade gracefully; providers are skipped, not errors.
- **Automatic fallback** — if a provider is down, slow, or over budget, Argus routes to the next best one. Your agent doesn't need retry logic or circuit breakers.
- **Better results than any single provider** — Reciprocal Rank Fusion merges results from multiple sources. A URL that appears in both Brave and Serper ranks higher than one that only appears in one.
- **Content extraction built in** — found a useful link? Pass the URL to Argus and get clean article text back. Trafilatura tries first (local, free), Jina Reader falls back if needed. Cached in memory and SQLite so the same URL is never fetched twice.
- **Multi-turn memory** — Argus remembers prior queries in a session. Follow-up searches like "fastapi" after "python web frameworks" get context-enriched automatically.
- **Budget-aware by default** — each provider has a generous free tier (Brave: 2k/mo, Serper: 2.5k/mo, Tavily: 1k/mo, Exa: 1k/mo). Argus tracks usage per provider and automatically rotates away from one when its quota is hit. Combined, that's thousands of free searches per month — enough for most personal and development use.

Think of it as the LiteLLM of web search — one API, multiple providers, unified interface.

## What It Does

You pass Argus a search query. It routes to providers in cheap-first order, stops early when the first provider already produced enough useful results, and only falls through when failure, weak output, cooldown, or budget limits justify it. Results are ranked, deduplicated, and returned as one clean list.

**Content extraction** — Pass a URL and get clean article text back. Trafilatura (local, fast) tries first, Jina Reader falls back if needed. Results cached in memory and SQLite (168h TTL) — survives restarts.

**Multi-turn sessions** — Pass a `session_id` and Argus remembers what you've asked before. Follow-up queries get context-enriched automatically. Sessions persist to SQLite across restarts.

**Token balance tracking** — Track API credits (Jina, etc.) in SQLite. Balances auto-decrement on extraction. Set via CLI, view via API.

**API key auth** — Set `ARGUS_API_KEY` to require authentication on all endpoints (health exempt).

## Quick Start

### Docker (recommended)

```bash
# 1. Create .env with your provider keys
cp .env.example .env
# Edit .env — at minimum, set one provider API key

# 2. Start Argus
docker compose up -d

# 3. Verify
curl http://localhost:8005/api/health
# {"status":"ok"}

curl -X POST http://localhost:8005/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi tutorial", "mode": "discovery"}'
```

### Local install

```bash
git clone https://github.com/Khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
cp .env.example .env
pip install -e ".[mcp]"
argus serve
```

## Provider Setup

All you need is API keys for whichever providers you want. SearXNG is free and runs locally.

| Provider | Free tier | Get a key |
|----------|----------|-----------|
| [SearXNG](https://github.com/searxng/searxng) | Unlimited (self-hosted) | No key needed — runs in Docker |
| [Brave Search](https://brave.com/search/api/) | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| [Serper](https://serper.dev) | 2,500 queries/month | [signup](https://serper.dev/signup) |
| [Tavily](https://tavily.com) | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| [Exa](https://exa.ai) | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |

Set keys in `.env`:
```
ARGUS_BRAVE_API_KEY=BSA...
ARGUS_SERPER_API_KEY=abc...
ARGUS_TAVILY_API_KEY=tvly-...
ARGUS_EXA_API_KEY=...
```

Unset or blank keys are silently skipped. You can run Argus with just SearXNG and no paid keys at all.

See [docs/providers.md](docs/providers.md) for SearXNG tuning and Docker networking details.

## Integration

### HTTP API

All endpoints prefixed with `/api`. OpenAPI docs at `/docs`. Set `ARGUS_API_KEY` to require auth (health exempt).

```bash
# Search
curl -X POST http://localhost:8005/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "mode": "discovery", "max_results": 5}'

# Multi-turn search
curl -X POST http://localhost:8005/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "session_id": "my-session"}'

# Extract content from a URL
curl -X POST http://localhost:8005/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'

# Recover a dead URL
curl -X POST http://localhost:8005/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Expand a query with related links
curl -X POST http://localhost:8005/api/expand \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi", "context": "python web framework"}'

# Health & budgets
curl http://localhost:8005/api/health
curl http://localhost:8005/api/budgets
```

### CLI

```bash
argus search -q "python web framework"
argus search -q "python web framework" --mode research -n 20
argus search -q "fastapi" --session my-session
argus extract -u "https://example.com/article"
argus recover-url -u "https://dead.link" -t "Title"
argus health
argus budgets
argus set-balance -s jina -b 9833638
argus test-provider -p brave
argus serve
argus mcp serve
```

### MCP

**Claude Code** — add to your MCP settings:

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

Available tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`

### Python

```python
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode
from argus.extraction import extract_url

broker = create_broker()

response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY)
)
for r in response.results:
    print(f"{r.title}: {r.url} (score: {r.score:.3f})")

content = await extract_url(response.results[0].url)
print(content.text)
```

## Search Modes

| Mode | When to use | Provider chain |
|------|------------|---------------|
| `discovery` | Related pages, canonical sources | searxng → brave → exa → tavily → serper |
| `recovery` | Dead/moved URL recovery | searxng → brave → serper → tavily → exa |
| `grounding` | Fact-checking with few sources | brave → serper → searxng |
| `research` | Broad exploratory retrieval | tavily → exa → brave → serper |

## Architecture

```
Caller (CLI / HTTP / MCP / Python)
  → SearchBroker
    → routing policy (per mode)
    → provider executor (cheap-first, bounded fallback)
    → result pipeline (cache → dedupe → RRF ranking → response)
    → persistence gateway (SQLite, non-fatal)
  → SessionStore (optional, per-request)
  → ContentExtractor (on demand)
    → trafilatura (primary) → Jina Reader (fallback)
    → cache: memory → SQLite
```

| Module | Responsibility |
|--------|---------------|
| `argus/core/` | Generic TTLCache, SlidingWindowLimiter |
| `argus/broker/` | Routing, ranking, dedup, health, budgets |
| `argus/providers/` | Provider adapters (SearXNG, Brave, Serper, Tavily, Exa) |
| `argus/extraction/` | URL content extraction (trafilatura + Jina) |
| `argus/sessions/` | Multi-turn session store |
| `argus/api/` | FastAPI HTTP endpoints + auth + rate limiting |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | SQLite search history |

## Configuration

All config via environment variables. See `.env.example`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ARGUS_DB_URL` | `sqlite:///argus.db` | Search history database |
| `ARGUS_BUDGET_DB_PATH` | `argus_budgets.db` | Budgets, sessions, extraction cache |
| `ARGUS_API_KEY` | — | Require API key on all endpoints (health exempt) |
| `ARGUS_SEARXNG_BASE_URL` | `http://127.0.0.1:8080` | SearXNG endpoint |
| `ARGUS_BRAVE_API_KEY` | — | Brave Search API key |
| `ARGUS_SERPER_API_KEY` | — | Serper API key |
| `ARGUS_TAVILY_API_KEY` | — | Tavily API key |
| `ARGUS_EXA_API_KEY` | — | Exa API key |
| `ARGUS_CACHE_TTL_HOURS` | 168 | Result cache TTL |
| `ARGUS_JINA_API_KEY` | — | Jina Reader key (optional) |
| `ARGUS_EXTRACTION_CACHE_TTL_HOURS` | 168 | Extraction cache TTL |
| `ARGUS_RATE_LIMIT` | 60 | Requests per window per client IP |
| `ARGUS_RATE_LIMIT_WINDOW` | 60 | Rate limit window (seconds) |

## License

MIT
