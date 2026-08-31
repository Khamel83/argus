# Argus Audit and Health-Check Runbook

This is the repeatable verification suite for the source, local runtime, PostgreSQL authority, package/image, deployed control plane, and downstream boundary. Run it from the repository root. Keep those evidence layers separate.

**Recorded baseline:** deep audit begun at `be5b5a8559816b1a142d043e3461639056a4d694`, then refreshed on 2026-08-31 after integrating runtime source `6c0129ee93ee6edf5f61076dfdeaa9c55fd09089`. The publication descendant changes documentation only. Read [AUDIT_REPORT.md](AUDIT_REPORT.md) before treating an existing failure as a regression.

## 1. Safety and Evidence Rules

1. Never source the repository `.env` for hermetic checks.
2. Never print tokens, decrypted secret files, full environments, cookies, or provider payloads.
3. Never run `test-provider --live`, a paid provider, balance mutation, archive creation, cookie import, reconciliation `--apply`, or production DDL as part of the default suite.
4. Use only a named disposable PostgreSQL container/database for migration tests. The production database is never a test target.
5. Record the exact commit, command, exit code, pass/skip/fail counts, and first causal traceback.
6. Report source, remote, image, deployed runtime, authenticated capability, provider effect, and Maya receipt as separate claims.
7. A command that exits zero with a failed/not-run payload is a semantic failure.

## 2. Toolchain Preflight

```bash
pwd
git status --short --branch
git rev-parse HEAD
git rev-parse origin/main
git rev-list --left-right --count HEAD...origin/main

uv --version
.venv/bin/python --version
.venv/bin/python -m pytest --version
ruff --version
alembic --version
docker --version
docker compose version
shellcheck --version
uvx vulture==2.16 --version
uvx --from build==1.6.0 pyproject-build --version
uvx twine==7.0.0 --version

uv pip check --python .venv/bin/python
uv lock --check
```

Expected at the recorded baseline:

- CWD is the Argus repository.
- At publication refresh, local `main` was one documentation-only audit commit above runtime source and then-current `origin/main` at `6c0129e`. Recalculate this on every run; do not assume it remains true.
- Venv Python is 3.12.x. CI covers 3.11, 3.12, and 3.13.
- `uv pip check` reports 135 compatible packages.
- `uv lock --check` resolves 139 packages and exits zero.

If the worktree is dirty, inventory it before any generated command. Do not stage, format, clean, reset, or overwrite unrelated changes.

## 3. Hermetic Environment

Use one isolated shell or prefix every command with these values:

```bash
export ARGUS_AUTOLOAD_DOTENV=false
export ARGUS_DISABLE_SECRET_RESOLUTION=true
export ARGUS_EGRESS_TYPE=unknown
export ARGUS_RESIDENTIAL_POLICY=off
export ARGUS_MCP_STANDALONE=true

ARGUS_AUDIT_TMP="$(mktemp -d)"
export ARGUS_DATA_ROOT="$ARGUS_AUDIT_TMP/data"
export ARGUS_DB_URL="sqlite:///$ARGUS_AUDIT_TMP/argus.db"
export ARGUS_BUDGET_DB_PATH="$ARGUS_AUDIT_TMP/argus-budgets.db"

run_bounded() {
  local limit_seconds="$1"
  shift
  .venv/bin/python -c \
    'import subprocess, sys; subprocess.run(sys.argv[2:], check=True, timeout=float(sys.argv[1]))' \
    "$limit_seconds" "$@"
}
```

Do not set provider keys. Do not inspect whether real keys exist; disabling dotenv and secret resolution is the boundary.

## 4. Fast Source Gate

Run before and after a source change:

```bash
uv run --no-sync python scripts/generate_provider_fixture_attestations.py --check
uv run --no-sync python scripts/verify_release_contract.py
ruff check argus tests scripts
bash -n \
  deploy/start-argus.sh \
  deploy/start-argus-mcp.sh \
  scripts/install-systemd.sh \
  scripts/provision-mcp-client.sh \
  scripts/start-mcp.sh \
  scripts/start-server.sh
shellcheck \
  deploy/start-argus.sh \
  deploy/start-argus-mcp.sh \
  scripts/install-systemd.sh \
  scripts/provision-mcp-client.sh \
  scripts/start-mcp.sh \
  scripts/start-server.sh
uv run --no-sync pytest -q
```

