# Retrieval Evidence Mechanical Port Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the accepted Argus retrieval, provider-readiness, extraction-outcome, evidence, and HTTP/MCP contracts into production code as the smallest dependency-ordered sequence of independently testable changes, then promote the exact stable and competitive release through the existing homelab gates.

**Architecture:** Keep `SearchBroker` and HTTP as the only production execution authority, but move policy and truth into focused deep modules: deterministic planning, provider evidence normalization, evidence fusion, provider readiness, extraction finalization/composition, durable acceptance/cache publication, and transport presentation. Legacy HTTP and MCP remain presentation adapters; version 2 is additive. Development and migrations are built hermetically in isolated worktrees, while production schema application, contract activation, and client cutover occur only through one immutable release after the operational promotion gates.

**Tech Stack:** Python 3.12, dataclasses and enums, `asyncio`, FastAPI, Pydantic, SQLAlchemy 2, Alembic, MCP Python SDK 1.x, PostgreSQL 16 in production, isolated SQLite/PostgreSQL fixtures in tests, `pytest`, `pytest-asyncio`, `jsonschema`, and `uv`.

## Global Constraints

- HTTP is the only production execution authority. MCP and CLI delegate to authenticated HTTP.
- The deterministic planner performs no model call, query rewrite, vector lookup, network operation, or inferred intent.
- Operation deadline is at most `120000` milliseconds and reserves the final `5000` milliseconds for fusion, durable acceptance, and presentation.
- Search result limits remain integers from `1` through `50`.
- Each provider is invoked at most once per plan; retries and redirects remain inside that attempt deadline.
- `free_only=true` is enforced both during planning and immediately before reservation/invocation.
- Routine diagnostics invoke no search provider and consume no monetary or quota-bearing request.
- RRF uses exact rational `1 / (60 + provider_rank + 1)` contributions and the accepted five-part deterministic tie-break.
- Search-only duplicate clustering uses only the conservative document key; weak URL/title/snippet hints never merge evidence.
- Extraction evaluates at most `16` steps; a composition contains at most `200` result links.
- Rejected or diagnostic-only extraction content never becomes a citation, source document, synthesis input, delivery, or positive cache entry.
- Archive fallback is lookup-only by default. Submitting a URL to create an external archive requires a distinct typed policy, explicit irreversible-action authority, and a human gate.
- Durable authority acceptance precedes cache publication and caller acknowledgment.
- The version-2 HTTP envelope is exactly `contract_version`, `outcome`, `request_id`, `result`, and `error`.
- MCP request bodies are capped at `4 MiB`; HTTP-authority responses consumed by MCP/CLI are capped at `11 MiB`.
- MCP transport sessions are process-local, principal-bound, cryptographically random, capped at `256`, and expire after `30` idle minutes.
- No presenter imports or calls providers, extractors, cache-fill code, persistence mappers, or rejection classifiers.
- No mixed execution, persistence, classification, or presentation authority may be deployed.
- No manual result labeling, owner research queue, provider-credit purchase, destructive cleanup, secret rotation, broad rewrite, production LLM dependency, vector database, or silent stale fallback.
- Every implementation slice starts from its predecessor's accepted commit, uses an isolated worktree, runs tests first, and publishes one non-duplicate review PR for that slice.
- Human merge and protected-environment deployment approval remain gates. They are not requests for the owner to research or grade results.

---

## Authoritative Inputs

The implementer must read these before changing code:

1. `docs/scorecards/stability-competitive.md`
2. `docs/research/2026-07-26-provider-extraction-contract-drift.md`
3. `docs/adr/0002-bounded-retrieval-plan-cache-identity.md`
4. `docs/adr/0003-provider-aware-freshness-provenance-ranking.md`
5. `docs/adr/0004-no-spend-provider-readiness.md`
6. `docs/adr/0005-structured-extraction-outcome-composition.md`
7. `docs/adr/0006-http-mcp-compatibility-contract.md`
8. `docs/prototypes/retrieval-evidence-envelope/NOTES.md`
9. `docs/prototypes/retrieval-evidence-envelope/vectors.json`
10. The exact merged revisions of PRs #71, #74, #75, #76, #77, #78, #79, and #80.

If a named dependency has not merged, the next slice remains stacked on its
review branch. It must not copy the dependency into a second PR.

### Planning-time gate snapshot

Verified on 2026-07-27:

- #40 is closed with PostgreSQL/recovery evidence recorded.
- #41, #42, and #44 remain open operational promotion gates.
- Argus PRs #71 and #74 through #80 are open, mergeable, and form the accepted
  design/prototype dependency stack.
- The branch baseline passes `880` tests with `37` skips.

This is sequencing evidence, not permission to deploy. Every implementation
session refreshes its dependency PRs, and P1 refreshes all live operational
evidence before mutation. A stale issue state or old green check never
satisfies a production gate.

## Dependency Graph

```text
S0 contract kernel and frozen fixtures
  ├── S1 deterministic retrieval plan
  ├── S2 provider batch normalization
  └── S3 extraction finalizer/composer

S1 + S2
  ├── S4 exact evidence fusion
  └── S5 provider readiness authority

S1 + S4 + S5
  └── S6 accepted retrieval, durable evidence, and cache

S3 + S6
  └── S7 accepted-operation orchestration and legacy presenters

S7
  └── S8 HTTP v2, transport security, and capabilities
        └── S9a capability negotiation
              └── S9b MCP transport/session security
                    └── S9c versioned MCP tools
                          └── S9d CLI negotiation
                                └── S9e workflow composition
                                      └── S10 hermetic scorecard and release candidate bundle
                                            └── P1 immutable homelab port and scorecard promotion
```

S1, S2, and S3 may be developed in parallel only in distinct worktrees. Their
PRs merge in the numbered order so later shared-file conflicts remain
mechanical. S4 through P1 are strictly serial.

## File Map

### New deep-module files

- `argus/contracts/outcomes.py` — canonical outcomes, bounded error facts, and `AcceptedOperation`.
- `argus/broker/planning.py` — `RetrievalControls`, `RetrievalPlan`, deterministic identities, and plan validation.
- `argus/broker/provider_evidence.py` — normalized provider batch/result/failure evidence and legacy tuple adapter.
- `argus/broker/fusion.py` — freshness, conservative document identity, exact RRF, representative choice, diversity, and structural floors.
- `argus/broker/readiness.py` — readiness observations/snapshots, probe authorization, and execution decisions.
- `argus/broker/accepted.py` — accepted retrieval orchestration and immutable evidence projection.
- `argus/extraction/outcomes.py` — accepted extraction facts, closed step taxonomy, artifact dispositions, and terminal causes.
- `argus/extraction/finalizer.py` — one-time classification and durable extraction acceptance.
- `argus/extraction/composition.py` — pure artifact-floor composition.
- `argus/persistence/readiness.py` — readiness observation/snapshot repository and durable half-open leases.
- `argus/persistence/evidence.py` — normalized retrieval/extraction evidence repository and acceptance receipts.
- `argus/api/contracts_v2.py` — Pydantic envelope/result/error schemas.
- `argus/api/presenters.py` — legacy and evidence HTTP presenters.
- `argus/api/security.py` — pre-execution Host/Origin/CORS and credential-carrier guard.
- `argus/api/routes_v2.py` — `/api/v2/search`, `/api/v2/recover-url`, `/api/v2/expand`, and `/api/v2/extract`.
- `argus/mcp/capabilities.py` — fail-closed capability negotiation and the bounded cache.
- `argus/mcp/sessions.py` — bounded principal-owned transport-session registry.
- `argus/mcp/v2_tools.py` — explicit `*_v2` tools and exact structured-content presenters.
- `argus/scorecard/__init__.py` — scorecard package marker.
- `argus/scorecard/corpus.py` — immutable corpus/identity loader.
- `argus/scorecard/stability.py` — hard-gate evaluator.
- `argus/scorecard/competitive.py` — reversed-order pair classification and exact sign-test verdict.
- `argus/scorecard/bundle.py` — secret-free checksummed evidence bundle writer/verifier.

### New tests and fixtures

- `tests/fixtures/contracts/retrieval_evidence_v2/` — frozen valid and invalid envelopes ported from issue #65.
- `tests/fixtures/providers/<provider>/` — first-party-shape success, empty, applicable auth/exhaustion/policy/timeout/rate-limit/5xx failures, malformed, usage, and freshness fixtures for all 14 providers.
- `tests/fixtures/extractors/crawl4ai/` — locked-result and malformed-result compatibility fixtures.
- `tests/fixtures/extractors/firecrawl/` — disabled-by-default v2 success/error compatibility fixtures.
- `tests/fixtures/transports/v1/` — frozen legacy HTTP/MCP golden responses.
- `tests/fixtures/transports/v2/` — version-2 envelopes for every canonical outcome.
- `tests/fixtures/scorecard/` — 24 search intents, 12 extraction cases, paired evaluator outputs, and bundle identities.
- `tests/test_contract_outcomes.py`
- `tests/test_retrieval_planning.py`
- `tests/test_provider_evidence.py`
- `tests/test_evidence_fusion.py`
- `tests/test_provider_readiness.py`
- `tests/test_accepted_retrieval.py`
- `tests/test_extraction_outcomes.py`
- `tests/test_extraction_composition.py`
- `tests/test_transport_v2.py`
- `tests/test_transport_security.py`
- `tests/test_mcp_v2.py`
- `tests/test_capability_negotiation.py`
- `tests/test_scorecard.py`
- `tests/test_evidence_bundle.py`
- `tests/test_architecture_boundaries.py`

