# Context

> **What this file is for:** background, glossary, and architectural decisions
> that don't belong in [README.md](README.md) (user-facing) or
> [AGENTS.md](AGENTS.md) (AI-agent conventions). Add entries here when a term
> or design choice keeps coming up in reviews or issues.

## Glossary

### Production restoration (2026-09-07)

HTTP is the sole production authority. Root Homelab Compose owns both the API
and stateless MCP adapter; PostgreSQL owns accepted operations, provider spend,
readiness and outbox state. Maya owns captured user-visible retrieval history.
The [dated public status](docs/STATUS.md) and private audit separate source,
image, loaded runtime, authenticated capability, provider results and durable
Maya capture evidence. The old 54/100 audit is historical.

The restoration repaired canonical vault projection, explicit provider account
registration, scoped callers and capture configuration, bounded admin probes,
native POST request framing, and baked-source binding for new probe spend rows.
DuckDuckGo's keyless worker uses guarded public-content policy and delegates
framing to the transport. Generated provider attestations must be refreshed
when their covered source changes. Neither a supplied credential nor a fixture
attestation proves a successful live provider request.

PostgreSQL remains on 0011_extraction_spend_scope. Canonical metadata and an
actual disposable restore/migration check replace the old missing-registry
blocker. Browser access still requires an external network-policy attestation;
a synthetic browser startup is not external browsing proof. See [TODO.md](TODO.md)
for bounded follow-ups and current capability limits.

Python 3.12 is the development/production baseline (local 3.12.13, image 3.12.3).
Python 3.11 remains the package floor and 3.13 a CI compatibility lane. The old
lease-owner HTTP 500 was a bounded-string defect, not a Python-version defect.

### Competitive enough

An Argus profile is **competitive enough** when it improves the evidence package
over a frozen Argus baseline on the agreed golden corpus. It does not mean
parity with a named external search engine or reward speed for its own sake.

### Evaluation profile

A scorecard verdict applies to one Argus release and operating profile, not to
Argus globally. The canonical profiles are **free** and **budgeted**.

### Free profile

An explicit `--free` or `free_only=true` operation that may use free recurring
quota and eligible cached evidence but initiates no billable provider call.

### Scorecard verdict

The separate **stable** and **competitive** conclusions for an evaluation
profile. The exact gates, thresholds, and evidence rules live in
[the stability and competitive evidence scorecard](docs/scorecards/stability-competitive.md).

### Golden corpus

A versioned set of query intents and extraction cases used to compare an Argus
candidate with its baseline. Live cases judge intent satisfaction; exact
outputs belong to hermetic contract fixtures.

### Evidence package

The **evidence package** is the normalized results, extracted content,
provenance, provider traces, freshness signals, and failure evidence that Argus
returns for downstream use. Argus is scored on this package, not on prose
synthesized by the calling model or agent.

### Benchmark generation

A set of scorecard runs that share one frozen corpus, evaluator, profile,
topology class, and other comparison identities. Changing a frozen identity
starts a new generation rather than extending an incomparable score series.

### RRF Score Attribution

Per-result attribution that decomposes a fused Reciprocal Rank Fusion score into
the providers that returned that result. Because RRF is additive, each provider's
attribution is its own rank contribution to the final score.

This is narrower than the broader attribution program, which may later include
provider value attribution, routing decision attribution, extraction chain
attribution, or session context attribution.

### Topology awareness

Argus distinguishes between **datacenter** and **residential** egress. Some
providers (notably scraped Yahoo and a handful of extraction targets) are
unreliable from datacenter IPs but work fine from residential ones. The
`ARGUS_EGRESS_TYPE` and `ARGUS_RESIDENTIAL_POLICY` settings tell Argus where
it is and how aggressively to prefer residential workers. See the
**Configuration** section of [README.md](README.md).

### Adaptive Domain Memory

A small SQLite table that records, per domain, whether datacenter extraction has
historically failed. Future extractions for that domain are routed to a
residential worker first instead of paying the failure cost again. Lives in
`argus/extraction/`.

