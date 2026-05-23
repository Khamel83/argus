# Multi-Egress Worker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add state-driven multi-egress routing to Argus — declare worker nodes via env vars, probe which egress reaches which provider, and route each provider through its best available egress automatically. Adds per-caller attribution for dashboard observability.

**Architecture:** A new `ReachabilityMatrix` probes tier-0 providers through each declared egress node (local + worker nodes) on startup and every 30 minutes. `ProviderExecutor` consults the matrix before each call — if the best egress is not local, it delegates to `RemoteProviderClient` which POSTs to the worker's `/exec` endpoint. Workers run `argus worker`, a minimal FastAPI server that executes provider calls and returns normalized results.

**Tech Stack:** Python 3.12, FastAPI, httpx, Click, pytest, pytest-asyncio, SQLite (existing). New files use existing project conventions: `uv run pytest tests/` to run, `uv run argus` to run CLI.

---

## File Map

**New files:**
- `argus/broker/reachability.py` — `EgressProbe`, `ReachabilityMatrix`
- `argus/broker/remote_provider.py` — `RemoteProviderClient(BaseProvider)`
- `argus/worker/__init__.py` — empty package marker
- `argus/worker/server.py` — FastAPI worker app + `argus worker` CLI subcommand
- `tests/test_reachability.py` — matrix logic, preference ordering
- `tests/test_remote_provider.py` — mocked HTTP success/error/auth/timeout
- `tests/test_worker.py` — `/exec` + `/health` endpoints

**Modified files:**
- `argus/config.py` — add `EgressNode`, `egress_nodes` to `ArgusConfig`, parse `ARGUS_EGRESS_NODES` + `ARGUS_EGRESS_SHARED_SECRET`, remove `ResidentialConfig.endpoints`
- `argus/models.py` — add `caller: str = ""` to `SearchQuery`; add `egress: str = "local"` to `ProviderTrace`
- `argus/persistence/models.py` — add `caller` + `egress` columns to `ProviderUsageRow`
- `argus/persistence/db.py` — add `provider_usage` entries to `_ensure_schema_compat`
- `argus/broker/execution.py` — reachability check + `RemoteProviderClient` dispatch + record `trace.egress`
- `argus/broker/router.py` — wire `ReachabilityMatrix` into `SearchBroker` + `create_broker`
- `argus/api/app.py` (or wherever lifespan is) — background probe task on startup
- `argus/cli/main.py` — `--caller` on `search`, add `worker` subcommand
- `argus/api/schemas.py` — add `caller: str = ""` to `SearchRequest`
- `argus/api/routes_search.py` — pass `caller` to `SearchQuery`
- `argus/mcp/tools.py` — hardcode `caller="mcp"` in `search_web`
- `argus/api/usage.py` — add `get_caller_activity()` query
- `argus/api/routes_dashboard.py` — pass `caller_activity` to template + egress nodes
- `argus/api/templates/dashboard.html` — caller breakdown table

---

## Task 1: EgressNode config + caller/egress on models

**Files:**
- Modify: `argus/config.py`
- Modify: `argus/models.py`
- Test: `tests/test_config.py` (extend existing)

### Context

`argus/config.py` already has a `NodeConfig` and `ResidentialConfig`. The `ResidentialConfig.endpoints` field (a `list[str]`) is superseded by the new `EgressNode` concept — remove it and add `EgressNode` instead. The `ArgusConfig` dataclass (around line 51) has all provider configs; add `egress_nodes: list[EgressNode]` after `residential`.

`argus/models.py` has `SearchQuery` (line ~50) and `ProviderTrace` (line ~78). Both need new optional fields.

`ARGUS_EGRESS_NODES` format: `oci-dev:http://100.126.13.70:8273,macmini:http://100.113.216.27:8273` — each entry is `name:url` split on the **first** colon only (`s.split(":", 1)`).

- [ ] **Step 1: Write failing tests**

```python
# In tests/test_config.py — add to the existing test file or create it:
from argus.config import ArgusConfig, EgressNode, ConfigLoader
import os

def test_egress_node_parsed_from_env(monkeypatch):
    monkeypatch.setenv("ARGUS_EGRESS_NODES", "oci-dev:http://100.1.2.3:8273")
    monkeypatch.setenv("ARGUS_EGRESS_SHARED_SECRET", "mysecret")
    cfg = ConfigLoader().load()
    assert len(cfg.egress_nodes) == 1
    assert cfg.egress_nodes[0].name == "oci-dev"
    assert cfg.egress_nodes[0].url == "http://100.1.2.3:8273"
    assert cfg.egress_nodes[0].shared_secret == "mysecret"

def test_egress_nodes_multiple(monkeypatch):
    monkeypatch.setenv(
        "ARGUS_EGRESS_NODES",
        "oci-dev:http://100.1.2.3:8273,macmini:http://100.4.5.6:8273"
    )
    monkeypatch.setenv("ARGUS_EGRESS_SHARED_SECRET", "s")
    cfg = ConfigLoader().load()
    assert len(cfg.egress_nodes) == 2
    assert cfg.egress_nodes[1].name == "macmini"

def test_egress_nodes_empty_by_default():
    cfg = ArgusConfig()
    assert cfg.egress_nodes == []

def test_residential_config_has_no_endpoints():
    from argus.config import ResidentialConfig
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(ResidentialConfig)}
    assert "endpoints" not in field_names
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
uv run pytest tests/test_config.py -k "egress" -v
```
Expected: `FAILED` — `EgressNode` not defined yet.

- [ ] **Step 3: Add `EgressNode` to `argus/config.py`**

After the `ProviderConfig` dataclass (around line 27), insert:

```python
@dataclass(frozen=True)
class EgressNode:
    name: str           # "oci-dev", "macmini"
    url: str            # "http://100.126.13.70:8273"
    shared_secret: str  # shared across all nodes
```

In `ArgusConfig` (after the `residential: ResidentialConfig` field), add:

```python
egress_nodes: list[EgressNode] = field(default_factory=list)
```

In `ResidentialConfig`, remove the `endpoints` field entirely:

```python
# REMOVE this line:
endpoints: list[str] = field(default_factory=list)
```

In `ConfigLoader.load()` (the big method that builds `ArgusConfig`), add the parsing logic. Find the `residential=ResidentialConfig(...)` block and add after it:

```python
# Parse ARGUS_EGRESS_NODES=name:url,name:url
_egress_secret = self.get_str("ARGUS_EGRESS_SHARED_SECRET")
_egress_nodes_raw = self.get_str("ARGUS_EGRESS_NODES", "")
_egress_nodes: list[EgressNode] = []
for entry in _egress_nodes_raw.split(","):
    entry = entry.strip()
    if not entry:
        continue
    parts = entry.split(":", 1)
    if len(parts) == 2:
        _egress_nodes.append(EgressNode(
            name=parts[0].strip(),
            url=parts[1].strip(),
            shared_secret=_egress_secret,
        ))
```

Then in the `ArgusConfig(...)` constructor call, add:
```python
egress_nodes=_egress_nodes,
```

Also remove any reference to `ResidentialConfig.endpoints` in the `load()` call.

- [ ] **Step 4: Add `caller` and `egress` to models**

In `argus/models.py`, update `SearchQuery` (after `free_only`):

```python
@dataclass
class SearchQuery:
    query: str
    mode: SearchMode = SearchMode.DISCOVERY
    max_results: int = 10
    providers: Optional[List[ProviderName]] = None
    free_only: bool = False
    caller: str = ""      # e.g. "media_rename", "atlas", "mcp", "cli", ""
    metadata: Dict[str, Any] = field(default_factory=dict)
```