### Persistence migrations

- `migrations/versions/0007_extraction_outcomes.py` — extraction plans/steps, artifacts/dispositions, exact rejection projection, result links, and composition receipt.
- `migrations/versions/0008_provider_readiness.py` — observations, materialized snapshots, lease/fencing state, and bounded evidence references.
- `migrations/versions/0009_retrieval_evidence.py` — plans, normalized batches/observations, clusters/contributions, cache lineage, accounting, and accepted operation identity.

The migrations are additive. They neither rewrite legacy rows nor import the
legacy SQLite database. Downgrade removes only the new, unused schema while
the release flag is still disabled. After version-2 evidence has been accepted
in production, rollback is an image rollback with schema retained; destructive
downgrade is forbidden.

## Slice Decision Matrix

| Slice | Independently accepted evidence | Compatibility and rollback boundary | Owner decision still required | Promotion-gate timing |
|---|---|---|---|---|
| S0 Contract kernel | Frozen v1/v2 fixtures, exhaustive outer outcome maps, prototype source/copy hashes | New internal package only; prototype remains executable | Human PR merge only | Allowed before #40/#41/#42/#44 |
| S1 Retrieval plan | ADR 0002 identity vectors, deadline/canonicalization tests | External `SearchQuery` unchanged; planner unused until S7 | Human PR merge only | Allowed before operational gates |
| S2 Provider evidence | 14-adapter applicability matrices, Crawl4AI/Firecrawl fixtures, and secret-leak sentinels | `LegacyProviderBatchAdapter` preserves tuple callers | Human PR merge only; live paid probes excluded | Allowed before operational gates |
| S3 Extraction outcome | Exact one-time rejection/finalization/composition tests plus default archive lookup-only proof | Existing `ExtractedContent` remains legacy projection; no historical replay or archive creation | Human PR merge only; archive creation requires a separate irreversible-action approval | Allowed before operational gates |
| S4 Evidence fusion | Exact rational ordering, freshness, duplicate, diversity, floor tests | Legacy `ranking.py` remains active until S7 | Human PR merge only | Allowed before operational gates |
| S5 Readiness authority | Repository-time expiry, no-spend diagnostics, leases, terminal exhaustion, concurrency | New tables additive; legacy trackers are migration inputs only | Human PR merge only; account reconciliation remains operator-gated | Code/migration allowed before gates; production migration only in P1 |
| S6 Accepted retrieval/cache | Atomic acceptance, immutable hits, origin spend, no cache on failure, timeout tests | New cache unused until S7; old cache remains active | Human PR merge only | Code/migration allowed before gates; production activation only in P1 |
| S7 Accepted operation/v1 | One execution feeds legacy presenters; production modules replay all prototype invariants; architecture test forbids secondary authority | Legacy byte semantics frozen; one release flag selects the complete path; throwaway executable retires only after replay | Human PR merge only | Allowed before operational gates; flag remains off in production |
| S8 HTTP v2/security | Every status/envelope, auth, Host/Origin, body bound, session ownership tests | `/api/*` remains; `/api/v2/*` additive; security rejection may tighten unsafe behavior | Human PR merge only | Allowed before gates; remote production enablement only in P1 |
| S9a Capability negotiation | Origin/deployment-scoped cache and no speculative/fallback POST | Resolver unused until adapters port | Human PR merge only | Allowed before gates |
| S9b MCP transport | Protocol/media/session concurrency and secured legacy SSE | Existing tool schemas unchanged | Human PR merge only | Allowed before gates; remote exposure only in P1 |
| S9c MCP tools | Frozen v1 tools and exact v2 structured content/error layers | Remove new `*_v2` registrations; legacy tools remain | Human PR merge only | Allowed before gates; client cutover only in P1 |
| S9d CLI | Exact negotiation/stdout/stderr/exit suite | Legacy-server selection remains supported | Human PR merge only | Allowed before gates; installed-client cutover only in P1 |
| S9e Workflows | Durable total extraction links and no rejected-content delivery | Activation remains under the atomic evidence-authority setting | Human PR merge only | Allowed before gates; external delivery canaries only in P1 |
| S10 Scorecard candidate | Hermetic stability bundle and reproducible candidate bundle verifier | Diagnostic tooling only; no runtime authority | Human PR merge only | Allowed before gates; live competitive lane only for P1 |
| P1 Production port | Immutable digest, migration receipt, canaries, stable profiles, competitive verdict, rollback proof | Roll back exact digest without deleting new schema; legacy v1 remains readable | Protected deployment approval and human merge; each budgeted live run also needs a bounded run authorization; destructive cleanup separately prohibited | Only after #40, #41, #42, and #44 are satisfied with current evidence |

There are no unresolved design questions in S0 through S10. Implementation
agents decide ordinary code organization within the file boundaries above.
The owner is not assigned research, result labeling, or manual benchmark work.

---

### Task 1: S0 — Contract kernel and frozen compatibility fixtures

**Inputs:** Scorecard canonical outcomes, ADR 0005 extraction outcomes, ADR 0006 status/error rules, and all eight issue #65 prototype vectors.

**Files:**
- Create: `argus/contracts/__init__.py`
- Create: `argus/contracts/outcomes.py`
- Create: `tests/test_contract_outcomes.py`
- Create: `tests/test_architecture_boundaries.py`
- Create: `tests/fixtures/contracts/retrieval_evidence_v2/*.json`
- Create: `tests/fixtures/contracts/retrieval_evidence_v2/manifest.json`
- Create: `tests/fixtures/transports/v1/*.json`
- Create: `tests/fixtures/transports/v2/*.json`
- Modify: `docs/prototypes/retrieval-evidence-envelope/README.md`

**Interfaces:**
- Produces: `CanonicalOutcome`, `OperationError`, `AcceptedOperation[T]`, `is_success_like()`, `http_status_for()`, and `mcp_is_error_for()`.
- Consumes: no product execution module.

- [ ] **Step 1: Freeze current legacy responses before adding a presenter**

Capture the existing hermetic outputs from `tests/test_api.py`,
`tests/test_http_authority.py`, and `tests/test_mcp_pack_tools.py` into named
JSON fixtures. Each fixture records route/tool name, input, HTTP/tool status,
and response value. Run:

```bash
uv run pytest tests/test_api.py tests/test_http_authority.py \
  tests/test_mcp_pack_tools.py -q
```

Expected: zero failures and no provider network access.

- [ ] **Step 2: Write the exhaustive outcome tests**

The test module declares this exact table:

```python
EXPECTED = {
    "success": (200, False),
    "degraded": (200, False),
    "empty": (200, False),
    "invalid_request": (422, True),
    "authentication_rejected": (401, True),
    "policy_rejected": (403, True),
    "timeout": (504, True),
    "persistence_failed": (503, True),
    "providers_failed": (502, True),
    "extraction_failed": (502, True),
    "unready": (503, True),
}
```

The table is the default accepted-operation mapping. Add exact narrow
transport-code cases from ADR 0006:
`malformed_request=400`, `payload_too_large=413`,
`unsupported_media_type=415`, `route_not_found=404`,
`idempotency_conflict=409`, `session_not_found=404`,
`rate_limited=429`, and `internal_failure=503`. Assert enum iteration equals
the canonical table keys, success-like outcomes have
`error is None` and an object result, failure outcomes have an error, and
`request_id` is bounded before `AcceptedOperation` construction succeeds.

- [ ] **Step 3: Implement the immutable contract types**

Use frozen dataclasses and a closed enum. `AcceptedOperation.__post_init__`
rejects contradictory outcome/result/error combinations. `OperationError`
stores only stable type/title/status/detail/instance/code/retry fields and
validates `status == http_status_for(outcome, code)`. Unknown code/outcome
combinations fail closed; the narrow transport codes never reclassify an
operation that already began.

- [ ] **Step 4: Port the prototype fixtures**

Copy the eight valid vectors and nineteen invalid mutations into standalone
fixture files without importing `docs/prototypes/.../model.py`. Add a fixture
manifest with SHA-256 for every source and copied fixture. At this slice, the
tests:

1. verify manifest hashes;
2. load every valid and invalid vector as immutable fixture data;
3. apply only the S0 outer outcome/error/request-ID invariants; and
4. reject forbidden keys/text recursively.

The complete nineteen-mutation replay remains owned by S7, after S3/S4/S6
production modules exist. Until then, the original prototype command remains
green and executable. The production contract package may reuse bounded value
objects, but it must not import the throwaway validator or JSON Schema.

- [ ] **Step 5: Add the architecture import test**

Parse the AST of `argus/api/presenters.py` when it exists and fail if it imports
from `argus.providers`, `argus.extraction.extractor`,
`argus.persistence`, `argus.broker.cache`, or the #57 classifier. Keep the
test skipped only while the presenter file does not exist; S7 removes that
skip.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/test_contract_outcomes.py \
  tests/test_architecture_boundaries.py -q
uv run pytest -q
git diff --check
git add argus/contracts tests/fixtures/contracts tests/fixtures/transports \
  tests/test_contract_outcomes.py tests/test_architecture_boundaries.py \
  docs/prototypes/retrieval-evidence-envelope/README.md
