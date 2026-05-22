---
date: 2026-05-22
status: approved
---

# Free Mode + Dashboard Call Count Fix

## Problem

Two related issues:

1. No way to constrain searches to free providers only — any query can silently cascade into paid providers and burn credits.
2. The dashboard "Calls" column counts skipped providers the same as actually-attempted ones, making provider activity unreadable (e.g. Yahoo showing 5,540 "calls" with 0ms latency).

## Design

### Feature 1: `free_only` flag

**Model change** — `argus/models.py`: add `free_only: bool = False` to `SearchQuery`.

**Execution gate** — `argus/broker/execution.py`: add one check before the existing tier gate:

```python
if query.free_only and tier > 0:
    traces.append(ProviderTrace(provider=pname, status="skipped", error="free_only mode"))
    continue
```

This runs before budget/health checks — a paid provider is never contacted when `free_only=True`.

**CLI** — `argus/cli/search.py`: add `--free` boolean flag that sets `free_only=True` on the `SearchQuery`.

**HTTP** — no extra work; `free_only` flows through the existing `SearchQuery` Pydantic model in the request body.

**MCP** — `argus/mcp/server.py`: add `free_only: bool = False` parameter to the `search_web` tool. Pass through to `SearchQuery`.

**Caching** — `free_only` searches use the same cache key as regular searches. A cached result from a paid-provider search can be served to a `free_only` caller (it's already computed, no credits spent). This is the correct tradeoff — cache complexity not worth it.

**Tier 0 providers** (always used, never gated by `free_only`): SearXNG, DuckDuckGo, Yahoo, GitHub, WolframAlpha.

### Feature 2: Dashboard call count fix

**SQL fix** — `argus/api/usage.py` `get_provider_activity()`: add `WHERE status != 'skipped'` so only providers that actually made an HTTP call appear in the table.

**Column rename** — Dashboard HTML: "Calls" → "Attempted" to be precise about what the number means.

**Effect**: Providers like Yahoo, Valyu, Exa, SearchAPI that are consistently skipped disappear from the table entirely when they have no real attempts. Providers with occasional skips only show their real attempt count.

## Out of Scope

- Per-caller access controls (separate feature)
- Rate limiting (separate feature)
- Separate cache keys for `free_only` vs full searches

## Files Changed

| File | Change |
|------|--------|
| `argus/models.py` | Add `free_only: bool = False` to `SearchQuery` |
| `argus/broker/execution.py` | Add free_only gate before tier check |
| `argus/cli/search.py` | Add `--free` flag |
| `argus/mcp/server.py` | Add `free_only` param to `search_web` |
| `argus/api/usage.py` | Add `WHERE status != 'skipped'` to provider activity query |
| Dashboard template | Rename "Calls" → "Attempted" |

## Verification

- `argus search -q "test" --free` never calls Brave, Tavily, Serper, Parallel, etc.
- `argus search -q "test"` behaviour unchanged.
- Dashboard "Attempted" column shows only providers that made real HTTP calls.
- Existing tests pass.
- New test: `free_only=True` query skips all tier > 0 providers even when free results < max_results.
