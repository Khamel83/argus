# MCP Stateless Compatibility and Clio Retirement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Document the MCP 2026-07-28 target without overstating support and remove retired Clio from active Argus/Homelab policy surfaces.

**Architecture:** Keep the authenticated HTTP authority as the owner of policy, budgets, sessions, and durable state. Treat the MCP adapter as a transport-versioned edge: current production remains the tested 2025 compatibility path, while stateless 2026 behavior is a future migration gate. The caller-cap generator filters retired patterns before writing runtime configuration.

**Tech Stack:** Markdown, Python tests, Bash/Python environment generator, Git, pytest, remote Docker Compose verification.

## Global Constraints

- Do not claim MCP `2026-07-28` support until direct no-spend, restart/multi-instance, header-routing, and deterministic-catalog probes pass.
- Do not stage unrelated research notes or historical plan files.
- Do not rotate secrets or change the deployed Argus image.
- Preserve historical Clio references in dated ADRs/plans; remove only active policy/config/current docs.

---

### Task 1: Record the compatibility boundary and update current Argus docs

**Files:**
- Create: `docs/research/2026-08-11-mcp-stateless-production-authority.md`
- Modify: `README.md`
- Modify: `docs/mcp-clients.md`
- Modify: `CONTEXT.md`
- Modify: `deploy/README.md`

**Interfaces:**
- Consumes: official MCP release and Simon Willison article.
- Produces: direct source links, explicit 2025-current/2026-target wording, and active caller examples with no Clio.

- [ ] **Step 1: Add source-backed research note**

Include the official stateless-core changes, operational no-affinity implications, explicit handles, headers, cache hints, auth hardening, Tasks/MRTR, deprecations, and Argus adoption gates. State that the deployed endpoint remains on the verified 2025-compatible contract.

- [ ] **Step 2: Update current client/deployment docs**

Make the canonical remote HTTPS endpoint and scoped authority boundary first-class. Label local stdio as explicit standalone/development use. Link the research note and avoid wording that implies 2026 support.

- [ ] **Step 3: Replace active Clio examples**

Use `hermes`, `maya`, `mac-agents`, or `interactive-cli` in active examples and caller-cap explanations. Leave dated ADRs/plans untouched.

- [ ] **Step 4: Verify documentation scope**

Run:

```bash
git diff --check
rg -n "clio|2026-07-28|2025-11-25|stateless" README.md docs/mcp-clients.md CONTEXT.md deploy/README.md docs/research/2026-08-11-mcp-stateless-production-authority.md
```

Expected: no `clio` in active files; both protocol versions are clearly scoped.

- [ ] **Step 5: Commit docs**

```bash
git add README.md CONTEXT.md deploy/README.md docs/mcp-clients.md docs/research/2026-08-11-mcp-stateless-production-authority.md
git commit -m "docs: record stateless MCP compatibility boundary"
```

### Task 2: Remove Clio from Argus active policy examples and tests

**Files:**
- Modify: `argus/config.py`
- Modify: `argus/api/schemas.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_caller_caps.py`

**Interfaces:**
- Consumes: existing caller-cap parser and attribution models.
- Produces: unchanged parser behavior with active caller examples only.

- [ ] **Step 1: Change test fixtures first**

Replace Clio-specific fixture patterns with `batch*` or `automation*`, preserving the same fnmatch and restrictive-cap assertions.

- [ ] **Step 2: Update comments/descriptions**

Remove retired caller names from parser comments and API field descriptions without changing serialized schemas.

- [ ] **Step 3: Run targeted tests**

```bash
ARGUS_AUTOLOAD_DOTENV=false ARGUS_DISABLE_SECRET_RESOLUTION=true uv run --no-sync pytest -q tests/test_config.py tests/test_caller_caps.py tests/test_attribution.py
```

Expected: all targeted tests pass.

- [ ] **Step 4: Commit active Argus policy cleanup**

```bash
git add argus/config.py argus/api/schemas.py tests/test_config.py tests/test_caller_caps.py
git commit -m "chore: remove retired caller examples"
```

### Task 3: Filter retired Clio in the Homelab environment generator

**Files:**
- Modify: `/Volumes/2TB_SSD/GitHub/homelab/.worktrees/argus-v3-authority-policy-20260810/scripts/gen-env.sh`
- Modify: `/Volumes/2TB_SSD/GitHub/homelab/.worktrees/argus-v3-authority-policy-20260810/tests/test_argus_tonight_config_contract.py`

**Interfaces:**
- Consumes: protected `ARGUS_CALLER_TIER_CAPS` input.
- Produces: generated caps with no case-insensitive `clio` pattern and required `mac-agents`/`maya` entries.

- [ ] **Step 1: Add a failing generator test**

Set `CUSTOM_CAPS=clio*:1,hermes*:1,mac-agents:1,maya:1` and assert generated `ARGUS_CALLER_TIER_CAPS` excludes `clio*` while preserving active caps.

- [ ] **Step 2: Filter retired patterns**

After parsing each cap entry, skip a case-insensitive `clio` or `clio*` pattern before deduplication. Change the fallback default to active callers only.

- [ ] **Step 3: Run Homelab contract tests**

```bash
cd /Volumes/2TB_SSD/GitHub/homelab/.worktrees/argus-v3-authority-policy-20260810
uv run --no-sync pytest -q tests/test_argus_tonight_config_contract.py tests/test_argus_acceptance_v3_contract.py
```

Expected: generator contract tests pass and output contains no retired cap.

- [ ] **Step 4: Commit Homelab change**

```bash
git add scripts/gen-env.sh tests/test_argus_tonight_config_contract.py
git commit -m "chore: remove retired Clio caller cap"
```

### Task 4: Clean the local stale Codex project entry and verify runtime policy

**Files:**
- Modify: `/Users/macmini/.codex/config.toml`
- Remote generated config: `/mnt/fast-storage/github/homelab/.env` (regenerated by the canonical Homelab generator; never commit)

**Interfaces:**
- Consumes: merged Homelab generator and current protected services input.
- Produces: no active Clio caller cap in generated runtime or local project trust list.

- [ ] **Step 1: Make a narrow backup**

```bash
cp -p /Users/macmini/.codex/config.toml /Users/macmini/.codex/config.toml.pre-clio-retirement
```

- [ ] **Step 2: Remove only the exact stale project section**

Delete `[projects."/Volumes/2TB_SSD/GitHub/clio"]` and its `trust_level` line; preserve all other Codex configuration.

- [ ] **Step 3: Regenerate Homelab environment**

Back up the remote generated `.env`, run the canonical generator from the merged Homelab checkout, and inspect only the `ARGUS_CALLER_TIER_CAPS` line. Do not print secrets.

- [ ] **Step 4: Recreate Argus services only if the generated policy changed**

Use the existing production Compose file and current digest. Verify the image digest is unchanged, then check `/api/live`, `/api/ready`, `/api/startup`, MCP initialize, and admin status.

- [ ] **Step 5: Final verification**

```bash
git status --short
rg -n -i "clio" /Users/macmini/.codex/config.toml
```

Expected: no active local project entry; historical repository records remain untouched; runtime caller caps contain no Clio.
