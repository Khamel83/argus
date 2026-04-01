# Argus

Stop wiring five different search APIs into every project. Argus is one endpoint that talks to SearXNG, Brave, Serper, Tavily, and Exa — with automatic fallback, result ranking, health tracking, and budget enforcement built in.

Connect any project via HTTP, CLI, MCP, or Python import. Add a provider key, it works. Remove it, it degrades gracefully.

## Quick Start

**Prerequisites:** Python 3.12+, PostgreSQL. Optionally [SearXNG](https://github.com/searxng/searxng) for free local search.

```bash
# Install
git clone https://github.com/khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Configure
cp .env.example .env
# Edit .env — at minimum set ARGUS_DB_URL and enable providers you have keys for

# Run
argus serve
```

Verify it's working:

```bash
# Health check
curl http://localhost:8000/api/health
# {"status":"ok"}

# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks 2025", "mode": "discovery", "max_results": 3}'
```

## Integration Examples

### HTTP API

```bash
# Discovery search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi tutorial", "mode": "discovery", "max_results": 5}'

# Recover a dead URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Expand with related links
curl -X POST http://localhost:8000/api/expand \
  -H "Content-Type: application/json" \
  -d '{"query": "system design patterns"}'

# Check health and budgets
curl http://localhost:8000/api/health/detail
curl http://localhost:8000/api/budgets
```

All endpoints return JSON. See `docs/search-operations.md` for full API reference.

### CLI

```bash
# Search (discovery mode by default)
argus search -q "python web framework"
argus search -q "python web framework" --mode research -n 20
argus search -q "https://dead.link" --mode recovery

# JSON output
argus search -q "fastapi" --json

# Admin
argus health
argus budgets
argus test-provider -p brave
argus test-provider -p searxng -q "climate change"
```

### MCP (for Claude, Cursor, or any MCP client)

Add to your MCP config:

**Claude Code** (`~/.claude.json`):
```json
{
  "mcpServers": {
    "argus": {
      "command": "argus",
      "args": ["mcp", "serve"],
      "transport": "stdio"
    }
  }
}
```

**Claude Desktop** (`claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "argus": {
      "command": "/path/to/.venv/bin/argus",
      "args": ["mcp", "serve"]
    }
  }
}
```

**Remote MCP** (SSE transport):
```json
{
  "mcpServers": {
    "argus": {
      "url": "http://localhost:8001/mcp"
    }
  }
}
```

MCP tools available: `search_web`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`

### Python

```python
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode

broker = create_broker()

# Discovery search
response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY, max_results=10)
)

for result in response.results:
    print(f"{result.title}: {result.url} (score: {result.score:.3f})")

# Check traces (which providers returned what)
for trace in response.traces:
    print(f"  {trace.provider}: {trace.status} ({trace.results_count} results, {trace.latency_ms}ms)")
```

## Why Argus?

**Without Argus:** Each project wires its own Brave/Serper/Tavily keys, handles rate limits, writes fallback logic, normalizes different response schemas, and tracks which provider is down today.

**With Argus:** One HTTP call. Argus picks the best provider, falls back automatically, ranks and deduplicates results, tracks budgets, and tells you exactly what happened via traces. Your project never touches a provider API directly.

- **One interface, five providers** — add/remove keys, your code doesn't change
- **Automatic fallback** — provider fails? Next one takes over. No code needed.
- **Budget enforcement** — set monthly limits per provider, Argus stops before you get billed
- **Health visibility** — always know which providers are up, degraded, or exhausted
- **Free floor** — SearXNG runs locally for free, always available as fallback
- **AI-native** — MCP server lets Claude, Cursor, or any agent search the web instantly

## Search Modes

| Mode | When to use | Provider chain |
|------|------------|---------------|
| `discovery` | Find related pages, canonical sources | searxng → brave → exa → tavily → serper |
| `recovery` | Dead/moved URL recovery | searxng → brave → serper → tavily → exa |
| `grounding` | Few live sources for fact-checking | brave → serper → searxng |
| `research` | Broad exploratory retrieval | tavily → exa → brave → serper |

## License

MIT
