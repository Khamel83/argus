# Provider Setup

## Free Tier Summary

> Last verified: 2026-04-02. Free tier limits change — check each provider's pricing page before relying on them.

| Provider | Free tier | Notes |
|----------|-----------|-------|
| SearXNG | Unlimited (self-hosted) | Requires running a local Docker container |
| Brave Search | 2,000 queries/month | No credit card required |
| Serper | 2,500 queries/month | No credit card required |
| Tavily | 1,000 queries/month | No credit card required |
| Exa | 1,000 queries/month | No credit card required |
| Jina Reader | ~1M tokens (one-time) | Tokens consumed on extraction fallback |

Combined free-tier capacity: ~7,500 searches/month + unlimited self-hosted SearXNG. Enough for most personal and development use without a credit card.

Argus rotates away from a provider automatically when its monthly budget is hit. All providers are independent — losing one doesn't break search.

---


Each provider needs an API key set in `.env`. Unset keys are silently skipped.

## SearXNG (free, self-hosted)

No API key needed. Runs locally in Docker.

```bash
docker run -d --name searxng -p 8080:8080 searxng/searxng:latest
```

Verify it returns JSON:

```bash
curl 'http://localhost:8080/search?q=test&format=json'
```

### Tuning

Edit SearXNG settings inside the container:

```bash
docker exec -it searxng sh -c "vi /etc/searxng/settings.yml"
docker restart searxng
```

Key settings:

```yaml
search:
  formats:
    - json        # Required for Argus
  safe_search: 0  # 0=off, 1=moderate, 2=strict

server:
  bind_address: "127.0.0.1"
  limiter: false  # Set to true in production
```

### Docker Networking

If SearXNG and Argus are on the same Docker network, use the container hostname:

```
ARGUS_SEARXNG_BASE_URL=http://searxng:8080
```

The included `docker-compose.yml` handles this automatically.

## Brave Search

Free tier: 2,000 queries/month.

1. Go to [brave.com/search/api](https://brave.com/search/api/)
2. Sign up → get API key
3. Set in `.env`:

```
ARGUS_BRAVE_API_KEY=BSA...
```

## Serper

Free tier: 2,500 queries/month.

1. Go to [serper.dev](https://serper.dev)
2. Sign up → copy API key from dashboard
3. Set in `.env`:

```
ARGUS_SERPER_API_KEY=abc...
```

## Tavily

Free tier: 1,000 queries/month.

1. Go to [app.tavily.com](https://app.tavily.com/sign-up)
2. Sign up → copy API key
3. Set in `.env`:

```
ARGUS_TAVILY_API_KEY=tvly-...
```

## Exa

Free tier: 1,000 queries/month.

1. Go to [dashboard.exa.ai](https://dashboard.exa.ai/signup)
2. Sign up → copy API key
3. Set in `.env`:

```
ARGUS_EXA_API_KEY=...
```

## Budgets

Set monthly spend limits per provider in `.env`:

```
ARGUS_BRAVE_MONTHLY_BUDGET_USD=5
ARGUS_SERPER_MONTHLY_BUDGET_USD=0   # 0 = unlimited
```

When a provider hits its budget, it's automatically skipped until next month.