Recorded baseline:

```text
provider fixture attestation check: PASS
release contract valid: version=1.6.4
ruff check: All checks passed!
bash -n / shellcheck: PASS
pytest: 2672 passed, 43 skipped, 4 warnings in 112.53s
```

Any changed count must be explained. Skips are not passes: inspect newly skipped tests and their markers.

## 5. Formatting, Types, and Dead-Code Checks

```bash
ruff format --check argus tests scripts
ruff format --check .
uv run --no-sync pyright
uv run --no-sync mypy
uvx vulture==2.16 argus scripts migrations --min-confidence 80
```

Recorded baseline is intentionally red:

- `ruff format --check argus tests scripts`: 127 files would be reformatted.
- Whole repository: 133 files would be reformatted.
- Pyright and Mypy are not installed/configured.
- Vulture exits 3 with four high-confidence findings:
  - unused compatibility parameter at `argus/broker/execution.py:148`;
  - unreachable bodies at `argus/extraction/firecrawl_extractor.py:64`, `argus/extraction/valyu_extractor.py:31`, and `argus/providers/valyu_answer.py:71`.

Do not bulk-format a dirty worktree as an audit action. Establish formatting in a separately reviewed change, then change the expected baseline to green.

## 6. Scorecards and Contract Fixtures

```bash
ARGUS_AUDIT_SCORECARD="$(mktemp -d)"
ARGUS_AUDIT_ACCEPTANCE="$(mktemp -d)"
uv run --no-sync python scripts/run-scorecard.py \
  --lane hermetic \
  --output "$ARGUS_AUDIT_SCORECARD/hermetic"

uv run --no-sync python scripts/run-scorecard.py \
  --lane live-config \
  --output "$ARGUS_AUDIT_SCORECARD/live-config"

uv run --no-sync python scripts/run-acceptance-v3.py \
  --fixture \
  --output "$ARGUS_AUDIT_ACCEPTANCE/acceptance-v3"
```

Expected:

- Hermetic scorecard is stable with 8 extraction cases and 24 search cases.
- Live-config output says execution was not performed. It proves configuration only.
- At the recorded baseline, acceptance-v3 exits zero but writes `preflight_failed` / `not_run`. Treat that as FAIL until the artifact says the acceptance run completed.

## 7. PostgreSQL Authority Gate

This section creates and later deletes only the exact container `argus-audit-postgres-disposable`. It refuses to start if that name or host port already exists.

### 7.1 Start a disposable PostgreSQL 16 authority

```bash
if docker ps -a --format '{{.Names}}' | rg -x 'argus-audit-postgres-disposable'; then
  echo "Refusing to reuse an existing container"
  exit 1
fi

if lsof -nP -iTCP:55439 -sTCP:LISTEN; then
  echo "Refusing to reuse occupied port 55439"
  exit 1
fi

run_bounded 300 docker run --rm --detach \
  --name argus-audit-postgres-disposable \
  -e POSTGRES_DB=argus_restore_audit \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=argus-audit-only \
  -p 127.0.0.1:55439:5432 \
  postgres:16-alpine@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777

cleanup_argus_audit_postgres() {
  docker stop argus-audit-postgres-disposable >/dev/null 2>&1 || true
}
trap cleanup_argus_audit_postgres EXIT

for attempt in 1 2 3 4 5 6 7 8 9 10; do
  if docker exec argus-audit-postgres-disposable \
    pg_isready -U postgres -d argus_restore_audit; then
    break
  fi
  sleep 1
done

docker exec argus-audit-postgres-disposable \
  pg_isready -U postgres -d argus_restore_audit
```

### 7.2 Migrate and validate schema

```bash
export ARGUS_TEST_POSTGRES_URL='postgresql+psycopg2://postgres:argus-audit-only@127.0.0.1:55439/argus_restore_audit'
export ARGUS_DB_URL="$ARGUS_TEST_POSTGRES_URL"

uv run --no-sync alembic upgrade head
uv run --no-sync alembic current

uv run --no-sync python scripts/generate_argus_schema_contract.py \
  --database-url "$ARGUS_TEST_POSTGRES_URL" \
  --schema-head 0009_retrieval_evidence \
  --check

uv run --no-sync alembic check
```

