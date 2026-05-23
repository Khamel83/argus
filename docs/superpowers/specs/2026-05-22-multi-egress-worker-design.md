# Multi-Egress Worker Architecture

**Date:** 2026-05-22  
**Status:** Approved  
**Motivation:** Argus runs on homelab (Spectrum residential, 23.241.236.110). Yahoo Search blocks Spectrum IP ranges. oci-dev (Oracle, 141.148.146.79) can reach Yahoo. Rather than hardcoding "Yahoo → oci-dev," the system should be state-driven: declare available machines, probe what each can reach, route providers through whichever egress works — and update automatically when reachability changes.

---

## First Principles

- **One primary** runs the full broker. All other machines are workers.
- **State, not config** — the routing table is derived from live reachability probes, not env vars.
- **Workers are minimal** — they run one thing: a provider executor exposed over HTTP on Tailscale.
- **Tailscale is the transport** — all machines are always reachable at their Tailscale IPs. No firewall rules, no VPN setup.
- **free_only is a first-class concern** — tier-0 providers that are locally blocked must be recoverable via workers without any special flags.

---

## Architecture

```
Primary (homelab)                  Workers
┌─────────────────────────┐        ┌─────────────────┐
│  Broker                 │        │ argus worker    │
│   ProviderExecutor      │─HTTP──▶│  /exec          │ oci-dev
│   ReachabilityMatrix    │        │  (Yahoo, etc.)  │
│   RemoteProviderClient  │        └─────────────────┘
│                         │        ┌─────────────────┐
│  Full stack             │─HTTP──▶│ argus worker    │ macmini
│  (API, MCP, dashboard)  │        │  /exec          │
└─────────────────────────┘        └─────────────────┘
```

Primary and workers share the same argus codebase. Workers run `argus worker` — nothing else.

---

## Components

### 1. `EgressNode` (config)

New dataclass added to `argus/config.py`, parsed from `ARGUS_EGRESS_NODES`:

```python
@dataclass(frozen=True)
class EgressNode:
    name: str          # "oci-dev", "macmini"
    url: str           # "http://100.126.13.70:8273"
    shared_secret: str # same value on all nodes, from vault

@dataclass(frozen=True)
class ArgusConfig:
    ...
    egress_nodes: list[EgressNode] = field(default_factory=list)
```

Env var format:
```
ARGUS_EGRESS_NODES=oci-dev:http://100.126.13.70:8273,macmini:http://100.113.216.27:8273
ARGUS_EGRESS_SHARED_SECRET=<from vault: argus_keys.ARGUS_EGRESS_SECRET>
```

`ResidentialConfig.endpoints` is superseded by `EgressNode`. Keep `ResidentialConfig` for the cooldown window / policy fields only; remove `endpoints`.

### 2. Worker server (`argus worker`)

New Click subcommand. Starts a minimal FastAPI app:

```
POST /exec
  Request:  {provider: str, query: str, max_results: int, mode: str}
  Response: {results: [...], trace: {...}}
  Auth:     Authorization: Bearer <shared_secret>

GET /health
  Response: {status: "ok", node: str}
```

- Binds to `ARGUS_WORKER_BIND` (Tailscale IP:port, default 0.0.0.0:8273)
- Instantiates only the requested provider — no DB, no broker, no budget tracking
- Returns the same `SearchResult` and `ProviderTrace` shapes the primary already consumes
- Auth failure → 401. Unknown provider → 400. Provider error → 200 with `trace.status = "error"` (caller decides whether to retry)

Deployment (one systemd unit per worker machine):
```ini
[Unit]
Description=Argus egress worker

[Service]
ExecStart=argus worker
Environment=ARGUS_NODE_ROLE=worker
Environment=ARGUS_WORKER_BIND=100.126.13.70:8273
Environment=ARGUS_EGRESS_SHARED_SECRET=<secret>
Restart=always

[Install]
WantedBy=multi-user.target
```

### 3. `ReachabilityMatrix`

New module: `argus/broker/reachability.py`

Probes `(provider, egress)` pairs and stores results in memory. "Local" is always one of the egress options.

```python
@dataclass
class EgressProbe:
    egress: str           # "local" | "oci-dev" | "macmini"
    reachable: bool
    latency_ms: int
    last_checked: float

class ReachabilityMatrix:
    # provider → list of EgressProbe, sorted by preference
    _matrix: dict[ProviderName, list[EgressProbe]]

    def best_egress(self, provider: ProviderName) -> str | None:
        """Return name of best reachable egress, or None if all blocked."""

    async def probe_all(self) -> None:
        """Probe all (provider × egress) pairs. Called on startup and every 30 min."""
```

