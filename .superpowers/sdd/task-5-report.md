# Task 5 report: S4 exact provider-aware evidence fusion

## Result

Implemented the inactive S4 fusion seam for normalized provider batches. The
module remains pure and synchronous: it performs no provider call, network
update, spend, persistence, cache publication, deployment, or production
activation. Legacy ranking and deduplication remain unchanged until S7.

## Strict RED to GREEN

- The initial Task 5 RED failed collection because `argus.broker.fusion` did
  not exist.
- The strict review RED failed collection for the missing pinned-PSL identity
  and typed temporal claim kind. Subsequent RED assertions covered the closed
  freshness/empty registries, exact applied windows, hard ceilings, non-finite
  clocks, reserve-aware activation, lossless query grammar, computed answers,
  complete ranking/diversity traces, and post-phase timeout semantics.
- The second strict re-review RED produced 25 failures across the complete
  publication precision/value cross-product, forged UNKNOWN timestamp proof,
  excluded URL/failure/empty/computed/CACHE batches, and duplicate provider
  batches. Provider identity mismatch coverage was also made explicit.
- Corrected evidence/provider GREEN: `388 passed`.
- Exact focused GREEN for `tests/test_evidence_fusion.py`,
  `tests/test_broker.py`, and `tests/test_attribution.py`: `193 passed`.
- Final full repository GREEN: `1622 passed, 42 skipped`, with four existing
  Starlette per-request-cookie deprecation warnings.
- `ruff check` and `git diff --check`: passed.

## Contract evidence

- Freshness proof is authorized only by the closed
  `(freshness_policy_version, provider, provider_contract_version,
  semantic_contract_ref, parser_version)` registry. It additionally requires
  typed `PUBLISHED` semantics, an approved provider source and confidence, and
  rejects modified, updated, indexed, created, and crawled fields, including
  camelCase variants.
- `PublicationEvidence` now enforces the exact temporal shape: `TIMESTAMP`
  requires only an actually aware datetime; `DATE`, `MONTH`, `YEAR`, and
  `PROVIDER_AGE` require only a plain `date`; `UNKNOWN` requires no temporal
  value. Fusion independently rejects forged or legacy-invalid shapes before
  they can establish freshness.
- Successful empty proof is separately version-authorized and requires an
  actual normalized `EMPTY` response, exact strict translation, and equality
  among the request's relative/resolved window, the applied provider window,
  and the retrieval plan. A 1999 or otherwise mismatched window fails closed.
- S2 URL sanitation retains raw safe query segments, preserving order,
  duplicates, key-only parameters, and empty values. Fusion therefore keeps
  `?flag` and `?flag=` as distinct document identities end to end.
- The immutable 14-batch, 50-observation-per-provider, and 700-total ceilings
  can only be tightened. Deadlines and clock samples must be finite;
  activatable use requires an inherited absolute deadline and a positive
  final-publication reserve. All eight before/after phase checks are covered,
  and a completed phase is recorded before its post-phase expiry check.
- Every batch provider must be an exact member of the plan's candidate-provider
  set. `CACHE`, excluded providers, duplicate provider batches, and forged
  observation/failure provider identity mismatches are rejected as stable
  invalid input before any fusion phase, retained batch, filter, computed
  answer, ranking, diversity, failure, or strict-empty trace can be produced.
- Computed answers are retained as frozen typed artifacts, excluded from URL
  clustering and RRF, and can satisfy only the grounding evidence floor when
  no freshness constraint applies.
- RRF trace records every eligible cluster before result limiting, all five
  ordering values, and every per-provider exact-fraction contribution.
  Diversity trace records every base candidate, including coverage, fill,
  soft-cap defer, relaxed backfill, and result-limit omission, with site counts,
  pass/disposition, and optional output rank.
- Site keys use direct runtime dependency `tld==0.13.2`. Fusion verifies the
  packaged PSL bytes against SHA-256
  `abf32ce9987d505b89765d76f35760543851235508f1f426b5b259a2062b5f68`;
  the immutable snapshot ID is bound to `domain_policy_version` and emitted in
  the trace. Private suffix behavior such as `github.io` is covered.

## Boundaries and residual risks

- This correction changes only S2 evidence/control normalization, S4
  fusion/models/tests, the direct PSL dependency/lock entry, and this report.
- No push, PR, merge, deployment, production mutation, network/provider call,
  spend, persistence, or external publication occurred.
- S2 still carries one publication claim per observation. A future plural
  claim shape must retain the same closed authorization and fail-closed
  conflict rule.
- S7 must explicitly construct the activatable reserve-aware `FusionPolicy`;
  this task does not activate the seam.
