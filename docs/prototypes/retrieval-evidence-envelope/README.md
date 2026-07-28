# Canonical retrieval evidence envelope decision record

The retired throwaway prototype answered one question:

> Can one bounded, closed-reference envelope represent the accepted retrieval
> plan, readiness decisions, cache decision, provider attempts, normalized
> provenance, deterministic ranking, freshness, extraction attempts,
> rejection, latency/spend, durable acceptance, and exact caller-visible
> result without allowing a renderer to reconstruct truth?

The executable prototype, aggregate vectors, and JSON Schema were retired
after S7 moved every learned invariant into the production validator at
`argus/contracts/evidence.py`. The standalone frozen fixtures remain the
executable compatibility evidence and perform no network, provider,
extractor, credential, or database work.

Run the production replay:

```bash
uv run pytest tests/test_accepted_operations.py \
  -k production_evidence_validator -q
```

The eight vectors cover the five cases required by issue #65, the two
additional downstream cases required by ADR 0005, and an eligible cache hit
that proves current execution is distinct from preserved origin evidence:

1. complete success;
2. degraded partial-provider/partial-extraction evidence;
3. complete fallback success that retains failed steps without a final
   rejection;
4. stale cache rejection followed by live success;
5. no eligible provider (`unready`, never `empty`);
6. rejected extraction with search evidence retained but no synthesis; and
7. persistence failure with no fabricated receipt, delivery, or cache
   publish; and
8. eligible cache reuse with zero current provider calls or spend while the
   paid origin attempt, provenance, and spend remain auditable.

## Production compatibility port

S0 of the issue #82 mechanical port copied all eight vectors and all nineteen
fail-closed mutations into
`tests/fixtures/contracts/retrieval_evidence_v2/`. Its manifest hashes both
these standalone fixtures and their prototype sources. S0 applies only the
accepted-operation outcome, error, request-ID, and privacy invariants; S7 owns
the complete production replay after the required deep modules exist.

S7 now replays every fixture through the production invariant validator. The
throwaway executable is no longer an alternate authority. Preserve the
decision in [NOTES.md](NOTES.md).
