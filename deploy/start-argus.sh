#!/bin/bash
# Argus HTTP API launch script for launchd (canonical mac mini deployment).
set -euo pipefail

ARGUS_DIR="/Users/macmini/github/argus"
VENV_DIR="${ARGUS_DIR}/.venv"

unset VIRTUAL_ENV PYTHONHOME PYTHONPATH PYTHONNOUSERSITE
export VIRTUAL_ENV="${VENV_DIR}"
export PATH="${VENV_DIR}/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "${ARGUS_DIR}"

exec "${VENV_DIR}/bin/argus" serve --host 0.0.0.0 --port 8300
