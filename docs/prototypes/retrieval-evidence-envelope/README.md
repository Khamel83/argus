# PROTOTYPE — canonical retrieval evidence envelope

This throwaway prototype answers one question:

> Can one bounded, closed-reference envelope represent the accepted retrieval
> plan, readiness decisions, cache decision, provider attempts, normalized
> provenance, deterministic ranking, freshness, extraction attempts,
> rejection, latency/spend, durable acceptance, and exact caller-visible
> result without allowing a renderer to reconstruct truth?

It is fixture-only. It imports no Argus product module, opens no database,
uses no credential, and makes no network/provider/extractor call. The schema
is a prototype projection, not a storage migration or production API.

Run every vector and the deliberately corrupted fail-closed checks:

```bash
uv run python docs/prototypes/retrieval-evidence-envelope/prototype.py --all
```

Run the terminal viewer:

```bash
uv run python docs/prototypes/retrieval-evidence-envelope/prototype.py
```

The viewer uses `n`/`p` to move between scenarios, `j` to toggle the complete
JSON envelope, and `q` to quit. When stdout is not a terminal, the prototype
automatically uses `--all`.

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

## Frozen compatibility port

S0 of the issue #82 mechanical port copies all eight vectors and all nineteen
fail-closed mutations into
`tests/fixtures/contracts/retrieval_evidence_v2/`. Its manifest hashes both
these standalone fixtures and their prototype sources. S0 applies only the
accepted-operation outcome, error, request-ID, and privacy invariants; S7 owns
the complete production replay after the required deep modules exist.

Keep this executable prototype green until S7 completes that replay. Delete
the executable shell and fixture schema only after the production contracts
cover every learned invariant. Preserve the decision in [NOTES.md](NOTES.md).
