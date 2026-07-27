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
  a distinct, one-use `ArchiveCreationAuthorization`; missing, reused,
  autonomous, or malformed authority is rejected before network work.
- Cache identity now covers mode, access/auth scope, plan, quality,
  completeness, and partial-artifact policy.
- Additive Alembic revision `0007_extraction_outcomes` with plan, step,
  artifact, rejection, acceptance, composition, link, and activation tables.
  The downgrade refuses before issuing a drop after an activation receipt.
- Recovery/status schema-head fixtures now truthfully track the 0007 Alembic
  head while the new tables remain outside the active runtime contract.

## TDD and verification

- RED: the exact focused command initially failed collection because
  `argus.extraction.outcomes` and `argus.extraction.composition` did not exist.
- GREEN focused:
  `122 passed, 6 skipped`.
- Recovery/status compatibility:
  `95 passed, 2 skipped`.
- Full suite:
  `1398 passed, 39 skipped`, with four pre-existing Starlette cookie
  deprecation warnings.
- Ruff over every changed Python path: all checks passed.
- `git diff --check`: passed.

PostgreSQL migration coverage is present but skips when the disposable
PostgreSQL fixture is unavailable. No migration was run against production;
no network retrieval, archive creation, historical replay, deployment, push,
PR, or merge was performed.

## Commit

Commit message: `feat: finalize and compose extraction outcomes`
