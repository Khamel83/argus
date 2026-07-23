# Argus Audit and Work Handoff

Last updated: 2026-07-23 America/Los_Angeles

## Purpose

This document is the durable handoff for the broad Argus audit requested before
working the incoming production issue. It consolidates confirmed failures,
operational evidence, all open GitHub work, and recommended execution order.

The audit is now in execution. Code fixes are developed natively on the Mac,
reviewed on isolated issue branches, and built in GitHub Actions. No production
deployment or Mac-side Docker/Compose execution has been performed. One
separately authorized, disposable, network-isolated homelab canary validated
issue #22 and was fully removed afterward without touching production. This
branch remains the local-only planning record and must never be pushed.

The canonical planning artifact is the
[Wayfinder: make Argus a reliable homelab retrieval platform for Maya](https://github.com/Khamel83/argus/issues/23)
map. The architecture is decision-complete. This handoff retains audit
evidence, operational constraints, and the implementation ledger; the map owns
the decision index and closes after the approved implementation issues are
linked.

## Handoff Maintenance Contract

Use this file as the task ledger until the audit work is exhausted:

- Add newly confirmed work here before starting it.
- Change a task to complete only after its acceptance evidence is recorded.
- Link to issues, plans, commits, or reports instead of copying their full
  contents here.
- Keep production evidence sanitized.
- Evaluate changes as fleet contracts: reuse health states, deployment gates,
  durable-event semantics, authentication boundaries, capability reporting,
  and resource ownership across homelab services instead of creating Argus-only
  exceptions.
- Do not install, start, configure, or invoke Docker/Compose on the Mac mini.
  Mac development uses native tooling; image builds and container/resource
  canaries run in GitHub Actions or on the homelab only when separately
  authorized.
- If an item is deliberately declined, record the decision and its replacement
  control.

Approved implementation backlog:

Immediate frontier:

- [x] [Fix failed Playwright launch leaks and the container OOM](https://github.com/Khamel83/argus/issues/22)
  — closed after lifecycle cleanup and the 512 MiB missing-browser regression
  were proven.
  - Merged implementation PR:
    [fix(extraction): make Playwright lifecycle safe](https://github.com/Khamel83/argus/pull/45).
    Controller verification passed 356 tests plus focused lifecycle,
    compilation, diff, privacy, and secret checks. It was manually squash
    merged as `efb7399` with push workflows skipped so the legacy automatic
    deploy did not run. The sanitized disposable 512 MiB homelab canary then
    completed 20 sequential attempts with one runtime start, 19 cached skips,
    zero orphan processes, zero OOM events/restarts, 49/49 passing health
    samples, and a 146.97 MiB peak. All canary resources were removed and the
    issue closed. Evidence:
    https://github.com/Khamel83/argus/issues/22#issuecomment-5055229365
- [x] [Make Argus configuration and image CI hermetic](https://github.com/Khamel83/argus/issues/32)
  — merged implementation PR
  [ci: make configuration and image builds hermetic](https://github.com/Khamel83/argus/pull/46)
  as `3df20e6`. The Python 3.11–3.13 matrix, production configuration,
  freshness, and actual GitHub-hosted production image build all passed. The
  main commit used `[skip ci]`, so the legacy automatic deployment workflow did
  not run.
- [x] [Record accepted search retrievals transactionally in PostgreSQL](https://github.com/Khamel83/argus/issues/33)
  — merged PR
  [feat: record accepted searches transactionally](https://github.com/Khamel83/argus/pull/47)
  as `b8c3e1f`. The Python matrix, real PostgreSQL concurrency/rollback suite,
  production configuration, freshness, and production image build passed.
  The merge used `[skip ci]`; no deployment workflow ran.
- [x] [Accept idempotent parent-and-child Argus retrieval captures](https://github.com/Khamel83/maya/issues/82)
  — merged Maya PR
  [feat: add durable Argus retrieval captures](https://github.com/Khamel83/maya/pull/83)
  as `adb9770`. Controller verification passed 1,328 tests plus focused API,
  auth, migration, lint, type, compile, diff, and secret checks. No deployment
  or secret change was performed; issue 36 owns Argus client delivery and
  operator provisioning of the shared scoped credential.

State and integration:

- [x] [Persist extraction and session operations in the authoritative ledger](https://github.com/Khamel83/argus/issues/34)
  — merged PR
  [feat: persist extraction and sessions transactionally](https://github.com/Khamel83/argus/pull/51)
  as `43f28dc`. The authoritative extraction/session ledger now fails closed in
  persistent mode, records cache attempts truthfully, sanitizes URLs before
  idempotency checks, and uses conflict-safe PostgreSQL/SQLite writes for
  cross-worker session and URL concurrency. Two independent rereview rounds
  closed seven Important findings. Final evidence was 459 passed and 9 skipped
  locally plus all seven CI jobs, including real PostgreSQL concurrency and
  both production image canaries. The `[skip ci]` merge triggered no workflow
  or deployment.
- [ ] [Reserve and reconcile paid-provider spending durably](https://github.com/Khamel83/argus/issues/35)
  — review fixes are in implementation on an isolated issue branch, rebasing
  onto issue 34 as the next migration.
- [ ] [Deliver user-visible retrievals to Maya through a transactional outbox](https://github.com/Khamel83/argus/issues/36)
  — both blockers are closed; implementation has started from `43f28dc` on an
  isolated issue branch.
- [ ] [Make HTTP the sole Argus execution authority and MCP stateless](https://github.com/Khamel83/argus/issues/37)
  — blocked by durable provider accounting and the Maya outbox.

Capability, recovery, and operations:

- [x] [Ship the declared Chromium capability and pass bounded browser canaries](https://github.com/Khamel83/argus/issues/38)
  — merged PR
  [feat: ship sandboxed Chromium capability](https://github.com/Khamel83/argus/pull/49)
  as `f84565f`. Tamper-evident browser admission, truthful loaded/degraded
  status, sandbox/nonroot enforcement, and Chromium plus Playwright-driver
  cleanup were independently approved. The final 1 GiB canary completed 20/20
  browser extractions at 338.34 MiB peak; the 512 MiB missing-browser
  regression peaked at 80.08 MiB. Both had zero OOMs and zero orphan runtime
  processes. The `[skip ci]` merge triggered no deployment.
- [ ] [Expose truthful readiness, runtime identity, and bounded operational evidence](https://github.com/Khamel83/argus/issues/39)
  — blocked by the sole HTTP authority and declared browser capability.
- [ ] [Run Argus on shared homelab PostgreSQL with verified recovery](https://github.com/Khamel83/argus/issues/40)
  — blocked by the search and extraction/session ledgers.
- [ ] [Promote immutable homelab releases with rollback proof](https://github.com/Khamel83/argus/issues/41)
  — blocked by hermetic image CI, truthful health/identity, and verified
  PostgreSQL recovery.
- [ ] [Cut over private homelab production and retire duplicate authorities](https://github.com/Khamel83/argus/issues/42)
  — blocked by Maya delivery, the sole HTTP authority, truthful status, shared
  PostgreSQL recovery, and immutable promotion.
- [ ] [Publish the Argus production operations and recovery runbook](https://github.com/Khamel83/argus/issues/44)
  — blocked by the implemented health, recovery, promotion, and cutover
  interfaces.

Independent follow-ons:

- [ ] [Correct Parallel's recurring-credit tier](https://github.com/Khamel83/argus/issues/21)
  — blocked by durable paid-provider accounting.
- [x] [Normalize Trafilatura output and audit extraction quality](https://github.com/Khamel83/argus/issues/43)
  — merged PR
  [fix: normalize Trafilatura results and preserve quality decisions](https://github.com/Khamel83/argus/pull/48)
  as `afa43f8`. All Trafilatura callers now share an allowlisted normalizer;
  the sanitized quality observation was reproduced and corrected without
  weakening low-quality rejection or safety/spend controls. All seven PR CI
  jobs passed, and the `[skip ci]` merge triggered no deployment.

GitHub's native `blocked by` relationships are the authoritative dependency
graph. Work any issue whose native blockers are all closed; do not infer
permission to bypass a failed acceptance gate from this summary.

Completed planning decisions:

- [x] [Define the Maya–Argus ownership and invocation contract](https://github.com/Khamel83/argus/issues/24):
  Maya is the durable system of record for every
  user-visible retrieval. Argus owns useful internal operational telemetry.
  Delivery uses an idempotent durable outbox, one parent retrieval capture with
  linked extracted-page children, and an explicit sanitized dead-letter path.
- [x] [Reconstruct the OCI dependency and choose Argus degraded mode](https://github.com/Khamel83/argus/issues/25):
  OCI is removed from Argus entirely. It was never a
  configured production egress worker; its only live Argus role is the GitHub
  Actions SSH jump to homelab. Replace that path, keep homelab self-contained,
  and treat direct Yahoo as opportunistic.
- [x] [Set Argus production reliability targets and evidence gates](https://github.com/Khamel83/argus/issues/26):
  Argus is a personal-production service with 99.5%
  monthly readiness, five-minute unattended container recovery, zero loss of
  acknowledged durable state, explicit ready/degraded/unready semantics, and
  blocking acceptance gates. Full matrix:
  https://github.com/Khamel83/argus/issues/26#issuecomment-5053978717
- [x] [Choose the container browser and extraction capability contract](https://github.com/Khamel83/argus/issues/27):
  production ships one version-matched Chromium
  headless-shell capability with serialized lifecycle ownership, fresh
  per-request contexts, sandboxing, truthful capability state, and graceful
  cleanup. Start the browser-enabled HTTP container at 1 GiB; preserve 512 MiB
  as the missing-browser leak regression. Research:
  `docs/research/browser-capability/research.md`.
- [x] [Choose the authoritative state and observability model](https://github.com/Khamel83/argus/issues/28):
  the authenticated HTTP service is the sole production
  execution authority and PostgreSQL is the sole homelab operational store.
  MCP and other fleet callers use HTTP. SQLite remains for isolated development
  and standalone installs. Retrieval history, attempts, actual budget charges,
  sessions, state snapshots, and the Maya outbox share one transactional
  persistence interface. The existing PostgreSQL 16 server is reused with a
  separate Argus database and roles, then promoted from its Atlas-specific name
  into a shared homelab service. Atlas already has daily logical dumps and one
  verified isolated restore; extend that path to Argus and cluster globals.
  Research: `docs/research/authoritative-state/research.md`.
- [x] [Choose the homelab production topology and transport ownership](https://github.com/Khamel83/argus/issues/29):
  homelab Docker runs one production authority behind
  Tailscale. The API owns the broker, browser, PostgreSQL writes, budgets,
  health, and Maya outbox; MCP is a stateless internal HTTP adapter. SearXNG
  and the shared PostgreSQL service remain internal. Mac launchd, OCI, and the
  host residential worker are not production authorities. Decision:
  `docs/adr/0002-homelab-production-topology.md`.
- [x] [Define deployment promotion, rollback, and runtime identity gates](https://github.com/Khamel83/argus/issues/30):
  build and test one attested GHCR digest, then promote it through an ephemeral
  Tailscale deployment identity and one locked homelab command. Prove the
  requested digest, local image, running container, and loaded application
  agree; admit only after isolated fault/resource/restart drills and a
  30-minute tailnet soak. Retain the previous schema-compatible digest for
  automatic application rollback. Research:
  `docs/research/deployment-gates/research.md`.
- [x] [Approve the decision-complete Argus homelab reliability design](https://github.com/Khamel83/argus/issues/31):
  the user approved the integrated architecture and authorized an incremental,
  dependency-ordered implementation backlog. Stabilize the current browser
  failure first, establish authoritative state and execution next, complete
  Maya/MCP integration, then cut over the private topology and finish
  deployment and operational hardening.

Accepted production topology and remaining implementation obligations:

- [x] Reuse the existing PostgreSQL 16 server as the shared homelab database
  platform. Atlas and Argus are isolated tenant databases with separate roles;
  `atlas-postgres` remains a compatibility alias during migration.
- [x] Keep production Argus private behind Tailscale: internal
  Docker/loopback paths serve homelab components, while approved remote callers
  such as the Mac mini and Maya use authenticated Tailscale access. No public
  Cloudflare/Funnel or general-LAN publication.
- [ ] Make the API container the sole production broker, browser, persistence,
  budget, health, and Maya-outbox authority. Make MCP a stateless adapter with
  no broker, provider keys, database credentials, browser, or writable volume.
- [ ] Bind API and MCP backends to host loopback and configure persistent
  Tailscale Serve HTTPS with explicit access controls. Verify there is no
  Funnel, Cloudflare, public, or general-LAN path.
- [ ] Rename the shared database service to `homelab-postgres`, give Argus its
  own database and least-privilege roles on a private network, and retain
  `atlas-postgres` only as a migration compatibility alias.
- [ ] Remove SearXNG's LAN listener. Keep it Docker-internal, with a temporary
  loopback-only compatibility port only while legacy direct callers migrate.
- [ ] Point Maya, Hermes, and agent clients at the private Argus endpoints
  behind Tailscale, then disable the Mac launchd production services.
- [ ] Retire the live host-level `argus-residential.service` after the
  containerized browser/extraction canary passes. It currently listens on all
  interfaces at `:8124`, reports incorrect node identity, duplicates the
  homelab-residential execution path, and had processed zero requests at audit
  time.
- [ ] Rotate the residential shared secret during that cutover. The current
  systemd unit exposed it in diagnostic output; never copy its value into this
  handoff, issues, logs, or commits.

Accepted deployment-gate contract and implementation obligations:

- [ ] Stop deploying documentation-only commits and stop treating image build
  success as production authorization. CI, image admission, and production
  promotion are distinct states.
- [ ] Build the production image once from the frozen lock, pin workflow
  actions by full commit SHA, publish provenance/SBOM evidence, and pass the
  exact GHCR digest to every later gate. Mutable tags may aid discovery but
  never select production.
- [ ] Add a GitHub `production` environment and serialized deployment group.
  Use a GitHub-hosted runner with Tailscale workload identity as an ephemeral,
  narrowly permitted deployer; remove OCI and `StrictHostKeyChecking=no`.
  Initially use standard SSH over Tailscale with a pinned host key and a
  forced-command deployment key.
- [ ] Put promotion behind one root-owned, idempotent homelab command with a
  host-local lock and a recovery record outside Argus containers. The command
  accepts only an allow-listed digest, expected commit, and deployment ID; it
  does not grant the Actions runner a shell or Docker socket.
- [ ] Bake source revision, package version, schema range, lock/capability
  inventory, and browser revisions into the image. Report build, deployment,
  and per-process instance identities through health/status/usage and MCP;
  externally compare them with Docker's actual digest and image ID.
- [ ] Use a network-free liveness-only Docker healthcheck. Promotion separately
  polls startup and authenticated readiness so PostgreSQL/provider outages do
  not create restart storms.
- [ ] Run the exact digest in an isolated, non-dispatching candidate with a
  scratch PostgreSQL database, no production ingress, no Maya delivery, and no
  paid credentials. Enforce search, extraction, MCP, accounting,
  dependency-loss, abrupt/graceful restart, 512 MiB negative-browser, and
  1 GiB browser-enabled resource gates.
- [ ] For schema changes, verify a fresh dump and isolated restore, migrate the
  scratch database, and prove the previous image accepts the new schema before
  production cutover. Never automate a destructive down migration.
- [ ] After in-place cutover, verify loopback and actual Tailscale Serve paths,
  auth rejection, loaded identity, free-only search, deterministic extraction,
  durable synthetic accounting/outbox behavior, and absence of LAN/Funnel
  ingress. Synthetic deployment probes remain useful Argus operations and do
  not create normal Maya user artifacts.
- [ ] Keep promotion provisional for 30 minutes of readiness, identity,
  latency, memory, restart/OOM, browser-process, accounting, and outbox
  observation. On a blocking failure, restore the previous exact digest and
  rerun the same proof; database recovery remains an explicit incident action.

Accepted paid-provider accounting boundary:

- [x] Persist a conservative provider-specific maximum reservation before each
  paid attempt, settle it to the known actual charge after the response, and
  keep an uncertain outcome charged until provider-authoritative
  reconciliation or explicit operator resolution. Never auto-refund an
  uncertain reservation merely because it aged out.

## Executive Summary

Argus works for both search and extraction, but it is not operating as one
coherent service:

- At audit start, ADR-0001 called the Mac mini launchd deployment on HTTP
  `:8300` and MCP `:8301` canonical. ADR-0002 now supersedes it, but the
  implementation still needs the accepted homelab cutover.
- GitHub Actions still deploys `argus` and `argus-mcp` Docker services to the
  homelab after every push to `main`.
- The Codex and Claude MCP configurations point to the homelab MCP on `:8271`,
  not the mac mini MCP.
- The mac mini HTTP/MCP processes started about 15 hours before the current
  commit and are therefore running code loaded before the current checkout
  revision.
- The two deployments have different provider enablement, budget history,
  storage, and failure modes.

The intended state has since been clarified: homelab Docker is production; the
Mac mini is development only; Maya replaces Clio's retired orchestration role.
ADR-0002 formally supersedes the old Mac-primary decision.

The most urgent defect is
[GitHub issue #22](https://github.com/Khamel83/argus/issues/22): a failed
Playwright browser launch leaks runtime processes in the production container
until the 512 MiB container is OOM-killed and restarted. The issue's root-cause
analysis matches the current code and Dockerfile.

## Audited Revision

- Branch: `main`
- Commit: `f9aa1adaa219c80aef209b7e9b994333b37c3adc`
- Version: `1.6.2` in both `pyproject.toml` and `server.json`
- Worktree was clean before this handoff was added.
- Open pull requests: none.
- The pre-map product issues are
  [Parallels Free tier](https://github.com/Khamel83/argus/issues/21) and
  [failed Playwright launch leaks runtimes until container OOM restart](https://github.com/Khamel83/argus/issues/22).
- The reliability Wayfinder map and its child decision tickets are
  [make Argus a reliable homelab retrieval platform for Maya](https://github.com/Khamel83/argus/issues/23).

## What Works

### Repository and CI

- The current `main` CI run passed on Python 3.11, 3.12, and 3.13.
- The current GHCR image build/push workflow passed.
- `uv lock --check` passes.
- Python bytecode compilation passes.
- With production egress removed from the test environment,
  `ARGUS_EGRESS_TYPE=datacenter uv run pytest -q` passes all 349 tests.
- Package metadata and MCP package configuration are version-aligned.

### Mac Mini Launchd Deployment

- `com.argus.server` and `com.argus.mcp` are running.
- `GET http://127.0.0.1:8300/api/health` returns HTTP 200 and version `1.6.2`.
- Admin authentication correctly rejects missing credentials and accepts the
  configured credential.
- `argus mcp check` reports the MCP package, progress notifications, client
  configuration, and remote API key as ready.
- A live aggregate search returned five results through DuckDuckGo.
- Direct provider smoke tests succeeded for DuckDuckGo, Yahoo, and GitHub.
- A live IANA extraction returned 120 words through Playwright with complete,
  residential provenance.

### Homelab MCP Deployment

- The configured remote MCP is reachable and lists Argus tools.
- A free-only grounding search returned five results through SearXNG and
  DuckDuckGo.
- A live IANA extraction returned 112 words through Trafilatura with homelab
  provenance.
- Provider budget history is persisted and available through
  `search_budgets`.

## Confirmed Failures and Blind Spots

### P0: Playwright Runtime Leak and Container OOM

Source: [issue #22](https://github.com/Khamel83/argus/issues/22).

Confirmed in current code:

- `argus/extraction/playwright_extractor.py::_get_browser` starts a Playwright
  runtime before attempting `chromium.launch()`.
- On launch failure it returns `None` without stopping or clearing the runtime.
- It does not cache the missing-browser capability failure.
- `_get_browser` has no initialization lock, so concurrent calls can race.
- `_extract_playwright` initializes `page` but not `context`; failure before
  `context` assignment can raise again in `finally`.
- `close_browser()` exists but is not called by the HTTP application lifespan.
- The Dockerfile installs the Playwright Python package but no Chromium binary
  or browser runtime dependencies.
- The Docker build workflow only builds and pushes the image; it does not run a
  bounded extraction or memory/restart smoke test.

Do not reproduce the 20-request production canary before the lifecycle fix.
The existing issue already has sanitized OOM, exit 137, restart, and connection
reset evidence.

### P0/P1: Audit-time Split-Brain Deployment and Stale Runtime

- At audit time, ADR-0001 declared one canonical Mac mini primary and said the
  homelab Docker primary should be decommissioned. ADR-0002 now supersedes that
  decision.
- `.github/workflows/docker-publish.yml` still deploys the homelab `argus` and
  `argus-mcp` services on every main-branch push.
- Codex and Claude are configured to use homelab MCP `:8271`.
- The mac mini launchd HTTP/MCP processes started at 2026-07-22 00:27 local.
  Current HEAD was committed at 2026-07-22 15:33 local. Launchd was not
  restarted after the code update.
- The mac mini and homelab report materially different provider configuration
  and budget history.

The architecture now chooses homelab as the single production authority.
Implementation remains incomplete, so live incidents and usage reports must
still name the observed instance until the Mac and homelab deployments
converge.

### P1: Health Reporting Is Configuration Status, Not Service Health

Mac mini live evidence:

| Provider | Config/health output | Reachability or smoke result |
|---|---|---|
| SearXNG | enabled / `OK` | unreachable; direct test failed after about 12 s |
| DuckDuckGo | enabled / `OK` | reachable; direct and aggregate search passed |
| Yahoo | enabled / `OK` | background probe said unreachable; direct test passed |
| GitHub | enabled / `OK` | reachable; direct test passed |

Problems:

- `/api/health` returns `ok` if any provider is configured as enabled; it does
  not require a reachable provider or validate extraction capability.
- `argus doctor` reported four providers ready while also reporting SearXNG
  unreachable.
- `search_health` reads the failure count from the wrong dictionary level, so
  it prints zero failures even when nested health state differs.
- Reachability false negatives can make routing skip a provider that a direct
  test can use, as observed with Yahoo.
- Health state is process-local and resets on restart.
- The extraction health surface does not advertise whether Playwright has a
  usable browser binary.

### P1: Usage and Budget Reporting Is Fragmented

The endpoints and commands exist:

- HTTP: `/api/admin/health/detail`, `/api/admin/budgets`, `/dashboard`
- MCP: `search_health`, `search_budgets`, `test_provider`, `cookie_health`
- CLI: `argus health`, `argus budgets`, `argus check-balances`

However:

- `argus/api/usage.py` supports SQLite only. It returns no dashboard usage for
  PostgreSQL.
- `get_daily_query_counts()` and `get_machine_summary()` count rows in
  `search_results`, so their displayed "query" counts are result counts, not
  search-run counts.
- Extraction requests are not persisted as usage records. Only access logs
  expose their volume.
- The mac mini `.env` does not configure `ARGUS_BUDGET_DB_PATH`; its budget
  endpoint therefore reports zero use and empty token balances.
- The homelab MCP has a separate persisted budget store and nonzero history.
- CLI config currently resolves a vault-provided PostgreSQL URL on localhost,
  but no PostgreSQL server is listening. The running launchd service is writing
  the platform SQLite database instead. Configuration therefore depends on
  which secret sources were available when each process started.
- `search_budgets` prints `None` for unlimited providers and prints Valyu as
  both `unlimited` and `EXHAUSTED` because it confuses `None` with numeric zero.
- Internal budget counters are estimates. `argus check-balances` can query
  provider-specific balance endpoints, but several checkers perform a real
  minimal search and spend a credit. No scheduled balance refresh was found.

### P1: Current Usage Snapshot

Mac mini SQLite (`argus.db`) after one audit search:

- 12 persisted search runs in 30 days.
- 4 persisted runs in 7 days.
- 1 persisted run on the database's current UTC date, 2026-07-23.
- 51 returned-result rows across those runs.
- Provider attempts in 30 days:
  - DuckDuckGo: 11 successes, 1 error.
  - GitHub: 11 successes.
  - Yahoo: 6 successes.
  - SearXNG: 3 errors.
- Named attempted-provider rows:
  - Historical retired caller `clio-lane-b`: 13 attempts, 92.3% success.
  - `hermes`: 8 attempts, 75.0% success.
  - `atlas`: 6 attempts, 100% success.
  - `deploy-smoke-test`: 4 attempts, 75.0% success.
  - `argus-audit-2026-07-22`: 1 attempt, 100% success.
- The current HTTP access log contains 41 successful search requests,
  53 successful extraction requests, and 5 extraction validation failures.
  These log counts span more activity than the database, demonstrating that
  the database is not a complete request ledger.

Homelab MCP budget counters after one audit search:

| Provider | Recorded use | Reported remaining |
|---|---:|---:|
| SearXNG | 89 calls | unlimited |
| DuckDuckGo | 89 calls | unlimited |
| Yahoo | 6 calls | unlimited |
| Brave | 8 calls | 1,992 |
| Serper | 1,204 lifetime calls | 1,296 |
| You.com | 20 lifetime calls | 19,980 |
| Parallel | 5,618 lifetime calls | 10,382 |
| Valyu | 20 recorded units | exhausted; display is contradictory |

The audit itself accounts for one SearXNG and one DuckDuckGo call in the
homelab totals and one DuckDuckGo attempt in the mac mini totals.

No live provider-credit refresh was run because the current balance-check
implementation may consume paid credits. The numbers above are Argus's
persisted counters, not provider-authoritative balances.

### P1: Test Suite Is Not Hermetic

Running `uv run pytest -q` in the production-configured checkout produces:

- 348 passed.
- 1 failed:
  `TestExtractUrl.test_enabled_extraction_chain_order`.

The test expects a residential fallback even though `.env` declares this
machine itself to have residential egress. In that environment Argus correctly
skips the redundant residential hop. Forcing datacenter egress produces 349
passes. The test must set and reset all relevant config, not inherit `.env`.

### P1/P2: Trafilatura Compatibility and Quality Follow-Up

The main extractor now normalizes Trafilatura `Document` objects with
`as_dict()`, but the following paths still call `.get()` directly:

- `argus/extraction/residential_service.py`
- `argus/extraction/auth_extractor.py`
- `argus/extraction/wayback_extractor.py`
- `argus/extraction/archive_extractor.py`
- `argus/mcp/tools.py` archive recovery

The stale mac mini process logged
`'Document' object has no attribute 'get'` during the audit and fell through to
Playwright. Normalize Trafilatura output through one shared helper.

Issue #22 also records a separate observation: substantial Trafilatura output
can be returned as `all_extractors_quality_failed`. Reassess this only after
the OOM fix so lifecycle and quality policy remain separate.

### P2: Documentation and Operations Drift

- `homelab.yaml` points to `docs/OPERATIONS.md`, but that file does not exist.
- README examples use default ports `8000`/`8001` while the declared canonical
  deployment uses `8300`/`8301` and the active homelab MCP uses `8271`.
- README says Docker Compose can bring up SearXNG alongside Argus, but the
  repository compose file defines only Argus and Caddy.
- The local Docker daemon was not running during this audit, so the repository
  compose stack was not tested locally.

## Open GitHub Work

### Issue #22 — Playwright launch leak

Priority: P0. Implement first.

Issue: https://github.com/Khamel83/argus/issues/22

The issue contains reproduction evidence, exact acceptance criteria, and the
boundary for the later quality-gate investigation. Do not duplicate or weaken
those criteria.

### Issue #21 — Parallel monthly free tier

Priority: P1 after production stability.

Issue: https://github.com/Khamel83/argus/issues/21

Parallel announced a recurring `$5` monthly credit. Current code treats
Parallel as tier 3 one-time credit with a 16,000-query lifetime counter. The
issue calls it "free unlimited," but its own quoted plan is neither unlimited
nor query-denominated. Confirm the provider's billing unit, then move Parallel
to the recurring tier and implement monthly dollar-credit semantics without
silently resetting legacy lifetime records.

## Audit-Derived Acceptance Notes

The approved issue graph near the top of this handoff is the authoritative
execution order. The sections below preserve the detailed audit evidence and
acceptance obligations that informed those issues; their older task numbering
does not override GitHub's native dependency relationships.

### Task 1 — Fix issue #22 with lifecycle-safe capability state

Files:

- `argus/extraction/playwright_extractor.py`
- `argus/api/main.py`
- `Dockerfile`
- focused new or existing extraction tests
- `.github/workflows/docker-publish.yml`

Required behavior:

1. Stop and clear a started Playwright runtime when browser launch fails.
2. Cache an explicit unavailable capability state so subsequent URLs do not
   repeat an impossible launch.
3. Provide an explicit reset/recovery function and cover recovery in tests.
4. Serialize browser initialization with an async lock.
5. Initialize both `context` and `page` before the extraction `try/finally`.
6. Close browser/runtime resources during application shutdown.
7. Install the accepted version-matched Chromium capability in the production
   image. A deliberately browser-disabled image is only the explicit degraded
   emergency profile, never an accidental missing dependency.
8. Add two bounded single-worker container canaries: the missing-browser
   failure path at 512 MiB and the version-matched Chromium path at the initial
   1 GiB production limit. Both require zero restarts, OOMs, resets, or orphan
   processes; the enabled path must remain at or below 80% peak memory.
9. Run the sanitized production canaries from issue #22 and the browser
   capability research only after unit and container tests pass.

### Task 2 — Enforce the canonical deployment

Files and systems:

- `docs/adr/0001-canonical-deployment.md`
- `.github/workflows/docker-publish.yml`
- launchd deployment scripts in `deploy/`
- MCP client configurations
- homelab service definition outside this repository

The decision is made: homelab Docker is the sole production Argus; the Mac mini
is development-only and OCI has no Argus role. The API container is the sole
broker and state authority, while MCP is a stateless internal HTTP adapter.
Expose API and MCP remotely only as a private service behind Tailscale Serve;
keep their Docker bindings on loopback and keep PostgreSQL and SearXNG internal.
Supersede the old Mac ADR, replace the OCI deployment jump, retire the host
residential worker after its browser canary, point all production clients to
the Tailscale endpoints, and ensure health, status, usage, and MCP diagnostics
expose build, deployment, and service-instance identity.

### Task 3 — Make health and readiness truthful

Files:

- `argus/api/routes_health.py`
- `argus/broker/health.py`
- `argus/broker/reachability.py`
- `argus/mcp/tools.py`
- `argus/cli/main.py`

Add distinct liveness and readiness semantics, report probe freshness and
actual reachability, expose extraction capabilities, fix nested failure-count
rendering, and make false-negative probes retry or decay safely. Use
`/health/live`, `/health/startup`, `/health/ready`, and an authenticated
`/api/admin/status`; do not restart a live process merely because an external
provider or PostgreSQL is temporarily unavailable.

### Task 4 — Unify usage and budget observability

Files:

- `argus/api/usage.py`
- `argus/api/routes_dashboard.py`
- `argus/api/routes_health.py`
- `argus/persistence/models.py`
- `argus/persistence/db.py`
- `argus/broker/budget_persistence.py`
- `argus/mcp/tools.py`

Make PostgreSQL the homelab production store and keep SQLite as the standalone
and development adapter. Replace the direct-`sqlite3` usage, budget, and session
paths with one repository contract and Alembic migrations. Count search runs
rather than result rows; persist extraction and provider attempts; charge
budgets from actual attempts; add machine-readable admin usage; and distinguish
estimates from provider-authoritative balance snapshots with observation
source and freshness. Derive caller identity and tier policy from scoped
credentials; retain any request-supplied label only as non-authoritative
diagnostic context. Reserve a conservative provider-specific maximum before
each paid attempt, settle the known actual charge afterward, and keep uncertain
consumption charged until reconciled.

The database must use a separate `argus` database and least-privilege roles on
the existing PostgreSQL 16 server. Promote that server into a shared homelab
database service while keeping `atlas-postgres` as a compatibility alias.
Atlas's current daily custom-format dumps, structural validation, seven-day
retention, and successful isolated restore prove the base server is reusable.
Extend them to the Argus database and cluster globals, add 5 weekly / 12
monthly retention, copy backups outside the live data directory, and automate
monthly restores before migrating Argus production state.

### Task 5 — Implement issue #21 and correct budget display semantics

Files:

- `argus/broker/budgets.py`
- `argus/broker/balance_check.py`
- `argus/mcp/tools.py`
- `argus/cli/main.py`
- provider and budget tests

Confirm Parallel's current API billing terms before coding. Correct `None`
versus zero formatting for every provider and preserve historical records
during tier migration.

### Task 6 — Make tests independent of developer and production `.env`

Files:

- `tests/test_extraction.py`
- shared pytest fixtures if introduced
- `argus/config.py` only if a supported test-mode boundary is needed

Explicitly control egress, residential policy, provider flags, and config cache
in tests. Add a CI job that exercises a production-like residential config.

### Task 7 — Centralize Trafilatura normalization and investigate quality

Files:

- shared normalization helper under `argus/extraction/`
- all direct `bare_extraction()` callers listed above
- extraction and quality-gate tests

First remove the `Document`/dictionary compatibility duplication. After issue
#22 is closed, reproduce the `all_extractors_quality_failed` cases with
sanitized fixtures and file a separate issue if the gate rejects usable
content.

### Task 8 — Repair operational documentation

Files:

- create `docs/OPERATIONS.md`
- `README.md`
- `docs/troubleshooting.md`
- `homelab.yaml`
- compose/deployment docs

Document the chosen primary, ports, liveness/readiness/admin endpoints, budget
store, balance refresh policy, restart procedure, loaded-revision check, and
safe OOM canary. Make the SearXNG Compose claim match an actual supported
profile or remove it.

## Verification Commands Already Run

```bash
uv lock --check
uv run python -m compileall -q argus tests
uv run pytest -q
ARGUS_EGRESS_TYPE=datacenter uv run pytest -q
uv run argus doctor --json
uv run argus health
uv run argus budgets
uv run argus mcp check
```

Live authenticated HTTP provider, aggregate search, extraction, health, budget,
and path probes were also run. Remote MCP health, budget, search, extraction,
and path tools were exercised. No provider balance-refresh command and no
production stress test were run.

## Safety Notes for the Next Session

- Do not print, commit, or paste API keys, admin keys, tailnet credentials, or
  provider secrets.
- Do not run issue #22's 20-request production canary until runtime cleanup and
  container capability detection are fixed.
- A provider balance refresh may spend real credits; inspect each checker and
  obtain explicit approval before running it against paid providers.
- Preserve unrelated user changes if the worktree becomes dirty.
- Treat homelab and mac mini evidence as different instances until deployment
  convergence is complete.

## Suggested Skills

- `diagnosing-bugs` or `systematic-debugging` for issue #22 and the later
  quality-gate investigation.
- `test-driven-development` for the Playwright lifecycle fix.
- `github:github` to keep issue #22 and issue #21 aligned with implementation.
- `verification-before-completion` before claiming the memory leak, container
  canary, health reporting, or budget migration is complete.
- `finishing-a-development-branch` after each independently reviewed task.
