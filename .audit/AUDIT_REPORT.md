# Repository Audit & Stabilization Report
**Audit Timestamp:** 2026-08-31T01:28:58Z
**Readiness Index:** 54% — The deterministic test, PostgreSQL ledger, package, and deployed control-plane baselines are strong. Production safety is still blocked by an authenticated SSRF-capable fetch path and extraction spend evidence that can report a paid call as reserved and settled at $0; topology, workflow isolation, and migration-autogeneration gaps further prevent an unqualified readiness claim.

## 1. Test & Health Check Matrix
| Suite / Tool | Command Executed | Result (PASS/FAIL) | Failure Count / Summary |
| :--- | :--- | :--- | :--- |
| Workspace and dependency baseline | `git status --short --branch`; `uv pip check --python .venv/bin/python`; `uv lock --check` | PASS | Runtime source parent `6c0129ee93ee6edf5f61076dfdeaa9c55fd09089`; 135 installed packages compatible; lock resolves 139 packages. At refresh time, local `main` had one documentation-only audit commit above that parent. |
| Full hermetic pytest suite | isolated environment + `uv run --no-sync pytest -q` | PASS | Final integrated runtime source: 2,672 passed, 43 skipped, 4 warnings in 112.53 s; exit 0. One earlier publication run transiently failed the five-second accepted-cache timing test after 2,665 passes; the test then passed 5/5 in isolation, one pre-rebase complete rerun passed, and two integrated-source complete runs passed. |
| Production-like configuration | isolated environment + `uv run --no-sync pytest tests/test_production_config.py -v --tb=short` | PASS | 1 passed; exit 0. Coverage weakness: the fixture labels SQLite as production-like and therefore does not require production PostgreSQL. |
| Stateless MCP v2 contract | isolated environment + `uv run --no-sync pytest tests/test_mcp_stateless_v2.py -q` | PASS | 4 passed in 1.71 s; exit 0. |
| PostgreSQL migration and schema contract | disposable PostgreSQL 16 + `alembic upgrade head`; `generate_argus_schema_contract.py ... --check` | PASS | Applied linear revisions 0001–0009; database reported `0009_retrieval_evidence`; checked schema contract passed. |
| PostgreSQL ledger contracts | disposable PostgreSQL 16 + CI-focused `pytest` selection | PASS | 314 passed in 39.82 s; exit 0. Includes ledger, spend, outbox, concurrency, operation, recovery, and accepted-retrieval tests. |
| PostgreSQL readiness scenarios | `verify_provider_readiness_postgres.py --output /tmp/...` against a disposable `argus_restore_*` database | PASS | `status=ok`; atomic rollback, settlement race, expiry, recurring reset, account-lock, and terminal-precedence scenarios passed. |
| PostgreSQL provisioning ACL test | CI-focused suite with `ARGUS_TEST_ALLOW_PROVISIONING=disposable-only` | FAIL | Collection stopped with `RuntimeError: ARGUS_TEST_ALLOW_PROVISIONING requires the psql executable`; local host has no `psql` client. The disposable database itself remained isolated. |
| Alembic autogeneration drift | `alembic current`; `alembic check` on migrated disposable PostgreSQL | FAIL | Current is head, but `alembic check` exits 255 and proposes removing spend, readiness, and evidence tables because `migrations/env.py` loads only `LedgerBase.metadata`. |
| Ruff static rules | `ruff check argus tests scripts` | PASS | `All checks passed!`; exit 0. |
| Ruff formatting baseline | `ruff format --check argus tests scripts` | FAIL | 127 files would be reformatted; whole-repository check reports 133. No format gate exists in CI. |
| Type checking | `uv run --no-sync pyright`; `uv run --no-sync mypy` | FAIL | Both executables are absent; there is no Pyright/Mypy configuration or CI type gate. |
| High-confidence dead-code scan | `uvx vulture==2.16 argus scripts migrations --min-confidence 80` | FAIL | Exit 3: one intentionally unused compatibility parameter and three 100%-confidence unreachable paid-provider implementations. |
| Shell syntax and lint | `bash -n ...`; `shellcheck ...` over deployment/start scripts | PASS | All checked shell scripts passed; exit 0. |
| Provider fixture attestations | `uv run --no-sync python scripts/generate_provider_fixture_attestations.py --check` | PASS | Canonical fixture attestations match; exit 0. |
| Hermetic scorecard | `run-scorecard.py --lane hermetic --output /tmp/...` | PASS | Stable: 8 extraction cases, 24 search cases, and hermetic adapter contract; exit 0. |
| Live-config scorecard | `run-scorecard.py --lane live-config --output /tmp/...` | PASS | Configuration bundle generated, explicitly marked `execution=not_performed`; this is not a live-provider proof. |
| Acceptance v3 fixture runner | `run-acceptance-v3.py --fixture --output /tmp/<fresh-dir>/acceptance-v3` | FAIL | Process exits 0 but artifact status is `preflight_failed` and score is `not_run`; the command did not produce acceptance evidence. |
| Release/version contract | `uv run --no-sync python scripts/verify_release_contract.py` | PASS | `release contract valid: version=1.6.4`; exit 0. |
| Python distribution build | `uvx --from build pyproject-build --outdir /tmp/argus-audit-dist-20260830 .` | PASS | Built `argus_search-1.6.4.tar.gz` and `argus_search-1.6.4-py3-none-any.whl`; exit 0. Setuptools emitted license-metadata deprecations with a 2027-02-18 deadline. |
| Distribution metadata | `uvx twine check /tmp/argus-audit-dist-20260830/*` | PASS | Wheel and sdist both passed. |
| Documented local release command | `python -m build` | FAIL | `No module named build.__main__; 'build' is a package` because the build frontend is not installed and the local `build/` namespace is found; Twine is also absent. CI installs both explicitly. |
| Docker Compose rendering | `docker compose config --quiet` and proxy-profile equivalent | PASS | Base and proxy configurations render; exit 0. |
| Runtime manifest and image admission | `build_runtime_manifest.py ...`; `argus image-admission --manifest ...` | FAIL | Matching Playwright Chromium headless shell is absent; no admissible manifest exists in this source checkout. |
| Dockerfile/image validation | `docker build --check .` | FAIL | BuildKit metadata lookup failed because the non-interactive macOS session could not access the GHCR credential helper; no local image/canary was produced. Current remote CI image build is green. |
| Isolated CLI diagnostics | `argus paths --json`; `argus health`; `argus budgets`; fixture-only `test-provider`; `check-balances` | PASS | Commands exit 0 without provider spend. Health is degraded/disabled with no configured external keys, as expected in the isolated profile. |
| `argus doctor` status contract | `argus doctor --json` | FAIL | Process exits 0 while payload says `Providers.ok=false` and `0 ready`; this contradicts `docs/adr/0004-no-spend-provider-readiness.md:429`. |
| MCP installation/config check | isolated `argus mcp check` | PASS | MCP package/context/config detected; explicit development standalone mode; exit 0. No provider call was made. |
| Deployed public control plane | `curl` to canonical `/api/live`, `/api/startup`, `/api/ready` | PASS | HTTP 200 for all: alive; initialized version 1.6.4; ready=true but status=degraded with provider and Maya reason codes. This does not prove an authenticated retrieval. |
| Remote branch/CI state | `git fetch origin main`; `git rev-list --left-right --count HEAD...origin/main`; `gh run list --branch main ...` | PASS | Runtime source `6c0129e...` was fetched and integrated. CI and immutable-image promotion for that SHA completed successfully on 2026-08-30; the audit publication commit is documentation-only and had not yet been pushed when this row was recorded. |

