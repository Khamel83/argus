# Argus Acceptance v3 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> `superpowers:subagent-driven-development` to implement this plan task by task.
> Use `superpowers:test-driven-development` for every behavior change and
> `superpowers:verification-before-completion` before every completion, merge,
> deployment, or acceptance claim.

**Goal:** Add a strict, free-only, receipt-bound targeted-research mode to the
existing `build-research-pack` workflow, expose it through the canonical safe
HTTP/MCP contracts, deploy one immutable 1.6.4 candidate, and execute the
superseding v3 one-shot acceptance contract without changing the failed v2
record.

**Architecture:** The Homelab HTTP authority remains the sole production
execution and PostgreSQL owner. MCP, CLI, and development MCP are adapters over
that authority. Target validation and selection live in small pure workflow
modules; the existing accepted-operation service remains the receipt authority.
The workflow persists a sanitized plan and run-start identity before returning
from the new safe start route. A separate v3 acceptance harness owns immutable
guards, redacted snapshots, transport reads, scoring inputs, and rollback
evidence without changing scorecard-v2 bundle semantics.

**Tech stack:** Python 3.11–3.13, FastAPI, Pydantic v2, MCP Streamable HTTP,
Click, PostgreSQL, Docker Compose, Tailscale Serve, SOPS/Age, pytest, Ruff,
GitHub Actions, GHCR.

**Frozen design:**
`docs/superpowers/specs/2026-08-10-argus-acceptance-v3-design.md`
at commit `7112ac8`, SHA-256
`b9542dfe66b574fece085cff6b9f0bca1742207ca1945837c67b2e7df453e446`.

## Global constraints

- Preserve v2 run `0f30946aa4fb`, every v1/v2 guard, artifact, score, and
  rollback receipt byte-for-byte.
- Add no provider, extractor, workflow engine, UI, or database migration.
- Do not purchase anything, change a vendor account, invoke tier-3 providers,
  or make an unbounded/paid helper call.
- No promotion is permitted before the exact implementation is merged, tested,
  published, and admitted. The protected candidate promotion/soak and read-only
  preflight occur before the v3 guard; no provider, Maya, or workflow-start
  side effect is permitted until the execution contract and guard are fsynced.
- Never print or commit a token, full environment, provider-native payload,
  local evidence path, full database identifier, or unredacted SQL result.
- Preserve unrelated dirty worktrees and user-owned untracked files. Use
  isolated worktrees, stage explicit paths, and never use `git add -A`.
- Keep the legacy workflow route and empty-target behavior compatible. New v3
  strictness belongs to the strict shared request model/safe route; historical
  fixtures remain historical.
- Use exact accepted-result URLs for receipt identity even when a separate
  canonical form is used for filtering and deduplication.
- A failed test, gate, promotion, canary, or acceptance branch stops forward
  progress and follows the frozen evidence/rollback path.

## Baseline already established

- Worktree: `/Volumes/2TB_SSD/GitHub/argus/.worktrees/acceptance-v3`
- Branch: `codex/acceptance-v3`
- Base: `origin/main` at `6a130ebc70b4e3af46b14a4aa64d6158b171787a`
- Clean baseline suite:
  `2339 passed, 43 skipped, 4 warnings in 60.18s`
- Baseline command:

  ```bash
  env ARGUS_AUTOLOAD_DOTENV=false \
    ARGUS_DISABLE_SECRET_RESOLUTION=true \
    uv run --no-sync pytest -q
  ```

- Baseline Ruff is not green: it reports exactly two pre-existing F401s,
  `pytest` in `tests/test_attribution.py` and `ProviderStatus` in
  `tests/test_remote_provider.py`. They are owned by Task 0; the acceptance gate
  is not weakened.

---

### Task 0: Make the verification baseline truthful

**Files:**

- Modify: `tests/test_attribution.py`
- Modify: `tests/test_remote_provider.py`

- [ ] Run
      `uv run --no-sync ruff check tests/test_attribution.py tests/test_remote_provider.py`
      and retain the exact two F401 failures.
- [ ] Remove only the two unused imports; do not reformat or alter test logic.
- [ ] Rerun Ruff for both files and their focused pytest modules; require zero
      exit status.
- [ ] Commit explicit paths with message `test: restore clean lint baseline`.

---

### Task 1: Add the pure strict research-target contract

**Files:**

- Create: `argus/workflows/research_targets.py`
- Modify: `argus/api/schemas.py`
- Create: `tests/test_research_target_contract.py`
- Test: `tests/test_attribution.py`

**Contract:** The shared model accepts the exact frozen five-target request,
canonicalizes public HTTPS prefixes to JSON strings, rejects unsafe/overlapping
plans before any operation, retains explicit `official_url: null`, and leaves an
empty target list on the legacy path.

- [ ] Write failing tests for `ResearchRequirement`, `ResearchTarget`, and
      `BuildResearchPackWorkflowRequest` strict unknown-key rejection, lengths,
      control/credential/path scans, caller-label bounds, target/name and
      claim-class uniqueness, at most 8 targets/16 requirements, page budget,
      and non-null-official-versus-target mutual exclusion.
- [ ] Explicitly test topic scans; `official_url` canonical HTTPS/public-host,
      no credential/query/fragment/control/path, and 2,048-character bound;
      one-to-three mandatory requirements per target; and mandatory semantics
      for every supplied target/requirement.
