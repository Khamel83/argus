# Argus Operations Guide

## Starting the Service

### CLI

```bash
argus serve --host 127.0.0.1 --port 8000
```

### Docker

```bash
docker compose up -d
```

### Direct Python

```bash
uvicorn argus.api.main:app --host 127.0.0.1 --port 8000
```

## Environment Variables

See `.env.example` for all configuration options. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `ARGUS_ENV` | development | Environment (development/staging/production) |
| `ARGUS_DB_URL` | postgresql+psycopg://postgres:postgres@localhost:5432/argus | Database connection |
| `ARGUS_CACHE_TTL_HOURS` | 168 | Cache TTL (7 days default) |
| `ARGUS_DISABLE_PROVIDER_AFTER_FAILURES` | 5 | Failures before cooldown |
| `ARGUS_PROVIDER_COOLDOWN_MINUTES` | 60 | Cooldown duration |
| `ARGUS_SEARXNG_BASE_URL` | http://127.0.0.1:8080 | SearXNG endpoint |
| `ARGUS_BRAVE_API_KEY` | | Brave Search API key |
| `ARGUS_SERPER_API_KEY` | | Serper API key |
| `ARGUS_TAVILY_API_KEY` | | Tavily API key |
| `ARGUS_EXA_API_KEY` | | Exa API key |

## Database Setup

Argus uses PostgreSQL for persistence.

```bash
# Create database
createdb argus

# Tables are auto-created on first startup
# or manually:
python -c "from argus.persistence.db import init_db; init_db()"
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/search` | Execute a search query |
| POST | `/api/recover-url` | Recover a dead URL |
| POST | `/api/expand` | Expand a query with related links |
| GET | `/api/health` | Health check (`{"status": "ok"}`) |
| GET | `/api/health/detail` | Detailed provider status |
| GET | `/api/budgets` | Budget status for all providers |
| POST | `/api/test-provider` | Smoke-test a provider |

### POST /api/search

```bash
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web framework", "mode": "discovery", "max_results": 5}'
```

### POST /api/recover-url

```bash
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/dead-page", "title": "Example Page"}'
```

### GET /api/health

```bash
curl http://localhost:8000/api/health
# {"status": "ok"}
```

### POST /api/test-provider

```bash
curl -X POST http://localhost:8000/api/test-provider \
  -H "Content-Type: application/json" \
  -d '{"provider": "searxng", "query": "test"}'
```

## CLI Usage

```bash
# Search
argus search -q "python web framework" -m discovery -n 5

# Recover URL
argus recover-url -u "https://example.com/dead-page" -t "Page Title"

# Health check
argus health

# Budget status
argus budgets

# Test provider
argus test-provider -p searxng

# Start server
argus serve --host 0.0.0.0 --port 8000
```

## MCP Server

```bash
# Start MCP server (stdio transport)
argus mcp serve

# Or with SSE transport
argus mcp serve --transport sse --port 8001
```

MCP tools available:
- `search_web` — Execute a search
- `recover_url` — Recover a dead URL
- `expand_links` — Discovery search with expansion
- `search_health` — Provider health status
- `search_budgets` — Budget status
- `test_provider` — Smoke-test a provider

## Monitoring

### Health Endpoint

The `/api/health` endpoint returns `{"status": "ok"}` if at least one provider is healthy, or `{"status": "degraded"}` otherwise.

### Request Correlation

Every response includes an `x-request-id` header for tracing.

### Logging

Set `ARGUS_LOG_LEVEL=DEBUG` for verbose output. Provider payloads can be logged with `ARGUS_LOG_PROVIDER_PAYLOADS=true`. Full results with `ARGUS_LOG_FULL_RESULTS=true`.

## Graceful Degradation

Argus degrades gracefully:
1. Missing API keys → provider skipped (status: `unavailable_missing_key`)
2. Consecutive failures → cooldown (status: `temporarily_disabled`)
3. Budget exhausted → provider skipped (status: `budget_exhausted`)
4. All providers down → returns empty results with traces showing what failed

The SearXNG local provider acts as a free fallback floor — as long as it's running, Argus can always search.