## 2. System Topology & Connectivity
- **Operational Subsystems:** The HTTP authority constructs the production broker; v2 accepted search creates durable retrieval/evidence receipts; session ownership is enforced on accepted search; PostgreSQL revisions 0001–0009, the checked schema contract, provider-spend/readiness scenarios, Maya outbox contracts, package build, and public liveness/startup/readiness endpoints were verified. The 14 search-provider adapters normalize through broker contracts, and free/provider fixture paths are extensively covered.
- **Degraded Subsystems:** General extraction and site acquisition have an incomplete fetch-security boundary; external extraction lacks trustworthy spend settlement; remote worker provenance loses egress/machine identity; background reachability refresh does not perform the documented network probe; workflows mix filesystem durability with SQL-backed operations and omit read ownership enforcement; the dashboard repeatedly creates transient SQLAlchemy engines; production configuration and deployment wrappers have conflicting defaults; MCP v2 error/attribution projection loses distinctions.
- **Dead / Unwired Subsystems:** Valyu Contents, Firecrawl, and Valyu Answer return before their implementations; `WorkflowPersistenceGateway` has no callers; the production 30-minute task cannot reach `ReachabilityMatrix.probe_all`; an unused `routing_policy` compatibility argument remains; launchd/systemd wrappers for retired or noncanonical authorities are still executable; a standalone site-acquisition `domain_root` helper is unreferenced.