- [ ] Add table-driven failing tests for HTTPS prefix rejection: credentials,
      query, fragment, wildcard, IP, localhost, `.local`, private/reserved host,
      public-suffix-only host, and path-boundary collision.
- [ ] Add tests proving `/Khamel83/argus/` matches its base path with or without
      the trailing slash and descendants, but not `/Khamel83/argusmatic`.
- [ ] Add tests proving canonical JSON uses string URLs, preserves null versus
      absent semantics, and hashes the exact frozen request deterministically.
- [ ] Add normalization/overlap vectors for default port, host case, IDNA,
      trailing slash, duplicate prefixes, ancestor/descendant overlap within
      one target, and overlap across targets.
- [ ] Run the focused tests and confirm RED because the models/helpers do not
      exist:

  ```bash
  env ARGUS_AUTOLOAD_DOTENV=false ARGUS_DISABLE_SECRET_RESOLUTION=true \
    uv run --no-sync pytest -q \
    tests/test_research_target_contract.py tests/test_attribution.py
  ```

- [ ] Implement pure validators, prefix matching, overlap detection, canonical
      request projection, and canonical SHA-256 in
      `argus/workflows/research_targets.py`; keep provider/network code out.
- [ ] Wire the strict models and additive fields into the API schema using
      `model_dump(mode="json")` at the service boundary.
- [ ] Run the focused tests plus `tests/test_schema_contract_generator.py` and
      `tests/test_architecture_boundaries.py`; confirm GREEN.
- [ ] Commit only Task 1 files with message
      `feat: add strict research target contract`.

---

### Task 2: Enforce free-only accepted extraction before network

**Files:**

- Modify: `argus/operations/accepted.py`
- Modify: `argus/extraction/extractor.py`
- Modify: `argus/extraction/models.py` if attempt state needs a skip field
- Modify: `argus/extraction/outcomes.py` if the decision projection needs wiring
- Modify: `argus/extraction/cache.py` for accepted-path eligibility/provenance
- Modify: `argus/extraction/finalizer.py` only if projection mapping is missing
- Test: `tests/test_extraction.py`
- Test: `tests/test_accepted_operations.py`
- Test: `tests/test_extraction_outcomes.py`
- Test: `tests/test_site_acquisition_limits.py`
- Test: `tests/test_workflow_composition.py`
- Test: `tests/test_retrieval_planning.py`
- Test: `tests/test_provider_readiness.py`

**Contract:** `free_only=true` reaches accepted site search, composition, and
extraction. Jina, Valyu Contents, Firecrawl, and You Contents become durable
`policy_skipped` attempts before network. Normal non-free extraction preserves
its legacy chain, while `ARGUS_JINA_ENABLED=false` actually suppresses Jina.

- [ ] Add failing tests proving accepted `acquire_site` no longer hard-codes
      `free_only=False`, and `extract`/`compose_workflow` carry the flag without
      changing omitted/default behavior.
- [ ] Add a failing no-network test with credentials present that monkeypatches
      all four billable helper call sites and asserts none is invoked in
      free-only mode.
- [ ] Add a failing Jina runtime-gate test asserting disabled configuration
      makes no HTTP request in normal mode; make existing chain-order fixtures
      explicitly enable Jina.
- [ ] Add evidence tests for `POLICY_SKIPPED`, bounded reason codes, attempted
      chain order, cache-hit `attempted=false`, and no false `INVOKED` record.
- [ ] Add cache-eligibility tests proving request mode/profile, effective tier
      cap, provider restrictions, eligible provider set, freshness window, and
      original evidence participate in the decision. Permit an otherwise
      eligible paid-origin cache hit with no new call while retaining its origin
      spend provenance; reject provider-restricted or otherwise
      policy-ineligible cross-boundary evidence.
- [ ] Add an accepted-evidence cache integration test. The current accepted
      path disables the legacy in-memory cache and always projects MISS; wire a
      durable accepted lookup/publication decision rather than proving only a
      synthetic finalizer object.
- [ ] Confirm RED with:

  ```bash
  env ARGUS_AUTOLOAD_DOTENV=false ARGUS_DISABLE_SECRET_RESOLUTION=true \
    uv run --no-sync pytest -q \
    tests/test_extraction.py tests/test_accepted_operations.py \
    tests/test_extraction_outcomes.py tests/test_site_acquisition_limits.py \
    tests/test_workflow_composition.py tests/test_retrieval_planning.py \
    tests/test_provider_readiness.py
  ```

- [ ] Thread the flag through accepted request namespaces and extraction
      functions. Evaluate policy before each external helper, emit durable skip
      evidence, and preserve accepted URL/extraction receipt identity.
- [ ] Map known free-only/caller-cap/unavailable skips to stable bounded reason
      codes; never store raw provider errors in the public projection.
- [ ] Preserve complete provider/extractor diagnostics: attempted/succeeded/
      failed/skipped state, timeout source, operation/cache latency, cache
      hit/miss/ineligible, cache age/origin/spend, freshness age/window/reason,
      and free-profile eligibility. Missing diagnostics are a failure, not an
      inferred zero-cost success.
- [ ] Run the focused extraction/accepted-operation suites and Ruff; confirm
      GREEN.
- [ ] Commit Task 2 files with message
      `feat: enforce free-only extraction policy`.

---

### Task 3: Add durable safe workflow start and persisted identity

**Files:**

