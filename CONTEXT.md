# Context

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
## Recent

- **2026-10-03 — Tavily metadata observer merged (`bf58a770a74e58fa07f0d9104fc9e91301b96453`).** PR #173 merged the disabled-by-default Tavily-only observer after its filesystem identity, malformed-input, and integer-attempt corrections. Independent plan and final reviews approved it; 69 combined observer/configuration/API lifecycle tests and focused Ruff passed. The hermetic provider-attestation generator was refreshed for the shared configuration change and its check passes. No provider request or deployment is evidenced, live source remains `e8f36cfea3552a952479b335f964618f89f4fd42`, and aggregate health remains unknown.

- **2026-09-29 — Guarded promotion and restore evidence.** The fresh-manifest backup and disposable restore completed candidate, rollback, production, and 1,800-second soak gates. Its exact digest is current and known-good, while runtime identity, an archived Janitor operation, and a later natural observation remain separate acceptance facts.

- **2026-09-29 — Legacy review workflow retired (`bdca7fb1fff2b1bf062e5345ce09bb28a9fa9d99`).** The retired `oci-ts` AI Review workflow and its queued jobs were removed. Public/fork CI remains on GitHub-hosted runners; separately managed OCI review and normal CI/image promotion paths are unchanged.

- **2026-09-29 — Public readiness identity projection (`337519d877e34452c3e395aa473195ecc5ae33fb`).** `/api/ready` may expose a validated 40-character source SHA to read-only observers. A missing validated SHA leaves runtime identity unknown; readiness alone does not prove a deployed image or downstream effect.

- **2026-09-29 — Restore inventory normalization (`e8f36cfea3552a952479b335f964618f89f4fd42`).** Shared PostgreSQL backup manifests bind archive checksums, exact row counts, and a schema fingerprint to Argus and Atlas. Inventory normalization handles only the known PostgreSQL cast-form variation; other schema components remain checked and existing manifests require a fresh backup after fingerprint changes.

- **2026-09-27 — Terminal spend snapshot authority (`4f8f0a5bd65ad45ae57079e6ada8f8a427ffc50a`).** Snapshot reads share one frozen authority timestamp with terminal-spend fanout. If real clock time advances beyond a mocked reset, `unknown` remains the correct spend state rather than proving different timestamps.
<!-- janitor:end:recent -->