git commit -m "feat: freeze accepted operation contracts"
```

**Acceptance evidence:** Exhaustive outcome/status/MCP mapping, immutable
constructor invariants, v1 goldens, hash-frozen envelope source/copies,
outer-contract subset checks, original prototype still green, and full suite
green.

**Rollback boundary:** Internal types and fixtures are unused by runtime.

**Human gate:** Merge only. No deployment decision.

---

### Task 2: S1 — Deterministic retrieval planning and identities

**Inputs:** ADR 0002 and S0 outcome types.

**Files:**
- Create: `argus/broker/planning.py`
- Create: `tests/test_retrieval_planning.py`
- Modify: `argus/broker/policies.py`
- Modify: `argus/broker/router.py`

**Interfaces:**
- Produces:
  `resolve_plan(effective_query, controls, include_attribution, policy_snapshot, utc_clock) -> RetrievalPlan`.
- Produces immutable `RetrievalControls`, `FreshnessWindow`,
  `DomainConstraints`, `ExecutionPolicySnapshot`, and `RetrievalPlan`.
- Consumes existing `SearchQuery`, `SearchMode`, `ProviderName`, provider tiers,
  routing policy, and caller tier caps.

- [ ] **Step 1: Write ADR 0002 vector tests**

Parameterize every row in ADR 0002's plan/cache identity table. Assert the exact
resolved plan order and whether `plan_id` and `cache_fingerprint` remain equal
or differ. Include exact expected SHA-256 values generated from fixed canonical
JSON fixtures so a later serializer change cannot silently move identity.

- [ ] **Step 2: Write invalid-control and boundary tests**

Cover empty normalized query, boolean/non-integer/0/51 result limits, the
synthetic cache provider, mixed relative/explicit freshness, reversed dates,
invalid domains, domain overlap, IPv4/IPv6 constraints, overlong deadline, and
unknown typed enum values. Each returns `invalid_request` without routing,
cache, reservation, or provider calls.

- [ ] **Step 3: Implement canonicalization and hashing**

Implement NFC query normalization with trimmed/collapsed Unicode whitespace,
IDNA host normalization, stable tier sorting, exact relative date windows from
an injected UTC clock, compact sorted-key JSON, and the exact
`argus-plan-v1\0` / `argus-cache-v1\0` hash prefixes.

- [ ] **Step 4: Resolve plans before cache lookup**

Change `SearchBroker.search()` to create a plan before calling the current
pipeline. Do not change runtime cache/execution behavior yet. Pass the plan as
an unused explicit argument through `SearchResultPipeline` and
`ProviderExecutor`; tests fail if either path accepts a request without a
validated plan.

- [ ] **Step 5: Add deadline scaffolding**

Create one monotonic operation deadline value on entry, compute the
provider-phase deadline with the fixed 5000ms reserve, and inject it without
changing current provider invocation. A fake clock proves no plan exceeds
120000ms.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/test_retrieval_planning.py tests/test_broker.py \
  tests/test_caller_caps.py -q
uv run pytest -q
git diff --check
git add argus/broker/planning.py argus/broker/policies.py \
  argus/broker/router.py tests/test_retrieval_planning.py
git commit -m "feat: resolve deterministic retrieval plans"
```

**Acceptance evidence:** Every ADR identity vector and invalid-input negative
case passes; no transport shape changes; full suite green.

**Rollback boundary:** Planner is internally required but current execution
outputs remain unchanged. Revert this slice without data migration.

**Human gate:** Merge only.

---

### Task 3: S2 — Provider batch normalization and private failure evidence

**Inputs:** Drift inventory, provider signal matrix, ADR 0003 normalized
evidence, ADR 0004 failure taxonomy, and S1 plan controls.

**Files:**
- Create: `argus/broker/provider_evidence.py`
- Create: `tests/test_provider_evidence.py`
- Create: `tests/test_extractor_contracts.py`
- Create: `tests/fixtures/providers/<provider>/*.json`
- Create: `tests/fixtures/providers/manifest.json`
- Create: `tests/fixtures/extractors/crawl4ai/*.json`
- Create: `tests/fixtures/extractors/firecrawl/*.json`
- Modify: `argus/providers/base.py`
- Modify: `argus/providers/searxng.py`
- Modify: `argus/providers/duckduckgo.py`
- Modify: `argus/providers/yahoo.py`
- Modify: `argus/providers/github.py`
- Modify: `argus/providers/wolfram.py`
- Modify: `argus/providers/brave.py`
- Modify: `argus/providers/tavily.py`
- Modify: `argus/providers/exa.py`
- Modify: `argus/providers/linkup.py`
- Modify: `argus/providers/parallel.py`
- Modify: `argus/providers/serper.py`
- Modify: `argus/providers/you.py`
- Modify: `argus/providers/valyu.py`
- Modify: `argus/providers/searchapi.py`
- Modify: `argus/provider_controls.py`
- Modify: `argus/extraction/crawl4ai_extractor.py`
- Modify: `argus/extraction/firecrawl_extractor.py`

`argus/providers/valyu_answer.py` is not one of the 14 search adapters and is
not changed in this slice.

**Interfaces:**
- Produces `ProviderSearchBatch`, `ResultObservation`,
  `ProviderRequestEvidence`, `ProviderResponseEvidence`,
  `ProviderFailure`, and `LegacyProviderBatchAdapter`.
- Provider adapters emit only `ProviderSearchBatch`; the temporary adapter
  accepts the legacy tuple while migration is incomplete.
- Consumes validated plan controls and per-attempt timeout.

- [ ] **Step 1: Build the 14-provider fixture manifest**

For every registered provider, add current success, valid empty, malformed
success, usage/rate evidence, and freshness translation fixtures. Maintain a
machine-readable applicability matrix for `401`, `402`/balance exhaustion,
`403`, `408`/`504`, `422`, `429`, and `5xx`; every cell names a fixture or an
authoritative reason the provider cannot emit that class. A generic
“documented error” fixture does not satisfy the matrix. Add migration aliases
only for Exa `published_date`, Parallel `excerpt`/`snippet`, and SearchAPI
`organic` where the inventory authorizes dual-read. The test fails when a
registered provider or applicable failure class lacks a fixture.

- [ ] **Step 2: Write privacy and bounds tests**

Inject authorization, signed-URL, cookie, raw-body, environment-path,
unbounded-message, NaN, infinity, malformed-date, duplicate-position, and
unknown-source sentinels. Assert none crosses the batch seam or safe log
record, while the batch retains bounded typed request IDs, warnings, usage,
cost, rate reset, publication evidence, and native score semantics.

- [ ] **Step 3: Implement normalized evidence types**

Use frozen bounded values and the closed evidence/source/snippet/failure enums
from ADRs 0003 and 0004. Provider rank is unique, zero-based returned-array
order after structurally invalid rows are removed. Unknown provider fields
remain private and are not copied into generic metadata.

- [ ] **Step 4: Port providers in deterministic groups**

Port and test in this order:

1. SearXNG, DuckDuckGo, Yahoo, GitHub, Wolfram;
2. Brave, Tavily, Exa, Linkup;
3. Parallel, Serper, You, Valyu, SearchAPI.

Correct Parallel `/v1/search`, Exa field casing/bounded contents request, and
Crawl4AI's locked result object. Correct dormant Firecrawl v2 parsing but keep
Firecrawl disabled behind its existing spend/config gate.

- [ ] **Step 5: Add extractor compatibility fixtures**

Freeze Crawl4AI locked markdown-object success/malformed shapes and Firecrawl
v2 success/error shapes. Prove Firecrawl remains disabled without its explicit
config/spend authorization and that fixture tests make no external request.
Run these through `tests/test_extractor_contracts.py` and the existing
extraction suite.

- [ ] **Step 6: Translate typed controls**

Each adapter declares `none`, `relative_only`, `date_range`,
`relative_and_date_range`, or `query_qualifier`, records exact/widened/
unsupported precision and strict/best-effort/unknown strength, and rejects a
required unsupported control rather than silently dropping it.

- [ ] **Step 7: Verify and commit**

```bash
uv run pytest tests/test_provider_evidence.py tests/test_extractor_contracts.py \
  tests/test_providers.py tests/test_extraction.py -q
uv run pytest -q
git diff --check
git add argus/broker/provider_evidence.py argus/providers \
  argus/provider_controls.py argus/extraction/crawl4ai_extractor.py \
  argus/extraction/firecrawl_extractor.py tests/test_provider_evidence.py \
  tests/test_extractor_contracts.py tests/fixtures/providers \
  tests/fixtures/extractors
git commit -m "feat: normalize provider evidence batches"
```

**Acceptance evidence:** Complete 14-provider applicability/fixture matrix for
success, empty, malformed, auth, exhaustion, policy, timeout, rate-limit, and
5xx behavior; Crawl4AI/Firecrawl fixture contracts; stable failure taxonomy;
current request shapes; intentional aliases only; and secret/bounds sentinels
rejected.

**Rollback boundary:** `LegacyProviderBatchAdapter` preserves the old broker
tuple while no runtime consumer requires the new batch.

**Human gate:** Merge only. No credentialed or paid probe is part of this
slice.

---

### Task 4: S3 — Extraction finalization and retrieval composition

**Inputs:** Merged PR #74, ADR 0005, S0 outcomes, and issue #65 extraction
vectors.