Expected:

- Upgrade applies 0001 through `0009_retrieval_evidence`.
- `alembic current` prints `0009_retrieval_evidence (head)`.
- Checked schema contract passes.
- Recorded defect: `alembic check` exits 255 and proposes removing spend/readiness/evidence tables. Do not generate or apply that proposed revision. Fix `migrations/env.py` metadata registration first.

### 7.3 Run the PostgreSQL contract suite

```bash
uv run --no-sync pytest \
  tests/test_search_ledger.py \
  tests/test_provider_spend.py \
  tests/test_maya_outbox.py \
  tests/test_api.py::TestSearchEndpoint::test_postgresql_constraint_failure_returns_503_and_rolls_back_ledger \
  tests/test_operation_ledger.py::test_postgresql_extraction_and_session_contract \
  tests/test_operation_ledger.py::test_postgresql_concurrent_session_creation_and_query_allocation \
  tests/test_operation_ledger.py::test_postgresql_concurrent_sanitized_session_url_inserts_are_idempotent \
  tests/test_recovery_database.py \
  tests/test_schema_contract_generator.py \
  tests/test_accepted_retrieval.py \
  -q --tb=short

uv run --no-sync python scripts/verify_provider_readiness_postgres.py \
  --output /tmp/argus-provider-readiness-postgres.json
```

Expected:

- 314 focused tests pass.
- Readiness verifier emits schema `argus-provider-readiness-postgres-v1` and `status=ok`.
- The database name must match `argus_restore_<purpose>` or the readiness verifier fails closed.

### 7.4 Optional shared-cluster provisioning contract

This test intentionally creates/drops `atlas` and `argus` databases and managed roles inside the disposable container only. It requires a host `psql` executable.

```bash
test -x "$(command -v psql)"
export ARGUS_TEST_ALLOW_PROVISIONING=disposable-only
export ARGUS_TEST_PSQL="$(command -v psql)"
export ARGUS_TEST_POSTGRES_URL='postgresql+psycopg2://postgres:argus-audit-only@127.0.0.1:55439/postgres'

uv run --no-sync pytest tests/test_postgres_provisioning.py -q --tb=short
```

Recorded local result: not runnable because `psql` is absent; collection fails with the explicit executable error. CI installs the client and is the current evidence for this gate.

### 7.5 Remove the disposable authority

```bash
docker stop argus-audit-postgres-disposable >/dev/null 2>&1 || true
docker ps -a \
  --filter name='^/argus-audit-postgres-disposable$' \
  --format '{{.Names}} {{.Status}}'
```

Expected: the second command prints nothing because `--rm` removes the stopped container. Its test-only data is not recoverable.

## 8. CLI Diagnostics Without Provider Spend

Keep the hermetic environment from section 3:

```bash
uv run --no-sync argus --version
uv run --no-sync argus paths --json
uv run --no-sync argus mcp check
uv run --no-sync argus doctor --json
uv run --no-sync argus health
uv run --no-sync argus budgets
uv run --no-sync argus test-provider -p brave
uv run --no-sync argus test-provider -p duckduckgo
uv run --no-sync argus check-balances
```

Expected:

- Version is 1.6.4 at the recorded baseline.
- Paths point only into the temporary root.
- MCP check validates package/context/config and does not invoke a provider.
- Provider tests are fixture-only unless `--live` is explicitly supplied. Never supply it in the default suite.
- With no keys, health is degraded/disabled and balance check says there is nothing to check.
- Recorded defect: doctor returns exit 0 while `Providers.ok=false` and `0 ready`. Read the JSON, not only the process status.

Do not run `mcp init`, `cookies import`, `corpus import-docs-cache`, ledger `--apply`, set-balance, or any live provider option during a read-only audit.

## 9. Distribution and Release Gate

The documented `python -m build` command requires the build frontend, which is not in the local dev environment. Use an isolated frontend and write distributions outside the repository:

