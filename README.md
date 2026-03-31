# Argus

Argus is a reusable, VPS-hosted search broker.

It provides one stable interface over multiple web-search providers, with a free/self-hosted-first policy and explicit health, budget, and fallback behavior.

## Goals

- Always-on search service
- Slow and reliable over fast and flaky
- Reusable across projects
- HTTP API first
- MCP optional, not core
- Free/self-hosted-first routing
- Clear provider health and budget tracking
- One normalized result schema for all callers

## Provider order

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

These must be config-driven.

## Core features

- Unified search broker
- Provider adapters
- Query/result normalization
- Ranking and dedupe
- Query caching
- Provider health tracking
- Provider budget tracking
- URL recovery mode
- Discovery mode
- Grounding mode
- Research mode
- HTTP API
- CLI
- Optional MCP wrapper

## Primary endpoints

- `POST /search`
- `POST /recover-url`
- `POST /expand`
- `GET /health`
- `GET /budgets`
- `POST /test-provider`

## Initial architecture

```text
Argus
├── argus/
│   ├── providers/
│   ├── broker/
│   ├── persistence/
│   ├── api/
│   ├── cli/
│   └── mcp/
├── tests/
├── docs/
└── .env.example
```

## Design rules

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

## Manual prerequisites

Before implementation:

1. Deploy SearXNG
2. Verify `format=json` works
3. Get Brave API key
4. Get Serper API key
5. Put Tavily and Exa keys in `.env`
6. Confirm PostgreSQL connection details
7. Decide initial monthly budgets

## Downstream consumers

Argus should be usable by:

* local Python code
* remote HTTP clients
* CLI workflows
* AI hosts through MCP
* other internal projects

## Non-goals for v1

* browser UI
* crawling framework
* massive distributed search
* provider-specific custom features exposed directly to clients
* full auth platform
* public multi-tenant service

## Success criteria for v1

* One reliable `POST /search`
* Search works even if one or two providers fail
* SearXNG works as local fallback floor
* Provider health is visible
* Budget exhaustion is visible
* Results are normalized
* URL recovery works
* Integration clients can call one interface instead of many
