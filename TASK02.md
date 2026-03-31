# TASK02 — Implement providers, broker, ranking, caching, and persistence

## Goal

Build the actual Argus search engine core.

## Scope

Implement provider adapters, the broker, ranking/dedupe, query caching, and Postgres persistence.

## Providers for this task

Implement:
- SearXNG
- Brave
- Serper
- Tavily
- Exa

Stub only:
- SearchApi
- You

## Required files

### Providers
- `argus/providers/base.py`
- `argus/providers/searxng.py`
- `argus/providers/brave.py`
- `argus/providers/serper.py`
- `argus/providers/tavily.py`
- `argus/providers/exa.py`
- `argus/providers/searchapi.py`
- `argus/providers/you.py`

### Broker
- `argus/broker/policies.py`
- `argus/broker/router.py`
- `argus/broker/ranking.py`
- `argus/broker/dedupe.py`
- `argus/broker/cache.py`
- `argus/broker/budgets.py`
- `argus/broker/health.py`

### Persistence
- `argus/persistence/db.py`
- `argus/persistence/models.py`
- migration files for: search_queries, search_runs, search_results, provider_usage, search_evidence

## Functional requirements

### Provider adapter contract
Each provider adapter must:
- expose provider name
- report availability
- validate config
- execute a search query
- normalize raw results into Argus SearchResult
- return provider trace metadata

### Broker
The broker must:
- accept query + mode
- route based on mode
- skip unavailable providers
- stop early when mode success criteria are met
- persist query/run/result metadata
- merge provider results
- dedupe and rank final output
- return normalized response with provider trace

### Policies (config-driven)
- `recovery`: cache -> searxng -> brave -> serper -> tavily -> exa
- `discovery`: cache -> searxng -> brave -> exa -> tavily -> serper
- `grounding`: cache -> brave -> serper -> searxng
- `research`: cache -> tavily -> exa -> brave -> serper

### Cache
- key by normalized query + mode
- TTL configurable
- usable before live provider hits

### Health tracking
- last success, last failure, consecutive failures
- degraded/unavailable state, cooldown windows

### Budget tracking
- usage count, estimated spend, monthly totals, budget exhausted state

### Ranking and dedupe
- canonical URL normalization, domain extraction
- duplicate collapse, reciprocal-rank fusion, stable final ordering

## Acceptance criteria

- broker can execute with SearXNG only
- broker degrades if Brave/Serper/etc keys missing
- ranked normalized results returned
- DB rows written
- cache works
- provider status is inspectable
- tests exist for routing, normalization, dedupe, and health state