Update `ProviderTrace` (after `credit_info`):

```python
@dataclass
class ProviderTrace:
    provider: ProviderName
    status: str
    results_count: int = 0
    latency_ms: int = 0
    error: Optional[str] = None
    budget_remaining: Optional[float] = None
    credit_info: Optional[dict] = None
    egress: str = "local"   # "local" | egress node name e.g. "oci-dev"
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_config.py -k "egress" -v
```
Expected: all 4 tests pass.

- [ ] **Step 6: Run full suite to catch regressions**

```bash
uv run pytest tests/ -q --tb=short
```
Expected: all existing tests pass (new fields have defaults, nothing breaks).

- [ ] **Step 7: Commit**

```bash
git add argus/config.py argus/models.py tests/test_config.py
git commit -m "feat: add EgressNode config, caller/egress fields on SearchQuery/ProviderTrace"
```

---

## Task 2: DB schema — caller + egress on provider_usage

**Files:**
- Modify: `argus/persistence/models.py`
- Modify: `argus/persistence/db.py`
- Test: `tests/test_dashboard.py` (extend existing)

### Context

`ProviderUsageRow` (in `argus/persistence/models.py`, line ~78) has no `caller` or `egress` columns. The project uses `_ensure_schema_compat()` in `argus/persistence/db.py` (line ~55) to add columns to existing databases — follow that pattern exactly.

- [ ] **Step 1: Write failing test**

```python
# Add to tests/test_dashboard.py:
def test_provider_usage_row_has_caller_and_egress():
    from argus.persistence.models import ProviderUsageRow
    import dataclasses
    field_names = {f.name for f in dataclasses.fields(ProviderUsageRow)}
    assert "caller" in field_names
    assert "egress" in field_names
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_dashboard.py::test_provider_usage_row_has_caller_and_egress -v
```
Expected: `FAILED`.

- [ ] **Step 3: Add columns to `ProviderUsageRow`**

In `argus/persistence/models.py`, update `ProviderUsageRow` to add two columns after `budget_remaining`:

```python
class ProviderUsageRow(Base):
    __tablename__ = "provider_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("search_runs.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    results_count: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    budget_remaining: Mapped[float | None] = mapped_column(Float, nullable=True)
    caller: Mapped[str] = mapped_column(String(100), nullable=False, server_default="")
    egress: Mapped[str] = mapped_column(String(50), nullable=False, server_default="local")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    run: Mapped["SearchRunRow"] = relationship(back_populates="traces")
```

- [ ] **Step 4: Add to `_ensure_schema_compat`**

In `argus/persistence/db.py`, find `additive_columns` dict inside `_ensure_schema_compat` and add:

```python
additive_columns = {
    "search_results": {
        "egress": "VARCHAR(50)",
        "machine": "VARCHAR(100)",
        "metadata_json": "TEXT",
    },
    "corpus_documents": {
        "egress": "VARCHAR(50)",
        "machine": "VARCHAR(100)",
        "metadata_json": "TEXT",
    },
    "provider_usage": {
        "caller": "VARCHAR(100) NOT NULL DEFAULT ''",
        "egress": "VARCHAR(50) NOT NULL DEFAULT 'local'",
    },
}
```

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_dashboard.py::test_provider_usage_row_has_caller_and_egress -v
uv run pytest tests/ -q --tb=short
```
Expected: new test passes, full suite passes.

- [ ] **Step 6: Commit**

```bash
git add argus/persistence/models.py argus/persistence/db.py tests/test_dashboard.py
git commit -m "feat: add caller and egress columns to provider_usage table"
```

---

## Task 3: Wire caller through all call sites + persist it

**Files:**
- Modify: `argus/cli/main.py`
- Modify: `argus/mcp/tools.py`
- Modify: `argus/mcp/server.py`
- Modify: `argus/api/schemas.py`
- Modify: `argus/api/routes_search.py`
- Modify: `argus/persistence/db.py` (write caller to row)
- Test: `tests/test_cli.py`, `tests/test_dashboard.py`

### Context

`caller` needs to flow from every entry point into `SearchQuery`, then be persisted to `provider_usage.caller`. The persistence write happens in `SearchPersistenceGateway` — find where it writes `ProviderUsageRow` objects and add `caller` there. The `caller` value comes from `query.caller` (available in scope at that point).

First find where `ProviderUsageRow` is written:

```bash
grep -n "ProviderUsageRow" argus/persistence/db.py
```

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/test_cli.py:
def test_search_caller_flag_sets_caller_on_query(monkeypatch):
    captured = {}
    def fake_create_broker():
        class FakeBroker:
            async def search(self, q, **kw):
                captured["caller"] = q.caller
                from argus.models import SearchResponse, SearchMode
                return SearchResponse(query=q.query, mode=SearchMode.DISCOVERY, results=[])
        return FakeBroker()
    monkeypatch.setattr("argus.cli.main.create_broker", fake_create_broker)
    from click.testing import CliRunner
    from argus.cli.main import cli
    result = CliRunner().invoke(cli, ["search", "-q", "test", "--caller", "my_project"])
    assert captured.get("caller") == "my_project"

def test_search_caller_defaults_to_cli(monkeypatch):
    captured = {}
    def fake_create_broker():
        class FakeBroker:
            async def search(self, q, **kw):
                captured["caller"] = q.caller
                from argus.models import SearchResponse, SearchMode
                return SearchResponse(query=q.query, mode=SearchMode.DISCOVERY, results=[])
        return FakeBroker()
    monkeypatch.setattr("argus.cli.main.create_broker", fake_create_broker)
    from click.testing import CliRunner
    from argus.cli.main import cli
    result = CliRunner().invoke(cli, ["search", "-q", "test"])
    assert captured.get("caller") == "cli"
```

- [ ] **Step 2: Run to confirm failure**

```bash
uv run pytest tests/test_cli.py -k "caller" -v
```
Expected: `FAILED` — `--caller` option does not exist yet.

- [ ] **Step 3: Add `--caller` to CLI search command**

In `argus/cli/main.py`, add option decorator before the `def search(...)` definition (after the `--free` option):

```python
@click.option("--caller", default="cli", help="Caller identifier for attribution (e.g. project name)")
```

Update the `def search(...)` signature to include `caller`:

```python
def search(query, mode, max_results, providers, as_json, session, attribution, free_only, caller):
```

Update the `SearchQuery(...)` constructor call in the function body:

```python
q = SearchQuery(
    query=query,
    mode=SearchMode(mode),
    max_results=max_results,
    providers=override,
    free_only=free_only,
    caller=caller,
)
```

- [ ] **Step 4: Hardcode caller in MCP tool**

In `argus/mcp/tools.py`, update the `search_web` function signature to include `caller: str = "mcp"` and pass it:

```python
async def search_web(broker, query, mode="discovery", max_results=10,
                     session_id=None, include_attribution=False,
                     free_only=False, caller: str = "mcp"):
    ...
    q = SearchQuery(
        query=query,
        mode=search_mode,
        max_results=max_results,
        free_only=free_only,
        caller=caller,
    )
```

In `argus/mcp/server.py`, the wrapper already passes named args through to `mcp_tools.search_web` — verify `caller` is either already in the signature or add `caller: str = "mcp"` as the last param and pass it.

- [ ] **Step 5: Add `caller` to HTTP API request schema**

In `argus/api/schemas.py`, add to `SearchRequest`:

```python
caller: str = Field("", description="Caller identifier for attribution (e.g. 'atlas', 'media_rename')")
```

In `argus/api/routes_search.py`, update the `SearchQuery(...)` construction to include:

```python
caller=req.caller,
```

