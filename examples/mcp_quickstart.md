# MCP quickstart

The fastest way to wire Argus into an MCP-aware client.

## One command (recommended)

After installing Argus with `pipx install "argus-search[mcp]"`:

```bash
argus mcp init --global --client all
```

That writes native config for Claude Code, Codex CLI, OpenCode, and Cursor. Restart the client afterward.

## Manual config — Claude Code, Cursor

Add to `~/.claude.json` (global) or `.mcp.json` (project):

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

If `argus` isn't on the client's `PATH`, use the absolute path:

```json
{
  "mcpServers": {
    "argus": {
      "command": "/home/you/.local/bin/argus",
      "args": ["mcp", "serve"]
    }
  }
}
```

## Manual config — Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.argus]
command = "argus"
args = ["mcp", "serve"]
```

## Manual config — OpenCode

Add to `~/.config/opencode/config.json` (global) or `.opencode/opencode.json` (project):

```json
{
  "mcp": {
    "argus": {
      "type": "local",
      "command": ["argus", "mcp", "serve"],
      "enabled": true
    }
  }
}
```

## Remote (over your network)

Run on one machine:

```bash
export ARGUS_API_KEY=$(openssl rand -hex 32)
argus mcp serve --transport streamable-http --host 0.0.0.0 --port 8001
```

On the client, point at the host and pass the bearer token. See the **MCP →
Option B** section of [../README.md](../README.md) for per-client config.

## Verify

```bash
argus mcp check
```

Then in your client, ask: *"What MCP tools do you have available?"* You should
see `search_web`, `extract_content`, `recover_url`, and friends.

For deeper per-client setup notes see [../docs/mcp-clients.md](../docs/mcp-clients.md).
