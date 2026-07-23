# Argus Language

Argus is the fleet's internet-retrieval boundary. This glossary names the
retrieval, ownership, reliability, and release concepts shared by its HTTP,
MCP, CLI, persistence, and deployment contracts.

## Retrieval

**Retrieval**: One authenticated search or extraction request and its
provenance-bearing outcome.
_Avoid_: Query, fetch, job when referring to the whole operation

**Search result**: One normalized candidate page returned by a search provider,
including its URL, title, snippet, score, provider, and provenance.
_Avoid_: Provider response

**Extracted content**: Normalized readable content obtained from one source URL,
including completeness and provenance.
_Avoid_: Scrape, page dump

**Provenance**: The provider or extractor, egress class, machine, and source
type that explain where a search result or extracted artifact came from.
_Avoid_: Attribution when referring to acquisition origin

**RRF score attribution**: The per-provider contributions that sum to a fused
Reciprocal Rank Fusion score.
_Avoid_: Provider value attribution

**Topology awareness**: Argus's distinction between residential and datacenter
egress when selecting a provider or extraction path.
_Avoid_: Location awareness

**Adaptive domain memory**: Time-bounded evidence that a domain behaves
differently across egress classes, used to avoid repeating a known-bad path.
_Avoid_: Permanent domain rule, blocklist

## Ownership and integration

**Argus**: The owner of internet retrieval and useful retrieval-operational
evidence.
_Avoid_: Memory store, orchestration control plane

**Maya**: The durable owner of user-visible retrieval artifacts and the fleet's
orchestration control plane.
_Avoid_: Argus log sink, transport

**Hermes**: The Slack transport that invokes typed Maya or Argus contracts; it
does not own retrieval or durable memory.
_Avoid_: Retrieval broker, durable system of record

**Clio**: A retired system name preserved only in historical evidence.
_Avoid_: Current caller, owner, worker, fallback

**Retrieval capture**: The bounded, sanitized Maya representation of one
user-visible retrieval, with one parent artifact and linked extracted-page
children.
_Avoid_: Operational log, raw provider payload

**Capture outbox**: Argus's durable queue of retrieval captures awaiting
idempotent Maya acknowledgement.
_Avoid_: Cache, best-effort delivery

**Capture dead letter**: A sanitized capture that cannot be delivered normally
and remains explicitly actionable without silent loss or truncation.
_Avoid_: Dropped capture, failed log

**Caller identity**: The authenticated principal derived from a scoped
credential and used for authorization, attribution, and budget policy.
_Avoid_: A caller-supplied name as security identity

**Invocation label**: Optional caller-provided context recorded for diagnosis
but never trusted for authorization or spending authority.
_Avoid_: Caller identity

## Production and reliability

**Canonical production**: The single homelab Docker Argus used by the fleet;
Mac instances are development-only and OCI has no Argus role.
_Avoid_: Mac primary, OCI fallback, second primary

**Private service boundary**: Approved remote callers reach Argus behind
Tailscale, while PostgreSQL, SearXNG, and container-internal traffic remain
inside the homelab boundary.
_Avoid_: Public endpoint, general-LAN service

**Personal production**: A single-instance service without high-availability
infrastructure that must recover unattended and never silently lose
acknowledged work.
_Avoid_: Hobby instance, highly available service

**Live**: The process can make progress. Liveness says nothing about whether
Argus can safely accept a retrieval.
_Avoid_: Healthy, ready

**Ready**: Argus can authenticate a caller, commit required durable state and a
capture outbox record, and use its minimum supported retrieval paths.
_Avoid_: Merely live, all optional providers healthy

**Degraded**: Argus remains ready through its minimum path while an optional
provider, capability, delivery path, or observation is unavailable.
_Avoid_: Healthy, unready

**Unready**: Accepting new retrievals would violate authentication, durability,
capture, schema, or minimum-capability guarantees.
_Avoid_: Dead, degraded

**Runtime capability**: A named local facility whose configured intent,
observed state, reason, and freshness are reported separately from service
readiness.
_Avoid_: Installed package, whole-service health

**Production authority**: The sole homelab HTTP process that executes
retrievals and owns broker, browser, budget, health, persistence, and outbox
state.
_Avoid_: MCP broker, CLI writer

**Production state**: Durable Argus operational truth in its isolated database
on the shared homelab PostgreSQL service.
_Avoid_: SQLite file, shared Atlas database, process cache

**Disposable state**: Reconstructible caches, browser contexts, warmed clients,
locks, scratch files, and in-flight gauges whose loss cannot erase
acknowledged work or reset spending.
_Avoid_: Production state

## Release identity

**Release**: One tested and attested immutable image digest eligible for
production promotion.
_Avoid_: `latest`, branch tag, build run

**Build identity**: The source revision, package version, schema compatibility,
and capability inventory baked into a release.
_Avoid_: Deployment identity

**Deployment identity**: One recorded attempt to promote an exact release with
a particular non-secret configuration revision.
_Avoid_: Build identity, process ID

**Service-instance identity**: One running API or MCP process; it changes on
restart even when the release and deployment remain unchanged.
_Avoid_: Deployment identity, hostname

**Known-good release**: A promoted release that completed the provisional
observation gates and remains eligible as the application rollback target.
_Avoid_: Most recently built image

## Spending and observation

**Provider tier**: A routing class based on the replenishment behavior of a
provider's allowance: free, recurring, or non-recurring.
_Avoid_: Quality rank

**Budget charge**: Argus's durable record of provider consumption attributable
to an attempt.
_Avoid_: Provider-authoritative balance

**Budget reservation**: A conservative provider-specific charge held durably
before a paid attempt and later settled to the known actual charge.
_Avoid_: Estimated usage after the call

**Uncertain charge**: A reservation whose provider outcome could not be
confirmed and that continues to reduce available budget until reconciled.
_Avoid_: Automatic refund, expired reservation

**Balance snapshot**: A time-bounded provider-reported observation kept
distinct from Argus's local budget ledger.
_Avoid_: Budget charge, eternal balance
