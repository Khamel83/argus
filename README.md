# Argus

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-brightgreen)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![MCP Server](https://img.shields.io/badge/MCP-server-purple)](https://modelcontextprotocol.io/)

Stop wiring search APIs into every project. Argus is one endpoint that talks to multiple search providers — with automatic fallback, result ranking, health tracking, and budget enforcement. Connect via HTTP, CLI, MCP, or Python import. Add a provider key, it works. Remove it, it degrades gracefully.

**Search → Extract → Answer.** Argus doesn't just find URLs — it can fetch and extract clean text from any page, and it remembers your prior queries so follow-up searches get smarter.

## What It Does

You pass Argus a search query. It routes to providers in cheap-first order, stops early when the first provider already produced enough useful results, and only falls through when failure, weak output, cooldown, or budget limits justify it. Results are then ranked, deduplicated, and returned as one clean list. Your project never touches a provider API directly.

**Content extraction** — Found a useful link? Pass the URL to Argus and get clean article text back. It runs a 6-step fallback chain with quality gates between every step: trafilatura → Playwright → Jina Reader → Wayback Machine → archive.is. Each result is checked for paywall stubs, soft 404s, and minimum quality before moving on. SSRF protection blocks private IPs. Results are cached in memory (168h TTL). Authenticated extraction via cookies is supported for paywall domains (NYT, Bloomberg, etc.).

**Multi-turn sessions** — Pass a `session_id` with your searches and Argus remembers what you've asked before. Follow-up queries like "fastapi" after searching "python web frameworks" get context-enriched automatically. Sessions persist to SQLite across restarts.

**Token balance tracking** — Track remaining API credits (Jina, etc.) in a local SQLite database. Balances auto-decrement as you extract content. Set balances via CLI, view via API or `argus budgets`.

**Domain rate limiting** — Prevents hammering any single domain (default: 10 requests/minute/domain).

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
# {"status":"ok"}

curl -X POST http://localhost:8000/api/search \
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

All you need is API keys for whichever providers you want. SearXNG is free and runs locally. The rest have generous free tiers.

| Provider | Status | Free tier | Get a key |
|----------|--------|----------|-----------|
| [SearXNG](https://github.com/searxng/searxng) | Active | Unlimited (self-hosted) | No key needed — runs in Docker |
| [Brave Search](https://brave.com/search/api/) | Active | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| [Serper](https://serper.dev) | Active | 2,500 queries/month | [signup](https://serper.dev/signup) |
| [Tavily](https://tavily.com) | Active | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| [Exa](https://exa.ai) | Active | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |
| SearchAPI | Stub | — | Not yet configured |
| You.com | Stub | — | Not yet configured |

Set keys in `.env`:
```
ARGUS_BRAVE_API_KEY=BSA...
ARGUS_SERPER_API_KEY=abc...
ARGUS_TAVILY_API_KEY=tvly-...
ARGUS_EXA_API_KEY=...
```

Unset or blank keys are silently skipped. You can run Argus with just SearXNG and no paid keys at all.

### SearXNG Setup

The included `docker-compose.yml` starts SearXNG automatically. If running separately:

```bash
docker run -d --name searxng -p 8080:8080 searxng/searxng:latest
curl http://localhost:8080/search?q=test\&format=json
```

See [docs/providers.md](docs/providers.md) for SearXNG tuning and Docker networking details.

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

curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi vs django", "session_id": "my-session"}'
# ↑ second query is context-enriched by the first

# Extract content from a URL
curl -X POST http://localhost:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'
# → {"url": "...", "title": "...", "text": "clean article text...", "word_count": 842, "extractor": "trafilatura", "quality_passed": true}

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
# → {"budgets": {...}, "token_balances": {"jina": {"balance": 9833638.0, "updated_at": ...}}}
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

**Cursor** — add to `.cursor/mcp.json` in your project root (same JSON).

**VS Code** — add to your settings under `mcp.servers` (same JSON).

**SSE transport** (remote access):

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

Available tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`

### Python

```python
from argus.api.main import create_app
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode
from argus.extraction import extract_url

app = create_app()  # same composition path used by the default ASGI export
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

# Multi-turn search with session context
resp, session_id = await broker.search_with_session(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY)
)
resp2, _ = await broker.search_with_session(
    SearchQuery(query="fastapi vs django"),
    session_id=session_id,  # refined by prior query
)
```

## Search Modes

| Mode | When to use | Provider chain |
|------|------------|---------------|
| `discovery` | Find related pages, canonical sources | searxng → brave → exa → tavily → serper |
| `recovery` | Dead/moved URL recovery | searxng → brave → serper → tavily → exa |
| `grounding` | Few live sources for fact-checking | brave → serper → searxng |
| `research` | Broad exploratory retrieval | tavily → exa → brave → serper |

## Architecture

```
Caller (CLI / HTTP / MCP / Python)
  → FastAPI app factory / CLI / MCP entry points
    → SearchBroker
      → routing policy (per mode)
      → provider executor (cheap-first, bounded fallback)
      → result pipeline (cache → dedupe → RRF ranking → response)
      → persistence gateway
  → SessionStore (optional, per-request)
    → query refinement from prior context
  → Extractor (on demand)
    → SSRF check → cache → rate limit → auth (cookies) → quality gate →
      trafilatura → quality gate → Playwright → quality gate →
      Jina Reader → quality gate → Wayback Machine → quality gate →
      archive.is → quality gate → return best
```

| Module | Responsibility |
|--------|---------------|
| `argus/broker/` | Routing, ranking, dedup, caching, health, budgets |
| `argus/providers/` | Provider adapters (one per search API) |
| `argus/extraction/` | URL content extraction (6-step fallback chain with quality gates, auth, SSRF) |
| `argus/sessions/` | Multi-turn session store and query refinement |
| `argus/api/` | FastAPI HTTP endpoints |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | PostgreSQL query/result storage |

### Composition Notes

- `argus.api.main:create_app()` is the explicit FastAPI assembly path. The module-level `app` export is still available for `uvicorn argus.api.main:app`.
- `SearchBroker` is a thin coordinator around provider execution, result processing, session-aware search flow, and non-fatal persistence.
- Provider routing is sequential and cheap-first by default. Argus skips unavailable, cooled-down, or budget-exhausted providers and records those decisions in traces.

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
| `ARGUS_CACHE_TTL_HOURS` | 168 | Result cache TTL |
| `ARGUS_BUDGET_DB_PATH` | `argus_budgets.db` | SQLite path for budget/session persistence |
| `ARGUS_EXTRACTION_TIMEOUT_SECONDS` | 10 | URL fetch timeout for extraction |
| `ARGUS_EXTRACTION_CACHE_TTL_HOURS` | 168 | Extraction cache TTL (same URL) |
| `ARGUS_EXTRACTION_DOMAIN_RATE_LIMIT` | 10 | Max extractions per domain per window |
| `ARGUS_EXTRACTION_DOMAIN_WINDOW_SECONDS` | 60 | Domain rate limit window |
| `ARGUS_JINA_API_KEY` | — | Jina Reader key (optional — works without, rate-limited) |
| `ARGUS_REMOTE_EXTRACT_URL` | — | Auth extraction service URL (Mac Mini Playwright + cookies) |
| `ARGUS_REMOTE_EXTRACT_KEY` | — | Auth extraction service API key |
| `ARGUS_RATE_LIMIT` | 60 | Requests per window per client IP |
| `ARGUS_API_KEY` | — | Bypass rate limiting for internal services |

## Contributing

Bug reports, feature ideas, and PRs are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Roadmap

**Now** — Search broker with fallback, health, budgets, content extraction with 6-step fallback chain (trafilatura → Playwright → Jina → Wayback → archive.is), quality gates, SSRF protection, authenticated extraction via cookies, domain rate limiting, persistent sessions (SQLite), and auto-decrementing token balance tracking. Search → extract → answer, all in one service.

**Soon** — Provider-specific tuning (use Exa for academic, Brave for general), query rewriting to improve recall, embedding-based dedup, SQLite extraction cache persistence.

**Later** — Conversational search with automatic query chaining, result summarization, multi-language support.

## License

MIT
