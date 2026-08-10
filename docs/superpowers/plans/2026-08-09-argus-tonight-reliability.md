# Argus Tonight Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task by task.
> Use `superpowers:test-driven-development` for every behavior change and
> `superpowers:verification-before-completion` before any completion claim.

**Goal:** Make the canonical Argus production authority reliably usable from
Mac agent clients, restore guarded Maya delivery, and expose the existing
research-pack workflow as a safe remotely readable evidence artifact that
passes the frozen acceptance contract.

**Architecture:** The Homelab HTTP authority remains the sole execution and
persistence owner. MCP remains a stateless authenticated HTTP adapter. The
existing workflow engine gains a safe run projection and bounded artifact read;
the calling agent performs final report synthesis. Production enables accepted
evidence, scoped caller identity, tier caps, and the existing Maya outbox using
dedicated secrets.

**Tech stack:** Python 3.12, FastAPI, Pydantic, MCP Streamable HTTP, PostgreSQL,
Docker Compose, SOPS/Age, Tailscale Serve, pytest, Ruff, GitHub Actions/GHCR.

**Frozen design:**
`docs/superpowers/specs/2026-08-09-argus-tonight-reliability-design.md`

## Global constraints

- Add no provider, extractor, workflow engine, UI, or database migration.
- Do not invoke one-time-credit providers or purchase/change vendor accounts.
- Never print, commit, or place a literal secret in client configuration.
- Preserve all unrelated dirty worktree changes. Stage explicit paths only.
- Do not edit/recreate/delete the existing Maya delivery intents.
- Deploy only an immutable digest through the existing promotion boundary.
- Keep the previous known-good digest and config snapshot available for
  rollback.
- A failed hard gate stops the release even if unit tests pass.

---

### Task 1: Correct terminal workflow serialization

**Files:**

- Modify: `argus/workflows/service.py`
- Test: `tests/test_workflows.py`

**Contract:** A successful workflow writes `completed` plus `finished_at` into
the report, manifest, and run state. A failed run remains terminal and is not
finalized as success.

- [ ] Add a regression test that executes a small research-pack fixture and
      asserts `WorkflowResult`, `SUMMARY.md`, and `manifest.json` all say
      `completed` and have a terminal timestamp.
- [ ] Run
      `ARGUS_AUTOLOAD_DOTENV=false ARGUS_DISABLE_SECRET_RESOLUTION=true .venv/bin/python -m pytest -q tests/test_workflows.py -k 'terminal or research_pack'`
      and confirm the test fails because finalization occurs while running.
- [ ] Move successful terminal-state assignment into the handler/finalization
      boundary without serializing the run twice or swallowing exceptions.
- [ ] Run all `tests/test_workflows.py` and confirm green.
- [ ] Commit only the workflow implementation and test.

### Task 2: Add safe status and bounded artifact HTTP contracts

**Files:**

- Modify: `argus/workflows/service.py`
- Modify: `argus/api/schemas.py`
- Modify: `argus/api/routes_workflows.py`
- Test: `tests/test_research_report_workflow.py`
- Test: `tests/test_http_authority.py`

**Interfaces:**

- `GET /api/workflows/{run_id}/status`
- `GET /api/workflows/{run_id}/artifacts/{report|manifest}` with `offset` and
  `max_bytes`

- [ ] Write tests for authenticated safe status, unknown run, pending artifact
      conflict, report/manifest reads, byte bounds, pagination metadata, stable
      SHA-256, invalid artifact, containment failure, and absence of local
      filesystem paths.
- [ ] Write a test proving the credential-derived caller identity remains in
      workflow metadata while a request-body caller label cannot change policy
      identity.
- [ ] Run focused tests and confirm RED because the routes/projection do not
      exist.
- [ ] Add dedicated public Pydantic projections. Do not reuse the legacy
      path-oriented `WorkflowRunResponse`.
- [ ] Add service helpers that locate only artifacts registered on the run,
      resolve under the run snapshot, read at most 256 KiB, compute SHA-256 over
      the full artifact, and return a UTF-8 bounded slice.