**Probe logic for tier-0 providers:** Make a real search call with `max_results=1`. If it succeeds (status="success"), mark reachable. If it 500s or times out, mark blocked. Tier-1/3 providers are never probed with real calls (would spend credits) — they are assumed reachable on all egresses and health-tracked reactively after real usage failures.

**Preference order:** local first (lower latency), then workers sorted by latency_ms ascending. If local is blocked, first reachable worker wins.

**Tier-0 providers are probed first** — they are the `free_only` pool. Tier-1/3 providers are probed lazily (on first use).

**Probe interval:** 30 minutes in background task. Re-probe immediately after N consecutive failures on any egress.

### 4. `RemoteProviderClient`

New module: `argus/broker/remote_provider.py`

Implements `BaseProvider` interface so `ProviderExecutor` treats it identically to a local provider:

```python
class RemoteProviderClient(BaseProvider):
    def __init__(self, provider: ProviderName, node: EgressNode): ...

    async def search(self, query: SearchQuery) -> tuple[list[SearchResult], ProviderTrace]:
        # POST to node.url/exec, deserialize response
        # On network error: return ProviderTrace(status="error", error=...)
```

### 5. `ProviderExecutor` changes

One additional check before local execution:

```python
egress = self._reachability.best_egress(pname)
if egress is None:
    # no egress available at all
    traces.append(ProviderTrace(provider=pname, status="skipped", error="no reachable egress"))
    continue
if egress != "local":
    client = RemoteProviderClient(pname, self._egress_nodes[egress])
    results, trace = await client.search(query)
else:
    results, trace = await self._execute_provider(query, provider, pname)
```

Budget tracking and health tracking happen on the primary regardless of which egress executed the call.

---

## free_only Interaction

`free_only=True` skips providers with tier > 0 — unchanged. With multi-egress, tier-0 providers that are locally blocked (Yahoo on Spectrum) become available via a worker. No code changes to `free_only` are needed: the executor checks reachability before execution, and if a tier-0 provider's best egress is a worker, it routes there transparently.

Net effect: `free_only` searches gain back Yahoo (and potentially WolframAlpha) that were blocked on homelab. The provider pool for free searches grows automatically as workers are added.

---

## Config Migration

`ResidentialConfig.endpoints` → removed (superseded by `EgressNode.url`).  
`ResidentialConfig.policy` → kept (still useful for extraction routing decisions).  
`NodeConfig` → unchanged.

Existing `.env` on homelab only needs two new vars:
```
ARGUS_EGRESS_NODES=oci-dev:http://100.126.13.70:8273
ARGUS_EGRESS_SHARED_SECRET=<new secret, add to vault>
```

---

## What Does NOT Change

- All provider implementations (`yahoo.py`, `duckduckgo.py`, etc.)
- Budget and health tracking logic
- HTTP API, MCP server, dashboard
- `free_only` flag semantics
- `SearchQuery`, `SearchResult`, `ProviderTrace` models
- Test suite structure (worker server gets its own test file)

---

## Error Handling

| Scenario | Behavior |
|----------|----------|
| Worker unreachable | `RemoteProviderClient` returns `status="error"` → health tracker increments failure count → after threshold, worker marked degraded → reachability probe skips it |
| Worker returns provider error | Treated same as local error |
| All egresses blocked for provider | `status="skipped", error="no reachable egress"` |
| Probe fails during startup | Matrix initializes with all-local, probe runs async in background |
| Worker shared secret mismatch | 401 → `status="error"` → health tracker |

---

## Deployment Sequence

1. Add `ARGUS_EGRESS_SHARED_SECRET` to vault
2. Deploy worker systemd unit on oci-dev (and optionally macmini)
3. Add `ARGUS_EGRESS_NODES` + `ARGUS_EGRESS_SHARED_SECRET` to homelab `.env`
4. Restart homelab argus container
5. Verify: `GET /api/admin/health/detail` shows `yahoo: effective_status=enabled, egress=oci-dev`

---

## Testing

- `tests/test_reachability.py` — matrix logic, preference ordering, probe result application
- `tests/test_remote_provider.py` — `RemoteProviderClient` with mocked HTTP (success, error, auth failure, timeout)
- `tests/test_worker.py` — worker `/exec` endpoint (valid request, unknown provider, bad auth)
- `tests/test_executor.py` — extend existing tests: executor routes to remote when local blocked, falls back to skipped when no egress
