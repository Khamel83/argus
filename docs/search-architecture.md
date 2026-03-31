# Argus Search Architecture

## Overview

Argus is a standalone search broker that routes queries across multiple web search providers through a single normalized interface.

```
                    ┌─────────────────┐
                    │   Downstream    │
                    │  (HTTP / CLI /  │
                    │   MCP / Import) │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Argus API     │
                    │   (FastAPI)     │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Broker        │
                    │ ┌────────────┐  │
                    │ │  Router    │  │
                    │ │  Policies  │  │
                    │ │  Health    │  │
                    │ │  Budget    │  │
                    │ └────────────┘  │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
       ┌──────▼──────┐ ┌────▼─────┐ ┌─────▼──────┐
       │   Cache     │ │  Rank    │ │  Persist   │
       │  (memory)   │ │  (RRF)   │ │  (Postgres)│
       └─────────────┘ └──────────┘ └────────────┘
              │              │
              │       ┌──────▼──────┐
              │       │   Dedupe   │
              │       │  (URL norm)│
              │       └──────┬──────┘
              │              │
    ┌─────────▼──────────────▼──────────────┐
    │           Provider Adapters           │
    ├────────┬────────┬───────┬──────┬──────┤
    │SearXNG │ Brave  │Serper │Tavily│ Exa  │
    │(local) │(API)   │(API)  │(API) │(API) │
    └────────┴────────┴───────┴──────┴──────┘
```

## Search Modes

| Mode | Purpose | Provider Order |
|------|---------|---------------|
| recovery | Find a moved/dead URL | cache → searxng → brave → serper → tavily → exa |
| discovery | Find related pages | cache → searxng → brave → exa → tavily → serper |
| grounding | Get supporting sources | cache → brave → serper → searxng |
| research | Broad exploratory search | cache → tavily → exa → brave → serper |

## Provider Interface

All providers implement `BaseProvider`:

```python
class BaseProvider(ABC):
    @property
    def name(self) -> ProviderName: ...

    def is_available(self) -> bool: ...

    def status(self) -> ProviderStatus: ...

    async def search(self, query: SearchQuery) -> tuple[List[SearchResult], ProviderTrace]: ...
```

## Result Model

Every result from every provider is normalized to `SearchResult`:

- `url` — canonical URL
- `title` — page title
- `snippet` — content excerpt
- `domain` — extracted domain
- `provider` — source provider
- `score` — RRF fused score
- `raw_rank` — provider's original rank
- `metadata` — provider-specific fields

## Ranking: Reciprocal Rank Fusion

Results from multiple providers are merged using RRF:

```
score(d) = Σ  1 / (k + rank_provider(d))
```

Where `k=60`. Results are then deduplicated by normalized URL.

## URL Deduplication

- Lowercase scheme and domain
- Strip `www.` prefix
- Remove trailing slashes
- Strip tracking parameters (utm_, ref=, fbclid, gclid)
- Sort remaining query parameters

## Health Tracking

Each provider tracks:
- Consecutive failures
- Last success/failure timestamps
- Cooldown window (triggered after N consecutive failures)

States: enabled → degraded → temporarily_disabled → (cooldown expires) → degraded

## Budget Tracking

Each provider tracks:
- Monthly query count
- Estimated cost per query
- Remaining budget
- Exhausted flag

When budget is exhausted, the provider is skipped.
