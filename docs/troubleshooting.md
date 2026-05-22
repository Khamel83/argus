# Troubleshooting

If you can't find your issue here, run `argus doctor` and include its output
when opening a [bug report](https://github.com/Khamel83/argus/issues/new?template=bug_report.yml).

## Installation

**`pip install argus-search` succeeds but `argus` is not on PATH.**
`pipx install argus-search[mcp]` is preferable for end-user installs — it
isolates Argus and adds it to PATH automatically. If you stick with `pip`,
make sure the install location (e.g. `~/.local/bin`) is on your `$PATH`.

**`pip install argus-search[mcp]` complains about `mcp>=1.0.0`.**
You're on an old Python. Argus requires Python 3.11+; the MCP package needs
3.10+. Check `python --version`.

**Playwright errors at runtime (`Executable doesn't exist`).**
After install run `playwright install chromium`. Argus does not run this for
you — the extraction chain falls through to other steps when Playwright is
missing, but you'll see warnings.

## Search

**DuckDuckGo returns no results or starts erroring.**
DuckDuckGo is scraped and occasionally rate-limits aggressive callers. Argus
will mark it unhealthy after repeated failures and move on. Wait a few minutes,
or add a free API key for any tier 1 provider (Brave, Tavily, Exa, Linkup) so
the broker has a real fallback.

**A provider I configured isn't being called.**
Three gates run before any HTTP call:

1. **Enabled?** Limited API-key providers need both the key *and*
   `ARGUS_<PROVIDER>_ENABLED=true`. Run `argus health` to see who's enabled.
2. **Healthy?** Five consecutive failures triggers a 60-minute cooldown.
   `argus health` shows the current state.
3. **Budget?** Run `argus budgets`. Tier 1 (monthly) uses a 30-day rolling
   window; tier 3 (one-time) uses a lifetime counter that never resets.

If all three look fine and the provider still isn't called, run
`argus test-provider -p <name>` to bypass the broker and hit the adapter
directly.

**WolframAlpha returns empty for normal search queries.**
By design. WolframAlpha returns *computed* answers (math, units, facts) only,
and only in `grounding` and `research` modes. Web-style queries return empty
without a health penalty.

## MCP

**My MCP client doesn't see Argus tools after `argus mcp init`.**
Restart the client — MCP servers are loaded at client startup. For Claude Code
that means `claude` (or the Claude Code app) needs a full restart, not just
`/clear`. Run `argus mcp check` to verify the config Argus wrote.

**Remote MCP returns 401.**
`Authorization: Bearer <ARGUS_API_KEY>` (or `X-API-Key: ...`) is required for
non-loopback HTTP MCP. The key must match what the server sees in its env.
Codex CLI requires the key to be exported in your shell rc (`~/.zshrc`,
`~/.bashrc`) so `bearer_token_env_var` resolves at process start.

**`stdio` MCP startup spams log lines to the client.**
Fixed in 1.6.1 — upgrade. Argus no longer writes to stdout before JSON-RPC
initialization.

## Extraction

**`argus extract` returns "extraction failed" with no detail.**
Use `--json` for the full per-step trace, then file an
[extraction failure issue](https://github.com/Khamel83/argus/issues/new?template=extraction_failure.yml)
with the JSON output and the URL.

**Extraction works on my laptop but fails on the cloud VM.**
You probably have a datacenter IP. Many sites block datacenter ranges. Set
`ARGUS_EGRESS_TYPE=datacenter` so Argus knows, then either run a residential
worker on a home machine or rely more heavily on Jina/Wayback fallbacks. See
[CONTEXT.md](../CONTEXT.md) on topology awareness.

**Content comes back truncated with "Read more…" at the end.**
Argus' completeness check should catch this and fall through to the next step
when confidence is ≥ 85%. If it didn't, the heuristics missed your specific
truncation pattern — please open an extraction-failure issue with the URL.

## SearXNG

**SearXNG container won't start.**
Check the container logs (`docker compose logs argus`). SearXNG needs 512MB
of free RAM and a writable volume. Make sure
`ARGUS_SEARXNG_BASE_URL=http://127.0.0.1:8080` matches the port your container
exposes.

**SearXNG is healthy but Argus isn't using it.**
SearXNG is disabled by default. Set `ARGUS_SEARXNG_ENABLED=true` in `.env` and
restart `argus serve` (or rerun the CLI — settings are read per-process).

## Dashboard / HTTP API

**`/dashboard` returns 404.**
You're on a build before 1.6.x. Upgrade.

**Dashboard is unauthenticated.**
By design when no `ARGUS_ADMIN_API_KEY` is set. Treat the unauthenticated mode
as "trusted local only" — bind to `127.0.0.1`, not `0.0.0.0`, unless you've
set an admin key.

**Dashboard links break behind a reverse proxy.**
Set `ARGUS_ROOT_PATH=/argus` (or whatever subpath the proxy serves Argus on)
so HTMX fragment URLs and redirects use the public prefix.