**Files:**
- Create: `argus/extraction/outcomes.py`
- Create: `argus/extraction/finalizer.py`
- Create: `argus/extraction/composition.py`
- Create: `tests/test_extraction_outcomes.py`
- Create: `tests/test_extraction_composition.py`
- Modify: `argus/extraction/models.py`
- Modify: `argus/extraction/extractor.py`
- Modify: `argus/extraction/archive_extractor.py`
- Modify: `argus/extraction/cache.py`
- Modify: `argus/persistence/search_ledger.py`
- Create: `migrations/versions/0007_extraction_outcomes.py`

**Interfaces:**
- Produces:
  `finalize_extraction(extraction_request, extraction_plan, raw_extractor_result, outcome_policy) -> AcceptedExtractionOutcome`.
- Produces:
  `compose_retrieval_evidence(accepted_retrieval, result_extraction_links, artifact_requirement) -> RetrievalComposition`.
- Consumes the #57 rejection mapper exactly once and a durable repository.
- Defines a narrow `RetrievalEvidenceView` protocol (`outcome`,
  `result_cluster_refs`, and `acceptance_receipt`) so this slice does not
  import the future S6 implementation. S7 supplies the concrete adapter.

- [ ] **Step 1: Write the closed taxonomy and truth-table tests**

Parameterize every ADR 0005 attempt outcome, terminal mapping,
quality/completeness row, artifact disposition, and composite precedence row.
Assert fallback success retains failed steps but has no final rejection.
Assert no autonomous path emits `manual_review`.

- [ ] **Step 2: Write trace-integrity and privacy tests**

Reject dangling/duplicate/mismatched ordinals, inconsistent exhausted sets,
over-16 steps, over-bound IDs/labels/signals/latency/cost, raw errors, URLs,
content, credentials, and request bodies inside rejection evidence.

- [ ] **Step 3: Separate archive lookup from external archive creation**

Write tests that return an archive lookup miss and instrument every HTTP POST
to fail. The default extraction chain must return a typed miss/fallback without
submitting the caller URL to archive.ph/archive.is or any other external
archive. If an archive-creation function remains available, it accepts a
distinct `ArchiveCreationAuthorization` carrying caller policy, authority
receipt, idempotency key, and bounded target; absence or reuse is
`policy_rejected` before network work. No autonomous profile constructs that
authorization, and no test performs a live submission.

- [ ] **Step 4: Implement immutable extraction plan/outcome values**

Separate cache decisions, extractor execution outcomes, artifact quality,
completeness, terminal cause, selected extractor, causative rejection provider,
provenance, and spend. Do not overload `ExtractionAttempt.status` with final
quality semantics at the new seam.

- [ ] **Step 5: Implement the finalizer and composer**

The finalizer validates, calls #57 once when required, chooses disposition and
canonical outcome, atomically persists exact facts/projection, and returns the
receipt-bearing object. The pure composer validates unique cluster refs,
exactly one link per selected cluster, per-result and aggregate floors,
readiness possibility, and the accepted precedence table. Multiple links may
reference one artifact/extraction only when they carry the same proven
artifact identity, access scope, policy versions, and explicit reuse origin;
otherwise duplicate artifact/extraction references are rejected. This
preserves ADR 0005's authorized artifact reuse without allowing an accidental
many-to-one link to hide a missing extraction.

- [ ] **Step 6: Add the migration and repository tests**

Migration 0007 creates additive plan/step/artifact/rejection/link/composition
tables. Foreign keys close extraction-plan, step, artifact, rejection, and
acceptance identities that exist in this slice; uniqueness closes one link per
selected cluster. `result_cluster_ref` remains a bounded opaque accepted
reference until S6 introduces the retrieval tables, and the composer validates
it against `RetrievalEvidenceView` before acceptance rather than declaring a
foreign key to a future table. Test SQLite upgrade from 0006, PostgreSQL
upgrade/rollback in the disposable database fixture, atomic rollback on fault
injection, idempotent retry by run ID, and no legacy row rewrite/replay.

- [ ] **Step 7: Keep legacy extraction readable**

Project `AcceptedExtractionOutcome` back to the existing `ExtractedContent`
fields for legacy callers. The old top-level completeness
`recommended_action` remains distinct from #57 rejection action. Runtime
activation waits for S7.

- [ ] **Step 8: Verify and commit**

```bash
uv run pytest tests/test_extraction_outcomes.py \
  tests/test_extraction_composition.py tests/test_extraction.py \
  tests/test_search_ledger.py -q
uv run pytest -q
git diff --check
git add argus/extraction argus/persistence/search_ledger.py \
  migrations/versions/0007_extraction_outcomes.py \
  tests/test_extraction_outcomes.py tests/test_extraction_composition.py
git commit -m "feat: finalize and compose extraction outcomes"
```

**Acceptance evidence:** Exhaustive mappings, one-time classification,
durable identity, composition precedence, privacy bounds, cache isolation, and
no fabricated receipt, plus a lookup-miss test proving the default chain
performs no external archive-creation POST.

**Rollback boundary:** Additive schema and legacy projection; new tables are
unused in production until P1.

**Human gate:** Merge only. No extraction wave, historical replay, or archive
creation. A future archive submission is a separately authorized irreversible
external action and is outside this plan.

---

### Task 5: S4 — Exact provider-aware evidence fusion

**Inputs:** S1 retrieval plan, S2 provider batches, and ADR 0003.

**Files:**
- Create: `argus/broker/fusion.py`
- Create: `tests/test_evidence_fusion.py`
- Modify: `argus/broker/pipeline.py`
- Modify: `argus/models.py`

**Interfaces:**
- Produces:
  `fuse_evidence(retrieval_plan, provider_batches, fusion_policy, utc_clock) -> FusionOutcome`.
- Consumes only normalized batches, never provider-native responses.

- [ ] **Step 1: Write freshness proof tests**

Cover inclusive day/week/month/year/date edges, precision intervals,
conflicting claims, unverified/modified/indexed/result-text claims, widened
translation with exact post-filter, strict empty, unproven empty, and
`freshness_unproven`.

- [ ] **Step 2: Write conservative identity tests**

Prove default ports and safe percent/dot normalization, while HTTP/HTTPS, path
case, trailing slash, query order, duplicate/key-only parameters, `www`,
tracking hints, title/snippet similarity, and provider canonical hints remain
distinct without accepted proof.

- [ ] **Step 3: Write exact ranking tests**

Use `fractions.Fraction`, `k=60`, one contribution per provider, retained
provider ranks, and exact expected numerators/denominators. Include the
7/11/29 floating-point counterexample, every provider-map permutation, all five
tie-breaks, representative selection, and attribution float tolerance
`1e-15`.

- [ ] **Step 4: Write diversity/floor tests**

Discovery/grounding/recovery preserve base order. Research runs the exact
coverage/fill/relax passes, two-per-site soft cap, pinned PSL site keys, and
scaled `min(3, result_limit)` / `min(2, required_clusters)` floor.

- [ ] **Step 5: Implement pure bounded fusion**

Normalize/filter, cluster, rank, diversify, and produce the full immutable
trace. No network import or async function is permitted. Slow-fusion fake
tests consume the operation's remaining deadline and return timeout evidence
without persistence/cache publication.

- [ ] **Step 6: Add a compatibility projection**

Render representative URL/title/snippet/provider and derived float score into
the existing `SearchResult` without mutating `FusionOutcome`. Keep
`argus/broker/ranking.py` and `dedupe.py` as legacy wrappers until S7.

- [ ] **Step 7: Verify and commit**

```bash
uv run pytest tests/test_evidence_fusion.py tests/test_broker.py \
  tests/test_attribution.py -q
uv run pytest -q
git diff --check
git add argus/broker/fusion.py argus/broker/pipeline.py argus/models.py \
  tests/test_evidence_fusion.py
git commit -m "feat: fuse provider evidence deterministically"
```

**Acceptance evidence:** Exact reproducible ranking, fail-closed freshness,
conservative duplicates, deterministic diversity/floors, no network/spend,
and legacy projection parity.

**Rollback boundary:** Legacy ranking wrappers remain selectable until S7.

**Human gate:** Merge only.

---

### Task 6: S5 — Provider readiness as the only decision authority

**Inputs:** S1 plan candidates, S2 private failure evidence, ADR 0004, existing
spend repository, health tracker, and reachability matrix.

**Files:**
- Create: `argus/broker/readiness.py`
- Create: `argus/persistence/readiness.py`
- Create: `migrations/versions/0008_provider_readiness.py`
- Create: `tests/test_provider_readiness.py`
- Modify: `argus/broker/execution.py`
- Modify: `argus/broker/router.py`
- Modify: `argus/broker/health.py`
- Modify: `argus/broker/reachability.py`
- Modify: `argus/persistence/provider_spend.py`
- Modify: `argus/api/routes_health.py`
- Modify: `argus/operations/status.py`

**Interfaces:**
- Produces `snapshot()`, `authorize_probe()`, and `record_observation()`.
- Produces one immutable `ProviderReadinessSnapshot` and one closed
  `ExecutionDecision`.
- Consumes legacy trackers only as normalized observations; no surface or
  executor composes them independently.

- [ ] **Step 1: Write observation/snapshot tests**

Cover catalog, registration, configuration issue sets, reachability,
compatibility, usability, cooldown, spend, healthy derivation, scope
fingerprints, authority-clock ingestion/expiry, 30-second future skew, earliest
`valid_until`, five-second renderer cache, 32-receipt compaction, 32 executable
scopes, and fail-closed overflow.

