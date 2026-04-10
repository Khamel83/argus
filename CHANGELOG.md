# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
