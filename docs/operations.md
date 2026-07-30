# Argus production operations

Last production-safe walkthrough: **2026-07-29**

This is the canonical operator guide for Argus. Homelab Docker is the sole
production execution authority. The Mac is source and development only. Maya
owns user-visible retrieval history; Argus owns retrieval execution and its
PostgreSQL evidence. The former Mac launchd authority, OCI authority, Clio
caller, and host residential worker are retired and are not fallbacks.

## Production topology

| Surface | Production address | Access |
|---|---|---|
| HTTP API | `https://homelab.deer-panga.ts.net/` | Tailnet plus caller credential |
| MCP | `https://homelab.deer-panga.ts.net:8443/mcp` | Tailnet plus caller credential |
| Host HTTP backend | `127.0.0.1:8270` | Homelab loopback only |
| Host MCP backend | `127.0.0.1:8271` | Homelab loopback only |
| API container | `argus:8000` | Docker networks only |
| MCP container | `argus-mcp:8001` | Docker networks only |
| PostgreSQL | `atlas-postgres:5432/argus` | Docker network only |
| SearXNG | `searxng:8080` | Docker network only |

Tailscale Serve is the only remote ingress. Funnel is disabled for Argus and
there is no Cloudflare route. The API and MCP require `ARGUS_API_KEY`.
Privileged `/api/admin/*` routes require the distinct
`ARGUS_ADMIN_API_KEY`. Secrets are SOPS-encrypted in the Homelab repository
and rendered to the host `.env`; never copy values into logs or issues.

The API has a 1 GiB memory limit, equal swap limit, 256-process limit,
256 MiB shared memory, `no-new-privileges`, and the checked-in Playwright
seccomp profile. The disabled `argus-residential.service` is retained only as
reversible evidence; it must remain disabled and inactive.

## Read-only health and identity

Run from an operator workstation:

```bash
ssh homelab 'sudo docker ps --format "{{.Names}} {{.Image}} {{.Status}}" |
  grep -E "^(argus|argus-mcp|searxng|atlas-postgres) "'
ssh homelab 'sudo ss -ltnp | grep -E ":(8270|8271|8124|5432)\b" || true'
ssh homelab 'tailscale serve status'
ssh homelab 'sudo systemctl is-enabled argus-residential.service;
  sudo systemctl is-active argus-residential.service'
```

Expected: `argus` and `argus-mcp` are healthy; only `127.0.0.1:8270` and
`127.0.0.1:8271` are host listeners; port 8124 is absent; the retired worker is
disabled/inactive; Tailscale Serve proxies `/` to 8270 and port 8443 to 8271.

Use the admin credential from inside the container so it is not printed:

```bash
ssh homelab 'sudo docker exec -i argus python - <<'"'"'PY'"'"'
import json, os, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:8000/api/admin/status",
    headers={"Authorization": "Bearer " + os.environ["ARGUS_ADMIN_API_KEY"]},
)
with urllib.request.urlopen(request, timeout=10) as response:
    status = json.load(response)
print(json.dumps({
    "ready": status["ready"],
    "reason_codes": status["reason_codes"],
    "build": status["build"],
    "authority": status["authority"],
    "dependencies": status["dependencies"],
}, sort_keys=True))
PY'
```

`/api/live` proves process liveness only. `/api/startup` proves initialization.
`/api/ready` is cached dependency readiness and must return 200 before
promotion. `/api/admin/status` is the detailed source for provider, browser,
outbox, backup, balance, schema, and runtime-identity diagnosis. See
[operational status](operations-status.md) for field semantics.

Useful focused checks:

```bash
ssh homelab 'sudo docker logs --tail=200 argus'
ssh homelab 'sudo docker stats --no-stream argus argus-mcp'
ssh homelab 'sudo docker exec atlas-postgres pg_isready -U postgres -d argus'
ssh homelab 'sudo docker exec atlas-postgres psql -U postgres -d argus -Atc
  "select version_num from alembic_version"'
```

The deterministic browser canary is
`sudo /usr/local/libexec/argus-browser-canary`; it must report no OOM event,
no orphan runtime process, and a bounded peak below the container limit.

## Promotion and rollback

Production accepts only one digest-addressed image for API and MCP. The GitHub
`production` environment and `argus-production` concurrency group serialize
promotion. Candidate gates use scratch PostgreSQL, a hermetic provider fixture,
no production ingress, and no paid-provider credentials.

