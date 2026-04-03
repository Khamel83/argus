# Changelog

All notable changes to Argus are documented here. Format follows [Keep a Changelog](https://keepachangelog.com/).

## [1.0.0] - 2026-04-02

First stable release. All core features complete and tested.

### Added
- **Search broker** — routes queries across SearXNG, Brave, Serper, Tavily, Exa with automatic fallback
- **Four search modes** — discovery, recovery, grounding, research — each with a tuned provider chain
- **Reciprocal Rank Fusion (RRF)** — merges and ranks results from multiple providers
- **Content extraction** — trafilatura (local) with Jina Reader fallback, cached in memory + SQLite
- **Multi-turn sessions** — context-enriched follow-up queries, persisted in SQLite
- **Budget enforcement** — per-provider monthly usage tracking with automatic rotation
- **Health tracking** — per-provider success/failure monitoring with cooldown windows
- **Token balance tracking** — track and auto-decrement API credits (Jina, etc.)
- **API key auth** — optional `ARGUS_API_KEY` for all endpoints (health exempt)
- **Rate limiting** — configurable per-client-IP request throttling
- **CORS configuration** — configurable allowed origins
- **Domain rate limiting** — 10 req/min/domain for content extraction
- **SSRF protection** — blocks private IP ranges in extraction
- **Provider admin** — runtime disable/enable/reset-health via CLI and API
- **Four interfaces** — HTTP API, CLI, MCP server, Python import
- **MCP server** — 7 tools + 3 resources for LLM integration (stdio and SSE transport)
- **Unified SQLite persistence** — all data in one file, WAL mode, graceful shutdown
- **URL deduplication** — normalization, tracking param removal, www-stripping
- **GitHub Actions CI** — pytest on Python 3.11 and 3.12
- **165+ tests** — unit, integration, and degraded-state test suites