### Provenance

Every `SearchResult` and `ExtractedContent` carries `egress` (residential or
datacenter), `machine` (the hostname that performed the fetch), and
`source_type` (search, extract, recover, etc.). The HTTP, CLI, and MCP surfaces
all expose these fields so downstream consumers can audit where a result came
from.

### Caller attribution

Every HTTP/MCP/CLI entry point accepts a `caller` string (e.g. `maya-lane-b`,
`hermes`, `mcp`) persisted with each search for the per-caller dashboard.
Fleet callers must always set it; unattributed traffic shows as `unknown`.

### Caller tier caps

Server-side spending guardrail: `ARGUS_CALLER_TIER_CAPS` maps fnmatch
caller patterns to a maximum provider tier. Motivated by the 2026-05
unexplained Valyu credit burn (see hermes `docs/ARGUS-VALVU-AUDIT.md`):
automated callers (Maya jobs, Hermes) are capped at tier 1 so one-time
credits (tier 3) can only be spent by interactive/uncapped callers.

### Canonical deployment

One Argus for the fleet: digest-addressed Homelab Docker, with HTTP and MCP
host backends on loopback ports 8270/8271 and tailnet-only Tailscale Serve
HTTPS ingress. PostgreSQL and SearXNG remain Docker-internal. The Mac is
development only; Mac launchd, OCI, Maya, and the host residential worker are
retired and are not fallbacks. See the
[production operations guide](docs/operations.md); ADR 0001 is superseded.
<!-- janitor:begin:recent -->
Active development focus is on operational deployment runbooks, infrastructure documentation, and provider resilience mechanisms. Recent commits introduced promotion and scorecard admission runbooks in DEPLOYMENT.md, documented infrastructure topology in INFRA.md, and activated self-healing circuit breakers with monthly-first tier routing.

- Added `docs/DEPLOYMENT.md` establishing the runbook for release promotion and scorecard admission handoff (73bff53).
- Implemented and enabled the self-healing circuit breaker and monthly-first tier routing (15b8c7a, a8be4df).
- Added `docs/INFRA.md` and updated G2K model and platform naming in `AGENTS.md` (4ecdcf1).
- Working tree is clean on `main` at 73bff53; no dirty or untracked files.
<!-- janitor:end:recent -->
<!-- janitor:begin:branches -->
## Branch and Worktree Review
Base: refs/remotes/origin/main @ 73bff53ff6c7bc51ef11bc5a488a76a2dce679d4
Freshness: current

