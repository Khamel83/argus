#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl is not available on this host" >&2
    exit 1
fi

if [[ ! -d /run/systemd/system ]]; then
    echo "systemd is not running on this host" >&2
    exit 1
fi

run_as_root() {
    if [[ "${EUID}" -eq 0 ]]; then
        "$@"
    elif command -v sudo >/dev/null 2>&1; then
        sudo "$@"
    else
        echo "root privileges are required and sudo is not available" >&2
        exit 1
    fi
}

run_as_root install -m 0644 "$repo_root/argus.service" /etc/systemd/system/argus.service
run_as_root install -m 0644 "$repo_root/scripts/argus-mcp.service" /etc/systemd/system/argus-mcp.service
run_as_root systemctl daemon-reload
run_as_root systemctl enable --now argus.service argus-mcp.service

systemctl --no-pager --plain is-enabled argus.service argus-mcp.service
systemctl --no-pager --plain is-active argus.service argus-mcp.service
