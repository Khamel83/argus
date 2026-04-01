# Argus Evaluation Report v2 — April 2026

## Executive Summary

**Argus should become your stable search substrate, not a miniature startup.**

Keep Argus as the project. Do not restart from scratch. Do not swap to someone else's repo. The core idea — one broker over multiple providers, with HTTP, CLI, Python import, and MCP exposure — is already aimed at a real architecture and has no direct Python competitor. The risk is not "bad idea"; it is "too much scaffolding relative to present needs."

The path forward: **collapse internal inconsistency, harden what exists, and ship a clean single-user service.** Not smaller for its own sake — cleaner, more opinionated, and more internally consistent.

---

## 1. Codebase Value Assessment

### What's genuinely valuable

The project is not a toy script. It's organized around a real architecture: provider abstraction, broker policy layer, extraction fallback, session persistence, and multiple interfaces. The README positions it accurately as a single endpoint with fallback, ranking, health tracking, budget enforcement, extraction, and multi-turn sessions.

| Component | Value | Assessment |
|-----------|-------|------------|
| **Broker policy layer** (routing, execution, policies) | Core product. Cheap-first routing, provider abstraction, extraction fallback, stable interface you control. | **Keep.** This is the real moat — not MCP, not any single provider. |
| RRF ranking | Fuses results across providers. Well-known algorithm, correctly implemented. | **Keep.** Adds genuine quality over naive concatenation. |
| URL deduplication | Normalizes and dedupes across providers. Handles tracking params, www stripping, query param sorting. | **Keep.** Necessary when merging results from multiple sources. |
| Health circuit breaker | Exponential cooldown on consecutive failures per provider. | **Keep.** Standard pattern, cleanly implemented. |
| Extraction fallback (trafilatura → Jina) | Correct hybrid approach — local first, API fallback. | **Keep.** Independently valuable. Could extend chain (add Firecrawl/Browserless for JS-heavy pages). |
| Provider abstraction | Clean base class, each provider maps to common `SearchResult`. | **Keep.** Makes adding new providers trivial. This should be *easier* to extend, not harder. |
| Sessions + budget tracking | Persisted across restarts. Coherent with always-on service feeding agents. | **Keep, but simplify implementation.** The features are right; the implementation complexity is the issue. |
| Tests | Real test directory with API, broker, config, extraction, providers, and sessions tests. | **Keep and expand.** Beyond prototype stage — this is a real project. |

### What needs fixing, not cutting

The architectural smells are about **internal inconsistency**, not bad features:

1. **Mixed persistence story** — Sessions and balances use raw `sqlite3`. The `persistence/` module pulls in SQLAlchemy + psycopg2 + PostgreSQL. Two database strategies for one single-user service. This is the biggest structural smell.

2. **Duplicate implementations** — Two cache classes (broker + extraction) with identical dict+TTL patterns. Two rate limiter implementations with identical sliding-window patterns. Should be one of each, shared.

3. **Global mutable state** — Config singleton, caches, Jina token counters are module-level globals. Makes testing harder, prevents running two instances in one process.

4. **Session refinement** — Naive string concatenation (`if query is short, prepend prior query`). Not semantic understanding. Worth keeping the *concept* but the implementation adds complexity without proportional value. Could defer improvement.

5. **Stub providers** — SearchAPI and You.com are empty shells (`enabled=False`). Either flesh them out or remove the dead code. Don't ship stubs.

6. **In-memory caches** — Lost on restart. Every deploy blows the cache. For a service, this means every query hits providers again after restart. Should be SQLite-backed.

### Critical gaps for production

- **In-memory caches** lost on restart → persistent cache needed
- **No authentication** on HTTP API → basic API key auth
- **Global mutable state** → proper app context or DI
- **No graceful shutdown** → cleanup hooks for connections and clients
- **Dual database strategy** → collapse to SQLite-only
- **Test coverage** — tests exist but need expansion for core broker logic

---

## 2. Competitor Landscape

### Direct competitors: NONE

No well-starred Python project does multi-provider search routing with ranking, dedup, budgets, and health tracking.

| Project | Stars | Language | Overlap |
|---------|-------|----------|---------|
| search-cli (199-biotechnologies) | 9 | Rust | Multi-provider fan-out, CLI-only, no ranking/budgets/sessions |
| Groqqle | 155 | Python | Single-provider, LLM summarization — not a broker |

### MCP search servers: 492 repos, ALL single-provider

| Server | Stars | Provider | Multi-provider? |
|--------|-------|----------|-----------------|
| Firecrawl MCP | ~5,900 | Firecrawl | No |
| Exa MCP | ~4,100 | Exa | No |
| DuckDuckGo MCP | ~939 | DuckDuckGo | No |
| Local Web Search MCP | ~700 | SearXNG | No |

**Argus is unique** in the MCP ecosystem — no other MCP server does multi-provider routing with ranking and dedup.

### SearXNG alone: covers 80% of use cases

SearXNG (27.5k stars) aggregates 100+ engines. What it lacks: API-key provider integration, content extraction, RRF ranking, budget enforcement, MCP interface, session management. Argus layers value on top of SearXNG.

### LLM native search: complementary, not replacing

