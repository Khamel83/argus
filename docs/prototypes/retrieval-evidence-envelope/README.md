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

The seven vectors cover the five cases required by issue #65 plus the two
additional downstream cases required by ADR 0005:

1. complete success;
2. degraded partial-provider/partial-extraction evidence;
3. complete fallback success that retains failed steps without a final
   rejection;
4. stale cache rejection followed by live success;
5. no eligible provider (`unready`, never `empty`);
6. rejected extraction with search evidence retained but no synthesis; and
7. persistence failure with no fabricated receipt, delivery, or cache publish.

Delete the executable shell and fixture schema after #67 converts the learned
invariants into production contracts. Preserve the decision in
[NOTES.md](NOTES.md).
