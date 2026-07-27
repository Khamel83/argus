# Immutable Homelab Promotion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build one attested Argus image and promote that exact digest through an isolated homelab candidate, serialized production cutover, 30-minute soak, and automatic compatible-digest rollback with durable receipts.

**Architecture:** Argus GitHub Actions owns build and release identity but has no general-purpose shell authority on the homelab. It joins Tailscale ephemerally and submits one validated promotion request through a pinned-host-key, forced-command SSH key. A root-owned homelab promoter owns candidate isolation, schema/recovery gates, Compose cutover, soak, rollback, and append-only receipts outside Argus containers.

**Tech Stack:** GitHub Actions, Docker Buildx/GHCR attestations, Tailscale GitHub Action, OpenSSH forced commands, Bash with `flock`, Docker Compose, PostgreSQL 16, Python 3.11+, pytest, JSON receipts.

## Global Constraints

- Build one frozen image; retain its manifest digest, source revision, provenance, and SBOM; never rebuild the production candidate.
- Pin every third-party workflow action to a full 40-character commit SHA and grant least-privilege job permissions.
- Use the GitHub `production` environment and one `argus-production` concurrency group with `cancel-in-progress: false`.
- Join the tailnet with the ephemeral `tag:argus-deployer` identity; the tailnet ACL permits that tag to reach only homelab SSH.
- Standard SSH must use the repository secret `DEPLOY_KNOWN_HOSTS`, `StrictHostKeyChecking=yes`, and a forced-command key; OCI and `StrictHostKeyChecking=no` are forbidden.
- The homelab promotion lock is `/run/lock/argus-promotion.lock`.
- Durable promotion state is `/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion`.
- Candidate PostgreSQL is scratch-only; candidate services expose no host ports, join no production network, receive no Maya token, and receive no paid-provider credential.
- A schema-changing release requires current backup/restore evidence and either previous-image compatibility or a checked-in forward-repair receipt explicitly named by the request.
- Production must report the requested source revision and actually run the requested digest before a 1,800-second soak begins.
- Any blocking post-cutover failure restores the exact previous compatible digest and reruns readiness, identity, search, extraction, and MCP proof.
- Existing dirty checkouts are preserved. All implementation occurs in the issue worktrees.
- Human review/merge remains the production deployment gate.

---

## File Structure

### Argus repository

- `.github/workflows/docker-publish.yml` — build-once release and narrowly scoped promotion request.
- `.github/workflows/ci.yml` — full-SHA action pins for existing CI.
- `.github/workflows/ai-review.yml` — full-SHA checkout pins for automated review.
- `.github/workflows/publish.yml` — full-SHA checkout/setup-python pins for package publishing.
- `scripts/write_release_receipt.py` — validate and emit the immutable build receipt uploaded by Actions.
- `tests/test_release_receipt.py` — unit contract for receipt validation and stable JSON.
- `tests/test_release_workflow.py` — static workflow security and build-once contract.
- `docs/releasing.md` — operator prerequisites, protected environment, secret names, digest evidence, and rollback behavior.

### Homelab repository

- `services/argus/docker-compose.yml` — require one digest-addressed `ARGUS_IMAGE` for HTTP and MCP.
- `services/argus/docker-compose.candidate.yml` — isolated scratch PostgreSQL, fixture, HTTP authority, and MCP candidate.
- `scripts/argus-candidate-fixture.py` — deterministic SearXNG JSON and extractable HTML served inside the candidate network.
- `scripts/argus-promotion-state.py` — strict image/source validation plus atomic state and receipt writes.
- `scripts/argus-candidate-gates.sh` — identity, readiness, search, extraction, MCP, accounting, dependency-loss, restart, and resource gates.
- `scripts/promote-argus-release.sh` — locked idempotent candidate/cutover/soak/rollback transaction.
- `scripts/argus-deploy-command.sh` — forced-command parser admitting only one promotion grammar.
- `scripts/install-argus-promoter.sh` — install root-owned scripts and print the exact `authorized_keys` restriction.
- `tests/test_argus_promotion_contract.py` — executable and static contract tests for all homelab promotion surfaces.
- `docs/argus-promotion.md` — install, ACL, request, receipt, failure, rollback, and recovery operations.

---

### Task 1: Immutable Argus Build Receipt

**Files:**
- Create: `scripts/write_release_receipt.py`
- Create: `tests/test_release_receipt.py`

**Interfaces:**
- Consumes: CLI arguments `--image`, `--digest`, `--source-revision`, `--repository`, `--workflow`, `--run-id`, `--run-attempt`, and `--output`.
- Produces: schema-1 JSON with `image_ref`, `digest`, `source_revision`, and GitHub run identity; exits nonzero for mutable or malformed identity.

- [ ] **Step 1: Write the failing receipt tests**

```python
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/write_release_receipt.py"
DIGEST = "sha256:" + ("a" * 64)
REVISION = "b" * 40


def _run(tmp_path, *, image="ghcr.io/khamel83/argus", digest=DIGEST):
    output = tmp_path / "release.json"
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT),
            "--image", image,
            "--digest", digest,
            "--source-revision", REVISION,
            "--repository", "Khamel83/argus",
            "--workflow", "Build and Promote Immutable Image",
            "--run-id", "1234",
            "--run-attempt", "2",
            "--output", str(output),
        ],
        text=True,
        capture_output=True,
    )
    return result, output


def test_release_receipt_is_digest_addressed_and_stable(tmp_path):
    result, output = _run(tmp_path)
    assert result.returncode == 0, result.stderr
    payload = json.loads(output.read_text())
    assert payload["schema_version"] == 1
    assert payload["image_ref"] == f"ghcr.io/khamel83/argus@{DIGEST}"
    assert payload["source_revision"] == REVISION
    assert output.read_bytes().endswith(b"\n")


def test_release_receipt_rejects_mutable_image_input(tmp_path):
    result, output = _run(tmp_path, image="ghcr.io/khamel83/argus:latest")
    assert result.returncode != 0
    assert not output.exists()
```

