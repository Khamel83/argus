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
  head. Recovery inventories all S3 tables and their required columns,
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

## Third reviewer correction

The third strict review is closed by a separate correction:

- Every cache hit carries an immutable current `ExtractionCacheIdentity`.
  Finalization derives the expected identity from the current plan and outcome
  policy, then requires exact equality with both the decision and durable
  origin across URL identity, mode, access/privacy/authentication scope, cache,
  extraction-plan, quality, completeness, outcome policy, and partial policy.
  Cache age is recomputed from the durable cache-creation timestamp and the
  authority clock; caller-authored age is not trusted.
- Link eligibility is derived from eligible candidates in the immutable
  accepted plan, while attempted state is derived from invoked accepted
  steps. A valid preflight unready outcome with no candidates deterministically
  produces `false/false`.
- Composition persistence rebinds and exactly compares every run-bearing link
  to its durable accepted projection before any fingerprint replay return.
  Forged artifact text, latency, or other semantic fields cannot reuse a real
  receipt.
- Artifact and rejection nullable composite groups retain `MATCH FULL` and
  now also require their plan IDs to equal the extraction acceptance plan.
  Named checks are enforced by migration, ORM metadata, tests, recovery
  constraint inventory, and orphan verification.
- The recovery-record workflow passes a trusted complete 0007 schema manifest
  into Argus verification instead of discarding its expected contract.
  Verification checks the head, required tables and columns, named
  constraints, and indexes before recording success.
- Composition acceptance uses atomic dialect-native insert-on-conflict keyed
  by the source fingerprint. A uniqueness loser reloads and returns the exact
  committed receipt; concurrent identical SQLite coverage is mandatory and
  equivalent PostgreSQL coverage runs when the disposable fixture is
  available.

Third correction commit message:
`fix: bind extraction replay to complete durable identity`

Third-correction verification:

- Exact focused S3 command: `166 passed, 7 skipped`.
- Recovery and operational-status command: `109 passed, 18 skipped`.
- Dedicated PostgreSQL migration and composition-concurrency tests: present;
  `2 skipped` because the disposable PostgreSQL fixture was unavailable.
- Full repository suite: `1444 passed, 40 skipped`, with the same four
  Starlette cookie deprecation warnings.
- Ruff over every changed Python path: all checks passed.
- `git diff --check`: passed.

PostgreSQL migration coverage is present but skips when the disposable
PostgreSQL fixture is unavailable. No migration was run against production;
no network retrieval, archive creation, historical replay, deployment, push,
PR, or merge was performed.

## Fourth reviewer correction

The fourth strict review is closed by a separate correction:

- `HIT_INELIGIBLE` is a cache-routing result, not a contract failure. An
  origin/current identity mismatch now bypasses reuse and continues through
  the ordinary extraction path; strict origin publication checks remain
  mandatory for `HIT_ELIGIBLE`.
- Cache maximum age is immutable input carried by the extraction plan, cache
  identity, canonical identity fingerprint, and origin evidence. Eligible
  reuse derives age from the durable acceptance timestamp and authority
  clock, accepts the exact boundary, and rejects one second beyond it or an
  arbitrarily stale entry.
- Authentication scope is an authority-receipted, opaque fingerprint bound
  from request to plan, invoked-step provenance, artifact provenance, and
  cache identity. Authenticated/cookie provenance must hash to that exact
  scope. Private or otherwise non-public work cannot silently use the
  anonymous scope.
- A populated artifact or rejection link now explicitly requires a
  non-null extraction plan and acceptance receipt for that same plan.
  Migration and ORM checks reject both orphan shapes in SQLite; recovery
  queries use null-safe `IS DISTINCT FROM` comparisons.
- The trusted 0007 recovery schema manifest now covers every required table
  and column with normalized type/nullability, required constraint and index
  definitions, and a contract SHA-256. The verifier compares the actual
  restored schema to the supplied manifest rather than comparing a supplied
  expectation with a second local expectation.

Fourth-correction TDD and verification:

- RED: focused regressions initially failed for ineligible-cache fallback,
  missing max-age identity, missing authority authentication scope, missing
  acceptance-bound branch checks, and the name-only/local manifest trust
  check.
- GREEN focused extraction/composition/recovery command:
  `127 passed, 4 skipped`.
- Full repository run: `1449 passed, 40 skipped`, with five failures limited
  to unchanged Brave/GitHub rate-reset fixtures whose hard-coded
  `2026-07-28T00:53:20Z` reset is now behind the wall clock, plus the same four
  Starlette cookie deprecation warnings.
- Full remainder excluding the two stale parameterized provider-fixture test
  functions: `1427 passed, 40 skipped, 27 deselected`.
- Ruff over every changed Python path: all checks passed.
- `git diff --check`: passed.

Fourth correction commit message:
`fix: enforce cache authority and schema contracts`

## Commit

Commit message: `feat: finalize and compose extraction outcomes`

Correction commit message:
`fix: close extraction finalization review gaps`

## Second reviewer correction

The second strict review is closed by a separate correction:

- Issue #57 now returns a sealed validated mapping. Structural opening never
  invokes the classifier again, and concurrent finalization through two
  independent repository instances executes the mapper exactly once total.
- `ResultExtractionLink.from_accepted` derives eligibility and attempted
  readiness solely from the accepted plan and trace. Callers cannot select
  alternate readiness for the same accepted failure.
- Cache identities are derived from accepted outcomes, including opaque URL
  identity, mode, access/privacy/authentication scope, and every governing
  policy version. Publication requires an exact durable acceptance reload.
  Cache-origin evidence carries the durable receipt, accepted timestamp,
  cache creation timestamp, scopes, identity, policies, artifact, rejection,
  and trace; fabricated receipts fail closed.
- The semantic accepted projection is sanitized before first persistence and
  return. It retains only the safe URL origin plus an opaque raw identity, so
  first acceptance and retry reload are equal and query, fragment, userinfo,
  and path secrets are not durable semantic facts.
- Composition persistence reloads the durable accepted projection by receipt
  and requires exact equality, including outcome, disposition, policies,
  trace, terminal cause, timestamps, scopes, and operation facts.
- Nullable composition relationships use named `MATCH FULL` composite foreign
  keys, separate nullable plan groups, and named all-or-none checks. SQLite
  behavior rejects a fabricated acceptance reference with a null plan;
  PostgreSQL inspection coverage verifies the same definitions when its
  disposable fixture is available.
- A canonical `extraction_artifact_identities` table owns the unique
  `artifact_ref`. Atomic conflict-do-nothing insertion followed by exact
  identity verification makes concurrent conflicting reuse deterministic,
  while per-plan artifact evaluations remain separate.
- Recovery inventories constraint and index definitions into the schema
  fingerprint and rejects a stamped database missing any required S3 foreign
  key, unique/check constraint, or index.
- `SQLiteArchiveCreationAuthorizationStore` atomically consumes an opaque
  target identity and proves replay remains rejected after store restart.
  Lookup-only archive behavior remains the default.

Second correction commit message:
`fix: enforce durable extraction identity invariants`

Second-correction verification:

- Exact focused S3 command: `153 passed, 6 skipped`.
- Recovery and operational-status command: `96 passed, 2 skipped`.
- Dedicated PostgreSQL migration/constraint test: present; `1 skipped`
  because the disposable PostgreSQL fixture was unavailable.
- Full repository suite: `1430 passed, 39 skipped`, with the same four
  Starlette cookie deprecation warnings.
- Ruff over every changed Python path: all checks passed.
- `git diff --check`: passed.