- [ ] **Step 2: Write probe/no-spend tests**

Instrument every provider adapter and reservation method to fail if invoked.
Run doctor, health, provider-health HTTP, MCP check, container liveness,
dashboard, and background refresh. Only fixtures, local components, and
versioned no-spend account probes may execute.

- [ ] **Step 3: Write concurrency and terminal-spend tests**

Use two repository instances against the same database. Prove one
compare-and-set half-open lease, monotonic fencing, no replacement merely from
deadline expiry, late stale-token settlement rejection, uncertain charge
blocking, durable one-time exhaustion across restart, documented recurring
reset, and one deduplicated operator alert.

- [ ] **Step 4: Add readiness persistence**

Migration 0008 creates observations, materialized snapshots, evidence refs,
leases, and alert-dedupe state. Repository transactions use database time,
not process time, for expiry and fencing. Every provider-attempt/spend
transaction records normalized observations and rematerializes the snapshot.

- [ ] **Step 5: Split catalog and executable registry**

Keep all 14 providers in the catalog. Register non-free providers only with
enabled config, credential presence, stable account binding, finite budget,
durable spend repository, and compatible fixture release. Emit a diagnostic
reason for every non-registered provider.

- [ ] **Step 6: Move execution and rendering to the service**

`ProviderExecutor` asks readiness, then atomically rechecks free-only, caller
tier, cooldown, and spend before invocation. HTTP/CLI/MCP/dashboard read the
same snapshot. Add an architecture test that fails on new direct semantic
reads of `HealthTracker`, `ReachabilityMatrix`, or `BudgetTracker`.

- [ ] **Step 7: Verify migrations and commit**

```bash
uv run pytest tests/test_provider_readiness.py tests/test_reachability.py \
  tests/test_provider_spend.py tests/test_operational_diagnostics.py -q
uv run pytest -q
git diff --check
git add argus/broker/readiness.py argus/persistence/readiness.py \
  migrations/versions/0008_provider_readiness.py argus/broker/execution.py \
  argus/broker/router.py argus/broker/health.py argus/broker/reachability.py \
  argus/persistence/provider_spend.py argus/api/routes_health.py \
  argus/operations/status.py tests/test_provider_readiness.py
git commit -m "feat: centralize provider readiness authority"
```

**Acceptance evidence:** No-spend diagnostics, scoped expiring truth,
distributed lease/fencing, terminal exhaustion, double free-only gate,
catalog/registry split, and no alternate status authority.

**Rollback boundary:** Additive tables; legacy trackers remain invocation
mechanics but cannot authorize or render independently.

**Human gate:** Merge only. Reconciliation of a real exhausted account remains
an operator action, not an automatic probe.

---

### Task 7: S6 — Accepted retrieval, durable evidence, and immutable cache

**Inputs:** S1 plans, S4 fusion, S5 readiness, issue #65 lineage invariants,
and existing search/spend ledgers.

**Files:**
- Create: `argus/broker/accepted.py`
- Create: `argus/persistence/evidence.py`
- Create: `migrations/versions/0009_retrieval_evidence.py`
- Create: `tests/test_accepted_retrieval.py`
- Modify: `argus/broker/cache.py`
- Modify: `argus/broker/execution.py`
- Modify: `argus/broker/pipeline.py`
- Modify: `argus/broker/router.py`
- Modify: `argus/persistence/search_ledger.py`

**Interfaces:**
- Produces `AcceptedRetrieval`, `RetrievalEvidence`, `CacheDecision`,
  `CacheEntry`, and `AcceptanceReceipt`.
- Implements exact order:
  plan → cache decision → execution → fusion → durable acceptance → immutable
  cache publication → acknowledgment.

- [ ] **Step 1: Write cache identity/admission tests**

Port every ADR 0002 admission test and issue #65 cache corruption. Prove
eligible paid-origin reuse by free profile with zero new calls/spend, bounded
age, explicit-provider/caller/domain/freshness rejection, complete contributor
lineage, short-lived proven empty, immutable deep-copy hit, and no stale
fallback.

- [ ] **Step 2: Write orchestration/deadline tests**

Use hanging async, blocking-worker, slow-fusion, slow-persistence, failed
leader, same-cohort, and policy-divergent cohort fakes. Assert typed timeout or
persistence failure inside the operation deadline and no unsafe entry.

- [ ] **Step 3: Add normalized evidence persistence**

Migration 0009 adds plans, provider batches/attempts, observations, clusters,
exact contributions, readiness decisions, cache lineage, accounting,
accepted-operation identity, and bounded trace refs. Use foreign keys and
unique constraints to enforce run/link identity. Preserve legacy rows and
acceptance fingerprints.

- [ ] **Step 4: Implement immutable cache storage and admission**

Key by `cache_fingerprint`; single-flight by fingerprint plus execution cohort.
Store accepted immutable facts, not `SearchResponse`. Every hit receives a new
logical run/receipt and current zero-call accounting while retaining origin
attempt/spend/provenance.

- [ ] **Step 5: Implement canonical outcome selection**

Distinguish success, degraded provider execution, proven empty,
freshness-unproven, structural-floor failure, every-provider failure,
unready, timeout, and persistence failure from complete traces. Do not infer
outcome from result count alone.

- [ ] **Step 6: Verify atomicity and commit**

```bash
uv run pytest tests/test_accepted_retrieval.py tests/test_search_ledger.py \
  tests/test_broker.py tests/test_spend_boundaries.py -q
uv run pytest -q
git diff --check
git add argus/broker/accepted.py argus/persistence/evidence.py \
  migrations/versions/0009_retrieval_evidence.py argus/broker/cache.py \
  argus/broker/execution.py argus/broker/pipeline.py argus/broker/router.py \
  argus/persistence/search_ledger.py tests/test_accepted_retrieval.py
git commit -m "feat: accept retrieval evidence before cache publication"
```

**Acceptance evidence:** Issue #65 valid/cache vectors, atomic acceptance,
complete origin lineage, exact accounting, bounded completion, no fabricated
receipt, and concurrency-safe single-flight.

**Rollback boundary:** New cache is not production-active until S7/P1. Old
entries have no valid evidence identity and are intentionally discarded at
activation.

**Human gate:** Merge only.

---

### Task 8: S7 — Accepted-operation orchestration and frozen legacy presenters

**Inputs:** S3 extraction outcomes, S6 retrieval outcomes, S0 legacy fixtures,
and ADRs 0005/0006.

**Files:**
- Create: `argus/api/presenters.py`
- Create: `tests/test_accepted_operations.py`
- Modify: `argus/api/routes_search.py`
- Modify: `argus/api/routes_extract.py`
- Modify: `argus/api/schemas.py`
- Modify: `argus/broker/router.py`
- Modify: `argus/extraction/extractor.py`
- Modify: `argus/config.py`
- Modify: `.env.example`
- Modify: `tests/test_architecture_boundaries.py`
- Delete after production replay passes:
  `docs/prototypes/retrieval-evidence-envelope/model.py`,
  `docs/prototypes/retrieval-evidence-envelope/prototype.py`,
  `docs/prototypes/retrieval-evidence-envelope/envelope.schema.json`, and
  `docs/prototypes/retrieval-evidence-envelope/vectors.json`
- Modify: `docs/prototypes/retrieval-evidence-envelope/README.md`
- Modify: `docs/README.md`

**Interfaces:**
- Produces one `AcceptedOperation` from search/extraction services.
- `LegacyHttpPresenter` projects it to current `/api/*`.
- No presenter executes, persists, classifies, ranks, or retries.

- [ ] **Step 1: Remove the architecture-test skip**

The AST/import test now requires presenters and forbids execution-layer
imports. Add monkeypatch traps proving presenters cannot invoke broker,
extractor, repository, cache, or rejection mapper.

- [ ] **Step 2: Route legacy HTTP through accepted operations**

Each route authenticates, validates, executes once, durably accepts once, then
passes the immutable operation to `LegacyHttpPresenter`. Preserve v1 route,
method, status, field names/types/defaults/nullability, ordering, and error
shape from S0 fixtures.

- [ ] **Step 3: Make the cutover atomic in code**

Add one startup-validated release setting,
`ARGUS_ACCEPTED_OPERATION_AUTHORITY=legacy|evidence`. `evidence` startup fails
unless planner, readiness, evidence repository, extraction finalizer, and all
legacy presenters are registered. No per-route mix is allowed.

- [ ] **Step 4: Prove one execution and one acceptance**

For search, extraction, recovery, expand, sessions, and representative
workflow entry, count provider/extractor/repository calls. Every accepted
request has one execution identity and one acceptance receipt; presenter
failure cannot initiate another execution.

- [ ] **Step 5: Replay the complete issue #65 invariant corpus**

Route each of the eight valid frozen fixtures through the S1/S3/S4/S5/S6/S7
production constructors and validators. Route each of the nineteen corruptions
to its owning production invariant and require the named rejection. This test
must not import or call the prototype validator. It proves cache age/origin
spend, exact ranking/order, composition/link/artifact/citation closure,
extractor provenance, persistence acceptance, and exact caller projection
before the executable proof is removed.

- [ ] **Step 6: Retire the throwaway executable**

