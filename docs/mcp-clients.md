# MCP Client Setup

Argus supports local stdio MCP and remote streamable HTTP MCP. Both are
stateless adapters over the authenticated Argus HTTP API. They do not own a
broker, provider credentials, browser, database, budgets, sessions, health
state, or the Maya outbox.

Use local stdio when Argus is installed on the same machine as the AI client.
This is the default for Claude Code, Codex CLI, OpenCode, Cursor, and similar
desktop/terminal clients. It requires no MCP listener key, but the process must
inherit a scoped HTTP authority credential:

```bash
export ARGUS_AUTHORITY_URL=http://argus-api:8000
export ARGUS_AUTHORITY_TOKEN=replace-with-a-scoped-caller-token
```

An in-process broker is available only for explicit standalone development
with `ARGUS_MCP_STANDALONE=true`; production rejects that mode.

Use remote streamable HTTP when one Argus server should serve other machines over Tailscale, a private LAN, or another trusted network. Remote mode should use `ARGUS_API_KEY`.

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
ARGUS_REMOTE_URL=http://100.x.x.x:8001 ARGUS_API_KEY=replace-with-a-long-random-secret argus mcp init --global --client all
```

Codex stores only the environment variable name in `~/.codex/config.toml`; export `ARGUS_API_KEY` in the shell that launches Codex.

## Provision another machine

From a checked-out repo:

```bash
scripts/provision-mcp-client.sh local
```

In local mode the script uses stdio when it can find an executable Argus at `$ARGUS_LOCAL_COMMAND` or `$HOME/github/argus/.venv/bin/argus`.

For remote HTTP:

```bash
export ARGUS_REMOTE_URL=http://100.x.x.x:8001
export ARGUS_API_KEY=replace-with-a-long-random-secret
scripts/provision-mcp-client.sh user@host
```

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
