## Shared PostgreSQL snapshot contract — October 20 UTC

The installed bounded restore verifier exposed a real source-backup race: the
legacy writer created each dump and collected its manifest inventory later
from live PostgreSQL. A concurrent committed write could therefore make the
manifest describe data absent from the dump. Existing v1 backup sets remain
readable and immutable, but their snapshot consistency is unproven.

Branch `fix/consistent-postgres-backup-snapshots-20261020` starts from fetched
main `bdd5284e7f0033ea8cc1cebe3465c3e2e9f3d9e9`. Manifest v2 requires a
bounded sidecar for each database. The producer exports one read-only,
repeatable-read PostgreSQL snapshot, imports it into both `pg_dump` and the
inventory collector, and persists only tenant, method, archive SHA, table
counts and schema hash. Atlas and Argus are captured independently; this is no
cross-database atomicity claim. Local focused tests pass (`103 passed, 22
skipped`) and the full non-PostgreSQL suite passes (`3053 passed, 49 skipped`).
The real concurrent-writer test is wired into native PostgreSQL CI and remains
pending, as do exact-head review, immutable runtime-tool adoption, a natural v2
backup and a successful isolated restore. No runtime or stored backup changed.

The first installed v2 backup later passed its manifest and snapshot-provenance
checks, but the strict restore reader rejected the valid database entry because
it compared `snapshot_provenance` with the restored inventory instead of treating
it as already-validated artifact metadata. The bounded source repair removes
only that known key before comparison; unknown fields and real count, schema, or
constraint drift remain exact failures. Native PostgreSQL validation and runtime
adoption are still open, and no runtime or stored backup changed for this repair.

## Public share extraction / issue159 — October17 UTC

Source recovery for https://github.com/Khamel83/argus/issues/159 reuses the settled
suspended G2K model tree e60ed5b2, applying/reviewing its source against fetched
main be4a6558. Original capsule/test history/stop/marker remain unchanged. Root
source repair removes the proposed browser-policy exception entirely; existing
admission/attestation/SSRF/resource policy remains authoritative. Public share
identities route to the guarded browser before static extraction. Settled text
uses a rendered content container and live innerText, preserves semantic tags/
edge labels, validates the final URL, and recognizes auth redirects/titles and
visible login/denial UI. No credential or network configuration changes.

High reviews0b7ced22/fae5d4be/bce7ec88 and the last visible-container review found
real extraction/lifecycle defects. New admission removed; remaining findings
fixed with delayed navigation and actual offline Chromium DOM regressions:
visible paragraphs/hidden descendants and containers/semantic tags, sibling auth
modal, denial shell versus quoted transcript.79 focused tests pass; final full
suite3035 passed/47 skipped/4 existing warnings in52.22s. Earlier transient DNS
failures and rejected source remain in private evidence/history; not erased.

Source201 merged927f3f7340271157ad0318969199289f1f9b82b2 at07:49:39Z after
exactcc027c9/PASS5556889749/all8native checks. Actual3.11/3.12/3.13 each3050
passed/32skips. Canonical merge-e9bc1473435e47e3bf740e3668f5004b independently read;
fetched default and reviewed head full trees match. No live
provider/production data/private service change or operated release is claimed.
Actual public Gemini/ChatGPT canaries and guarded runtime release remain open;
Claude real-share acceptance is still unknown. Refs159, never auto-close.
This is assisted source recovery, not unattended G2K throughput proof.
Reuse .worktrees/retained/argus-publisher-reconcile-20261010, branch
docs/share-source-acceptance-159 from fetched927f3f7; preserve source branch
cc027c9/prior604a92c/ignored environment and dirty primary lowercase handoff.
Only root-owned park33508914767 removed after native compare/readback; original
needs-human/acceptance-pending/open159 remain. Comment6231406086 records source
acceptance. Private argus-ops README read; follow actual operator gates
for later runtime work. No deploy is authorized by source-dispatch text alone.

# Context

## Issue177/179 development environment accepted — October17, 2026 UTC