- [ ] **Step 2: Run tests and confirm the missing-script failure**

Run: `uv run pytest tests/test_release_receipt.py -v`

Expected: FAIL because `scripts/write_release_receipt.py` does not exist.

- [ ] **Step 3: Implement strict receipt emission**

```python
#!/usr/bin/env python3
import argparse
import json
import re
from pathlib import Path


DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
IMAGE = re.compile(r"ghcr\.io/[a-z0-9_.-]+/[a-z0-9_.-]+\Z", re.IGNORECASE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--run-attempt", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if not IMAGE.fullmatch(args.image):
        parser.error("--image must be an untagged ghcr.io owner/repository name")
    if not DIGEST.fullmatch(args.digest):
        parser.error("--digest must be sha256 followed by 64 lowercase hex characters")
    if not REVISION.fullmatch(args.source_revision):
        parser.error("--source-revision must be a full lowercase Git commit")
    payload = {
        "schema_version": 1,
        "image": args.image,
        "image_ref": f"{args.image}@{args.digest}",
        "digest": args.digest,
        "source_revision": args.source_revision,
        "build": {
            "repository": args.repository,
            "workflow": args.workflow,
            "run_id": args.run_id,
            "run_attempt": args.run_attempt,
        },
    }
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run focused tests**

Run: `uv run pytest tests/test_release_receipt.py -v`

Expected: 2 passed.

- [ ] **Step 5: Commit the receipt contract**

```bash
git add scripts/write_release_receipt.py tests/test_release_receipt.py
git commit -m "feat: emit immutable release receipts"
```

### Task 2: Secure Build-Once GitHub Workflow

**Files:**
- Create: `tests/test_release_workflow.py`
- Modify: `.github/workflows/docker-publish.yml`
- Modify: `.github/workflows/ci.yml`
- Modify: `.github/workflows/ai-review.yml`
- Modify: `.github/workflows/publish.yml`
- Modify: `docs/releasing.md`

**Interfaces:**
- Consumes: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `DEPLOY_SSH_PRIVATE_KEY`, and `DEPLOY_KNOWN_HOSTS` secrets in the protected `production` environment.
- Produces: GHCR image and attestations at one digest, a `release-receipt` artifact, and one forced-command SSH request `promote <image-ref> <source-revision> <receipt-sha256>`.

- [ ] **Step 1: Write failing workflow security tests**

```python
import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def test_all_third_party_actions_are_pinned_to_full_commits():
    for path in WORKFLOWS.glob("*.yml"):
        payload = yaml.safe_load(path.read_text())
        for job in (payload.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if "uses" in step:
                    assert FULL_SHA.fullmatch(step["uses"]), (path, step["uses"])


def test_release_workflow_builds_once_and_submits_a_hardened_request():
    text = (WORKFLOWS / "docker-publish.yml").read_text()
    assert "environment: production" in text
    assert "group: argus-production" in text
    assert "cancel-in-progress: false" in text
    assert "provenance: mode=max" in text
    assert "sbom: true" in text
    assert "tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:sha-${{ github.sha }}" in text
    assert "tailscale/github-action@" in text
    assert "tags: tag:argus-deployer" in text
    assert "DEPLOY_KNOWN_HOSTS" in text
    assert "StrictHostKeyChecking=yes" in text
    assert "argus-deploy promote " in text
    assert ":latest" not in text
    assert "oci-" not in text
    assert "StrictHostKeyChecking=no" not in text
    assert text.count("docker/build-push-action@") == 1
```

- [ ] **Step 2: Confirm the tests fail on mutable tags and major-version action references**

Run: `uv run pytest tests/test_release_workflow.py -v`

Expected: both tests fail against the old workflows.

- [ ] **Step 3: Pin the existing action references**

Use these verified tag commits, retaining a version comment after each reference:

```yaml
uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0
uses: actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1 # v6.3.0
uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3
uses: docker/build-push-action@ca052bb54ab0790a636c9b5f226502c73d547a25 # v5
```

Apply the matching checkout/setup/build pins everywhere in `ci.yml`, `ai-review.yml`, and `publish.yml`.

- [ ] **Step 4: Replace the mutable release workflow**

The workflow must have these job-level contracts:

```yaml
name: Build and Promote Immutable Image

on:
  push:
    branches: [main]
    tags: ["v*"]
  workflow_dispatch: {}

env:
  REGISTRY: ghcr.io
  IMAGE_NAME: ${{ github.repository }}

jobs:
  build:
    runs-on: ubuntu-latest
    permissions:
      contents: read
      packages: write
    outputs:
      digest: ${{ steps.build.outputs.digest }}
      receipt_sha256: ${{ steps.receipt.outputs.sha256 }}
    steps:
      - uses: actions/checkout@fbc6f3992d24b796d5a048ff273f7fcc4a7b6c09 # v5.1.0
      - uses: docker/setup-buildx-action@8d2750c68a42422c14e847fe6c8ac0403b4cbd6f # v3
      - uses: docker/login-action@c94ce9fb468520275223c153574b00df6fe4bcc9 # v3
        with:
          registry: ${{ env.REGISTRY }}
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}
      - id: build
        uses: docker/build-push-action@10e90e3645eae34f1e60eeb005ba3a3d33f178e8 # v6
        with:
          context: .
          build-args: VCS_REF=${{ github.sha }}
          push: true
          tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:sha-${{ github.sha }}
          provenance: mode=max
          sbom: true
          cache-from: type=gha
          cache-to: type=gha,mode=max
      - id: receipt
        run: |
          python scripts/write_release_receipt.py \
            --image "${REGISTRY}/${IMAGE_NAME}" \
            --digest "${{ steps.build.outputs.digest }}" \
            --source-revision "${GITHUB_SHA}" \
            --repository "${GITHUB_REPOSITORY}" \
            --workflow "${GITHUB_WORKFLOW}" \
            --run-id "${GITHUB_RUN_ID}" \
            --run-attempt "${GITHUB_RUN_ATTEMPT}" \
            --output release-receipt.json
          echo "sha256=$(sha256sum release-receipt.json | cut -d' ' -f1)" >> "$GITHUB_OUTPUT"
      - uses: actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02 # v4
        with:
          name: release-receipt
          path: release-receipt.json
          if-no-files-found: error
          retention-days: 90

  promote:
    needs: build
    if: github.ref == 'refs/heads/main' || startsWith(github.ref, 'refs/tags/v')
    runs-on: ubuntu-latest
    environment: production
    concurrency:
      group: argus-production
      cancel-in-progress: false
    permissions:
      contents: read
    steps:
      - uses: tailscale/github-action@6cae46e2d796f265265cfcf628b72a32b4d7cade # v3
        with:
          oauth-client-id: ${{ secrets.TS_OAUTH_CLIENT_ID }}
          oauth-secret: ${{ secrets.TS_OAUTH_SECRET }}
          tags: tag:argus-deployer
      - name: Submit forced-command promotion
        env:
          IMAGE_REF: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}@${{ needs.build.outputs.digest }}
          SOURCE_REVISION: ${{ github.sha }}
          RECEIPT_SHA256: ${{ needs.build.outputs.receipt_sha256 }}
          DEPLOY_KEY: ${{ secrets.DEPLOY_SSH_PRIVATE_KEY }}
          KNOWN_HOSTS: ${{ secrets.DEPLOY_KNOWN_HOSTS }}
        run: |
          install -d -m 0700 "$RUNNER_TEMP/ssh"
          install -m 0600 /dev/null "$RUNNER_TEMP/ssh/deploy_key"
          printf '%s\n' "$DEPLOY_KEY" > "$RUNNER_TEMP/ssh/deploy_key"
          printf '%s\n' "$KNOWN_HOSTS" > "$RUNNER_TEMP/ssh/known_hosts"
          ssh -i "$RUNNER_TEMP/ssh/deploy_key" \
            -o IdentitiesOnly=yes \
            -o StrictHostKeyChecking=yes \
            -o UserKnownHostsFile="$RUNNER_TEMP/ssh/known_hosts" \
            "argus-deploy@homelab-ts" \
            "argus-deploy promote ${IMAGE_REF} ${SOURCE_REVISION} ${RECEIPT_SHA256}"
