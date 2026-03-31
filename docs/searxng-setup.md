# SearXNG Setup

## Why SearXNG?

SearXNG is Argus's free local provider floor — a self-hosted metasearch engine that aggregates results from multiple search engines without sending data to third parties. It runs on your infrastructure and costs nothing per query.

## Quick Start (Docker)

```bash
docker run -d \
  --name searxng \
  -p 8080:8080 \
  -v searxng-data:/etc/searxng \
  searxng/searxng:latest
```

SearXNG will be available at `http://localhost:8080`.

## Configuration

Edit the SearXNG settings:

```bash
docker exec -it searxng sh
vi /etc/searxng/settings.yml
```

Key settings:

```yaml
search:
  safe_search: 0  # 0=off, 1=moderate, 2=strict
  autocomplete: ""
  default_lang: "en"
  formats:
    - html
    - json  # Required for Argus

server:
  secret_key: "<generate a random string>"
  bind_address: "127.0.0.1"
  port: 8080
  limiter: false  # Set to true in production
```

Restart after changes:

```bash
docker restart searxng
```

## Verify Argus Can Reach It

```bash
curl http://127.0.0.1:8080/search?q=test&format=json
```

You should see JSON output with `{"query": "test", "results": [...], ...}`.

## Argus Configuration

In your `.env`:

```bash
ARGUS_SEARXNG_ENABLED=true
ARGUS_SEARXNG_BASE_URL=http://127.0.0.1:8080
ARGUS_SEARXNG_TIMEOUT_SECONDS=12
```

If SearXNG runs on a different host (e.g., Docker network), use the container hostname:

```bash
ARGUS_SEARXNG_BASE_URL=http://searxng:8080
```

## Production Notes

- Bind SearXNG to `127.0.0.1` only — it should not be publicly accessible
- Enable the limiter (`limiter: true`) for production use
- Set a strong `secret_key`
- Consider adding a reverse proxy with rate limiting in front of SearXNG
- SearXNG instances on the same Docker network as Argus can use internal hostnames
