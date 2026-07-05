#!/bin/bash
# Argus remote MCP (streamable-http) launch script for launchd.
set -euo pipefail

ARGUS_DIR="/Users/macmini/github/argus"
VENV_DIR="${ARGUS_DIR}/.venv"

unset VIRTUAL_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE
export VIRTUAL_ENV="${VENV_DIR}"
export PATH="${VENV_DIR}/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "${ARGUS_DIR}"

exec "${VENV_DIR}/bin/argus" mcp serve --transport streamable-http --host 0.0.0.0 --port 8301