- [ ] Add thin authenticated routes with stable 404/409/500 mappings.
- [ ] Populate source/domain/disposition and cost-state metadata from existing
      run/evidence data; use `unavailable` when no durable cost proof exists.
- [ ] Run the focused tests, API tests, schema/OpenAPI tests, and architecture
      checks until green.
- [ ] Commit explicit implementation and test paths.

### Task 3: Expose workflow status and artifacts through production MCP

**Files:**

- Modify: `argus/mcp/http_adapter.py`
- Modify: `argus/mcp/server.py`
- Modify: `argus/capabilities.py` if the registered tool manifest requires it
- Test: `tests/test_mcp_research_report.py`
- Test: `tests/test_http_authority.py`
- Test: `tests/test_capability_negotiation.py`

**Interfaces:**

- `get_workflow_status(run_id)`
- `read_workflow_artifact(run_id, artifact="report", offset=0,
  max_bytes=65536)`

- [ ] Add HTTP-adapter and MCP registration tests proving both tools forward the
      incoming scoped token, call only the canonical HTTP authority, render
      useful content, and expose no local path.
- [ ] Confirm RED for missing methods/tools.
- [ ] Implement the two thin methods and tool registrations. Reuse the
      authority client; do not open authority-local files from MCP.
- [ ] Preserve typed errors and bounded read metadata in both JSON and Markdown
      response formats.
- [ ] Run focused MCP, transport, capability, and authority tests until green.
- [ ] Commit explicit paths.

### Task 4: Make research evidence and release identity truthful

**Files:**

- Modify: `argus/workflows/service.py`
- Modify: `argus/api/routes_workflows.py`
- Modify: `argus/api/schemas.py`
- Modify: `pyproject.toml`
- Modify: `server.json`
- Modify: `argus/__init__.py`
- Modify: `argus/api/main.py`
- Modify: `CHANGELOG.md`
- Test: `tests/test_research_report_workflow.py`
- Test: `tests/test_runtime_manifest.py`
- Test: `tests/test_release_workflow.py`

**Contract:** A research pack reports source/domain floors, partial/degraded
artifacts, cost uncertainty, and full runtime identity. The package/server/app
version is `1.6.3` everywhere.

- [ ] Add tests for at least these projections: unique source count, domain
      count, primary-source count, partial/degraded reasons, `cost_state`, full
      source revision, and image/deployment identity when supplied by the
      runtime manifest.
- [ ] Confirm RED where metadata/version is absent or still `1.6.2`.
- [ ] Derive metrics from existing documents/citations and runtime manifest.
      Do not infer confirmed spend from zero-valued legacy fields.
- [ ] Bump the four user-visible version locations together and add a concise
      changelog entry. Do not rewrite historical fixture attestations that
      intentionally contain `argus-1.6.2`.
- [ ] Run the focused workflow/runtime/release tests until green.
- [ ] Commit explicit paths.

### Task 5: Complete Argus repository verification and review

**Files:** No new production behavior.

- [ ] Run `git diff --check`.
- [ ] Run Ruff check/format in check mode using the repository's documented
      commands.
- [ ] Run focused workflow, HTTP authority, MCP, caller-cap, spend-boundary,
      runtime-manifest, release, and architecture suites.
- [ ] Run the full hermetic suite with dotenv/secret resolution disabled and
      record the final exit code and counts.
- [ ] Run a spec and standards code review against the frozen design. Resolve
      every high/medium correctness or security finding with another red/green
      cycle.
- [ ] Push the branch and open a reviewable PR; merge only after required checks
      pass.

### Task 6: Repair Homelab configuration generation in isolation

**Repository:** `/Volumes/2TB_SSD/GitHub/homelab`

**Files (exact names confirmed after isolated checkout):**

- Modify: `services/argus/docker-compose.yml`
- Modify: `scripts/gen-env.sh`
- Modify: `scripts/render-maya-runtime-env.sh`
- Modify: `scripts/setup-argus-mcp.sh`
- Modify: `ansible/roles/dotfiles/files/claude/settings_mcp_fragment.json`
- Modify: `ansible/roles/dotfiles/files/gemini/settings_mcp_fragment.json`
- Modify: `ansible/roles/dotfiles/files/opencode/settings_mcp_fragment.json`
- Test: add/extend the repository's Argus compose and secret-projection
  contract tests

