# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- **Valyu Credits Balance Checker** — New `check-balances` integration for Valyu AI (`GET /v1/credits/balance`).
- **Valyu Extraction Tracking** — Extraction calls to Valyu Contents API are now properly recorded against the budget tracker ($0.001/URL).

### Changed
- **USD-aware Budget Tracking** — Valyu provider now tracks usage in USD instead of query counts.
- **Actual Cost Tracking** — Valyu search now uses the `total_deduction_dollars` returned by the API for precise budget deduction.
- **CLI Budget Display** — `argus budgets` now displays USD for Valyu (e.g., `$8.32` spent, `$1.68` remaining).
- **Default Valyu Budget** — Updated default Valyu budget from 10,000 queries to $10.0 to reflect USD tracking.
- **Limited Provider Defaults** — API-key providers with finite credits are now opt-in; having a key in env/secrets no longer enables spend by itself.

### Fixed
- Budget discrepancy where Valyu was showing 0 usage despite credit depletion due to untracked extraction calls.
- Untracked Valyu Answer API usage now records returned USD cost in the budget store.
- Paid providers are skipped when free providers have already satisfied the requested result count, unless the caller explicitly requests providers.

## [1.6.0] - 2026-05-03

### Added
- **Topology-aware acquisition** — Argus now recognizes its egress environment (residential vs. datacenter) and node role (primary, worker, caller). Optimized for "homelab-first" retrieval.
- **Adaptive Domain Memory** — SQLite-backed system that learns which domains require residential egress due to datacenter IP blocks. Automatically routes future requests for those domains to residential endpoints.
- **Universal Provenance** — Every search result and extraction is now tagged with `egress` (residential|datacenter), `machine`, and `source_type`. Exported via API, CLI JSON, and MCP tools.
- **`archive_ingest` extraction mode** — Optimized fallback chain for Atlas-style archival ingestion. Prioritizes local residential and archive recovery (Wayback, Archive.is) before spending credits on paid APIs.
- **Residential Search Support** — SearXNG provider now supports an optional `ARGUS_SEARXNG_RESIDENTIAL_BASE_URL` to route searches through trusted residential nodes over Tailscale.
- **Hardened Worker Safety** — Residential worker service now includes per-domain rate limiting and post-redirect SSRF validation for all fetched URLs.

### Changed
- **"Zero-Config" Persistence** — Default database switched from PostgreSQL to **SQLite** (located in `~/.local/share/argus/argus.db`). Enables search history and domain memory out-of-the-box.
- **Centralized Extractor Config** — Jina and Firecrawl extractors now use the centralized `ArgusConfig` for API keys and timeouts, consistent with the rest of the system.
- **Documentation Overhaul** — README, AGENTS.md, and CLAUDE.md rewritten to reflect the residential-first architecture and remove stale OCI-centric primary assumptions.

## [1.5.1] - 2026-04-29

### Changed
- Secrets resolver now batch-loads all vault files in a single pass instead of spawning a subprocess per key. MCP server startup ~19x faster (15s → 0.8s). Gracefully handles machines without the `secrets` CLI.
- **SearXNG disabled by default** — no confusing Docker errors on first run. Enable in `.env` when you have a container running.
- **Human-readable provider status** — `argus health` and `argus test-provider` show "MISSING KEY", "COOLDOWN", etc. instead of raw enum values.
- **Search mode descriptions** in `--help` and `argus mcp init` warns before overwriting existing config.
- **Budget warnings** displayed after CLI search and included in MCP `search_web` JSON response.
- **`argus doctor`** — new diagnostic command: config check, provider audit, SearXNG/DuckDuckGo connectivity, MCP package check.
- **`argus mcp check`** — validates MCP setup: package, Context support, config file, API key.
- **MCP progress notifications** — `capture_site`, `build_research_pack`, and `recover_dead_article` report progress back to Claude/Codex sessions via `ctx.report_progress()`.
- All providers enabled by default.
- WolframAlpha 501 handling — queries that can't compute return empty without health penalty.

## [1.5.0] - 2026-04-28

### Added
- **Content completeness assessment** (`argus/extraction/completeness.py`) — detects feed-level truncation (not just paywalls) via five signals: trailing ellipsis, feed truncation markers ("Read more", WordPress RSS footers, etc.), mid-sentence endings, abrupt final paragraphs, and suspicious round word counts. Returns `is_complete`, `completeness_confidence` (0–1), `truncation_type`, `completeness_signals`, and `recommended_action` on every extraction result.
- **`POST /api/assess-content`** — lightweight endpoint that assesses completeness of text you already have, no fetching. Accepts `{text, url}`, returns the full completeness breakdown. Ideal for callers scanning stored content (RSS feeds, archives).
- **Completeness-aware extractor chain** — free extractors (steps 1–6: auth, trafilatura, crawl4ai, obscura, playwright, residential) now continue to the next extractor when content passes the quality gate but completeness confidence is ≥ 0.85. This means a trafilatura result that ends with "..." or a "Read more" marker automatically falls through to Playwright, residential, etc. Paid/external extractors (steps 7–12) assess but do not continue the chain for completeness alone.
- **Retrieval workflows** — `recover-article`, `capture-site`, `build-research-pack` CLI commands with local artifact persistence.
- **Corpus storage** — runtime data in writable user data directory via `platformdirs` (or `ARGUS_DATA_ROOT`).
- Auth extraction step for paywall domains (NYT, Bloomberg, etc.) via cookie-based authentication.
- Residential IP extraction step (remote service over Tailscale).
- Obscura stealth browser integration (CLI step + CDP backend for Playwright).
- `cookie_health` MCP tool for monitoring authenticated domain status.
- Streamable HTTP MCP transport for Antigravity clients.
- `AGENTS.md` for AI contributor onboarding.
- Systemd service file for Argus HTTP API.

