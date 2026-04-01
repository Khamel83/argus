# Project: Argus Hardening — Stable Search Substrate

## Goal
Transform Argus from over-engineered prototype into a durable personal search broker. One clean core, one database, one deployment story. Collapsed internal inconsistency, hardened for production.

## Done When
- Single SQLite database for all persistence (budgets, sessions, search history, cache)
- SQLAlchemy + psycopg2 dependencies removed
- Duplicate cache and rate limiter consolidated
- Global mutable state fixed
- Docker deployment working with health checks
- Tests pass
- MCP server available in Claude Code
- HTTP API functional
- Stub providers removed or implemented
- Service is "set and forget" on oci-dev

## In Scope
- Collapse PostgreSQL persistence to SQLite
- Consolidate duplicate cache/rate limiter implementations
- Fix global mutable state
- Add WAL mode for Docker SQLite
- Remove stub providers (SearchAPI, You.com)
- Hardening (auth, graceful shutdown, structured logging, persistent cache)
- Docker deployment cleanup
- Test coverage expansion

## Out of Scope
- New features or providers
- Adding Firecrawl/Browserless to extraction chain (deferred)
- Session refinement improvement (deferred)
- Public release or marketing
- systemd deployment (Docker only)

## Constraints
- Single-user, Docker on oci-dev
- Must work with Claude Code MCP
- Python 3.12, FastAPI, uvicorn
- No new heavy dependencies
- Keep all active providers (SearXNG, Brave, Serper, Tavily, Exa)

## Riskiest Part
- PostgreSQL → SQLite migration (low risk — gateway is non-fatal, data is analytics-only)
- Docker SQLite volume mount reliability (mitigated with WAL mode)
- Potential breakage during refactor of global state

## Status
PLAN
<!-- change to COMPLETE when all tasks pass verify + challenge -->
