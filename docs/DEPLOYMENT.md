# Argus — Deployment Architecture & Operations Guide

> **What this file is for:** Canonical runbook for Argus deployment, scorecard admission, manual promotion, and CI/CD troubleshooting.

---

## 1. Architecture & Core Invariants

- **Production Authority**: Sole production instance on Homelab Docker (`ARGUS_NODE_ROLE=primary`).
- **Callers**: All external agents (Claude Code, Antigravity, Codex), CLI instances, and downstream services (Maya, G2K) operate strictly as thin callers (`ARGUS_NODE_ROLE=caller`). Callers never receive database credentials or provider keys.
- **Network Boundaries**: Secured strictly within Tailscale mesh TLS (no public ports exposed).
  - HTTP Authority API: `https://homelab.deer-panga.ts.net:8270`
  - MCP Adapter: `https://homelab.deer-panga.ts.net:8443`
  - Admin Endpoint: `https://homelab.deer-panga.ts.net:8270/api/admin/*`

---

## 2. Scorecard Admission Requirements

The homelab promotion pipeline strictly enforces a **Scorecard Admission** requirement before any new container image can be promoted. The state tool requires four explicit SHA-256 hashes bound to the candidate digest and source revision:

1. `hermetic_manifest_sha256`: Built during CI (`ci.yml` scorecard job).
2. `attempt_one_sha256`: Output of live scorecard comparison attempt 1.
3. `attempt_two_sha256`: Output of live scorecard comparison attempt 2.
4. `residual_sha256`: Verified residual compiled from attempt 1 and attempt 2.

### Live Scorecard Command Syntax (on Homelab)

When running `argus-live-scorecard`, the `--searxng-image` flag **must** be exact digest-addressed (tags like `:latest` fail closed), and `--argus-root` must point to an Argus repository checkout containing `scripts/run-scorecard.py` and `tests/fixtures/scorecard/corpus.json`:

```bash
sudo /usr/local/sbin/argus-live-scorecard \
  --baseline-image "ghcr.io/khamel83/argus@sha256:<BASELINE_DIGEST>" \
  --baseline-revision "<BASELINE_REVISION>" \
  --candidate-image "ghcr.io/khamel83/argus@sha256:<CANDIDATE_DIGEST>" \
  --candidate-revision "<CANDIDATE_REVISION>" \
  --searxng-image "searxng/searxng@sha256:<SEARXNG_DIGEST>" \
  --argus-root /mnt/fast-storage/github/argus \
  --output /tmp/live-scorecard-output
```

Executing `argus-live-scorecard` automatically records the scorecard admission in `/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion/scorecard-admissions/`.

---

## 3. Manual Promotion Procedure (Fallback)

If the automated GitHub Actions SSH promotion fails or times out:

1. **Pull Candidate Image on Homelab**:
   ```bash
   docker pull ghcr.io/khamel83/argus@sha256:<CANDIDATE_DIGEST>
   ```

2. **Execute Live Scorecard & Record Admission**:
   Run the `argus-live-scorecard` command shown above.

3. **Execute Promotion Script**:
   ```bash
   sudo /usr/local/sbin/promote-argus-release \
     "ghcr.io/khamel83/argus@sha256:<CANDIDATE_DIGEST>" \
     "<CANDIDATE_REVISION>" \
     "<RECEIPT_SHA256>"
   ```

---

## 4. Promotion State Inspection & Debugging

All promotion state files are stored in `/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion/`:

- `current.json`: Currently deployed live image, revision, and receipt hash.
- `known-good.json`: Verified baseline image for automatic rollback.
- `last-failure.json`: Details on the last failed promotion phase and reason.
- `logs/attempt.*.log`: Comprehensive promotion logs (the promotion script redirects all output to these log files).

To inspect promotion logs on failure:
```bash
sudo cat /mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion/logs/$(sudo ls -t /mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion/logs/ | head -1)
```

---

## 5. Post-Deployment Verification Protocol

Once promotion completes:

1. **Check Provider Health**:
   ```bash
   argus health
   ```
2. **Execute Test Retrieval**:
   ```bash
   argus search -q "test query" --max-results 3
   ```
3. **Verify Provider Health Endpoint**:
   ```bash
   curl -s -H "Authorization: Bearer $ARGUS_AUTHORITY_TOKEN" \
     https://homelab.deer-panga.ts.net:8270/api/provider-health | python3 -m json.tool
   ```
