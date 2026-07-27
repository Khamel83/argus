# Prototype verdict

The envelope is viable if it remains a projection over accepted deep-module
facts, not a second execution model.

## What the vectors establish

- One outer result can retain the immutable plan, runtime readiness, cache
  lineage, attempts, normalized observations, fusion/ranking, extraction,
  spend, and acceptance without copying provider-native payloads or raw
  errors.
- References close cleanly: final results point to ranked clusters; citations
  point only to citation-eligible artifacts; extraction links point to the
  exact cluster and extraction run; cache lineage points to an origin run.
- Attempt outcome and artifact quality remain separate. A failed early
  extractor followed by a complete fallback is final `success`; accepted
  partial content and cross-provider partial execution are visibly
  `degraded`.
- A stale entry is rejected and never served implicitly. Live execution may
  still succeed, but the rejected cache decision remains in the trace.
- An eligible cache hit performs zero current provider calls and incurs zero
  new spend while preserving and reconciling the origin provider attempt,
  provenance, and spend.
- No eligible provider is `unready`, not `empty` or `providers_failed`.
- An extraction-floor failure may expose accepted search evidence and
  citation-eligible artifacts diagnostically, but cannot synthesize or deliver
  an answer.
- Persistence failure has no acceptance receipt, cache publication, result
  delivery, or success-like outcome.
- Wall-clock latency, summed attempt latency, and spend reconciliation are
  separate evidence; none affects ranking.
- Ranked clusters retain the exact RRF fraction and deterministic tie-break
  inputs. The accepted caller projection is validated against those clusters,
  extraction links, artifacts, and citations rather than reconstructed by a
  renderer.

## Physical nesting recommendation for #67

Keep the version-2 transport envelope from ADR 0006 unchanged. Put this
prototype shape inside `result.evidence`, with the small caller result beside
it rather than copying trace fields into every result. Persist normalized
facts in deep-module-owned records and assemble this projection from immutable
references only after acceptance.

The production implementation may use typed objects and normalized tables
instead of this JSON nesting. It must retain the reference/invariant behavior,
not the prototype file layout.

## Deliberately undecided here

- database tables, migrations, indexes, retention, and archival;
- public pagination or trace-detail expansion;
- final Python class/module names;
- transport rollout order beyond ADR 0006;
- streaming/resumability beyond the accepted bounded response contract; and
- any live provider, paid validation, deployment, or production cutover.