- Modify: `argus/api/schemas.py`
- Modify: `argus/api/routes_workflows.py`
- Modify: `argus/workflows/service.py`
- Create: `tests/test_workflow_start_contract.py`
- Test: `tests/test_research_report_workflow.py`
- Test: `tests/test_http_authority.py`

**Interface:**

- `POST /api/workflows/build-research-pack/start` → HTTP 202
- Existing legacy `POST /api/workflows/build-research-pack` remains unchanged
- Existing safe status/artifact routes remain run-ID based

"Legacy unchanged" means the valid legacy topic/official/page/caller request
and its broad response keep their behavior. Both routes intentionally use the
new strict shared request model, so formerly ignored unknown fields or unsafe
URLs now fail closed as required by the frozen design.

- [ ] Write failing route/service tests for the exact safe response keys:
      `run_id`, `kind`, `status`, `target`, aware `created_at`, `status_url`, and
      canonical `request_sha256`; assert no snapshot/report/manifest path,
      document body, receipt, exception, or provider payload.
- [ ] Add tests proving the PENDING state, sanitized target plan,
      authenticated identity, body label, start runtime identity, request hash,
      and aware deadline are fsynced before the 202 response.
- [ ] Simulate initial persistence failure and process reload; require a stable
      failed response/state rather than an orphaned in-memory run. A reloaded
      targeted `pending`/`running` state with no live task must atomically become
      `workflow_interrupted`, retain deadline/runtime/request evidence, start no
      operation, and write no report/manifest.
- [ ] Add tests proving terminal status uses persisted run-start runtime even if
      the current deployment runtime changes later; expose the live mismatch as
      separate observation evidence without replacing persisted identity.
- [ ] Confirm RED:

  ```bash
  env ARGUS_AUTOLOAD_DOTENV=false ARGUS_DISABLE_SECRET_RESOLUTION=true \
    uv run --no-sync pytest -q \
    tests/test_workflow_start_contract.py \
    tests/test_research_report_workflow.py tests/test_http_authority.py
  ```

- [ ] Add a dedicated safe response model/route; do not reuse the legacy
      path-bearing `WorkflowRunResponse`.
- [ ] Persist PENDING synchronously and store JSON-mode target/free-only/deadline
      metadata before scheduling `_execute_run`.
- [ ] Extend atomic state writes to fsync the file and parent directory before
      the 202 response or terminal status is observable.
- [ ] Preserve stable HTTP error mapping and authenticated principal/body-label
      separation.
- [ ] Run focused API/workflow tests,
      `tests/test_schema_contract_generator.py`,
      `tests/test_architecture_boundaries.py`, and Ruff; confirm GREEN.
- [ ] Commit Task 3 files with message
      `feat: add durable safe workflow start`.

---

### Task 4: Forward the strict request through every caller surface

**Files:**

- Modify: `argus/mcp/http_adapter.py`
- Modify: `argus/mcp/server.py`
- Modify: `argus/development_mcp_tools.py`
- Modify: `argus/development_mcp_adapter.py` if its wrapper is separate
- Modify: `argus/cli/main.py`
- Test: `tests/test_mcp_research_report.py`
- Test: `tests/test_mcp_v2.py`
- Test: `tests/test_http_authority.py`
- Test: `tests/test_cli_v2.py`
- Test: `tests/test_development_mcp_adapter.py` if present

**Contract:** Production MCP remains a stateless authenticated HTTP adapter.
CLI accepts repeatable `--research-target-json` plus `--free-only`; every
surface validates through the same model, forwards JSON-mode strings, preserves
authenticated identity, and exposes the safe start/status/artifact semantics.

- [ ] Write failing adapter and tool-registration tests for exact nested target
      JSON, `official_url:null`, `free_only`, caller label, safe-start path, and
      protected token forwarding.
- [ ] Add malformed/unknown target CLI tests that fail before any HTTP call.
- [ ] Add parity tests for success, validation failure, authentication failure,
      status JSON, and artifact JSON pagination; preserve adapter-only
      `response_format` outside the workflow request hash.
- [ ] Confirm RED with the focused transport suites.
- [ ] Implement thin forwarding only. The MCP process must not receive provider
      credentials, DB configuration, browser paths, or writable data volumes.
- [ ] Run MCP/CLI/HTTP authority and
      `tests/test_architecture_boundaries.py`; confirm GREEN.
- [ ] Commit Task 4 files with message
      `feat: forward targeted research workflow inputs`.

---

### Task 5: Build deterministic target selection and failure semantics

**Files:**

- Create: `argus/workflows/targeted_research.py`
- Modify: `argus/workflows/service.py`
- Create: `tests/test_research_target_workflow.py`
- Test: `tests/test_workflow_composition.py`
- Test: `tests/test_research_report_workflow.py`

**Contract:** One accepted research search per supplied requirement yields at
most the first two exact receipt-bound prefix candidates. Global canonical
dedupe does not alter accepted identity. Every supplied requirement requires
one usable artifact; one independent external-secondary is mandatory, a second
is best-effort when budget remains, and the unique document total never exceeds
the caller's validated page budget. Fifteen requirements/17 pages are frozen
Task 12 inputs, not hard-coded workflow constants.

- [ ] Start with pure failing planner tests for target/input order, prefix-path
      filtering, exact-URL retention, global dedupe, one URL per requirement,
      target-domain exclusion from external results, registrable-domain limits,
      and exact 15+1+optional page math.