The permanent module, interface, lifecycle, persistence, and dependency map is in [SYSTEM_TOPOLOGY.md](SYSTEM_TOPOLOGY.md).

## 3. P0 Blockers (Critical Breakages & Data Loss Risks)
- **P0-1 — The general fetch boundary permits authenticated SSRF and unsafe redirect hops.** `CaptureSiteWorkflowRequest.url` is only a string (`argus/api/schemas.py:517`), and site discovery fetches the root, sitemap, and links through an HTTP client with redirects enabled and no public-address validation (`argus/operations/site_acquisition.py:147`, `argus/operations/site_acquisition.py:154`). The shared validator explicitly fails open on DNS resolution failure (`argus/extraction/ssrf.py:53`, `argus/extraction/ssrf.py:73`). Trafilatura fetches before validating the effective URL (`argus/extraction/extractor.py:198`, `argus/extraction/extractor.py:203`); Playwright and authenticated Playwright navigate before checking the final URL (`argus/extraction/playwright_extractor.py:159`, `argus/extraction/auth_extractor.py:118`); Crawl4AI validates only after `crawler.arun` returns (`argus/extraction/crawl4ai_extractor.py:76`). A valid caller can make the authority contact loopback, private, link-local, or rebinding targets, including redirect intermediates and browser subresources. Scope: the bounded `fetch-raw` path has stronger interception, so this finding applies to general extraction/site workflows.
- **P0-2 — Paid extraction can be executed and durably reported as reserved/settled at $0.** `ExtractRequest` exposes neither `free_only` nor a spend authorization (`argus/api/schemas.py:159`), while the external-policy gate only knows `free_only` and Jina enablement (`argus/extraction/extractor.py:448`). You Contents performs a keyed external call without a spend reservation (`argus/extraction/you_extractor.py:23`); Jina tracks only process-global estimates and flushes every tenth call (`argus/extraction/extractor.py:1178`). `ExtractedContent.cost` defaults to zero (`argus/extraction/models.py:115`), and accepted finalization copies that value into both actual and reserved spend while manufacturing a spend reference (`argus/extraction/extractor.py:1032`). When You/Jina is enabled, real charge or quota consumption can therefore produce apparently complete $0 evidence, defeating budget enforcement and incident reconciliation.

