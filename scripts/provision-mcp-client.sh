#!/usr/bin/env bash
# provision-mcp-client.sh — push Argus MCP config to a client machine (no argus install needed)
#
# Usage (from oci-dev, with secrets loaded):
#   eval $(cd ~/github/oneshot && secrets decrypt argus | grep -E 'ARGUS_REMOTE_URL|ARGUS_API_KEY' | sed 's/^/export /')
#   ./scripts/provision-mcp-client.sh khamel83@100.64.121.72   # MBA
#   ./scripts/provision-mcp-client.sh khamel83@100.113.216.27  # macmini
#   ./scripts/provision-mcp-client.sh local                    # this machine

set -euo pipefail

TARGET="${1:-}"
ARGUS_REMOTE_URL="${ARGUS_REMOTE_URL:-http://100.112.130.100:8271}"
ARGUS_API_KEY="${ARGUS_API_KEY:-}"
MCP_URL="${ARGUS_REMOTE_URL%/}/mcp"

if [[ -z "$ARGUS_API_KEY" ]]; then
    echo "Error: ARGUS_API_KEY is not set. Load from secrets first:" >&2
    echo "  eval \$(cd ~/github/oneshot && secrets decrypt argus | grep -E 'ARGUS_REMOTE_URL|ARGUS_API_KEY' | sed 's/^/export /')" >&2
    exit 1
fi

if [[ -z "$TARGET" ]]; then
    echo "Usage: $0 <user@host | local>" >&2
    echo "Known machines:" >&2
    echo "  khamel83@100.64.121.72   (MBA)" >&2
    echo "  khamel83@100.113.216.27  (macmini)" >&2
    echo "  local                    (this machine)" >&2
    exit 1
fi

SCRIPT=$(cat <<PYTHON
import json, os, re
from pathlib import Path

mcp_url = "${MCP_URL}"
api_key = "${ARGUS_API_KEY}"

# 1. ~/.claude.json (Claude Code, OpenCode, Cursor)
claude_path = Path.home() / ".claude.json"
try:
    data = json.loads(claude_path.read_text()) if claude_path.exists() and claude_path.stat().st_size else {}
except json.JSONDecodeError:
    data = {}
data.setdefault("mcpServers", {})["argus"] = {
    "type": "http",
    "url": mcp_url,
    "headers": {"Authorization": f"Bearer {api_key}"},
}
claude_path.write_text(json.dumps(data, indent=2) + "\n")
print(f"[claude.json] written: {mcp_url}")

# 2. ~/.codex/config.toml (Codex CLI)
codex_path = Path.home() / ".codex" / "config.toml"
if codex_path.parent.exists():
    toml_text = codex_path.read_text() if codex_path.exists() else ""
    new_section = f"\n[mcp_servers.argus]\nurl = \"{mcp_url}\"\nbearer_token_env_var = \"ARGUS_API_KEY\"\n"
    if "[mcp_servers.argus]" in toml_text:
        toml_text = re.sub(r"\n\[mcp_servers\.argus\][^\[]*", new_section, toml_text)
    else:
        toml_text = toml_text.rstrip("\n") + new_section
    codex_path.write_text(toml_text)
    print(f"[codex config.toml] written: {mcp_url}")
else:
    print("[codex config.toml] skipped — ~/.codex not found (Codex not installed here)")

# 3. ~/.zshrc / ~/.bashrc — export ARGUS_API_KEY for Codex
for rc_name in [".zshrc", ".bashrc"]:
    rc = Path.home() / rc_name
    if rc.exists():
        rc_text = rc.read_text()
        if "ARGUS_API_KEY" not in rc_text:
            with rc.open("a") as f:
                f.write(f"\n# Argus MCP bearer token\nexport ARGUS_API_KEY={api_key}\n")
            print(f"[{rc_name}] added ARGUS_API_KEY export")
        else:
            print(f"[{rc_name}] ARGUS_API_KEY already present")
        break

print("Done. Restart your AI clients to pick up the new config.")
PYTHON
)

if [[ "$TARGET" == "local" ]]; then
    python3 - <<< "$SCRIPT"
else
    ssh "$TARGET" "python3 -" <<< "$SCRIPT"
fi