- [ ] Add a generic matrix for 0 (legacy), 1, 15, and 16 supplied requirements,
      varied valid/invalid `max_research_pages`, and presence/absence of the
      optional second external page. Derive every count from the request.
- [ ] Assert exactly one accepted `research` search per requirement,
      `max_results=8`, at most 16 requirement searches, and external selection
      through the same canonical-HTTPS/public-host/no-credential/no-query-or-
      fragment/SSRF-safe validator as target prefixes.
- [ ] Add failing disposition tests:
      - zero prefix candidates → `workflow_required_target_unready`;
      - requirement search timeout →
        `workflow_required_target_search_timeout`;
      - all available candidates diagnostic/failed →
        `workflow_required_target_extraction_failed`;
      - missing mandatory independent external →
        `workflow_external_evidence_unready` or extraction-failed;
      - optional second external absent → `degraded_external_unavailable`;
      - no more than four external extraction candidates are attempted; and
      - budget overrun → `workflow_page_budget_exceeded`.
- [ ] Add tests proving accepted authority UNREADY/persistence/contract errors
      retain their original fatal code and are not rewritten with the legacy
      `workflow_composition_` prefix.
- [ ] Confirm RED with the new suite.
- [ ] Implement pure selection records and a stable-code-preserving target
      failure type. Keep diagnostics for failed candidates private.
- [ ] Give each target/requirement a deterministic internal request ID derived
      from the accepted search receipt plus requirement reference; never reuse
      `run_id` alone for every composition.
- [ ] Run planner, workflow composition, and report workflow tests; confirm
      GREEN.
- [ ] Commit Task 5 files with message
      `feat: plan receipt-bound research targets`.

---

### Task 6: Execute targets within the persisted 540-second deadline

**Files:**

- Modify: `argus/workflows/targeted_research.py`
- Modify: `argus/workflows/service.py`
- Modify: `argus/operations/accepted.py` only for missing projection fields
- Test: `tests/test_research_target_workflow.py`
- Test: `tests/test_research_report_workflow.py`
- Test: `tests/test_accepted_operations.py`

**Contract:** At most one accepted operation per target and five globally; each
requirement search is bounded to 30 seconds, each candidate composition to 45,
external remainder to 120, and the complete persisted workflow to 540. No new
accepted operation starts after budget exhaustion; cancellation is awaited
before terminal persistence.

- [ ] Add deterministic fake-clock tests for bounded concurrency, required
      ordering after concurrent collection, remaining-budget checks, candidate
      timeout fallback, global timeout, cancellation, restart/reload deadline,
      and zero operations after terminal timeout. Reload never resumes network
      work: an orphaned active targeted run atomically becomes
      `workflow_interrupted` with original evidence and zero new accepted calls.
- [ ] Add tests proving targeted mode bypasses the legacy single-official docs
      cache alias and does not sort target documents by word count.
- [ ] Add tests for execution evidence propagation: provider/extractor,
      egress/machine/source type, cache state/age/origin, spend provenance,
      retrieval timestamp, source date, artifact disposition, and exact text
      hash; also require result count, timeout source, operation/cache latency,
      cache eligibility/ineligible reason, freshness age/window/reason, and
      free-profile eligibility for every provider/extractor path.
- [ ] Confirm RED with the focused workflow suites.
- [ ] Implement the async scheduler with a per-target lock/semaphore, global
      semaphore, remaining-budget timeout wrappers, and awaited cancellation.
      Collect results back in target/requirement order.
- [ ] Add a narrow injected aware-UTC/monotonic clock provider to
      `WorkflowService` rather than scattering monkeypatches over direct
      `datetime.now()` calls; legacy construction keeps the real-clock default.
- [ ] Integrate the targeted branch into `_build_research_pack_impl`; leave the
      legacy branch byte-compatible except for accepted-citation closure.
- [ ] Run focused tests and Ruff; confirm GREEN.
- [ ] Commit Task 6 files with message
      `feat: execute targeted research within deadline`.

---

### Task 7: Enforce report, manifest, status, and legacy citation closure

**Files:**

- Modify: `argus/workflows/service.py`
- Modify: `argus/api/schemas.py`
- Create: `tests/test_research_pack_closure.py`
- Test: `tests/test_research_report_workflow.py`
- Test: `tests/test_workflows.py`

**Contract:** Completed targeted packs expose the same sanitized research plan
and run-start identity in status/manifest/report, one citation per requirement,
the Claim Evidence Matrix, bounded excerpts/full-text hashes, deterministic
freshness, complete provenance/cache diagnostics, and zero unresolved claim or
URL closure. Failed runs expose only bounded incomplete status and no artifacts.

- [ ] Add failing tests for the completed v3 manifest shape, target/requirement
      outcome enums, exact one-to-one selected URL/citation/source closure,
      external-secondary closure, unique counts, and 17-page cap.
- [ ] Repeat count/closure assertions with non-frozen 1- and 16-requirement
      fixtures so manifest totals and page budgets cannot be hard-coded to
      15/17.
- [ ] Add failing report tests for header/runtime identity, one evidence-matrix
      row per requirement, citation markers, qualified undated claims, partial
      labels, and explicit unknowns.
- [ ] Add table-driven freshness tests for the inclusive frozen
      `2025-08-09..2026-08-09` source-date window; older, later/future,
      unparseable, and missing dates must never become `dated_current`.
