# Argus Tooling And Security Hardening Plan

## Goal

Move Argus from a capable local search/extraction tool to a controlled internal service that is safe to run on a Tailscale network by default, with an explicit path to optional internet exposure behind a reverse proxy.

## Locked Decisions

- Primary runtime: Python 3.12
- Supported floor: Python 3.11 may remain supported if verification stays clean
- Default deployment target: Tailscale-only internal service
- Internet exposure: allowed only behind a reverse proxy and only after Argus itself requires authentication
- Scope: repo, tooling, CI, Docker, scripts, docs, API, MCP, and residential extractor
- Breaking changes: allowed when they materially improve safety or operability

## Current State Summary

### Good

- `pyproject.toml` already declares `requires-python = ">=3.11"`
- Docker already uses Python 3.12
- CI already exists and runs tests on 3.11, 3.12, and 3.13
- Extraction path already includes SSRF checks and domain rate limiting

### Gaps

- Local shell here is on Python 3.9, below the declared repo floor
- Remote HTTP surfaces are rate-limited but not actually authenticated
- Privileged routes are not separated from normal caller routes
- Tailscale-facing examples default to broad binds and trust the network too much
- Residential extraction is treated like an internal worker in concept, but exposed like a normal HTTP service
- Tooling verification is test-only today; there is no repo-standard lint/typecheck/dev bootstrap path

## Workstreams

## Phase 1: Tooling Baseline

### Objective

Make local development, verification, and release behavior reproducible on Python 3.12.

### Changes

- Standardize local development docs and commands on Python 3.12
- Add one canonical bootstrap path for contributors
- Add one canonical verification command set
- Decide whether to stay with `venv + pip` or move to a managed workflow such as `uv`
- Ensure tests run cleanly under the supported runtime

### Deliverables

- Updated contributor instructions
- Explicit local setup and verification commands
- Any needed project config updates for Python 3.12 alignment

### Acceptance Criteria

- A fresh Python 3.12 environment can install the repo and run verification without manual guesswork
- Local docs no longer imply Python 3.9 compatibility

## Phase 2: Verification And CI

### Objective

Make correctness and policy drift visible in CI before security changes land.

### Changes

- Audit existing GitHub Actions workflows
- Keep the current test matrix only if it still matches the supported versions after tooling work
- Add lint and typecheck if they are introduced in Phase 1
- Add checks for security-sensitive configuration defaults where practical

### Deliverables

- Updated `.github/workflows/ci.yml`
- Any added config files for lint/typecheck tools

### Acceptance Criteria

- CI enforces the same verification path documented for local development
- The Python version policy is explicit and reflected in both docs and CI

## Phase 3: Service Trust Model

### Objective

Replace the current network trust model with explicit authentication and surface separation.

### Changes

- Require authentication for non-local HTTP access
- Require authentication for remote MCP access
- Convert the current API key behavior from "rate-limit bypass" to actual auth where appropriate
- Split normal caller routes from privileged/admin routes
- Restrict privileged routes such as provider testing, budget visibility, detailed health, and cookie health
- Revisit CORS defaults and reduce them to the minimum required behavior

### Deliverables

- Auth middleware or equivalent enforcement layer
- Clear route classification: public/internal/admin
- Updated tests for auth and route access

### Acceptance Criteria

- An unauthenticated remote caller cannot use Argus over HTTP or MCP
- Privileged endpoints are not reachable through the normal caller surface

## Phase 4: Deployment Hardening

### Objective

Make Tailscale-only operation the safe default and document a separate internet-facing mode.

### Changes

- Change default bind guidance to prefer loopback unless explicitly configured otherwise
- Review Docker, `docker-compose.yml`, and service scripts for insecure defaults
- Make Tailscale-only deployment the primary documented mode
- Define an internet-behind-proxy deployment mode with TLS termination and upstream auth expectations
- Tighten residential worker deployment assumptions

### Deliverables

- Updated Docker and service defaults as needed
- Updated deployment scripts
- Updated deployment docs with separate modes

### Acceptance Criteria

- Default setup does not accidentally expose privileged surfaces beyond intended peers
- Internet exposure is documented as an advanced mode with clear prerequisites

## Phase 5: Residential Extractor Hardening

### Objective

Treat residential extraction as a privileged internal worker rather than a general network service.

### Changes

- Require service-to-service authentication between Argus and residential endpoints
- Narrow which callers can reach the residential worker
- Reassess cookie forwarding and authenticated browser use across that boundary
- Tighten health and extract endpoints to fit the internal-worker model

### Deliverables

- Hardened residential worker auth model
- Updated endpoint handling and tests
- Deployment guidance for residential workers on Tailscale

### Acceptance Criteria

- The residential worker cannot be used as an open unauthenticated fetcher
- Cookie-backed extraction is only available through authenticated internal flows

## Phase 6: Documentation And Handoff Quality

### Objective

Make the safe operating model obvious to future maintainers and clients.

### Changes

- Update `README.md`, `SECURITY.md`, `.env.example`, and contributor docs
- Document the runtime policy, verification commands, and deployment modes
- Update client configuration examples if auth or MCP transport expectations change

### Deliverables

- Revised docs and examples
- Clean handoff notes for future sessions

### Acceptance Criteria

- The docs teach the secure path by default
- A fresh maintainer can understand the intended trust boundaries without reverse-engineering the code

## Recommended Execution Order

1. Phase 1: Tooling Baseline
2. Phase 2: Verification And CI
3. Phase 3: Service Trust Model
4. Phase 4: Deployment Hardening
5. Phase 5: Residential Extractor Hardening
6. Phase 6: Documentation And Handoff Quality

## Non-Goals For The First Pass

- Full zero-trust redesign across every internal dependency
- Perfect SSRF resistance against every browser/runtime edge case
- Enterprise IAM integration
- Major feature expansion unrelated to service safety or tooling reproducibility

## Definition Of Done

Argus is done with this program of work when:

- Python 3.12 is the clearly documented and verified primary runtime
- Local and CI verification are aligned
- Remote HTTP and MCP use require explicit authentication
- Privileged routes are separated and protected
- Tailscale-only deployment is the default documented operating mode
- Residential extraction is treated and deployed as an internal worker
- Internet exposure is possible only through a documented hardened pattern
