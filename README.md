# Argus

<!-- mcp-name: io.github.Khamel83/argus -->

[![Python 3.11+](https://img.shields.io/badge/Python-3.11+-blue)](https://www.python.org/downloads/)
[![PyPI Version](https://img.shields.io/pypi/v/argus-search)](https://pypi.org/project/argus-search/)
[![PyPI Downloads](https://img.shields.io/pepy/dt/argus-search)](https://pepy.tech/projects/argus-search)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![CI](https://github.com/Khamel83/argus/actions/workflows/ci.yml/badge.svg)](https://github.com/Khamel83/argus/actions/workflows/ci.yml)
[![MCP Registry](https://img.shields.io/badge/MCP-Registry-blue)](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus)
[![Docker](https://img.shields.io/badge/ghcr.io-khamel83%2Fargus-blue)](https://github.com/Khamel83/argus/pkgs/container/argus)

Retrieval platform for AI agents. Argus routes search across 14 providers, recovers dead URLs, captures important site content, builds local docs-plus-research packs, and persists everything with traceable local artifacts.

**Features at a glance:**

- **Topology-aware acquisition** — Argus knows if it's on a residential IP or datacenter, routing search and extraction automatically to avoid blocks and minimize network hops.
- **14 providers, one API** — free-first tier routing, budget-exhausted providers skipped automatically
- **Zero-key start** — `pip install argus-search` gives you DuckDuckGo + Yahoo immediately, no accounts needed
- **SearXNG self-host = 70+ engines** — Google, Bing, Yahoo, Startpage, Ecosia, Qwant and more via one Docker container
- **12-step content extraction** — returns full page text with quality gates, not just links
- **Opinionated retrieval workflows** — recover dead articles, capture important pages from a site, and build local docs-plus-research packs
- **Argus-owned corpus storage** — runtime data goes to a writable user data directory, not your repo checkout
- **Multi-turn sessions** — pass `session_id` for conversational context across searches
- **4 search modes** — discovery, research, recovery, grounding
- **Dead URL recovery** — `/recover-url` with Wayback Machine and archive fallbacks
- **4 integration paths** — HTTP API, CLI, MCP server, Python SDK

_Built for AI agent builders, RAG pipelines, and ops teams who need reliable search, capture, and local evidence without stitching APIs together._

> Status: beta. The retrieval workflows and corpus model are production-oriented, but still maturing.

## Contents

- [Quickstart](#quickstart)
- [Development](#development)
- [Where Argus Writes Data](#where-argus-writes-data)
- [Opinionated Workflows](#opinionated-workflows)
- [Providers](#providers)
- [HTTP API](#http-api)
- [Integration](#integration)
  - [CLI](#cli)
  - [MCP](#mcp)
  - [Python](#python)
- [Content Extraction](#content-extraction)
- [Architecture](#architecture)
- [Configuration](#configuration)
- [When Not To Use Argus](#when-not-to-use-argus)
- [FAQ](#faq)

## Quickstart

### Mode 1: Local CLI (zero config)

```bash
pip install argus-search && argus search -q "python web frameworks"
```

That's it. DuckDuckGo handles the search — no accounts, no keys, no containers. You get unlimited free search from your laptop right now. Add API keys whenever you want more providers, or don't.

```bash
argus extract -u "https://example.com/article"       # extract clean text from any URL
argus recover-article -u "https://example.com/dead-post"
argus capture-site -u "https://docs.example.com"
argus build-research-pack -t "example sdk" --official-url "https://docs.example.com"
```

Works on any machine with Python 3.11+ — laptop, Mac Mini, Raspberry Pi, cloud VM. Nothing to host.

**For MCP (Claude Code, Codex, OpenCode, Cursor, VS Code):**

```bash
pipx install argus-search[mcp]
argus mcp init --global --client all
```

Then add to your MCP config:

```json
{"mcpServers": {"argus": {"command": "argus", "args": ["mcp", "serve"]}}}
```

Or install from the [MCP Registry](https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus):

```json
{
  "mcpServers": {
    "argus": {
      "registryType": "pypi",
      "identifier": "argus-search",
      "runtimeHint": "uvx"
    }
  }
}
```

One command to install, one command to connect. No server to run, no keys to configure.

### Mode 2: Full Stack Server

Got a Raspberry Pi running Pi-hole? A Mac Mini on your desk? An old laptop? That's enough to run the full stack — SearXNG (your own private search engine, disabled by default) plus local JS-rendering content extraction.

```bash
# Optional: tell Argus it has residential egress to optimize routing
export ARGUS_EGRESS_TYPE=residential
ARGUS_SEARXNG_ENABLED=true docker compose up -d    # SearXNG + Argus
```

| What you have | What you get |
|--------------|-------------|
| **Any machine with Python 3.11+** | DuckDuckGo + API providers (no server) |
| **Home server / old laptop** (4GB+) | Everything — SearXNG, all providers, Crawl4AI, Obscura |
| **Mac Mini M1+** (8GB+) | Full stack with headroom |
| **Free cloud VM** (1GB) | SearXNG + search providers (use residential workers for extraction) |

SearXNG takes 512MB of RAM and gives you a private Google-style search engine (disabled by default — set `ARGUS_SEARXNG_ENABLED=true`) that nobody can rate-limit, block, or charge for. It runs alongside Pi-hole on hardware millions of people already own.

## Where Argus Writes Data

Argus code and Argus runtime data are different things.

- **Code** lives wherever you install or clone Argus.
- **Runtime corpus data** lives in a writable user data directory resolved by `platformdirs`, or in `ARGUS_DATA_ROOT` if you override it.

Inspect the exact paths on your machine:

```bash
argus paths
```

By default Argus writes:

- official docs cache under the resolved `docs/cache/`
- research packs under `docs/research/`
- workflow run state under `workflows/runs/`
- versioned workflow snapshots under `snapshots/`

This means Argus does **not** require a sibling `../docs-cache` checkout. If you have an older `docs-cache` tree, import it once with:

```bash
argus corpus import-docs-cache -s /path/to/docs-cache
```

## Opinionated Workflows

These workflows build local artifacts, not just transient JSON responses.

### Recover A Dead Article

```bash
argus recover-article -u "https://example.com/old-post" -t "Example Post"
```

Argus searches for recovery candidates, extracts the best result, saves the recovered sources locally, and writes a citation-backed report plus manifest.

### Capture The Important Parts Of A Site

```bash
argus capture-site -u "https://docs.example.com"
```

Argus stays on-domain, uses sitemap-assisted discovery plus heuristic link scoring, saves the important pages it finds, and writes a detailed summary with references.

### Build A Docs + Research Pack

```bash
argus build-research-pack -t "example sdk"
argus build-research-pack -t "example sdk" --official-url "https://docs.example.com"
```

Argus captures official docs into its local docs cache, adds non-official supporting sources from search, and writes a combined research pack with traceable artifacts.

## Development

Repo development is pinned to Python 3.12. The package runtime floor remains Python 3.11, but contributors should use the `uv` workflow below so local verification matches CI and avoids accidentally using an older system interpreter.

```bash
uv sync --python 3.12 --extra dev --extra mcp
uv run pytest tests/ -v --tb=short
```

The repo includes `.python-version` with `3.12` so `uv`, `pyenv`, and similar tools pick the right interpreter by default. More contributor guidance lives in [CONTRIBUTING.md](CONTRIBUTING.md).

## Providers

| Provider | Credit type | Free capacity | Setup |
|----------|------------|---------------|-------|
| DuckDuckGo | Free (scraped) | Unlimited | None |
| Yahoo | Free (scraped) | Unlimited | None — fragile, auto-skipped if broken |
| SearXNG | Free (self-hosted, off by default) | Unlimited — 70+ engines¹ | Docker |
| GitHub | Free (API) | Unlimited | None (token for higher rate limit) |
| WolframAlpha | Free (API key) | 2,000 queries/month | [free key](https://developer.wolframalpha.com/) |
| Brave Search | Monthly recurring | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| Tavily | Monthly recurring | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| Exa | Monthly recurring | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |
| Linkup | Monthly recurring | 1,000 queries/month | [signup](https://linkup.so) |
| Serper | One-time signup | 2,500 credits | [signup](https://serper.dev/signup) |
| Parallel AI | One-time signup | 4,000 credits | [signup](https://parallel.ai) |
| You.com | One-time signup | $20 credit | [platform](https://you.com/platform) |
| Valyu | One-time signup | $10 credit | [platform](https://platform.valyu.ai) |

¹ SearXNG aggregates Google, Bing, Yahoo, Startpage, Ecosia, Qwant, Wikipedia, and 60+ more — all behind a single self-hosted endpoint. Run `docker compose up -d` on any machine with 512MB of free RAM.

² WolframAlpha returns **computed answers** (math, unit conversions, factual lookups), not web search results. It only activates in `grounding` and `research` modes. Queries it can't compute (general web searches) return empty — no error, no health penalty.

**7,000+ free queries/month** from recurring free-tier providers alone (WolframAlpha 2k + Brave 2k + Tavily 1k + Exa 1k + Linkup 1k). DuckDuckGo, Yahoo, and GitHub have no monthly cap. SearXNG is disabled by default (enable in `.env`). Routing priority: **Tier 0** (free: SearXNG*, DuckDuckGo, Yahoo, GitHub, WolframAlpha) → **Tier 1** (monthly recurring: Brave, Tavily, Exa, Linkup) → **Tier 3** (one-time: Serper, Parallel, You.com, Valyu, SearchAPI). Budget-exhausted providers are skipped automatically.

## HTTP API

All endpoints prefixed with `/api`. OpenAPI docs at `http://localhost:8000/docs`.

Local loopback calls can use the API without auth. Remote HTTP callers must send `ARGUS_API_KEY` as either `Authorization: Bearer ...` or `X-API-Key: ...`. Privileged routes under `/api/admin/*` require `ARGUS_ADMIN_API_KEY` (or fall back to `ARGUS_API_KEY` if no separate admin key is configured).

```bash
# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "mode": "discovery", "max_results": 5}'

# Multi-turn search (conversational refinement)
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "what about async?", "session_id": "my-session"}'

# Extract content from a working URL
curl -X POST http://localhost:8000/api/extract \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/article"}'

# Recover a dead or moved URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Public health
curl http://localhost:8000/api/health

# Admin health & budgets
curl -H "Authorization: Bearer $ARGUS_ADMIN_API_KEY" \
  http://localhost:8000/api/admin/health/detail
curl -H "Authorization: Bearer $ARGUS_ADMIN_API_KEY" \
  http://localhost:8000/api/admin/budgets
```

#### Search modes

| Mode | Use for | Example |
|------|---------|---------|
| `discovery` | Related pages, canonical sources | "Find the official docs for X" |
| `research` | Broad exploratory retrieval | "Latest approaches to Y?" |
| `recovery` | Finding moved/dead content | "This URL is 404" |
| `grounding` | Fact-checking with live sources | "Verify this claim about Z" |

Tier-based routing always applies first. Within each tier, the mode selects provider order.

#### Response format

```json
{
  "query": "python web frameworks",
  "mode": "discovery",
  "results": [
    {"url": "https://fastapi.tiangolo.com", "title": "FastAPI", "snippet": "Modern Python web framework", "score": 0.942}
  ],
  "total_results": 1,
  "cached": false,
  "traces": [
    {"provider": "duckduckgo", "status": "success", "results_count": 5, "latency_ms": 312}
  ]
}
```

Each result includes `url`, `title`, `snippet`, `domain`, `provider`, and `score`. The `traces` array shows which providers were called and their outcomes.

#### Budgets

```json
{
  "budgets": {
    "brave": {"remaining": 1847, "monthly_usage": 153, "usage_count": 153, "exhausted": false},
    "duckduckgo": {"remaining": 0, "monthly_usage": 0, "usage_count": 42, "exhausted": false}
  },
  "token_balances": {"jina": 9833638}
}
```

Each provider tracks usage. Tier 1 (monthly) uses a 30-day rolling window; tier 3 (one-time) uses a lifetime counter that never resets. When a provider hits its budget, Argus skips it and moves to the next. Free providers (DuckDuckGo, GitHub) have no limit. SearXNG is free but disabled by default. Set `ARGUS_*_MONTHLY_BUDGET_USD` to enforce custom limits per provider.

## Integration

### CLI

```bash
argus search -q "python web framework"              # zero-config, uses DuckDuckGo
argus search -q "python web framework" --mode research -n 20
argus search -q "fastapi" --session my-session       # multi-turn context
argus extract -u "https://example.com/article"       # extract clean text
argus extract -u "https://example.com/article" -d nytimes.com  # auth extraction
argus recover-url -u "https://dead.link" -t "Title"
argus doctor                                         # full setup diagnostics
argus health                                         # provider status
argus budgets                                        # budget + token balances
argus mcp check                                      # validate MCP setup
argus set-balance -s jina -b 9833638                 # track token balance
argus test-provider -p brave                         # smoke-test a provider
argus serve                                          # start API server
argus mcp serve                                      # start MCP server
argus mcp init                                       # add MCP config to project
```

All commands support `--json` for structured output.

<details>
<summary>How sessions work</summary>

Pass `session_id` to any search call. Argus stores each query and extracted URL in a SQLite-backed session. Reusing the same `session_id` gives the broker context from prior queries — follow-up searches are automatically refined using earlier conversation context. Sessions persist across restarts. Omit `session_id` for stateless, one-shot searches.

</details>

### MCP

**Option A — Local (stdio)**

Install and run on the same machine as your MCP client:

```json
{
  "mcpServers": {
    "argus": {
      "command": "argus",
      "args": ["mcp", "serve"]
    }
  }
}
```

Use the full path if `argus` isn't on PATH: `"/home/you/.local/bin/argus"`.

Works with **Claude Code**, **Codex CLI**, **OpenCode**, **Cursor**, and any stdio-based MCP client. Use `argus mcp init --global --client all` to write native client configs for the current machine.

**Option B — Self-hosted server (remote clients over Tailscale)**

Run Argus on one machine, connect every client over the network. No local install on clients.

On the server:
```bash
export ARGUS_API_KEY=replace-with-a-long-random-secret
argus mcp serve --transport streamable-http --host YOUR_TAILSCALE_IP --port 8001
```

To keep the HTTP API and remote MCP service running after reboot on a systemd host:
```bash
scripts/install-systemd.sh
systemctl status argus argus-mcp --no-pager
```

The installer copies `argus.service` and `scripts/argus-mcp.service`, enables both services, and starts them immediately. The start scripts load `.env` first, then optional local secrets vaults if the `secrets` CLI is installed.

On each client:

| Client | Config |
|--------|--------|
| **Claude Code** | `{"mcpServers":{"argus":{"type":"http","url":"http://<server>:<port>/mcp","headers":{"Authorization":"Bearer <ARGUS_API_KEY>"}}}}` in `~/.claude.json` (global) or `.mcp.json` (project) |
| **OpenCode** | `{"mcp":{"argus":{"type":"remote","url":"http://<server>:<port>/mcp","enabled":true,"headers":{"Authorization":"Bearer <ARGUS_API_KEY>"}}}}` in `~/.config/opencode/config.json` (global) or `.opencode/opencode.json` (project) |
| **Cursor** | Same as Claude Code — reads `.mcp.json` |
| **Codex CLI** | `[mcp_servers.argus]` section in `~/.codex/config.toml` with `url` and `bearer_token_env_var = "ARGUS_API_KEY"` — key must also be exported in `~/.zshrc` |
| **Gemini CLI** | `gemini mcp add argus http://<server>:<port>/mcp -t http -H "Authorization: Bearer <ARGUS_API_KEY>"` |
| **Antigravity** | `{"mcpServers":{"argus":{"serverUrl":"http://<server>:<port>/mcp","headers":{"Authorization":"Bearer <ARGUS_API_KEY>"}}}}` |

With [Tailscale](https://tailscale.com), `<server>` is your machine's Tailscale IP (e.g. `100.x.x.x`). One server, every machine on your mesh gets search.

**One-command provisioning:**

```bash
# Load secrets, then push config to any machine:
eval $(secrets decrypt argus | grep -E 'ARGUS_REMOTE_URL|ARGUS_API_KEY' | sed 's/^/export /')

curl -s https://raw.githubusercontent.com/Khamel83/argus/main/scripts/provision-mcp-client.sh | bash -s local              # this machine; uses local stdio if argus is installed
curl -s https://raw.githubusercontent.com/Khamel83/argus/main/scripts/provision-mcp-client.sh | bash -s user@100.x.x.x    # remote machine
```

The script writes Claude/Cursor, Codex, and OpenCode configs on the target. In local mode it prefers stdio and does not require `ARGUS_API_KEY`; remote HTTP mode still requires `ARGUS_REMOTE_URL` and `ARGUS_API_KEY`. Requires Python 3 (standard on macOS/Linux).

`argus mcp init` also generates configs automatically:
```bash
argus mcp init --global              # local stdio for Claude Code + OpenCode + Cursor
argus mcp init --client codex        # local stdio for Codex (writes ~/.codex/config.toml)
argus mcp init --client opencode     # local stdio for OpenCode
ARGUS_REMOTE_URL=http://argus.local:8271 ARGUS_API_KEY=... argus mcp init --global --client all
argus mcp init --client gemini       # prints gemini mcp add command
argus mcp init --global --client all # everything above
```

**Transports**: `stdio` (default, for local), `sse` (legacy remote), `streamable-http` (modern remote, `"type":"http"` in config). Remote HTTP transports require `ARGUS_API_KEY`.

Available tools:
- Local `stdio`: `search_web`, `extract_content`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`, `cookie_health`, `valyu_answer`
- Remote HTTP MCP: `search_web`, `extract_content`, `recover_url`, `expand_links`, `valyu_answer`; authenticated remote servers also expose `search_health`, `search_budgets`, `test_provider`, and `cookie_health`

### Python

```python
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode
from argus.extraction import extract_url

broker = create_broker()

response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY, max_results=10)
)
for r in response.results:
    print(f"{r.title}: {r.url} (score: {r.score:.3f})")

content = await extract_url(response.results[0].url)
print(content.title)
print(content.text)
```

## Content Extraction

Argus tries up to twelve methods to extract content from any URL: auth extraction for paywalls, then local extractors (trafilatura, Crawl4AI, Obscura, Playwright, residential IP), then external APIs (Jina, Valyu Contents, Firecrawl, You.com, Wayback, archive.is). Each attempt is quality-checked for completeness and garbage output. See [docs/providers.md](docs/providers.md) for the full extractor comparison.

**Completeness assessment** runs automatically after every successful extraction. Argus scores five signals — trailing ellipsis, feed truncation markers ("Read more", WordPress RSS footers), mid-sentence endings, abrupt final paragraphs, and suspicious round word counts — and returns `is_complete`, `completeness_confidence`, and `truncation_type` alongside the text. When confidence is ≥ 85%, Argus continues trying the next extractor rather than returning a partial result; this means a trafilatura fetch that ends with "..." will automatically fall through to Playwright, Jina, Wayback, etc. Callers that already have text (e.g. RSS feed items) can use `POST /api/assess-content` to check completeness without triggering extraction.

**Obscura** (optional) is a lightweight Rust headless browser (~70MB binary, 30MB RAM) with built-in stealth mode — it sets `navigator.webdriver=undefined`, randomizes canvas/GPU/audio fingerprints per session, and blocks 3,520 tracker domains. This directly addresses bot detection on JS-heavy and anti-scraping sites that block standard Playwright/Chrome. No API key, no rate limit — fully local.

Two ways to use it:

| Mode | Setup | What you get |
|------|-------|-------------|
| **CLI extraction step** | Install binary on `$PATH` | Argus auto-detects it; stealth browser as fallback step before Playwright |
| **CDP backend for Playwright** | Run `obscura serve --stealth --port 9222`, set `ARGUS_OBSCURA_CDP_URL=ws://127.0.0.1:9222` | Playwright uses Obscura as its browser engine — stealth + 30MB vs 200MB + DOM-to-Markdown output |

Install the binary: [github.com/h4ckf0r0day/obscura/releases](https://github.com/h4ckf0r0day/obscura/releases)

**Extract** gets the full text of a working URL and tells you whether that text is complete. **Recover-URL** finds alternatives when a URL is dead, paywalled, or radically changed.

## Architecture

```
Caller (CLI/HTTP/MCP/Python) → SearchBroker → tier-sorted providers → RRF ranking → response
                                     ↕ SessionStore (optional)
                            Extractor (on demand) → 12-step fallback chain with quality gates
```

| Module | Responsibility |
|--------|---------------|
| `argus/broker/` | Tier-based routing, ranking, dedup, caching, health, budgets |
| `argus/providers/` | Provider adapters (one per search API) |
| `argus/extraction/` | 12-step URL extraction fallback chain with quality gates |
| `argus/sessions/` | Multi-turn session store and query refinement |
| `argus/api/` | FastAPI HTTP endpoints |
| `argus/cli/` | Click CLI commands |
| `argus/mcp/` | MCP server for LLM integration |
| `argus/persistence/` | SQLite (default) or PostgreSQL query/result storage |

Add new providers or extractors with a single adapter file. See [CONTRIBUTING.md](CONTRIBUTING.md) for the interface.

### How a Query Works

```
query arrives → cache? → build provider queue → execute sequentially → RRF fuse → dedup → respond
```

1. **Cache check.** `SearchCache` hashes `normalized_query:mode` (SHA256). Hit returns immediately with a TTL of 168 hours (7 days).

2. **Provider queue.** `resolve_routing()` takes the mode-specific preference list and stable-sorts by tier: tier 0 (free) first, tier 1 (monthly) next, tier 3 (one-time) last. Example for discovery mode:
   ```
   searxng → duckduckgo → yahoo → github → brave → exa → tavily → linkup → serper → parallel → you → valyu
   ```

3. **Sequential execution with gates.** Each provider is checked in order. Four gates must pass before an API call:
   - **Config** — is the provider enabled and configured (API key present)?
   - **Health** — has it failed 5+ consecutive times (triggers 60-minute cooldown)?
   - **Budget** — for tier 1+: is the budget exhausted? For tier 1 (monthly), pacing checks if the 7-day usage rate would drain the remaining budget in under a week — empty days bank headroom. For tier 3 (one-time), a lifetime counter gates access — exhaustion is the sole check.
   - **Execute** — the actual HTTP call. Successes reset failure counters; failures increment them.

4. **RRF fusion.** Results from all queried providers are merged using Reciprocal Rank Fusion (`k=60`). Each result's score is the sum of `1/(k + rank)` across every provider that returned it. Results appearing in multiple providers rank higher.

5. **Dedup and truncate.** URLs are normalized (stripped `www.`, tracking params like `utm_*`, trailing slashes) and deduplicated. The merged list is truncated to `max_results` (default 10).

6. **Cache and persist.** The final response is written to the in-memory cache and persisted to the local SQLite database (default) or PostgreSQL. Search results and extractions include rich provenance metadata (`egress`, `machine`, `source_type`) for downstream audit.

## Configuration

All config via environment variables. See `.env.example` for the full list. Limited API-key providers are opt-in: set both the API key and `ARGUS_<PROVIDER>_ENABLED=true`. Missing keys degrade gracefully — providers are skipped, not errors.

| Variable | Default | Description |
|----------|---------|-------------|
| `ARGUS_NODE_ROLE` | `primary` | `primary`, `worker`, `caller`, or `dev` |
| `ARGUS_EGRESS_TYPE` | `unknown` | `residential`, `datacenter`, or `unknown` |
| `ARGUS_RESIDENTIAL_POLICY` | `fallback` | `off`, `fallback`, `prefer_on_datacenter`, `prefer_for_domains`, or `always` |
| `ARGUS_SEARXNG_ENABLED` | `false` | Set `true` when you have a SearXNG Docker container |
| `ARGUS_SEARXNG_BASE_URL` | `http://127.0.0.1:8080` | SearXNG endpoint |
| `ARGUS_SEARXNG_RESIDENTIAL_BASE_URL` | — | Remote residential SearXNG endpoint (e.g. over Tailscale) |
| `ARGUS_<PROVIDER>_ENABLED` | `false` for limited API-key providers | Opt in to providers that consume limited credits or quotas |
| `ARGUS_BRAVE_API_KEY` | — | Brave Search API key |
| `ARGUS_SERPER_API_KEY` | — | Serper API key |
| `ARGUS_TAVILY_API_KEY` | — | Tavily API key |
| `ARGUS_EXA_API_KEY` | — | Exa API key |
| `ARGUS_LINKUP_API_KEY` | — | Linkup API key |
| `ARGUS_PARALLEL_API_KEY` | — | Parallel AI API key |
| `ARGUS_YOU_API_KEY` | — | You.com API key |
| `ARGUS_VALYU_API_KEY` | — | Valyu API key (search, contents, answer) |
| `ARGUS_FIRECRAWL_API_KEY` | — | Firecrawl API key (content extraction) |
| `ARGUS_GITHUB_API_KEY` | — | GitHub token (higher rate limit) |
| `ARGUS_DATA_ROOT` | platformdirs user data dir | Override the Argus runtime corpus root |
| `ARGUS_*_MONTHLY_BUDGET_USD` | provider-specific | Query-count budget for most providers; USD budget for Valyu |
| `ARGUS_CRAWL4AI_ENABLED` | false | Enable Crawl4AI extraction step |
| `ARGUS_YOU_CONTENTS_ENABLED` | false | Enable You.com Contents API extraction |
| `ARGUS_OBSCURA_CDP_URL` | — | Obscura CDP endpoint (e.g. `ws://127.0.0.1:9222`) — makes Playwright use Obscura as its browser engine |
| `ARGUS_OBSCURA_TIMEOUT_SECONDS` | 20 | Timeout for Obscura CLI subprocess calls |
| `ARGUS_CACHE_TTL_HOURS` | 168 | Result cache TTL |

## When Not To Use Argus

Argus is best when you need search, capture, provenance, and local artifacts together.

Avoid it when:

- you only need one search API and do not need fallback or budget controls
- you only need a one-off page scrape with no persistent corpus or report output
- you need an end-user search UI rather than backend retrieval infrastructure
- you need fully deterministic summarization with no heuristic or LLM-assisted steps

## FAQ

**How is this different from calling Tavily/Serper directly?**
Argus calls them for you — plus 13 other providers. You get one ranked, deduplicated result set instead of managing multiple API keys and stitching results together. Free providers are tried first, so you only burn credits when needed.

**Can I run only one provider?**
Yes. Set only the API key for the provider you want. All others are silently skipped. For zero-config, just install and go — DuckDuckGo + Yahoo handle search with no keys.

**Do I need Docker?**
No. `pip install argus-search` works immediately on any machine with Python 3.11+. Docker is only needed for SearXNG (set `ARGUS_SEARXNG_ENABLED=true` in `.env`) or Crawl4AI (local JS rendering).

**Which Python version should contributors use?**
Use Python 3.12 for repo development and verification: `uv sync --python 3.12 --extra dev --extra mcp` then `uv run pytest tests/ -v --tb=short`. The published package still supports Python 3.11+.

**What is the safest way to deploy Argus on a network?**
Use Tailscale or another private network, bind explicitly to the trusted interface, set `ARGUS_API_KEY`, and reserve `/api/admin/*` for `ARGUS_ADMIN_API_KEY`. Treat direct internet exposure as an advanced mode behind a reverse proxy.

## License

MIT — see [CHANGELOG.md](CHANGELOG.md) for release history.