- [ ] Add leakage/bounds tests for titles, excerpts, bodies, absolute URLs,
      control characters, local paths, bearer/private-key markers, raw errors,
      receipts, DB IDs, and the 4 MiB cap.
- [ ] Assert the exact public limits: labels 100, titles 1,000, URLs 2,048,
      evidence excerpts 2,000, section titles 200, and evidence/summary bodies
      20,000 UTF-8 characters. Every excerpt must be an exact substring of the
      accepted artifact and bind to its full-text SHA-256 after the public
      redaction audit.
- [ ] Add the v2 regression test: a discovered official URL absent from accepted
      artifacts may appear only as `discovery_candidate_only`; it cannot be
      claimed captured. A caller-supplied non-null official URL is required
      receipt-bound evidence and fails closed if diagnostic/missing.
- [ ] Add persistence/reload tests for sanitized plan, deadline, runtime, caller
      identity/label, request hash, source metadata, and terminal artifact hash.
- [ ] Confirm RED with closure/workflow suites.
- [ ] Implement pre-finalization closure validation before either artifact is
      written. Render Pack Composition from accepted citations only.
- [ ] Ensure artifact generation is atomic: validation failure writes terminal
      safe status but no partial report/manifest.
- [ ] Run focused suites, API schemas, Ruff, and
      `tests/test_architecture_boundaries.py`; confirm GREEN.
- [ ] Commit Task 7 files with message
      `feat: enforce research pack citation closure`.

---

### Task 8: Add the separate v3 acceptance evidence harness

**Files:**

- Create: `argus/acceptance_v3/__init__.py`
- Create: `argus/acceptance_v3/contract.py`
- Create: `argus/acceptance_v3/observations.py`
- Create: `argus/acceptance_v3/bundle.py`
- Create: `scripts/run-acceptance-v3.py`
- Create: `tests/test_acceptance_v3_contract.py`
- Create: `tests/test_acceptance_v3_observations.py`
- Create: `tests/test_acceptance_v3_bundle.py`
- Preserve: `argus/scorecard/bundle.py`

**Contract:** The harness is a separate
`argus-acceptance-v3/free-targeted` artifact writer. It never changes
scorecard-bundle-v2, never embeds a secret/local path publicly, and never
retries a dispatched POST. It supports explicit completed/scored,
pre-artifact-not-run, evaluator-not-run, preflight-failed, FAIL/rollback, and
rollback-incomplete branches.

- [ ] Write failing unit tests for compact canonical JSON/hashes, trusted
      non-symlink 0700 evidence roots, 0600 `O_EXCL` guards/phase markers, file
      and parent fsync, retained v1/v2 guards, null-versus-absent request
      identity, and one immutable returned-run binding containing run kind,
      topic, request/body hashes, and dispatch time. A second bind must fail
      without replacing the first file.
- [ ] Make the execution-contract fixture exact: cycle ID
      `argus-acceptance-v3-free-targets-2026-08-10`, profile `free`, schema
      `build-research-pack/v3`, trusted resolved private root, mode-0600
      `execution-contract.json`, and global guard path
      `/Users/macmini/.local/state/argus-tonight-final-score-v3-started.json`.
      Require canonical workflow/start-body hashes; every HTTP/MCP endpoint,
      request, pagination, and envelope-normalization hash; full candidate and
      rollback identity/receipt; merged spec, scorecard, synthesis prompt,
      evaluator, harness, and client-probe hashes; unauthenticated probe shape,
      response, and no-side-effect hashes; topology/provider/extractor/corpus/
      authority snapshots; canary query/body/key hashes; and pre-canary
      snapshot hashes. Missing or extra identity fields fail before a guard.
- [ ] Write synthetic snapshot tests for deterministic DB-UTC ordering,
      enforced 15-second statement and 2-second lock timeouts, all-new-row spend
      rejection, predecessor immutable fields, cache-hit attempted=false,
      outbox drain/dead-letter rules, Maya exact replay, hashed opaque IDs, and
      a distinct pre-rollback snapshot that precedes delivery quiescence or
      promoter mutation.
- [ ] Enumerate the all-new-row rejection predicate: paid, tier above zero,
      nonzero reserved/actual/overrun, uncertain, unsettled, estimator-
      violating, unlabeled, caller-identity/phase-label mismatched, or outside
      the DB-UTC observation window. Also reject unresolved spend-audit/balance
      deltas and unledgered billable network attempts.
- [ ] Require a redacted hash-bound value-free policy snapshot proving evidence
      and PostgreSQL authority, no SQLite fallback, authenticated
      `mac-agents` cap 1, free-profile routing, You Contents disabled,
      Jina/Valyu/Firecrawl/You request-time policy skips, and the exact eligible
      provider/extractor set with complete search/extraction diagnostics.
- [ ] Add local-config recovery tests for byte-for-byte mode-0600 backups,
      original/proposed/post hashes, exact field-level delta, unrelated-field
      preservation, candidate-specific versus independently approved topology
      classification, and restoration of candidate-specific edits only.
- [ ] Write transport-page tests for contiguous offsets, byte counts, UTF-8,
      strict manifest JSON, terminal hash/size, 64-page/4 MiB bounds, exact MCP
      tool args/envelope normalization, and unauthenticated 401/403 no-side-
      effect evidence.
