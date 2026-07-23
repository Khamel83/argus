# OCI dependency and degraded-mode research

**Date:** 2026-07-22

**Wayfinder ticket:** [#25 — Reconstruct the OCI dependency and choose Argus degraded mode](https://github.com/Khamel83/argus/issues/25)

**Scope:** Planning evidence only. No paid-provider probes or production changes were made.

## Decision

Production Argus should have **no OCI dependency**. OCI is retired from the
Argus search, extraction, deployment, readiness, and recovery paths. A future
remote-egress design would require a new explicit decision and must not assume
OCI as its host.

The no-OCI production contract has these semantics:

- Argus remains ready when its local homelab API, MCP transport, persistence,
  SearXNG, and minimum local search/extraction capability are available.
- The deployment path must not transit OCI. Publishing an image and promoting
  it on the homelab need an OCI-independent route with explicit verification
  and rollback.
- Direct Yahoo search is opportunistic. If it is blocked from homelab, Argus
  uses SearXNG and the remaining local/free or configured recurring providers;
  it does not restore OCI to recover direct Yahoo access.

This policy matches the original design's “one primary, optional workers”
boundary and its stated goal of state-driven routing
([design lines 9–15](../../superpowers/specs/2026-05-22-multi-egress-worker-design.md#L9-L15)).
It also matches the production state observed below: the homelab currently runs
without any configured Argus egress node.

## What OCI was intended to do

### 1. Recover direct Yahoo search

The multi-egress design was created because direct Yahoo requests from the
homelab's Spectrum address returned an `INKApi` failure while the Oracle address
succeeded. OCI was therefore selected as a worker for Yahoo, not as the Argus
primary
([design lines 3–5](../../superpowers/specs/2026-05-22-multi-egress-worker-design.md#L3-L5),
[architecture lines 19–34](../../superpowers/specs/2026-05-22-multi-egress-worker-design.md#L19-L34)).

The intended deployment was:

1. install an `argus worker` systemd unit on OCI at port 8273;
2. add OCI to `ARGUS_EGRESS_NODES` on the homelab;
3. verify direct Yahoo results were routed through OCI
   ([plan lines 1906–1948](../../superpowers/plans/2026-05-22-multi-egress-worker.md#L1906-L1948),
   [plan lines 1956–1986](../../superpowers/plans/2026-05-22-multi-egress-worker.md#L1956-L1986)).

The worker implementation exists. It exposes `/health` and `/exec`, and can
instantiate Yahoo and a bounded set of other providers
([worker lines 1–8](../../../argus/worker/server.py#L1-L8),
[worker lines 31–64](../../../argus/worker/server.py#L31-L64),
[worker lines 76–125](../../../argus/worker/server.py#L76-L125)).

### 2. Act as the public deployment jump host

The current GitHub Actions deploy job connects to `DEPLOY_HOST` and then SSHes
from that host to `homelab-ts`. The step is explicitly named “via oci-dev jump”
([workflow lines 53–73](../../../.github/workflows/docker-publish.yml#L53-L73)).
This makes the deployment control path dependent on that jump host, even though
the running Argus service does not need it.

### 3. It was not the durable primary or extraction host

The approved multi-egress model assigns the full broker, API, MCP server,
dashboard, budgets, and health tracking to the homelab primary. Workers execute
individual provider calls without a database or broker
([design lines 19–34](../../superpowers/specs/2026-05-22-multi-egress-worker-design.md#L19-L34),
[design lines 65–82](../../superpowers/specs/2026-05-22-multi-egress-worker-design.md#L65-L82)).

On a node declared `residential`, the extraction chain deliberately skips a
remote residential hop and runs its local extraction fallbacks before external
APIs
([extractor lines 271–308](../../../argus/extraction/extractor.py#L271-L308),
[extractor lines 338–385](../../../argus/extraction/extractor.py#L338-L385)).
The homelab is currently declared residential, so OCI is not needed for its
normal extraction path.

## Current verified state

Read-only inspection on 2026-07-22 established:

| Surface | Observation | Primary evidence |
|---|---|---|
| Homelab runtime | `argus`, `argus-mcp`, and `searxng` were running. Argus was healthy on `:8270`; MCP was healthy on `:8271`. | `ssh homelab-ts`; `docker ps` and `docker inspect` |
| Homelab role | Both Argus containers declare `ARGUS_NODE_ROLE=primary`, `ARGUS_EGRESS_TYPE=residential`, and `ARGUS_MACHINE_NAME=homelab`. | `docker inspect argus argus-mcp` |
| Egress configuration | Neither running container has `ARGUS_EGRESS_NODES`. The checked-in homelab compose service also does not declare it. | `docker inspect`; `/mnt/fast-storage/github/homelab/services/argus/docker-compose.yml` at homelab commit `56fafc3cb11ffe9a2edf6999268479614b1ddc19` |
| OCI availability | Tailscale reported `oci-dev` online at `100.126.13.70`. SSH identified the machine as `instance-first`, booted since 2026-06-28. | `tailscale status --json` on homelab; read-only SSH through homelab |
| OCI Argus worker | `argus-worker.service` was not installed and port 8273 was not listening. The OCI Argus checkout was at `0c2839e8314f58e9fce2771b035276da7b0eb2a8`. | `systemctl is-enabled/is-active argus-worker`, `ss -ltn`, and `git rev-parse` on OCI |
| Deployment | The latest Actions run built the image and successfully completed the OCI-labelled jump-host deployment; the homelab container creation time matches that deployment window. | [GitHub Actions run 29963269029](https://github.com/Khamel83/argus/actions/runs/29963269029); `docker inspect argus` |
| Workflow failures | No non-success conclusion appeared in the most recent 100 runs of `docker-publish.yml`. | GitHub Actions first-party API via `gh run list` |

The important correction is that OCI is **currently online**, but it is **not
currently an Argus egress worker**. The live service is already operating
without an OCI runtime dependency. The evidence does not establish an active
OCI-shutdown failure in search or extraction, and the recent workflow history
does not contain a failed deploy to attribute to OCI loss.

The currently observed 11 restarts of the `argus` container therefore cannot be
attributed to OCI from this evidence. They align with the separately tracked
container/browser failure investigation in issue #22, not with a configured
remote egress.

## What actually fails if OCI disappears

### Today

- The running search and extraction service continues because no
  `ARGUS_EGRESS_NODES` entry points at OCI.
- Direct Yahoo may still be unavailable from the homelab due to the originally
  observed Spectrum block. Argus retains SearXNG, DuckDuckGo, GitHub,
  WolframAlpha when configured, and the configured API providers. The tier
  definitions show OCI was never a provider category of its own
  ([budget tiers lines 23–41](../../../argus/broker/budgets.py#L23-L41)).
- Automated deployment from GitHub becomes unavailable if the secret
  `DEPLOY_HOST` is in fact this OCI instance, because the workflow has no
  alternate path. The current workflow name and command strongly indicate that
  it is, but secret values were intentionally not inspected.

### Why OCI must not be reintroduced without a new design decision

The present code does not yet provide reliable same-request degradation:

- Reachability is in-memory and optimistic before a probe. It prefers local,
  then the lowest-latency reachable worker
  ([reachability lines 23–70](../../../argus/broker/reachability.py#L23-L70)).
- Tier-0 probes run serially for local and every configured worker. Results are
  refreshed by a background loop every 30 minutes
  ([reachability lines 90–126](../../../argus/broker/reachability.py#L90-L126),
  [API lifespan lines 83–104](../../../argus/api/main.py#L83-L104)).
- A remote request has a fixed 30-second client timeout and turns network,
  authentication, or HTTP failures into an error trace
  ([remote client lines 39–72](../../../argus/broker/remote_provider.py#L39-L72)).
- After a chosen worker returns an error, the executor records a failure against
  the provider and immediately continues to the next provider. It does not try
  local or another worker for that same provider
  ([executor lines 141–168](../../../argus/broker/execution.py#L141-L168)).
- The basic `/api/health` endpoint is `ok` when any provider appears enabled,
  while the detailed endpoint always returns `"status": "ok"`. Neither endpoint
  expresses required versus optional capabilities
  ([health routes lines 15–32](../../../argus/api/routes_health.py#L15-L32),
  [health routes lines 35–60](../../../argus/api/routes_health.py#L35-L60)).

Those behaviors can cause a 30-second delay, a lost provider attempt, and
provider-wide cooldown pressure after a worker disappears. They are not
sufficient justification for retaining or restoring OCI.

## Configuration drift discovered

The homelab compose file still declares `ARGUS_RESIDENTIAL_ENDPOINTS`, but
current Argus considers residential extraction configured only when
`config.egress_nodes` is non-empty
([residential client lines 52–54](../../../argus/extraction/residential_extractor.py#L52-L54)).
Because the homelab identifies itself as residential, the extraction chain
skips the remote residential step anyway. The stale variable is therefore not
the current extraction path, but it should be removed or migrated so future
operators are not misled.

There is also a modeling hazard if a search-only OCI worker is configured:
`ARGUS_EGRESS_NODES` feeds both `/exec` search delegation and `/extract`
residential extraction, while the minimal worker app only exposes `/exec` and
`/health`
([remote client lines 53–60](../../../argus/broker/remote_provider.py#L53-L60),
[residential client lines 124–146](../../../argus/extraction/residential_extractor.py#L124-L146),
[worker lines 76–87](../../../argus/worker/server.py#L76-L87)).
Search-egress nodes and extraction workers need separate declared
capabilities—or separate configuration types—before OCI is enabled.

Finally, the worker currently permits unauthenticated requests when no egress
secret is configured
([worker lines 67–73](../../../argus/worker/server.py#L67-L73)).
An optional production worker must fail closed when its shared secret is
missing.

## Required follow-up design constraints

1. **Readiness contract:** define a minimum local capability set and return
   structured `ready`, `degraded`, or `unready` state. Optional worker loss is
   `degraded`; local API/persistence/core-provider loss is `unready`.
2. **Egress-scoped state:** store health, cooldown, last probe, and failure
   reason per `(provider, egress)`, with an expiry on probe observations.
3. **Same-request fallback:** when a worker fails, try the next eligible egress
   within a bounded total deadline. Do not penalize the provider globally for a
   transport-only worker failure.
4. **Capability declaration:** distinguish search execution from extraction
   capability. Do not infer `/extract` support from a search worker URL.
5. **Fail-closed worker auth:** refuse production startup or all requests when
   the worker secret is absent; retain open mode only behind an explicit
   development setting.
6. **OCI-independent delivery:** replace the jump-host deployment with a
   homelab-owned pull/promotion path or another independently available
   authenticated control path. Require post-deploy runtime identity, health,
   and rollback evidence.
7. **Yahoo policy:** classify direct Yahoo as opportunistic. SearXNG and the
   remaining local/free pool are the supported no-OCI baseline; loss of direct
   Yahoo does not authorize restoring OCI.

## Conclusion

OCI was useful for a narrow network asymmetry and became a convenient
deployment bridge, but neither role belongs in Argus production. The egress
worker was planned and implemented in code but was not deployed in the
currently inspected environment. The deployment jump is real and must be
removed as a single point of failure. Argus runs on homelab without OCI; Mac
mini remains development-only.
