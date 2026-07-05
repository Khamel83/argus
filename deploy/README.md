# Argus canonical deployment (mac mini)

See `docs/adr/0001-canonical-deployment.md` for why.

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
`ARGUS_CALLER_TIER_CAPS=clio*:1,hermes*:1`, plus provider keys.

Redeploy after a merge: `git pull && uv sync --extra mcp && launchctl kickstart -k gui/$(id -u)/com.argus.server && launchctl kickstart -k gui/$(id -u)/com.argus.mcp`
```
