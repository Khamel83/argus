#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/ubuntu/github/argus"
SERVICE_NAME="argus"

echo "=== Deploying Argus ==="

cd "$REPO_DIR"
git pull origin main

source .venv/bin/activate
pip install -e ".[mcp]"

sudo cp deploy/argus.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable argus
sudo systemctl restart argus

echo "Waiting for health check..."
sleep 3
if curl -sf http://127.0.0.1:8000/api/health > /dev/null; then
    echo "OK: Argus is healthy"
    sudo systemctl status argus --no-pager -l
else
    echo "WARN: Health check failed, check logs:"
    sudo journalctl -u argus --no-pager -n 30
fi