```bash
ARGUS_AUDIT_DIST="$(mktemp -d)"
uvx --from build==1.6.0 pyproject-build \
  --outdir "$ARGUS_AUDIT_DIST" \
  .

uvx twine==7.0.0 check "$ARGUS_AUDIT_DIST"/*
uv run --no-sync python scripts/verify_release_contract.py
```

Expected:

- Wheel and sdist `argus_search-<version>` are built.
- Twine checks both as PASSED.
- Version contract reports 1.6.4 at the recorded baseline.
- Current Setuptools emits deprecations for TOML-table license metadata and license classifiers, with a 2027-02-18 support deadline.

Release review must also inspect `.github/workflows/publish.yml`. A successful job is not proof of PyPI publication while upload ends with `|| true`.

## 10. Compose, Image, and Browser Admission

### Configuration-only

```bash
docker compose config --quiet
docker compose --profile proxy config --quiet
```

Both must exit zero.

### Full image gate

Use the same commands as `.github/workflows/ci.yml`. This downloads public base images and creates the exact local tag `argus-browser-canary:audit`:

```bash
run_bounded 1800 docker build \
  --build-arg VCS_REF="$(git rev-parse HEAD)" \
  --tag argus-browser-canary:audit \
  .

run_bounded 180 docker run --rm --init \
  --network none \
  --memory 1g --memory-swap 1g --pids-limit 256 --shm-size 256m \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=256m \
  --security-opt no-new-privileges:true \
  --security-opt seccomp=./docker/playwright-seccomp.json \
  --volume "$PWD/scripts/browser_canary.py:/canary/browser_canary.py:ro" \
  argus-browser-canary:audit \
  python /canary/browser_canary.py --memory-limit-mib 1024

run_bounded 180 docker run --rm --init \
  --network none \
  --memory 512m --memory-swap 512m --pids-limit 256 --shm-size 128m \
  --read-only \
  --tmpfs /tmp:rw,noexec,nosuid,size=128m \
  --tmpfs /ms-playwright:rw,noexec,nosuid,size=1m \
  --security-opt no-new-privileges:true \
  --security-opt seccomp=./docker/playwright-seccomp.json \
  --volume "$PWD/scripts/browser_canary.py:/canary/browser_canary.py:ro" \
  argus-browser-canary:audit \
  python /canary/browser_canary.py \
    --expect-missing --memory-limit-mib 512
```

Recorded local boundary: source-checkout runtime-manifest admission failed because a matching browser was absent; Docker metadata lookup failed because the noninteractive macOS session could not use the credential helper. Remote CI for `origin/main` completed the immutable image workflow successfully. Do not convert remote CI into a local-image PASS.

## 11. Deployed Control-Plane Checks

These public probes are read-only and do not prove acquisition:

```bash
for endpoint in live startup ready; do
  curl --silent --show-error --max-time 10 \
    --write-out "${endpoint} HTTP %{http_code} time=%{time_total}s\n" \
    "https://homelab.deer-panga.ts.net/api/${endpoint}"
  printf '\n'
done
```

Expected contract:

- `/api/live`: HTTP 200, `status=alive`.
- `/api/startup`: HTTP 200, initialized=true, current version.
- `/api/ready`: HTTP 200 only when ready=true; inspect degraded reason codes.

For detailed authority, PostgreSQL, browser, outbox, recovery, and identity checks, use the secret-safe `docker exec` procedure in `docs/operations.md:35`. It constructs the authorization header inside the container and prints only selected status fields.

An authenticated search/extraction/MCP canary, provider-side balance/attempt evidence, restart persistence, and Maya durable receipt are separate promotion gates. They can invoke external systems and must use the canonical operator workflow and explicit spend/authority rules; they are not part of this default repository audit.

## 12. Remote Source and CI Evidence

```bash
git ls-remote origin refs/heads/main
gh run list \
  --branch main \
  --limit 12 \
  --json databaseId,workflowName,headSha,status,conclusion,createdAt,updatedAt,url
```

Compare the remote SHA with the audited local SHA and the deployed build identity. If they differ, report each separately. Do not merge/fetch/deploy merely to make the values match.

## 13. Failure Debugging Cheat Sheet

