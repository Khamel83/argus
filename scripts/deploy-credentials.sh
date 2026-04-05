#!/usr/bin/env bash
# deploy-credentials.sh
#
# Pulls site login credentials from the SOPS vault and writes
# ~/.config/argus/credentials.json on the Mac Mini (chmod 600).
#
# Usage:
#   ./scripts/deploy-credentials.sh           # deploy to macmini
#   ./scripts/deploy-credentials.sh --dry-run # print JSON, don't deploy
#
# Credentials are stored in the vault under argus_auth.env.
# To update a password: secrets set argus_auth KEY=newvalue
#
# Sites and their vault keys:
#   nytimes.com      → NYTIMES_EMAIL / NYTIMES_PASSWORD
#   wsj.com          → WSJ_EMAIL / WSJ_PASSWORD
#   bloomberg.com    → BLOOMBERG_EMAIL / BLOOMBERG_PASSWORD
#   espn.com         → ESPN_EMAIL / ESPN_PASSWORD
#   latimes.com      → LATIMES_EMAIL / LATIMES_PASSWORD
#   chicagotribune   → Google auth — manual cookies only, not in this file

set -euo pipefail

DRY_RUN="${1:-}"
REMOTE_HOST="macmini"
REMOTE_PATH="/Users/macmini/.config/argus/credentials.json"

# Read vault into env
eval "$(secrets decrypt argus_auth 2>/dev/null | grep -E '^[A-Z_]+=.+' | sed 's/^/export /')" || {
    echo "ERROR: could not decrypt argus_auth vault" >&2
    exit 1
}

# Build JSON and validate
JSON_OUT=$(python3 - <<'PYEOF'
import json, os, sys

DOMAIN_MAP = {
    "NYTIMES":   "nytimes.com",
    "WSJ":       "wsj.com",
    "BLOOMBERG": "bloomberg.com",
    "ESPN":      "espn.com",
    "LATIMES":   "latimes.com",
}

creds = {}
missing = []
for prefix, domain in DOMAIN_MAP.items():
    email    = os.environ.get(f"{prefix}_EMAIL", "")
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    if email and password:
        creds[domain] = {"email": email, "password": password}
    else:
        missing.append(domain)

if missing:
    print(f"WARNING: no credentials for: {', '.join(missing)} (skipped)", file=sys.stderr)

print(json.dumps(creds, indent=2))
PYEOF
)

if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo "=== DRY RUN — would write to $REMOTE_HOST:$REMOTE_PATH ==="
    echo "$JSON_OUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for domain, c in d.items():
    print(f'  {domain}: {c[\"email\"]} / ***')
print(f'  ({len(d)} domains)')
"
    exit 0
fi

echo "Deploying credentials to $REMOTE_HOST..."
ssh "$REMOTE_HOST" "bash -s" <<SSHEOF
mkdir -p \$(dirname "$REMOTE_PATH")
cat > "$REMOTE_PATH" << 'JSONEOF'
$JSON_OUT
JSONEOF
chmod 600 "$REMOTE_PATH"
echo "Written: $REMOTE_PATH"
python3 -c "
import json
d = json.load(open('$REMOTE_PATH'))
for domain, c in d.items():
    print(f'  {domain}: {c[\"email\"]}')
print(f'  Total: {len(d)} domains')
"
SSHEOF

echo ""
echo "Done. Test a login:"
SERVICE_KEY="571647eb25034d7e6ec49e04defe3638df21ac0a8a5bcd37bb8ed1ec9397d0a3"
echo "  ssh macmini curl -s -X POST http://localhost:8910/login \\"
echo "    -H 'x-api-key: $SERVICE_KEY' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"domain\": \"nytimes.com\"}'"
