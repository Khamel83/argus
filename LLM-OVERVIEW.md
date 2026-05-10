# LLM Overview — argus
*Updated: 2026-05-10 07:35 UTC | Tier: standard | Auto-updated: daily cron*

## What This Is
Retrieval platform for AI agents. Argus routes search across 14 providers, recovers dead URLs, captures important site content, builds local docs-plus-research packs, and persists everything with traceable local artifacts.

## Current State
*Status: 🟢 active from local git history*

**Active work:**
- c55888a chore: bootstrap LLM-OVERVIEW files 2026-05-10
- 591bcfb feat: persist workflow status and extraction mode
- a6145b7 feat: improve MCP configuration automation and client support
- c483015 docs: remove remaining machine-specific assumptions and update roadmap
- ae8dda0 test: add comprehensive hardening tests for residential worker
- 7493f57 chore: bump version to 1.6.0 and update changelog

**Known issues:**
- No known issue found in recent commit subjects or local TODO/BLOCKERS docs.

**Recent changes (7 days):**
- `c55888a chore: bootstrap LLM-OVERVIEW files 2026-05-10`
- `2632e8c Merge pull request #4 from Khamel83/codex/argus-atlas-integration`
- `591bcfb feat: persist workflow status and extraction mode`
- `a6145b7 feat: improve MCP configuration automation and client support`
- `8488dcc chore: align API and worker health with version 1.6.0`
- `7753300 chore: final lint cleanup across core modules`
- `5d08410 docs: finalize generic home server descriptions in AGENTS and CLAUDE`
- `4efc75d chore: make startup scripts fully user-neutral and relative`
- `53da034 docs: finalize repository-wide generic sync`
- `3e81d42 docs: use generic user and path placeholders in service templates`
- `5af71df docs: finalize generic examples and remove specific Tailscale IPs`
- `6c99037 docs: complete topology-aware sync for LLM guides and service templates`
- `f5df678 docs: finalize persistence and provenance descriptions in README`
- `20d6376 chore: align root compose config and internal version with 1.6.0`
- `c483015 docs: remove remaining machine-specific assumptions and update roadmap`
- `ae8dda0 test: add comprehensive hardening tests for residential worker`
- `541a6cc security: harden residential service against cookie leakage and timing attacks`
- `7493f57 chore: bump version to 1.6.0 and update changelog`
- `2cebf1a fix: complete centralized config for Jina and Firecrawl`
- `75f8f59 docs: finalize topology-aware instructions and SQLite defaults`

## Architecture
- Stack marker: Python package/CLI
- Stack marker: Docker Compose service
- Stack marker: systemd service
- Top-level entry: `AGENTS.md`
- Top-level entry: `argus/`
- Top-level entry: `argus.service`
- Top-level entry: `argus_search.egg-info/`
- Top-level entry: `CHANGELOG.md`
- Top-level entry: `CLAUDE.md`
- Top-level entry: `CODE_OF_CONDUCT.md`
- Top-level entry: `CONTRIBUTING.md`

## Key Commands
- `python3 -m pytest  # run tests if configured`
- `python3 -m pip install -e .  # editable install`
- `docker compose up -d  # start compose service from the relevant service directory`
- `git status --short`
- `git log --oneline -5`

