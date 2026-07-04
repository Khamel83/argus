# AGENTS.md — Argus

> **What this file is for:** the canonical guide for AI coding agents working in
> this repository (Claude Code, Codex, Cursor, Copilot, OpenCode, and friends).
> Human contributors should start with [CONTRIBUTING.md](CONTRIBUTING.md);
> background on the project lives in [CONTEXT.md](CONTEXT.md) and
> [README.md](README.md). [CLAUDE.md](CLAUDE.md) is a short pointer back here
> with Claude-Code-specific notes.

## Overview

Search infrastructure for AI agents: 14 providers, topology-aware routing, WolframAlpha computed answers, and a 12-step content extraction chain. Provider adapters: SearXNG (self-hosted, aggregates 70+ engines, disabled by default), DuckDuckGo, Yahoo (scraped), GitHub, WolframAlpha (free API, computed answers), Brave, Tavily, Exa, Linkup (monthly free tiers), Serper, Parallel AI, You.com, Valyu, SearchAPI. Tier-based routing: free providers first, monthly recurring next, one-time credits last. Budget enforcement skips exhausted providers automatically. Multi-turn sessions (SQLite). Connect via HTTP, CLI, MCP, or Python import.

## Features
- **Topology-aware acquisition** — Argus knows if it's on a residential IP or datacenter, routing search and extraction automatically to avoid blocks and minimize network hops.
- **Adaptive Domain Memory** — Learns which domains fail from datacenter IPs but succeed from residential ones, automatically routing future requests for those domains to residential egress.
- **Universal Provenance** — Every search result and extraction is tagged with `egress` (residential|datacenter), `machine`, and `source_type`.
- **Intelligent Routing** — Tier-based routing: free providers first, monthly recurring next, one-time credits last. Budget enforcement skips exhausted providers automatically.
- **12-step Extraction** — Trafilatura → Crawl4AI → Playwright → Jina → Valyu → Firecrawl → You → Archive with quality gates and completeness assessment.

## Two Deployment Tiers

### Tier 1: No server (API keys only)
- `pip install argus-search` — works immediately with DuckDuckGo + Yahoo
- Add WOLFRAM_APP_ID for computed answers (math, facts, conversions — 2,000 free/month)
- Add API keys for 7,000+ more free monthly queries
- Extraction via external APIs only (Jina, Valyu Contents, Firecrawl, You.com Contents, Wayback)
- Default SQLite persistence (no database server required)
- Works on any machine with Python 3.11+ (laptop, Mac Mini, Pi, cloud VM)

### Tier 2: Full install on hardware you already have
- Raspberry Pi 4 (4GB): Everything — SearXNG, all providers, Crawl4AI local JS extraction, Obscura stealth browser.
- Home Server (e.g. homelab): Set `ARGUS_EGRESS_TYPE=residential` to optimize routing and skip external workers.
- `docker compose up -d` for one-command setup

## Key Commands

```bash
# Setup
cp .env.example .env                    # configure providers and DB
pip install "argus-search[mcp]"         # install from PyPI (with MCP support)
pip install "argus-search[mcp,crawl4ai]" # with Crawl4AI extractor

# Run
argus serve                   # HTTP API on :8000
argus mcp serve               # MCP server (stdio)
argus mcp serve --transport streamable-http --port 8001  # modern remote

# Search
argus search -q "query" --mode discovery
argus search -q "follow up" --session abc123   # multi-turn context

# Content Extraction
argus extract -u "https://example.com/article"

# Diagnostics
argus doctor                  # full setup check (config, providers, connectivity, MCP)
argus health                  # provider status
argus budgets                 # budget status + token balances
argus mcp check               # validate MCP server setup
```

## Architecture

```
Caller (CLI/HTTP/MCP/Python)
  → SearchBroker (topology-aware)
    → routing policy (tier-sorted, egress-policy)
      → provider executor (budget → health → search → provenance injection)
    → result pipeline (cache → dedupe → RRF ranking → response)
  → SessionStore (optional, per-request)
  → Extractor (12-step chain with topology awareness)
    → Adaptive Domain Memory (SQLite)
```