## 4. P1 / P2 Architectural Debt & Edge Cases
- **P1 — Alembic drift detection is unsafe.** `migrations/env.py:7` imports only `LedgerBase` and `migrations/env.py:24` sets only its metadata. A database migrated to head still causes `alembic check` to propose removal of spend, readiness, and evidence tables. Manual migration/schema-contract checks currently mask the autogeneration defect.
- **P1 — Workflow reads are not scoped to the run owner.** Caller authentication covers all workflow paths (`argus/auth.py:172`), but status/artifact handlers accept any valid run ID and never compare the authenticated principal to persisted owner metadata (`argus/api/routes_workflows.py:192`, `argus/api/routes_workflows.py:214`, `argus/workflows/service.py:996`). A scoped caller that learns another run ID can read target/status/citations and bounded report/manifest content; the legacy response also serializes internal path fields (`argus/api/routes_workflows.py:41`, `argus/api/routes_workflows.py:250`).
- **P1 — Remote execution violates universal provenance.** Remote results flow through `LegacyProviderBatchAdapter` without trusted worker provenance (`argus/broker/execution.py:281`). Node names do not map to the `EgressType` enum (`argus/broker/provider_evidence.py:1066`), and response projection can omit egress/machine (`argus/broker/router.py:334`).
- **P1 — Worker/deployment defaults can expose development authority surfaces.** Missing `ARGUS_EGRESS_SHARED_SECRET` leaves worker execution open (`argus/worker/server.py:80`), while `argus worker` binds `0.0.0.0:8273` by default (`argus/cli/main.py:852`). The systemd server wrapper defaults to development and `0.0.0.0:8005` (`scripts/start-server.sh:29`, `scripts/start-server.sh:35`); `argus.service:6` supplies no production environment file. Retired launchd files hardcode a stale checkout and ports 8300/8301 (`deploy/start-argus.sh:5`, `deploy/start-argus-mcp.sh:5`).
- **P1 — Extraction finalization does not durably claim before classification.** `finalize_extraction_once` flushes the claim, invokes `build_projection()` inside the same transaction, then commits only after projection writes (`argus/persistence/search_ledger.py:1794`, `argus/persistence/search_ledger.py:1857`, `argus/persistence/search_ledger.py:1858`). A crash rolls back the claim and permits repeated work; slow classification holds write/transaction locks.
- **P1 — Authenticated browser resources have no shutdown owner.** `auth_extractor.py` holds a global browser and one context per domain (`argus/extraction/auth_extractor.py:28`) but exposes no close/reset path. API shutdown closes only the separate general Playwright browser (`argus/api/main.py:578`).
- **P1 — Workflow lifecycle and artifact durability are split.** Scheduled runs are process-local asyncio tasks (`argus/workflows/service.py:3284`) with no WorkflowService shutdown hook. Run state is atomically written, but captured Markdown/JSON documents use direct writes (`argus/workflows/service.py:4251`). Workflow runs are intentionally filesystem-local (`argus/workflows/service.py:1003`), so incomplete shutdown/auxiliary writes are node-affine durability risks even though execution remains behind the single HTTP authority.
- **P1 — Search-and-summarize assumes an undocumented direct LLM gateway.** The workflow always chooses `get_summarizer("llm")` (`argus/workflows/service.py:2877`). `LLMSummarizer` reads undocumented `ARGUS_GATEWAY_URL/KEY` and posts directly with no configuration guard or spend reservation (`argus/workflows/summarizer.py:179`, `argus/workflows/summarizer.py:222`).
- **P1 — Production configuration can remain live while permanently uninitializable.** Production+SQLite is accepted by the production test (`tests/test_production_config.py:6`), but only production+PostgreSQL receives bounded lifecycle capability (`argus/api/main.py:160`, `argus/api/main.py:195`). Other production factories retry as unsafe in a background loop after process liveness (`argus/api/main.py:217`), rather than fail startup.
- **P1 — Release publishing masks failure.** PyPI upload uses `twine upload dist/* || true` (`.github/workflows/publish.yml:43`), allowing the registry job to continue after an unsuccessful publication.
- **P2 — The advertised reachability scheduler is disconnected.** The task claims network probes every 30 minutes (`argus/api/main.py:413`), but real brokers call a no-op readiness compatibility method (`argus/broker/router.py:946`, `argus/broker/readiness.py:1193`). Explicit admin probes remain functional, so this is an automatic routing-evidence gap rather than total probe loss.
- **P2 — Dashboard queries cause repeated engine/pool churn.** Four queries per dashboard request (`argus/api/routes_dashboard.py:137`) each create a new repository engine and omit `close()` (`argus/api/usage.py:14`). Permanent descriptor leakage is GC-dependent and not proven, but concurrent load needlessly creates pools and lacks a disposal test.
- **P2 — Additional pool/state cleanup is incomplete.** The provider-spend factory creates its own engine (`argus/persistence/provider_spend.py:990`) but the repository has no close method; lifespan cleanup closes the budget tracker and singleton search repository only (`argus/api/main.py:587`). Per-run finalization locks, residential domain semaphores, auth contexts, and rate-limit client/path maps have no eviction.
- **P2 — Synchronous legacy persistence runs inside async extraction.** Domain memory calls the legacy synchronous session gateway and performs read-modify-write updates without row locking; errors are broadly swallowed. Direct broker mode still defaults `persist_legacy=True` while accepted HTTP disables it, leaving legacy and evidence ledgers able to diverge.
- **P2 — Dead paid-provider bodies remain in the active chain.** Valyu Contents (`argus/extraction/valyu_extractor.py:23`), Firecrawl (`argus/extraction/firecrawl_extractor.py:56`), and Valyu Answer (`argus/providers/valyu_answer.py:53`) immediately return disabled errors, making all code below unreachable. The extraction chain still visits the first two slots (`argus/extraction/extractor.py:592`).
- **P2 — Configuration is permissive and internally contradictory.** CWD dotenv files autoload before the repository and without trust/ownership checks (`argus/config.py:35`); malformed booleans/numbers silently fall back (`argus/config.py:260`). `.env.example` advertises zero-config/no-server use but assigns a PostgreSQL URL (`.env.example:9`, `.env.example:23`), defaults accepted operations to legacy (`.env.example:45`), and omits the LLM gateway and several egress/runtime variables.
- **P2 — Error and attribution contracts lose information.** Accepted extraction maps every exception to `PERSISTENCE_FAILED` (`argus/operations/accepted.py:1658`). MCP v2 maps every `AuthorityRequestError` to generic unready 503 (`argus/mcp/http_adapter.py:247`), and recover/expand/extract caller attribution is discarded or hardcoded.
- **P2 — One cache test has a wall-clock-sized flake boundary.** `tests/test_task2_fix.py:188` replaces the accepted-cache freshness window with five seconds while the same test comment acknowledges that fallback work may take several seconds. A complete publication run observed `HIT_INELIGIBLE` after 2,665 passes; five isolated repetitions then passed in 3.55–5.06 seconds, and two complete runs passed. The cache behavior is not proven wrong, but the test can cross its own real-time threshold under suite load.
- **P2 — Static quality governance is incomplete.** Ruff rules pass, but formatting has 127-file drift, type checking is absent, and `WorkflowService`, MCP server, CLI, broker router, and search ledger are oversized change-concentration points. The package builds, but its license metadata is already deprecated by current Setuptools.
- **P2 — Documentation and executable artifacts drift from current runtime/deployment state.** `docs/operations.md:5` says Homelab is sole authority, while the older launchd ADR is correctly marked superseded (`docs/adr/0001-canonical-deployment.md:4`) but its executable scripts/plists remain in active deployment paths. ADR 0006 says MCP <2 (`docs/adr/0006-http-mcp-compatibility-contract.md:409`) while `pyproject.toml` pins 2.0.0; `docs/releasing.md:5` and `.env.example:7` retain 1.6.2 examples; extraction docs omit several actual steps.

