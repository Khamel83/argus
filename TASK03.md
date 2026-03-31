# TASK03 — Build HTTP API, CLI, optional MCP wrapper, and integration-ready ops layer

## Goal

Expose Argus cleanly so any downstream project can use it.

## Scope

Build the HTTP API, CLI, and optional MCP wrapper on top of the existing core broker. Do not move business logic into the API or MCP layer.

## Required files

### API
- `argus/api/main.py`
- `argus/api/routes_search.py`
- `argus/api/routes_health.py`
- `argus/api/routes_admin.py`
- `argus/api/schemas.py`

### CLI
- `argus/cli/main.py`

### MCP
- `argus/mcp/server.py`
- `argus/mcp/tools.py`
- `argus/mcp/resources.py`

### Docs
- `docs/search-architecture.md`
- `docs/search-operations.md`
- `docs/searxng-setup.md`

## Required HTTP endpoints

- `POST /search` — query, mode, max_results, optional provider constraints
- `POST /recover-url` — url, optional title/domain hint
- `POST /expand` — query, optional context object
- `GET /health` — provider statuses, last success/failure, degraded state, missing key state
- `GET /budgets` — provider usage, estimated spend, exhausted status
- `POST /test-provider` — provider-specific smoke test result

## CLI requirements

- `argus search --query "..." --mode discovery`
- `argus recover-url --url "..."`
- `argus health`
- `argus budgets`
- `argus test-provider --provider searxng --query "argus"`

## MCP requirements

Tools: search_web, recover_url, expand_links, search_health, search_budgets, test_provider
Resources: argus://providers/status, argus://providers/budgets, argus://policies/current

## Operational requirements

- Dockerfile
- docker-compose for Argus service
- healthcheck endpoint
- no public auth system in v1

## Acceptance criteria

- API starts
- `/health` works
- `/search` works
- CLI can call broker
- MCP wrapper can call broker
- docs explain setup and usage