| Symptom | First broken edge to verify | Likely cause / next safe check |
|---|---|---|
| Pytest imports real config or secrets | Isolation variables | Confirm `ARGUS_AUTOLOAD_DOTENV=false` and `ARGUS_DISABLE_SECRET_RESOLUTION=true` before importing Argus |
| Full test count changes | Collection/skips | Run `pytest --collect-only -q` and inspect new skip reasons |
| PostgreSQL test skips | `ARGUS_TEST_POSTGRES_URL` | Confirm disposable DB URL and `pg_isready`; never substitute production |
| Provisioning test collection error | Host `psql` | Install/use a client or rely on CI; do not weaken `disposable-only` |
| Readiness verifier rejects DB | Database name | Use `argus_restore_<purpose>`; protected names fail closed |
| `alembic check` proposes removals | Target metadata registration | Do not create the revision; load SpendBase, ReadinessBase, and evidence model registrations |
| Doctor exits zero but provider is false | Semantic exit bug | Inspect JSON `ok` fields; count this as FAIL |
| Acceptance runner exits zero/not-run | Artifact status | Inspect the generated status/score, not just exit code |
| Docker build cannot read keychain | Noninteractive credential helper | Use a valid BuildKit session or current remote CI; do not claim image verification |
| Runtime manifest says browser missing | Playwright/image mismatch | Build the production image and run both memory canaries |
| `ruff format` produces a large diff | Existing baseline | Do not format during audit; isolate a dedicated baseline change |
| MCP check fails | Authority URL/token/package context | Check presence and role without printing token values; do not switch to standalone in production |
| `/live` is 200 but capability fails | Wrong health layer | Check startup, ready, admin dependency status, then authenticated operation and durable receipt |
| Extraction shows `PERSISTENCE_FAILED` | Error flattening | Inspect authority logs/evidence around the request ID; the underlying cause may be network, SSRF rejection, code, or DB |
| Paid extraction evidence says zero | Missing spend gateway | Disable You/Jina extraction and inspect provider account evidence; do not accept the synthetic spend ref |
| Accepted-cache UTC test intermittently reports `HIT_INELIGIBLE` | Five-second wall-clock freshness window crossed under suite load | Run `tests/test_task2_fix.py::test_accepted_cache_reload_uses_utc_receipt_age_and_no_second_extractor --durations=1`; do not hide the full-suite failure. The publication run passed 5/5 focused repetitions and two complete runs, but the timing boundary remains architectural test debt. |
| Remote result has unknown egress | Legacy adapter projection | Trace worker identity through `RemoteProviderClient` and trusted provenance normalization |
| Workflow artifact visible cross-caller | Missing owner check | Disable/share no run IDs until principal-to-run authorization is enforced |

## 14. Audit Artifact Refresh Protocol

After a material change to runtime, transport, provider/extraction policy, persistence, workflow, configuration, packaging, or deployment:

1. Run the applicable sections above.
2. Update [AUDIT_REPORT.md](AUDIT_REPORT.md) with a new UTC timestamp, commit, exact matrix result, raw summary, corrected severity, and ordered punch list.
3. Update [SYSTEM_TOPOLOGY.md](SYSTEM_TOPOLOGY.md) when an entrypoint, interface, data owner, persistence boundary, dependency, or dead path changes.
4. Preserve historical facts only when labeled; remove findings that are no longer true.
5. Run the structural/link checks below, then `git diff --check` and
   `git status --short --branch`:

   ```bash
   test -f .audit/AUDIT_REPORT.md
   test -f .audit/SYSTEM_TOPOLOGY.md
   test -f .audit/HEALTH_CHECK_RUNBOOK.md
   rg -n 'SYSTEM_TOPOLOGY\.md' .audit/AUDIT_REPORT.md
   rg -n 'AUDIT_REPORT\.md|SYSTEM_TOPOLOGY\.md' \
     .audit/HEALTH_CHECK_RUNBOOK.md
   rg -n '\.audit/(AUDIT_REPORT|SYSTEM_TOPOLOGY|HEALTH_CHECK_RUNBOOK)\.md' \
     AGENTS.md
   git diff --check
   git status --short --branch
   ```
6. Never mark the repository ready solely because tests, CI, `/healthz`, or HTTP 200 are green. Require the evidence layer the change affects.
