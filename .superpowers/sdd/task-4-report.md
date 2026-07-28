# Task 4 report: S3 extraction finalization and composition

## Result

Implemented the inactive S3 seam for immutable extraction finalization,
atomic durable acceptance, and retrieval-evidence composition. Runtime
activation remains deferred to S7.

## Delivered

- Closed 11-value extractor-attempt taxonomy and immutable request, plan,
  trace, artifact, terminal-cause, rejection, acceptance, and receipt values.
- Single-authority finalizer with exact trace and terminal validation,
  one-time #57 rejection classification, deterministic retry/conflict
  semantics, legacy `ExtractedContent` projection, and no fabricated receipt.
- Pure retrieval composer over the narrow `RetrievalEvidenceView` protocol,
  enforcing one link per selected cluster, per-result and aggregate floors,
  readiness, canonical precedence, and explicit policy-compatible artifact
  reuse.
- Inclusive hard bounds of 16 extraction steps and 200 unique result links;
  the 17th step and 201st link are rejected without partial publication.
- Privacy-safe bounded rejection facts: no raw error, URL, request body,
  content, or credential material enters the rejection projection.
- Archive lookup remains the default. External archive creation now requires
  a distinct `ArchiveCreationAuthorization`, authority-backed verification,
  and atomic consumption by a durable store before network work.
- Cache identity now covers the exact normalized URL, mode, access/auth and
  privacy scope, cache, plan, quality, completeness, outcome, and
  partial-artifact policy.
- Additive Alembic revision `0007_extraction_outcomes` with plan, step,
  artifact, rejection, acceptance, composition, link, and activation tables.
  The downgrade refuses before issuing a drop after an activation receipt.
- Recovery/status schema-head fixtures now truthfully track the 0007 Alembic
  head. Recovery inventories all eight S3 tables and their required columns,
  then validates every plan/link relationship.

## Reviewer correction

The separate correction closes every S3 review finding:

- #57 alone derives rejection codes from typed source facts. The finalizer
  validates the complete mapper result, rejects signed-64 aggregate overflow,
  and derives fallback readiness from the immutable plan.
- SQL finalization claims/locks the run before classification. Concurrent
  retries classify once; a uniqueness loser reloads the committed acceptance.
- Terminal variants are closed and plan-bound, including exact exhaustion and
  named attempt-terminal policy rules.
- Stable artifact and rejection references support identity-verified reuse
  across distinct runs while conflicting identities fail closed.
- Composition links are constructed from typed accepted outcomes and
  defensively rebound to the same run, plan, receipt, artifact/rejection row,
  scope, policies, readiness, and reuse origin. Composite foreign keys close
  the same-plan durable relationships; no-run links persist with null
  extraction identities.
- Eligible cache entries require receipt-bearing accepted usable or
  policy-allowed partial outcomes and preserve the complete origin artifact,
  rejection, trace, provenance, spend, policy, receipt, and creation lineage.
- Credential-bearing URL material is removed from every durable plan,
  extraction projection, and composition projection; only a redacted URL and
  safe source identity remain.
- Migration latency is `BigInteger`; downgrade remains activation-guarded.

## TDD and verification

- RED: the exact focused command initially failed collection because
  `argus.extraction.outcomes` and `argus.extraction.composition` did not exist.
- GREEN focused:
  `141 passed, 6 skipped`.
- Recovery/status compatibility:
  `95 passed, 2 skipped`.
- Full suite:
  `1417 passed, 39 skipped`, with four pre-existing Starlette cookie
  deprecation warnings.
- Ruff over every changed Python path: all checks passed.
- `git diff --check`: passed.

PostgreSQL migration coverage is present but skips when the disposable
PostgreSQL fixture is unavailable. No migration was run against production;
no network retrieval, archive creation, historical replay, deployment, push,
PR, or merge was performed.

## Commit

Commit message: `feat: finalize and compose extraction outcomes`

Correction commit message:
`fix: close extraction finalization review gaps`