## 5. Sequential Stabilization Punch List
- [x] Task 1: Reconcile local `main` with remote runtime source `6c0129e`, preserve the unrelated research document, revalidate extraction findings, and refresh the audit for the additive `article|webpage` quality-profile contract.
- [ ] Task 2: Create one fail-closed public-HTTP fetch gateway: validate scheme/host, resolve all addresses, reject special-use ranges, pin the connection target, inspect every redirect before following it, and intercept browser subresources. Route site acquisition and every local/remote extractor through it.
- [ ] Task 3: Add SSRF regression tests for private IPv4/IPv6, alternative encodings, DNS failure/rebinding, redirect chains, Playwright subresources, sitemap traversal, and residential-worker hops.
- [ ] Task 4: Put Jina and You Contents behind the same durable reserve → invoke → settle/uncertain spend gateway as search providers; add explicit extraction `free_only`/caller-tier/spend authority to request, plan, cache identity, and receipt contracts.
- [ ] Task 5: Make extraction finalization use real spend-attempt IDs and provider evidence; reject “complete” paid steps whose reservation/settlement is missing; keep Valyu/Firecrawl/Valyu Answer disabled until this invariant is enforced.
- [ ] Task 6: Load every authoritative SQLAlchemy metadata registry in Alembic, make `alembic check` clean after `upgrade head`, and add it to the PostgreSQL CI job before any schema change is accepted.
- [ ] Task 7: Restore automatic reachability observation without paid calls, persist scoped evidence, and preserve trusted remote worker egress/machine provenance through normalization and response projection.
- [ ] Task 8: Bind workflow status/artifact reads to authenticated owner identity; add cross-token denial tests and keep admin override explicit and audited.
- [ ] Task 9: Split the 5,119-line WorkflowService into durable run, artifact, acquisition, and orchestration modules; add graceful task shutdown/recovery and atomic/fsynced auxiliary artifact publication.
- [ ] Task 10: Commit the extraction outcome claim before classification or replace it with a short fenced lease; bound/evict per-run/domain/client maps and add contention/crash tests.
- [ ] Task 11: Reuse the app-owned usage repository, add deterministic close methods for spend/auth-browser resources, and test engine/context disposal.
- [ ] Task 12: Fail fast for invalid production role/DB/auth/bind combinations; retire or quarantine noncanonical launchd/systemd wrappers; require worker shared-secret configuration before non-loopback bind.
- [ ] Task 13: Replace silent config coercion with typed validation, disable CWD dotenv autoload in services, document all static operational variables, and make search-and-summarize use a bounded configured gateway with accounting.
- [ ] Task 14: Preserve HTTP/MCP error categories and principal attribution end-to-end; remove dead compatibility parameters/bodies or isolate them behind explicit future modules.
- [ ] Task 15: Establish one formatting baseline, add `ruff format --check` and a configured type checker to CI, fix `argus doctor` exit semantics, and make PyPI upload failure fatal.
- [ ] Task 16: Reconcile operations/ADR/release/extraction documentation, refresh this audit, then promote only after PostgreSQL, image/browser, authenticated search/extraction/MCP, restart, and downstream Maya receipt evidence all pass on the same immutable digest.

