# Context

> **What this file is for:** background, glossary, and architectural decisions
> that don't belong in [README.md](README.md) (user-facing) or
> [AGENTS.md](AGENTS.md) (AI-agent conventions). Add entries here when a term
> or design choice keeps coming up in reviews or issues.

## Glossary

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

Every HTTP/MCP/CLI entry point accepts a `caller` string (e.g. `clio-lane-b`,
`hermes`, `mcp`) persisted with each search for the per-caller dashboard.
Fleet callers must always set it; unattributed traffic shows as `unknown`.

### Caller tier caps

Server-side spending guardrail: `ARGUS_CALLER_TIER_CAPS` maps fnmatch
caller patterns to a maximum provider tier. Motivated by the 2026-05
unexplained Valyu credit burn (see hermes `docs/ARGUS-VALVU-AUDIT.md`):
automated callers (Clio jobs, Hermes) are capped at tier 1 so one-time
credits (tier 3) can only be spent by interactive/uncapped callers.

### Canonical deployment

One Argus for the fleet: digest-addressed Homelab Docker, with HTTP and MCP
host backends on loopback ports 8270/8271 and tailnet-only Tailscale Serve
HTTPS ingress. PostgreSQL and SearXNG remain Docker-internal. The Mac is
development only; Mac launchd, OCI, Clio, and the host residential worker are
retired and are not fallbacks. See the
[production operations guide](docs/operations.md); ADR 0001 is superseded.