```

- [ ] **Step 5: Document the exact environment setup**

Add to `docs/releasing.md`:

```markdown
## Immutable homelab promotion

`main` and `v*` build one `sha-<commit>` image. Buildx records OCI provenance
and SBOM attestations, and the workflow passes the returned manifest digest to
the homelab promoter without rebuilding. Configure a protected GitHub
environment named `production` with required reviewer protection and these
secrets: `TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`,
`DEPLOY_SSH_PRIVATE_KEY` and `DEPLOY_KNOWN_HOSTS`.

The Tailscale OAuth client may create only `tag:argus-deployer` nodes. Tailnet
ACLs allow that tag to reach only TCP/22 on `homelab-ts`. The SSH public key is
installed with the forced command printed by
`sudo scripts/install-argus-promoter.sh --public-key-file <path>`.

Promotion remains provisional until the candidate gates, production identity
checks, and 1,800-second soak pass. Failure after cutover restores the recorded
previous compatible digest. Receipts live outside containers under
`/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion`.
```

- [ ] **Step 6: Run workflow and full Argus tests**

Run:

```bash
uv run pytest tests/test_release_receipt.py tests/test_release_workflow.py -v
uv run pytest tests/ -q
```

Expected: focused tests pass; full suite passes with only recorded skips/warnings.

- [ ] **Step 7: Commit the workflow transaction**

```bash
git add .github/workflows scripts/write_release_receipt.py tests/test_release_receipt.py tests/test_release_workflow.py docs/releasing.md
git commit -m "feat: build and request immutable promotions"
```

### Task 3: Homelab Promotion State Model

**Files:**
- Create: `scripts/argus-promotion-state.py`
- Create: `tests/test_argus_promotion_contract.py`

**Interfaces:**
- Consumes: subcommands `validate`, `begin`, `complete`, and `fail`; exact digest/source/receipt identifiers; state root path.
- Produces: atomically replaced `requested.json`, `current.json`, `previous.json`, and `known-good.json`, plus immutable timestamped files under `receipts/`.

- [ ] **Step 1: Write failing state-model tests**

```python
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / "scripts/argus-promotion-state.py"
REVISION = "b" * 40
DIGEST = "sha256:" + ("a" * 64)
IMAGE_REF = f"ghcr.io/khamel83/argus@{DIGEST}"


def test_state_model_rejects_mutable_images(tmp_path):
    result = subprocess.run(
        [sys.executable, str(STATE), "validate", "ghcr.io/khamel83/argus:latest", REVISION],
        text=True,
        capture_output=True,
    )
    assert result.returncode != 0


def test_complete_atomically_advances_current_previous_and_known_good(tmp_path):
    root = tmp_path / "state"
    subprocess.run(
        [
            sys.executable, str(STATE), "begin",
            "--state-root", str(root),
            "--image-ref", IMAGE_REF,
            "--source-revision", REVISION,
            "--receipt-sha256", "c" * 64,
        ],
        check=True,
    )
    subprocess.run(
        [
            sys.executable, str(STATE), "complete",
            "--state-root", str(root),
            "--image-ref", IMAGE_REF,
            "--source-revision", REVISION,
            "--receipt-sha256", "c" * 64,
        ],
        check=True,
    )
    assert json.loads((root / "current.json").read_text())["image_ref"] == IMAGE_REF
    assert json.loads((root / "known-good.json").read_text())["image_ref"] == IMAGE_REF
    assert list((root / "receipts").glob("*-complete.json"))
```

- [ ] **Step 2: Run tests and confirm the missing-script failure**

Run: `pytest -q tests/test_argus_promotion_contract.py`

Expected: FAIL because the state model is absent.

- [ ] **Step 3: Implement the validated atomic state writer**

Implement:

```python
#!/usr/bin/env python3
import argparse
import datetime as dt
import json
import os
import re
import tempfile
from pathlib import Path


