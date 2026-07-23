# Argus Fleet Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Argus the canonical, attributed, budget-guarded retrieval service for the Clio/Maya/Hermes fleet — closing issues #19 and #20, wiring caller attribution end-to-end, and converging all callers on one deployment.

**Architecture:** Argus (search/extraction broker) runs as two launchd services on the mac mini — HTTP API on :8300 and streamable-http MCP on :8301 — reachable over Tailscale (`omars-mac-mini`). Clio calls it over HTTP with caller attribution; Hermes calls it over MCP (harness rule); Maya never calls it. Server-side per-caller tier caps prevent automated callers from burning one-time provider credits (the Valyu incident). Research packs become pipeable to Maya via a JSON manifest + a `read_pack_file` MCP tool.

**Tech Stack:** Python 3.12 + uv, FastAPI, FastMCP, Click, pytest, launchd (mac mini), httpx (Clio side).

---

## Context for a zero-context engineer

### The four services

| Service | Role | Where it runs | Transport |
|---|---|---|---|
| **Argus** (this repo) | Internet retrieval: 14 search providers, 12-step extraction, research-pack workflows | mac mini (being canonicalized by this plan) | HTTP `:8300` + MCP `:8301` |
| **Clio** | Executor: intake → classify → GitHub issue → coding-agent dispatch → PR audit | mac mini, launchd, `:8100` | HTTP only |
| **Maya** | Memory: file ingestion, vault, briefings, ContextPacks | mac mini, launchd (`com.maya.server`), `:8200` | HTTP only |
| **Hermes** | Slack-facing agent (Docker container on the homelab host, OpenClaw-style runtime) | homelab host | Slack + tool calls |

Hard rules (from `maya/docs/CONTEXT-CONTRACT.md`): Clio never calls Maya; Maya may call Clio; Hermes calls both; Maya never calls Argus; Argus never stores personal memory and never calls Maya.

### Repo checkouts on this machine

| Repo | Dev checkout (work + commit here) | Service checkout (deploy target) |
|---|---|---|
| argus | `/Volumes/2TB_SSD/GitHub/argus` | `/Users/macmini/github/argus` |
| clio | `/Volumes/2TB_SSD/GitHub/clio` | discover in Task 12 (likely `/Users/macmini/github/clio`) |
| hermes | `/Volumes/2TB_SSD/GitHub/hermes` | deployed by `scripts/hermes-bootstrap.sh` on homelab (operator step) |
| maya | `/Volumes/2TB_SSD/GitHub/maya` | `/Users/macmini/github/maya` (docs-only change; no restart needed) |

### Decisions already made (do not relitigate)

