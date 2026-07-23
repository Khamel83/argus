#!/usr/bin/env bash
set -euo pipefail

export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

: "${ARGUS_AUTHORITY_URL:?ARGUS_AUTHORITY_URL is required}"
: "${ARGUS_AUTHORITY_TOKEN:?ARGUS_AUTHORITY_TOKEN is required}"

export ARGUS_MCP_HOST="${ARGUS_MCP_HOST:-0.0.0.0}"
export ARGUS_MCP_PORT="${ARGUS_MCP_PORT:-8001}"

exec argus mcp serve --transport streamable-http --host "$ARGUS_MCP_HOST" --port "$ARGUS_MCP_PORT"
