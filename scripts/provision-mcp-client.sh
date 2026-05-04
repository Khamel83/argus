#!/usr/bin/env bash
# provision-mcp-client.sh — push Argus MCP config to a client machine (no argus install needed)
#
# Usage (with secrets loaded):
#   eval $(cd ~/github/oneshot && secrets decrypt argus | grep -E 'ARGUS_REMOTE_URL|ARGUS_API_KEY' | sed 's/^/export /')
#   ./scripts/provision-mcp-client.sh user@host             # remote machine
#   ./scripts/provision-mcp-client.sh local                 # this machine

set -euo pipefail

TARGET="${1:-}"
ARGUS_REMOTE_URL="${ARGUS_REMOTE_URL:-http://localhost:8271}"
ARGUS_API_KEY="${ARGUS_API_KEY:-}"
MCP_URL="${ARGUS_REMOTE_URL%/}/mcp"

if [[ -z "$ARGUS_API_KEY" ]]; then
    echo "Error: ARGUS_API_KEY is not set. Load from secrets first." >&2
    exit 1
fi

if [[ -z "$TARGET" ]]; then
    echo "Usage: $0 <user@host | local>" >&2
    exit 1
fi

SCRIPT=$(cat <<PYTHON
import json, os, re
from pathlib import Path

mcp_url = "${MCP_URL}"
api_key = "${ARGUS_API_KEY}"

# 1. Configuration files (Claude Code, OpenCode, Cursor, Claude Desktop)
config_paths = [
    Path.home() / ".claude.json",
    Path.home() / ".cursor" / "mcp.json",
]

# Add Claude Desktop based on OS
if os.uname().sysname == "Darwin":
    config_paths.append(Path.home() / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json")
else:
    config_paths.append(Path.home() / ".config" / "Claude" / "claude_desktop_config.json")

for config_path in config_paths:
    # Only write if the directory exists (except for project-level .mcp.json which we don't do here)
    if not config_path.parent.exists():
        continue
    
    try:
        data = json.loads(config_path.read_text()) if config_path.exists() and config_path.stat().st_size else {}
    except json.JSONDecodeError:
        data = {}
    
    data.setdefault("mcpServers", {})["argus"] = {
        "type": "http",
        "url": mcp_url,
        "headers": {"Authorization": f"Bearer {api_key}"},
    }
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"[{config_path.name}] written: {mcp_url}")

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

# 3. Gemini CLI
import subprocess
try:
    subprocess.run(["gemini", "mcp", "add", "argus", mcp_url, "-t", "http", "-H", f"Authorization: Bearer {api_key}", "--trust", "--scope", "user"], capture_output=True)
    print(f"[gemini] added argus MCP: {mcp_url}")
except FileNotFoundError:
    print("[gemini] skipped — gemini command not found")

# 4. ~/.zshrc / ~/.bashrc — export ARGUS_API_KEY for Codex
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