- [ ] Exercise the lockfile-pinned MCP Python `streamable_http_client` plus
      `ClientSession`: protected Authorization reference, exact
      `Accept: application/json, text/event-stream`, initialize then list-tools
      before calls, no redirect/reconnect/automatic retry, bounded SSE/JSON and
      TextContent, and hashed negotiated protocol/package/session identity.
- [ ] Write bundle tests for exactly eight gates, six-cell arithmetic,
      `claim-support.json`, recovery/config backups, `not_run` schemas,
      checksum coverage of every other file, and PASS iff all eight are PASS,
      none is PENDING/missing, `score.status="scored"`, no required section is
      `not_run`, and score ≥85.
- [ ] Define the separate v3 eight-gate tuple/validator in the acceptance
      namespace. Never reuse `argus.scorecard.stability.HARD_GATES`, which is a
      distinct 22-gate stability contract, and never alter its v2 semantics.
      Hash the exact eight frozen v2 acceptance definitions and test v3 equality
      against that fixture so names, order, and pass conditions cannot drift.
- [ ] Assert competitive baseline/pair sections are explicitly
      `not_applicable`, never copied or fabricated from the separate 24-search
      scorecard corpus.
- [ ] Add minimum-evidence audit tests that recompute from manifest sources,
      exclude partial/rejected entries, and require at least 5 unique usable
      URLs, 3 registrable domains, 2 primary sources, all 15 target requirements,
      complete provenance/degraded labels, and zero closure/leak counts.
- [ ] Add fake-transport tests proving exactly three canary POSTs and one
      workflow-start POST, with attempt marker fsynced before dispatch and no
      automatic retry on timeout/disconnect/5xx.
- [ ] Make the canary fixture exact: fresh nonce/request/idempotency hashes;
      body `query=argus-acceptance-v3-canary-<nonce>`, `mode=discovery`,
      `max_results=1`, `providers=[github]`, `free_only=true`, and
      `caller=tonight-acceptance-v3-canary`; one uncached GitHub-only HTTP 200
      with one success/empty trace, one
      accepted operation/plan/batch and zero non-GitHub/paid/legacy-delivery;
      then one frozen Maya body with nonce idempotency key, mode `discovery`,
      identical aware-UTC start/completion, fixed summary, provenance
      `providers=[github]`, `egress=unknown`,
      `machine=argus-acceptance-v3`, `source_type=search`, and `pages=[]`, sent
      byte-identically for 201 nonduplicate and 200 duplicate. Require response
      and durable caller `argus`, one capture/key/body hash, identical capture
      ID, and zero pages. Assert predecessor immutability, allowed canary row,
      bounded drain, and exclusion from the benchmark baseline.
- [ ] Add preflight-matrix tests for exact live/startup/ready/health predicates,
      PostgreSQL/evidence authority with explicit rejection of any SQLite
      fallback, runtime identity, zero restarts,
      profile/topology, Codex/Claude/OpenCode HTTPS initialization and tool
      listing, Gemini disabled, and absence of local MCP/retired port/SSE/
      literal bearer. The unauthenticated MCP request must produce 401/403 with
      no redirect/session/side effect, and every probe hash enters the contract.
- [ ] Add log-window tests that bound, redact, and hash Argus and MCP logs from
      pre-canary observation through post-benchmark. Correlate the one allowed
      unauthenticated 401/403 and any predeclared expected probe by request hash
      and time; reject unexpected 5xx/421 or repeated/unexpected 401 loops.
- [ ] Add evaluator semantics tests: `unsupported` fails unchanged Gate 7 only
      when a material factual assertion lacks actual support; `partial` and an
      explicitly labeled unknown remain qualified and reduce applicable rubric
      cells instead of creating a ninth hard gate.
- [ ] Confirm RED with all new acceptance tests.
- [ ] Implement pure writers/auditors first, then the CLI orchestration around
      injected HTTP/MCP/SSH/SQL adapters. Do not hard-code or print tokens.
- [ ] Require evaluator input/output as hash-bound private JSON; validate the
      exact model/settings/prompt contract and prove web, tools, and memory are
      disabled without giving the harness model, provider, DB, or spend
      authority.
- [ ] Run new tests, existing scorecard bundle tests, Ruff, and architecture
      tests; prove scorecard-v2 fixtures/hashes remain unchanged.
- [ ] Commit Task 8 files with message
      `feat: add guarded acceptance v3 harness`.

---

### Task 9: Bump release identity only after behavior is green

**Files:**

- Modify: `pyproject.toml`
- Modify: `server.json`
- Modify: `argus/__init__.py`
- Modify: `argus/api/main.py`
- Modify mechanically: `uv.lock`
- Modify: `tests/test_release_workflow.py`
- Modify: `tests/test_runtime_manifest.py`
- Modify current-release constants only where truly current
- Preserve historical version fixtures

- [ ] Change current release assertions from 1.6.3 to 1.6.4 and confirm RED.
- [ ] Update all five user/runtime identity locations; run `uv lock --offline`
      or the repository-approved deterministic lock refresh and inspect that
      only the local package version changed.
- [ ] Run:

  ```bash
  env ARGUS_AUTOLOAD_DOTENV=false ARGUS_DISABLE_SECRET_RESOLUTION=true \
    uv run --no-sync pytest -q \
    tests/test_release_workflow.py tests/test_runtime_manifest.py
  uv run --no-sync python scripts/verify_release_contract.py
  ```

