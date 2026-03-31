# Argus — Full Implementation Plan

## Overview

Argus is a reusable, VPS-hosted search broker. It provides one stable interface over multiple web-search providers, with a free/self-hosted-first policy and explicit health, budget, and fallback behavior.

The old Argus (semantic search extracted from Atlas) is replaced by this new architecture: a search **broker** that routes queries across SearXNG, Brave, Serper, Tavily, and Exa.

## Phase 0: Infrastructure

### Deploy SearXNG

```bash
mkdir -p ~/services/searxng/core-config
cd ~/services/searxng
curl -fsSLO \
  https://raw.githubusercontent.com/searxng/searxng/master/container/docker-compose.yml \
  https://raw.githubusercontent.com/searxng/searxng/master/container/.env.example
mv .env.example .env
docker compose up -d
```

Settings at `~/services/searxng/core-config/settings.yml`:

```yaml
use_default_settings: true

server:
  secret_key: "CHANGE_THIS_TO_A_LONG_RANDOM_STRING"
  bind_address: "0.0.0.0"
  port: 8080

search:
  formats:
    - html
    - json

general:
  instance_name: "Argus SearXNG"

engines:
  - name: brave
    disabled: true
  - name: google
    disabled: true
  - name: bing
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: wikipedia
    disabled: false
  - name: qwant
    disabled: false
```

Verify: `curl 'http://127.0.0.1:8080/search?q=argus&format=json'`

## Phase 1: Project Files

Create these files in the Argus repo:
- `README.md`
- `IMPLEMENTATION_CONTEXT.md`
- `TASK01.md` — Bootstrap core skeleton and config
- `TASK02.md` — Implement providers, broker, ranking, caching, persistence
- `TASK03.md` — Build HTTP API, CLI, optional MCP wrapper
- `.env.example`
- `docs/searxng-setup.md`

## Phase 2: TASK01 — Bootstrap Argus core skeleton and config

### Deliverables
- `argus/__init__.py`
- `argus/config.py`
- `argus/models.py`
- `argus/logging.py`
- Directories: `argus/providers/`, `argus/broker/`, `argus/persistence/`, `argus/api/`, `argus/cli/`, `argus/mcp/`, `tests/`, `docs/`
- `pyproject.toml`

### Config requirements
- DB URL
- Provider enable/disable flags
- Provider API keys and monthly budgets
- Cache TTL, failure thresholds, cooldown minutes
- SearXNG base URL
- Logging level
- MCP toggle

### Models
- SearchMode, ProviderName, ProviderStatus
- SearchQuery, SearchResult, ProviderTrace, SearchResponse

### Acceptance criteria
- Project installs
- Config loads
- Enums/models exist
- Tests can import package

## Phase 3: TASK02 — Providers, broker, ranking, caching, persistence

### Providers (implement)
- SearXNG, Brave, Serper, Tavily, Exa

### Providers (stub)
- SearchApi, You

### Broker components
- `argus/broker/policies.py` — routing policies (config-driven)
- `argus/broker/router.py` — main broker logic
- `argus/broker/ranking.py` — RRF or equivalent
- `argus/broker/dedupe.py` — URL normalization + dedupe
- `argus/broker/cache.py` — query caching
- `argus/broker/budgets.py` — budget tracking
- `argus/broker/health.py` — provider health tracking

### Persistence
- Postgres tables: search_queries, search_runs, search_results, provider_usage, search_evidence

### Routing policies (default)
- `recovery`: cache -> searxng -> brave -> serper -> tavily -> exa
- `discovery`: cache -> searxng -> brave -> exa -> tavily -> serper
- `grounding`: cache -> brave -> serper -> searxng
- `research`: cache -> tavily -> exa -> brave -> serper

### Acceptance criteria
- Broker executes with SearXNG only
- Degrades gracefully if keys missing
- Ranked normalized results returned
- DB rows written
- Cache works
- Provider status inspectable

## Phase 4: TASK03 — HTTP API, CLI, MCP

### HTTP endpoints
- `POST /search`
- `POST /recover-url`
- `POST /expand`
- `GET /health`
- `GET /budgets`
- `POST /test-provider`

### CLI commands
- `argus search --query "..." --mode discovery`
- `argus recover-url --url "..."`
- `argus health`
- `argus budgets`
- `argus test-provider --provider searxng --query "argus"`

### MCP (optional wrapper)
- Tools: search_web, recover_url, expand_links, search_health, search_budgets, test_provider
- Resources: argus://providers/status, argus://providers/budgets, argus://policies/current

### Ops
- Dockerfile
- docker-compose.yml
- Healthcheck endpoint

## Architecture

```text
Argus
├── argus/
│   ├── providers/    # Provider adapters (SearXNG, Brave, Serper, Tavily, Exa)
│   ├── broker/       # Routing, ranking, dedupe, cache, health, budgets
│   ├── persistence/  # Postgres models and migrations
│   ├── api/          # FastAPI HTTP endpoints
│   ├── cli/          # Click CLI
│   └── mcp/          # Optional MCP wrapper
├── tests/
├── docs/
└── .env.example
```

## Design rules

1. Argus is the system of record for web-search routing logic
2. Provider-specific response shapes must never leak outside adapters
3. The broker owns routing, retries, fallback, budgets, and degradation
4. API layer must stay thin
5. MCP is optional wrapper, not core
6. Missing keys degrade gracefully
7. Provider exhaustion is a first-class state
8. Persistence belongs in Postgres
9. SearXNG is the free local floor
10. Default output must be normalized and auditable

## Non-goals for v1

- Browser UI
- Crawling framework
- Massive distributed search
- Provider-specific features exposed to clients
- Full auth platform
- Public multi-tenant service

## Success criteria for v1

- One reliable `POST /search`
- Search works even if one or two providers fail
- SearXNG works as local fallback floor
- Provider health is visible
- Budget exhaustion is visible
- Results are normalized
- URL recovery works
- Integration clients can call one interface instead of many
