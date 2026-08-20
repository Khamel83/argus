# MCP Client Setup

Argus supports local stdio MCP and remote streamable HTTP MCP. Both are
stateless execution adapters over the authenticated Argus HTTP API. They do not own a
broker, provider credentials, browser, database, budgets, sessions, health
state, or the Maya outbox.

The canonical production clients use the remote HTTPS listener. Local stdio is
an explicit standalone/development choice for a client running beside Argus;
it is not the production default. The deployed listener supports both the
legacy `2025-11-25` compatibility contract and the MCP `2026-07-28`
stateless revision. The latter is a one-shot request path: it does not require
an initialize handshake or `Mcp-Session-Id`. Keep the version matrix and direct
no-spend/restart probes in
[`docs/research/2026-08-11-mcp-stateless-production-authority.md`](research/2026-08-11-mcp-stateless-production-authority.md)
pass.

For local stdio, the process must inherit a scoped HTTP authority credential:

```bash
export ARGUS_AUTHORITY_URL=http://argus-api:8000
export ARGUS_AUTHORITY_TOKEN=replace-with-a-scoped-caller-token
```

An in-process broker is available only for explicit standalone development
with `ARGUS_MCP_STANDALONE=true`; production rejects that mode.

Use remote streamable HTTP when one Argus server should serve other machines over
Tailscale, a private LAN, or another trusted network. Remote mode should use
`ARGUS_API_KEY` and the canonical production listener should be configured as
`https://homelab.deer-panga.ts.net:8443/mcp`.

### Homelab over Tailscale Serve

On the Mac mini, use the Tailscale Serve HTTPS listener rather than a Docker
loopback port or a raw tailnet IP:

```bash
export ARGUS_REMOTE_URL=https://homelab.deer-panga.ts.net:8443
export ARGUS_API_KEY=replace-with-the-existing-Argus-client-token
scripts/provision-mcp-client.sh local
```

Pass either the listener base URL or its existing `/mcp` endpoint: the helper
normalizes it to exactly one `/mcp` suffix. A missing `ARGUS_REMOTE_URL` is an
error; the helper intentionally never falls back to `localhost`.
`127.0.0.1:8271` exists only on Homelab, and
`https://100.112.130.100:8443` cannot validate the Tailscale TLS certificate
because it omits the issued hostname.

Before supplying a token, this no-secret check should return `401 Unauthorized`:

```bash
curl -I https://homelab.deer-panga.ts.net:8443/mcp
```

That response proves the Mac-to-Homelab Tailscale route, TLS name, and MCP
listener are working. A connection error, certificate error, or any endpoint
other than the HTTPS hostname above is a client-routing problem, not an Argus
provider problem.

### ChatGPT

The ChatGPT cloud service is not a Tailscale peer, so it cannot use the
private Homelab URL directly. Keep Argus private and use an
[OpenAI Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)
instead. Create and associate a tunnel in Platform settings, then run
`tunnel-client` inside the network with that `tunnel_id`, a runtime API key,
and the private Argus MCP URL. In ChatGPT Developer Mode, choose **Tunnel** and
select or paste that `tunnel_id`; do not configure ChatGPT with the Homelab
URL or a raw tailnet IP. This requires Platform Tunnels Read + Use (and
Manage to create the tunnel) plus ChatGPT developer-mode access.

## Local stdio

Install Argus with MCP support:

```bash
pipx install 'argus-search[mcp]'
```

Configure the current user:

```bash
argus mcp init --global --client all
```

That command writes:

| Client | File | Shape |
|--------|------|-------|
| Claude Code | `~/.claude.json` | `mcpServers.argus.command + args` |
| Codex CLI | `~/.codex/config.toml` | `[mcp_servers.argus] command + args` |
| OpenCode | `~/.config/opencode/config.json` | `mcp.argus.type = "local"` |
| Cursor | `~/.cursor/mcp.json` when `~/.cursor/` exists | `mcpServers.argus.command + args` |

Restart the client after changing MCP config.

## Verify

```bash
argus --version
argus mcp check
codex mcp list
claude mcp list
opencode mcp list --print-logs
```

Expected results:

- `argus --version` reports the same version as `pyproject.toml`.
- Codex lists `argus` with `mcp serve`.
- Claude Code shows `argus ... Connected`.
- OpenCode shows `argus connected` and loads the Argus tools.
- Argus startup logs appear on stderr, never stdout, so stdio JSON-RPC handshakes remain clean.

## Remote HTTP

The examples below show a generic private listener. For the production
Homelab deployment, substitute the canonical HTTPS URL above.

Run Argus on the server:

```bash
export ARGUS_API_KEY=replace-with-a-long-random-secret
export ARGUS_AUTHORITY_URL=http://argus-api:8000
export ARGUS_AUTHORITY_TOKEN="$ARGUS_API_KEY"
argus mcp serve --transport streamable-http --host 100.x.x.x --port 8001
```

The remote MCP listener credential must also be a valid scoped credential at
the HTTP authority. The adapter forwards each authenticated bearer token
unchanged so identity and provider-tier policy remain end to end.

Configure clients:

```bash
ARGUS_REMOTE_URL=https://homelab.deer-panga.ts.net:8443 ARGUS_API_KEY=replace-with-an-Argus-client-token argus mcp init --global --client all
```

The direct-listener server command above applies only when the server is
explicitly bound to that address. For Homelab client configuration, use the
HTTPS Tailscale Serve base URL above.
Codex stores only the environment variable name in `~/.codex/config.toml`;
export `ARGUS_API_KEY` in the environment that launches Codex.

## Provision another machine

From a checked-out repo:

```bash
scripts/provision-mcp-client.sh local
```

In local mode the script uses stdio when it can find an executable Argus at `$ARGUS_LOCAL_COMMAND` or `$HOME/github/argus/.venv/bin/argus`.

For remote HTTP:

```bash
export ARGUS_REMOTE_URL=https://homelab.deer-panga.ts.net:8443
export ARGUS_API_KEY=replace-with-the-existing-Argus-client-token
scripts/provision-mcp-client.sh local
```

The explicit remote URL is mandatory. To provision another trusted machine,
run the helper from a trusted provisioning machine with its SSH target; it
writes the same normalized remote configuration on that target.

## Troubleshooting

If a client reports an initialize or handshake failure:

1. Run the client-specific list command above.
2. Confirm the configured `command` exists and is executable.
3. Confirm `ARGUS_AUTHORITY_URL` and `ARGUS_AUTHORITY_TOKEN` are present in the environment that launches the adapter.
4. Run `argus --version`; if it reports an old version, reinstall with `pipx upgrade argus-search` or reinstall from the current checkout.
5. Confirm no log lines are printed to stdout before MCP JSON-RPC messages. Argus logs should appear on stderr.
6. For Codex, inspect `~/.codex/config.toml` and ensure the Argus section contains only valid TOML:

```toml
[mcp_servers.argus]
command = "argus"
args = ["mcp", "serve"]
```

For OpenCode local mode:

```json
{
  "mcp": {
    "argus": {
      "type": "local",
      "command": ["argus", "mcp", "serve"],
      "enabled": true,
      "timeout": 10000
    }
  }
}
```