1. **Issue #19** — the `build_research_pack` MCP tool already exists (`argus/mcp/server.py:146`). The gap is output shape: it returns prose markdown and leaves files on disk. Fix: JSON manifest response format + new `read_pack_file` MCP tool so an agent (Hermes) can pipe pack files to Maya `POST /ingest/file` without a shell escape. Argus must NOT push to Maya itself.
2. **Issue #20** — README gets a "Using Argus from MCP vs HTTP" section linking to `khamel83/maya` `docs/CONTEXT-CONTRACT.md`.
3. **Contract fix** — Clio's existing Argus usage (URL extraction at intake, Lane B research, search-and-summarize workflow) is legitimate. `maya/docs/CONTEXT-CONTRACT.md` gets updated to permit it (with mandatory caller attribution); the "never" that remains is using Argus as memory/agent-context.
4. **Guardrails** — server-side caller tier caps: `ARGUS_CALLER_TIER_CAPS="clio*:1,hermes*:1"` means callers matching those fnmatch patterns may route to monthly tier-1 providers, including Parallel, but never route to tier-3 one-time-credit providers (Serper, You.com, Valyu, SearchAPI). This is enforced in the broker regardless of what the caller passes.
5. **Canonical deployment** — mac mini, launchd, HTTP `:8300`, streamable-http MCP `:8301`, bound to `0.0.0.0` (Tailscale-only network exposure; the mini is not port-forwarded). Tailscale hostname: `omars-mac-mini` (100.113.216.27). The old inconsistent deployments (Clio's `:8005` default, homelab Docker) get converged/decommissioned.
6. **Hermes** — MCP toolset registration in `config/config.yaml.template` + a SOUL.md section. Bearer token comes from the secrets vault via `load_secret_env ARGUS_API_KEY` in the bootstrap script (`${ARGUS_API_KEY}` interpolation in the template, same pattern as `${PENNY_WEBHOOK_SECRET}`).

### Provider tiers (for the caps work)

Tier 0 = free (SearXNG, DuckDuckGo, Yahoo, GitHub, WolframAlpha). Tier 1 = monthly recurring (Brave, Tavily, Exa, Linkup, Parallel). Tier 3 = one-time credits (Serper, You.com, Valyu, SearchAPI). `BudgetTracker.get_provider_tier(provider)` returns the tier.

### Conventions (argus repo)

- `uv run pytest ...` — never the system interpreter.
- User-facing changes update `README.md`, `.env.example`, and `CHANGELOG.md` under `[Unreleased]`. Never bump versions in `pyproject.toml`/`server.json`.
- Commits: conventional style (`feat:`, `fix:`, `docs:`), small and frequent.

---

# Phase 1 — Argus core (repo: `/Volumes/2TB_SSD/GitHub/argus`)

### Task 1: Caller tier caps — config parsing

**Files:**
- Modify: `argus/config.py` (ArgusConfig dataclass ~line 108–142; `load()` ~line 274–380)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
class TestCallerTierCaps:
    def test_parses_caller_tier_caps(self):
        from argus.config import EnvironmentConfigLoader

        loader = EnvironmentConfigLoader(
            environ={"ARGUS_CALLER_TIER_CAPS": "clio*:1,hermes*:1, atlas:0"},
            secrets_resolver=_NullSecrets(),
        )
        config = loader.load()
        assert config.caller_tier_caps == {"clio*": 1, "hermes*": 1, "atlas": 0}

    def test_caller_tier_caps_default_empty(self):
        from argus.config import EnvironmentConfigLoader

        loader = EnvironmentConfigLoader(environ={}, secrets_resolver=_NullSecrets())
        assert loader.load().caller_tier_caps == {}

    def test_caller_tier_caps_skips_malformed_entries(self):
        from argus.config import EnvironmentConfigLoader

        loader = EnvironmentConfigLoader(
            environ={"ARGUS_CALLER_TIER_CAPS": "clio*:notanumber,,hermes:1"},
            secrets_resolver=_NullSecrets(),
        )
        assert loader.load().caller_tier_caps == {"hermes": 1}
```

If `tests/test_config.py` has no `_NullSecrets` helper, add one at module level (check first — the file may already stub secrets; reuse whatever pattern exists there for constructing an `EnvironmentConfigLoader` without hitting the real secrets CLI):

```python
class _NullSecrets:
    def get(self, key):
        return None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_config.py -k caller_tier -v`
Expected: FAIL with `TypeError` (unexpected keyword) or `AttributeError: caller_tier_caps`

- [ ] **Step 3: Implement**

In `argus/config.py`, add the field to `ArgusConfig` (after `log_provider_payloads: bool = False`, ~line 141):

```python
    caller_tier_caps: dict[str, int] = field(default_factory=dict)
```

In `EnvironmentConfigLoader.load()`, after the `ARGUS_EGRESS_NODES` parsing block (~line 299) add:

```python
        # Parse ARGUS_CALLER_TIER_CAPS=clio*:1,hermes*:1
        # fnmatch pattern -> max provider tier that caller may use.
        _caps_raw = self.get_str("ARGUS_CALLER_TIER_CAPS", "")
        _caller_tier_caps: dict[str, int] = {}
        for entry in _caps_raw.split(","):
            entry = entry.strip()
            if not entry or ":" not in entry:
                continue
            pattern, _, cap = entry.rpartition(":")
            try:
                _caller_tier_caps[pattern.strip()] = int(cap.strip())
            except ValueError:
                continue
```

And in the `return ArgusConfig(...)` call add:

```python
            caller_tier_caps=_caller_tier_caps,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS (all, including pre-existing tests)

- [ ] **Step 5: Commit**

```bash
git add argus/config.py tests/test_config.py
git commit -m "feat(config): parse ARGUS_CALLER_TIER_CAPS into caller_tier_caps"
```

### Task 2: Caller tier caps — broker enforcement

**Files:**
- Modify: `argus/broker/execution.py` (`ProviderExecutor.__init__` ~line 41; `execute()` loop ~line 84–145)
- Modify: `argus/broker/router.py` (`SearchBroker.__init__` executor construction, ~line 47)
- Test: `tests/test_caller_caps.py` (create)

Note: this task also fixes a latent bug — today the tier lookup and `free_only` check happen *after* the remote-egress branch, so `free_only` never applies to searches delegated to remote workers. Hoisting the tier check above the reachability block fixes both.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_caller_caps.py`. Before writing, open `tests/test_broker.py` and reuse its existing fake-provider/fixture pattern if one exists (preferred). If none fits, use this self-contained version:

```python
"""Tests for per-caller provider tier caps in ProviderExecutor."""

import pytest

from argus.broker.budgets import BudgetTracker
from argus.broker.execution import ProviderExecutor, caller_tier_cap
from argus.broker.health import HealthTracker
from argus.models import ProviderName, SearchMode, SearchQuery, SearchResult


class _StubProvider:
    def __init__(self, name: ProviderName):
        self._name = name

    def is_available(self) -> bool:
        return True

    async def search(self, query):
        return [
            SearchResult(
                url="https://example.com",
                title="t",
                snippet="s",
                domain="example.com",
                provider=self._name.value,
            )
        ]


def _executor(caps: dict[str, int]) -> ProviderExecutor:
    providers = {
        ProviderName.DUCKDUCKGO: _StubProvider(ProviderName.DUCKDUCKGO),  # tier 0
        ProviderName.SERPER: _StubProvider(ProviderName.SERPER),          # tier 3
    }
    return ProviderExecutor(
        providers=providers,
        health_tracker=HealthTracker(),
        budget_tracker=BudgetTracker(persist_path=None),
        caller_tier_caps=caps,
    )


class TestCallerTierCapHelper:
    def test_no_caller_means_no_cap(self):
        assert caller_tier_cap("", {"clio*": 1}) is None

    def test_no_caps_means_no_cap(self):
        assert caller_tier_cap("clio-lane-b", {}) is None

    def test_fnmatch_pattern_matches(self):
        assert caller_tier_cap("clio-lane-b", {"clio*": 1}) == 1

    def test_exact_match(self):
        assert caller_tier_cap("hermes", {"hermes": 1}) == 1

    def test_non_matching_caller_uncapped(self):
        assert caller_tier_cap("interactive-cli", {"clio*": 1}) is None

    def test_most_restrictive_wins(self):
        assert caller_tier_cap("clio-x", {"clio*": 1, "clio-x": 0}) == 0


class TestExecutorEnforcement:
    @pytest.mark.asyncio
    async def test_capped_caller_skips_tier3_provider(self):
        executor = _executor({"clio*": 1})
        query = SearchQuery(
            query="q", mode=SearchMode.DISCOVERY, max_results=10, caller="clio-lane-b"
        )
        outcome = await executor.execute(
            query, [ProviderName.DUCKDUCKGO, ProviderName.SERPER]
        )
        serper_traces = [t for t in outcome.traces if t.provider == ProviderName.SERPER]
        assert serper_traces and serper_traces[0].status == "skipped"
        assert "caller tier cap" in (serper_traces[0].error or "")
        assert "duckduckgo" in outcome.provider_results

    @pytest.mark.asyncio
    async def test_uncapped_caller_reaches_tier3(self):
        executor = _executor({"clio*": 1})
        query = SearchQuery(
            query="q", mode=SearchMode.DISCOVERY, max_results=50, caller="someone-else"
        )
        outcome = await executor.execute(query, [ProviderName.SERPER])
        serper_traces = [t for t in outcome.traces if t.provider == ProviderName.SERPER]
        assert serper_traces and serper_traces[0].status != "skipped"
```

Adapt field names to reality if the `ProviderExecutionOutcome` attribute isn't `provider_results`/`traces` — check the dataclass at the top of `argus/broker/execution.py` and mirror how `tests/test_broker.py` asserts on outcomes.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_caller_caps.py -v`
Expected: FAIL with `ImportError: cannot import name 'caller_tier_cap'`

- [ ] **Step 3: Implement enforcement**

In `argus/broker/execution.py`:

Add near the top (after imports):

```python
import fnmatch
from typing import Mapping


def caller_tier_cap(caller: str, caps: Mapping[str, int]) -> int | None:
    """Max provider tier this caller may use, or None if uncapped.

    Patterns are fnmatch-style; the most restrictive matching cap wins.
    """
    if not caller or not caps:
        return None
    matches = [cap for pattern, cap in caps.items() if fnmatch.fnmatch(caller, pattern)]
    return min(matches) if matches else None
```

Extend `ProviderExecutor.__init__` with a new keyword and store it:

```python
        caller_tier_caps: Mapping[str, int] | None = None,
```
```python
        self._caller_tier_caps = dict(caller_tier_caps or {})
```

In `execute()`, move the tier lookup **above** the reachability block. Today the loop reads (~lines 109–142):

```python
            # Reachability check — route to worker if local is blocked
            best_egress = self._reachability.best_egress(pname)
            ...
            tier = self._budgets.get_provider_tier(pname)

            if query.free_only and tier > 0:
                traces.append(ProviderTrace(provider=pname, status="skipped", error="free_only mode"))
                continue
```

Restructure so that immediately after the health check (`if health_status is not None: ... continue`) it reads:

```python
            tier = self._budgets.get_provider_tier(pname)

            cap = caller_tier_cap(query.caller, self._caller_tier_caps)
            if cap is not None and tier > cap:
                traces.append(ProviderTrace(
                    provider=pname,
                    status="skipped",
                    error=f"caller tier cap: caller {query.caller!r} limited to tier <= {cap}",
                ))
                continue

            if query.free_only and tier > 0:
                traces.append(ProviderTrace(provider=pname, status="skipped", error="free_only mode"))
                continue

            # Reachability check — route to worker if local is blocked
            best_egress = self._reachability.best_egress(pname)
```

Then delete the now-duplicated `tier = self._budgets.get_provider_tier(pname)` and `free_only` block from their old position below the remote branch. The `if query.providers is None and tier > 0 and total_results_so_far >= query.max_results:` check stays where it is (below the remote branch) — it depends on `total_results_so_far`, and `tier` is still in scope.

In `argus/broker/router.py`, `SearchBroker.__init__` (~line 47), add the kwarg to the `ProviderExecutor(...)` construction:

```python
        self._executor = executor or ProviderExecutor(
            providers=self._providers,
            health_tracker=self._health,
            budget_tracker=self._budgets,
            reachability=self._reachability,
            egress_nodes=self._egress_nodes,
            caller_tier_caps=self._config.caller_tier_caps,
        )
```

(`self._config = get_config()` is assigned a few lines above — keep the assignment order so `self._config` exists first.)

- [ ] **Step 4: Run the full broker test surface**

Run: `uv run pytest tests/test_caller_caps.py tests/test_broker.py tests/test_api.py -v`
Expected: PASS. If a pre-existing test asserted that `free_only` is checked after reachability (unlikely), update it — the hoist is intentional.

- [ ] **Step 5: Commit**

```bash
git add argus/broker/execution.py argus/broker/router.py tests/test_caller_caps.py
git commit -m "feat(broker): enforce per-caller provider tier caps; apply free_only before remote egress"
```

### Task 3: JSON response format for the research-pack MCP tool (issue #19, part 1)

**Files:**
- Modify: `argus/mcp/tools.py` (`_serialize_workflow` ~line 359, `build_research_pack` ~line 439)
- Modify: `argus/mcp/server.py` (`build_research_pack` registration ~line 145–159)
- Test: `tests/test_mcp_pack_tools.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_mcp_pack_tools.py`:

```python
"""Tests for machine-readable MCP research-pack output (issue #19)."""

import json
from datetime import datetime

from argus.mcp.tools import _serialize_workflow_json
from argus.workflows.models import (
    StoredDocument,
    WorkflowArtifact,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)


def _run(tmp_path):
    report = tmp_path / "SUMMARY.md"
    report.write_text("# Summary\n", encoding="utf-8")
    doc = tmp_path / "doc1.md"
    doc.write_text("content", encoding="utf-8")
    return WorkflowResult(
        run_id="run-123",
        kind=WorkflowKind.BUILD_RESEARCH_PACK,
        status=WorkflowStatus.COMPLETED,
        target="fastapi",
        created_at=datetime(2026, 7, 5),
        snapshot_dir=str(tmp_path),
        report_path=str(report),
        manifest_path=str(tmp_path / "manifest.json"),
        artifacts=[WorkflowArtifact(kind="report", path=str(report))],
        documents=[
            StoredDocument(
                id="d1", url="https://x.test", title="Doc 1", artifact_path=str(doc)
            )
        ],
    )


def test_serialize_workflow_json_shape(tmp_path):
    payload = json.loads(_serialize_workflow_json(_run(tmp_path)))
    assert payload["run_id"] == "run-123"
    assert payload["status"] == "completed"
    assert payload["report_path"].endswith("SUMMARY.md")
    paths = {f["path"] for f in payload["files"]}
    assert str(tmp_path / "SUMMARY.md") in paths
    assert str(tmp_path / "doc1.md") in paths
    for f in payload["files"]:
        assert isinstance(f["bytes"], int)


def test_serialize_workflow_json_error_run(tmp_path):
    run = _run(tmp_path)
    run.error = "boom"
    run.status = WorkflowStatus.FAILED
    payload = json.loads(_serialize_workflow_json(run))
    assert payload["error"] == "boom"
    assert payload["status"] == "failed"
```

Check the real enum member names in `argus/workflows/models.py` (`WorkflowKind`, `WorkflowStatus`) and adjust `BUILD_RESEARCH_PACK` / `COMPLETED` / `FAILED` to the actual members before running.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_pack_tools.py -v`
Expected: FAIL with `ImportError: cannot import name '_serialize_workflow_json'`

- [ ] **Step 3: Implement**

In `argus/mcp/tools.py`, next to `_serialize_workflow` (~line 359), add:

```python
def _serialize_workflow_json(result) -> str:
    """Machine-readable workflow result: run metadata plus a file manifest.

    Designed so an MCP agent can enumerate pack files and fetch each one via
    read_pack_file, then forward them (e.g. to Maya POST /ingest/file)
    without shelling out.
    """
    import os

    files = []
    seen: set[str] = set()
    candidate_paths = [a.path for a in result.artifacts] + [
        d.artifact_path for d in result.documents
    ]
    for path in candidate_paths:
        if not path or path in seen:
            continue
        seen.add(path)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        files.append({"path": path, "bytes": size})

    payload = {
        "run_id": result.run_id,
        "kind": result.kind.value,
        "status": result.status.value,
        "target": result.target,
        "error": result.error,
        "report_path": result.report_path,
        "manifest_path": result.manifest_path,
        "snapshot_dir": result.snapshot_dir,
        "pack_dir": result.metadata.get("current_dir"),
        "files": files,
    }
    return json.dumps(payload, indent=2)
```

(`json` is already imported in this module; if not, add `import json` at the top.)

Change `build_research_pack` in `argus/mcp/tools.py` (~line 439) to accept and honor a format switch:

```python
async def build_research_pack(
    broker: SearchBroker,
    topic: str,
    official_url: Optional[str] = None,
    max_research_pages: int = 40,
    response_format: str = "markdown",
    ctx: Any = None,
) -> str:
    """Build a combined official-docs and external-research pack."""
    result = await WorkflowService(broker, progress_callback=_make_progress_callback(ctx)).build_research_pack(
        topic=topic,
        official_url=official_url,
        max_research_pages=max_research_pages,
    )
    if response_format == "json":
        return _serialize_workflow_json(result)
    return _serialize_workflow(result)
```

In `argus/mcp/server.py` (~line 145), update the registration to pass it through and document it:

```python
    @mcp.tool()
    async def build_research_pack(
        topic: str,
        official_url: str = None,
        max_research_pages: int = 40,
        response_format: str = "markdown",
        ctx: McpContext = None,
    ) -> str:
        """Build a local pack with official docs plus external research.

        Set response_format="json" for a machine-readable manifest
        (run_id, status, file list with paths/sizes) — use with
        read_pack_file to pipe pack contents to another service
        (e.g. Maya POST /ingest/file) without shell access.
        """
        return await mcp_tools.build_research_pack(
            broker,
            topic,
            official_url=official_url,
            max_research_pages=max_research_pages,
            response_format=response_format,
            ctx=ctx,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_pack_tools.py tests/test_workflows.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add argus/mcp/tools.py argus/mcp/server.py tests/test_mcp_pack_tools.py
git commit -m "feat(mcp): add json response_format with file manifest to build_research_pack (#19)"
```

### Task 4: `read_pack_file` MCP tool (issue #19, part 2)

**Files:**
- Modify: `argus/mcp/tools.py` (append new function)
- Modify: `argus/mcp/server.py` (register tool, non-admin section — after `build_research_pack`)
- Test: `tests/test_mcp_pack_tools.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mcp_pack_tools.py`:

```python
import base64

from argus.mcp.tools import read_pack_file


def test_read_pack_file_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "argus.corpus.paths.resolve_data_root", lambda: tmp_path
    )
    f = tmp_path / "SUMMARY.md"
    f.write_text("hello pack", encoding="utf-8")
    payload = json.loads(read_pack_file(str(f)))
    assert payload["encoding"] == "utf-8"
    assert payload["content"] == "hello pack"
    assert payload["truncated"] is False


def test_read_pack_file_binary_falls_back_to_base64(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path)
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x00\x01")
    payload = json.loads(read_pack_file(str(f)))
    assert payload["encoding"] == "base64"
    assert base64.b64decode(payload["content"]) == b"\xff\xfe\x00\x01"


def test_read_pack_file_rejects_path_outside_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path / "root")
    (tmp_path / "root").mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    payload = json.loads(read_pack_file(str(outside)))
    assert "error" in payload and "content" not in payload