| Module | Responsibility |
|--------|---------------|
| `argus/broker/` | Tier-based routing, ranking, topology-aware search, caching, budgets |
| `argus/providers/` | Provider adapters with egress/machine metadata injection |
| `argus/extraction/` | 12-step extraction with topology awareness, adaptive domain memory, and quality gates |
| `argus/api/` | FastAPI HTTP endpoints with provenance metadata |
| `argus/cli/` | Click CLI commands (supports `--json` provenance) |
| `argus/mcp/` | MCP server for LLM integration with rich provenance tools |
| `argus/persistence/` | SQLite (default) or PostgreSQL query/result storage |

## Provider Tiers

| Tier | Providers | Credits |
|------|-----------|---------|
| 0 (free) | SearXNG (70+ engines, disabled by default — enable if you have Docker), DuckDuckGo, Yahoo (scraped), GitHub, WolframAlpha (2k/mo, API key) | Unlimited or free recurring |
| 1 (monthly) | Brave (2k/mo), Tavily (1k/mo), Exa (1k/mo), Linkup (1k/mo) | Recurring monthly |
| 3 (one-time) | Serper (2.5k), Parallel (4k), You.com ($20), SearchAPI, Valyu ($10) | Don't come back |

## Search Modes

| Mode | Use case |
|------|----------|
| `discovery` | Related pages, canonical sources |
| `recovery` | Dead/moved URL |
| `grounding` | Fact-checking + computed answers |
| `research` | Broad exploratory |

## Content Extraction

12-step fallback chain with quality gates and completeness assessment between every step:

```
trafilatura → Crawl4AI → Obscura → Playwright → Jina Reader →
Valyu Contents → Firecrawl → You.com Contents → Wayback Machine → archive.is
```

## Multi-Turn Sessions

Pass `session_id` to search to enable conversational refinement. The broker remembers prior queries and uses them to context-enrich follow-up searches. Sessions persist to SQLite across restarts.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ARGUS_NODE_ROLE` | `primary` | `primary`, `worker`, `caller`, or `dev` |
| `ARGUS_EGRESS_TYPE` | `unknown` | `residential`, `datacenter`, or `unknown` |
| `ARGUS_RESIDENTIAL_POLICY` | `fallback` | `off`, `fallback`, `prefer_on_datacenter`, `prefer_for_domains`, or `always` |
| `ARGUS_DB_URL` | `sqlite:///.../argus.db` | Main database URL |
| `ARGUS_AUTOLOAD_DOTENV` | `true` | Auto-load `.env` / `.env.local` from cwd and repo root (does not override exported env vars) |

## Conventions

- Provider adapters must never leak provider-specific shapes outside `argus/providers/`
- All search results are `SearchResult`: url, title, snippet, domain, provider, score, egress, machine
- Extracted content is `ExtractedContent`: url, title, text, author, date, word_count, egress, machine, source_type
- Routes prefixed with `/api`
- Token balances persist in SQLite alongside budget tracking
- Version bumps must update `pyproject.toml` AND `server.json`
- `README.md` must retain `<!-- mcp-name: io.github.Khamel83/argus -->`

## Agent Usage Contract

Argus is internet retrieval, not personal memory. Use it when you need web search, URL recovery, link expansion, or page extraction. Do not use it as a note store or a substitute for Maya.

### MCP usage

Use MCP only from an AI harness that can speak MCP natively. The core tools are:

- `search_web` — web search and ranking across providers.
- `extract_content` — extract readable content from a URL.
- `recover_url` — recover moved or dead URLs.
- `expand_links` — expand a page into related links for follow-up retrieval.

Use MCP when the caller is an interactive agent session, and prefer the MCP tools over shelling out. For scripts, jobs, or direct service integrations, use HTTP instead.

### HTTP usage

- Search: `POST /api/search`
- Extraction: `POST /api/extract`
- Auth headers: `Authorization: Bearer $ARGUS_API_KEY` or `X-API-Key: $ARGUS_API_KEY`
- Privileged admin routes: `Authorization: Bearer $ARGUS_ADMIN_API_KEY` or `X-Admin-API-Key: $ARGUS_ADMIN_API_KEY`

Example search payload:

```json
{
  "query": "python web frameworks",
  "mode": "discovery",
  "max_results": 5
}
```

Modes:

- `discovery` — find canonical sources and related pages.
- `research` — broad exploratory retrieval.
- `recovery` — find moved or dead URLs.
- `grounding` — fact-checking and computed-answer support.

### Canonical transport policy

See [maya/docs/CONTEXT-CONTRACT.md](https://github.com/Khamel83/maya/blob/main/docs/CONTEXT-CONTRACT.md) for the cross-service transport and role contract.