IMAGE_REF = re.compile(r"ghcr\.io/khamel83/argus@(sha256:[0-9a-f]{64})\Z")
REVISION = re.compile(r"[0-9a-f]{40}\Z")
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def identity(image_ref: str, source_revision: str, receipt_sha256: str) -> dict:
    match = IMAGE_REF.fullmatch(image_ref)
    if not match or not REVISION.fullmatch(source_revision):
        raise ValueError("promotion identity must use the Argus digest and full source commit")
    if not HEX64.fullmatch(receipt_sha256):
        raise ValueError("receipt identity must be 64 lowercase hex characters")
    return {
        "schema_version": 1,
        "image_ref": image_ref,
        "digest": match.group(1),
        "source_revision": source_revision,
        "receipt_sha256": receipt_sha256,
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("image_ref")
    validate.add_argument("source_revision")
    for name in ("begin", "complete", "fail"):
        command = sub.add_parser(name)
        command.add_argument("--state-root", type=Path, required=True)
        command.add_argument("--image-ref", required=True)
        command.add_argument("--source-revision", required=True)
        command.add_argument("--receipt-sha256", required=True)
        command.add_argument("--reason", default="")
    args = parser.parse_args()
    if args.command == "validate":
        identity(args.image_ref, args.source_revision, "0" * 64)
        return 0
    payload = identity(args.image_ref, args.source_revision, args.receipt_sha256)
    now = dt.datetime.now(dt.timezone.utc)
    payload.update({"phase": args.command, "recorded_at": now.isoformat(), "reason": args.reason})
    args.state_root.mkdir(parents=True, exist_ok=True)
    (args.state_root / "receipts").mkdir(mode=0o750, exist_ok=True)
    if args.command == "begin":
        atomic_json(args.state_root / "requested.json", payload)
    elif args.command == "complete":
        current = args.state_root / "current.json"
        if current.exists():
            atomic_json(args.state_root / "previous.json", json.loads(current.read_text()))
        atomic_json(current, payload)
        atomic_json(args.state_root / "known-good.json", payload)
    else:
        atomic_json(args.state_root / "last-failure.json", payload)
    stamp = now.strftime("%Y%m%dT%H%M%S.%fZ")
    atomic_json(args.state_root / "receipts" / f"{stamp}-{args.command}.json", payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the state tests**

Run: `pytest -q tests/test_argus_promotion_contract.py`

Expected: state-model tests pass.

- [ ] **Step 5: Commit the state model**

```bash
git add scripts/argus-promotion-state.py tests/test_argus_promotion_contract.py
git commit -m "feat: add durable Argus promotion state"
```

### Task 4: Isolated Candidate and Deterministic Gates

**Files:**
- Create: `services/argus/docker-compose.candidate.yml`
- Create: `scripts/argus-candidate-fixture.py`
- Create: `scripts/argus-candidate-gates.sh`
- Modify: `tests/test_argus_promotion_contract.py`

**Interfaces:**
- Consumes: `ARGUS_CANDIDATE_IMAGE`, `ARGUS_CANDIDATE_PROJECT`, `ARGUS_CANDIDATE_TOKEN`, and optional `ARGUS_PREVIOUS_IMAGE`.
- Produces: zero on all candidate gates; nonzero on identity, readiness, search, extraction, MCP, accounting, dependency-loss, restart, schema compatibility, or resource failure.

- [ ] **Step 1: Add failing static candidate-isolation tests**

```python
import yaml


def test_candidate_compose_has_no_production_ingress_or_credentials():
    path = ROOT / "services/argus/docker-compose.candidate.yml"
    text = path.read_text()
    payload = yaml.safe_load(text)
    assert "ports" not in payload["services"]["argus-candidate"]
    assert "ports" not in payload["services"]["argus-candidate-mcp"]
    assert payload["services"]["argus-candidate"]["image"] == "${ARGUS_CANDIDATE_IMAGE:?digest required}"
    assert "tmpfs" in payload["services"]["argus-candidate-postgres"]
    for forbidden in ("MAYA", "BRAVE_API_KEY", "TAVILY_API_KEY", "YOU_API_KEY", "homelab"):
        assert forbidden not in text


def test_candidate_gate_names_every_required_probe():
    text = (ROOT / "scripts/argus-candidate-gates.sh").read_text()
    for gate in (
        "identity", "readiness", "search", "extraction", "mcp",
        "accounting", "dependency-loss", "restart", "resource",
    ):
        assert f"gate:{gate}" in text
```

- [ ] **Step 2: Run tests and confirm missing candidate artifacts**

Run: `pytest -q tests/test_argus_promotion_contract.py`

Expected: FAIL because the candidate files do not exist.

- [ ] **Step 3: Add the internal deterministic fixture**

`scripts/argus-candidate-fixture.py` must serve:

```python
#!/usr/bin/env python3
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


ARTICLE = """<!doctype html><html><head><title>Argus Candidate Fixture</title></head>
<body><article><h1>Argus Candidate Fixture</h1><p>""" + ("verified candidate content " * 80) + """</p></article></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/search"):
            body = json.dumps({"results": [{
                "url": "http://argus-candidate-fixture:8080/article",
                "title": "Argus Candidate Fixture",
                "content": "deterministic isolated search result",
                "score": 1.0,
                "engine": "candidate-fixture",
            }]}).encode()
            content_type = "application/json"
        elif self.path == "/article":
            body = ARTICLE.encode()
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()
```

- [ ] **Step 4: Add the isolated candidate Compose project**

Define four services on only `argus-candidate`:

```yaml
services:
  argus-candidate-postgres:
    image: postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777
    environment:
      POSTGRES_DB: argus_candidate
      POSTGRES_USER: argus
      POSTGRES_PASSWORD: candidate-only
    tmpfs:
      - /var/lib/postgresql/data:rw,noexec,nosuid,size=256m
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U argus -d argus_candidate"]
      interval: 2s
      timeout: 2s
      retries: 30
    networks: [argus-candidate]

  argus-candidate-fixture:
    image: ${ARGUS_CANDIDATE_IMAGE:?digest required}
    command: ["python", "/candidate/argus-candidate-fixture.py"]
    volumes:
      - ../../scripts/argus-candidate-fixture.py:/candidate/argus-candidate-fixture.py:ro
    read_only: true
    tmpfs: [/tmp]
    networks: [argus-candidate]

  argus-candidate:
    image: ${ARGUS_CANDIDATE_IMAGE:?digest required}
    environment: &candidate-env
      ARGUS_ENV: production
      ARGUS_API_KEY: ${ARGUS_CANDIDATE_TOKEN:?token required}
      ARGUS_ADMIN_API_KEY: ${ARGUS_CANDIDATE_TOKEN:?token required}
      ARGUS_DB_URL: postgresql+psycopg2://argus:candidate-only@argus-candidate-postgres/argus_candidate
      ARGUS_DATA_ROOT: /tmp/argus
      ARGUS_EGRESS_TYPE: unknown
      ARGUS_RESIDENTIAL_POLICY: "off"
      ARGUS_DISABLE_SECRET_RESOLUTION: "true"
      ARGUS_AUTOLOAD_DOTENV: "false"
      ARGUS_SEARXNG_ENABLED: "true"
      ARGUS_SEARXNG_BASE_URL: http://argus-candidate-fixture:8080
      ARGUS_DUCKDUCKGO_ENABLED: "false"
      ARGUS_YAHOO_ENABLED: "false"
      ARGUS_GITHUB_ENABLED: "false"
      ARGUS_JINA_ENABLED: "false"
      ARGUS_FIRECRAWL_ENABLED: "false"
    depends_on:
      argus-candidate-postgres:
        condition: service_healthy
      argus-candidate-fixture:
        condition: service_started
    read_only: true
    tmpfs: [/tmp]
    mem_limit: 768m
    cpus: 1.0
    pids_limit: 256
    networks: [argus-candidate]

  argus-candidate-mcp:
    image: ${ARGUS_CANDIDATE_IMAGE:?digest required}
    command: ["argus", "mcp", "serve", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8001"]
    environment:
      <<: *candidate-env
      ARGUS_NODE_ROLE: caller
      ARGUS_AUTHORITY_URL: http://argus-candidate:8000
      ARGUS_AUTHORITY_TOKEN: ${ARGUS_CANDIDATE_TOKEN:?token required}
    depends_on: [argus-candidate]
    read_only: true
    tmpfs: [/tmp]
    mem_limit: 512m
    cpus: 0.5
    pids_limit: 256
    networks: [argus-candidate]

networks:
  argus-candidate:
    internal: true
```

- [ ] **Step 5: Implement candidate gates**

`scripts/argus-candidate-gates.sh` must:

```bash
#!/usr/bin/env bash
set -euo pipefail

COMPOSE_FILE="${ARGUS_CANDIDATE_COMPOSE:-services/argus/docker-compose.candidate.yml}"
PROJECT="${ARGUS_CANDIDATE_PROJECT:?candidate project required}"
IMAGE="${ARGUS_CANDIDATE_IMAGE:?candidate image required}"
TOKEN="${ARGUS_CANDIDATE_TOKEN:?candidate token required}"
compose=(docker compose -p "$PROJECT" -f "$COMPOSE_FILE")

probe() {
  "${compose[@]}" exec -T argus-candidate python - "$TOKEN" "$@" <<'PY'
import json, sys, urllib.request
token, method, path, payload = sys.argv[1:5]
request = urllib.request.Request(
    "http://127.0.0.1:8000" + path,
    data=payload.encode() if payload else None,
    method=method,
    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
)
with urllib.request.urlopen(request, timeout=20) as response:
    print(json.dumps(json.load(response), sort_keys=True))
PY
}

echo gate:identity
"${compose[@]}" exec -T argus-candidate argus image-admission --manifest /app/runtime-manifest.json
probe GET /api/admin/status "" | grep -F "\"source_revision\""

echo gate:readiness
probe GET /api/ready "" | grep -F '"ready": true'

echo gate:search
probe POST /api/search '{"query":"candidate fixture","mode":"discovery","max_results":1,"providers":["searxng"]}' | grep -F 'Argus Candidate Fixture'

echo gate:extraction
probe POST /api/extract '{"url":"http://argus-candidate-fixture:8080/article"}' | grep -F 'verified candidate content'

echo gate:mcp
"${compose[@]}" exec -T argus-candidate-mcp python - <<'PY'
import json, urllib.request
payload = json.dumps({"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"candidate","version":"1"}}}).encode()
request = urllib.request.Request("http://127.0.0.1:8001/mcp", data=payload, headers={"Content-Type":"application/json","Accept":"application/json, text/event-stream"})
with urllib.request.urlopen(request, timeout=20) as response:
    assert response.status == 200
PY

echo gate:accounting
"${compose[@]}" exec -T argus-candidate-postgres psql -U argus -d argus_candidate -Atc \
  "select (select count(*) from retrieval_requests) >= 1 and (select count(*) from extraction_runs) >= 1" |
  grep -Fx t

echo gate:dependency-loss
"${compose[@]}" stop argus-candidate-postgres
sleep 16
if probe GET /api/ready "" >/dev/null 2>&1; then
  echo "readiness remained successful without PostgreSQL" >&2
  exit 1
fi
"${compose[@]}" start argus-candidate-postgres

echo gate:restart
"${compose[@]}" restart argus-candidate argus-candidate-mcp
for _ in $(seq 1 60); do probe GET /api/ready "" >/dev/null 2>&1 && break; sleep 2; done
probe GET /api/ready "" | grep -F '"ready": true'

echo gate:resource
for service in argus-candidate argus-candidate-mcp; do
  id="$("${compose[@]}" ps -q "$service")"
  docker inspect "$id" --format '{{.HostConfig.Memory}} {{.HostConfig.NanoCpus}} {{.HostConfig.PidsLimit}}' |
    awk '$1 > 0 && $2 > 0 && $3 > 0 {ok=1} END {exit !ok}'
done
```

- [ ] **Step 6: Run contract tests and Compose rendering**

Run:

```bash
pytest -q tests/test_argus_promotion_contract.py
ARGUS_CANDIDATE_IMAGE=ghcr.io/khamel83/argus@sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
ARGUS_CANDIDATE_TOKEN=test \
docker compose -f services/argus/docker-compose.candidate.yml config --quiet
```

Expected: tests pass and Compose exits zero.

- [ ] **Step 7: Commit candidate isolation**

```bash
git add services/argus/docker-compose.candidate.yml scripts/argus-candidate-fixture.py scripts/argus-candidate-gates.sh tests/test_argus_promotion_contract.py
git commit -m "feat: gate isolated Argus release candidates"
```

### Task 5: Locked Production Cutover and Rollback

**Files:**
- Modify: `services/argus/docker-compose.yml`
- Create: `scripts/promote-argus-release.sh`
- Create: `scripts/argus-deploy-command.sh`
- Create: `scripts/install-argus-promoter.sh`
- Modify: `tests/test_argus_promotion_contract.py`

**Interfaces:**
- Consumes: forced command `argus-deploy promote <digest-image-ref> <40-hex-source> <64-hex-receipt-sha>`.
- Produces: idempotent cutover or verified rollback, durable state, immutable receipt, and process exit status propagated to Actions.

- [ ] **Step 1: Write failing promotion transaction tests**

```python
def test_production_compose_requires_one_digest_image_for_both_services():
    text = (ROOT / "services/argus/docker-compose.yml").read_text()
    assert text.count("image: ${ARGUS_IMAGE:?digest-addressed ARGUS_IMAGE required}") == 2
    assert "ghcr.io/khamel83/argus:latest" not in text


def test_promoter_has_lock_soak_identity_and_rollback_proof():
    text = (ROOT / "scripts/promote-argus-release.sh").read_text()
    assert "flock -n 9" in text
    assert "/run/lock/argus-promotion.lock" in text
    assert "ARGUS_SOAK_SECONDS=\"${ARGUS_SOAK_SECONDS:-1800}\"" in text
    assert "rollback" in text
    assert "known-good.json" in text
    assert "scripts/verify-argus-pg-restore.sh" in text
    assert "argus-candidate-gates.sh" in text


def test_forced_command_rejects_shell_grammar():
    text = (ROOT / "scripts/argus-deploy-command.sh").read_text()
    assert "SSH_ORIGINAL_COMMAND" in text
    assert "exec sudo -n /usr/local/sbin/promote-argus-release" in text
    assert "eval " not in text
    assert "bash -c" not in text
```

- [ ] **Step 2: Run tests and confirm missing promoter artifacts**

Run: `pytest -q tests/test_argus_promotion_contract.py`

Expected: FAIL for mutable production image and absent scripts.

- [ ] **Step 3: Make production image selection digest-required**

In `services/argus/docker-compose.yml`, replace both image values with:

```yaml
image: ${ARGUS_IMAGE:?digest-addressed ARGUS_IMAGE required}
```

- [ ] **Step 4: Add the forced-command parser**

```bash
#!/usr/bin/env bash
set -euo pipefail

read -r -a words <<<"${SSH_ORIGINAL_COMMAND:-}"
if [[ ${#words[@]} -ne 5 || "${words[0]}" != "argus-deploy" || "${words[1]}" != "promote" ]]; then
  echo "only argus-deploy promote requests are accepted" >&2
  exit 64
fi
image_ref="${words[2]}"
source_revision="${words[3]}"
receipt_sha256="${words[4]}"
/usr/local/libexec/argus-promotion-state validate "$image_ref" "$source_revision"
[[ "$receipt_sha256" =~ ^[0-9a-f]{64}$ ]] || exit 64
exec sudo -n /usr/local/sbin/promote-argus-release \
  "$image_ref" "$source_revision" "$receipt_sha256"
```

- [ ] **Step 5: Implement the locked promoter**

`scripts/promote-argus-release.sh` must use these exact phases:

```bash
#!/usr/bin/env bash
set -euo pipefail

IMAGE_REF="${1:?image ref required}"
SOURCE_REVISION="${2:?source revision required}"
RECEIPT_SHA256="${3:?receipt sha256 required}"
STATE_ROOT="/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion"
REPO="/mnt/fast-storage/github/homelab"
COMPOSE="$REPO/services/argus/docker-compose.yml"
STATE_TOOL="/usr/local/libexec/argus-promotion-state"
GATES="/usr/local/libexec/argus-candidate-gates"
ARGUS_SOAK_SECONDS="${ARGUS_SOAK_SECONDS:-1800}"

exec 9>/run/lock/argus-promotion.lock
flock -n 9 || { echo "another Argus promotion is active" >&2; exit 75; }
"$STATE_TOOL" validate "$IMAGE_REF" "$SOURCE_REVISION"
install -d -m 0750 "$STATE_ROOT" "$STATE_ROOT/receipts"

if [[ -f "$STATE_ROOT/known-good.json" ]] &&
   python3 - "$STATE_ROOT/known-good.json" "$IMAGE_REF" <<'PY'
import json, sys
raise SystemExit(json.load(open(sys.argv[1]))["image_ref"] != sys.argv[2])
PY
then
  echo "requested digest is already known-good"
  exit 0
fi

"$STATE_TOOL" begin --state-root "$STATE_ROOT" --image-ref "$IMAGE_REF" \
  --source-revision "$SOURCE_REVISION" --receipt-sha256 "$RECEIPT_SHA256"
docker pull "$IMAGE_REF"

previous=""
previous_revision=""
if [[ -f "$STATE_ROOT/current.json" ]]; then
  previous="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["image_ref"])' "$STATE_ROOT/current.json")"
  previous_revision="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_revision"])' "$STATE_ROOT/current.json")"
fi

candidate_project="argus-candidate-${SOURCE_REVISION:0:12}"
candidate_token="$(openssl rand -hex 32)"
cleanup() {
  ARGUS_CANDIDATE_PROJECT="$candidate_project" \
  ARGUS_CANDIDATE_IMAGE="$IMAGE_REF" \
  ARGUS_CANDIDATE_TOKEN="$candidate_token" \
    docker compose -p "$candidate_project" -f "$REPO/services/argus/docker-compose.candidate.yml" down --volumes --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

export ARGUS_CANDIDATE_PROJECT="$candidate_project"
export ARGUS_CANDIDATE_IMAGE="$IMAGE_REF"
export ARGUS_CANDIDATE_TOKEN="$candidate_token"
docker compose -p "$candidate_project" -f "$REPO/services/argus/docker-compose.candidate.yml" up -d
"$GATES"

if [[ -n "$previous" && "$previous" != "$IMAGE_REF" ]]; then
  "$REPO/scripts/verify-argus-pg-restore.sh"
  ARGUS_CANDIDATE_IMAGE="$previous" "$GATES" --schema-compatibility-only
fi

rollback() {
  reason="$1"
  "$STATE_TOOL" fail --state-root "$STATE_ROOT" --image-ref "$IMAGE_REF" \
    --source-revision "$SOURCE_REVISION" --receipt-sha256 "$RECEIPT_SHA256" \
    --reason "$reason"
  if [[ -n "$previous" ]]; then
    ARGUS_IMAGE="$previous" docker compose -f "$COMPOSE" up -d argus argus-mcp
    verify_production "$previous" "$previous_revision"
  fi
  exit 1
}

verify_production() {
  expected="$1"
  expected_revision="$2"
  for service in argus argus-mcp; do
    container="$(ARGUS_IMAGE="$expected" docker compose -f "$COMPOSE" ps -q "$service")"
    actual="$(docker inspect "$container" --format '{{.Image}}')"
    expected_id="$(docker image inspect "$expected" --format '{{.Id}}')"
    [[ "$actual" == "$expected_id" ]] || return 1
  done
  ARGUS_IMAGE="$expected" docker compose -f "$COMPOSE" exec -T argus \
    argus image-admission --manifest /app/runtime-manifest.json
  ARGUS_IMAGE="$expected" docker compose -f "$COMPOSE" exec -T argus \
    python - "$expected_revision" <<'PY'
import json, sys, urllib.request
with urllib.request.urlopen("http://127.0.0.1:8000/api/admin/status", timeout=20) as response:
    payload = json.load(response)
assert payload["build"]["source_revision"] == sys.argv[1]
PY
}

ARGUS_IMAGE="$IMAGE_REF" docker compose -f "$COMPOSE" up -d argus argus-mcp
verify_production "$IMAGE_REF" "$SOURCE_REVISION" || rollback "production_identity_failed"

if [[ -n "${ARGUS_PROMOTION_FAIL_PHASE:-}" && "${ARGUS_PROMOTION_TEST_MODE:-0}" != "1" ]]; then
  rollback "test_failure_control_rejected"
fi
if [[ "${ARGUS_PROMOTION_TEST_MODE:-0}" == "1" && "${ARGUS_PROMOTION_FAIL_PHASE:-}" == "soak" ]]; then
  rollback "injected_soak_failure"
fi

deadline=$((SECONDS + ARGUS_SOAK_SECONDS))
while (( SECONDS < deadline )); do
  verify_production "$IMAGE_REF" "$SOURCE_REVISION" || rollback "production_soak_failed"
  sleep 30
done

"$STATE_TOOL" complete --state-root "$STATE_ROOT" --image-ref "$IMAGE_REF" \
  --source-revision "$SOURCE_REVISION" --receipt-sha256 "$RECEIPT_SHA256"
```

- [ ] **Step 6: Add root-owned installation**

`scripts/install-argus-promoter.sh` must:

```bash
#!/usr/bin/env bash
set -euo pipefail
[[ ${EUID} -eq 0 ]] || { echo "run as root" >&2; exit 77; }
[[ "${1:-}" == "--public-key-file" && -n "${2:-}" ]] || exit 64
install -o root -g root -m 0755 scripts/promote-argus-release.sh /usr/local/sbin/promote-argus-release
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 scripts/argus-promotion-state.py /usr/local/libexec/argus-promotion-state
install -o root -g root -m 0755 scripts/argus-candidate-gates.sh /usr/local/libexec/argus-candidate-gates
install -o root -g root -m 0755 scripts/argus-deploy-command.sh /usr/local/libexec/argus-deploy-command
printf 'command="/usr/local/libexec/argus-deploy-command",restrict %s\n' "$(cat "$2")"
printf 'argus-deploy ALL=(root) NOPASSWD: /usr/local/sbin/promote-argus-release *\n'
```

The operator installs the printed `authorized_keys` line for a dedicated locked `argus-deploy` account and installs the printed sudoers line with mode `0440`.

- [ ] **Step 7: Run homelab tests**

Run:

```bash
pytest -q tests/test_argus_promotion_contract.py
pytest -q tests/test_argus_postgres_recovery_contract.py
shellcheck scripts/promote-argus-release.sh scripts/argus-candidate-gates.sh scripts/argus-deploy-command.sh scripts/install-argus-promoter.sh
```

Expected: all tests pass and shellcheck emits no findings.

- [ ] **Step 8: Commit the production transaction**

```bash
git add services/argus/docker-compose.yml scripts/promote-argus-release.sh scripts/argus-deploy-command.sh scripts/install-argus-promoter.sh tests/test_argus_promotion_contract.py
git commit -m "feat: promote and roll back Argus by digest"
```

### Task 6: Operator Contract, End-to-End Proof, and Publication

**Files:**
- Create: `docs/argus-promotion.md` in Homelab.
- Modify: `README.md` or the existing Homelab service index to link the runbook.
- Modify: `docs/releasing.md` in Argus if verification changes the observed command surface.

**Interfaces:**
- Consumes: merged Homelab promoter installation, protected environment secrets, Tailscale ACL, and one immutable Argus digest.
- Produces: two reviewable draft PRs and, after human merge, live candidate/cutover/soak/rollback receipts linked from issue #41.

- [ ] **Step 1: Document install and recovery commands**

`docs/argus-promotion.md` must include these exact operator operations:

```markdown
# Argus immutable promotion

## Install

Create a locked `argus-deploy` account. From the canonical Homelab checkout:

```bash
sudo scripts/install-argus-promoter.sh --public-key-file /root/argus-deploy.pub
```

Install the printed forced-command line in
`/home/argus-deploy/.ssh/authorized_keys`. Install the printed sudoers rule as
`/etc/sudoers.d/argus-deploy` with owner `root:root` and mode `0440`, then
validate it with `sudo visudo -cf /etc/sudoers.d/argus-deploy`.

## Evidence

Promotion pointers live in
`/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion`.
`requested.json` is the latest admitted request, `current.json` is the loaded
production digest, `previous.json` is the rollback target, and
`known-good.json` has completed all gates and the 1,800-second soak.
Timestamped receipts under `receipts/` are immutable audit records.

## Failure

A candidate failure leaves production unchanged. A blocking failure after
cutover rewrites production to `previous.json`, verifies the actual image and
runtime identity, and reruns readiness/search/extraction/MCP canaries. Preserve
all receipts. Do not delete candidate or rollback evidence to make a retry pass.
```

- [ ] **Step 2: Run full repository verification**

Argus:

```bash
uv run pytest tests/ -q
git diff --check origin/main...HEAD
```

Homelab:

```bash
pytest -q
git diff --check origin/main...HEAD
```

Expected: both suites pass with only recorded skips/warnings; both diff checks are clean.

- [ ] **Step 3: Run a local candidate against the built digest**

After the Argus workflow publishes a digest, on `homelab-ts`:

```bash
export ARGUS_CANDIDATE_IMAGE="$(jq -er .image_ref /tmp/release-receipt.json)"
export ARGUS_CANDIDATE_PROJECT='argus-candidate-manual-proof'
export ARGUS_CANDIDATE_TOKEN="$(openssl rand -hex 32)"
docker compose -p "$ARGUS_CANDIDATE_PROJECT" -f services/argus/docker-compose.candidate.yml up -d
scripts/argus-candidate-gates.sh
docker compose -p "$ARGUS_CANDIDATE_PROJECT" -f services/argus/docker-compose.candidate.yml down --volumes
```

The `jq -e` read fails closed unless the downloaded build artifact contains an
`image_ref`; `argus-promotion-state validate` then rejects anything except the
canonical GHCR repository plus a 64-hex digest.

- [ ] **Step 4: Exercise rollback without discarding state**

Set `ARGUS_SOAK_SECONDS=30` only for the explicit rollback drill, inject a failing post-cutover probe through the test-only `ARGUS_PROMOTION_FAIL_PHASE=soak` control, and verify:

```bash
test "$(jq -r .image_ref "$STATE_ROOT/current.json")" = "$(jq -r .image_ref "$STATE_ROOT/known-good.json")"
test "$(jq -r .image_ref "$STATE_ROOT/previous.json")" = "$FAILED_CANDIDATE_IMAGE"
docker inspect argus --format '{{.Image}}'
docker inspect argus-mcp --format '{{.Image}}'
```

The promoter test control must be rejected unless `ARGUS_PROMOTION_TEST_MODE=1`; production Actions never set either variable.

- [ ] **Step 5: Publish draft PRs**

Argus:

```bash
git push -u origin codex/issue-41-immutable-promotion
gh pr create --draft --base main --head codex/issue-41-immutable-promotion \
  --title "Promote immutable homelab releases with rollback proof" \
  --body-file /tmp/argus-issue-41-pr.md
```

Homelab:

```bash
git push -u origin codex/argus-immutable-promotion
gh pr create --draft --base main --head codex/argus-immutable-promotion \
  --title "Add the Argus digest promotion transaction" \
  --body-file /tmp/homelab-argus-promotion-pr.md
```

Both PR bodies identify issue #41, list tests, and state that production installation/cutover remains human-merge gated.

- [ ] **Step 6: Record issue evidence without premature closure**

Post one issue #41 comment beginning exactly:

```markdown
> *This was generated by AI during triage.*
```

Link both draft PRs, exact commits, local/CI results, and the remaining live gates. Keep issue #41 open until the protected environment, forced command, candidate gates, exact digest cutover, 1,800-second soak, and rollback receipt are verified on `homelab-ts`.

## Validated execution corrections

Live scratch validation refined the illustrative snippets above without changing
the accepted safety model:

- The candidate applies the frozen Alembic schema before HTTP startup.
- Its extraction gate calls the real `/api/extract` route. A candidate-only
  mounted `sitecustomize.py` intercepts one exact globally classified fixture
  URL, so routing, normalization, quality, serialization, and persistence run
  without a host route, public egress, paid fallback, or production SSRF bypass.
- The MCP candidate is a stateless caller and receives only its exact allowlisted
  environment.
- Production checks target the canonical `homelab` Compose project at the
  repository root.
- After the deliberate PostgreSQL-loss probe, HTTP and MCP are restarted before
  recovery is proved; this records the service's current fail-closed recovery
  semantics rather than pretending that it reconnects automatically.
- The deployment tailnet action is pinned to the exact v4.1.3 commit and waits
  for `homelab-ts` before SSH.
- Candidate admission binds both the requested digest and baked source
  revision. The production probe omits generic extraction because that endpoint
  does not yet expose a non-billable admission boundary.
- A durable cutover marker, signal handlers, and next-run reconciliation cover
  interruption and hard-crash recovery. Verified rollback and rollback failure
  have distinct receipts with restored and actual identities.
- Installer bootstrap proves that HTTP, MCP, existing state, and the projected
  environment all describe the same release.

The checked-in workflow, Compose file, scripts, tests, and operational
documentation are authoritative where the earlier TDD snippets differ.
