#!/usr/bin/env bash
set -euo pipefail

export PATH="/home/ubuntu/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

_load_vault() {
    local name="$1"
    while IFS= read -r line; do
        [[ -z "$line" || "$line" == "#"* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        export "$key=$value"
    done < <(secrets decrypt "$name")
}

_load_vault argus_keys
_load_vault research_keys
_load_vault argus_auth

export ARGUS_SEARXNG_BASE_URL="http://127.0.0.1:8080"

exec argus mcp serve --transport streamable-http --host 0.0.0.0 --port 8001
