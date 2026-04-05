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
# One-time vault setup (run these once to store credentials):
#   secrets set argus_auth NYTIMES_EMAIL=your@email.com
#   secrets set argus_auth NYTIMES_PASSWORD=yourpassword
#   secrets set argus_auth WSJ_EMAIL=your@email.com
#   secrets set argus_auth WSJ_PASSWORD=yourpassword
#   secrets set argus_auth ESPN_EMAIL=your@email.com
#   secrets set argus_auth ESPN_PASSWORD=yourpassword
#   secrets set argus_auth LATIMES_EMAIL=your@email.com
#   secrets set argus_auth LATIMES_PASSWORD=yourpassword
#   secrets set argus_auth CHICAGOTRIBUNE_EMAIL=your@email.com
#   secrets set argus_auth CHICAGOTRIBUNE_PASSWORD=yourpassword

set -euo pipefail

DRY_RUN="${1:-}"
REMOTE_HOST="macmini"
REMOTE_PATH="/Users/macmini/.config/argus/credentials.json"

# Read vault into env
eval "$(secrets decrypt argus_auth 2>/dev/null | grep -E '^[A-Z_]+=.+' | sed 's/^/export /')" || true

# Build JSON from vault vars — mapping: PREFIX → domain
python3 - <<'PYEOF'
import json, os, sys

DOMAIN_MAP = {
    "NYTIMES":        "nytimes.com",
    "WSJ":            "wsj.com",
    "ESPN":           "espn.com",
    "LATIMES":        "latimes.com",
    "CHICAGOTRIBUNE": "chicagotribune.com",
}

creds = {}
missing = []
for prefix, domain in DOMAIN_MAP.items():
    email    = os.environ.get(f"{prefix}_EMAIL", "")
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    if email and password:
        creds[domain] = {"email": email, "password": password}
    else:
        missing.append(f"{prefix}_EMAIL / {prefix}_PASSWORD")

if missing:
    print(f"WARNING: missing vault keys for: {', '.join(missing)}", file=sys.stderr)

print(json.dumps(creds, indent=2))
PYEOF

# Capture JSON output from python
JSON_OUT=$(python3 - <<'PYEOF'
import json, os, sys

DOMAIN_MAP = {
    "NYTIMES":        "nytimes.com",
    "WSJ":            "wsj.com",
    "ESPN":           "espn.com",
    "LATIMES":        "latimes.com",
    "CHICAGOTRIBUNE": "chicagotribune.com",
}

creds = {}
for prefix, domain in DOMAIN_MAP.items():
    email    = os.environ.get(f"{prefix}_EMAIL", "")
    password = os.environ.get(f"{prefix}_PASSWORD", "")
    if email and password:
        creds[domain] = {"email": email, "password": password}

print(json.dumps(creds, indent=2))
PYEOF
)

if [[ "$DRY_RUN" == "--dry-run" ]]; then
    echo "=== DRY RUN — would write to $REMOTE_HOST:$REMOTE_PATH ==="
    echo "$JSON_OUT"
    exit 0
fi

echo "Deploying credentials to $REMOTE_HOST..."
ssh "$REMOTE_HOST" "
  mkdir -p \$(dirname $REMOTE_PATH)
  cat > $REMOTE_PATH << 'EOF'
$JSON_OUT
EOF
  chmod 600 $REMOTE_PATH
  echo \"Written: $REMOTE_PATH\"
"

echo "Done. Run this to test a login:"
echo "  ssh $REMOTE_HOST curl -s -X POST http://localhost:8910/login \\"
echo "    -H 'x-api-key: \$(grep ARGUS_EXTRACT_SERVICE_KEY $REMOTE_HOST:~/argus-extract-service/com.argus.extract-service.plist | awk -F'<string>|</string>' '{print \$2}')' \\"
echo "    -H 'Content-Type: application/json' -d '{\"domain\":\"nytimes.com\"}'"
