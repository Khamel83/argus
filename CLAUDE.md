# Project Instructions

## Overview

Search broker with content extraction and multi-turn sessions. Seven provider adapters (SearXNG, Brave, Serper, Tavily, Exa active; SearchAPI, You.com stubs). Automatic fallback, result ranking, health tracking, budget enforcement, token balance tracking with auto-decrement. Extract clean text from any URL — results cached in memory (168h TTL). Domain rate limiting (10 req/min/domain). Persistent sessions (SQLite). Connect via HTTP, CLI, MCP, or Python import.

## Key Commands

```bash
# Setup
cp .env.example .env          # configure providers and DB
pip install -e ".[mcp]"       # install with MCP support

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
    → routing policy (per mode)
      → providers (parallel, with fallback)
    → cache → dedupe → RRF ranking → response
  → SessionStore (optional, per-request)
    → query refinement from prior context
  → Extractor (on demand)
    → trafilatura (primary) → Jina Reader (fallback)
```

| Module | Responsibility |
|--------|---------------|
| `argus/broker/` | Routing, ranking, dedup, caching, health, budgets |
| `argus/providers/` | Provider adapters (one per search API) |
| `argus/extraction/` | URL content extraction (trafilatura + Jina) |
| `argus/sessions/` | Multi-turn session store and query refinement |
| `argus/api/` | FastAPI HTTP endpoints |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | PostgreSQL query/result storage |

## Interfaces

| Interface | How to use |
|-----------|-----------|
| HTTP API | `POST /api/search`, `POST /api/extract`, `POST /api/recover-url`, `POST /api/expand` — OpenAPI at `/docs` |
| CLI | `argus search`, `argus extract`, `argus recover-url`, `argus health`, `argus budgets`, `argus set-balance` |
| MCP | `argus mcp serve` — tools: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider` |
| Python | `from argus.broker.router import create_broker`, `from argus.extraction import extract_url` |

## Search Modes

| Mode | Use case | Chain |
|------|----------|-------|
| `discovery` | Related pages, canonical sources | searxng → brave → exa → tavily → serper |
| `recovery` | Dead/moved URL | searxng → brave → serper → tavily → exa |
| `grounding` | Few sources for fact-checking | brave → serper → searxng |
| `research` | Broad exploratory | tavily → exa → brave → serper |

## Content Extraction

Hybrid approach: trafilatura (local, fast, runs in thread pool) tries first, Jina Reader API (external) as fallback. Returns clean text with title, author, date, word count. SSRF protection blocks private IPs. Results cached in memory (168h TTL). Domain rate limiting (10 req/min/domain). Jina token balance auto-decrements on use.

```bash
# CLI
argus extract -u "https://example.com/article"

# HTTP
curl -X POST http://localhost:8000/api/extract -H "Content-Type: application/json" -d '{"url": "https://example.com/article"}'

# Python
from argus.extraction import extract_url
result = await extract_url("https://example.com/article")
print(result.text)
```

## Multi-Turn Sessions

Pass `session_id` to search to enable conversational refinement. The broker remembers prior queries and uses them to context-enrich follow-up searches. Sessions persist to SQLite (`argus_budgets.db`) across restarts.

```bash
# CLI
argus search -q "python web frameworks" --session mysession
argus search -q "fastapi" --session mysession  # refined by prior context

# HTTP
curl -X POST http://localhost:8000/api/search -H "Content-Type: application/json" \
  -d '{"query": "fastapi", "session_id": "mysession"}'
```

## Configuration

All config via env vars (see `.env.example`). Missing API keys degrade gracefully — providers are skipped, not errors.

## Conventions

- Provider adapters must never leak provider-specific shapes outside `argus/providers/`
- All search results are `SearchResult`: url, title, snippet, domain, provider, score
- Extracted content is `ExtractedContent`: url, title, text, author, date, word_count
- Routes prefixed with `/api`
- Free/self-hosted-first: SearXNG is always the fallback floor
- Token balances persist in SQLite (`argus_budgets.db`) alongside budget tracking
