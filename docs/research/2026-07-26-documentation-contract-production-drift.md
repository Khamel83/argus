# Documentation, contract, and production drift inventory

Date: 2026-07-26

Issue: [#69 — Audit Argus documentation against current contracts and production](https://github.com/Khamel83/argus/issues/69)

Scope: inventory only. This report does not edit product documentation, change
production, close the operations-runbook ticket, invoke credentialed providers,
or spend provider credits.

## Executive conclusion

Argus has good current reference material, but it does not have one coherent
documentation authority. Three incompatible stories are simultaneously
discoverable:

1. `CONTEXT.md`, accepted ADR 0001, and `deploy/README.md` declare a Mac mini
   launchd deployment on ports 8300/8301.
2. `docs/releasing.md`, current homelab Compose, and live runtime show homelab
   Docker on ports 8270/8271, automatically redeployed from mutable `latest`.
3. historical plans and generated summaries describe earlier SQLite, SSE,
   systemd, worker, and multi-authority states without reliable historical
   banners.

The live authority on 2026-07-26 was healthy homelab Docker. Both `argus` and
`argus-mcp` ran image revision
`1dcef5da60bb8af1f8f68d076ca3414ac0dfe7d5`; HTTP was exposed at host port
8270 and Streamable HTTP MCP at 8271. The PostgreSQL authority and recovery
mount on homelab `origin/main` match the completed recovery work, although a
legacy budget-SQLite comment/setting does not. The most dangerous
documentation problem is therefore not missing prose: it is that accepted and
operator-facing prose can direct a future agent to create a second production
authority.

The smallest safe correction sequence is:

1. supersede ADR 0001 and retire the Mac launchd deployment guide;
2. establish the issue #44 operations guide as the only operator authority;
3. make Argus and homelab metadata point to it;
4. fix unsafe/current public reference claims;
5. restore or remove broken generated-document promises; and
6. mark plans/specs/handoffs as historical evidence, not instructions.

## Evidence boundary

### Repository authority

- Argus was audited from `origin/main` at
  `1dcef5da60bb8af1f8f68d076ca3414ac0dfe7d5`.
- The local homelab checkout was intentionally preserved even though it was
  ahead 3 and behind 60. Homelab file evidence below comes from current
  `origin/main` at `4e232d54605683a131617a287a2df3ce84036486`,
  not from that drifted working tree.
- Code, tests, current workflow files, and generated schemas outrank prose for
  static contract claims.

### Live authority observation

Read-only `ssh homelab-ts` inspection observed:

```text
argus     image revision 1dcef5d...  host 8270 -> container 8000  healthy
argus-mcp image revision 1dcef5d...  host 8271 -> container 8001  healthy
image     ghcr.io/khamel83/argus@sha256:eff11a32...
started   2026-07-26T23:40:07Z
```

The image was created at `2026-07-26T23:39:12Z` and both containers used the
same image ID. This is a point-in-time observation, not scorecard-grade
promotion evidence: it has no retained artifact, expiry, eligibility snapshot,
or rollback proof.

No environment values, credentials, provider payloads, or private data were
read or printed.

## P0: unsafe operator contradictions

### D1 — The accepted canonical-deployment decision is false

| Field | Finding |
|---|---|
| Severity / audience | **Critical** — operators, agents, deployers, fleet callers |
| Exact stale statements | `CONTEXT.md:99-105` says the fleet authority is Mac mini launchd at 8300/8301 and treats homelab Docker as drift. `docs/adr/0001-canonical-deployment.md:1-4,15-29` is still `Status: accepted` and says to decommission homelab Docker. `deploy/README.md:1-23` gives live-looking launchd installation and redeploy commands. |
| Canonical evidence | Live Docker inspection above; homelab `origin/main:services/argus/docker-compose.yml:1-30,85-96,110-136`; Argus `docs/releasing.md:91-113`; issue #40 closeout. |
| Why unsafe | Following the accepted ADR creates a second execution authority, bypasses PostgreSQL/recovery/runtime identity assumptions, and contradicts the HTTP-authority/MCP-caller architecture. |
| Smallest correction | Add a superseding ADR naming homelab Docker as sole production and Mac as development only. Replace `deploy/README.md` with a tombstone/link to the canonical runbook. Update `CONTEXT.md` after the superseding decision lands. |
| Destination | Internal architecture plus the future public/operator guide from #44. |

### D2 — The accurate release guide does not label the current deploy as provisional

| Field | Finding |
|---|---|
| Severity / audience | **High** — release managers and operators |
| Exact current statements | `docs/releasing.md:91-113` accurately says every push to `main` builds `latest` and a SHA tag, then remotely runs Compose pull/up. It notes that #41 tracks a durable fix, but does not explicitly label the described path as provisional, non-transactional, and lacking proved automatic rollback. `.github/workflows/docker-publish.yml` implements that behavior. Homelab `origin/main:services/argus/docker-compose.yml:3,111` deploys `:latest`. |
| Live evidence | Merging the scorecard changed production from earlier revision `7c1c084...` to `1dcef5d...` at 23:40Z without an immutable digest-addressed promotion record. |
| Contract conflict | Issue #41 requires a serialized digest-addressed promotion, isolated candidate gates, protected environment, rollback proof, and no OCI jump/`StrictHostKeyChecking=no`. The current workflow has none of those properties. |
| Smallest correction | Add the explicit provisional/no-rollback-proof warning now. Do not present implementation as a documentation correction: #41 separately owns the promoter, exact-digest deployment, and rollback proof. Rewrite the guide around that mechanism only after it lands. |
| Destination | Immediate warning in `docs/releasing.md`; the later canonical procedure belongs in #44. Workflow changes remain solely owned by #41. |

### D3 — Argus project metadata points at a nonexistent operations authority

| Field | Finding |
|---|---|
| Severity / audience | **High** — Homelab automation, Baywatch, operators |
| Exact stale statements | `homelab.yaml:7-16` calls the project `development`, monitoring `standby`, and production `managed_by: unknown`; `homelab.yaml:34-38` points to `docs/OPERATIONS.md`, which does not exist. |
| Canonical evidence | Live Docker authority; current homelab service registry; `docs/operations-status.md` exists but is a status-contract reference, not the full operator runbook required by #44. |
| Smallest correction | Set the already-proved fields now (`lifecycle: production`, active monitoring state if supported, `managed_by: docker`). Remove the broken single-path `docs.operations` field and explicitly record the unresolved runbook gap until #44 creates the one canonical path. Do not invent a repair ID before a real safe operator action exists. |
| Destination | Generated/operator metadata in `homelab.yaml`. |

### D4 — Valyu and Firecrawl are documented as usable but are unconditionally disabled

| Field | Finding |
|---|---|
| Severity / audience | **High** — production operators and budget owners |
| Exact surfaces | `docs/providers.md:52-55` presents Valyu Contents and Firecrawl as ordinary configured fallbacks requiring only keys. Homelab `origin/main:services/argus/docker-compose.yml:74-84` projects their keys. |
| Canonical code | `argus/extraction/valyu_extractor.py:23-30` and `firecrawl_extractor.py:20-27` return immediately with “disabled: durable spend reservation is required”; the implementation below each return is unreachable. |
| Why unsafe | Documentation tells users that configuration makes a capability usable when code guarantees it cannot run. |
| Smallest correction | Mark both adapters “implemented but unavailable pending durable spend reservation.” Treat the unreachable code as compatibility scaffolding, not a supported setup. |
| Destination | `docs/providers.md`, configuration reference, and comments adjacent to homelab key projection. |

### D5 — You Contents is executable without the durable spend boundary implied for paid extractors

| Field | Finding |
|---|---|
| Severity / audience | **High** — production operators and budget owners |
| Exact surfaces | Homelab `origin/main:services/argus/docker-compose.yml:60-62,77` enables You Contents and supplies its key. `docs/providers.md:55` presents the flag/key as sufficient setup. |
| Canonical code | `argus/extraction/extractor.py` selects You Contents when `ARGUS_YOU_CONTENTS_ENABLED` is true. `you_extractor.py:23-65` calls the provider directly and has no durable reservation/admission step. |
| Why unsafe | The docs/config imply a coherent paid-extractor policy, but You has a materially different and weaker execution boundary than the two disabled adapters. |
| Smallest correction | Document the current flag-only behavior and mark it ineligible for the production budgeted profile until a separately reviewed spend-gateway change admits it. This audit does not authorize the config or code change. |
| Destination | Provider/config reference. Implementation ownership is currently unresolved: closed #35 established the durable spend contract but did not integrate this extractor; #64 must classify the gate and #67 must assign a concrete implementation slice rather than treating this audit as authorization. |

## P1: current contract and public-reference drift

### D6 — Provider count and setup table disagree

| Field | Finding |
|---|---|
| Severity / audience | **High** — package users |
| Exact stale statements | `README.md:192-208` labels a 14-provider section but lists 13 rows and omits SearchAPI. `README.md:214` still names SearchAPI in routing. |
| Canonical evidence | `argus/models.py` provider enum, registration code, health output, and `docs/providers.md:13-28` all include 14 providers. |
| Smallest correction | Add SearchAPI to the table with a cautious paid/credentialed label and link to current provider docs. |
| Destination | Public README. |

### D7 — GitHub provider defaults are documented incorrectly

| Field | Finding |
|---|---|
| Severity / audience | **Medium** — installers and adapter contributors |
| Exact stale statements | `docs/providers.md:18` says GitHub defaults disabled. Lines 30-32 say every disabled provider requires both an enabled flag and API key and that missing keys are silently skipped. |
| Canonical evidence | `argus/config.py` constructs GitHub with `enabled_default=True`; `.env.example` sets `ARGUS_GITHUB_ENABLED=true`; `github.py` permits unauthenticated repository search. DuckDuckGo and Yahoo are also enabled without keys. |
| Smallest correction | Mark GitHub enabled/key-optional. Replace the blanket paragraph with per-provider eligibility rules. |
| Destination | Public provider reference. |

### D8 — Extraction references omit the YouTube special path and blur eligibility

| Field | Finding |
|---|---|
| Severity / audience | **Medium** — callers and extractor contributors |
| Exact stale statements | `docs/providers.md:38-57`, `README.md` extraction prose, `AGENTS.md`, `server.json`, and package metadata describe a 12-step chain as the complete extraction surface. |
| Canonical evidence | `argus/extraction/extractor.py` routes YouTube before the generic chain; `youtube_extractor.py` uses local `yt-dlp` plus JSON3 captions. Authenticated/residential and paid steps also have policy-dependent eligibility that a simple numbered list hides. |
| Smallest correction | Describe “YouTube special path plus the 12-step general fallback chain,” and annotate each step as local, configured, policy-gated, spend-gated, lookup-only, or externally mutating. |
| Destination | Public README, provider reference, AGENTS, generated metadata at the next versioned release. |

### D9 — Public promotional copy says “SQLite only, no external dependencies”

| Field | Finding |
|---|---|
| Severity / audience | **High** — prospective users and public listings |
| Exact stale statement | `docs/PUBLICITY-CHECKLIST.md:38-41`. |
| Canonical evidence | Production requires PostgreSQL authority/recovery evidence; full-stack modes use SearXNG/browser/external APIs; the repo supports SQLite only for standalone development. |
| Smallest correction | Replace with “SQLite standalone; PostgreSQL production profile; optional self-hosted and provider dependencies.” Update any already-published directory text separately rather than assuming repo edits propagate. |
| Destination | Publicity checklist and external listing corrections. |

### D10 — Release status is stale even though the procedure is mostly current

| Field | Finding |
|---|---|
| Severity / audience | **Medium** — release managers |
| Exact stale statements | `docs/releasing.md:5-10,75-77` says PyPI, GitHub release, and MCP Registry are 1.6.1. |
| Primary evidence | PyPI JSON reports 1.6.2 uploaded `2026-05-22T22:09:41Z`; GitHub latest release is v1.6.2 published `2026-05-22T22:09:13Z`; MCP Registry v0.1 reports active server/package 1.6.2 published `2026-05-22T22:09:53Z`. |
| Smallest correction | Replace hand-maintained “current version” prose with verification commands or generated badges; retain historical run links only under a dated history heading. |
| Destination | Public release guide. |

### D11 — Homelab registries call Streamable HTTP “SSE”

| Field | Finding |
|---|---|
| Severity / audience | **High** — MCP clients and homelab operators |
| Exact stale surfaces | Current homelab `origin/main:services/registry.yml:160-172` identifies port 8271 but its continuation notes call it SSE and older setup scripts use `/sse`; `config/service-tiers.yml` and root Compose comments also say SSE. |
| Canonical evidence | Homelab Compose command at `services/argus/docker-compose.yml:110-122` is `--transport streamable-http`; Argus `docs/mcp-clients.md` documents `/mcp`. Live container port 8271 is healthy. |
| Smallest correction | Replace SSE labels/URLs with Streamable HTTP `/mcp`; retain SSE only as an explicitly legacy local option. Add a contract test over homelab metadata. |
| Destination | Homelab registry, service tiers, setup script, operator guide. |

### D12 — Adaptive Domain Memory is incorrectly documented as SQLite-specific

| Field | Finding |
|---|---|
| Severity / audience | **High** — operators and architecture contributors |
| Exact stale statements | `CONTEXT.md:70-75` calls Domain Memory “a small SQLite table.” `argus/extraction/domain_memory.py:1-6` repeats that storage claim in its module docstring. |
| Canonical code | `domain_memory.py:11-25` obtains the configured SQLAlchemy session through `get_session`; production uses PostgreSQL and standalone development may use SQLite. |
| Smallest correction | Say “configured SQL repository: PostgreSQL in production, SQLite for standalone development.” Correct both the curated context and source-module docstring. |
| Destination | Internal architecture and code documentation. |

### D13 — Homelab Compose labels a legacy SQLite budget path as production persistence

| Field | Finding |
|---|---|
| Severity / audience | **High** — production operators |
| Exact stale surface | Homelab `origin/main:services/argus/docker-compose.yml:31-32` says `# Budget persistence (SQLite)` and sets `ARGUS_BUDGET_DB_PATH`. |
| Canonical contract | `docs/operations-status.md:98-104` says production ignores the legacy filesystem-backed counter because the PostgreSQL provider-spend ledger is authoritative. |
| Smallest correction | Remove the ignored production setting/comment if no compatibility consumer needs it; otherwise label it legacy/development-only and add a contract test that production accounting remains PostgreSQL-backed. |
| Destination | Homelab Compose and operations guide. |

## P2: generated and historical-document drift

### D14 — `LLM-OVERVIEW.md` is a stale generated artifact

| Field | Finding |
|---|---|
| Severity / audience | **High** — coding agents |
| Exact stale statements | `LLM-OVERVIEW.md:3-11` promises daily regeneration but is dated 2026-05-10. Lines 19-50 show May commits as active/recent; line 28 says no known issues; lines 52-76 list stale topology/files/environment names. The linked `llms.txt` and `llms-full.txt` do exist and were updated on 2026-07-23, which makes the older overview's “daily” status more misleading rather than breaking the links. |
| Canonical evidence | File timestamp is 2026-05-10 while current date is 2026-07-26; open issues #41, #42, #44, #57, and #58-#69 are known. |
| Smallest correction | Either restore a tested generator for the overview with freshness/commit metadata, or delete that redundant artifact and point agents to AGENTS/CONTEXT plus the maintained `llms*.txt` files. A stale “daily” snapshot is worse than no snapshot. |
| Destination | Generated metadata pipeline. |

### D15 — Plans/specs are discoverable as instructions without lifecycle labels

| Field | Finding |
|---|---|
| Severity / audience | **Medium** — agents and future contributors |
| Examples | `docs/superpowers/plans/2026-07-05-fleet-integration.md` describes Mac production; `docs/superpowers/specs/2026-05-22-free-mode-dashboard-fix-design.md` declares a cache tradeoff that issue #61 is reconsidering; older handoffs describe SQLite and SSE as current. |
| Canonical evidence | Current code, current map, live homelab, and superseding issues. |
| Smallest correction | Do not rewrite historical plans. Add a standard header: status, implemented commit/PR, superseded-by link, and “not an operator runbook.” Move abandoned plans under an archive index where practical. |
| Destination | Internal documentation lifecycle/index. |

### D16 — Documentation ownership is fragmented

| Field | Finding |
|---|---|
| Severity / audience | **High** — everyone |
| Exact surfaces | `docs/README.md` lists references but no canonical operations guide; `docs/operations-status.md` defines truthful status semantics; `docs/releasing.md` owns current deploy behavior; homelab recovery handoff owns restore evidence; accepted ADR/deploy README own a contradictory topology. |
| Smallest correction | Issue #44 should create one operations guide that links rather than duplicates: runtime/status contract, immutable promotion, private ingress/cutover, PostgreSQL recovery, spend reconciliation, outbox/dead letters, and rollback. README, troubleshooting, metadata, and homelab registry should all link to it. |
| Destination | #44 canonical runbook and documentation index. |

## Surfaces checked with no material drift

- `pyproject.toml`, `argus/__init__.py`, `argus/api/main.py`, `server.json`, and
  the locked package metadata agree on repository version 1.6.2.
- `docs/operations-status.md` matches the PostgreSQL authority and truthful
  readiness model established by issues #37-#40.
- `docs/scorecards/stability-competitive.md` is current and should remain the
  acceptance authority rather than being copied into multiple runbooks.
- `docs/mcp-clients.md` correctly treats Streamable HTTP as the modern remote
  transport and requires application authentication.
- `docs/releasing.md:91-113` truthfully describes the current provisional
  container deployment, including its mutable-tag risk.

## Static versus live/provider checks

Safe static work:

- link/path validation;
- generated-file freshness checks;
- code/schema/config-to-doc comparison;
- archive/status headers;
- fixture-backed examples;
- version consistency and official registry metadata checks.

Safe read-only live work:

- loaded image digest/revision and container health;
- host/container port mapping;
- authority/backend/role and recovery-evidence freshness;
- endpoint transport and authenticated synthetic canaries.

Credentialed or spend-bearing work not performed:

- provider searches to validate current quotas, headers, or result shapes;
- paid content extraction;
- balance-exhaustion probes;
- secret rotation;
- public archive submission;
- external listing edits.

Provider-contract specifics belong to issue #60 and its report. No
documentation correction should claim provider usability from configured keys
or static prose alone.

## Correction ownership and order

1. Land the independent static corrections now: truthful `homelab.yaml`
   lifecycle/manager fields and temporary valid links; provider/default and
   extractor eligibility facts; release versions; Domain Memory backend
   wording; legacy budget-path labeling; Streamable HTTP naming; and the
   explicit provisional-deploy warning.
2. **#41:** replace mutable automatic deployment with immutable promotion and
   rollback proof.
3. **#42:** establish private ingress and one production authority; retire the
   Mac/OCI/worker duplicates under explicit safety gates.
4. **#44:** publish and live-verify the canonical operations guide.
5. Supersede ADR 0001, tombstone `deploy/README.md`, and replace temporary
   metadata links with the canonical guide.
6. Restore or remove the broken generated overview.
7. Add lifecycle metadata to historical plans/specs/handoffs.

## Out of scope and unresolved gaps

- This inventory does not perform #41, #42, #44, #57, or provider-contract
  implementation.
- It does not disable You Contents, rotate credentials, retire services, alter
  ingress, or deploy a new image.
- No claim is made that all third-party provider documentation is current; #60
  owns that primary-source matrix.
- Current approved-client endpoint configuration was not exhaustively inspected
  across Maya, Hermes, Codex, Claude, and other machines. #42 must prove those
  callers during cutover.
- The live observation is not an immutable promotion or rollback proof.

## Resolution

The audit question is resolved: documentation correction must follow the
actual architecture in dependency order, not attempt a broad prose refresh
before promotion and cutover settle. Homelab Docker is the current authority;
the Mac canonical-deployment ADR is superseded in fact and must be superseded
in documentation; current public/provider/generated claims can be corrected
independently; and the final operator truth belongs in #44 after #41 and #42.