### Changed
- Extraction chain expanded from 10 to 12 steps (added auth + residential).
- HTTP server binds to 0.0.0.0 for Tailscale network access.
- Tier-aware budget tracking with 7-day pacing.
- Python badge URL fixed (cleaner shields.io format).
- PyPI and MCP Registry descriptions updated.
- Documentation accuracy pass — extraction step counts verified.

## [1.3.3] - 2026-04-14

### Added
- MCP Registry publishing — live at [modelcontextprotocol.io](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus) as `io.github.Khamel83/argus`
- `server.json` for MCP Registry metadata and verification
- GitHub Actions publish workflow (PyPI + MCP Registry on release)
- `mcp-name` verification tag in README for PyPI-based ownership

### Changed
- MCP badge links to registry listing
- README MCP quickstart includes registry-based install option
- CLAUDE.md documents version sync convention and MCP Registry interface

## [1.3.2] - 2026-04-13

### Added
- GitHub search provider (free, tier 0) — 10 req/min unauthenticated, 30/min with token
- Valyu provider — search, contents extraction, and AI-synthesized answers with citations
- Firecrawl extractor — content extraction (1 credit/page)
- Pace-aware routing — always queries free providers, paces paid ones based on remaining budget
- Proactive balance checking — header parsing + Tavily usage API for live balance tracking

### Changed
- Documentation quality pass — features, JSON examples, modes, FAQ
- README and CLAUDE.md updated for new providers and ease-of-setup positioning

## [1.3.1] - 2026-04-09

### Added
- DuckDuckGo search provider — zero-config free search, no API key, unlimited
- `ddgs` package as core dependency

### Fixed
- Duplicate SearXNG entry in RESEARCH mode preferences causing double-dispatch

### Changed
- Documentation rewritten with free-first positioning and two deployment tiers
- Hardware requirements table (Raspberry Pi, Mac Mini, laptop, cloud VM)
- All credit claims corrected to standard signup amounts (not promo deals)

## [1.3.0] - 2026-04-08

### Added
- 3 new search providers: Linkup, Parallel AI, You.com
- 2 new extractors: You.com Contents API, Crawl4AI (local JS rendering)
- Tier-based credit routing: Tier 0 (free) → Tier 1 (monthly) → Tier 3 (one-time)
- Budget enforcement with per-provider query-count tracking on 30-day rolling window
- `argus mcp init` command for MCP client configuration

### Changed
- Provider routing now sorts by credit tier first, mode preference second
- Override provider lists are also tier-sorted
- DuckDuckGo added to all 4 search mode preference lists

## [1.2.1] - 2026-03-28

### Changed
- Renamed PyPI package from `argus` to `argus-search`
- Updated install commands across all documentation

### Fixed
- MCP server sync with mcp 1.26.0 API changes

## [1.2.0] - 2026-03-25

### Added
- Content extraction with quality gates — trafilatura, Playwright, Jina, Wayback, archive.is
- `argus extract` CLI command for URL content extraction
- `argus cookies import/health` commands for authenticated extraction
- Cookie-based authenticated extraction for paywall domains
- Quality gate system between extraction steps (paywall detection, soft 404s, minimum quality)
- Docker multi-stage build with GHCR auto-publish on push/tag

### Changed
- Extract response now includes `quality_passed`, `quality_reason`, `extractors_tried`

### Fixed
- MCP server version kwarg removal and async fixes
- Docker builder stage source copy

## [1.1.0] - 2026-03-20

### Added
- Single retry for unhandled provider exceptions
- Documented and configurable cost estimates

### Changed
- Lazy extractor initialization (no SQLite trigger at import time)
- Deduplicated `_extract_domain` across providers
- UTC-aware timestamps throughout

### Removed
- Docker files (replaced with direct install approach)

## [1.0.0] - 2026-03-15

### Added
- 7 search providers: SearXNG, Brave, Serper, Tavily, Exa, You.com, SearchAPI
- Tier-based routing policies (RECOVERY, DISCOVERY, GROUNDING, RESEARCH modes)
- Reciprocal Rank Fusion (RRF) result ranking
- URL deduplication with normalization (www, trailing slash, tracking params, case)
- In-memory search cache with configurable TTL
- Health tracker with failure threshold and cooldown
- Budget tracker with 30-day rolling window
- Multi-turn sessions with TTL, max turns, and context limits
- Authenticated extraction for paywall domains via Playwright
- HTTP API (FastAPI) with OpenAPI docs
- CLI (Click) with search, health, budgets, extract commands
- MCP server for LLM integration
- PostgreSQL persistence layer
- PyPI publishing pipeline

## [1.0] - 2026-03-12

### Added
- Session TTL, max turns, max context chars, and delete endpoint
- Runtime provider disable/enable/reset-health admin endpoints
- Provenance fields on SearchResult model
- Degraded-state test suite (14 tests)