## 6. Audit Scope and Evidence Boundaries

- **Source inspected:** the deep static audit began at `be5b5a8559816b1a142d043e3461639056a4d694`; publication refresh integrated runtime source parent `6c0129ee93ee6edf5f61076dfdeaa9c55fd09089`, re-anchored affected findings, verified the additive webpage-quality contract, and reran the complete hermetic suite. The audit publication descendant changes documentation only. The untracked `docs/research/2026-08-09-argus-product-viability-audit.md` remains outside the commit.
- **Remote source:** `origin/main` was fetched at `6c0129ee93ee6edf5f61076dfdeaa9c55fd09089` and integrated before publication. No deployment was performed; green CI/image evidence remains evidence for runtime source `6c0129e`, not for deployed authenticated capability.
- **Deployed runtime:** public liveness/startup/readiness returned HTTP 200 and version 1.6.4 on 2026-08-30. No credential was printed or used, and no authenticated retrieval/provider/downstream receipt was attempted.
- **Spend boundary:** no paid or live provider probe was executed. Fixture checks and durable PostgreSQL accounting scenarios do not prove a real provider balance.
- **Database boundary:** PostgreSQL-focused DDL/data tests used a named disposable PostgreSQL container and test databases; the hermetic suite and CLI diagnostics used temporary SQLite paths. The disposable PostgreSQL container and its unrecoverable test-only data were removed after verification.