| Branch | Class | Sources | Merged | Ahead/behind | Worktree | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| auto-wip/20260912-210255 | abandoned_auto_wip | local | yes | +0/-19 | unattached | subject: docs: record Argus restoration result [skip ci] |
| fix/self-healing-monthly-first-routing | active | local | yes | +0/-2 | unattached | subject: docs: add docs/INFRA.md and update G2K naming in AGENTS.md |
| feat/auth-browser-domains-148 | active | local | yes | +0/-7 | unattached | subject: feat: add scoped paywall browser exception (#148) |
| backup-local-main | aging | local | yes | +0/-19 | unattached | subject: docs: record Argus restoration result [skip ci] |
| codex/private-service-transport-20260903 | aging | local, origin/codex/private-service-transport-20260903 | no | +30/-51 | unattached | subject: ci: retrigger validation for private transport fix; paths: .env.example, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, .github/workflows/publish.yml, .gitignore |
| codex/production-stabilization-20260902 | aging | local, origin/codex/production-stabilization-20260902 | no | +30/-51 | unattached | subject: ci: retrigger validation for private transport fix; paths: .env.example, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, .github/workflows/publish.yml, .gitignore |
| codex/foundational-agent-tooling-2026-08-26 | aging | local | no | +1/-55 | unattached | subject: docs: standardize foundational agent tooling; paths: AGENTS.md |
| codex/agy-cli-operational-rules-2026-08-24 | aging | local, origin/codex/agy-cli-operational-rules-2026-08-24 | no | +1/-55 | missing | subject: docs: add agy CLI operating contract; paths: AGENTS.md |
| codex/argus-homelab-reliability-design | aging | local, origin/codex/argus-homelab-reliability-design | yes | +0/-62 | unattached | subject: docs: record research admission decisions |
| codex/mcp-v2-stateless-20260811 | stale | local | no | +6/-138 | unattached | subject: docs: design MCP v2 stateless migration; paths: CONTEXT.md, README.md, argus/api/schemas.py, argus/config.py, deploy/README.md |
| codex/mcp-stateless-clio-retirement-20260811 | stale | local, origin/codex/mcp-stateless-clio-retirement-20260811 | no | +5/-138 | unattached | subject: docs: index MCP compatibility research; paths: CONTEXT.md, README.md, argus/api/schemas.py, argus/config.py, deploy/README.md |
| codex/acceptance-v3 | stale | local | no | +42/-77 | unattached | subject: fix: make acceptance contract tests portable; paths: argus/__init__.py, argus/acceptance_v3/__init__.py, argus/acceptance_v3/bundle.py, argus/acceptance_v3/contract.py, argus/acceptance_v3/observations.py |
| codex/argus-tonight-reports | stale | local | no | +2/-78 | unattached | subject: docs: publish Argus acceptance and managed-stack verdict; paths: docs/research/2026-08-09-argus-managed-stack-evaluation.md, docs/research/2026-08-09-argus-market-landscape.md, docs/research/2026-08-09-argus-tonight-acceptance.md |
| codex/promotion-ssh-keepalive | stale | local | no | +3/-79 | unattached | subject: test: bound promotion transport retries; paths: .github/workflows/docker-publish.yml, tests/test_release_workflow.py |
| codex/workflow-runtime-manifest-fix | stale | local | yes | +0/-80 | unattached | subject: fix: persist workflow runtime identity |
| codex/workflow-reliability-fix | stale | local | yes | +0/-82 | unattached | subject: fix(runtime): expose immutable identity and caller labels |
| codex/runtime-identity-label-fix | stale | local | no | +1/-85 | clean | subject: fix(runtime): expose immutable identity and caller labels; paths: argus/broker/execution.py, argus/broker/readiness.py, argus/operations/status.py, argus/persistence/readiness.py, tests/test_operational_status.py |
| codex/promote-transport-fix | stale | local | yes | +0/-85 | unattached | subject: Merge pull request #116 from Khamel83/codex/fix-hard-page-limit |
| codex/fix-hard-page-limit | stale | local | yes | +0/-86 | unattached | subject: Fix site acquisition search result bound |
| codex/maya-outbox-timeout-fix | stale | local | yes | +0/-88 | unattached | subject: docs: note Maya outbox timeout fix |
| codex/argus-tonight-reliability | stale | local | yes | +0/-91 | unattached | subject: fix: sanitize MCP workflow start JSON |
| codex/fix-fetch-raw-playwright-event | stale | local | yes | +0/-105 | unattached | subject: fix: ignore safely blocked third-party resources |
| codex/fix-production-tailnet | stale | local | yes | +0/-108 | unattached | subject: test: update tailnet deployment contract |
| codex/fetch-raw-tix | stale | local | yes | +0/-111 | unattached | subject: fix: validate raw fetch DNS answers |
| codex/finalize-wayfinder-docs | stale | local | yes | +0/-118 | unattached | subject: docs: record completed Wayfinder P1 rollout |
| codex/fix-idle-retrieval-readiness | stale | local | yes | +0/-120 | unattached | subject: fix: keep idle retrieval path ready |
| codex/fix-current-recovery-schema-contract | stale | local | no | +1/-122 | unattached | subject: fix(recovery): verify restored database at current schema head; paths: argus/recovery/records.py, tests/test_recovery_records.py |
| codex/fix-v2-session-id-width | stale | local | no | +1/-123 | unattached | subject: fix(api): fit signed retrieval sessions in durable schema; paths: argus/api/security.py, tests/fixtures/contracts/retrieval_evidence_v2/manifest.json, tests/test_accepted_operations.py |
| codex/fix-production-readiness-active-scope | stale | local | yes | +0/-124 | unattached | subject: fix: finalize production readiness and operations |
| codex/wayfinder-p1-promotion | stale | local | no | +13/-126 | unattached | subject: fix: bind accepted invocation accounting; paths: .superpowers/sdd/2026-07-27-retrieval-evidence-mechanical-port/task-16-attempt-identity-report.md, argus/api/contracts_v2.py, argus/broker/accepted.py, argus/broker/execution.py, argus/broker/router.py |
| codex/wayfinder-candidate-evidence | stale | local | no | +5/-127 | unattached | subject: fix: independently verify scorecard evidence; paths: .github/workflows/ci.yml, .github/workflows/scorecard-live.yml, CONTRIBUTING.md, argus/api/admin_operations.py, argus/api/admin_presenters.py |
| codex/wayfinder-workflow-safety | stale | local | no | +7/-128 | unattached | subject: fix: enforce hostname boundary during site admission; paths: argus/api/main.py, argus/contracts/result_refs.py, argus/development_mcp_tools.py, argus/extraction/composition.py, argus/operations/accepted.py |
| codex/wayfinder-client-transports | stale | local | no | +16/-129 | unattached | subject: style: format client transport changes; paths: .env.example, argus/api/routes_health.py, argus/authority.py, argus/capabilities.py, argus/cli/main.py |
| codex/wayfinder-http-authority | stale | local | no | +13/-130 | unattached | subject: fix: serialize PostgreSQL cache publication; paths: .env.example, CHANGELOG.md, README.md, argus/api/contracts_v2.py, argus/api/main.py |
| codex/wayfinder-s6-accepted-retrieval | stale | local | no | +54/-131 | unattached | subject: Merge remote-tracking branch 'origin/main' into codex/wayfinder-s6-accepted-retrieval; paths: .github/workflows/ci.yml, .superpowers/sdd/task-7-report.md, argus/broker/accepted.py, argus/broker/cache.py, argus/broker/execution.py |
| codex/wayfinder-s5-readiness-authority | stale | local | no | +47/-132 | unattached | subject: test: preserve recurring spend without boundary; paths: .github/workflows/ci.yml, .github/workflows/publish.yml, CONTRIBUTING.md, argus/api/routes_admin.py, argus/api/routes_dashboard.py |
| codex/wayfinder-s4-evidence-fusion | stale | local | no | +32/-133 | unattached | subject: test: wait for Maya delivery observation; paths: .superpowers/sdd/task-5-report.md, argus/broker/fusion.py, argus/broker/pipeline.py, argus/broker/provider_evidence.py, argus/models.py |
| codex/wayfinder-s3-extraction-finalization | stale | local | no | +28/-134 | unattached | subject: Merge remote-tracking branch 'origin/main' into codex/wayfinder-s3-extraction-finalization; paths: .superpowers/sdd/task-4-report.md, argus/extraction/archive_extractor.py, argus/extraction/cache.py, argus/extraction/composition.py, argus/extraction/extractor.py |
| codex/wayfinder-remaining-plan-validation | stale | local | no | +1/-136 | unattached | subject: docs: make Wayfinder completion closure-driven; paths: docs/superpowers/plans/2026-07-27-retrieval-evidence-mechanical-port.md |
| codex/wayfinder-s2-provider-evidence | stale | local | no | +17/-136 | unattached | subject: Merge origin/main into S2 provider evidence; paths: argus/broker/__init__.py, argus/broker/execution.py, argus/broker/provider_evidence.py, argus/broker/reachability.py, argus/broker/router.py |
| codex/wayfinder-s1-retrieval-planning | stale | local | no | +5/-138 | unattached | subject: fix: preserve invalid retrieval outcomes; paths: argus/broker/execution.py, argus/broker/pipeline.py, argus/broker/planning.py, argus/broker/policies.py, argus/broker/router.py |
| codex/wayfinder-s0-contract-kernel | stale | local | no | +2/-138 | unattached | subject: fix: harden accepted operation contracts; paths: argus/contracts/__init__.py, argus/contracts/outcomes.py, docs/prototypes/retrieval-evidence-envelope/README.md, tests/fixtures/contracts/retrieval_evidence_v2/invalid_cache_hit_drops_origin_attempts.json, tests/fixtures/contracts/retrieval_evidence_v2/invalid_cache_hit_is_older_than_the_accepted_maximum.json |
| codex/issue-67-mechanical-plan | stale | local | no | +40/-139 | unattached | subject: Merge main after evidence prototype; paths: docs/README.md, docs/superpowers/plans/2026-07-27-retrieval-evidence-mechanical-port.md |
| codex/issue-65-evidence-envelope | stale | local | no | +29/-140 | unattached | subject: Merge main after transport contract; paths: docs/README.md, docs/prototypes/retrieval-evidence-envelope/NOTES.md, docs/prototypes/retrieval-evidence-envelope/README.md, docs/prototypes/retrieval-evidence-envelope/envelope.schema.json, docs/prototypes/retrieval-evidence-envelope/model.py |
| codex/issue-66-http-mcp-contract | stale | local | no | +26/-141 | unattached | subject: Merge main after extraction contract; paths: docs/README.md, docs/adr/0006-http-mcp-compatibility-contract.md, docs/research/2026-07-27-http-mcp-compatibility-matrix.md, docs/research/2026-07-27-http-mcp-compatibility-primary-sources.md |
| codex/issue-63-extraction-integration | stale | local | no | +24/-142 | unattached | subject: Merge main after readiness policy; paths: docs/README.md, docs/adr/0005-structured-extraction-outcome-composition.md, docs/research/2026-07-27-extraction-outcome-compatibility-matrix.md |
| codex/issue-64-no-spend-health | stale | local | no | +17/-143 | unattached | subject: Merge main after ranking policy; paths: docs/README.md, docs/adr/0004-no-spend-provider-readiness.md, docs/research/2026-07-27-provider-health-probe-matrix.md |
| codex/issue-62-ranking-policy | stale | local | no | +13/-144 | unattached | subject: Merge main after retrieval plan review; paths: docs/README.md, docs/adr/0002-bounded-retrieval-plan-cache-identity.md, docs/adr/0003-provider-aware-freshness-provenance-ranking.md, docs/research/2026-07-27-provider-ranking-signals.md |
| codex/issue-61-retrieval-plan | stale | local | no | +7/-149 | unattached | subject: Clarify retrieval lifecycle edge contracts; paths: docs/README.md, docs/adr/0002-bounded-retrieval-plan-cache-identity.md, docs/research/2026-07-26-provider-extraction-contract-drift.md |
| codex/issue-57-structured-rejections | stale | local | no | +3/-149 | unattached | subject: Keep diagnostic evidence out of operation identity; paths: argus/api/routes_extract.py, argus/api/schemas.py, argus/extraction/rejection.py, argus/persistence/search_ledger.py, docs/README.md |
| codex/issue-41-immutable-promotion | stale | local | no | +2/-149 | unattached | subject: Use Node 24 GitHub actions; paths: .github/workflows/ai-review.yml, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, .github/workflows/publish.yml, docs/releasing.md |
| codex/issue-69-doc-audit | stale | local | no | +1/-149 | unattached | subject: Inventory documentation and production drift; paths: docs/research/2026-07-26-documentation-contract-production-drift.md |
| codex/issue-60-contract-drift | stale | local | no | +1/-149 | unattached | subject: Document provider and extraction contract drift; paths: docs/research/2026-07-26-provider-extraction-contract-drift.md |
| codex/issue-59-scorecard | stale | local | no | +1/-150 | unattached | subject: docs: define Argus evidence scorecard; paths: CONTEXT.md, docs/README.md, docs/scorecards/stability-competitive.md |
| codex/issue-40-production-closeout | stale | local | yes | +0/-150 | unattached | subject: Harden containerized PostgreSQL recovery operations (#68) |
| codex/argus-production-hardening | stale | local | no | +2/-155 | unattached | subject: stabilize recovery review follow-up tests; paths: argus/recovery/records.py, ops/postgres/backup_shared_postgres.sh, ops/postgres/postgres_recovery.py, ops/postgres/verify_restore.sh, tests/test_postgres_recovery_artifacts.py |
| codex/new | stale | local | no | +14/-169 | unattached | subject: docs: record issue 39 completion locally; paths: CONTEXT.md, docs/adr/0001-canonical-deployment.md, docs/adr/0002-homelab-production-topology.md, docs/research/authoritative-state/research.md, docs/research/browser-capability/research.md |
| local/argus-reliability-planning-2026-07-22 | stale | local, origin/local/argus-reliability-planning-2026-07-22 | no | +14/-169 | unattached | subject: docs: record issue 39 completion locally; paths: CONTEXT.md, docs/adr/0001-canonical-deployment.md, docs/adr/0002-homelab-production-topology.md, docs/research/authoritative-state/research.md, docs/research/browser-capability/research.md |
| claude/argus-pr56-issue40-auth-f6f31a | stale | local | yes | +0/-157 | unattached | subject: feat: expose truthful operational status [skip ci] |
| codex/issue-39-operational-status | stale | local | no | +12/-158 | unattached | subject: test: fix PostgreSQL outbox clock determinism; paths: .env.example, Dockerfile, README.md, argus/api/lifecycle.py, argus/api/main.py |
| codex/issue-37-http-authority | stale | local | no | +2/-159 | unattached | subject: fix: bind extraction records to authenticated callers; paths: .env.example, AGENTS.md, CONTRIBUTING.md, README.md, argus/api/main.py |
| codex/issue-36-maya-outbox | stale | local | no | +10/-160 | unattached | subject: fix: advance recovery verifier to Maya schema; paths: .env.example, .github/workflows/ci.yml, README.md, argus/api/main.py, argus/api/routes_admin.py |
| codex/issue-40-shared-postgres-recovery | stale | local | no | +11/-161 | unattached | subject: Bind retention plans and scrub public ACLs; paths: .github/workflows/ci.yml, argus/api/routes_health.py, argus/recovery/__init__.py, argus/recovery/artifacts.py, argus/recovery/database.py |
| codex/issue-21-parallel-tier | stale | local | no | +2/-162 | unattached | subject: docs: clarify Parallel monthly credit eligibility; paths: .env.example, AGENTS.md, README.md, argus/broker/budgets.py, argus/broker/policies.py |
| codex/issue-35-provider-spend | stale | local | no | +8/-163 | unattached | subject: fix: audit provider charge overruns on settlement; paths: .env.example, .github/workflows/ci.yml, argus/api/main.py, argus/api/routes_admin.py, argus/api/routes_search.py |
| codex/issue-34-extraction-sessions | stale | local | no | +5/-164 | unattached | subject: fix: close cross-worker session races; paths: .github/workflows/ci.yml, README.md, argus/api/routes_dashboard.py, argus/api/routes_extract.py, argus/api/schemas.py |
| codex/issue-38-chromium-capability | stale | local | no | +5/-166 | unattached | subject: fix: make browser admission and canaries tamper-evident; paths: .github/workflows/ci.yml, Dockerfile, argus/api/routes_health.py, argus/extraction/playwright_extractor.py, argus/runtime_manifest.py |
| codex/issue-43-trafilatura-normalization | stale | local | no | +1/-166 | unattached | subject: fix(extraction): normalize Trafilatura results; paths: argus/extraction/archive_extractor.py, argus/extraction/auth_extractor.py, argus/extraction/extractor.py, argus/extraction/residential_service.py, argus/extraction/trafilatura_result.py |
| codex/issue-32-hermetic-ci | stale | local | no | +6/-168 | unattached | subject: fix(ci): run manifest build in project environment; paths: .env.example, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, Dockerfile, argus/broker/router.py |
| codex/issue-22-playwright-lifecycle | stale | local | no | +1/-169 | unattached | subject: fix(extraction): make playwright lifecycle safe; paths: argus/api/main.py, argus/extraction/playwright_extractor.py, tests/test_playwright_lifecycle.py |
| clio-agent/issue-18 | stale | local, origin/clio-agent/issue-18 | yes | +0/-183 | unattached | subject: docs: add AGENTS.md — MCP + HTTP agent usage contract |
| clio-agent/issue-19 | stale | local, origin/clio-agent/issue-19 | yes | +0/-183 | unattached | subject: docs: add AGENTS.md — MCP + HTTP agent usage contract |
| clio-agent/issue-20 | stale | local, origin/clio-agent/issue-20 | yes | +0/-183 | unattached | subject: docs: add AGENTS.md — MCP + HTTP agent usage contract |
| fix/residential-endpoints-to-egress-nodes | stale | origin/fix/residential-endpoints-to-egress-nodes | no | +1/-189 | unattached | subject: fix: migrate residential_extractor from config.residential.endpoints to config.egress_nodes; paths: argus/extraction/residential_extractor.py, tests/test_residential_extractor.py |
| chore/dashboard-design-sweep | stale | origin/chore/dashboard-design-sweep | no | +10/-216 | unattached | subject: docs: add dashboard-design.md reference; paths: .github/CODEOWNERS, .github/ISSUE_TEMPLATE/bug_report.md, .github/ISSUE_TEMPLATE/bug_report.yml, .github/ISSUE_TEMPLATE/config.yml, .github/ISSUE_TEMPLATE/extraction_failure.yml |
| chore/public-readiness-sweep | stale | origin/chore/public-readiness-sweep | no | +7/-216 | unattached | subject: docs: log this sweep in CHANGELOG and beef up CONTRIBUTING; paths: .github/CODEOWNERS, .github/ISSUE_TEMPLATE/bug_report.md, .github/ISSUE_TEMPLATE/bug_report.yml, .github/ISSUE_TEMPLATE/config.yml, .github/ISSUE_TEMPLATE/extraction_failure.yml |
| main | active | local, origin/main | yes | +0/-0 | clean | subject: docs: add DEPLOYMENT.md runbook for promotion and scorecard admission |
| codex/argus-readiness-20260906 | aging | local | yes | +0/-11 | clean | subject: fix: refresh retrieval evidence manifest hash |
| codex/final-operational-docs | aging | local | no | +1/-48 | clean | subject: docs: document additive schema bridge rollout; paths: docs/operations.md |
| codex/argus-compat-bridge | aging | local, origin/codex/argus-compat-bridge | no | +1/-53 | clean | subject: fix: keep rollback bridge compatible with additive schema; paths: argus/recovery/database.py, tests/test_recovery_database.py |
| codex/private-service-transport-clean-20260903 | aging | local | yes | +0/-49 | clean | subject: fix: allow guarded private service transport |
| codex/extraction-spend-20260902 | aging | local, origin/codex/extraction-spend-20260902 | no | +16/-51 | clean | subject: fix extraction spend evidence and cache ordering; paths: .env.example, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, .github/workflows/publish.yml, argus/acquisition/__init__.py |
| codex/provider-transport-20260902 | aging | local, origin/codex/provider-transport-20260902 | no | +17/-51 | clean | subject: feat: route provider adapters through guarded transport; paths: .env.example, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, .github/workflows/publish.yml, argus/acquisition/__init__.py |
| codex/transport-parity-20260902 | aging | local, origin/codex/transport-parity-20260902 | no | +15/-51 | clean | subject: fix: align HTTP MCP and CLI retrieval transports; paths: .env.example, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, .github/workflows/publish.yml, argus/acquisition/__init__.py |
| codex/acquisition-foundation-20260902 | aging | local | no | +4/-51 | clean | subject: feat: route acquisition paths through guarded seam; paths: argus/acquisition/__init__.py, argus/acquisition/browser_policy.py, argus/acquisition/dns.py, argus/acquisition/errors.py, argus/acquisition/guarded.py |
| codex/persistence-foundation-20260902 | aging | local, origin/codex/persistence-foundation-20260902 | no | +4/-51 | clean | subject: feat: bind schema identity and restore promotion; paths: .superpowers/sdd/persistence-registry-report.md, argus/extraction/domain_memory.py, argus/persistence/db.py, argus/persistence/domain_policy.py, argus/persistence/registry.py |
| codex/release-identity-20260902 | aging | local, origin/codex/release-identity-20260902 | no | +11/-51 | clean | subject: fix: fail release on publication and identity drift; paths: .env.example, .github/workflows/ci.yml, .github/workflows/docker-publish.yml, .github/workflows/publish.yml, argus/acquisition/__init__.py |
| codex/provider-registration-20260902 | aging | local, origin/codex/provider-registration-20260902 | no | +9/-51 | clean | subject: feat: make provider registration authoritative; paths: .env.example, argus/acquisition/__init__.py, argus/acquisition/browser_policy.py, argus/acquisition/dns.py, argus/acquisition/errors.py |
| codex/workflow-ownership-20260902 | aging | local | no | +5/-51 | clean | subject: fix: enforce workflow owner reads and singleton artifact ownership; paths: argus/acquisition/__init__.py, argus/acquisition/dns.py, argus/acquisition/errors.py, argus/acquisition/models.py, argus/acquisition/transport.py |
| codex/argus-audit-public-split-20260901 | aging | local | no | +1/-52 | clean | subject: docs: publish public audit status; paths: .audit/AUDIT_REPORT.md, .audit/HEALTH_CHECK_RUNBOOK.md, .audit/SYSTEM_TOPOLOGY.md, .gitignore, AGENTS.md |
| codex/argus-webpage-profile-20260829 | aging | local | no | +2/-54 | clean | subject: fix(extraction): isolate legacy quality caches; paths: argus/api/schemas.py, argus/extraction/extractor.py, argus/extraction/quality_gate.py, argus/operations/accepted.py, tests/test_webpage_profile.py |
| fix/log-persistence-failure | aging | origin/fix/log-persistence-failure | no | +2/-54 | unattached | subject: fix(persistence): add the missing domain_policies migration; paths: argus/operations/accepted.py, migrations/versions/0010_domain_policies.py |
| codex/argus-homelab-mcp-repair | aging | local, origin/codex/argus-homelab-mcp-repair | no | +10/-67 | clean | subject: docs: record full-suite limitation; paths: README.md, argus/cli/main.py, docs/argus-visual-overview.html, docs/mcp-clients.md, docs/superpowers/plans/2026-08-19-argus-visual-overview.md |
| codex/mcp-v2-stateless-latest | stale | origin/codex/mcp-v2-stateless-latest | yes | +0/-71 | unattached | subject: feat: support MCP 2026-07-28 stateless transport |
| clio-agent/issue-15 | stale | origin/clio-agent/issue-15 | yes | +0/-186 | unattached | subject: fix(config): auto-load repo .env for cli/api/mcp and document behavior |
| claude/argus-usage-dashboard-D3rbv | stale | origin/claude/argus-usage-dashboard-D3rbv | yes | +0/-219 | unattached | subject: feat: add usage dashboard at /dashboard |
| codex/argus-atlas-integration | stale | origin/codex/argus-atlas-integration | yes | +0/-229 | unattached | subject: feat: persist workflow status and extraction mode |

Detached worktrees:
- /Users/macmini/.codex/worktrees/29b9/argus (dirty @ 8d471f6090abeaff0106c4716d80d35fbb4e4147)
<!-- janitor:end:branches -->
