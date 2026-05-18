#!/usr/bin/env bash
# provision-mcp-client.sh — configure Argus MCP clients.
#
# Usage (with secrets loaded):
#   eval $(cd ~/github/oneshot && secrets decrypt argus | grep -E 'ARGUS_REMOTE_URL|ARGUS_API_KEY' | sed 's/^/export /')
#   ./scripts/provision-mcp-client.sh user@host             # remote machine
#   ./scripts/provision-mcp-client.sh local                 # this machine, local stdio if available

set -euo pipefail

TARGET="${1:-}"
ARGUS_REMOTE_URL="${ARGUS_REMOTE_URL:-http://localhost:8271}"
ARGUS_API_KEY="${ARGUS_API_KEY:-}"
ARGUS_LOCAL_COMMAND="${ARGUS_LOCAL_COMMAND:-$HOME/github/argus/.venv/bin/argus}"
MCP_URL="${ARGUS_REMOTE_URL%/}/mcp"

if [[ -z "$TARGET" ]]; then
    echo "Usage: $0 <user@host | local>" >&2
    exit 1
fi

MODE="remote"
if [[ "$TARGET" == "local" && -x "$ARGUS_LOCAL_COMMAND" ]]; then
    MODE="local"
elif [[ -z "$ARGUS_API_KEY" ]]; then
    echo "Error: ARGUS_API_KEY is not set and local Argus was not found at $ARGUS_LOCAL_COMMAND." >&2
    echo "Set ARGUS_LOCAL_COMMAND for local stdio, or load ARGUS_API_KEY for remote HTTP." >&2
    exit 1
fi

SCRIPT=$(cat <<PYTHON
import json, os, re
from pathlib import Path

mode = "${MODE}"
mcp_url = "${MCP_URL}"
api_key = "${ARGUS_API_KEY}"
local_command = "${ARGUS_LOCAL_COMMAND}"
local_argv = [local_command, "mcp", "serve"]

def claude_config():
    if mode == "local":
        return {
            "command": local_argv[0],
            "args": local_argv[1:],
        }
    return {
        "type": "http",
        "url": mcp_url,
        "headers": {"Authorization": f"Bearer {api_key}"},
    }

def opencode_config():
    if mode == "local":
        return {
            "type": "local",
            "command": local_argv,
            "enabled": True,
            "timeout": 10000,
        }
    return {
        "type": "remote",
        "url": mcp_url,
        "enabled": True,
        "headers": {"Authorization": f"Bearer {api_key}"},
    }

def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text()) if path.exists() and path.stat().st_size else {}
    except json.JSONDecodeError:
        return {}

# 1. Configuration files (Claude Code, Cursor, Claude Desktop)
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

    data = read_json(config_path)
    data.setdefault("mcpServers", {})["argus"] = claude_config()
    config_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"[{config_path.name}] written: argus ({mode})")

# 2. OpenCode user config
opencode_path = Path.home() / ".config" / "opencode" / "config.json"
if opencode_path.parent.exists():
    data = read_json(opencode_path)
    data.setdefault("mcp", {})["argus"] = opencode_config()
    opencode_path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"[opencode config.json] written: argus ({mode})")
else:
    print("[opencode config.json] skipped — ~/.config/opencode not found")

# 3. ~/.codex/config.toml (Codex CLI)
codex_path = Path.home() / ".codex" / "config.toml"
if codex_path.parent.exists():
    toml_text = codex_path.read_text() if codex_path.exists() else ""
    if mode == "local":
        new_section = (
            "\n[mcp_servers.argus]\n"
            f"command = \"{local_argv[0]}\"\n"
            "args = [\"mcp\", \"serve\"]\n"
        )
    else:
        new_section = (
            "\n[mcp_servers.argus]\n"
            f"url = \"{mcp_url}\"\n"
            "bearer_token_env_var = \"ARGUS_API_KEY\"\n"
        )
    if "[mcp_servers.argus]" in toml_text:
        toml_text = re.sub(r"(?ms)\n\[mcp_servers\.argus\].*?(?=\n\[|\Z)", new_section, toml_text)
    else:
        toml_text = toml_text.rstrip("\n") + new_section
    codex_path.write_text(toml_text)
    print(f"[codex config.toml] written: argus ({mode})")
else:
    print("[codex config.toml] skipped — ~/.codex not found (Codex not installed here)")

# 4. Gemini CLI only supports remote HTTP in this provisioning path.
import subprocess
if mode == "remote":
    try:
        subprocess.run(["gemini", "mcp", "add", "argus", mcp_url, "-t", "http", "-H", f"Authorization: Bearer {api_key}", "--trust", "--scope", "user"], capture_output=True)
        print(f"[gemini] added argus MCP: {mcp_url}")
    except FileNotFoundError:
        print("[gemini] skipped — gemini command not found")
else:
    print("[gemini] skipped — local stdio mode selected")

# 5. ~/.zshrc / ~/.bashrc — export ARGUS_API_KEY for remote Codex only.
if mode == "remote":
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