def test_read_pack_file_rejects_traversal(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: root)
    (tmp_path / "escape.txt").write_text("nope", encoding="utf-8")
    payload = json.loads(read_pack_file(str(root / ".." / "escape.txt")))
    assert "error" in payload


def test_read_pack_file_truncation_and_offset(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path)
    f = tmp_path / "big.md"
    f.write_text("abcdefghij", encoding="utf-8")
    first = json.loads(read_pack_file(str(f), max_bytes=4))
    assert first["content"] == "abcd"
    assert first["truncated"] is True
    rest = json.loads(read_pack_file(str(f), max_bytes=100, offset=4))
    assert rest["content"] == "efghij"
    assert rest["truncated"] is False


def test_read_pack_file_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path)
    payload = json.loads(read_pack_file(str(tmp_path / "nope.md")))
    assert "error" in payload
```

Note on the monkeypatch target: `read_pack_file` must import `resolve_data_root` lazily inside the function body (`from argus.corpus.paths import resolve_data_root`) so patching `argus.corpus.paths.resolve_data_root` takes effect.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_mcp_pack_tools.py -v`
Expected: FAIL with `ImportError: cannot import name 'read_pack_file'`

- [ ] **Step 3: Implement**

Append to `argus/mcp/tools.py`:

```python
def read_pack_file(path: str, max_bytes: int = 262144, offset: int = 0) -> str:
    """Read a workflow artifact file from inside the Argus data root.

    Returns JSON: {path, size, offset, bytes_returned, truncated,
    encoding: "utf-8"|"base64", content}. Rejects paths outside the
    Argus data root (research packs, docs cache, snapshots all live there).
    """
    import base64
    from pathlib import Path

    from argus.corpus.paths import resolve_data_root

    root = Path(resolve_data_root()).resolve()
    target = Path(path).resolve()
    if not (target == root or target.is_relative_to(root)):
        return json.dumps(
            {"error": "path is outside the Argus data root", "data_root": str(root)}
        )
    if not target.is_file():
        return json.dumps({"error": "file not found", "path": str(target)})

    size = target.stat().st_size
    max_bytes = max(1, min(max_bytes, 1_048_576))
    offset = max(0, offset)
    with open(target, "rb") as handle:
        handle.seek(offset)
        chunk = handle.read(max_bytes)

    try:
        content = chunk.decode("utf-8")
        encoding = "utf-8"
    except UnicodeDecodeError:
        content = base64.b64encode(chunk).decode("ascii")
        encoding = "base64"

    return json.dumps(
        {
            "path": str(target),
            "size": size,
            "offset": offset,
            "bytes_returned": len(chunk),
            "truncated": offset + len(chunk) < size,
            "encoding": encoding,
            "content": content,
        }
    )
```