- [ ] Run focused workflow, extraction, transport, acceptance, scorecard, and
      architecture suites. Then run Ruff and the full hermetic suite with final
      exit code captured.
- [ ] Generate an unsealed local hermetic scorecard bundle and `live-config`
      interface manifest in a private temporary directory; verify their
      checksums and no-network behavior.
- [ ] Request two independent code reviews: standards against AGENTS.md and
      spec against the frozen v3 design. Resolve every P0/P1/P2 and rerun the
      affected tests.
- [ ] Commit Task 9 files with message `chore: release argus 1.6.4`.

---

### Task 10: Project the production authority policy through Homelab

**Repository/worktree:** Create a clean isolated worktree from current
`/Volumes/2TB_SSD/GitHub/homelab` origin/main. Do not edit its dirty default
checkout.

**Files:**

- Modify: `services/argus/docker-compose.yml`
- Modify candidate/scorecard compose only where parity requires it
- Modify/create: `tests/test_argus_promotion_contract.py`
- Create: `tests/test_argus_acceptance_v3_contract.py`
- Modify promoter/config scripts only if a test proves the projection missing

**Contract:** The runtime receives explicit evidence authority, scoped caller
credentials, caller caps, Maya capture URL/token, and You Contents disabled.
MCP remains stateless and receives only authority URL/token. Candidate and
production config support reversible Maya delivery pause without editing rows.

- [ ] Add failing compose-contract tests for the required in-container names:
      `ARGUS_API_KEY`, `ARGUS_CALLER_CREDENTIALS_JSON`,
      `ARGUS_CALLER_TIER_CAPS`, `ARGUS_ACCEPTED_OPERATION_AUTHORITY`,
      `ARGUS_MAYA_CAPTURE_URL`, and `ARGUS_MAYA_CAPTURE_TOKEN`; assert authority
      `evidence`, `ARGUS_YOU_CONTENTS_ENABLED=false`, and no secret literal.
- [ ] Add tests proving `mac-agents:1` matching, candidate/production parity,
      safe delivery pause/re-enable, MCP credential minimalism, and no local DB
      or browser mount on MCP.
- [ ] Confirm RED, then add explicit Compose environment projections using
      protected env/secret references. Do not echo their values.
- [ ] Run Homelab focused tests, shellcheck/YAML checks, and its required full
      contract suite; capture final exit codes.
- [ ] Obtain independent review, commit explicit paths, push, and merge through
      the normal repository workflow before Argus promotion.

---

### Task 11: Integrate, publish, admit, and promote one immutable candidate

- [ ] Rebase the Argus worktree on the latest origin/main without touching the
      root checkout; resolve only scoped conflicts.
- [ ] Rerun the full Argus suite and both independent reviews on the exact
      rebased head.
- [ ] Push the branch and open a draft PR. Require CI 3.11/3.12/3.13,
      scorecard, PostgreSQL ledger, production-config, image-build, freshness,
      and release-contract checks to pass on the exact head.
- [ ] Merge only after review/checks are terminal. Record merge SHA; do not
      treat PR/CI success as runtime proof.
- [ ] Publish the digest-addressed 1.6.4 image from that exact source. Record
      image digest, source revision, deployment identity, release receipt,
      hermetic PASS bundle hash, `live-config` interface hash, and protected
      promotion-gate receipt.
- [ ] Verify the baseline rollback identity is still exactly the frozen
      dbe4/d11c release and its receipt before cutover.
- [ ] Promote through the protected Homelab promoter. Monitor the entire soak,
      current/known-good/cutover markers, health, restart counts, logs, and
      PostgreSQL delivery/spend state. A lost runner connection is reconciled
      only from durable host state; never infer completion from an SSH exit.
- [ ] Leave the v3 global guard absent and make no provider, Maya, or workflow
      call in this task. Candidate canary and start belong only to Task 12 after
      its immutable contract, snapshots, and guard are fsynced.
- [ ] Stop and execute the preflight-failed rollback branch on any candidate
      identity, authority, policy, delivery, health, or log gate failure.

---

### Task 12: Execute and evaluate the one-shot v3 acceptance

- [ ] Verify the old v2 guard and artifacts remain untouched and the v3 guard
      does not yet exist.
- [ ] Create the trusted private evidence root. Before any local config change,
      make a byte-for-byte mode-0600 backup; hash original/proposed/post bytes;
      conditionally refresh only the protected mac-agents token reference;
      remove only a verified dormant Claude local-Argus entry; preserve every
      unrelated field; record the exact field delta and classify it as
      candidate-specific or independently approved. Restore only
      candidate-specific edits on rollback.
- [ ] Take the pre-canary snapshots and prove exact
      `/api/live.status=alive`, `/api/startup.status=initialized`,
      `/api/ready.ready=true`, `/api/health.status=ok`, PostgreSQL evidence
      authority with no SQLite fallback, persisted runtime identity, zero
      restarts, and sanitized profile/topology. Prove Codex, Claude Code, and
      OpenCode initialize/list tools over canonical HTTPS; Gemini is disabled;
      no effective/dormant entry uses local MCP, a retired port, SSE, or a
      literal bearer. Require one unauthenticated MCP 401/403 with no redirect,
      session, provider, spend, outbox, or Maya side effect and hash every probe.
