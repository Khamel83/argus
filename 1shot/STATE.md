# Argus Hardening — State

## Phase: plan → build
## Started: 2026-04-01
## Branch: codex/architectural-refactor-plan-20260331

## Completed Tasks
(none yet)

## Current Status
Plan approved. Ready to execute Phase 1. Tasks #1, #6, #7, #3 are unblocked.

## Key Decisions
- KEEP Argus — "stable search substrate, not a miniature startup"
- Collapse PostgreSQL to SQLite (gateway is non-fatal, data is analytics-only)
- Keep all active providers (SearXNG, Brave, Serper, Tavily, Exa)
- Docker-only deployment (no systemd)
- Add WAL mode for SQLite in Docker
- MCP is distribution, not moat — keep as interface alongside HTTP + Python import

## User Context
- Single user, Docker on oci-dev (100.126.13.70)
- MCP connected to Claude Code
- Barely using Argus today — wants better searching for himself
- Wants to keep as personal infrastructure, not a product
- Wants to add MORE providers in future, not fewer