After the production replay and source/copy manifest pass, delete the
prototype executable, validator, JSON Schema, and aggregate vector file listed
above. Keep `NOTES.md` as the decision record and the frozen production
fixtures as executable evidence. Update both README entries in the same commit
to point to the production test command; do not attempt to embed the commit's
own not-yet-created hash.

- [ ] **Step 7: Verify and commit**

```bash
uv run pytest tests/test_accepted_operations.py \
  tests/test_architecture_boundaries.py tests/test_api.py \
  tests/test_http_authority.py -q
uv run pytest -q
git diff --check
git add argus/api/presenters.py argus/api/routes_search.py \
  argus/api/routes_extract.py argus/api/schemas.py argus/broker/router.py \
  argus/extraction/extractor.py argus/config.py .env.example \
  tests/test_accepted_operations.py \
  tests/test_architecture_boundaries.py \
  tests/fixtures/contracts/retrieval_evidence_v2 \
  docs/prototypes/retrieval-evidence-envelope docs/README.md
git commit -m "feat: render legacy routes from accepted operations"
```

**Acceptance evidence:** Golden v1 semantics, exactly one execution/acceptance,
no presenter authority, startup rejection of a mixed path, all eight envelope
scenarios accepted, and all nineteen corruptions rejected by production
modules before prototype retirement.

**Rollback boundary:** Production setting stays `legacy` until P1. Reverting
the image restores the old orchestrator without changing legacy wire shapes.

**Human gate:** Merge only.

---

### Task 9: S8 — Version-2 HTTP, capabilities, and transport security

**Inputs:** S7 accepted operations and ADR 0006.

**Files:**
- Create: `argus/capabilities.py`
- Create: `argus/api/contracts_v2.py`
- Create: `argus/api/routes_v2.py`
- Create: `argus/api/security.py`
- Create: `tests/test_transport_v2.py`
- Create: `tests/test_transport_security.py`
- Modify: `argus/api/main.py`
- Modify: `argus/api/lifecycle.py`
- Modify: `argus/api/routes_health.py`
- Modify: `argus/auth.py`
- Modify: `argus/config.py`
- Modify: `argus/authority.py`

**Interfaces:**
- Produces `EvidenceHttpPresenter`, `TransportSecurityGuard`, and authenticated
  `/api/v2/*`.
- Produces an immutable release capability manifest and pure deterministic
  validator in `argus/capabilities.py`; later MCP slices extend the same
  manifest. Each process validates the manifest independently at startup.
- Adds authenticated `http_contracts` / `mcp_contract` capability entries.

- [ ] **Step 1: Write exhaustive envelope/status tests**

For every canonical outcome, assert status, `Argus-Contract-Version: 2.0`,
matching body/header request ID, result/error invariant, bounded RFC 9457
member, partial evidence behavior, `WWW-Authenticate`, `Allow`, and
authoritative `Retry-After`.

- [ ] **Step 2: Write framework-edge tests**

Cover malformed JSON, semantic validation, 4 MiB MCP and route-specific body
bounds, unsupported media, route/method errors, unknown caller-owned session,
idempotency conflict, admission rate limit, and safe internal failure. No
matched `/api/v2` response may be framework HTML.

- [ ] **Step 3: Implement the pre-execution security guard**

Validate actual Host and exact Origin before auth, rate limit, body parsing,
session, persistence, or execution. Ignore forwarded headers unless a trusted
ingress already rewrote the actual request. Remote/proxy exposure fails
startup without bearer auth and explicit Host/Origin policy. CORS uses exact
origins, no wildcard, no credentials, and wraps safe errors.

- [ ] **Step 4: Enforce principal authority**

Reject ambiguous v2 credential carriers. Bearer principal owns tier policy,
session, spend, and audit identity; request caller labels are non-authoritative.
Retrieval sessions are bounded, caller-owned resources and never consume an
MCP transport session ID.

- [ ] **Step 5: Register additive routes/capabilities**

All `/api/v2` routes call the same S7 accepted-operation service exactly once.
At S8, `GET /api/capabilities` advertises HTTP contract 2 only when
`ARGUS_ACCEPTED_OPERATION_AUTHORITY=evidence` passed the complete HTTP startup
registration check; otherwise it advertises only legacy HTTP capability. It
does not advertise an MCP v2 suffix before S9c registers and proves those
tools. A direct HTTP v2 request while the evidence authority is disabled
returns `unready` before provider, extractor, cache, persistence, or session
work. The HTTP process validates the immutable release manifest during its own
startup and stores the resulting frozen snapshot in app state.
`argus/api/routes_health.py` reads exactly that snapshot; it never reconstructs
support from separate booleans. The manifest describes release contract
support, not whether a separate MCP process is currently live. Existing
capability fields remain unchanged.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/test_transport_v2.py tests/test_transport_security.py \
  tests/test_api.py tests/test_http_authority.py -q
uv run pytest -q
git diff --check
git add argus/capabilities.py argus/api/contracts_v2.py argus/api/routes_v2.py \
  argus/api/security.py argus/api/main.py argus/api/lifecycle.py \
  argus/api/routes_health.py argus/auth.py argus/config.py argus/authority.py \
  tests/test_transport_v2.py tests/test_transport_security.py
git commit -m "feat: add secure version two HTTP contract"
```

**Acceptance evidence:** Exact v2 envelope and status matrix, early security
rejection, authenticated principal ownership, legacy route parity, and no
duplicate execution.

**Rollback boundary:** Additive route family; v1 remains. Production exposure
and evidence authority remain disabled until P1.

**Human gate:** Merge only.

---

### Task 10: S9a — Fail-closed HTTP capability negotiation

**Inputs:** S8 capability document and ADR 0006 negotiation rules.

**Files:**
- Create: `argus/mcp/capabilities.py`
- Create: `tests/test_capability_negotiation.py`
- Modify: `argus/authority.py`

**Interfaces:**
- Produces
  `resolve_http_contract(authority_origin, deployment_id, clock) -> ContractSelection`.
- Produces a bounded in-process cache keyed by authority origin and deployment
  ID; it performs discovery only and never executes an operation.

- [ ] **Step 1: Write negotiation/cache tests**

Cover advertised v2, a proven legacy document with no `http_contracts`,
malformed/unavailable discovery, a server that advertised v2 then fails,
60-second expiry, origin separation, deployment change, and an error while a
still-valid entry exists.

- [ ] **Step 2: Implement fail-closed selection**

Select v2 only when advertised and legacy only when discovery proves the old
capability shape. Malformed/unavailable discovery is `unready`. Never probe a
POST to discover a contract, never overwrite a valid entry with an error, and
never retry an ambiguous execution against another version.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest tests/test_capability_negotiation.py \
  tests/test_http_authority.py -q
uv run pytest -q
git diff --check
git add argus/mcp/capabilities.py argus/authority.py \
  tests/test_capability_negotiation.py
git commit -m "feat: negotiate HTTP contracts without replay"
```

**Acceptance evidence:** Exact legacy/v2 selection, bounded cache behavior,
and zero speculative or fallback execution requests.

**Rollback boundary:** New resolver is unused by MCP/CLI until S9c/S9d.

**Human gate:** Merge only. Allowed before operational gates.

---

### Task 11: S9b — MCP transport sessions and legacy SSE security

**Inputs:** S8 `TransportSecurityGuard`, the pinned MCP SDK, and ADR 0006
transport/session rules.

**Files:**
- Create: `argus/mcp/sessions.py`
- Create: `tests/test_mcp_transport.py`
- Modify: `argus/capabilities.py`
- Modify: `argus/mcp/server.py`
- Modify: `argus/config.py`
- Modify: `.env.example`

**Interfaces:**
- Produces a process-local `McpSessionRegistry` with atomic
  `initialize`, `lookup`, `touch`, `terminate`, and `sweep` operations.
- Extends the immutable release manifest with the S9b transport descriptor but
  no externally advertised MCP v2 contract yet. The MCP process binds its
  actual transport registration to that descriptor and validates it before
  opening a listener.
- Keeps existing tool registration and result shapes unchanged.

- [ ] **Step 1: Write protocol/method/media tests**

Contract-test `2024-11-05`, `2025-03-26`, `2025-06-18`, and `2025-11-25`;
POST/GET/DELETE/OPTIONS; JSON/SSE Accept and Content-Type; 4 MiB body bound;
202 notification/response acknowledgment; and 405 `Allow`.

- [ ] **Step 2: Write session concurrency/security tests**

Cover missing, wrong, expired, terminated, wrong-principal, post-restart, and
capacity-limited sessions; 256 concurrent initialize attempts; cryptographic
IDs; forced collision retry; 30-minute idle expiry; bounded sweep; and no LRU
eviction.

- [ ] **Step 3: Implement and bind the registry**

Reclaim expiry, reserve capacity, collision-check, and insert atomically.
Bind every session to the authenticated principal and negotiated protocol.
Session IDs never authenticate, identify retrieval sessions, or derive from
request IDs/tokens.

- [ ] **Step 4: Secure legacy SSE without changing its shape**

Keep `GET /sse` and `POST /messages/` wire behavior, but apply the shared
bearer, Host, Origin, CORS, body, and principal guard before session/tool work.
Capabilities continue to direct new clients to `/mcp`.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_mcp_transport.py tests/test_transport_security.py \
  tests/test_http_authority.py -q
uv run pytest -q
git diff --check
git add argus/capabilities.py argus/mcp/sessions.py argus/mcp/server.py \
  argus/config.py \
  .env.example tests/test_mcp_transport.py
