# Troubleshooting

Common issues and how to fix them.

## No providers available

**Symptom:** Search returns an error about no providers being available, or returns zero results for everything.

**Fix:**
1. Run `argus health` to see which providers are active
2. Check `.env` — at least one provider needs a valid API key (or SearXNG needs to be running)
3. Run `argus test-provider -p <name>` for each provider you expect to work
4. Check `argus budgets` — a provider may be budget-exhausted for the month

## Empty results

**Symptom:** Search succeeds but returns zero results.

**Causes:**
- All providers in the chain returned empty results for your query
- All providers are budget-exhausted or in cooldown
- Your query is too narrow or unusual

**Fix:**
1. Test individual providers: `argus test-provider -p brave`
2. Try a broader query
3. Check budgets: `argus budgets`
4. If a provider was temporarily disabled after failures, reset it: `argus provider reset-health brave`

## database is locked

**Symptom:** SQLite errors about the database being locked, especially under concurrent access.

**Cause:** Argus uses SQLite with WAL mode, which handles moderate concurrency but not heavy parallel access. This is a known design boundary — Argus is designed for single-user or small-team deployments.

**Fix:**
- Reduce the number of concurrent clients (e.g., don't run 10 MCP clients against the same instance)
- If you consistently hit this, you may be exceeding the intended use case

## MCP import error

**Symptom:** `argus mcp serve` fails with an import error about the `mcp` package.

**Fix:** Install with MCP support:
```bash
pip install -e ".[mcp]"
```

The `mcp` package is an optional dependency. The base install (`pip install -e .`) does not include it.

## SearXNG not returning results

**Symptom:** SearXNG is running but Argus gets no results from it.

**Common causes:**

1. **JSON format not enabled.** SearXNG must be configured to return JSON. In your SearXNG `settings.yml`:
   ```yaml
   search:
     formats:
       - html
       - json
   ```
   Restart SearXNG after changing this.

2. **Wrong URL.** Check `ARGUS_SEARXNG_BASE_URL` in `.env`. Default is `http://127.0.0.1:8080`. If SearXNG runs on a different port or host, update this.

3. **SearXNG itself is failing.** Try hitting SearXNG directly:
   ```bash
   curl "http://127.0.0.1:8080/search?q=test&format=json"
   ```
   If this fails, the issue is with SearXNG, not Argus.

See [providers.md](providers.md) for full SearXNG setup details.

## Session context not enriching follow-ups

**Symptom:** You're passing `session_id` but follow-up queries don't seem context-aware.

**Cause:** Context enrichment only fires for **short follow-up queries** (4 words or fewer, no question words like "what", "how", "why"). Longer queries are passed through unchanged — the assumption is that a long query already contains enough context.

**Example:**
- "fastapi" after "python web frameworks" → enriched (short follow-up)
- "what are the best fastapi deployment options" → not enriched (too long, contains question word)

Sessions expire after `ARGUS_SESSION_TTL_HOURS` (default: 168 hours / 7 days).

## Provider in cooldown

**Symptom:** A provider you expect to work is being skipped.

**Fix:**
1. Check health: `argus health` — look for `in_cooldown: true`
2. A provider enters cooldown after consecutive failures. It will auto-recover after the cooldown window
3. To force it back immediately: `argus provider reset-health <provider>`

## Rate limited

**Symptom:** HTTP 429 responses from the Argus API.

**Cause:** Default rate limit is 60 requests per 60-second window per client IP.

**Fix:** Adjust in `.env`:
```
ARGUS_RATE_LIMIT=120
ARGUS_RATE_LIMIT_WINDOW=60
```
