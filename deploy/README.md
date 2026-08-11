# Argus production deployment (Homelab authority)

The production authority is the Homelab Compose deployment, reached through
the canonical Tailscale HTTPS endpoints:

- HTTP authority: `https://homelab.deer-panga.ts.net:8443`
- MCP adapter: `https://homelab.deer-panga.ts.net:8443/mcp`

The MCP adapter is a stateless execution edge over the HTTP authority. The
deployed listener currently exposes the verified MCP `2025-11-25`
compatibility contract; the 2026-07-28 stateless revision is a separate
migration target documented in
`docs/research/2026-08-11-mcp-stateless-production-authority.md`.

See `docs/adr/0001-canonical-deployment.md` for historical context.

## Historical standalone Mac mini setup

The launchd commands below are for explicit standalone development or legacy
recovery only; they are not the current production deployment.

```bash
# one-time setup, from the service checkout
cd /Users/macmini/github/argus
git pull
uv sync --extra mcp
cp deploy/start-argus.sh deploy/start-argus-mcp.sh /Users/macmini/Library/Scripts/
chmod +x /Users/macmini/Library/Scripts/start-argus*.sh
cp deploy/com.argus.server.plist deploy/com.argus.mcp.plist /Users/macmini/Library/LaunchAgents/
launchctl load /Users/macmini/Library/LaunchAgents/com.argus.server.plist
launchctl load /Users/macmini/Library/LaunchAgents/com.argus.mcp.plist
```

Required `.env` in the service checkout (never committed):
`ARGUS_ENV=production`, `ARGUS_NODE_ROLE=primary`,
`ARGUS_EGRESS_TYPE=residential`, `ARGUS_MACHINE_NAME=omars-mac-mini`,
`ARGUS_PORT=8300`, `ARGUS_API_KEY=<generated>`,
`ARGUS_CALLER_TIER_CAPS=hermes*:1,mac-agents:1,maya:1`, plus provider keys.

Redeploy after a merge: `git pull && uv sync --extra mcp && launchctl kickstart -k gui/$(id -u)/com.argus.server && launchctl kickstart -k gui/$(id -u)/com.argus.mcp`
```
