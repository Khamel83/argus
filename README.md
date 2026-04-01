# Argus

Stop wiring search APIs into every project. Argus is one endpoint that talks to SearXNG, Brave, Serper, Tavily, and Exa — with automatic fallback, result ranking, health tracking, and budget enforcement. Connect via HTTP, CLI, MCP, or Python import. Add a provider key, it works. Remove it, it degrades gracefully.

## What It Does

You pass Argus a search query. It fans out to multiple providers, collects results, ranks them, deduplicates, and returns a clean list. If a provider is down or over budget, it skips it automatically. Your project never touches a provider API directly.

The output is designed for LLM consumption — enough context (title, snippet, URL, domain, relevance score) for a model to decide "this link is worth reading" without downloading the whole page.

## Quick Start

### Docker (recommended)

```bash
# 1. Create .env with your provider keys
cp .env.example .env
# Edit .env — set ARGUS_DB_URL and at least one provider key

# 2. Start Argus + Postgres + SearXNG
docker compose up -d

# 3. Verify
curl http://localhost:8000/api/health
# {"status":"ok"}

curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "fastapi tutorial", "mode": "discovery"}'
```

### Local install

```bash
git clone https://github.com/Khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
cp .env.example .env
pip install -e ".[mcp]"
argus serve
```

## Provider Setup

All you need is API keys for whichever providers you want. SearXNG is free and runs locally. The rest have generous free tiers.

| Provider | Free tier | Get a key |
|----------|----------|-----------|
| [SearXNG](https://github.com/searxng/searxng) | Unlimited (self-hosted) | No key needed — runs in Docker |
| [Brave Search](https://brave.com/search/api/) | 2,000 queries/month | [dashboard](https://brave.com/search/api/) |
| [Serper](https://serper.dev) | 2,500 queries/month | [signup](https://serper.dev/signup) |
| [Tavily](https://tavily.com) | 1,000 queries/month | [signup](https://app.tavily.com/sign-up) |
| [Exa](https://exa.ai) | 1,000 queries/month | [signup](https://dashboard.exa.ai/signup) |

Set keys in `.env`:
```
ARGUS_BRAVE_API_KEY=BSA...
ARGUS_SERPER_API_KEY=abc...
ARGUS_TAVILY_API_KEY=tvly-...
ARGUS_EXA_API_KEY=...
```

Unset or blank keys are silently skipped. You can run Argus with just SearXNG and no paid keys at all.

### SearXNG Setup

The included `docker-compose.yml` starts SearXNG automatically. If running separately:

```bash
docker run -d --name searxng -p 8080:8080 searxng/searxng:latest
curl http://localhost:8080/search?q=test\&format=json
```

See [docs/providers.md](docs/providers.md) for SearXNG tuning and Docker networking details.

## Integration

### HTTP API

```bash
# Search
curl -X POST http://localhost:8000/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "python web frameworks", "mode": "discovery", "max_results": 5}'

# Recover a dead URL
curl -X POST http://localhost:8000/api/recover-url \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/old-page", "title": "Example Article"}'

# Health & budgets
curl http://localhost:8000/api/health/detail
curl http://localhost:8000/api/budgets
```

OpenAPI docs available at `http://localhost:8000/docs`.

### CLI

```bash
argus search -q "python web framework"
argus search -q "python web framework" --mode research -n 20
argus recover-url -u "https://dead.link" -t "Page Title"
argus health
argus test-provider -p brave
```

### MCP

Add to your MCP client config:

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

Tools: `search_web`, `recover_url`, `expand_links`, `search_health`, `search_budgets`, `test_provider`

### Python

```python
from argus.broker.router import create_broker
from argus.models import SearchQuery, SearchMode

broker = create_broker()
response = await broker.search(
    SearchQuery(query="python web frameworks", mode=SearchMode.DISCOVERY, max_results=10)
)
for r in response.results:
    print(f"{r.title}: {r.url} (score: {r.score:.3f})")
```

## Search Modes

| Mode | When to use | Provider chain |
|------|------------|---------------|
| `discovery` | Find related pages, canonical sources | searxng → brave → exa → tavily → serper |
| `recovery` | Dead/moved URL recovery | searxng → brave → serper → tavily → exa |
| `grounding` | Few live sources for fact-checking | brave → serper → searxng |
| `research` | Broad exploratory retrieval | tavily → exa → brave → serper |

## Configuration

All config via environment variables. See `.env.example` for the full list.

Key variables:
- `ARGUS_DB_URL` — PostgreSQL connection string (required)
- `ARGUS_SEARXNG_BASE_URL` — SearXNG endpoint (default: `http://127.0.0.1:8080`)
- `ARGUS_BRAVE_API_KEY`, `ARGUS_SERPER_API_KEY`, etc. — provider keys
- `ARGUS_CACHE_TTL_HOURS` — result cache TTL (default: 168)
- `ARGUS_BRAVE_MONTHLY_BUDGET_USD` — per-provider monthly spend limit

## Roadmap

Theoretical — shaped by what's actually useful, not a fixed plan.

**Now** — Search broker with fallback, health, budgets. Output is ranked URLs with snippets. The caller decides what to fetch next.

**Soon** — Content extraction layer. Given a URL from search results, fetch and extract clean text so the caller gets the content, not just a link. This closes the loop: search → identify useful link → extract → answer.

**Later** — Multi-turn search context. Remember previous queries in a session, refine results based on what was useful. Conversational search that gets better as you narrow down.

**Maybe** — Provider-specific tuning (use Exa for academic, Brave for general), query rewriting to improve recall, result caching across sessions, embedding-based dedup.

## License

MIT