git commit -m "feat: bind and bound MCP transport sessions"
```

**Acceptance evidence:** Exact MCP protocol/method/media behavior,
principal-owned bounded sessions, concurrent capacity proof, and secured
legacy SSE.

**Rollback boundary:** Existing tool names/results remain unchanged; reverting
this slice removes only new session/security machinery.

**Human gate:** Merge only. Allowed before operational gates; remote exposure
still waits for P1.

---

### Task 12: S9c — Versioned MCP tools and exact structured content

**Inputs:** S9a contract resolver, S9b transport, S8 HTTP v2, and ADR 0006 tool
compatibility.

**Files:**
- Create: `argus/mcp/v2_tools.py`
- Create: `tests/test_mcp_v2.py`
- Modify: `argus/capabilities.py`
- Modify: `argus/api/routes_health.py`
- Modify: `argus/mcp/http_adapter.py`
- Modify: `argus/mcp/tools.py`
- Modify: `argus/mcp/server.py`

**Interfaces:**
- Produces explicit `search_web_v2`, `recover_url_v2`, `expand_links_v2`, and
  `extract_content_v2`.
- Existing tool names retain their current inputs, text, and
  `{"result": "<text>"}` structured schema.

- [ ] **Step 1: Write MCP error-layer tests**

Keep listener HTTP, JSON-RPC, SDK input-schema, Argus tool error, and accepted
operation layers distinct. Schema-invalid values return bounded SDK errors
without `structuredContent`, rejected-value echo, HTTP execution, or provider
work.

- [ ] **Step 2: Write v1/v2 tool contract tests**

Freeze every legacy tool golden. V2 tools advertise exact input/output schemas,
return the exact HTTP envelope as `structuredContent`, include bounded text,
and set `isError=false` only for success/degraded/empty.

- [ ] **Step 3: Implement HTTP-only tool adapters**

Use S9a discovery before the first execution. Once v2 is selected, a failure
never falls back to v1. Presenters do not classify outcomes or parse Markdown
for evidence.

- [ ] **Step 4: Advertise MCP v2 only after complete registration**

Add the MCP v2 suffix and tool-contract version to capabilities only when the
S9b transport and all four S9c `*_v2` tools/output schemas passed one startup
registration check. Missing transport/tool/schema registration keeps the
legacy MCP capability document and fails direct v2 tool registration before
the listener accepts requests.

S9c completes the immutable release manifest with one MCP descriptor containing
the transport version, four tool names, and exact input/output schema digests.
The HTTP authority and MCP server are separate processes: neither relies on a
mutable singleton or cross-process update. The HTTP process independently
validates the complete manifest at startup and
`argus/api/routes_health.py` renders its frozen result as release contract
support, not MCP-process liveness. The MCP process independently validates the
same manifest against its actual bound transport, tools, and schemas before it
opens a listener. Contract tests start the two processes independently, mutate
each manifest field, and prove HTTP suppresses the MCP suffix while MCP refuses
startup on incomplete or mismatched descriptors.

- [ ] **Step 5: Verify and commit**

```bash
uv run pytest tests/test_mcp_v2.py tests/test_mcp_transport.py \
  tests/test_http_authority.py -q
uv run pytest -q
git diff --check
git add argus/capabilities.py argus/api/routes_health.py \
  argus/mcp/v2_tools.py argus/mcp/http_adapter.py argus/mcp/tools.py \
  argus/mcp/server.py tests/test_mcp_v2.py
git commit -m "feat: add versioned MCP evidence tools"
```

**Acceptance evidence:** Frozen legacy tool contracts, exact v2 structured
content/schema, correct error layers, and no fallback POST.

**Rollback boundary:** Remove explicit `*_v2` registrations; legacy tools and
transport remain.

**Human gate:** Merge only. Allowed before operational gates; clients stay on
legacy until P1.

---

### Task 13: S9d — CLI version negotiation and exact output behavior

**Inputs:** S9a resolver, S8 HTTP v2 envelope, and ADR 0006 CLI rules.

**Files:**
- Create: `tests/test_cli_v2.py`
- Modify: `argus/cli/main.py`

**Interfaces:**
- CLI consumes only authenticated HTTP and `ContractSelection`.
- `--json` emits one exact version-2 envelope to stdout.

- [ ] **Step 1: Write stdout/stderr/exit tests**

Cover v2 preference, proven legacy fallback, failed discovery, no fallback
POST, exact JSON stdout, diagnostic stderr, exit 0 for success/degraded/empty,
exit 1 for canonical failures, and unchanged Click usage exit 2.

- [ ] **Step 2: Implement negotiated CLI rendering**

Human rendering preserves core legacy result text and adds visible
outcome/request/evidence labels. JSON mode writes no progress, warning, or log
text to stdout.

- [ ] **Step 3: Verify and commit**

```bash
uv run pytest tests/test_cli_v2.py tests/test_cli.py \
  tests/test_http_authority.py -q
uv run pytest -q
git diff --check
git add argus/cli/main.py tests/test_cli_v2.py
git commit -m "feat: negotiate CLI evidence responses"
```

**Acceptance evidence:** Exact stdout/stderr/exit behavior and no ambiguous
execution retry.

**Rollback boundary:** Old CLI/server negotiation still selects v1; no server
or workflow state changes.

**Human gate:** Merge only. Allowed before operational gates; installed client
cutover waits for P1.

---

### Task 14: S9e — Workflow extraction composition and delivery safety

**Inputs:** S3 composer, S7 accepted operations, and ADR 0005 workflow
obligations.

**Files:**
- Create: `tests/test_workflow_composition.py`
- Modify: `argus/workflows/service.py`
- Modify: `argus/workflows/models.py`
- Modify: `tests/test_workflows.py`
- Modify: `argus/persistence/search_ledger.py`

**Interfaces:**
- Workflows consume `AcceptedOperation` and persist one
  `ResultExtractionLink` for every planned selection.
- Workflows never classify raw errors or bypass the artifact requirement.

- [ ] **Step 1: Write link/floor/failure tests**

Cover success, optional rejection/degraded, required rejection,
no-eligible-path/unready, persistence failure, exception paths, per-result
minimum, aggregate minimum, explicit partial policy, shared eligible artifact,
and idempotent resume.

- [ ] **Step 2: Write negative delivery tests**

Instrument `StoredDocument`, `CitationRef`, summarizer, report, and delivery
constructors. Diagnostic/rejected artifacts must never reach them. A failed
composition may render accepted evidence but cannot accept synthesis or
external delivery.

- [ ] **Step 3: Port workflows to the composer**

Remove silent `continue`/status reconstruction paths. Persist every planned
link, enforce the immutable requirement, and return the exact accepted
composition projection.

- [ ] **Step 4: Verify and commit**

```bash
uv run pytest tests/test_workflow_composition.py tests/test_workflows.py \
  tests/test_search_ledger.py -q
uv run pytest -q
git diff --check
git add argus/workflows argus/persistence/search_ledger.py \
  tests/test_workflow_composition.py tests/test_workflows.py
