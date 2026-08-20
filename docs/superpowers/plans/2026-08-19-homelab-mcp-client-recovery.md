# Homelab MCP Client Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent the client-provisioning helper from silently registering an unusable localhost MCP endpoint, and document the verified Tailscale HTTPS path for Homelab clients.

**Architecture:** The generic helper remains topology-neutral: it only enters remote mode when the caller explicitly supplies `ARGUS_REMOTE_URL`. The deployment-specific documentation names the private Tailscale Serve HTTPS URL and distinguishes it from Docker loopback listeners and raw tailnet-IP TLS requests.

**Tech Stack:** Bash, Python subprocess test harness, Markdown.

## Global Constraints

- Keep Argus authority and MCP containers private; do not bind Docker ports to the tailnet IP.
- Homelab remote client configuration must use HTTPS and contain exactly one `/mcp` suffix; callers may supply either the listener base URL or its existing `/mcp` endpoint.
- Never place production credential values in source, tests, docs, or command output.
- Preserve local stdio when no remote URL is supplied and `ARGUS_LOCAL_COMMAND` is executable.
- An explicit `ARGUS_REMOTE_URL` always selects remote configuration, even on a machine with a local Argus executable.

---

### Task 1: Fail closed when remote provisioning lacks an endpoint

**Files:**
- Create: `tests/test_provision_mcp_client.py`
- Modify: `scripts/provision-mcp-client.sh`

**Interfaces:**
- Consumes: `ARGUS_REMOTE_URL`, `ARGUS_API_KEY`, `ARGUS_LOCAL_COMMAND`, and the positional target accepted by `scripts/provision-mcp-client.sh`.
- Produces: exit status `2` and an `ARGUS_REMOTE_URL is required` diagnostic before the generated Python configuration writer or SSH is invoked.

- [x] **Step 1: Write the failing regression test**

```python
def test_provisioning_refuses_remote_fallback_to_localhost(tmp_path):
    env = os.environ | {
        "HOME": str(tmp_path),
        "ARGUS_API_KEY": "test-token",
        "ARGUS_LOCAL_COMMAND": str(tmp_path / "missing-argus"),
    }
    env.pop("ARGUS_REMOTE_URL", None)

    result = subprocess.run(
        ["bash", str(SCRIPT), "local"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "ARGUS_REMOTE_URL is required" in result.stderr
    assert not (tmp_path / ".claude.json").exists()
```

- [x] **Step 2: Run the focused test to verify it fails**

Run: `uv run --extra mcp --extra dev pytest tests/test_provision_mcp_client.py::test_provisioning_refuses_remote_fallback_to_localhost -q`

Expected: FAIL because the helper writes a remote `http://localhost:8271/mcp` configuration and exits successfully.

- [x] **Step 3: Implement the minimal validation**

```bash
MODE="remote"
if [[ -z "$ARGUS_REMOTE_URL" && "$TARGET" == "local" && -x "$ARGUS_LOCAL_COMMAND" ]]; then
    MODE="local"
else
    if [[ -z "$ARGUS_REMOTE_URL" ]]; then
        echo "Error: ARGUS_REMOTE_URL is required for remote HTTP provisioning." >&2
        exit 2
    fi
    if [[ -z "$ARGUS_API_KEY" ]]; then
        echo "Error: ARGUS_API_KEY is required for remote HTTP provisioning." >&2
        exit 2
    fi
fi

ARGUS_REMOTE_URL="${ARGUS_REMOTE_URL%/}"
MCP_URL="$ARGUS_REMOTE_URL"
[[ "$MCP_URL" == */mcp ]] || MCP_URL="${MCP_URL}/mcp"
```

Remove the old `http://localhost:8271` fallback and calculate `MCP_URL` only after the validation block.

- [x] **Step 4: Run the focused test to verify it passes**

Run: `uv run --extra mcp --extra dev pytest tests/test_provision_mcp_client.py::test_provisioning_refuses_remote_fallback_to_localhost -q`

Expected: PASS.

- [x] **Step 5: Add an explicit-endpoint regression assertion**

```python
def test_provisioning_uses_the_explicit_remote_https_endpoint(tmp_path):
    endpoint = "https://homelab.deer-panga.ts.net:8443"
    env = os.environ | {
        "HOME": str(tmp_path),
        "ARGUS_API_KEY": "test-token",
        "ARGUS_REMOTE_URL": endpoint,
        "ARGUS_LOCAL_COMMAND": str(tmp_path / "missing-argus"),
    }
    result = subprocess.run(["bash", str(SCRIPT), "local"], cwd=REPO_ROOT, env=env, text=True, capture_output=True)

    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert config["mcpServers"]["argus"]["url"] == endpoint + "/mcp"
```

- [x] **Step 6: Run both helper tests**

Run: `uv run --extra mcp --extra dev pytest tests/test_provision_mcp_client.py -q`

Expected: PASS with both no-fallback and explicit-HTTPS behavior covered.

- [x] **Step 7: Add remote-precedence and suffix-normalization regressions**

