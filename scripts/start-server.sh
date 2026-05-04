#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:/usr/local/bin:/usr/bin:/bin:$PATH"

_load_env_file() {
    local file="$1"
    [[ -f "$file" ]] || return 0

    set -a
    # shellcheck disable=SC1090
    source "$file"
    set +a
}

_load_vault() {
    local name="$1"
    command -v secrets >/dev/null 2>&1 || return 0

    while IFS= read -r line; do
        [[ -z "$line" || "$line" == "#"* ]] && continue
        key="${line%%=*}"
        value="${line#*=}"
        export "$key=$value"
    done < <(secrets decrypt "$name" 2>/dev/null || true)
}

_load_env_file "$repo_root/.env"
_load_vault argus_keys
_load_vault research_keys
_load_vault argus_auth

export ARGUS_SEARXNG_BASE_URL="${ARGUS_SEARXNG_BASE_URL:-http://127.0.0.1:8080}"
export ARGUS_HOST="${ARGUS_HOST:-0.0.0.0}"
export ARGUS_PORT="${ARGUS_PORT:-8005}"

exec argus serve --host "$ARGUS_HOST" --port "$ARGUS_PORT"