## 7. Selected Raw Command Evidence

```text
$ uv run --no-sync pytest -q
2672 passed, 43 skipped, 4 warnings in 112.53s
[exit 0; integrated runtime source]

$ uv run --no-sync pytest -q  # earlier publication attempt
FAILED tests/test_task2_fix.py::test_accepted_cache_reload_uses_utc_receipt_age_and_no_second_extractor
1 failed, 2665 passed, 43 skipped, 4 warnings in 102.48s
[five focused repetitions then passed; the complete suite passed twice]

$ uv run --no-sync pytest \
    tests/test_search_ledger.py \
    tests/test_provider_spend.py \
    tests/test_maya_outbox.py \
    tests/test_api.py::TestSearchEndpoint::test_postgresql_constraint_failure_returns_503_and_rolls_back_ledger \
    tests/test_operation_ledger.py::test_postgresql_extraction_and_session_contract \
    tests/test_operation_ledger.py::test_postgresql_concurrent_session_creation_and_query_allocation \
    tests/test_operation_ledger.py::test_postgresql_concurrent_sanitized_session_url_inserts_are_idempotent \
    tests/test_recovery_database.py \
    tests/test_schema_contract_generator.py \
    tests/test_accepted_retrieval.py \
    -q --tb=short
314 passed in 39.82s
[exit 0]

$ uv run --no-sync alembic current
0009_retrieval_evidence (head)

$ uv run --no-sync alembic check
FAILED: New upgrade operations detected: ... remove_table provider_readiness_observations ... remove_table provider_spend_attempts ... remove_table retrieval_evidence_plans ...
[exit 255]

$ uv run --no-sync python scripts/run-acceptance-v3.py \
    --fixture --output /tmp/<fresh-dir>/acceptance-v3
manifest.status=preflight_failed
score.status=not_run
[exit 0; semantic FAIL]

$ ruff check argus tests scripts
All checks passed!
[exit 0]

$ ruff format --check argus tests scripts
127 files would be reformatted
[exit 1]

$ uvx vulture==2.16 argus scripts migrations --min-confidence 80
argus/broker/execution.py:148: unused variable 'routing_policy' (100% confidence)
argus/extraction/firecrawl_extractor.py:64: unreachable code after 'return' (100% confidence)
argus/extraction/valyu_extractor.py:31: unreachable code after 'return' (100% confidence)
argus/providers/valyu_answer.py:71: unreachable code after 'return' (100% confidence)
[exit 3]

$ uvx twine check /tmp/argus-audit-dist-20260830/*
Checking .../argus_search-1.6.4-py3-none-any.whl: PASSED
Checking .../argus_search-1.6.4.tar.gz: PASSED
[exit 0]

$ curl https://homelab.deer-panga.ts.net/api/live
HTTP 200 {"status":"alive"}
$ curl https://homelab.deer-panga.ts.net/api/startup
HTTP 200 {"status":"initialized","initialized":true,"version":"1.6.4"}
$ curl https://homelab.deer-panga.ts.net/api/ready
HTTP 200 {"status":"degraded","ready":true,"reason_codes":["provider:duckduckgo","provider:github","provider:searxng","provider:yahoo","maya"]}
```
