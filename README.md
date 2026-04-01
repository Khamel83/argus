# Argus

Argus is a reusable, VPS-hosted search broker. It provides one stable interface over multiple web-search providers, with a free/self-hosted-first policy and explicit health, budget, and fallback behavior.

## Quick Start

**Prerequisites:** Python 3.12+, PostgreSQL. Optionally [SearXNG](https://github.com/searxng/searxng) for free local search.

```bash
# Install
git clone https://github.com/khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env — at minimum set ARGUS_DB_URL and enable providers you have keys for

# Run
argus serve
```

Verify it's working:

```bash
# Health check
curl http://localhost:8000/api/health
# {"status":"ok"}

# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks 2025", "mode": "discovery", "max_results": 3}'
```

## Integration Examples

### HTTP API

```bash
# Discovery search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi tutorial", "mode": "discovery", "max_results": 5}'

# Recover a dead URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Expand with related links
curl -X POST http://localhost:8000/api/expand \
  -H "Content-Type: application/json" \
  -d '{"query": "system design patterns"}'

# Check health and budgets
curl http://localhost:8000/api/health/detail
curl http://localhost:8000/api/budgets
```

All endpoints return JSON. See `docs/search-operations.md` for full API reference.

### CLI

```bash
# Search (discovery mode by default)
argus search -q "python web framework"
argus search -q "python web framework" --mode research -n 20
argus search -q "https://dead.link" --mode recovery

# JSON output
argus search -q "fastapi" --json

# Admin
argus health
argus budgets
argus test-provider -p brave
argus test-provider -p searxng -q "climate change"
```

### MCP (for Claude, Cursor, or any MCP client)

Add to your MCP config:

**Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "argus": {
      "command": "argus",
      "args": ["mcp", "serve"],
      "transport": "stdio"
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "argus": {
      "command": "/path/to/.venv/bin/argus",
      "args": ["mcp", "serve"]
    }
  }
}
```

**Remote MCP** (SSE transport):
```json
{
  "mcpServers": {
    "argus": {
      "url": "http://localhost:8001/mcp"
    }
  }
}
```

MCP tools available: `search_web`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`

### Python

```python
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode

broker = create_broker()

# Discovery search
response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY, max_results=10)
)

for result in response.results:
    print(f"{result.title}: {result.url} (score: {result.score:.3f})")

# Check traces (which providers returned what)
for trace in response.traces:
    print(f"  {trace.provider}: {trace.status} ({trace.results_count} results, {trace.latency_ms}ms)")
```

## Goals

- Always-on search service
- Slow and reliable over fast and flaky
- Reusable across projects
- HTTP API first
- MCP optional, not core
- Free/self-hosted-first routing
- Clear provider health and budget tracking
- One normalized result schema for all callers

## Provider Order

Default provider chain:

- SearXNG
- Brave
- Serper
- Tavily
- Exa

Initial policy:

- `recovery`: cache -> searxng -> brave -> serper -> tavily -> exa
- `discovery`: cache -> searxng -> brave -> exa -> tavily -> serper
- `grounding`: cache -> brave -> serper -> searxng
- `research`: cache -> tavily -> exa -> brave -> serper

These are config-driven.

## Core Features

- Unified search broker
- Provider adapters (normalize all responses to one schema)
- Ranking and deduplication (reciprocal rank fusion)
- Query caching (configurable TTL)
- Provider health tracking (auto-disable after repeated failures)
- Provider budget tracking (monthly spend limits)
- URL recovery mode
- Discovery mode
- Grounding mode
- Research mode
- HTTP API with OpenAPI docs at `/docs`
- CLI
- Optional MCP wrapper

## Primary Endpoints

- `POST /api/search`
- `POST /api/recover-url`
- `POST /api/expand`
- `GET /api/health`
- `GET /api/budgets`
- `POST /api/test-provider`

## Architecture

```
Argus
├── argus/
│   ├── providers/     # Provider adapters (SearXNG, Brave, Serper, Tavily, Exa)
│   ├── broker/        # Routing, ranking, caching, health, budgets
│   ├── persistence/   # PostgreSQL via SQLAlchemy
│   ├── api/           # FastAPI HTTP endpoints
│   ├── cli/           # Click CLI
│   ├── mcp/           # MCP server (stdio + SSE)
│   ├── models.py      # Core data models
│   └── config.py      # Environment-based configuration
├── tests/
├── docs/
└── .env.example
```

## Design Rules

1. Argus is the system of record for web-search routing logic.
2. Provider-specific response shapes must never leak outside adapters.
3. The broker owns routing, retries, fallback, budgets, and degradation.
4. The API layer must stay thin.
5. MCP is an optional wrapper around the core service, not the core itself.
6. Missing keys must degrade gracefully.
7. Provider exhaustion and repeated failure must be first-class states.
8. Query/result persistence belongs in Postgres.
9. SearXNG is the free local floor.
10. The default output must be normalized and auditable.

## Environment

Argus expects:

* PostgreSQL
* SearXNG reachable from the host
* API keys for Brave and Serper
* Existing Tavily and Exa keys if enabled

See `.env.example` for all configuration variables.

## Downstream Consumers

Argus is designed to be usable by any project:

* local Python code
* remote HTTP clients
* CLI workflows
* AI agents through MCP
* other internal projects

## Non-goals for v1

* browser UI
* crawling framework
* massive distributed search
* provider-specific custom features exposed directly to clients
* full auth platform
* public multi-tenant service

## Success Criteria for v1

* One reliable `POST /api/search`
* Search works even if one or two providers fail
* SearXNG works as local fallback floor
* Provider health is visible
* Budget exhaustion is visible
* Results are normalized
* URL recovery works
* Integration clients can call one interface instead of many

## License

MIT