| LLM | Native search | Implication |
|-----|--------------|-------------|
| Claude | Yes (WebSearch tool) | Good for ad-hoc lookups. No provider control, no budget, no extraction. |
| ChatGPT | Yes (web browsing + API) | Same. You want self-hosting and control. |
| Gemini | Yes (Google Search grounding) | Very good integration, but locked to Google's index. |
| Perplexity | IS a search product | Different category. Could be *added as a provider*. |

**Key insight**: Native LLM search is fine for quick lookups. Argus exists for when you want **your search control plane** — provider choice, cost optimization, extraction, ranking, session continuity. These are not the same use case.

### Content extraction: commoditizing but hybrid is correct

The hybrid approach (trafilatura local → Jina API fallback) is what the market is converging on. Could extend the chain with Firecrawl or Browserless for JS-heavy pages, but the pattern is right.

---

## 3. MCP vs HTTP vs Native Integration

### MCP is useful distribution, not defensibility

The broker policy layer is the real product. MCP is one of several interfaces the broker exposes. If Argus is good, it's because it's your search control plane — not because it speaks MCP.

**What MCP buys you**: Universal tool discovery across MCP-compatible hosts (Claude Code, Claude Desktop, Zed, Cursor). Write the server once, tools appear everywhere. This is real convenience.

**What MCP doesn't buy you**: Better search quality, defensibility, or any technical advantage over HTTP. It adds 10-200ms overhead (JSON-RPC framing, negligible vs LLM calls) and an extra failure mode (transport disconnection).

**Verdict**: Keep MCP as a first-class interface alongside HTTP and Python import. It's the right integration for Claude Code. Don't make it the only interface or treat it as a moat.

| Scenario | Best approach |
|----------|--------------|
| Claude Code / Claude Desktop | MCP (already configured) |
| Custom Python agent | Python import or HTTP API |
| Production service for other agents | HTTP API |
| Sharing across multiple LLM hosts | MCP (write once, use everywhere) |
| Quick ad-hoc search | Claude's native WebSearch (fine for lookups) |

---

## 4. The Recommendation

### KEEP ARGUS — make it a durable personal search substrate

**Why keep:**
1. No competitor does what it does (multi-provider broker with policy layer + extraction)
2. Core broker architecture is sound and correct
3. Provider abstraction makes adding new providers trivial (you want to add more, not fewer)
4. Content extraction is independently valuable
5. Already deployed, configured, in your MCP settings
6. Real test suite exists — beyond prototype stage

**What to collapse (internal consistency, not feature removal):**
1. Dual database strategy → SQLite for everything
2. Duplicate caches → one generic cache class
3. Duplicate rate limiters → one generic rate limiter class
4. Global mutable state → app context pattern
5. Stub providers → either implement or remove dead code

**What to keep (and improve, not cut):**
1. All active providers (SearXNG, Brave, Serper, Tavily, Exa) — and make adding new ones easier
2. Session persistence and budget tracking — but with simpler implementation
3. Extraction fallback chain — and potentially extend it
4. All three interfaces (HTTP, MCP, CLI)
5. Tests — and expand coverage

**Why NOT sunset or switch:**
1. No replacement provides the broker policy layer (routing, ranking, dedup, health, budgets)
2. A pile of individual provider MCP servers gives up the routing/ranking layer
3. Native LLM search gives up provider control, cost optimization, and extraction
4. The core is solid — it needs consistency and hardening, not replacement

---

## 5. Hardening Plan

### Phase 1: Collapse internal inconsistency
- [ ] Unify persistence to SQLite-only (remove SQLAlchemy + psycopg2 dependency)
- [ ] Consolidate duplicate cache into one generic `TTLCache` class
- [ ] Consolidate duplicate rate limiter into one generic `RateLimiter` class
- [ ] Fix global mutable state → app context or factory pattern
- [ ] Remove or implement stub providers (SearchAPI, You.com)

### Phase 2: Harden for production
- [ ] Persistent cache (SQLite-backed, survives restarts)
- [ ] Basic auth on HTTP API (API key middleware)
- [ ] Graceful shutdown hooks (close connections, flush state)
- [ ] Structured logging
- [ ] Expand test coverage for core broker logic
- [ ] Standardize error responses in HTTP API

### Phase 3: Ship as infrastructure
- [ ] systemd service file
- [ ] Health check endpoint (already exists, verify it works)
- [ ] Docker deployment (simplified, single container, SQLite volume)
- [ ] Document the "set and forget" setup
- [ ] Make adding a new provider a ~50-line task (clear template)

---

## 6. What "Done" Looks Like

**Argus running as a systemd service on oci-dev — your stable search substrate.**

- All active providers (SearXNG floor + Brave + Serper + Tavily + Exa)
- Easy to add new providers (~50 lines each)
- Content extraction (trafilatura → Jina, extensible)
- MCP server available to Claude Code and any MCP host
- HTTP API with basic auth for programmatic access
- Persistent everything (SQLite: cache, sessions, budgets)
- Tests pass
- Health endpoint returns provider status
- Zero maintenance beyond occasional `argus set-balance` for Jina tokens
- One database, one cache, one rate limiter, one config story