```python
def test_explicit_remote_url_wins_over_an_executable_local_adapter(tmp_path):
    local = tmp_path / "argus"
    local.write_text("#!/usr/bin/env bash\nexit 0\n")
    local.chmod(0o755)
    config = run_provision(
        tmp_path,
        remote_url="https://homelab.deer-panga.ts.net:8443",
        local_command=local,
    )
    assert config["mcpServers"]["argus"]["url"] == "https://homelab.deer-panga.ts.net:8443/mcp"


def test_provisioning_does_not_duplicate_an_existing_mcp_path(tmp_path):
    config = run_provision(
        tmp_path,
        remote_url="https://homelab.deer-panga.ts.net:8443/mcp",
    )
    assert config["mcpServers"]["argus"]["url"] == "https://homelab.deer-panga.ts.net:8443/mcp"


def test_executable_local_adapter_uses_stdio_when_no_remote_url_is_set(tmp_path):
    local = tmp_path / "argus"
    local.write_text("#!/usr/bin/env bash\nexit 0\n")
    local.chmod(0o755)
    config = run_provision(tmp_path, local_command=local)
    assert config["mcpServers"]["argus"]["command"] == str(local)
```

- [x] **Step 8: Run the new tests to verify the two new remote behaviors fail**

Run: `uv run --extra mcp --extra dev pytest tests/test_provision_mcp_client.py -q`

Expected: FAIL because a local executable currently overrides an explicit
remote URL and a supplied `/mcp` path becomes `/mcp/mcp`.

- [x] **Step 9: Make explicit remote configuration win and normalize the endpoint**

```bash
if [[ -z "$ARGUS_REMOTE_URL" && "$TARGET" == "local" && -x "$ARGUS_LOCAL_COMMAND" ]]; then
    MODE="local"
else
    # existing explicit URL and key validation
fi

ARGUS_REMOTE_URL="${ARGUS_REMOTE_URL%/}"
if [[ "$ARGUS_REMOTE_URL" == */mcp ]]; then
    MCP_URL="$ARGUS_REMOTE_URL"
else
    MCP_URL="${ARGUS_REMOTE_URL}/mcp"
fi
```

- [x] **Step 10: Run the complete helper test module**

Run: `uv run --extra mcp --extra dev pytest tests/test_provision_mcp_client.py -q`

Expected: PASS with explicit remote precedence, one `/mcp` suffix,
fail-closed remote validation, and preserved local stdio behavior.

### Task 2: Document the verified Homelab listener boundary

**Files:**
- Modify: `docs/mcp-clients.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the Homelab Tailscale Serve listener at `https://homelab.deer-panga.ts.net:8443/mcp`.
- Produces: copy-paste remote-provisioning instructions that never use `localhost`, `127.0.0.1:8271`, or a raw `100.x` HTTPS address from a client.

- [x] **Step 1: Replace the generic remote provisioning example with the canonical Homelab form**

```bash
export ARGUS_REMOTE_URL=https://homelab.deer-panga.ts.net:8443
export ARGUS_API_KEY=replace-with-the-existing-Argus-client-token
scripts/provision-mcp-client.sh local
```

State that the helper normalizes a listener base URL or an already-suffixed `/mcp` URL to exactly one `/mcp` path.

- [x] **Step 2: Add the topology warning and no-secret verification command**

```bash
curl -I https://homelab.deer-panga.ts.net:8443/mcp
```

Document `401 Unauthorized` as the expected proof that Tailscale routing and TLS work before a bearer token is supplied. State that `127.0.0.1:8271` is Homelab-only Docker loopback and raw tailnet-IP HTTPS fails certificate/SNI validation.

- [x] **Step 3: Review the rendered diff for conflicting localhost guidance**

Run: `rg -n 'localhost:8271|127\.0\.0\.1:8271|100\.x\.x\.x:8001' scripts/provision-mcp-client.sh docs/mcp-clients.md`

Expected: no stale provisioning default; generic server-build examples may retain `100.x.x.x:8001` only when labeled as a direct listener topology.

- [x] **Step 4: Align the README remote-client examples and `argus mcp init` URL normalization**

Replace raw-IP and localhost provisioning examples with the same explicit
Homelab Tailscale Serve base URL. State that raw `100.x` HTTPS is not the
Homelab endpoint and that the helper accepts the listener base URL or an
already-suffixed `/mcp` URL without duplication.

### Task 3: Verify the bounded repair

**Files:**
- Verify: `tests/test_provision_mcp_client.py`
- Verify: `tests/test_cli.py`
- Verify: `scripts/provision-mcp-client.sh`
- Verify: `docs/mcp-clients.md`
- Verify: `README.md`
- Verify: `argus/cli/main.py`

**Interfaces:**
- Consumes: the focused regression tests and Bash parser.
- Produces: evidence that accidental localhost configuration is rejected and explicit Tailscale HTTPS configuration is generated exactly once.

- [x] **Step 1: Run static and focused verification**

Run:

```bash
bash -n scripts/provision-mcp-client.sh
uv run --extra mcp --extra dev pytest tests/test_provision_mcp_client.py -q
uv run --extra mcp --extra dev pytest tests/test_cli.py -q
```

Expected: both commands exit `0`.

- [x] **Step 2: Inspect the exact diff and worktree status**

Run:

```bash
git diff --check
git diff -- scripts/provision-mcp-client.sh docs/mcp-clients.md tests/test_provision_mcp_client.py
git status --short
```

Expected: only the script, documentation, regression test, and implementation plan are changed; no credentials appear.

- [x] **Step 3: Commit the bounded repair**

```bash
git add scripts/provision-mcp-client.sh docs/mcp-clients.md tests/test_provision_mcp_client.py docs/superpowers/plans/2026-08-19-homelab-mcp-client-recovery.md
git commit -m "fix: require explicit remote MCP endpoint"
```
