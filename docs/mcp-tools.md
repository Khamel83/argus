# MCP Tools and Resources

Argus exposes 7 tools and 3 resources via MCP. Start the server with:

```bash
argus mcp serve                                    # stdio (default)
argus mcp serve --transport sse --port 8001        # HTTP/SSE
```

Add to Claude Code, Cursor, or any MCP client:

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

---

## Tools

### search_web

Search the web using the Argus broker. Routes across providers with automatic fallback.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | *required* | Search query |
| `mode` | string | `"discovery"` | Search mode: `discovery`, `recovery`, `grounding`, `research` |
| `max_results` | int | `10` | Maximum results to return |
| `session_id` | string | `null` | Session ID for multi-turn context |

**Returns:** JSON with `query`, `mode`, `results` (array of `{url, title, snippet, provider, score}`), `total_results`, `cached`, `traces` (per-provider execution details), `run_id`. If `session_id` was provided, also includes `session_id`.

**Example:**
```json
// Input
{"query": "python async patterns", "mode": "discovery", "max_results": 5}

// Output (abbreviated)
{
  "query": "python async patterns",
  "mode": "discovery",
  "results": [
    {
      "url": "https://docs.python.org/3/library/asyncio.html",
      "title": "asyncio — Asynchronous I/O",
      "snippet": "asyncio is a library to write concurrent code...",
      "provider": "searxng",
      "score": 0.0164
    }
  ],
  "total_results": 5,
  "cached": false,
  "traces": [
    {"provider": "searxng", "status": "success", "results_count": 5, "latency_ms": 342, "error": null}
  ]
}
```

---

### extract_content

Extract clean text from a URL. Uses trafilatura (local) first, Jina Reader as fallback. Results are cached.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | *required* | URL to extract content from |

**Returns:** JSON with `url`, `title`, `text`, `author`, `date`, `word_count`, `extractor` (`"trafilatura"` or `"jina"`), `error`.

**Example:**
```json
// Input
{"url": "https://example.com/article"}

// Output (abbreviated)
{
  "url": "https://example.com/article",
  "title": "Example Article",
  "text": "The full article text extracted and cleaned...",
  "author": "Jane Doe",
  "date": "2026-03-15",
  "word_count": 1247,
  "extractor": "trafilatura",
  "error": null
}
```

---

### recover_url

Find where a dead, moved, or unavailable URL went. Searches for the URL itself plus optional hints.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `url` | string | *required* | The dead/moved URL |
| `title` | string | `null` | Title of the original page (helps narrow results) |
| `domain` | string | `null` | Domain hint (helps if the content moved to a new domain) |

**Returns:** Same format as `search_web`. Uses `recovery` mode internally.

---

### expand_links

Find related links for a topic. Useful for discovery and building reading lists.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | *required* | Topic to expand |
| `context` | string | `null` | Additional context to improve results |

**Returns:** Same format as `search_web`. Uses `discovery` mode with `max_results: 15`.

---

### search_health

Get health status of all search providers. No parameters.

**Returns:** JSON with `providers` (per-provider availability status) and `health_tracking` (success/failure counts, cooldown state).

**Example:**
```json
{
  "providers": {
    "searxng": {"status": "available", "reason": null},
    "brave": {"status": "available", "reason": null},
    "serper": {"status": "unavailable_missing_key", "reason": "API key not configured"}
  },
  "health_tracking": {
    "searxng": {"healthy": true, "consecutive_failures": 0, "in_cooldown": false},
    "brave": {"healthy": true, "consecutive_failures": 0, "in_cooldown": false}
  }
}
```

---

### search_budgets

Get budget status for all providers. No parameters.

**Returns:** JSON with `budgets` — per-provider `remaining` budget, `monthly_usage`, `usage_count`, and `exhausted` flag.

---

### test_provider

Smoke-test a single provider to verify it's working.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `provider` | string | *required* | Provider name: `searxng`, `brave`, `serper`, `tavily`, `exa` |
| `query` | string | `"argus"` | Test query to run |

**Returns:** JSON with `provider`, `available`, `status`, `trace` (execution details), and `sample_results` (up to 3 results with truncated snippets).

---

## Resources

MCP resources are read-only data that clients can subscribe to.

### argus://providers/status

Current availability status of all search providers. Same data as `search_health` but as a subscribable resource.

### argus://providers/budgets

Budget status for all providers. Same data as `search_budgets` but as a subscribable resource.

### argus://policies/current

Current routing policies — shows the provider chain for each search mode.

**Example:**
```json
{
  "discovery": ["searxng", "brave", "exa", "tavily", "serper"],
  "recovery": ["searxng", "brave", "serper", "tavily", "exa"],
  "grounding": ["brave", "serper", "searxng"],
  "research": ["tavily", "exa", "brave", "serper"]
}
```
