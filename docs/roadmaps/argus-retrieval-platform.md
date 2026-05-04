# Argus Retrieval Platform Roadmap

## Summary
- Reposition Argus as a retrieval platform for agents: search, recover, capture, summarize, and persist web knowledge.
- Internalize the `docs-cache` pattern into Argus itself. Do not require a sibling repository.
- Default runtime corpus output to a global user data root resolved via `platformdirs`, overrideable with `ARGUS_DATA_ROOT`.
- Make runtime paths visible through CLI, HTTP, and MCP.

## Workstreams
1. Corpus foundation and path model.
2. Recover dead article workflow.
3. Site capture and summary workflow.
4. Docs plus research pack workflow.
5. End-to-end evals and public-product refinements.
6. Topology-aware acquisition and provenance (Completed 2026-05-03).

## Storage Defaults
- Runtime data is stored outside the repo checkout by default.
- Resolved paths are exposed by `argus paths`, `GET /api/admin/paths`, and the MCP `argus_paths` tool.
- Official docs live under the Argus-owned docs cache area.
- Research packs, workflow manifests, and versioned snapshots live under the Argus data root.

## Notes
- `docs-cache` is no longer a required sibling repo.
- Legacy `docs-cache` trees can be imported with `argus corpus import-docs-cache`.
- The public product story should describe Argus as owning its own corpus model directly.
