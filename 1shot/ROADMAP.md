# Argus Hardening Roadmap

## Context
See `ANALYSIS.md` for the full evaluation. This is the execution plan.
See `PROJECT.md` for scope, constraints, and acceptance criteria.

## Phase 1: Foundation (collapse inconsistency)
> Success: Single SQLite database, one cache, one rate limiter, no stubs

### Task #1: Consolidate duplicate caches into generic TTLCache [UNBLOCKED]
Create `argus/core/cache.py` with generic `TTLCache`. Replace `broker/cache.py` and `extraction/cache.py`.
- New: `argus/core/__init__.py`, `argus/core/cache.py`
- Delete: `argus/broker/cache.py`, `argus/extraction/cache.py`
- Update: `broker/router.py`, `broker/pipeline.py`, `extraction/extractor.py`

### Task #6: Remove stub providers (SearchAPI, You.com) [UNBLOCKED]
Delete dead code. Clean all references.
- Delete: `argus/providers/searchapi.py`, `argus/providers/you.py`
- Update: `argus/providers/__init__.py`, `config.py`, `broker/router.py`

### Task #7: Consolidate duplicate rate limiters into generic RateLimiter [UNBLOCKED]
Create `argus/core/rate_limit.py` with generic sliding-window `RateLimiter`.
- New: `argus/core/rate_limit.py`
- Delete: `argus/extraction/rate_limit.py`, `argus/api/rate_limit.py`
- Update: `extraction/extractor.py`, `api/main.py`

### Task #3: Add basic API key auth to HTTP endpoints [UNBLOCKED]
API key from `ARGUS_API_KEY` env var. If set, require `X-API-Key` header. Health endpoint exempt.
- Update: `api/main.py`, `config.py`

## Phase 2: Unify and harden
> Success: One database, persistent cache, clean state, proper shutdown

### Task #10: Unify persistence to SQLite [BLOCKED by #1, #7, #6]
Rewrite `persistence/db.py` to raw sqlite3. Remove SQLAlchemy models. Add WAL mode.
- Rewrite: `argus/persistence/db.py`
- Delete: `argus/persistence/models.py`
- Update: `argus/persistence/__init__.py`, `pyproject.toml` (remove sqlalchemy, psycopg2-binary)
- Update: `broker/router.py`, `broker/pipeline.py`

### Task #2: Persistent SQLite-backed extraction cache [BLOCKED by #1]
Add `extraction_cache` table to `argus_budgets.db`. Check SQLite first, in-memory for hot data.
- Update: `extraction/extractor.py`

### Task #5: Fix global mutable state [BLOCKED by #1, #7, #10]
Move Jina counters to Extractor instance. Inject cache via SearchBroker constructor.
- Update: `config.py`, `extraction/extractor.py`, `broker/router.py`

### Task #11: Graceful shutdown [BLOCKED by #10]
FastAPI lifespan: close SQLite connections, flush state, log shutdown.
- Update: `api/main.py`, `broker/budget_persistence.py`, `sessions/persistence.py`

## Phase 3: Ship
> Success: Docker running, health checks passing, tests green

### Task #8: Docker cleanup [BLOCKED by #10]
Simplify Dockerfile (no PostgreSQL). Update docker-compose.yml (SQLite WAL volume, env vars).
- Update: `Dockerfile`, `docker-compose.yml`

### Task #4: Expand test coverage [BLOCKED by all above]
Tests for: unified cache, unified rate limiter, SQLite persistence, API auth, shutdown, extraction cache.
- Update: `tests/test_broker.py`, `tests/test_extraction.py`, `tests/test_api.py`

### Task #9: Verify and adversarial challenge [BLOCKED by #4]
Run pytest. Run docker compose up. Adversarial review of all changes.
- Acceptance: tests green, Docker starts, health 200, search returns results, MCP works.

## Dependency Graph
```
#1 ──┬── #10 ──┬── #11
     │         ├── #8
     │         └── #5
     └── #2
#6 ────── #10
#7 ──┬── #10
     └── #5
#3 ────── #4
All ───── #4 ── #9
```

## Parallel Start
Tasks #1, #6, #7, #3 can execute simultaneously.