**Runtime settings:** evidence authority, retrieval session secret, scoped
credentials, caller tier caps, You Contents disabled, Maya capture URL/token,
and a configurable Maya outbox batch size.

- [ ] Read Homelab `AGENTS.md`, create a clean worktree from `origin/main`, and
      record the live checkout's four dirty tracked files. Never reset/stash or
      copy a whole clean file over the live dirty compose/vault.
- [ ] Add failing contract tests for exact Argus environment names, evidence
      authority/caps, disabled You Contents, dedicated Maya URL/token, and
      generator projections.
- [ ] Add failing tests for canonical MCP HTTPS `/mcp`, environment references
      instead of literal bearer values, and the installed OpenCode config
      shape.
- [ ] Implement the smallest generator/compose/template changes. Preserve the
      existing manual LAN bind and allowed-host delta when integrating.
- [ ] Run focused and full Homelab tests plus shell/YAML/Compose validation.
- [ ] Commit, review, push, and merge the Homelab branch. Apply the merged
      changes to the live dirty checkout only through a targeted, diff-checked
      integration that preserves all unrelated modifications.

### Task 7: Provision dedicated secrets without disclosure

**Protected state only:** Homelab SOPS vault/rendered `.env`, Mac protected Maya
runtime secret, and agent runtime environment.

- [ ] Capture names-only and SHA-256-prefix snapshots for existing Argus
      caller/admin values; never capture values in output.
- [ ] Generate three independent high-entropy values: scoped `mac-agents`
      credential, retrieval-session secret, and Maya capture token.
- [ ] Store them through the canonical SOPS/helper path and render mode-0600
      runtime files. Do not put values on a command line that will enter shell
      history or logs.
- [ ] Update Maya's canonical launch environment with
      `MAYA_ARGUS_CAPTURE_TOKEN`; update Argus with the matching
      `ARGUS_MAYA_CAPTURE_TOKEN` plus the independent caller/session values.
- [ ] Hash-compare the Maya token on both sides without printing it. Verify the
      three new values are mutually distinct and distinct from admin/ingest
      tokens.
- [ ] Prepare revocation of the old exposed Argus caller token, but retain it
      until all supported clients prove the new credential.

### Task 8: Activate Maya delivery in the safe order

**Live systems:** Maya launchd on Mac mini, Argus Docker on Homelab.

- [ ] Snapshot Maya process SHA/health, capture/page counts, Argus pending and
      dead-letter counts, and current image/config hashes.
- [ ] Restart Maya only. Confirm `/healthz`, confirm unauthenticated retrieval
      capture now rejects as configured rather than `503`, and run one approved
      authenticated synthetic request plus exact replay to prove `201` then
      idempotent `200` without duplicating rows.
- [ ] Configure Argus outbox batch size `1`; recreate only API/MCP services at
      the current known-good digest if the new Argus image is not yet promoted.
- [ ] Watch one real pending intent become delivered, a valid receipt persist,
      and the matching Maya capture appear. Stop on any 401, 409, invalid
      receipt, unexpected retry, or dead letter.
- [ ] Restore the normal bounded batch size, restart only Argus API/MCP, and let
      the queue drain while monitoring counts and logs.
- [ ] Record final delivered/pending/dead-letter/capture/page counts and explain
      any residual. Do not mark this task complete while delivery is merely
      attempted rather than durably received.

### Task 9: Build and promote the immutable Argus release

**Authority:** GitHub `docker-publish.yml` and the protected Homelab promoter.

- [ ] Record current and known-good receipt identity plus prior digest; do not
      expose receipt secrets or delete state.
- [ ] Trigger/observe the image workflow for the exact merged Argus revision.
- [ ] Verify the candidate image runtime manifest, package version, lock hash,
      schema head, browser capability, and same digest for API/MCP.
- [ ] Run the existing identity/readiness, free GitHub search, MCP initialize +
      tools/list, and durable accounting gates. No paid provider is permitted.