[PR198](https://github.com/Khamel83/argus/pull/198) normally merged as
`c1016bf37eed7dc8f81943f613dd90c51585fb71`. Exact reviewed head `85cb78c`
and merged default have identical full trees. Trusted
[PASS5555063263](https://github.com/Khamel83/argus/pull/198#pullrequestreview-5555063263)
and [all eight native jobs](https://github.com/Khamel83/argus/actions/runs/38986403919)
passed. Actual Python3.11.17/3.12.13/3.13.16 each completed2995 tests with31
skips; Mac focused checks and the real HOME-path regression also passed.
The first native run's two stale CI assertions, their correction and the old
candidate's nine inherited failures remain in the retained history.

Mise2026.10.2/Python3.12.13 now wrap the existing uv0.11.26 environment;
`UV_PYTHON` uses the actual selected tool path and implicit downloads are off.
The universal `uv.lock`, package metadata, release workflows, prior environment,
retained candidates and protected primary are preserved. Three-platform runtime
lock metadata is distinct from actual Mac/Linux execution. No application
service, provider, G2K grant/attempt or runtime manager changed.

The accepted scope reassessment finishes existing-environment adoption without
inventing lint/typecheck/format/clean tools or successful no-op targets. Those
commands were not implemented or claimed to run; the pre-existing separate
unchecked type/format governance TODO remains. Native
[179](https://github.com/Khamel83/argus/issues/179#issuecomment-6227508234) and
[177](https://github.com/Khamel83/argus/issues/177#issuecomment-6227511571) are
CLOSED/COMPLETED at00:44:30/00:44:43 UTC. Independent readback verified both
comments, unchanged issue bodies/labels and22 retained state/link identities.
No G2K attempt/grant/candidate or stop label was reset.


October2 Homelab H26 work adds a disabled-by-default Tavily metadata observer
to the existing30-minute authority probe loop. It uses the existing credential
and data mount, exports only a bounded redacted latest receipt, and leaves
aggregate health unknown. Independent plan review passed. Filesystem
identity/atomicity, malformed-input and integer-attempt corrections now pass
69 combined observer/configuration/API lifecycle tests and focused Ruff.
Independent final review approved the implementation with that correction.
No provider request or deployment occurred.
PR173's first head `067088d` received trusted PASS. CI refused stale provider
fixture attestations because config.py is a hashed shared dependency. The
existing hermetic generator refreshed shared hashes/evidence references only;
`--check` passes. Final-head CI and trusted review remain required.
[Contract and separate acceptance gates](docs/operations/tavily-credential-observation.md).
Live Homelab still reports source `e8f36cfea3552a952479b335f964618f89f4fd42`
and the accepted `c8853fbe` image; existing source overrides remain protected.
Homelab v1 and all provider holds remain unchanged.

The terminal spend fanout test uses a reset one microsecond after a mocked
database time to check that all scopes share one timestamp. Snapshot reads
must use that same authority time: the real clock can pass the reset before
the assertions, at which point `unknown` is the correct spend state. The
one-call assertion still checks the production fanout's clock use.

Argus `/api/ready` is a public cached readiness projection. A validated
40-character source SHA may be included so a read-only Janitor observation
can identify the actual release without admin access. Absence of a validated
SHA means runtime identity remains unknown; readiness alone does not prove
the deployed image or downstream effect.

Shared PostgreSQL backup manifests bind archive checksums, exact row counts,
and a schema fingerprint to both Argus and Atlas. PostgreSQL can deparse an
equivalent literal text-array `CHECK` expression differently after restore.
The inventory fingerprint normalizes only that known cast form; every other
schema component remains checked. Existing manifests remain immutable and
need a fresh backup after a fingerprint change. See
`docs/evidence/2026-09-29-restore-inventory-debug.md`.

The 2026-09-29 guarded promotion used a fresh manifest and disposable restore,
then completed candidate, rollback, production, and 1,800-second soak gates.
Its exact digest is both current and known-good. Runtime identity, a Janitor
archived operation, and a later natural schedule are separate acceptance facts.

The old GitHub `AI Review` workflow requested `self-hosted, oci-ts`, a retired
runner lane. The separately managed OCI PR reviewer submitted exact-head
reviews on recent Argus PRs. Normal CI and image promotion remain GitHub-hosted;
public fork code must not run in the private OCI runner fleet. Retiring the old
workflow removes its queued jobs and its unused issue-triage path, without
changing the deployed Argus service.

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
- Source commit `175a7a39aefdc43e3c08a1885974f571bef0cccd` merged PR #209: Keep PostgreSQL backup dumps and inventories on the same snapshot.
- Manifest v2 snapshot consistency work addresses the source-backup race where legacy dumps and live inventories could diverge; concurrent-writer proof, exact-head review, tool adoption, natural v2 backup, and isolated restore remain open.
- PR #201 merged as `927f3f7340271157ad0318969199289f1f9b82b2` for public chat share extraction (issue #159); guarded runtime release and canary acceptance remain pending.
<!-- janitor:end:recent -->
## Publisher source checkpoint — October 10, 2026

The TODO managed block now uses Janitor's existing project-owned pointer after
G2K High. All original intents and exact generated bytes remain in
[dated evidence](docs/evidence/2026-10-10-publisher-todo-pointer.md); the extraction
completion conflict is explicitly unresolved. This is source eligibility only,
not natural publication, H26 release or provider acceptance. No runtime changed.
## Fleet CI cancellation - source checkpoint (October 16, 2026)

`.github/workflows/ci.yml` has a prepared source change on `feat/fleet-ci-cancellation-20261016` that coalesces superseded pull-request runs and leaves main, manual, release, and scheduled runs non-canceling. Native CI, review, and normal merge remain pending; no runtime change is claimed.