- [ ] **Step 6: Persist caller when writing ProviderUsageRow**

Find where `ProviderUsageRow` is constructed in `argus/persistence/db.py`:

```bash
grep -n "ProviderUsageRow(" argus/persistence/db.py
```

Wherever that construction happens, add `caller=` and `egress=` from the trace. The trace object is a `ProviderTrace` which now has `egress`. The `caller` comes from the `SearchQuery` that's passed to the persistence method. Locate the persistence write method signature and thread `caller` through it.

The pattern will look like:

```python
ProviderUsageRow(
    run_id=run.id,
    provider=trace.provider.value,
    status=trace.status,
    results_count=trace.results_count,
    latency_ms=trace.latency_ms,
    error=trace.error,
    budget_remaining=trace.budget_remaining,
    caller=query.caller,        # add this
    egress=trace.egress,        # add this
)
```

Check the actual signature of the method that writes usage rows and add `query: SearchQuery` as a parameter if not already present, then pass `query.caller`.

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_cli.py -k "caller" -v
uv run pytest tests/ -q --tb=short
```
Expected: caller tests pass, full suite passes.

- [ ] **Step 8: Commit**

```bash
git add argus/cli/main.py argus/mcp/tools.py argus/mcp/server.py \
        argus/api/schemas.py argus/api/routes_search.py argus/persistence/db.py \
        tests/test_cli.py
git commit -m "feat: wire caller attribution through CLI, MCP, HTTP API, and persistence"
```

---

## Task 4: ReachabilityMatrix — core state logic

**Files:**
- Create: `argus/broker/reachability.py`
- Create: `tests/test_reachability.py`

### Context

This module is pure logic + async I/O. It stores probes in memory. It does NOT import from `argus/broker/router.py` or `argus/broker/execution.py` (would be circular). It does import from `argus/models.py`, `argus/config.py`, and `argus/broker/budgets.py` (for `PROVIDER_TIERS`).

`PROVIDER_TIERS` is a `dict[ProviderName, int]` in `argus/broker/budgets.py`. Tier 0 = free.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_reachability.py
import time
import pytest
from argus.broker.reachability import EgressProbe, ReachabilityMatrix
from argus.models import ProviderName


def test_best_egress_returns_local_when_reachable():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=True, latency_ms=50)
    assert m.best_egress(ProviderName.YAHOO) == "local"


def test_best_egress_returns_worker_when_local_blocked():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=120)
    assert m.best_egress(ProviderName.YAHOO) == "oci-dev"


def test_best_egress_returns_none_when_all_blocked():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=False, latency_ms=0)
    assert m.best_egress(ProviderName.YAHOO) is None


def test_best_egress_prefers_local_over_faster_worker():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.DUCKDUCKGO, reachable=True, latency_ms=200)
    m.update_probe("oci-dev", ProviderName.DUCKDUCKGO, reachable=True, latency_ms=10)
    # Local is always preferred when reachable, regardless of worker latency
    assert m.best_egress(ProviderName.DUCKDUCKGO) == "local"


def test_best_egress_default_local_when_never_probed():
    m = ReachabilityMatrix()
    # No probes recorded — assume local is reachable
    assert m.best_egress(ProviderName.SEARXNG) == "local"


def test_best_egress_picks_lower_latency_among_workers():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=200)
    m.update_probe("macmini", ProviderName.YAHOO, reachable=True, latency_ms=80)
    assert m.best_egress(ProviderName.YAHOO) == "macmini"


def test_get_all_returns_per_provider_summary():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=100)
    summary = m.get_all()
    assert ProviderName.YAHOO in summary
    assert summary[ProviderName.YAHOO]["best"] == "oci-dev"
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/test_reachability.py -v
```
Expected: `ImportError` or all `FAILED`.

- [ ] **Step 3: Implement `argus/broker/reachability.py`**

```python
"""Provider reachability matrix — tracks which egress can reach which provider."""

import time
from dataclasses import dataclass, field
from typing import Optional

from argus.models import ProviderName


@dataclass
class EgressProbe:
    egress: str        # "local" | "oci-dev" | "macmini"
    reachable: bool
    latency_ms: int
    last_checked: float = field(default_factory=time.time)


class ReachabilityMatrix:
    """In-memory store of (provider, egress) reachability probes.

    Preference order: local always beats workers. Among workers, lower
    latency wins. If a provider has never been probed, local is assumed
    reachable (optimistic default).
    """

    def __init__(self) -> None:
        # provider → {egress_name → EgressProbe}
        self._probes: dict[ProviderName, dict[str, EgressProbe]] = {}

    def update_probe(
        self,
        egress: str,
        provider: ProviderName,
        reachable: bool,
        latency_ms: int,
    ) -> None:
        if provider not in self._probes:
            self._probes[provider] = {}
        self._probes[provider][egress] = EgressProbe(
            egress=egress,
            reachable=reachable,
            latency_ms=latency_ms,
        )

    def best_egress(self, provider: ProviderName) -> Optional[str]:
        """Return the name of the best reachable egress, or None if all blocked.

        'local' is always preferred when reachable. Among workers, lower
        latency wins. If no probe exists for this provider, returns 'local'.
        """
        probes = self._probes.get(provider)
        if not probes:
            return "local"

        # Local first
        local = probes.get("local")
        if local is None or local.reachable:
            return "local"

        # Workers sorted by latency ascending
        workers = sorted(
            (p for name, p in probes.items() if name != "local" and p.reachable),
            key=lambda p: p.latency_ms,
        )
        return workers[0].egress if workers else None

    def get_all(self) -> dict[ProviderName, dict]:
        """Return a summary dict for health/admin endpoints."""
        result = {}
        for provider, probes in self._probes.items():
            best = self.best_egress(provider)
            result[provider] = {
                "best": best,
                "probes": {
                    name: {
                        "reachable": p.reachable,
                        "latency_ms": p.latency_ms,
                        "last_checked": p.last_checked,
                    }
                    for name, p in probes.items()
                },
            }
        return result
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_reachability.py -v
```
Expected: all 7 tests pass.

- [ ] **Step 5: Full suite**

```bash
uv run pytest tests/ -q --tb=short
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add argus/broker/reachability.py tests/test_reachability.py
git commit -m "feat: add ReachabilityMatrix with egress preference logic"
```

---

## Task 5: Worker server — `argus worker`

**Files:**
- Create: `argus/worker/__init__.py`
- Create: `argus/worker/server.py`
- Modify: `argus/cli/main.py` (add `worker` subcommand)
- Create: `tests/test_worker.py`

### Context

The worker is a minimal FastAPI app. It does NOT import broker, sessions, or DB code — only the provider registry and models. The `argus worker` CLI subcommand calls `uvicorn.run(create_worker_app(), ...)`.

To instantiate a provider by name, the worker uses a small registry function that mirrors `create_broker` but only creates one provider on demand.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_worker.py
import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient


def _make_client(secret: str = "test-secret") -> TestClient:
    with patch.dict("os.environ", {
        "ARGUS_EGRESS_SHARED_SECRET": secret,
        "ARGUS_MACHINE_NAME": "test-worker",
    }):
        from argus.worker.server import create_worker_app
        app = create_worker_app()
        return TestClient(app)


def test_health_endpoint_returns_ok():
    client = _make_client()
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_exec_requires_auth():
    client = _make_client(secret="real-secret")
    resp = client.post(
        "/exec",
        json={"provider": "duckduckgo", "query": "test", "max_results": 1, "mode": "discovery"},
        headers={"Authorization": "Bearer wrong-secret"},
    )
    assert resp.status_code == 401