- [ ] Promote through `argus-deploy promote`, preserving the existing LAN bind
      and Tailscale Serve routes. Allow the required soak to finish.
- [ ] Verify current/known-good/cutover/rollback receipt state, exact running
      image/revision, restart counts, and logs. If a gate fails, let the promoter
      roll back and record the failure rather than forcing known-good.

### Task 10: Cut over Mac agent clients and revoke the exposed token

**Files/state:**

- Modify: `~/.codex/config.toml`
- Modify: `~/.claude/settings.json`
- Modify: `~/.claude.json` if it is an active Claude MCP source
- Modify: `~/.config/opencode/config.json`
- Quarantine or repair narrowly: `~/.opencode/opencode.json`
- Disable stale Argus only: `~/.gemini/settings.json`

- [ ] Create mode-preserving timestamped backups of each target and record
      SHA-256 hashes. Do not copy secret values into backup filenames or output.
- [ ] Replace only each Argus MCP entry with canonical HTTPS `/mcp` and the
      client-specific environment reference. Remove literal Authorization
      headers. Preserve every unrelated MCP server and setting.
- [ ] Repair/quarantine only the invalid legacy OpenCode file while preserving
      its unrelated Janus configuration in a supported location.
- [ ] Leave Gemini's Argus entry disabled unless installed-client testing proves
      environment expansion in remote headers without persisting the value.
- [ ] Ensure each launched CLI has `ARGUS_API_KEY` from the protected runtime
      source. Run Codex, Claude Code, and OpenCode MCP list/initialize/tools-list
      through canonical HTTPS and confirm the server observes `mac-agents`.
- [ ] Search supported config files for retired Argus URLs and literal bearer
      material using redacted/value-free checks; expected count is zero.
- [ ] Revoke the old exposed Argus caller token, recreate only Argus API/MCP,
      prove old authentication fails, and rerun all three clients with the new
      token.

### Task 11: Run the frozen acceptance contract and research benchmark

**Artifacts:**

- Create: `docs/research/2026-08-09-argus-tonight-acceptance.md`
- Create: `docs/research/2026-08-09-argus-managed-stack-evaluation.md`

- [ ] Snapshot provider-spend rows, unresolved charges, outbox counts, runtime
      identity, and client-route checks before the benchmark.
- [ ] Through MCP as `mac-agents`, start the frozen research-pack benchmark from
      the design. Poll via `get_workflow_status` for at most ten minutes and read
      both artifacts through `read_workflow_artifact`.
- [ ] Run the fixed research prompt against only the returned evidence pack.
      Validate every material citation resolves to its manifest entry and URL.
- [ ] Compute the predetermined 100-point score and every hard gate. Do not
      reinterpret the rubric after observing the result.
- [ ] Compare post-run provider-spend/unresolved/outbox state with the snapshot;
      record actual cost state and caller identity.
- [ ] Write a literal PASS or FAIL, exact failing gates, exact image/revision,
      timestamps, source/domain counts, latency, score, spend delta, delivery
      delta, and rollback target in the acceptance artifact.
- [ ] Commit the two reports on a documentation-only branch/PR if live evidence
      changes after the code merge. Do not claim Argus useful or unnecessary
      from a failed activation test.

### Task 12: Completion and 30-day handoff

- [ ] Re-run canonical API/MCP synthetics and confirm no unexpected 5xx, 421,
      or authentication loop in the post-benchmark window.
- [ ] Confirm Maya outbox age/count is healthy or document a bounded residual
      with the worker actively delivering.
- [ ] Confirm repositories are clean except for preserved pre-existing changes
      and link exact commits/PRs/deployment receipts/reports.
- [ ] Record the 30-day measurement set: named operations by caller, report
      completions, evidence utility/source diversity, hard-page wins, latency,
      provider skip reasons, spend, delivery receipts, and actual consumption of
      execution-provenance fields.
- [ ] Mark the goal complete only when every hard gate passes and the frozen
      score is at least 85. Otherwise report the literal failed gate and leave
      the previous known-good runtime available.

