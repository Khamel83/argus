# Task 5 report: S4 exact provider-aware evidence fusion

## Result

Implemented the inactive S4 fusion seam for normalized provider batches. The
module is pure and synchronous: it performs no provider call, network update,
spend, persistence, cache publication, deployment, or production activation.
Legacy ranking and deduplication remain unchanged and selectable until S7.

## RED and GREEN

- Initial RED: the exact focused command failed collection with
  `ModuleNotFoundError: No module named 'argus.broker.fusion'`.
- Review REDs separately proved the private-PSL rule gap, missing normalized
  provider-batch trace, missing five-key ordering helper, and missing
  compatibility provenance before each correction.
- Dedicated GREEN: `52 passed`.
- Exact focused GREEN:
  `158 passed` for `tests/test_evidence_fusion.py`, `tests/test_broker.py`, and
  `tests/test_attribution.py`.
- Final full repository GREEN: `1551 passed, 42 skipped`, with four existing
  Starlette per-request-cookie deprecation warnings.
- Ruff over all four changed Python paths and `git diff --check`: passed.

## Exact contract evidence

- Freshness proves inclusive day, week, 30-day month, 365-day year, explicit
  date, and end-of-day timestamp edges. Date/month/year precision is converted
  to its complete UTC interval; the complete interval must fit the request.
- Approved proof requires provider-field/provider-age source, approved contract
  confidence, a bounded semantic contract reference, and publication semantics.
  Unverified, result-text, modified, indexed, missing, coarse-outside-window,
  and disjoint same-document claims fail closed. Widened translations always
  receive the exact broker post-filter.
- A freshness empty is proven only by a recognized `EMPTY` batch with exact,
  strict-contract translation. Other zero-result or fully rejected evidence is
  `freshness_unproven`, not provider failure.
- The v1 document key removes default ports/fragments, applies IDNA/lowercase
  host handling, normalizes unreserved percent bytes and dot segments, and
  preserves scheme, path case, trailing slash, query order, duplicates,
  key-only/empty values, `www`, and tracking parameters. Similar text and weak
  provider hints never merge.
- RRF uses `Fraction(1, 60 + provider_rank + 1)`, one best contribution per
  provider, exact numerator/denominator storage, retained observations/ranks,
  and all five declared tie keys. The 7/11/29 vector is replayed over all six
  provider-map permutations. Compatibility attribution is additive within
  absolute tolerance `1e-15`.
- Discovery, grounding, and recovery preserve base order. Research executes
  coverage, two-per-site fill, then base-order relax. Site keys use the locally
  packaged, uv-locked PSL snapshot including private rules such as `github.io`.
  Floors scale exactly to `min(3, result_limit)` clusters and
  `min(2, required_clusters)` sites.
- Bounds are checked before phase work: at most 14 provider batches, 50
  observations per provider, and 700 total observations. The eight fake-clock
  expiry positions prove monotonic checks before and after normalize/filter,
  cluster, rank, and diversify; expiry does not start the next phase.
- `FusionOutcome`, clusters, contributions, provider batches, filter,
  duplicate, ranking, diversity, floor, and phase traces are frozen snapshots.
  The `SearchResult` projection derives its float score and preserves the
  representative URL/title/snippet/provider plus egress, machine, source kind,
  and observation time without mutating the outcome.
- Static AST coverage rejects async functions, `await`, and network-client
  imports. Provider-native responses and plaintext query copies never enter the
  fusion interface or trace.

## Commit and boundaries

Planned local commit: `feat: fuse provider evidence deterministically`.
Only the four Task 5 implementation/test paths and this report are staged.
There is no push, PR, merge, deployment, production mutation, or external
publication.

## Residual risks

- S2 currently carries one publication claim per observation. S4 detects
  conflicting approved claims across observations sharing a proven document
  key; a future plural per-observation claim shape must retain the same
  fail-closed overlap rule.
- S2 exposes no accepted Argus-verified redirect/canonical relation, so v1
  search fusion intentionally merges only equal conservative keys. The
  verified-canonical representative priority remains dormant until that typed
  evidence exists.
- The PSL implementation uses the `tld` package and its data already locked
  transitively in `uv.lock`; a future dependency reorganization must make that
  runtime dependency explicit or vendor the exact snapshot without changing
  `domain_policy_version`.
- S7 must pass the inherited absolute operation deadline in `FusionPolicy`.
  Leaving it unset is supported only for isolated pure tests and inactive
  compatibility use; this task does not activate the seam.
