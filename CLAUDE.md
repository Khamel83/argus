# Project Instructions

## Overview

Search broker. One endpoint, five providers (SearXNG, Brave, Serper, Tavily, Exa). Automatic fallback, result ranking, health tracking, budget enforcement. Connect via HTTP, CLI, MCP, or Python import.

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
argus search -q "https://dead.link" --mode recovery

# Admin
argus health                  # provider status
argus budgets                 # budget status
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
```

Provider adapters (`argus/providers/`) hide all provider-specific logic. The broker (`argus/broker/`) owns routing, retries, budgets, degradation. The API layer (`argus/api/`) is thin.

## Interfaces

| Interface | How to use |
|-----------|-----------|
| HTTP API | `POST http://localhost:8000/api/search` — OpenAPI docs at `/docs` |
| CLI | `argus search -q "query"` |
| MCP | `argus mcp serve` — tools: `search_web`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider` |
| Python | `from argus.broker.router import create_broker` |

## Search Modes

| Mode | Use case | Chain |
|------|----------|-------|
| `discovery` | Related pages, canonical sources | searxng → brave → exa → tavily → serper |
| `recovery` | Dead/moved URL | searxng → brave → serper → tavily → exa |
| `grounding` | Few sources for fact-checking | brave → serper → searxng |
| `research` | Broad exploratory | tavily → exa → brave → serper |

## Configuration

All config via env vars (see `.env.example`). Missing API keys degrade gracefully — providers are skipped, not errors.

## Conventions

- Provider adapters must never leak provider-specific shapes outside `argus/providers/`
- All results are `SearchResult`: url, title, snippet, domain, provider, score
- Routes prefixed with `/api`
- Free/self-hosted-first: SearXNG is always the fallback floor
