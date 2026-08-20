# Argus Improvement Map Design

## Purpose

Turn the existing visual overview from a concept poster into a practical,
operator-first map. The page must help someone diagnose the class of problem
they are seeing, locate the authoritative evidence, and choose the smallest
safe next action. It is the working surface for the later Argus improvement
plan; it is not itself a live dashboard or a second operations runbook.

## Artifact

- Update the existing `docs/argus-visual-overview.html`; do not create a
  parallel visual page.
- Keep it a single, self-contained editorial HTML document with inline CSS and
  inline diagrams. Google Fonts may remain its only external dependency.
- Retain the README discovery link.
- Source its claims from `README.md`, `CONTEXT.md`, `docs/operations.md`,
  `docs/operations-status.md`, and `docs/mcp-clients.md`.
- Use a denser, calm operations-notebook treatment: short evidence labels,
  generous hierarchy, and expandable-looking cards without JavaScript.

## Content Model

1. **Operator thesis** — Argus is a private retrieval authority for AI agents.
   State the page's job plainly: turn an observed symptom into a source of
   truth, a bounded next action, and a verification gate.
2. **Access and evidence chain** — Show caller surfaces (native MCP, HTTP,
   CLI) flowing through private ingress to the authenticated authority, then
   broker, persistence, providers, and extraction. Distinguish caller
   configuration from authority execution so the map makes clear why an MCP
   client does not own a local broker, database, or browser.
3. **Status semantics** — Explain `healthy`, `degraded`, `unready`, `unknown`,
   and `disabled`, and the practical difference between process liveness,
   dependency readiness, and full operator status. Make explicit that a
   configured provider is not necessarily reachable or healthy.
4. **Symptom-to-action guide** — A compact decision tree for common practical
   failures:
   - cannot connect or receive a certificate/transport error;
   - receives `401` from MCP;
   - receives a liveness success but retrieval is impaired;
   - a provider or extraction path fails;
   - ChatGPT cannot reach the private MCP endpoint;
   - a development test is red.

   Each branch gives the meaning, safe read-only probe category, responsible
   subsystem, and canonical document. It must not print credentials, private
   endpoints, raw IPs, or stale command recipes.
5. **Improvement board** — Present the durable work areas as actionable cards:
   client onboarding and acceptance, status/observability, provider
   reliability and spend control, documentation/navigation, development
   baseline health, and external-boundary clarity. Every card carries the
   user-visible pain, evidence to collect, safe next action, verification
   gate, and a deliberately honest planning state (`ready to plan`, `needs
   evidence`, or `human authorization required`).
6. **Hard boundaries** — Make the difference visible between the ChatGPT
   private-MCP tunnel's human authorization gate and a retired/external host
   that is not a production fallback. Neither becomes a hidden recovery path.
7. **Sources of truth** — Link to README, Context, MCP client guide,
   Operations, and Operations Status. Replace the old primary deployment link
   to the historical ADR with `docs/operations.md`; retain the ADR only as a
   clearly labelled historical reference, if included.

## Evidence Rules

- Use labels such as **documented contract**, **needs a live probe**, and
  **human-gated** rather than representing a static document as current
  telemetry.
- The map may describe stable, documented status meanings and acceptance
  boundaries. It must not assert a current provider, database, tunnel, or
  deployment condition without a generated private snapshot.
- A future private live snapshot can enrich the map, but is explicitly outside
  this change.

## Boundaries

- No JavaScript, build tooling, generated images, separate assets, secrets,
  hostname/IP values, bearer-token shapes, or live operational claims.
- No new protocol, runtime, deployment, provider, or client behavior.
- Do not edit the root checkout's unrelated research files; work only in the
  established repair worktree.

## Verification

1. Parse the HTML and run `git diff --check`.
2. Confirm every reference is a clickable relative repository link and that
   `docs/operations.md`, rather than the historical ADR, is the primary
   deployment/operations source.
3. Scan the page for environment-variable assignments, token-shaped text,
   private hostnames, raw IP addresses, and unsupported live-status claims.
4. Open the page locally and inspect both desktop and narrow viewport behavior
   with the available renderer; if visual automation is unavailable, record
   that limitation precisely rather than claiming visual proof.