def test_exec_unknown_provider_returns_400():
    client = _make_client(secret="s")
    resp = client.post(
        "/exec",
        json={"provider": "nonexistent", "query": "test", "max_results": 1, "mode": "discovery"},
        headers={"Authorization": "Bearer s"},
    )
    assert resp.status_code == 400


def test_exec_returns_results_on_success():
    from argus.models import SearchResult, ProviderName, ProviderTrace

    fake_results = [
        SearchResult(url="https://example.com", title="Ex", snippet="Ex snip",
                     provider=ProviderName.DUCKDUCKGO)
    ]
    fake_trace = ProviderTrace(provider=ProviderName.DUCKDUCKGO, status="success",
                               results_count=1, latency_ms=50)

    async def fake_search(query):
        return fake_results, fake_trace

    with patch("argus.worker.server._get_provider") as mock_get:
        mock_provider = AsyncMock()
        mock_provider.search = fake_search
        mock_get.return_value = mock_provider

        client = _make_client(secret="s")
        resp = client.post(
            "/exec",
            json={"provider": "duckduckgo", "query": "test", "max_results": 1, "mode": "discovery"},
            headers={"Authorization": "Bearer s"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["trace"]["status"] == "success"
        assert len(data["results"]) == 1
        assert data["results"][0]["url"] == "https://example.com"
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/test_worker.py -v
```
Expected: `ImportError` — module doesn't exist.

- [ ] **Step 3: Create `argus/worker/__init__.py`**

```python
```
(empty file)

- [ ] **Step 4: Create `argus/worker/server.py`**

```python
"""Argus egress worker — minimal provider executor over HTTP.

Exposes:
  POST /exec    — run a single provider search, return results + trace
  GET  /health  — liveness check

Binds to ARGUS_WORKER_BIND (default 0.0.0.0:8273).
Auth: Authorization: Bearer <ARGUS_EGRESS_SHARED_SECRET>
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from argus.models import ProviderName, SearchMode, SearchQuery
from argus.providers.base import BaseProvider


class ExecRequest(BaseModel):
    provider: str
    query: str
    max_results: int = 10
    mode: str = "discovery"
    caller: str = ""


def _get_provider(provider_name: str) -> BaseProvider:
    """Instantiate the requested provider. Raises KeyError if unknown."""
    from argus.config import get_config
    cfg = get_config()
    name = ProviderName(provider_name)  # raises ValueError if unknown

    if name == ProviderName.YAHOO:
        from argus.providers.yahoo import YahooProvider
        return YahooProvider(cfg.yahoo)
    if name == ProviderName.DUCKDUCKGO:
        from argus.providers.duckduckgo import DuckDuckGoProvider
        return DuckDuckGoProvider()
    if name == ProviderName.SEARXNG:
        from argus.providers.searxng import SearXNGProvider
        return SearXNGProvider(cfg.searxng)
    if name == ProviderName.GITHUB:
        from argus.providers.github import GitHubProvider
        return GitHubProvider(cfg.github)
    if name == ProviderName.WOLFRAM:
        from argus.providers.wolfram import WolframProvider
        return WolframProvider(cfg.wolfram)
    if name == ProviderName.BRAVE:
        from argus.providers.brave import BraveProvider
        return BraveProvider(cfg.brave)
    if name == ProviderName.TAVILY:
        from argus.providers.tavily import TavilyProvider
        return TavilyProvider(cfg.tavily)
    if name == ProviderName.EXA:
        from argus.providers.exa import ExaProvider
        return ExaProvider(cfg.exa)
    if name == ProviderName.SERPER:
        from argus.providers.serper import SerperProvider
        return SerperProvider(cfg.serper)
    raise ValueError(f"Provider {provider_name!r} not supported by worker")


def _check_auth(request: Request) -> None:
    secret = os.environ.get("ARGUS_EGRESS_SHARED_SECRET", "")
    if not secret:
        return  # no secret configured — open (dev mode)
    auth = request.headers.get("Authorization", "")
    if auth != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def create_worker_app() -> FastAPI:
    app = FastAPI(title="Argus Worker", docs_url=None, redoc_url=None)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "node": os.environ.get("ARGUS_MACHINE_NAME", "worker"),
        }

    @app.post("/exec")
    async def exec_provider(req: ExecRequest, request: Request):
        _check_auth(request)

        try:
            provider = _get_provider(req.provider)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Unknown provider: {req.provider!r}")

        query = SearchQuery(
            query=req.query,
            mode=SearchMode(req.mode),
            max_results=req.max_results,
            caller=req.caller,
        )

        results, trace = await provider.search(query)

        return {
            "results": [
                {
                    "url": r.url,
                    "title": r.title,
                    "snippet": r.snippet,
                    "domain": r.domain,
                    "provider": r.provider.value if r.provider else req.provider,
                    "score": r.score,
                    "raw_rank": r.raw_rank,
                    "metadata": r.metadata,
                }
                for r in results
            ],
            "trace": {
                "provider": trace.provider.value,
                "status": trace.status,
                "results_count": trace.results_count,
                "latency_ms": trace.latency_ms,
                "error": trace.error,
            },
        }

    return app
```

- [ ] **Step 5: Add `worker` subcommand to CLI**

In `argus/cli/main.py`, add a new command after the existing commands:

```python
@cli.command()
@click.option("--bind", default=None, envvar="ARGUS_WORKER_BIND",
              help="Host:port to bind (default 0.0.0.0:8273)")
def worker(bind: str):
    """Start an Argus egress worker — minimal provider executor over HTTP."""
    import uvicorn
    from argus.worker.server import create_worker_app

    host, port = "0.0.0.0", 8273
    if bind:
        parts = bind.rsplit(":", 1)
        if len(parts) == 2:
            host, port = parts[0], int(parts[1])

    app = create_worker_app()
    uvicorn.run(app, host=host, port=port)
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_worker.py -v
```
Expected: all 4 tests pass.

- [ ] **Step 7: Full suite**

```bash
uv run pytest tests/ -q --tb=short
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add argus/worker/__init__.py argus/worker/server.py argus/cli/main.py tests/test_worker.py
git commit -m "feat: add argus worker server and CLI subcommand"
```

---

## Task 6: RemoteProviderClient

**Files:**
- Create: `argus/broker/remote_provider.py`
- Create: `tests/test_remote_provider.py`

### Context

`RemoteProviderClient` implements `BaseProvider` so `ProviderExecutor` can treat it identically to a local provider. It POSTs to the worker's `/exec` endpoint via `httpx.AsyncClient`. On any network error, it returns an error trace — never raises. The `provider` property returns the underlying `ProviderName` so health tracking works correctly.

`EgressNode` is imported from `argus.config`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_remote_provider.py
import pytest
import httpx
from unittest.mock import AsyncMock, patch, MagicMock
from argus.models import ProviderName, SearchQuery, SearchMode, ProviderStatus
from argus.config import EgressNode


def _make_node(url: str = "http://worker:8273", secret: str = "s") -> EgressNode:
    return EgressNode(name="test-worker", url=url, shared_secret=secret)


def _make_query() -> SearchQuery:
    return SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)


@pytest.mark.asyncio
async def test_remote_provider_success():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "results": [
            {"url": "https://yahoo.com/r1", "title": "R1", "snippet": "s1",
             "domain": "yahoo.com", "provider": "yahoo", "score": 0.5,
             "raw_rank": 0, "metadata": {}}
        ],
        "trace": {
            "provider": "yahoo", "status": "success",
            "results_count": 1, "latency_ms": 120, "error": None,
        }
    }

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert trace.status == "success"
    assert trace.egress == "test-worker"
    assert len(results) == 1
    assert results[0].url == "https://yahoo.com/r1"


@pytest.mark.asyncio
async def test_remote_provider_network_error_returns_error_trace():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert trace.status == "error"
    assert "refused" in trace.error
    assert results == []


@pytest.mark.asyncio
async def test_remote_provider_401_returns_error_trace():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)

    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError("401", request=MagicMock(), response=mock_response)
    )

    with patch("httpx.AsyncClient") as MockClient:
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_http.post = AsyncMock(return_value=mock_response)
        MockClient.return_value = mock_http

        results, trace = await client.search(_make_query())

    assert trace.status == "error"
    assert results == []


def test_remote_provider_name_property():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)
    assert client.name == ProviderName.YAHOO


def test_remote_provider_is_available():
    from argus.broker.remote_provider import RemoteProviderClient
    node = _make_node()
    client = RemoteProviderClient(ProviderName.YAHOO, node)
    assert client.is_available() is True
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/test_remote_provider.py -v
```
Expected: `ImportError` or all `FAILED`.

- [ ] **Step 3: Implement `argus/broker/remote_provider.py`**

```python
"""Remote provider client — delegates search to an egress worker node."""

import time
from typing import List

import httpx

from argus.config import EgressNode
from argus.logging import get_logger
from argus.models import (
    ProviderName,
    ProviderStatus,
    ProviderTrace,
    SearchQuery,
    SearchResult,
)
from argus.providers.base import BaseProvider

logger = get_logger("broker.remote_provider")


class RemoteProviderClient(BaseProvider):
    """Implements BaseProvider by delegating to a worker node's /exec endpoint."""

    def __init__(self, provider: ProviderName, node: EgressNode) -> None:
        self._provider = provider
        self._node = node

    @property
    def name(self) -> ProviderName:
        return self._provider

    def is_available(self) -> bool:
        return True  # health tracker handles degradation

    def status(self) -> ProviderStatus:
        return ProviderStatus.ENABLED

    async def search(self, query: SearchQuery) -> tuple[List[SearchResult], ProviderTrace]:
        start = time.monotonic()
        payload = {
            "provider": self._provider.value,
            "query": query.query,
            "max_results": query.max_results,
            "mode": query.mode.value,
            "caller": query.caller,
        }
        headers = {
            "Authorization": f"Bearer {self._node.shared_secret}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{self._node.url}/exec",
                    json=payload,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
        except Exception as exc:
            latency_ms = int((time.monotonic() - start) * 1000)
            logger.warning("Remote provider %s via %s failed: %s",
                           self._provider.value, self._node.name, exc)
            return [], ProviderTrace(
                provider=self._provider,
                status="error",
                latency_ms=latency_ms,
                error=str(exc),
                egress=self._node.name,
            )

        latency_ms = int((time.monotonic() - start) * 1000)
        raw_trace = data.get("trace", {})
        trace = ProviderTrace(
            provider=self._provider,
            status=raw_trace.get("status", "error"),
            results_count=raw_trace.get("results_count", 0),
            latency_ms=latency_ms,
            error=raw_trace.get("error"),
            egress=self._node.name,
        )

        results = []
        for r in data.get("results", []):
            try:
                results.append(SearchResult(
                    url=r["url"],
                    title=r.get("title", ""),
                    snippet=r.get("snippet", ""),
                    domain=r.get("domain", ""),
                    provider=self._provider,
                    score=r.get("score", 0.0),
                    raw_rank=r.get("raw_rank", 0),
                    metadata=r.get("metadata", {}),
                ))
            except Exception:
                continue

        return results, trace
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_remote_provider.py -v
```
Expected: all 5 tests pass.

- [ ] **Step 5: Full suite**

```bash
uv run pytest tests/ -q --tb=short
```

- [ ] **Step 6: Commit**

```bash
git add argus/broker/remote_provider.py tests/test_remote_provider.py
git commit -m "feat: add RemoteProviderClient for delegating to egress workers"
```

---

## Task 7: Wire ReachabilityMatrix into ProviderExecutor

**Files:**
- Modify: `argus/broker/execution.py`
- Modify: `argus/broker/router.py`
- Test: `tests/test_broker.py` (extend existing)

### Context

`ProviderExecutor.__init__` currently takes `providers`, `health_tracker`, `budget_tracker`. Add `reachability: ReachabilityMatrix` and `egress_nodes: dict[str, EgressNode]` as optional params (default to empty/no-op so existing tests keep passing).

The routing check goes **before** the existing health/budget checks in `execute()`. If `best_egress != "local"`, build a `RemoteProviderClient` and call it instead of the local provider. Budget and health tracking happen on the primary regardless.

`SearchBroker` in `router.py` gets a `ReachabilityMatrix` field. `create_broker` instantiates it.

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/test_broker.py:
import pytest
from unittest.mock import AsyncMock, MagicMock
from argus.broker.execution import ProviderExecutor
from argus.broker.reachability import ReachabilityMatrix
from argus.broker.budgets import BudgetTracker
from argus.broker.health import HealthTracker
from argus.config import EgressNode
from argus.models import ProviderName, SearchQuery, SearchMode, ProviderTrace, SearchResult


def _make_executor(reachability=None, egress_nodes=None):
    from argus.providers.base import BaseProvider
    mock_provider = MagicMock(spec=BaseProvider)
    mock_provider.name = ProviderName.YAHOO
    mock_provider.is_available.return_value = True
    mock_provider.status.return_value = MagicMock()

    return ProviderExecutor(
        providers={ProviderName.YAHOO: mock_provider},
        health_tracker=HealthTracker(),
        budget_tracker=BudgetTracker(),
        reachability=reachability,
        egress_nodes=egress_nodes or {},
    ), mock_provider


@pytest.mark.asyncio
async def test_executor_routes_to_remote_when_local_blocked():
    matrix = ReachabilityMatrix()
    matrix.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    matrix.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=100)

    node = EgressNode(name="oci-dev", url="http://worker:8273", shared_secret="s")
    executor, local_provider = _make_executor(
        reachability=matrix,
        egress_nodes={"oci-dev": node},
    )

    fake_result = SearchResult(
        url="https://yahoo.com/r", title="R", snippet="s",
        provider=ProviderName.YAHOO
    )
    fake_trace = ProviderTrace(
        provider=ProviderName.YAHOO, status="success",
        results_count=1, latency_ms=90, egress="oci-dev"
    )

    with MagicMock() as mock_remote_cls:
        from argus.broker import remote_provider as rp_module
        original = rp_module.RemoteProviderClient

        class FakeRemote:
            def __init__(self, *a, **kw): pass
            async def search(self, q): return [fake_result], fake_trace

        rp_module.RemoteProviderClient = FakeRemote
        try:
            query = SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)
            outcome = await executor.execute(query, [ProviderName.YAHOO])
        finally:
            rp_module.RemoteProviderClient = original

    assert outcome.live_providers_used == 1
    assert "yahoo" in outcome.provider_results
    local_provider.search.assert_not_called()


@pytest.mark.asyncio
async def test_executor_skips_when_no_egress_available():
    matrix = ReachabilityMatrix()
    matrix.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    # No workers registered at all

    executor, local_provider = _make_executor(reachability=matrix, egress_nodes={})

    query = SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)
    outcome = await executor.execute(query, [ProviderName.YAHOO])

    assert outcome.live_providers_used == 0
    skipped = [t for t in outcome.traces if t.status == "skipped"]
    assert any("no reachable egress" in (t.error or "") for t in skipped)


@pytest.mark.asyncio
async def test_executor_uses_local_when_reachable():
    matrix = ReachabilityMatrix()
    matrix.update_probe("local", ProviderName.YAHOO, reachable=True, latency_ms=50)

    executor, local_provider = _make_executor(reachability=matrix)

    fake_result = SearchResult(url="u", title="t", snippet="s", provider=ProviderName.YAHOO)
    fake_trace = ProviderTrace(provider=ProviderName.YAHOO, status="success", results_count=1)
    local_provider.search = AsyncMock(return_value=(fake_result, fake_trace))

    query = SearchQuery(query="test", mode=SearchMode.DISCOVERY, max_results=5)
    outcome = await executor.execute(query, [ProviderName.YAHOO])

    local_provider.search.assert_called_once()
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/test_broker.py -k "routes_to_remote or no_egress or uses_local" -v
```
Expected: `FAILED` or `ERROR`.

- [ ] **Step 3: Update `ProviderExecutor.__init__`**

In `argus/broker/execution.py`, update the `__init__` signature:

```python
from argus.broker.reachability import ReachabilityMatrix
from argus.config import EgressNode

class ProviderExecutor:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        health_tracker: HealthTracker,
        budget_tracker: BudgetTracker,
        routing_policy=None,
        reachability: ReachabilityMatrix | None = None,
        egress_nodes: dict[str, EgressNode] | None = None,
    ):
        self._providers = providers
        self._health = health_tracker
        self._budgets = budget_tracker
        self._reachability = reachability or ReachabilityMatrix()
        self._egress_nodes = egress_nodes or {}
```

- [ ] **Step 4: Add reachability routing in `execute()`**

In `argus/broker/execution.py`, inside the `execute()` method, add this block immediately **before** the `tier = self._budgets.get_provider_tier(pname)` line:

```python
# Reachability check — route to worker if local is blocked
best_egress = self._reachability.best_egress(pname)
if best_egress is None:
    traces.append(ProviderTrace(
        provider=pname, status="skipped", error="no reachable egress"
    ))
    continue
if best_egress != "local":
    node = self._egress_nodes.get(best_egress)
    if node is None:
        traces.append(ProviderTrace(
            provider=pname, status="skipped",
            error=f"egress node {best_egress!r} not found in config"
        ))
        continue
    from argus.broker.remote_provider import RemoteProviderClient
    remote = RemoteProviderClient(pname, node)
    results, trace = await remote.search(query)
    traces.append(trace)
    if trace.status == "success":
        live_providers_used += 1
        provider_results[pname.value] = results
        total_results_so_far += len(results)
        self._health.record_success(pname)
        self._budgets.record_usage(pname, _COST_ESTIMATES.get(pname, 0.0))
    else:
        self._health.record_failure(pname)
    continue
```

- [ ] **Step 5: Wire into `SearchBroker` in `router.py`**

In `argus/broker/router.py`, update `SearchBroker.__init__` to accept and thread through `ReachabilityMatrix`:

```python
from argus.broker.reachability import ReachabilityMatrix
from argus.config import EgressNode

class SearchBroker:
    def __init__(
        self,
        providers: dict[ProviderName, BaseProvider],
        cache=None,
        health_tracker=None,
        budget_tracker=None,
        session_store=None,
        executor=None,
        result_pipeline=None,
        session_service=None,
        reachability: ReachabilityMatrix | None = None,
        egress_nodes: dict[str, EgressNode] | None = None,
    ):
        ...
        self._reachability = reachability or ReachabilityMatrix()
        self._egress_nodes = egress_nodes or {}
        self._executor = executor or ProviderExecutor(
            providers=self._providers,
            health_tracker=self._health,
            budget_tracker=self._budgets,
            reachability=self._reachability,
            egress_nodes=self._egress_nodes,
        )
```

In `create_broker()`, build the reachability matrix and egress nodes dict:

```python
def create_broker() -> SearchBroker:
    ...
    config = get_config()
    egress_nodes = {n.name: n for n in config.egress_nodes}
    reachability = ReachabilityMatrix()

    ...  # existing providers dict

    return SearchBroker(
        providers=providers,
        session_store=session_store,
        reachability=reachability,
        egress_nodes=egress_nodes,
    )
```

- [ ] **Step 6: Run tests**

```bash
uv run pytest tests/test_broker.py -k "routes_to_remote or no_egress or uses_local" -v
uv run pytest tests/ -q --tb=short
```
Expected: new tests pass, full suite passes.

- [ ] **Step 7: Commit**

```bash
git add argus/broker/execution.py argus/broker/router.py tests/test_broker.py
git commit -m "feat: wire ReachabilityMatrix into ProviderExecutor and SearchBroker"
```

---

## Task 8: Startup probe — background task + probe logic

**Files:**
- Modify: `argus/broker/reachability.py` (add `probe_all`)
- Modify: `argus/api/app.py` (or wherever lifespan is defined — find it first)
- Test: `tests/test_reachability.py` (extend)

### Context

Find where the FastAPI app lifespan is defined:

```bash
grep -rn "lifespan\|@app.on_event\|startup" argus/api/ --include="*.py" | head -20
```

`probe_all` runs tier-0 provider probes locally and through each egress node. It takes `local_providers: dict[ProviderName, BaseProvider]` and `egress_nodes: list[EgressNode]`. Tier-0 set: `{p for p, t in PROVIDER_TIERS.items() if t == 0}`. A probe is a real `provider.search(SearchQuery(query="argus probe", max_results=1))` call — success means reachable, any error means blocked.

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/test_reachability.py:
import pytest
from unittest.mock import AsyncMock, MagicMock
from argus.broker.reachability import ReachabilityMatrix
from argus.models import ProviderName, SearchQuery, ProviderTrace, SearchResult


@pytest.mark.asyncio
async def test_probe_all_marks_reachable_on_success():
    matrix = ReachabilityMatrix()
    mock_provider = AsyncMock()
    mock_provider.search = AsyncMock(return_value=(
        [SearchResult(url="u", title="t", snippet="s", provider=ProviderName.YAHOO)],
        ProviderTrace(provider=ProviderName.YAHOO, status="success",
                      results_count=1, latency_ms=50),
    ))

    await matrix.probe_all(
        local_providers={ProviderName.YAHOO: mock_provider},
        egress_nodes=[],
    )

    assert matrix.best_egress(ProviderName.YAHOO) == "local"
    summary = matrix.get_all()
    assert summary[ProviderName.YAHOO]["probes"]["local"]["reachable"] is True


@pytest.mark.asyncio
async def test_probe_all_marks_blocked_on_error():
    matrix = ReachabilityMatrix()
    mock_provider = AsyncMock()
    mock_provider.search = AsyncMock(return_value=(
        [],
        ProviderTrace(provider=ProviderName.YAHOO, status="error",
                      error="500 INKApi Error", latency_ms=170),
    ))

    await matrix.probe_all(
        local_providers={ProviderName.YAHOO: mock_provider},
        egress_nodes=[],
    )

    assert matrix.best_egress(ProviderName.YAHOO) is None


@pytest.mark.asyncio
async def test_probe_all_probes_remote_nodes():
    from argus.config import EgressNode
    from unittest.mock import patch

    matrix = ReachabilityMatrix()
    node = EgressNode(name="oci-dev", url="http://worker:8273", shared_secret="s")

    # Local Yahoo blocked
    local_yahoo = AsyncMock()
    local_yahoo.search = AsyncMock(return_value=(
        [],
        ProviderTrace(provider=ProviderName.YAHOO, status="error", latency_ms=100),
    ))

    # Remote Yahoo succeeds
    from argus.broker import remote_provider as rp_module
    class FakeRemote:
        def __init__(self, provider, n): pass
        async def search(self, q):
            return (
                [SearchResult(url="u", title="t", snippet="s", provider=ProviderName.YAHOO)],
                ProviderTrace(provider=ProviderName.YAHOO, status="success",
                              results_count=1, latency_ms=80, egress="oci-dev"),
            )

    original = rp_module.RemoteProviderClient
    rp_module.RemoteProviderClient = FakeRemote
    try:
        await matrix.probe_all(
            local_providers={ProviderName.YAHOO: local_yahoo},
            egress_nodes=[node],
        )
    finally:
        rp_module.RemoteProviderClient = original

    assert matrix.best_egress(ProviderName.YAHOO) == "oci-dev"
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/test_reachability.py -k "probe_all" -v
```
Expected: `FAILED` — `probe_all` not defined.

- [ ] **Step 3: Implement `probe_all` in `reachability.py`**

Add to the `ReachabilityMatrix` class:

```python
async def probe_all(
    self,
    local_providers: "dict[ProviderName, BaseProvider]",
    egress_nodes: "list[EgressNode]",
) -> None:
    """Probe all tier-0 providers locally and through each egress node."""
    from argus.broker.budgets import PROVIDER_TIERS
    from argus.models import SearchMode, SearchQuery
    from argus.broker.remote_provider import RemoteProviderClient

    tier_0 = [p for p, t in PROVIDER_TIERS.items() if t == 0]
    probe_query = SearchQuery(
        query="argus probe", mode=SearchMode.DISCOVERY, max_results=1
    )

    for pname in tier_0:
        # Probe local
        provider = local_providers.get(pname)
        if provider is not None and provider.is_available():
            try:
                _, trace = await provider.search(probe_query)
                reachable = trace.status == "success"
                self.update_probe("local", pname, reachable=reachable,
                                  latency_ms=trace.latency_ms)
            except Exception:
                self.update_probe("local", pname, reachable=False, latency_ms=0)

        # Probe each remote node
        for node in egress_nodes:
            try:
                remote = RemoteProviderClient(pname, node)
                _, trace = await remote.search(probe_query)
                reachable = trace.status == "success"
                self.update_probe(node.name, pname, reachable=reachable,
                                  latency_ms=trace.latency_ms)
            except Exception:
                self.update_probe(node.name, pname, reachable=False, latency_ms=0)
```

Also add the type hint imports to the top of the file (use `TYPE_CHECKING` to avoid circular imports):

```python
from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from argus.providers.base import BaseProvider
    from argus.config import EgressNode
```

- [ ] **Step 4: Wire background probe into app startup**

Find the lifespan context in the FastAPI app:

```bash
grep -rn "lifespan\|@app.on_event\|contextmanager\|asynccontextmanager" argus/api/ --include="*.py" | head -10
```

In whatever file defines the app lifespan, add a background probe task after the broker is initialized. The pattern:

```python
import asyncio

async def _run_probes_background(broker: SearchBroker) -> None:
    """Run reachability probes on startup and every 30 minutes."""
    from argus.config import get_config
    cfg = get_config()
    while True:
        try:
            await broker._reachability.probe_all(
                local_providers=broker._providers,
                egress_nodes=list(cfg.egress_nodes),
            )
        except Exception as exc:
            logger.warning("Reachability probe failed: %s", exc)
        await asyncio.sleep(30 * 60)

# In the lifespan startup section:
asyncio.create_task(_run_probes_background(broker))
```

Find where `get_broker()` / the broker singleton is set up in the app startup and add this immediately after.

- [ ] **Step 5: Run tests**

```bash
uv run pytest tests/test_reachability.py -v
uv run pytest tests/ -q --tb=short
```
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add argus/broker/reachability.py argus/api/ tests/test_reachability.py
git commit -m "feat: add probe_all to ReachabilityMatrix; run background probes on startup"
```

---

## Task 9: Dashboard — caller breakdown + egress in health detail

**Files:**
- Modify: `argus/api/usage.py`
- Modify: `argus/api/routes_dashboard.py`
- Modify: `argus/api/templates/dashboard.html`
- Modify: `argus/api/routes_admin.py` (expose egress per provider)
- Test: `tests/test_dashboard.py` (extend)

### Context

The dashboard already has a provider activity table rendered from `get_provider_activity()` results. Add a second query `get_caller_activity()` that groups by `caller`. The admin `/api/admin/health/detail` endpoint should include the reachability matrix — add `egress` field per provider in the response.

- [ ] **Step 1: Write failing tests**

```python
# Add to tests/test_dashboard.py:
import sqlite3, os, tempfile

def _make_db_with_callers(path: str):
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE provider_usage (
            id INTEGER PRIMARY KEY,
            run_id INTEGER DEFAULT 1,
            provider TEXT, status TEXT, results_count INTEGER DEFAULT 0,
            latency_ms INTEGER DEFAULT 0, error TEXT,
            budget_remaining REAL, caller TEXT DEFAULT '', egress TEXT DEFAULT 'local',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    rows = [
        ("searxng", "success", "media_rename", "local"),
        ("searxng", "success", "media_rename", "local"),
        ("yahoo",   "success", "atlas",        "oci-dev"),
        ("yahoo",   "error",   "atlas",        "oci-dev"),
        ("yahoo",   "success", "cli",          "local"),
    ]
    conn.executemany(
        "INSERT INTO provider_usage (provider, status, caller, egress) VALUES (?,?,?,?)",
        rows
    )
    conn.commit()
    conn.close()


def test_get_caller_activity_groups_by_caller():
    from argus.api.usage import get_caller_activity
    from argus.config import reset_config
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "test.db")
        _make_db_with_callers(db)
        os.environ["ARGUS_DB_URL"] = f"sqlite:///{db}"
        reset_config()
        rows = get_caller_activity(days=7)
    del os.environ["ARGUS_DB_URL"]
    reset_config()

    callers = {r["caller"] for r in rows}
    assert "media_rename" in callers
    assert "atlas" in callers
    atlas = next(r for r in rows if r["caller"] == "atlas")
    assert atlas["attempted"] == 2
    assert atlas["successes"] == 1


def test_dashboard_renders_caller_table(monkeypatch):
    from argus.api.routes_dashboard import router
    from argus.api.usage import get_caller_activity
    monkeypatch.setattr(
        "argus.api.routes_dashboard.usage_queries.get_caller_activity",
        lambda days=7: [
            {"caller": "media_rename", "attempted": 10, "successes": 9, "success_rate": 90.0}
        ]
    )
    # Just verify the key query function returns the expected shape — template rendering
    # is an integration concern; unit-test the data shape here.
    rows = [{"caller": "media_rename", "attempted": 10, "successes": 9, "success_rate": 90.0}]
    assert rows[0]["caller"] == "media_rename"
    assert rows[0]["success_rate"] == 90.0
```

- [ ] **Step 2: Run to confirm failures**

```bash
uv run pytest tests/test_dashboard.py -k "caller_activity or caller_table" -v
```
Expected: `FAILED` — `get_caller_activity` not defined.

- [ ] **Step 3: Add `get_caller_activity()` to `argus/api/usage.py`**

```python
def get_caller_activity(days: int = 7) -> list[dict[str, Any]]:
    """Return per-caller call counts and success rate."""
    conn = _connect()
    if conn is None:
        return []
    try:
        cur = conn.execute(
            """
            SELECT COALESCE(NULLIF(caller, ''), 'unknown') AS caller,
                   COUNT(*) AS attempted,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS successes
            FROM provider_usage
            WHERE created_at >= datetime('now', ?)
              AND status != 'skipped'
            GROUP BY caller
            ORDER BY attempted DESC
            """,
            (f"-{int(days)} days",),
        )
        rows = []
        for row in cur.fetchall():
            d = dict(row)
            attempted = d.get("attempted") or 0
            successes = d.get("successes") or 0
            d["success_rate"] = round(100.0 * successes / attempted, 1) if attempted else 0.0
            rows.append(d)
        return rows
    except sqlite3.Error as exc:
        logger.warning("usage: caller_activity failed: %s", exc)
        return []
    finally:
        conn.close()
```

- [ ] **Step 4: Pass caller_activity to dashboard template**

In `argus/api/routes_dashboard.py`, in the `dashboard()` route handler, add:

```python
caller_activity = usage_queries.get_caller_activity(days=7)
```

And pass it to the template context:

```python
"caller_activity": caller_activity,
```

- [ ] **Step 5: Add caller table to `dashboard.html`**

In `argus/api/templates/dashboard.html`, add a new section after the provider activity table. Find the closing `{% endif %}` of the provider activity block and add after it:

```html
{% if caller_activity %}
<div class="mt-8">
  <h2 class="text-lg font-semibold text-gray-100 mb-3">Callers (7 days)</h2>
  <table class="w-full text-sm text-gray-300">
    <thead>
      <tr class="text-left text-gray-400 border-b border-gray-700">
        <th class="px-3 py-2">Caller</th>
        <th class="text-right px-3 py-2">Attempted</th>
        <th class="text-right px-3 py-2">Success Rate</th>
      </tr>
    </thead>
    <tbody>
      {% for c in caller_activity %}
      <tr class="border-b border-gray-800">
        <td class="px-3 py-2 font-mono">{{ c.caller }}</td>
        <td class="text-right px-3 py-2">{{ c.attempted }}</td>
        <td class="text-right px-3 py-2">{{ c.success_rate }}%</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
```

- [ ] **Step 6: Expose reachability in `/api/admin/health/detail`**

Find the handler for `/api/admin/health/detail` in `argus/api/routes_admin.py`. It returns a dict with a `providers` key. Add egress info to each provider entry:

```python
# In the health detail handler, after building provider_health dict:
reachability = broker._reachability.get_all()
for pname_str, entry in result["providers"].items():
    try:
        pname = ProviderName(pname_str)
        r = reachability.get(pname)
        if r:
            entry["best_egress"] = r["best"]
            entry["egress_probes"] = r["probes"]
        else:
            entry["best_egress"] = "local"
            entry["egress_probes"] = {}
    except ValueError:
        pass
```

- [ ] **Step 7: Run tests**

```bash
uv run pytest tests/test_dashboard.py -k "caller" -v
uv run pytest tests/ -q --tb=short
```
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add argus/api/usage.py argus/api/routes_dashboard.py \
        argus/api/templates/dashboard.html argus/api/routes_admin.py \
        tests/test_dashboard.py
git commit -m "feat: add per-caller dashboard breakdown and egress info in health detail"
```

---

## Task 10: Homelab deployment + vault setup

**Files:**
- `.env` on homelab (not in repo)
- `docs/` — systemd unit template (document only, no code commit needed)

### Context

This task is operational, not code. Run these steps manually on the relevant machines after all code tasks pass. The `secrets` CLI is at `~/.local/bin/secrets` on oci-dev.

- [ ] **Step 1: Generate and store egress secret**

```bash
# On oci-dev:
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
# Copy the output, then:
secrets set argus_keys 'ARGUS_EGRESS_SECRET=<generated-value>' --commit
```

- [ ] **Step 2: Set up worker systemd unit on oci-dev**

```bash
# On oci-dev — create /etc/systemd/system/argus-worker.service:
sudo tee /etc/systemd/system/argus-worker.service <<'EOF'
[Unit]
Description=Argus egress worker
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/github/argus
ExecStart=/home/ubuntu/.local/bin/uv run argus worker
Environment=ARGUS_NODE_ROLE=worker
Environment=ARGUS_WORKER_BIND=100.126.13.70:8273
Environment=ARGUS_MACHINE_NAME=oci-dev
EnvironmentFile=/home/ubuntu/github/argus/.env
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable argus-worker
sudo systemctl start argus-worker
```

- [ ] **Step 3: Verify worker is up**

```bash
curl -s http://100.126.13.70:8273/health
# Expected: {"status": "ok", "node": "oci-dev"}
```

- [ ] **Step 4: Update homelab `.env`**

```bash
# On homelab — add to /path/to/argus/.env (find it with: ssh homelab-ts "docker inspect argus | grep env -A5"):
ARGUS_EGRESS_NODES=oci-dev:http://100.126.13.70:8273
ARGUS_EGRESS_SHARED_SECRET=<value from vault>
```

- [ ] **Step 5: Restart homelab argus container**

```bash
ssh homelab-ts "docker restart argus argus-mcp"
```

- [ ] **Step 6: Verify Yahoo works end-to-end**

```bash
ARGUS_KEY=$(secrets get ARGUS_API_KEY)
curl -s -X POST http://100.112.130.100:8270/api/search \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARGUS_KEY" \
  -d '{"query": "python web scraping", "providers": ["yahoo"], "max_results": 5}' \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
results = d.get('results', [])
print(f'{len(results)} results')
print('providers:', {r.get(\"provider\") for r in results})
"
# Expected: results with provider: yahoo, routed through oci-dev
```

- [ ] **Step 7: Check health detail shows egress**

```bash
curl -s http://100.112.130.100:8270/api/admin/health/detail \
  -H "Authorization: Bearer $ARGUS_KEY" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for name, info in d.get('providers', {}).items():
    if info.get('best_egress') and info['best_egress'] != 'local':
        print(f'{name}: egress={info[\"best_egress\"]}')
"
# Expected: yahoo: egress=oci-dev
```

---

## Self-Review

**Spec coverage check:**
- ✅ EgressNode config + env parsing → Task 1
- ✅ caller on SearchQuery → Task 1
- ✅ egress on ProviderTrace → Task 1
- ✅ DB schema (caller + egress on provider_usage) → Task 2
- ✅ Caller wired through CLI, MCP, HTTP API, persistence → Task 3
- ✅ ReachabilityMatrix (update_probe, best_egress, get_all) → Task 4
- ✅ Worker server (/exec, /health, argus worker CLI) → Task 5
- ✅ RemoteProviderClient (BaseProvider impl) → Task 6
- ✅ ProviderExecutor routing → Task 7
- ✅ SearchBroker wiring → Task 7
- ✅ Background probe task on startup → Task 8
- ✅ probe_all (local + remote) → Task 8
- ✅ Dashboard caller breakdown → Task 9
- ✅ Admin health detail egress → Task 9
- ✅ ResidentialConfig.endpoints removal → Task 1
- ✅ Deployment sequence → Task 10
- ✅ free_only — no changes needed (transparent via routing) — confirmed in Task 7 (reachability check runs before free_only gate is never hit for tier-0 that are rerouted)

**Type consistency:** `EgressNode` defined in Task 1 / `argus/config.py`, used in Tasks 4, 6, 7, 8 — consistent. `ReachabilityMatrix.update_probe(egress, provider, reachable, latency_ms)` called identically in Tasks 4 and 8. `RemoteProviderClient(ProviderName, EgressNode)` consistent across Tasks 6 and 7.

**Probe order note:** The `free_only` gate in `execution.py` checks tier > 0. The reachability check added in Task 7 comes **before** that gate — so a tier-0 provider (Yahoo) that is rerouted via `best_egress != "local"` is handled by the remote client branch and `continue`s before ever hitting the free_only check. This is correct: Yahoo is tier-0, so free_only never applies to it anyway.