Register in `argus/mcp/server.py` directly after the `build_research_pack` registration (NOT inside the `expose_admin_tools` block — Hermes connects via remote auth, but this tool should be available in every mode the pack builder is):

```python
    @mcp.tool()
    def read_pack_file(path: str, max_bytes: int = 262144, offset: int = 0) -> str:
        """Read a file produced by an Argus workflow (research pack, report, capture).

        Returns JSON with utf-8 or base64 content plus truncation info.
        Use after build_research_pack(response_format="json") to fetch each
        manifest file, e.g. to forward into Maya POST /ingest/file.
        Paths must be inside the Argus data root.
        """
        return mcp_tools.read_pack_file(path, max_bytes=max_bytes, offset=offset)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_mcp_pack_tools.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add argus/mcp/tools.py argus/mcp/server.py tests/test_mcp_pack_tools.py
git commit -m "feat(mcp): add read_pack_file tool for piping workflow artifacts to other services (#19)"
```

### Task 5: Caller attribution for extract + workflow HTTP endpoints

Today only `POST /api/search` accepts `caller`. Clio also calls `/api/extract` and `/api/workflows/search-and-summarize`; workflow-driven broker searches are attributed as `unknown`.

**Files:**
- Modify: `argus/api/schemas.py` (`ExtractRequest` ~line 102, `SearchAndSummarizeWorkflowRequest` ~line 270, `BuildResearchPackWorkflowRequest` ~line 256)
- Modify: `argus/api/routes_extract.py`
- Modify: `argus/api/routes_workflows.py`
- Modify: `argus/workflows/service.py` (`__init__` ~line 145; `SearchQuery(` constructions at ~lines 282, 414, 608, 618)
- Modify: `argus/mcp/tools.py` (WorkflowService constructions — pass `caller="mcp"`)
- Test: `tests/test_attribution.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_attribution.py` (mirror the existing test style in that file for client setup — it already tests caller attribution for `/api/search`):

```python
def test_extract_request_accepts_caller():
    from argus.api.schemas import ExtractRequest

    req = ExtractRequest(url="https://example.com/a", caller="clio-intake-extract")
    assert req.caller == "clio-intake-extract"


def test_workflow_requests_accept_caller():
    from argus.api.schemas import (
        BuildResearchPackWorkflowRequest,
        SearchAndSummarizeWorkflowRequest,
    )

    a = SearchAndSummarizeWorkflowRequest(query="q", caller="clio-workflows")
    b = BuildResearchPackWorkflowRequest(topic="t", caller="hermes")
    assert a.caller == "clio-workflows"
    assert b.caller == "hermes"


def test_workflow_service_tags_internal_searches_with_caller():
    import inspect

    from argus.workflows.service import WorkflowService

    sig = inspect.signature(WorkflowService.__init__)
    assert "caller" in sig.parameters
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_attribution.py -v`
Expected: new tests FAIL (`ValidationError`/`TypeError`), existing ones PASS

- [ ] **Step 3: Implement**

`argus/api/schemas.py` — add to `ExtractRequest`, `SearchAndSummarizeWorkflowRequest`, and `BuildResearchPackWorkflowRequest`:

```python
    caller: str = Field("", description="Caller identifier for attribution (e.g. 'clio-intake-extract')")
```

`argus/api/routes_extract.py` — log the caller. Add imports and a logger at the top:

```python
from argus.logging import get_logger

logger = get_logger("api.extract")
```

and at the start of the `extract` handler body:

```python
    if req.caller:
        logger.info("extract caller=%s url=%s", req.caller, req.url)
```

`argus/workflows/service.py` — `__init__` gains a caller (find the signature at ~line 145):

```python
    def __init__(
        self,
        broker: SearchBroker,
        progress_callback=None,
        corpus_paths: CorpusPaths | None = None,
        caller: str = "workflows",
    ):
```

store it (`self._caller = caller or "workflows"`), then add `caller=self._caller,` to every `SearchQuery(` construction in this file. Find them all:

Run: `grep -n "SearchQuery(" argus/workflows/service.py`
Expected sites (verify — the file may have drifted): lines ~282, ~414, ~608, ~618. Edit each, e.g.:

```python
        resp = await self._broker.search(
            SearchQuery(
                query=f"{topic} official docs",
                mode=SearchMode.DISCOVERY,
                max_results=8,
                caller=self._caller,
            )
        )
```

`argus/api/routes_workflows.py` — thread the request caller into the run's metadata so it shows in manifests. In the `search-and-summarize` and `build-research-pack` handlers, after `run = await workflows.start_...(...)`:

```python
    if req.caller:
        run.metadata["caller"] = req.caller
```

