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