## Dependencies
- **Runs on:** Not declared in local repo evidence.
- **Calls out to:** See repo docs and config files.
- **Called by:** Not declared in local repo evidence.
- **Env vars required:** `ARGUS_ALLOW_MCP`, `ARGUS_ALLOW_WEB_UI`, `ARGUS_BIND_HOST`, `ARGUS_BRAVE_API_KEY`, `ARGUS_BRAVE_ENABLED`, `ARGUS_BRAVE_MONTHLY_BUDGET_USD`, `ARGUS_BRAVE_TIMEOUT_SECONDS`, `ARGUS_BUDGET_DB_PATH`, `ARGUS_CACHE_TTL_HOURS`, `ARGUS_DB_URL`, `ARGUS_DEFAULT_MAX_RESULTS`, `ARGUS_DISABLE_PROVIDER_AFTER_FAILURES`, `ARGUS_ENV`, `ARGUS_EXA_API_KEY`, `ARGUS_EXA_ENABLED`, `ARGUS_EXA_MONTHLY_BUDGET_USD`, `ARGUS_EXA_TIMEOUT_SECONDS`, `ARGUS_EXTRACTION_CACHE_TTL_HOURS`, `ARGUS_EXTRACTION_DOMAIN_RATE_LIMIT`, `ARGUS_EXTRACTION_DOMAIN_WINDOW_SECONDS`, `ARGUS_EXTRACTION_TIMEOUT_SECONDS`, `ARGUS_GITHUB_API_KEY`, `ARGUS_GITHUB_ENABLED`, `ARGUS_GITHUB_TIMEOUT_SECONDS`, `ARGUS_HOST`, `ARGUS_LINKUP_API_KEY`, `ARGUS_LINKUP_ENABLED`, `ARGUS_LINKUP_MONTHLY_BUDGET_USD`, `ARGUS_LINKUP_TIMEOUT_SECONDS`, `ARGUS_LOG_FULL_RESULTS`, `ARGUS_LOG_LEVEL`, `ARGUS_LOG_PROVIDER_PAYLOADS`, `ARGUS_PARALLEL_API_KEY`, `ARGUS_PARALLEL_ENABLED`, `ARGUS_PARALLEL_MONTHLY_BUDGET_USD`, `ARGUS_PARALLEL_TIMEOUT_SECONDS`, `ARGUS_PORT`, `ARGUS_PROVIDER_COOLDOWN_MINUTES`, `ARGUS_RATE_LIMIT`, `ARGUS_RATE_LIMIT_WINDOW`, `ARGUS_RESIDENTIAL_ENDPOINTS`, `ARGUS_SEARCHAPI_API_KEY`, `ARGUS_SEARCHAPI_ENABLED`, `ARGUS_SEARCHAPI_MONTHLY_BUDGET_USD`, `ARGUS_SEARXNG_BASE_URL`, `ARGUS_SEARXNG_ENABLED`, `ARGUS_SEARXNG_TIMEOUT_SECONDS`, `ARGUS_SERPER_API_KEY`, `ARGUS_SERPER_ENABLED`, `ARGUS_SERPER_MONTHLY_BUDGET_USD`, `ARGUS_SERPER_TIMEOUT_SECONDS`, `ARGUS_TAVILY_API_KEY`, `ARGUS_TAVILY_ENABLED`, `ARGUS_TAVILY_MONTHLY_BUDGET_USD`, `ARGUS_TAVILY_TIMEOUT_SECONDS`, `ARGUS_VALYU_API_KEY`, `ARGUS_VALYU_ENABLED`, `ARGUS_VALYU_MONTHLY_BUDGET_USD`, `ARGUS_VALYU_TIMEOUT_SECONDS`, `ARGUS_WOLFRAM_API_KEY`, `ARGUS_WOLFRAM_ENABLED`, `ARGUS_WOLFRAM_MONTHLY_BUDGET_USD`, `ARGUS_WOLFRAM_TIMEOUT_SECONDS`, `ARGUS_YAHOO_ENABLED`, `ARGUS_YAHOO_TIMEOUT_SECONDS`, `ARGUS_YOU_API_KEY`, `ARGUS_YOU_ENABLED`, `ARGUS_YOU_MONTHLY_BUDGET_USD`

## Critical Rules
- Preserve repo-local instructions in `AGENTS.md`, `CLAUDE.md`, or README when present.
- Do not infer behavior from the repository name alone; verify against local docs and source.

## Gotchas
- Generated from local evidence only: git history, top-level structure, README/CLAUDE/AGENTS/docs, and env examples.