(The shared `WorkflowService` instance keeps its static `"workflows"` broker tag; per-request granularity lives in run metadata. Do not mutate the shared service's `_caller` per request — it would race.)

`argus/mcp/tools.py` — the MCP layer constructs a fresh `WorkflowService` per call, so tag those as MCP traffic. In `build_research_pack`, `capture_site`, and `recover_dead_article`, change the construction to:

```python
    WorkflowService(broker, progress_callback=_make_progress_callback(ctx), caller="mcp")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_attribution.py tests/test_workflows.py tests/test_api.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add argus/api/schemas.py argus/api/routes_extract.py argus/api/routes_workflows.py argus/workflows/service.py argus/mcp/tools.py tests/test_attribution.py
git commit -m "feat(api): accept caller attribution on extract and workflow endpoints; tag workflow searches"
```

### Task 6: Docs — README MCP-vs-HTTP section (issue #20), env example, changelog, glossary

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Modify: `CHANGELOG.md`
- Modify: `CONTEXT.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: README section (closes #20)**

In `README.md`, find the Integration/MCP area (search for `mcp-name:` or an existing "Integration" heading) and add this section adjacent to it, preserving the `<!-- mcp-name: io.github.Khamel83/argus -->` comment wherever it currently is:

```markdown
## Using Argus from MCP vs HTTP

Two transports, one rule: **agents use MCP, everything else uses HTTP.**

- **MCP** (`argus mcp serve`, or remote streamable-http on the canonical
  deployment) — for AI harnesses that speak MCP natively: Claude Code,
  Cursor, Codex, Hermes. Core tools: `search_web`, `extract_content`,
  `recover_url`, `expand_links`, `build_research_pack` (+
  `read_pack_file` for piping pack artifacts onward).
- **HTTP** (`POST /api/search`, `POST /api/extract`,
  `POST /api/workflows/...`) — for scripts, cron jobs, and service
  integrations (e.g. Clio). Send `Authorization: Bearer $ARGUS_API_KEY`
  and always set `"caller"` in the request body for attribution.

The cross-service transport and role contract for the wider fleet
(Clio / Maya / Hermes / Argus) is canonical in
[khamel83/maya docs/CONTEXT-CONTRACT.md](https://github.com/Khamel83/maya/blob/main/docs/CONTEXT-CONTRACT.md).
```

- [ ] **Step 2: .env.example**

Append (near the existing node/egress settings block — find `ARGUS_NODE_ROLE` or add at the end of the routing section):

```bash
# Per-caller provider tier caps (fnmatch pattern:max_tier, comma-separated).
# Callers matching a pattern never route to providers above that tier —
# e.g. keep automated fleet callers off tier-3 one-time-credit providers.
# ARGUS_CALLER_TIER_CAPS=clio*:1,hermes*:1
```

- [ ] **Step 3: CHANGELOG under `[Unreleased]`**

Add to the existing `### Added` list:

```markdown
- **Per-caller provider tier caps** — `ARGUS_CALLER_TIER_CAPS=clio*:1,hermes*:1` enforces, server-side, that matching callers never route to providers above the given tier (e.g. tier-3 one-time-credit providers). Enforced in the broker regardless of client flags.
- **Machine-readable research packs (MCP)** — `build_research_pack` accepts `response_format="json"` returning a run manifest (files, sizes, paths); new `read_pack_file` MCP tool returns utf-8/base64 file content so agents can pipe pack artifacts to other services (e.g. Maya `POST /ingest/file`) without shell access. Closes #19.
- **Caller attribution on extract + workflows** — `POST /api/extract` and workflow endpoints accept `caller`; workflow-driven broker searches are tagged (`workflows` via HTTP service, `mcp` via MCP tools).
```

Add to `### Fixed`:

```markdown
- **`free_only` now applies to remote-egress searches** — the tier check previously ran after the remote-worker branch, so `free_only` (and now caller caps) never filtered searches delegated to egress workers.
```

- [ ] **Step 4: CONTEXT.md glossary + AGENTS.md config row**

Append to `CONTEXT.md` Glossary:

```markdown
### Caller attribution

Every HTTP/MCP/CLI entry point accepts a `caller` string (e.g. `clio-lane-b`,
`hermes`, `mcp`) persisted with each search for the per-caller dashboard.
Fleet callers must always set it; unattributed traffic shows as `unknown`.

### Caller tier caps

Server-side spending guardrail: `ARGUS_CALLER_TIER_CAPS` maps fnmatch
caller patterns to a maximum provider tier. Motivated by the 2026-05
unexplained Valyu credit burn (see hermes `docs/ARGUS-VALVU-AUDIT.md`):
automated callers (Clio jobs, Hermes) are capped at tier 1 so one-time
credits (tier 3) can only be spent by interactive/uncapped callers.

### Canonical deployment

One Argus for the fleet: the mac mini (`omars-mac-mini` on Tailscale,
residential egress), run by launchd from `/Users/macmini/github/argus` —
HTTP API on `:8300`, streamable-http MCP on `:8301`. Chosen 2026-07-05
over the drifted alternatives (Clio pointing at `:8005`, a Docker argus
on homelab). See `docs/adr/0001-canonical-deployment.md`.
```

In `AGENTS.md`, add to the Configuration table:

```markdown
| `ARGUS_CALLER_TIER_CAPS` | (empty) | Per-caller max provider tier, fnmatch patterns (e.g. `clio*:1,hermes*:1`) |
```

- [ ] **Step 5: Commit**

```bash
git add README.md .env.example CHANGELOG.md CONTEXT.md AGENTS.md
git commit -m "docs: MCP vs HTTP section, caller tier caps, canonical deployment glossary (#20)"
```

### Task 7: ADR — canonical deployment

**Files:**
- Create: `docs/adr/0001-canonical-deployment.md`

- [ ] **Step 1: Write the ADR**

```markdown
# 0001 — One canonical Argus on the mac mini (launchd, :8300/:8301)

Date: 2026-07-05
Status: accepted

## Context

Argus had drifted into inconsistent deployments: Clio's config defaulted
to `http://localhost:8005`, the repo documented `argus serve` on `:8000`,
and a Docker argus (+ argus-mcp) existed on the homelab host. Nothing was
actually listening on the mac mini. Meanwhile the fleet convention put
Clio on `:8100` and Maya on `:8200`, both launchd services on the mini,
Tailscale-only.

## Decision

Run exactly one canonical Argus, on the mac mini:

- HTTP API `:8300`, streamable-http MCP `:8301`, bound `0.0.0.0`
  (network exposure is Tailscale-only; hostname `omars-mac-mini`).
- launchd services `com.argus.server` and `com.argus.mcp`, running from
  the service checkout `/Users/macmini/github/argus` (same pattern as
  `com.maya.server`).
- `ARGUS_EGRESS_TYPE=residential`, `ARGUS_NODE_ROLE=primary` — the mini
  is a residential egress, which is the whole point of topology-aware
  routing; a datacenter-ish homelab default fights it.
- All fleet callers converge on these endpoints; the homelab Docker
  argus is decommissioned (or later, explicitly re-introduced as an
  egress *worker*, not a second primary).

## Consequences

- Clio's `argus_base_url` default changes `:8005` → `:8300`.
- Hermes registers `http://omars-mac-mini:8301/mcp` as an MCP toolset.
- Remote MCP requires `ARGUS_API_KEY`; HTTP callers send it as a bearer
  token (rate-limit exemption + attribution hygiene).
- Port `8300` joins the fleet convention: 8100 Clio, 8200 Maya, 8300 Argus.
```

- [ ] **Step 2: Commit**

```bash
git add docs/adr/0001-canonical-deployment.md
git commit -m "docs(adr): record canonical mac-mini deployment decision"
```

### Task 8: Deploy templates + full test pass + push

**Files:**
- Create: `deploy/com.argus.server.plist`
- Create: `deploy/com.argus.mcp.plist`
- Create: `deploy/start-argus.sh`
- Create: `deploy/start-argus-mcp.sh`
- Create: `deploy/README.md`

- [ ] **Step 1: Create the start scripts** (modeled on maya's `/Users/macmini/Library/Scripts/start-maya.sh`)

`deploy/start-argus.sh`:

```bash
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
```

`deploy/start-argus-mcp.sh`:

```bash
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
```

- [ ] **Step 2: Create the plists**

`deploy/com.argus.server.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.argus.server</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/macmini/Library/Scripts/start-argus.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/macmini/github/argus</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/macmini</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>/Users/macmini/Library/Logs/argus.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/macmini/Library/Logs/argus.err.log</string>
</dict>
</plist>
```

`deploy/com.argus.mcp.plist` — identical except: `Label` = `com.argus.mcp`, script path = `/Users/macmini/Library/Scripts/start-argus-mcp.sh`, log paths = `argus-mcp.log` / `argus-mcp.err.log`. Write it out fully:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.argus.mcp</string>

    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>/Users/macmini/Library/Scripts/start-argus-mcp.sh</string>
    </array>

    <key>WorkingDirectory</key>
    <string>/Users/macmini/github/argus</string>

    <key>EnvironmentVariables</key>
    <dict>
        <key>HOME</key>
        <string>/Users/macmini</string>
    </dict>

    <key>RunAtLoad</key>
    <true/>

    <key>KeepAlive</key>
    <true/>

    <key>ThrottleInterval</key>
    <integer>10</integer>

    <key>StandardOutPath</key>
    <string>/Users/macmini/Library/Logs/argus-mcp.log</string>

    <key>StandardErrorPath</key>
    <string>/Users/macmini/Library/Logs/argus-mcp.err.log</string>
</dict>
</plist>
```

`deploy/README.md`:

```markdown
# Argus canonical deployment (mac mini)

See `docs/adr/0001-canonical-deployment.md` for why.

```bash
# one-time setup, from the service checkout
cd /Users/macmini/github/argus
git pull
uv sync --extra mcp
cp deploy/start-argus.sh deploy/start-argus-mcp.sh /Users/macmini/Library/Scripts/
chmod +x /Users/macmini/Library/Scripts/start-argus*.sh
cp deploy/com.argus.server.plist deploy/com.argus.mcp.plist /Users/macmini/Library/LaunchAgents/
launchctl load /Users/macmini/Library/LaunchAgents/com.argus.server.plist
launchctl load /Users/macmini/Library/LaunchAgents/com.argus.mcp.plist
```

Required `.env` in the service checkout (never committed):
`ARGUS_ENV=production`, `ARGUS_NODE_ROLE=primary`,
`ARGUS_EGRESS_TYPE=residential`, `ARGUS_MACHINE_NAME=omars-mac-mini`,
`ARGUS_PORT=8300`, `ARGUS_API_KEY=<generated>`,
`ARGUS_CALLER_TIER_CAPS=clio*:1,hermes*:1`, plus provider keys.

Redeploy after a merge: `git pull && uv sync --extra mcp && launchctl kickstart -k gui/$(id -u)/com.argus.server && launchctl kickstart -k gui/$(id -u)/com.argus.mcp`
```

- [ ] **Step 3: Full test suite, then push**

Run: `uv run pytest`
Expected: PASS (no regressions anywhere)

```bash
git add deploy/
git commit -m "feat(deploy): launchd templates for canonical mac-mini deployment (:8300 http, :8301 mcp)"
git push origin main
```

(If the repo requires PRs on main, create a branch `feat/fleet-integration` at Phase 1 start instead, and open a PR here with `gh pr create`.)

### Task 9: Deploy to the service checkout and verify live

This task changes machine state — follow it exactly and stop if a verification fails.

- [ ] **Step 1: Generate the API key and write the production .env**

```bash
ARGUS_KEY=$(openssl rand -hex 32)
echo "Generated ARGUS_API_KEY: $ARGUS_KEY"   # will be needed for clio .env and the secrets vault
```

Update `/Users/macmini/github/argus/.env` — preserve existing provider keys, and set/replace these lines:

```bash
ARGUS_ENV=production
ARGUS_NODE_ROLE=primary
ARGUS_EGRESS_TYPE=residential
ARGUS_MACHINE_NAME=omars-mac-mini
ARGUS_PORT=8300
ARGUS_API_KEY=<the generated value>
ARGUS_CALLER_TIER_CAPS=clio*:1,hermes*:1
```

If `/Users/macmini/github/argus/.env` does not exist, create it from `.env.example` plus the lines above. Do NOT overwrite existing provider API keys — read the file first.

Also store the key in the secrets vault for Hermes (namespace conventions live in `~/github/secrets-vault`; check `secrets --help` for the exact set syntax):

```bash
secrets set ARGUS_API_KEY "$ARGUS_KEY" 2>/dev/null || echo "OPERATOR: store ARGUS_API_KEY in the secrets vault manually (hermes bootstrap reads it via 'secrets get')"
```

- [ ] **Step 2: Sync and install**

```bash
cd /Users/macmini/github/argus
git pull origin main
uv sync --extra mcp
cp deploy/start-argus.sh deploy/start-argus-mcp.sh /Users/macmini/Library/Scripts/
chmod +x /Users/macmini/Library/Scripts/start-argus.sh /Users/macmini/Library/Scripts/start-argus-mcp.sh
cp deploy/com.argus.server.plist deploy/com.argus.mcp.plist /Users/macmini/Library/LaunchAgents/
launchctl load /Users/macmini/Library/LaunchAgents/com.argus.server.plist
launchctl load /Users/macmini/Library/LaunchAgents/com.argus.mcp.plist
```

- [ ] **Step 3: Verify**

```bash
sleep 5
curl -sf http://localhost:8300/api/health | head -c 400; echo
curl -sf -X POST http://localhost:8300/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "argus deployment smoke test", "mode": "discovery", "max_results": 3, "free_only": true, "caller": "deploy-smoke-test"}' | head -c 400; echo
# MCP endpoint answers (401/406 without a proper MCP handshake is fine; connection refused is not):
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8301/mcp
# Tier cap enforcement: a clio-attributed search must show tier-3 providers skipped with "caller tier cap"
curl -sf -X POST http://localhost:8300/api/search \
  -H "Content-Type: application/json" \
  -d '{"query": "tier cap smoke", "max_results": 3, "caller": "clio-smoke"}' | python3 -c "import json,sys; d=json.load(sys.stdin); print([t for t in d['traces'] if 'tier cap' in (t.get('error') or '')])"
```

Expected: health JSON; search JSON with results; an HTTP status (not connection-refused) from :8301; at least one `caller tier cap` skip trace in the last command **if any tier-3 provider is enabled in the .env** (if none are enabled, note that and move on).

Also run the built-in diagnostic: `cd /Users/macmini/github/argus && uv run argus doctor` — investigate anything red.

- [ ] **Step 4: Close the GitHub issues**

```bash
gh issue close 19 --repo Khamel83/argus --comment "Done. The MCP tool existed since 37819fc; what was missing was pipeable output. build_research_pack now takes response_format=\"json\" (run manifest with file paths/sizes) and the new read_pack_file MCP tool returns utf-8/base64 file content, so a harness (e.g. Hermes) can loop the manifest and POST each file to Maya /ingest/file with no shell escape. Canonical MCP endpoint: http://omars-mac-mini:8301/mcp."
gh issue close 20 --repo Khamel83/argus --comment "Added 'Using Argus from MCP vs HTTP' to README, linking khamel83/maya docs/CONTEXT-CONTRACT.md as the canonical transport policy."
```

---

# Phase 2 — Clio (repo: `/Volumes/2TB_SSD/GitHub/clio`)

Clio calls Argus from five places, all with no attribution, no auth header, and a wrong default port. Fix with one shared helper. Note: Argus (Pydantic) ignores unknown JSON fields by default, so sending `caller` is safe even if an old Argus is running — but Phase 1 must be deployed for attribution to actually record.

Read `/Volumes/2TB_SSD/GitHub/clio/CLAUDE.md` and `AGENTS.md` before starting — follow that repo's own conventions for tests and commits.

### Task 10: Shared Argus HTTP helper + config

**Files:**
- Create: `app/clio/argus_http.py`
- Modify: `app/config.py` (line ~99: `argus_base_url`)
- Test: `tests/test_argus_http.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_argus_http.py` (clio's pytest runs with `asyncio_mode=auto`):

```python
"""Tests for the shared Argus HTTP helper."""

from unittest.mock import patch

from app.clio import argus_http


def test_argus_url_joins_cleanly():
    with patch.object(argus_http.settings, "argus_base_url", "http://localhost:8300/"):
        assert argus_http.argus_url("/api/search") == "http://localhost:8300/api/search"


def test_headers_include_bearer_when_key_set():
    with patch.object(argus_http.settings, "argus_api_key", "sekrit"):
        assert argus_http.argus_headers() == {"Authorization": "Bearer sekrit"}


def test_headers_empty_without_key():
    with patch.object(argus_http.settings, "argus_api_key", ""):
        assert argus_http.argus_headers() == {}


def test_payload_gains_caller():
    body = argus_http.attributed_payload({"query": "x"}, caller="clio-lane-b")
    assert body == {"query": "x", "caller": "clio-lane-b"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Volumes/2TB_SSD/GitHub/clio && uv run pytest tests/test_argus_http.py -v`
Expected: FAIL with `ModuleNotFoundError: app.clio.argus_http`

- [ ] **Step 3: Implement**

`app/config.py` — change line 99 and add a key setting right after it:

```python
    argus_base_url: str = "http://localhost:8300"
    argus_api_key: str = ""
```

Create `app/clio/argus_http.py`:

```python
"""Shared helper for calling the Argus HTTP API.

Every Clio -> Argus call must go through this module so that caller
attribution and bearer auth are never forgotten (see the 2026-05 Valyu
credit-burn incident). Caller names use the convention "clio-<lane/job>".
"""

import httpx

from app.config import settings


def argus_url(path: str) -> str:
    return f"{settings.argus_base_url.rstrip('/')}{path}"


def argus_headers() -> dict[str, str]:
    if settings.argus_api_key:
        return {"Authorization": f"Bearer {settings.argus_api_key}"}
    return {}


def attributed_payload(payload: dict, *, caller: str) -> dict:
    return {**payload, "caller": caller}


async def argus_post(
    path: str, payload: dict, *, caller: str, timeout: float = 30.0
) -> httpx.Response:
    """POST to Argus with attribution and auth. Raises nothing extra; callers
    keep their existing status-code / exception handling."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(
            argus_url(path),
            json=attributed_payload(payload, caller=caller),
            headers=argus_headers(),
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_argus_http.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/clio/argus_http.py app/config.py tests/test_argus_http.py
git commit -m "feat: shared Argus HTTP helper with caller attribution and auth; canonical :8300 default"
```

### Task 11: Migrate the five Argus call sites

**Files:**
- Modify: `app/clio/dispatch/non_code_runner.py` (~line 27, `_argus_search`)
- Modify: `app/clio/chronos/jobs/idea_research.py` (~line 25)
- Modify: `app/clio/chronos/scheduler.py` (~line 81, the `/api/extract` URL builder and its POST site)
- Modify: `app/clio/dispatch/runner.py` (~line 2123, search-and-summarize)
- Modify: `app/adapters/base.py` (~line 57, adapter search)

Caller names (fixed, use exactly these): `clio-lane-b`, `clio-idea-research`, `clio-intake-extract`, `clio-workflows`, `clio-adapter`.

- [ ] **Step 1: `non_code_runner.py`** — replace the body of `_argus_search`:

```python
async def _argus_search(query: str, max_results: int = 7) -> list[dict]:
    """Run Argus web search. Returns list of {url, title, snippet} dicts."""
    from app.clio.argus_http import argus_post

    try:
        r = await argus_post(
            "/api/search",
            {"query": query, "mode": "discovery", "max_results": max_results},
            caller="clio-lane-b",
        )
        if r.status_code != 200:
            return []
        return r.json().get("results", [])
    except Exception as exc:
        logger.warning("non_code_runner: argus_search failed: %s", exc)
        return []
```

- [ ] **Step 2: `idea_research.py`** — replace the body of its `_argus_search` (~line 19; it is near-identical to non_code_runner's):

```python
async def _argus_search(query: str, max_results: int = 7) -> list[dict]:
    """Run Argus web search. Returns list of {url, title, snippet} dicts."""
    from app.clio.argus_http import argus_post

    try:
        r = await argus_post(
            "/api/search",
            {"query": query, "mode": "discovery", "max_results": max_results},
            caller="clio-idea-research",
        )
        if r.status_code != 200:
            return []
        return r.json().get("results", [])
    except Exception as exc:
        logger.warning("argus_search failed: %s", exc)
        return []
```

- [ ] **Step 3: `scheduler.py`** — the extract POST lives in an inner `fetch_url` coroutine (~line 324) that deliberately shares one `httpx.AsyncClient` under a semaphore. Keep that structure; add attribution to the payload and auth to the post:

```python
        from app.clio.argus_http import argus_headers

        async def fetch_url(client: httpx.AsyncClient, item: IntakeItem):
            async with semaphore:
                try:
                    payload = {"url": item.url, "caller": "clio-intake-extract"}
                    domain_hint = _domain_hint_for_url(item.url)
                    if domain_hint:
                        payload["domain"] = domain_hint
                    resp = await client.post(
                        _argus_extract_url(),
                        json=payload,
                        headers=argus_headers(),
                    )
                    return item, resp, None
                except Exception as exc:
                    return item, None, exc
```

(Put the import at the top of the file with the other `app.` imports, not inside the function.)

- [ ] **Step 4: `runner.py` ~line 2123** — the search-and-summarize trigger becomes:

```python
        from app.clio.argus_http import argus_headers, argus_post, argus_url

        resp = await argus_post(
            "/api/workflows/search-and-summarize",
            {"query": title, "max_search_results": 5},
            caller="clio-workflows",
            timeout=45.0,
        )
```

and the status polling a few lines below keeps using a plain client but gains the auth header and helper URL:

```python
            status_url = argus_url(f"/api/workflows/{run_id}")
            ...
                status_resp = await client.get(status_url, headers=argus_headers())
```

(The existing `async with httpx.AsyncClient(timeout=45) as client:` block structure changes: `argus_post` manages its own client for the trigger; keep the existing client only for the polling loop. Preserve the existing error handling and `run_data = resp.json()` lines, renaming `resp` references consistently.)

- [ ] **Step 5: `app/adapters/base.py` ~line 57** — inside `search`, replace the client/post pair with:

```python
                from app.clio.argus_http import argus_post

                r = await argus_post(
                    "/api/search",
                    {"query": query, "mode": "discovery", "max_results": opts.get("max_results", 10)},
                    caller="clio-adapter",
                )
```

keeping the surrounding try/except and result-mapping untouched.

- [ ] **Step 6: Run clio's test suite**

Run: `uv run pytest`
Expected: PASS. If existing tests mock `httpx.AsyncClient.post` for these call sites, update the mocks to target `app.clio.argus_http.argus_post` instead.

- [ ] **Step 7: Commit**

```bash
git add app/clio/dispatch/non_code_runner.py app/clio/chronos/jobs/idea_research.py app/clio/chronos/scheduler.py app/clio/dispatch/runner.py app/adapters/base.py
git commit -m "feat: attribute and authenticate all Argus calls via shared helper"
git push origin main
```

(If clio's conventions require a PR instead of pushing main, open one with `gh pr create` and note it for the operator.)

### Task 12: Deploy Clio's config change

- [ ] **Step 1: Find the service checkout and runtime**

```bash
launchctl list | grep -i clio
grep -rl "clio" /Users/macmini/Library/LaunchAgents/ 2>/dev/null
```

Read the matching plist's `WorkingDirectory` — that's the service checkout. If nothing matches, check `docker ps | grep clio` and `/Users/macmini/github/clio`.

- [ ] **Step 2: Update its `.env`** — in the service checkout's `.env`, set (respecting however clio names env vars — check `app/config.py` settings class for the env prefix; pydantic-settings default maps `argus_base_url` to `ARGUS_BASE_URL`):

```bash
ARGUS_BASE_URL=http://localhost:8300
ARGUS_API_KEY=<the key generated in Task 9>
```

- [ ] **Step 3: Pull + restart + verify**

```bash
cd <service checkout> && git pull origin main
launchctl kickstart -k gui/$(id -u)/<clio launchd label>   # or the equivalent restart for its runtime
sleep 5
curl -sf http://localhost:8100/api/status | head -c 300; echo
```

Then verify attribution end-to-end: trigger any Clio search path (or wait for a scheduled job) and check Argus records it. Caller activity lives in the `provider_usage` table of the main Argus DB and renders on `GET /api/dashboard`:

```bash
curl -s http://localhost:8300/api/dashboard | grep -o 'clio-[a-z-]*' | sort -u
# or directly:
DB="$(cd /Users/macmini/github/argus && uv run python -c 'from argus.corpus.paths import resolve_data_root; print(resolve_data_root())')/argus.db"
sqlite3 "$DB" "SELECT COALESCE(NULLIF(caller,''),'unknown'), COUNT(*) FROM provider_usage GROUP BY 1 ORDER BY 2 DESC LIMIT 10;"
```

Expected: rows with `clio-*` callers appearing after Clio activity.

---

# Phase 3 — Hermes (repo: `/Volumes/2TB_SSD/GitHub/hermes`)

Read `/Volumes/2TB_SSD/GitHub/hermes/CONTEXT.md` first — the Template/Deployed-config/Bootstrap/SOUL.md vocabulary below comes from there. Key trap: the Bootstrap **clobbers** the deployed config from the Template on every deploy, so the Template must carry everything (that's what caused the 2026-07-03 capability regression).

### Task 13: Register Argus MCP in the Template (decision gate)

**Files:**
- Modify: `config/config.yaml.template`
- Modify: `scripts/hermes-bootstrap.sh`

- [ ] **Step 1: Discover the runtime's MCP registration schema.** The MCP toolset syntax for Hermes's runtime is NOT documented in the hermes or openclaw repos. Determine it from the deployed container before editing anything:

```bash
ssh homelab "docker exec hermes cat /opt/data/config.yaml | grep -n -i -B3 -A10 mcp" 2>/dev/null
ssh homelab "docker exec hermes <runtime-binary> --help 2>&1 | grep -i mcp" 2>/dev/null
```

Also grep the runtime's docs inside the container or its image if available. You are looking for how to declare an MCP *server/toolset* (the template's existing `toolsets:` list and `delegation.inherit_mcp_toolsets: true` prove the capability exists).

- [ ] **Step 2A (if the schema supports remote MCP servers):** add the Argus registration to `config/config.yaml.template` using the discovered schema. The values to plug in, whatever the exact YAML shape is:

- name/id: `argus`
- type/transport: `streamable-http` (fall back to `sse` only if streamable-http is unsupported)
- url: `http://omars-mac-mini:8301/mcp`
- auth header: `Authorization: Bearer ${ARGUS_API_KEY}` (the template already uses `${PENNY_WEBHOOK_SECRET}`-style interpolation — line ~397)

And ensure the new toolset is enabled (add `argus` to the `toolsets:` list at line ~16 if the schema requires opt-in there).

Then add the secret to `scripts/hermes-bootstrap.sh`, next to the existing `load_secret_env` block (~line 61–74):

```bash
load_secret_env ARGUS_API_KEY
```

- [ ] **Step 2B (fallback — only if the runtime genuinely cannot register remote MCP servers):** skip the template change; instead the SOUL.md section in Task 14 documents the HTTP API precisely (the Executor sandbox can `curl http://omars-mac-mini:8300/api/search` with the bearer token and `"caller": "hermes"`), and file the gap:

```bash
gh issue create --repo Khamel83/hermes --title "Register Argus remote MCP once runtime supports it" \
  --body "Argus exposes streamable-http MCP at http://omars-mac-mini:8301/mcp (bearer ARGUS_API_KEY from the vault). The deployed runtime could not register remote MCP servers as of 2026-07; until it can, SOUL.md documents HTTP usage. Replace the HTTP instructions with a proper MCP toolset when possible. Context: argus docs/adr/0001-canonical-deployment.md."
```

- [ ] **Step 3: Commit**

```bash
git add config/config.yaml.template scripts/hermes-bootstrap.sh
git commit -m "feat: register Argus remote MCP toolset (bearer from vault) in template"
```

(Adjust the message if 2B was taken.)

### Task 14: SOUL.md — when and how Hermes uses Argus

**Files:**
- Modify: `SOUL.md` (repo root — the canonical copy; the deployed one at `/opt/data/SOUL.md` follows via bootstrap)

- [ ] **Step 1: Add the section.** Place it near the existing routing/table material (after the routing table that currently mentions Argus only as a topic keyword, ~line 26–32):

```markdown
## Internet retrieval (Argus)

Argus is the fleet's web-retrieval broker. Use it — not ad-hoc fetching —
whenever a task needs web search, page extraction, dead-URL recovery, or a
research pack. Argus is NOT memory: anything worth keeping goes to Maya.

- Tools (MCP toolset `argus`): `search_web`, `extract_content`,
  `recover_url`, `expand_links`, `build_research_pack`, `read_pack_file`,
  `valyu_answer` (costs real one-time credits — only when explicitly asked).
- Always pass `caller="hermes"` on `search_web`. Argus caps hermes-attributed
  searches at tier 1 (free + monthly providers) server-side; don't try to
  work around it.
- Research pack → Maya: call
  `build_research_pack(topic=..., response_format="json")`, then for each
  entry in `files`, call `read_pack_file(path)` and POST the content to Maya
  `POST /ingest/file` (base64 the content if `encoding` is `utf-8`; pass it
  through if already `base64`), with a sensible channel tag. Never write the
  vault directly; Maya owns it.
- Endpoints (Tailscale-only): MCP `http://omars-mac-mini:8301/mcp`;
  HTTP `http://omars-mac-mini:8300/api/*` with
  `Authorization: Bearer ${ARGUS_API_KEY}` — HTTP is for scripted jobs only;
  from a chat/agent context use the MCP tools.
```

If Task 13 took branch 2B, replace the first two bullets with explicit `curl` templates against `/api/search` and `/api/extract` (JSON bodies including `"caller": "hermes"`, bearer header) and note the MCP migration issue number.

- [ ] **Step 2: Commit + push**

```bash
git add SOUL.md
git commit -m "docs(soul): Argus retrieval — tools, caller attribution, research-pack-to-Maya recipe"
git push origin main
```

- [ ] **Step 3: OPERATOR STEP (cannot be done from this machine unattended):** deploy on homelab:

```bash
ssh homelab "cd /mnt/fast-storage/github/hermes && git pull && bash scripts/hermes-bootstrap.sh"
```

Then verify in Slack: ask Hermes to "search the web for the FastAPI 0.115 changelog" and confirm (a) it uses the argus toolset (or documented HTTP path), and (b) `caller='hermes'` rows appear in Argus's searches table (same sqlite check as Task 12). If the bootstrap fails on the missing vault secret, store it: the key was generated in Task 9.

---

# Phase 4 — Maya contract + cleanup (repo: `/Volumes/2TB_SSD/GitHub/maya`)

### Task 15: Update CONTEXT-CONTRACT.md to match reality

**Files:**
- Modify: `docs/CONTEXT-CONTRACT.md`

- [ ] **Step 1: Edit the Roles table.** Replace the Clio row:

```markdown
| Clio | Executor | Calls Argus over HTTP (URL extraction at intake; Lane B research; workflows) with mandatory caller attribution | Never calls Maya; never uses Argus as memory or agent-context; never handles memory |
```

Replace the Argus row:

```markdown
| Argus | Internet retrieval broker | Called by harnesses (MCP), and by scripts/jobs and Clio (HTTP) | Never stores personal memory; never substitutes for Maya; never calls Maya |
```

- [ ] **Step 2: Edit the Hard Rules list.** Replace `- Clio never uses Argus for retrieval.` with:

```markdown
- Clio may call Argus over HTTP for retrieval work (extraction, Lane B research, workflows) and must send a `caller` on every request; Clio never uses Argus as memory or as a context source for dispatch decisions.
- Hermes uses Argus via MCP (`http://omars-mac-mini:8301/mcp`), passing `caller="hermes"`.
```

(Keep every other rule as is.)

- [ ] **Step 3: Edit the Transport Policy table.** Replace the Argus row with:

```markdown
| Argus | MCP when running inside an AI harness; HTTP otherwise | `ARGUS_API_KEY` bearer for HTTP and remote MCP; `ARGUS_ADMIN_API_KEY` for privileged admin routes | Canonical: mac mini (`omars-mac-mini`), HTTP `:8300`, streamable-http MCP `:8301/mcp`, Tailscale-only. Automated fleet callers are tier-capped server-side (`ARGUS_CALLER_TIER_CAPS`) |
```

- [ ] **Step 4: Sanity-check the Never Rules section** at the bottom — `Never use Argus for memory` and `Never use Clio for retrieval` stay; remove or reword any line that forbids what the Hard Rules now permit (as of the last read there was no separate "Clio never uses Argus" line in Never Rules — verify).

- [ ] **Step 5: Commit + push**

```bash
git add docs/CONTEXT-CONTRACT.md
git commit -m "docs(contract): permit attributed Clio->Argus HTTP and Hermes->Argus MCP; record canonical endpoints"
git push origin main
```

### Task 16: File the homelab decommission issue

- [ ] **Step 1:**

```bash
gh issue create --repo Khamel83/homelab --title "Decommission (or demote to worker) the Docker argus/argus-mcp on homelab" \
  --body "The canonical Argus now runs on the mac mini (omars-mac-mini): HTTP :8300, MCP :8301 — see argus docs/adr/0001-canonical-deployment.md. The homelab Docker argus + argus-mcp (services/argus/docker-compose.yml) should be stopped and removed, or explicitly re-introduced later as an Argus egress *worker* (ARGUS_NODE_ROLE=worker) — not left running as a second unattributed primary. Note: the 2026-05 Valyu credit-burn investigation (hermes docs/ARGUS-VALVU-AUDIT.md) suspected this instance's healthchecks/keepalives."
```

### Task 17: End-to-end verification sweep

- [ ] **Step 1: Argus services healthy**

```bash
launchctl list | grep com.argus
curl -sf http://localhost:8300/api/health >/dev/null && echo "http OK"
curl -s -o /dev/null -w "mcp endpoint: %{http_code}\n" http://localhost:8301/mcp
```

- [ ] **Step 2: Research-pack pipe works end-to-end via MCP** (from the dev checkout; this exercises #19's whole loop locally):

```bash
cd /Volumes/2TB_SSD/GitHub/argus
uv run python - <<'EOF'
import asyncio, json
from argus.broker.router import create_broker
from argus.mcp import tools as mcp_tools

async def main():
    broker = create_broker()
    out = await mcp_tools.build_research_pack(
        broker, "httpx python library", max_research_pages=3, response_format="json"
    )
    manifest = json.loads(out)
    print("status:", manifest["status"], "| files:", len(manifest["files"]))
    first = manifest["files"][0]["path"]
    blob = json.loads(mcp_tools.read_pack_file(first, max_bytes=200))
    print("read_pack_file:", blob["encoding"], blob["bytes_returned"], "bytes")

asyncio.run(main())
EOF
```

Expected: `status: completed`, a non-zero file count, and a successful `read_pack_file` readback.

- [ ] **Step 3: Attribution visible** — after Clio has run any job (or the smoke searches above):

```bash
DB="$(cd /Users/macmini/github/argus && uv run python -c 'from argus.corpus.paths import resolve_data_root; print(resolve_data_root())')/argus.db"
sqlite3 "$DB" "SELECT COALESCE(NULLIF(caller,''),'unknown') c, COUNT(*) FROM provider_usage GROUP BY c ORDER BY 2 DESC LIMIT 10;"
```

Expected: named callers (`deploy-smoke-test`, `clio-*`, later `hermes`) instead of a single `unknown` bucket. The same data renders in the per-caller table on `http://localhost:8300/api/dashboard`.

- [ ] **Step 4: Report.** Summarize for the operator: what shipped in each repo, the two operator steps that remain (hermes bootstrap on homelab; homelab argus decommission issue), and where the ARGUS_API_KEY lives.

---

## Out of scope (deliberately)

- Re-introducing homelab as an Argus egress worker (`ARGUS_EGRESS_NODES`) — future work, tracked by the Task 16 issue.
- Per-request caller threading through a shared `WorkflowService` (would race; run-metadata attribution is enough for now).
- Any Maya code changes — `POST /ingest/file` already accepts what the pack pipe sends.
- MCP client work in Clio (Clio is HTTP-only by contract).
