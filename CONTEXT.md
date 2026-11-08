## November 8 — ARG-QS-02 release proof complete

Reviewed exact-source handoff is installed and a genuinely new digest passed
the guarded release gates: source `bd7dded90d4378ee6dba516b114738cb81a474a7`,
image `sha256:c9275a40142e452c31c0aa40bf9e57d9da83431855a72e33f632b777b337da56`.
Native promotion/full1,800-second soak completed23:04:32UTC. Current/known-good
agree; healthy API/MCP load that digest and baked source; live200 verified.
Hosted run42187925242 attempt3 is SUCCESS (promote128900892858), returning
existing known-good proof after attempt2 cancelled. Original failed retries
remain retained. [Bounded operator evidence](https://github.com/Khamel83/argus/issues/151#issuecomment-6728505607).

ARG-QS-03/04/05 remain open: uncertain provider settlement, restore/ordinary
reader proof and broader monitoring qualification are independent. No claim of
whole-host zero-timeout acceptance. Detailed private receipts remain in Argus-ops.
Records only; no further deployment is needed for this documentation.
Own `.worktrees/nov8-todo-closeout` is isolated from the protected primary and
other sessions; retain through exact-head review/CI and normal merge, then remove
if clean. Next: verify that records PR's native gates and merge receipt.

## PR245 records repair — November 6, 2026 UTC

Owner-authorized [Engage campaign](https://github.com/Khamel83/engage/issues/96) applies only to this tested-PR repair. Dot prepared a spacing correction against c49791bf; Engage verified native head, original-file and patch hashes, dry-run application and unchanged non-whitespace bytes. Identifiers, dates and quantities are readable again; operational facts are unchanged. Earlier ARG quickstrike caps/checkpoints below are historical for their own scope; this does not restart parked browser/cleanup work or authorize provider/runtime actions.

Source candidate only: fresh exact-head trusted review, native CI and normal merge/readback are pending. Existing runtime, soak, restore, grants and downstream acceptance remain separate. Recheck `gh pr view 245 --repo Khamel83/argus --json state,headRefOid,reviews,statusCheckRollup,mergeCommit`.

## Approved reliability closure — November 5, 2026

Owner approved corrected Quickstrike ARG-QS-01–05; excluded browser 148 and
parked cleanup 232. Cap 5.75 Codex weekly points from 41% used; stop new delivery
at 45.6% used, reserve 1.15 points; no paid APIs/new provider canaries. One existing
claim and direct Codex writer own this delivery. Runtime remains 0e3821ed / image
355468da; source, merge, installation, restore and downstream evidence stay
separate. Infra PR 1851 connects exact compiler and existing scorecard admission.
Prepared recovery verifier compares registered table/column coverage with the
verified catalog; omitted CLI attestation no longer overwrites this evidence.
Actual schema compatibility and human rollback approval are not inferred.

## Glama catalog startup repaired — October 22, 2026

The failure email referred to a successful install followed by MCP startup
without the required HTTP authority configuration. Corrected Glama's saved
adapter-only schema, supplied deliberately unusable discovery placeholders,
aligned Python 3.12 and frozen-lock install, and published Glama 1.6.5 after the
[new native test passed](https://glama.ai/mcp/servers/Khamel83/argus/admin/dockerfile/tests/01a1650d-da73-725e-926d-0b3cabecf580).
Independent UI readback confirms the published release and persisted settings.
The checked source metadata now includes the required maintainer and 1.6.5,
with a release-contract guard against future Glama version drift.

72 focused delivery/release/HTTP-authority checks pass. Local bare MCP startup
fails closed; dummy-config MCP initializes and lists 15 tools without an authority
or provider call. Source review/CI/normal merge are tracked in
https://github.com/Khamel83/argus/pull/221. No new PyPI package or operated HTTP authority rollout is claimed.
See [dated evidence](docs/evidence/2026-10-22-glama-startup-repair.md).

The reporter accepted credit and has no fixed advisory date; his only open
question is who handles the CVE. Live repository/global pip advisory queries
returned empty. No new security report or native GitHub issue was found. No
new message or CVE request was sent in this turn; suggested wording stays in chat.

## Legacy worker security patch released — October 22, 2026

[PR217](https://github.com/Khamel83/argus/pull/217) merged as
`f0386c760bfe05127a264540e4a79b373c71c123` at 06:59:49 UTC through the normal
trusted reviewer/Janitor path. Exact-head trusted PASS review5610327986 and
[all eight native CI checks](https://github.com/Khamel83/argus/actions/runs/39737027592)
passed. Native GitHub readback verified the merge actor and identical full trees
for reviewed `ff659cb` and merged main (`a1140108edd931d439cb0c7cbb8848279346d4e8`).

[Release v1.6.5](https://github.com/Khamel83/argus/releases/tag/v1.6.5) targets
that exact merged source. [Publish39737816197](https://github.com/Khamel83/argus/actions/runs/39737816197)
passed both PyPI and MCP Registry jobs. PyPI metadata lists the 1.6.5 wheel and
sdist. An isolated installation from PyPI outside the repository verified
version/site-packages identity, the reported PoC raises 401, missing-secret
startup is rejected, unauthenticated `/exec` returns401 and authenticated paid
provider execution remains 403. No provider was constructed or called.
Local suite:3066 passed/49 skipped; wheel/sdist metadata and release contracts pass.

GitHub private vulnerability reporting is enabled with independent readback.
The reporter acknowledgment was sent in the original email thread and its SENT
status independently read back. It confirms the published fix, links release
credit and asks about preferred wording/disclosure timing. His reply remains
external coordination, not a gate on the published patch.

This closes the package security repair and publication. No claim is made about
an operated production rollout or live provider retrieval. The legacy worker is
development-only; production HTTP execution remains a separate authority.
See [dated evidence](docs/evidence/2026-10-21-worker-authentication.md).

## Shared PostgreSQL snapshot contract — October 20 UTC

The bounded restore verifier exposed a real source-backup race: the legacy
writer created each dump and collected its manifest inventory later from live
PostgreSQL. A concurrent committed write could therefore make the manifest
describe data absent from the dump. Existing v1 backup sets remain readable and
immutable, but their snapshot consistency is unproven.

PR [209](https://github.com/Khamel83/argus/pull/209) merged as
`175a7a39aefdc43e3c08a1885974f571bef0cccd` after trusted review
`5580007693` and required CI run `39357846393`. The native PostgreSQL ledger
completed 323 tests, including the concurrent-writer proof. Manifest v2 uses a
bounded sidecar for each database: the producer exports one read-only,
repeatable-read PostgreSQL snapshot and imports it into both `pg_dump` and the
inventory collector. It records only tenant, method, archive SHA, table counts
and schema hash. Atlas and Argus are captured independently; this is not a
cross-database atomicity claim.

The previous `175a7a39` producer remains a rollback anchor. The current exact
source `66365a661abcfa6ccb4a10dfe5e4b251eaf339ff` is sealed and
import-validated in the private backup-tool capsule; its 225-file sealed
manifest is distinct from the application identity. The fixed Infra wrapper
from merge `13c7f97` is installed, and the protected 01:45 cron row preserves
its schedule, job identity and log path.

A bounded manual producer run from the previous source completed
02:30:52–02:34:20 UTC in 208 seconds and published schema-2 set
`20261020T023056Z`; both tenants carry exported-snapshot provenance bound to
their dump hashes. This proves that producer path for that run, not a natural
recurrence. The first test of the new installed producer was incorrectly run as
root and failed backup-root identity preflight before making a dump. Its logs
and failure receipt are retained. A corrected ordinary-user run completed
03:28:54–03:32:07 UTC in 193 seconds with 82.984 CPU-seconds, 768 MiB peak and
zero swap. It published schema-2 set `20261020T032858Z` with manifest
`e69d7b6dfb15c75e40ce53dc0109e992dc9d6cd2fd28cd024e62ebab9dacae02`; this is
manual functional acceptance, not a natural recurrence.

The first strict restore of the previous set `20261020T023056Z` failed at
02:36:52 UTC; its inner log was not retained, cleanup completed and canonical
state stayed unchanged. A
subsequent bounded reproduction identified the v2 reader defect that compared
the already-validated `snapshot_provenance` metadata as restored database
inventory. PR
[211](https://github.com/Khamel83/argus/pull/211) merged as
`e7ee75a9193615beb12137467aa9b4104164fdb0` after trusted review
`5580171742` and required CI run `39360416459`; its native ledger completed 323
tests with the full v2 entry. The second restore attempt failed at 02:52:27 UTC
with canonical state unchanged. An independent observer matched both database
table sets, row counts and constraints; Argus's schema fingerprint matched, but
Atlas had a function-order-only schema-fingerprint mismatch. The diagnosis
matched all 36 tables, 350 columns, 115 constraints, 106 indexes and 46
functions. PR [213](https://github.com/Khamel83/argus/pull/213) merged as
`66365a661abcfa6ccb4a10dfe5e4b251eaf339ff` after trusted review
`5580366828` and required CI run `39363477632`; its native ledger completed 324
tests. A fresh strict isolated restore completed at 03:34:47 UTC using source
`66365a6` and restore-only image
`sha256:01f59e0040f12ff1c51b1636e089b99396685b1469a29a482e66408a3406e3c3`.
Integrity, row counts, schema, the Argus read path and migration compatibility
all passed against the same schema-2 backup identity. The canonical restore
record SHA is `4bc1adb82b19ebcbf11d932b0f3ea4b4264471b1fbbdb2805c3e4580a8f6a5d1`.
The flags `metadata_registry_complete`, `schema_contract_clean`,
`forward_compatible` and `rollback_path_human_approved` remain false; no application
cutover occurred, and legacy v1 manifests remain immutable and unproven.

## Public share extraction / issue 159 — October 17 UTC

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
Claude real-share acceptance is still unknown. Refs 159, never auto-close.
This is assisted source recovery, not unattended G2K throughput proof.
Reuse .worktrees/retained/argus-publisher-reconcile-20261010, branch
docs/share-source-acceptance-159 from fetched927f3f7; preserve source branch
cc027c9/prior604a92c/ignored environment and dirty primary lowercase handoff.
Only root-owned park33508914767 removed after native compare/readback; original
needs-human/acceptance-pending/open159 remain. Comment6231406086 records source
acceptance. Private argus-ops README read; follow actual operator gates
for later runtime work. No deploy is authorized by source-dispatch text alone.

# Context

## Issue 177/179 development environment accepted — October 17, 2026 UTC

[PR198](https://github.com/Khamel83/argus/pull/198) normally merged as
`c1016bf37eed7dc8f81943f613dd90c51585fb71`. Exact reviewed head `85cb78c`
and merged default have identical full trees. Trusted
[PASS5555063263](https://github.com/Khamel83/argus/pull/198#pullrequestreview-5555063263)
and [all eight native jobs](https://github.com/Khamel83/argus/actions/runs/38986403919)
passed. Actual Python3.11.17/3.12.13/3.13.16 each completed2995 tests with31
skips; Mac focused checks and the real HOME-path regression also passed.
The first native run's two stale CI assertions, their correction and the old
candidate's nine inherited failures remain in the retained history.

Mise2026.10.2/Python 3.12.13 now wrap the existing uv0.11.26 environment;
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
`--check` passes. PR173 subsequently merged October2 as
`bf58a770a74e58fa07f0d9104fc9e91301b96453`; the pending source instruction
is superseded. Deployment and natural-consumer receipt gates remain separate.
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
## Approved reliability closure — November 5, 2026

Owner approved corrected Quickstrike ARG-QS-01–05; excluded browser 148 and
parked cleanup 232. Cap 5.75 Codex weekly points from 41% used; stop new delivery
at 45.6% used, reserve 1.15 points; no paid APIs/new provider canaries. One existing
claim and direct Codex writer own this delivery. Runtime remains 0e3821ed / image
355468da; source, merge, installation, restore and downstream evidence stay
separate. Infra PR 1851 connects exact compiler and existing scorecard admission.
Prepared recovery verifier compares registered table/column coverage with the
verified catalog; omitted CLI attestation no longer overwrites this evidence
(source `77a29f979cb647f38868edd3a09d43c2c9c8ab28`, PR 242). ARG-QS-01 reconciled
current records and closed PR 238 as superseded by PR 242. Actual schema
compatibility and human rollback approval are not inferred.

## Workflow finalization repair — November 5 UTC

PR 241 merged frozen-metadata repair as `0e3821ed7edb0da26ca2815e71abf942279f8da5`
(fix commit `8bda8221da5800ac0f9ad1333b90bfb0c34ca59e`). Nested accepted
cache-age MappingProxyType inside StoredDocument had blocked dataclasses.asdict;
copy at the document-storage boundary preserves plain containers. Prior live
PR 240 source a335e289 / image f966565f had healthy API/MCP under active soak
argus-promote-a335e28.service; workflow a49653dc1c18 extracted one source and
generated a cited Answer; OCI row 707713 at 07:22:42 UTC recorded successful
agy/primary/gemini-3.8-flash-medium inference (proves inference, not completion).
73 focused tests had passed including report+manifest publication. Full native
source gates and guarded deployment of the accepted repair image, plus one
complete workflow/report with a new inference receipt, remain required. Keep
failed run and successful inference receipt; no model retry to conceal
finalization failure. Runtime identity after PR 241 promotion stays separate
from merge evidence.

## Search-and-summarize receipt projection repair — November 5 UTC

PR 240 merged receipt-projection repair as `a335e28911a7684c960ee64a1e3232c411ace683`
(fix `b34012c496c9dce1878e42429b37046b177e5268`). Earlier live PR 239
source 061f5c2 / image 73f5ff35 served healthy API/MCP; workflow e98f4e950f6a failed
before extraction because public search projection added
retrieved_at/source_date/source_type absent from receipt-bound results. Repair
binds composition to accepted stored results; strict receipt binding unchanged.
Focused workflow checks 61 passed. PR 239 synthesize path remains
`061f5c2788b27b44dffcbba72df174a0c3f68358`. Immutable image admission, scorecard,
recovery/rollback/soak and a post-repair gateway workflow remain separate gates.
Spend totals uncertain; readiness degraded on last reported discovery.

## Glama catalog startup repaired — October 22, 2026

Corrected Glama adapter-only schema and published 1.6.5 after native test pass.
PR 221 merged as `e71965e3e95297944847c6846d8b2fdcb7f91172`. Independent UI
readback confirmed release and settings. 72 focused checks pass; bare MCP fails
closed; dummy-config lists 15 tools. No new PyPI package or operated HTTP
authority rollout claimed beyond the separate worker 1.6.5 publication path.
See [dated evidence](docs/evidence/2026-10-22-glama-startup-repair.md).

## Legacy worker security patch released — October 22, 2026

PR 217 merged as `f0386c760bfe05127a264540e4a79b373c71c123`. Release v1.6.5
published; PyPI/MCP Registry jobs passed. Isolated PyPI install verified PoC
raises 401 and paid provider execution remains 403. No operated production
rollout claimed. See [dated evidence](docs/evidence/2026-10-21-worker-authentication.md).

## Shared PostgreSQL snapshot contract — October 20 UTC

Manifest v2 exported-snapshot path merged via PR 209 `175a7a39`, reader repair
PR 211 `e7ee75a9`, inventory ordering PR 213 `66365a66`. Bounded manual producer
and strict isolated restore accepted for schema-2 set `20261020T032858Z`; flags
`metadata_registry_complete`, `schema_contract_clean`, `forward_compatible` and
`rollback_path_human_approved` remain false; no application cutover; legacy v1
manifests immutable and unproven.

## Public share extraction / issue 159 — October 17 UTC

Source recovery merged PR 201 as `927f3f7340271157ad0318969199289f1f9b82b2`.
Refs 159, never auto-close. Actual public Gemini/ChatGPT canaries and guarded
runtime release remain open; Claude real-share acceptance unknown.

## Issue 177/179 development environment accepted — October 17, 2026 UTC

PR 198 merged as `c1016bf37eed7dc8f81943f613dd90c51585fb71`. Native 179/177
CLOSED/COMPLETED. Pre-existing separate unchecked type/format governance TODO
remains. No application service or provider runtime changed.

Source commit for this documentation reconciliation: `e2a632c5310be5b4044f2fc6f2898b4d7132870b`.
<!-- janitor:end:recent -->
## Publisher source checkpoint — October 10, 2026

The TODO managed block now uses Janitor's existing project-owned pointer after
G2K High. All original intents and exact generated bytes remain in
[dated evidence](docs/evidence/2026-10-10-publisher-todo-pointer.md); the extraction
completion conflict is explicitly unresolved. This is source eligibility only,
not natural publication, H26 release or provider acceptance. No runtime changed.
## Fleet CI cancellation - source checkpoint (October 16, 2026)

`.github/workflows/ci.yml` has a prepared source change on `feat/fleet-ci-cancellation-20261016` that coalesces superseded pull-request runs and leaves main, manual, release, and scheduled runs non-canceling. Native CI, review, and normal merge remain pending; no runtime change is claimed.

## Search-and-summarize receipt projection repair — November 5 UTC

Live PR 239 source 061f5c2 / image 73f5ff35 serves healthy API/MCP and passed doctor.
A fresh tier-capped discovery search returned 10 results; Exa, Tavily, Linkup,
Parallel, SearXNG, Yahoo and GitHub succeeded. Brave was skipped; DuckDuckGo
failed. Spend totals remain uncertain and overall readiness remains degraded.
Workflow e98f4e950f6a failed before extraction/model inference: its public
search projection added retrieved_at/source_date/source_type absent from the
receipt-bound results. Read-only database comparison confirmed exactly those
three differences. The real broker/presenter regression reproduced UNREADY.

The repair recognizes only that exact presentation shape, verifies every stored
field, and uses the original stored results for both claim and composition.
Existing strict receipt binding remains unchanged; changed URLs/content, unknown
fields or malformed presentation context remain rejected. Public response shape
and accepted historical rows are untouched. Focused workflow checks 61 passed
before expanded tamper cases; native CI/review, immutable image, admission and
actual gateway workflow remain required. The existing 061f5c2 guarded promotion
is in its mandatory 1800s soak, with healthy runtime; do not interrupt it.

Worktree `.worktrees/deploy-latest` now owns `fix/workflow-receipt-projection`;
prior records/source are retained on `ops/pr239-rollout-checkpoint` at ab679bf.
Next: finish source gates, then promote only the accepted built digest with
normal scorecard/recovery/rollback/soak and retry one actual workflow after repair.
No model request occurred in the failed workflow.

## Workflow finalization repair — November 5 UTC

Argus PR 240 source a335e289 / image f966565f is serving healthy API/MCP, with its
required soak still active under argus-promote-a335e28.service. Doctor passed
and a fresh discovery returned 10 results; Exa/Tavily/Linkup/Parallel/SearXNG/
GitHub succeeded. Brave skipped; DuckDuckGo/Yahoo failed. Readiness remains
degraded and spend totals uncertain. Actual workflow a49653dc1c18 extracted
one source and generated a cited Answer. OCI durable row 707713 at 07:22:42 UTC
records successful agy/primary/gemini-3.8-flash-medium inference, authenticated
workload argus / caller argus-workflows. This proves inference, not workflow completion.

Finalization then failed because nested accepted cache-age metadata remained
MappingProxyType inside StoredDocument. dataclasses.asdict cannot deep-copy it;
report publication and durable completion did not occur. The regression using
the real accepted extraction/composition authority reproduced the exactTypeError.
A copy at the existing document-storage boundary now preserves all metadata
values in plain containers; immutable accepted evidence remains unchanged.
73focused tests pass, including complete report+manifest publication, disk
reload and authenticated artifact read. Full native source gates and guarded
deployment remain required. Keep failed run and successful inference receipt;
no model retry is authorized merely to conceal that finalization failure.
Next: publish reviewed repair, deploy its exact accepted image after the active
soak ends, and verify one complete workflow/report with a new inference receipt.
Reuse the now-idle Homelabdeploy-latest compiler tree for this source; keep
activea335compilerdeploy-workflow unchanged. Preserve protected primaries.