git commit -m "feat: compose workflow extraction evidence"
```

**Acceptance evidence:** Durable total link coverage, deterministic artifact
floors, idempotent resume, and no rejected-content delivery.

**Rollback boundary:** HTTP/MCP/CLI transport changes remain independently
usable; workflow activation remains behind the S7 atomic evidence authority.

**Human gate:** Merge only. Allowed before operational gates; external
production delivery waits for P1 canaries.

---

### Task 15: S10 — Hermetic scorecard and immutable candidate evidence bundle

**Inputs:** Accepted scorecard and complete S0-S9 release candidate.

**Files:**
- Create: `argus/scorecard/__init__.py`
- Create: `argus/scorecard/corpus.py`
- Create: `argus/scorecard/stability.py`
- Create: `argus/scorecard/competitive.py`
- Create: `argus/scorecard/bundle.py`
- Create: `tests/test_scorecard.py`
- Create: `tests/test_evidence_bundle.py`
- Create: `tests/fixtures/scorecard/**`
- Create: `scripts/run-scorecard.py`
- Modify: `.github/workflows/ci.yml`
- Modify: `docs/scorecards/stability-competitive.md`

**Interfaces:**
- Produces deterministic hermetic stability verdict and validated checksummed
  bundle.
- Produces live competitive runner configuration, but does not silently run
  live providers from PR CI.

- [ ] **Step 1: Freeze corpus and evaluator fixtures**

Encode 24 search intents, 8 hermetic extraction cases, 4 synchronized live
case descriptors, profile applicability, required source characteristics,
forbidden patterns, freshness windows, and minimum evidence shapes. Add paired
evaluator outputs for win/loss/tie/conflict/catastrophic/malformed/unavailable.

- [ ] **Step 2: Implement hard stability gates**

Evaluate every scorecard gate independently. Any failed required gate yields
`unstable`; missing evidence invalidates the verdict. Free and budgeted
profiles are separate and strong budgeted evidence cannot mask free failure.

- [ ] **Step 3: Implement exact competitive procedure**

Apply the scorecard's ordered catastrophic, per-mode, 20-consistent,
8-decisive, and one-sided exact sign-test rules. Speed and ordinary spend
never enter the score. Order-conflict/evaluator outage remain pair-level
inconclusive evidence.

- [ ] **Step 4: Build and verify bundles**

Write the declared manifest/identities/corpus/stability/competitive/artifact
tree and `checksums.sha256`. Reject missing required sections, mismatched
identities/hashes, cross-generation comparisons, raw credentials, auth
headers, provider-native payloads, and secret sentinels.

- [ ] **Step 5: Add PR CI**

PR CI runs only hermetic stability and publishes the bundle artifact. Live
competitive execution is a separate weekly or explicit-promotion workflow
through canonical HTTP, with the baseline and candidate close together under
the same topology/profile/provider snapshot. The free profile may run
automatically. A budgeted run requires an immutable authorization receipt for
that exact run ID/generation naming permitted providers, maximum tier, call
count, and cost/credit cap; absence or reuse fails before reservation.
One-time-credit providers remain disabled unless the receipt names them
individually.

- [ ] **Step 6: Verify and commit**

```bash
uv run pytest tests/test_scorecard.py tests/test_evidence_bundle.py -q
uv run python scripts/run-scorecard.py --lane hermetic \
  --output .artifacts/scorecard-hermetic
uv run pytest -q
git diff --check
git add argus/scorecard tests/fixtures/scorecard tests/test_scorecard.py \
  tests/test_evidence_bundle.py scripts/run-scorecard.py \
  .github/workflows/ci.yml docs/scorecards/stability-competitive.md
git commit -m "feat: automate retrieval stability scorecard"
```

Expected: hermetic verdict `stable`, bundle checksum verification succeeds,
and the full suite has zero failures.

**Acceptance evidence:** Reproducible stable verdict and checksummed bundle
with every required identity/gate/artifact; no manual labels or live PR calls.

**Rollback boundary:** Evaluation tooling is diagnostic and cannot authorize
runtime execution or deployment by itself.

**Human gate:** Merge only.

---

### Task 16: P1 — Immutable homelab port, competitive proof, and rollback

**Inputs:** Exact merged S10 revision, closed/current operational gates #40,
#41, #42, and #44, protected production environment, canonical
`homelab-postgres`, immutable release workflow, and documented rollback
operator.

**Files:**
- Modify in Argus only when required by release evidence: `CHANGELOG.md`,
  `docs/operations-status.md`
- Modify in Homelab through its own one review PR: Argus immutable digest,
  encrypted non-secret configuration references, migration job declaration,
  canaries, and evidence receipt paths.
- Never edit the live Homelab checkout by hand.

**Interfaces:**
- Consumes immutable image digest and additive migrations 0007-0009.
- Produces migration receipt, candidate/rollback receipts, HTTP/MCP/client
  canaries, scorecard bundle, and the map closeout evidence.

- [ ] **Step 1: Prove promotion prerequisites**

Read GitHub and live homelab evidence. Require:

- #40 recovery current for the schema-changing promotion;
- #41 exact digest promotion and rollback path current;
- #42 one private production authority and approved client inventory current;
- #44 runbook current;
- PostgreSQL backup and isolated restore fresh;
- previous compatible image digest retained;
- no dirty checkout mutation or unresolved migration lock.

Failure leaves production unchanged and records the missing gate.

- [ ] **Step 2: Build once and test the isolated candidate**

Build the exact merged revision once. Record source revision, image digest,
SBOM, provenance, lock hash, schema heads, and config/profile hash. Start it
against scratch PostgreSQL without paid credentials, production ingress, or
external delivery. Run migrations 0007-0009 and the full hermetic stability
lane.

- [ ] **Step 3: Exercise rollback before production**

Upgrade a restored production-compatible database copy, run search/extraction/
HTTP/MCP/persistence/readiness canaries, switch back to the previous image
against the retained additive schema, and rerun legacy v1 canaries. Do not
downgrade or delete the new tables.

- [ ] **Step 4: Run the bounded competitive lane**

Through canonical HTTP, run baseline and candidate close together for the
declared profile/topology/provider snapshot. Run both evaluator orders and
verify the bundle. Promotion requires exact candidate `stable` plus
`competitive`. The free profile may run under the automated lane. Before a
budgeted profile reserves anything, require a distinct, unused authorization
receipt for this exact run/generation with provider allowlist, maximum tier,
maximum calls, and maximum cost/credit exposure. Existing deployment approval,
configured budget, or an older run receipt is not spend authorization.
One-time-credit providers remain disabled unless individually named.
`inconclusive` leaves production unchanged; a free rerun may be scheduled
automatically, while a budgeted rerun requires a new authorization receipt.
Neither path creates owner labeling work.

- [ ] **Step 5: Publish one Homelab review PR**

Pin the exact candidate digest and declare the migration/canary receipts.
Preserve the prior digest as rollback target. Do not duplicate the PR or
include secrets. Human merge and protected-environment approval remain
required.

- [ ] **Step 6: Apply additive migrations and promote**

After approval, acquire the host promotion lock, verify backup/restore again,
apply migrations exactly once, promote the digest, and atomically set
`ARGUS_ACCEPTED_OPERATION_AUTHORITY=evidence`. Verify `/api` v1, `/api/v2`,
`/mcp`, legacy SSE, CLI, PostgreSQL acceptance, cache origin lineage,
readiness, free-only no-spend, extraction rejection, workflow links, and
recovery evidence.

- [ ] **Step 7: Cut approved clients to v2**

MCP/CLI clients negotiate capabilities. Explicit HTTP clients change to
`/api/v2` one at a time with caller identity and canary receipts. A failed
client returns to v1 without repeating an ambiguous provider-spending
operation. No Mac/OCI/duplicate Argus authority is introduced.

- [ ] **Step 8: Soak and prove rollback**

Observe the runbook-defined soak. If a hard stability gate fails, promote the
previous digest, keep additive schema, and rerun v1 canaries. Do not discard
accepted data. Record both candidate and rollback receipts.

- [ ] **Step 9: Close the map from evidence**

Attach:

- exact Argus and Homelab PRs/merge revisions;
- image and prior rollback digests;
- migration/backup/restore receipts;
- stable and competitive bundle hashes;
- HTTP/MCP/CLI/workflow/free-only/provider/readiness canaries;
- rollback drill;
- current runbook;
- one-authority proof.

Close implementation children and #67 only after their code/evidence is
merged. Close parent #58 only when every named operational and retrieval
requirement is proven current.

**Acceptance evidence:** One immutable private production authority running
the exact stable/competitive digest with PostgreSQL durability, truthful v1/v2
transports, current recovery proof, client canaries, and tested rollback.

**Rollback boundary:** Promote the retained previous digest; never delete or
downgrade accepted evidence tables during emergency rollback.

**Human gate:** Human merge and protected deployment approval. A budgeted live
competitive run additionally requires its bounded run authorization. Secret
rotation, credit purchase, destructive legacy cleanup, external archive
creation, and irreversible data operations remain separate explicit approvals
and are not part of this plan.

---

## Per-Slice Review and Publication Protocol

For S0 through S10:

1. Claim the implementation issue with an AI-generated disclosure.
2. Create a clean worktree from the exact predecessor commit.
3. Run `uv sync --frozen --extra dev --extra mcp`.
4. Run the predecessor's full suite before editing.
5. Implement tests first and preserve the red-to-green evidence.
6. Run the focused tests, `uv run pytest -q`, formatting/static checks, fixture
   hash verifier, secret sentinel scan, and `git diff --check`.
7. Request independent review against the exact predecessor merge base.
8. Fix every evidence-backed Critical or Important finding and rerun the exact
   verification.
9. Commit focused changes and push the named branch.
10. Open one ready PR against the predecessor branch/main as appropriate,
    include exact verification, dependency order, compatibility/rollback
    boundary, and `Resolves #<slice issue>`.
11. Wait for all GitHub checks; disposition automated comments with evidence.
12. Update issue #67 and parent #58 with PR, revision, tests, review, gate
    status, and next slice.

Do not close a design or implementation issue merely because a PR exists.
Resolution requires its accepted evidence. Do not merge, deploy, buy credits,
rotate secrets, or delete state from an autonomous implementation session.

## Completion Audit

Before declaring the mechanical port complete, prove every row:

| Requirement | Authoritative evidence |
|---|---|
| Deterministic planning/cache identity | ADR 0002 vector suite and exact hashes |
| Provider normalization/privacy | 14-provider fixture manifest and sentinel suite |
| Freshness/ranking/duplicates/diversity | Exact S4 fusion suite and trace replay |
| Readiness/no-spend/exhaustion | Durable repository/concurrency suite and diagnostics traps |
| Extraction rejection/composition | Exhaustive taxonomy/truth-table/link tests |
| Durable acceptance/cache ordering | Fault-injection, idempotency, immutable hit, and PostgreSQL tests |
| HTTP v1/v2 truth | Golden v1 fixtures plus exhaustive v2 status/envelope tests |
| MCP/CLI truth | Protocol/session/error/structured-content/exit suites |
| Workflow artifact safety | Durable link and no-diagnostic-delivery tests |
| Stability/competitiveness | Verified checksummed bundle for exact release/profile |
| Production authority | Live digest, PostgreSQL, private ingress, client, and duplicate-authority evidence |
| Recovery/rollback | Fresh backup/restore plus candidate/previous-digest rollback receipts |
| No manual owner labor | Automated corpus/evaluator/bundle receipts; no pending review queue |

Any missing, expired, indirect, or contradictory evidence means the map is not
complete. Continue the next bounded slice or leave the specific protected
deployment gate visibly pending; never replace missing proof with an optimistic
status.