Normal promotion:

```bash
gh workflow run docker-publish.yml --ref main
gh run watch --exit-status
```

On the homelab, the root-owned forced-command promoter:

1. validates the immutable digest, source revision, and receipt;
2. runs isolated candidate identity, readiness, search, extraction, MCP,
   accounting, restart, dependency-loss, and resource gates;
3. verifies a fresh recovery checkpoint;
4. atomically records requested/current/previous state;
5. loads the exact image and runs production gates;
6. marks the digest known-good only after success.

State is under
`/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion`.
`current.json` is loaded truth, `previous.json` is the compatible rollback
target, and `known-good.json` is the last admitted release. Never edit these
files by hand.

For an automatic rollback, let `promote-argus-release` restore the recorded
previous digest. If the previous binary is not fully compatible with the
current schema, restore the candidate and use the fresh database backup plus
forward repair. Do not downgrade schema or restore data over production without
an explicit irreversible-data gate. The detailed release contract is in
[releasing](releasing.md).

## PostgreSQL backup and restore

Before promotion or recovery, run the Homelab backup and verifier:

```bash
ssh homelab 'sudo /usr/local/libexec/argus-recovery-checkpoint'
ssh homelab 'sudo /mnt/fast-storage/github/homelab/scripts/verify-argus-pg-restore.sh'
```

Require a successful exit code, checksum verification, both tenant scopes,
schema compatibility, and a fresh isolated restore receipt. A restore drill
must use an isolated database/container. A production restore is an
irreversible data operation and remains a separate explicit gate.

## Outbox and uncertain spend

Read outbox state and dead letters with the admin routes:

```text
GET  /api/admin/maya-outbox/status
GET  /api/admin/maya-outbox/dead-letters
POST /api/admin/maya-outbox/{delivery_id}/recover
```

Recover a dead letter only after correcting its terminal cause. Confirm both
the Argus delivery row and Maya's durable receipt; Argus is not the
user-visible history store.

For a provider attempt left `uncertain`, first check provider/account evidence.
Resolve it through
`POST /api/admin/provider-spend/attempts/{attempt_id}/resolve` with an
idempotency key, actual charge, evidence reference, and resolution source.
Use zero only when evidence proves no charge. Never clear rows directly in
PostgreSQL. Paid calls or credit purchases require the paid-spend gate.

## Secret rotation and client cutover

Rotate application/admin/residential credentials only through the Homelab SOPS
workflow, then run `scripts/gen-env.sh`. Keep caller and admin values distinct.
Restart the two Argus containers, run identity/readiness/search/extraction/MCP
canaries, then revoke the old credential. Never print decrypted files or full
environments.

For a client cutover, configure the private HTTPS authority URL and a scoped
caller token, verify unauthenticated rejection, then verify its actual search,
extraction, or MCP path. Only after durable success should the old endpoint be
disabled. Mac launch agents and OCI services must remain disabled; they are not
rollback targets. Homelab loopback remains locally available if Tailscale
remote ingress is interrupted.

## 2026-07-29 walkthrough record

Wayfinder P1 completed with both production containers healthy on
`ghcr.io/khamel83/argus@sha256:2249702ef10b4a7bcc80e47ea9de55f0c569c46d28f3eb3dfb445522e1510716`,
source revision `edb7c926070ca051a644ae50bc647526b1f4f115`, and release-receipt
SHA-256
`34a8a492eba7eb784109122c6a51f38a5a8b30b6ec7c19dffa238bfafb6c805f`.
`current.json` and `known-good.json` name that exact release with phase
`complete`; no cutover marker remains.

The exact two-run scorecard is retained at
`/mnt/fast-storage/appdata/argus-scorecards/2026-07-29-idle-readiness-v1`.
After 330 seconds without search traffic, all enabled free providers had
unknown health and reachability observations while `/api/ready` correctly
returned HTTP 200 with `status=degraded` and `ready=true`. A subsequent full
production gate passed image/source identity, readiness, deterministic
free-only GitHub search, MCP initialize/tools, and durable accounting. No paid
provider was called.

The broader walkthrough also verified loopback-only 8270/8271 bindings,
tailnet-only Tailscale Serve, distinct application/admin authorization,
PostgreSQL schema 0009, extraction, browser resource limits, fresh backup and
isolated restore evidence, disabled Mac and OCI authorities, and the retired
host residential worker.
