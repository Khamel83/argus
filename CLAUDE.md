# Project Instructions

## Overview

Argus is a search broker. One HTTP endpoint over multiple web-search providers (SearXNG, Brave, Serper, Tavily, Exa). It normalizes results, handles fallbacks, tracks provider health and budgets, and exposes CLI/HTTP/MCP/Python interfaces.

## Key Commands

```bash
# Setup
cp .env.example .env          # configure providers and DB
pip install -e ".[dev]"       # install with dev deps

# Run
argus serve                   # start HTTP API on :8000
argus mcp serve               # start MCP server (stdio)
argus mcp serve --transport sse --port 8001  # MCP over SSE

# Search
argus search -q "python web framework" --mode discovery
argus search -q "https://dead.link" --mode recovery

# Admin
argus health                  # provider status
argus budgets                 # budget status
argus test-provider -p brave  # smoke test one provider

# Test
pytest tests/
```

## Architecture

```
Caller (CLI/HTTP/MCP/Python)
  → SearchBroker
    → routing policy (per mode)
      → providers (parallel, with fallback)
        → normalized SearchResult list
    → cache → dedupe → RRF ranking → response
```

Provider adapters (`argus/providers/`) hide all provider-specific logic. The broker (`argus/broker/`) owns routing, retries, budgets, and degradation. The API layer (`argus/api/`) is thin.

## Interfaces

| Interface | How to use |
|-----------|-----------|
| HTTP API | `POST http://localhost:8000/api/search` with JSON body |
| CLI | `argus search -q "query"` |
| MCP | Add to Claude/Cursor config (see README) |
| Python | `from argus.broker.router import create_broker` |

### HTTP API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/api/search` | Search with mode routing |
| POST | `/api/recover-url` | Recover a dead/moved URL |
| POST | `/api/expand` | Expand query with related links |
| GET | `/api/health` | Service health (ok/degraded) |
| GET | `/api/health/detail` | Per-provider health |
| GET | `/api/budgets` | Provider budget status |
| POST | `/api/test-provider` | Smoke test a provider |

### MCP Tools

`search_web`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`

### Search Modes

| Mode | Use case | Provider chain |
|------|----------|---------------|
| `discovery` | Find related pages, canonical sources | searxng → brave → exa → tavily → serper |
| `recovery` | Dead/moved URL recovery | searxng → brave → serper → tavily → exa |
| `grounding` | Few live sources for fact-checking | brave → serper → searxng |
| `research` | Broad exploratory retrieval | tavily → exa → brave → serper |

## Configuration

All config via environment variables (see `.env.example`). Key vars:

- `ARGUS_DB_URL` — PostgreSQL connection string
- `ARGUS_SEARXNG_BASE_URL` — local SearXNG instance
- `ARGUS_BRAVE_API_KEY`, `ARGUS_SERPER_API_KEY` — provider keys
- `ARGUS_CACHE_TTL_HOURS` — result cache TTL (default: 168)
- `ARGUS_DISABLE_PROVIDER_AFTER_FAILURES` — failure threshold (default: 5)

Missing API keys degrade gracefully — providers are skipped, not errors.

## Conventions

- Provider adapters must never leak provider-specific response shapes outside `argus/providers/`
- All results are `SearchResult` objects with url, title, snippet, domain, provider, score
- Routes are prefixed with `/api`
- Provider health has explicit states: enabled, disabled_by_config, unavailable_missing_key, temporarily_disabled_after_failures, budget_exhausted
- Free/self-hosted-first: SearXNG is always the fallback floor