- [ ] If any post-promotion pre-guard check fails, write the separate
      `preflight_failed` bundle; persist/checksum the preflight, runtime, and
      config evidence; restore candidate-specific config; take the pre-rollback
      snapshot; quiesce delivery; restore/soak the exact baseline; and record
      the terminal restored identity or `rollback_incomplete`. Stop without
      creating a guard/phase marker or dispatching a provider, Maya, or workflow
      request.
- [ ] Freeze the exact request bytes from the design, evaluator identity,
      claim-support prompt, HTTP/MCP request shapes, candidate/rollback values,
      preflight/topology/provider/corpus/authority/canary hashes, merged
      spec/scorecard/client-probe hashes, and harness hash in the exact Task 8
      execution contract. Fsync the exact global v3 guard path before the first
      provider/Maya/workflow side effect.
- [ ] Assert fresh nonce/request/idempotency hashes, then run exactly one
      canary POST with the exact hashed body
      `query=argus-acceptance-v3-canary-<nonce>`, `mode=discovery`,
      `max_results=1`, `providers=[github]`, `free_only=true`, and
      `caller=tonight-acceptance-v3-canary`. Require uncached HTTP 200, one success/empty
      GitHub trace, one accepted operation/plan/batch, and zero non-GitHub,
      paid, or legacy-delivery rows. Send one frozen Maya body byte-identically
      twice and require 201/nonduplicate then 200/duplicate, identical nonempty
      capture ID/body hash, exactly one durable capture/key, and zero pages.
      Prove predecessor immutability and bounded drain, then take a fresh
      benchmark baseline that excludes every canary row.
- [ ] Fsync the start-attempt marker and issue exactly one canonical HTTP safe
      start. Bind the returned run ID once; never retry an ambiguous response.
- [ ] Poll within 600 seconds/540-second workflow deadline. Follow the explicit
      completed versus `not_run` branch.
- [ ] For a completed pack, reconstruct report and manifest over HTTP and MCP,
      exercise small-page pagination, verify bytes/hashes/identity, apply the
      fixed claim-support evaluator, and recompute manifest evidence excluding
      partial/rejected entries. Require ≥5 unique usable URLs, ≥3 registrable
      domains, ≥2 primary sources, all 15 covered requirements, complete
      provenance/degraded labels, and zero closure/leak counts.
- [ ] Run the unchanged fixed research-prompt template with the exact later-v2
      Frozen benchmark question/decision/scope/constraints, using only the v3
      report and manifest. Audit every material claim/URL and separate verified
      facts, inference, conflicts, and unknowns. Hash the synthesis and freeze
      all six rubric cells before reading separate market research.
- [ ] Diff all spend/balance/outbox/Maya rows and provider/extractor traces;
      reject every new paid, tier>0, reserved/actual/overrun, uncertain,
      unsettled, estimator-violating, unlabeled, caller/phase-mismatched, or
      out-of-window row; require no unresolved balance/audit delta or unledgered
      billable attempt, no predecessor mutation, no new workflow outbox row
      under evidence authority, bounded delivery residual, and no unexpected
      5xx/421/401 loop.
- [ ] Capture the bounded, redacted, hash-stable Argus and MCP log window from
      pre-canary observation through post-benchmark. Correlate the allowlisted
      unauthenticated 401/403 and every expected probe by request hash/time;
      reject unexpected 5xx/421 and repeated or unexpected 401 loops.
- [ ] Write/checksum `gates.json`, `score.json`, `claim-support.json`,
      `recovery-evidence.json`, report/manifest pages, logs, snapshots, and the
      bundle manifest. Derive the literal verdict from those files only.
- [ ] PASS only with exactly eight PASS gates and score ≥85. Otherwise publish
      immutable FAIL, take and fsync the separate pre-rollback accounting,
      delivery, runtime, and health snapshot. Quiesce Argus-to-Maya by removing
      the candidate capture endpoint and recreating only Argus; prove by
      name-only in-container inspection that it is absent, observe the
      dispatcher quiescent, and never edit/delete/acknowledge rows to manufacture
      quiescence. Restore the exact dbe4/d11c baseline through the promoter,
      complete the soak, restore only baseline delivery config, and record the
      separate post-rollback identity/health/accounting. A failed rollback is
      `rollback_incomplete`, stops all further mutation, requires explicit
      operator intervention, and can never be reported as PASS or restored.
- [ ] Update the durable acceptance and managed-stack evaluation documents with
      the exact v3 outcome, without overwriting v2, then obtain a final
      evidence-only review.

## Final verification matrix

Before any completion claim, retain the final exit/result for:

```bash
env ARGUS_AUTOLOAD_DOTENV=false ARGUS_DISABLE_SECRET_RESOLUTION=true \
  uv run --no-sync pytest -q
uv run --no-sync ruff check argus tests scripts
uv run --no-sync python scripts/verify_release_contract.py
uv run --no-sync python scripts/run-scorecard.py \
  --lane hermetic --output "$PRIVATE_HERMETIC_BUNDLE" \
  --candidate-image-digest "$CANDIDATE_DIGEST"
uv run --no-sync python scripts/run-scorecard.py \
  --lane live-config --output "$PRIVATE_LIVE_CONFIG"
```

Also retain terminal exact-head GitHub checks, Homelab promotion/soak receipt,
runtime manifest, HTTP/MCP/client probes, canary ledger/delivery evidence,
acceptance bundle checksums, literal verdict, and—on failure—the exact rollback
receipt. A partial command log, a merged PR, a passing unit suite, or prose alone
is not completion evidence.
