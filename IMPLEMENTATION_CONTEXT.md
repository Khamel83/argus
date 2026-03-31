# Argus Implementation Context

This repository is a standalone search broker and shared search infrastructure service.

It is designed to support one primary internal knowledge/archive system first, but it must remain generic and reusable. Nothing in the core architecture should hardcode a single downstream project.

## Product definition

Argus is:

- a standalone service
- API-first
- broker-based
- provider-agnostic at the interface layer
- stateful about health, usage, budgets, and failures
- reusable by any project

## Main jobs

1. Web search
2. URL recovery
3. Discovery search
4. Grounding / research augmentation
5. Provider-health and budget visibility

## Core principle

All search providers must be hidden behind one broker.

No downstream project should ever need to know:
- provider URLs
- auth headers
- provider-specific response schemas
- retry logic
- budget logic
- fallback ordering

## Initial providers

Implemented in first wave:
- SearXNG
- Brave
- Serper
- Tavily
- Exa

Optional stubs:
- SearchApi
- You

## Modes

### recovery
Use when a URL is dead, moved, soft-404, or unavailable.

### discovery
Use when trying to find likely related pages, transcripts, or canonical sources.

### grounding
Use when a caller needs a few live web sources to support a question.

### research
Use when broader exploratory retrieval is acceptable.

## Persistence

Postgres should store:

- search_queries
- search_runs
- search_results
- provider_usage
- search_evidence

## Operational states per provider

Each provider should be one of:

- enabled
- disabled_by_config
- unavailable_missing_key
- temporarily_disabled_after_failures
- budget_exhausted
- degraded
- healthy

## Ranking requirements

Need:
- canonical URL normalization
- domain extraction
- result dedupe
- reciprocal-rank fusion or equivalent
- source preference rules

## Source preference rules

Default:
1. canonical publisher page
2. official transcript page
3. archive snapshot
4. mirror
5. scraped copy

## Interfaces

### required
- HTTP API
- Python importable client
- CLI

### optional
- MCP wrapper

## Downstream integration stance

Downstream systems should:
- call Argus through HTTP or Python client
- not reproduce broker logic
- not duplicate provider code
- not own budgets or fallback order
