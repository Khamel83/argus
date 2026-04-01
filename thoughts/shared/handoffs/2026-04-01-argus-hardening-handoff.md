# Handoff: Argus Hardening — Stable Search Substrate

**Created**: 2026-04-01 15:02
**Branch**: `codex/architectural-refactor-plan-20260331`

## Quick Summary
Completed full evaluation of Argus (competitor landscape, MCP analysis, codebase audit). Verdict: KEEP — "stable search substrate, not a miniature startup." Created hardening plan with 11 tasks. No code changes yet. Ready to execute Phase 1.

## What's Done
- [x] Competitor research — no direct Python competitor for multi-provider search broker
- [x] MCP vs HTTP vs native analysis — MCP is distribution, not moat; keep both interfaces
- [x] Codebase audit — 10% actual logic, 90% infrastructure tax; core is sound
- [x] Analysis v2 written incorporating external feedback (`1shot/ANALYSIS.md`)
- [x] Hardening plan with 11 tasks, dependencies mapped (`1shot/ROADMAP.md`)
- [x] Project scope and constraints documented (`1shot/PROJECT.md`)

## In Progress
Nothing — plan is complete, execution not started.

## Not Started (task list in priority order)
- [ ] **#1** Consolidate duplicate caches into generic TTLCache [UNBLOCKED]
- [ ] **#6** Remove stub providers (SearchAPI, You.com) [UNBLOCKED]
- [ ] **#7** Consolidate duplicate rate limiters into generic RateLimiter [UNBLOCKED]
- [ ] **#3** Add basic API key auth to HTTP endpoints [UNBLOCKED]
- [ ] **#10** Unify persistence to SQLite — remove SQLAlchemy/PostgreSQL [blocked by #1, #7, #6]
- [ ] **#2** Persistent SQLite-backed extraction cache [blocked by #1]
- [ ] **#5** Fix global mutable state [blocked by #1, #7, #10]
- [ ] **#11** Graceful shutdown [blocked by #10]
- [ ] **#8** Docker cleanup [blocked by #10]
- [ ] **#4** Expand test coverage [blocked by all above]
- [ ] **#9** Verify and adversarial challenge [blocked by #4]

## Active Files
- `1shot/ANALYSIS.md` — full evaluation (competitors, MCP analysis, recommendation)
- `1shot/PROJECT.md` — scope, constraints, acceptance criteria
- `1shot/ROADMAP.md` — phase plan, task definitions with file lists, dependency graph
- `1shot/STATE.md` — current status, key decisions, user context
- `argus/broker/cache.py` — to be deleted/replaced by `argus/core/cache.py`
- `argus/extraction/cache.py` — to be deleted/replaced by `argus/core/cache.py`
- `argus/extraction/rate_limit.py` — to be deleted/replaced by `argus/core/rate_limit.py`
- `argus/api/rate_limit.py` — to be deleted/replaced by `argus/core/rate_limit.py`
- `argus/providers/searchapi.py` — stub, to be deleted
- `argus/providers/you.py` — stub, to be deleted
- `argus/persistence/db.py` — rewrite from SQLAlchemy to raw sqlite3
- `argus/persistence/models.py` — ORM models, to be deleted

## Key Decisions Made
1. **KEEP Argus** — unique niche, no competitor, core is solid | Rationale: full analysis in ANALYSIS.md
2. **Collapse PostgreSQL to SQLite** — gateway is non-fatal, data is analytics-only | Rationale: single-user Docker deployment, simplify deps
3. **Keep all active providers** (SearXNG, Brave, Serper, Tavily, Exa) — user wants to ADD more, not remove | Rationale: provider abstraction is the real value
4. **Docker-only deployment** — no systemd | Rationale: user confirmed
5. **MCP is distribution, not moat** — broker policy layer is the real product | Rationale: v2 feedback correction
6. **SQLite WAL mode** — for Docker volume reliability | Rationale: prevent filesystem issues
7. **Keep sessions + budget tracking** — coherent with always-on agent service | Rationale: v2 feedback — simplify implementation, don't cut features

## Important Discoveries
- `sqlite3.connect()` context manager lesson applies — BudgetStore and SessionPersistence already handle connections correctly with explicit `close()`, not `with` blocks
- 492 MCP search servers on GitHub, ALL single-provider — Argus is unique in multi-provider routing
- The dual database (SQLite for budgets/sessions + PostgreSQL for search history) is the biggest architectural smell
- `SearchPersistenceGateway` is already non-fatal (wraps exceptions with warning log) — zero-risk migration

## Blockers / Open Questions
None — plan is approved and ready to execute.

## Next Steps (Prioritized)
1. **Immediate**: Read `1shot/ROADMAP.md`, recreate tasks from it (they were in-memory), start Task #1 (consolidate caches)
2. **Parallel**: Tasks #6 (remove stubs), #7 (consolidate rate limiters), #3 (API auth) can run simultaneously with #1
3. **Critical path**: Task #10 (unify SQLite) unlocks Phase 2 and 3

## Resume
/restore @thoughts/shared/handoffs/2026-04-01-argus-hardening-handoff.md
