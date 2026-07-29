# Argus Documentation

Top-level entry points live at the repo root:

- [README.md](../README.md) — user-facing overview, install, quickstart, HTTP API reference, configuration table, FAQ
- [CONTRIBUTING.md](../CONTRIBUTING.md) — dev setup, how to add a provider, PR conventions
- [CONTEXT.md](../CONTEXT.md) — glossary and architectural concepts that recur in reviews
- [AGENTS.md](../AGENTS.md) — canonical guide for AI coding agents
- [CLAUDE.md](../CLAUDE.md) — Claude-Code-specific notes (short, points to AGENTS.md)
- [CHANGELOG.md](../CHANGELOG.md) — release history (Keep a Changelog format)
- [SECURITY.md](../SECURITY.md) — security policy and private disclosure path

## In this directory

- [mcp-clients.md](mcp-clients.md) — per-client MCP setup, verification, troubleshooting (Claude Code, Codex, OpenCode, Cursor, Gemini CLI, Antigravity)
- [providers.md](providers.md) — full reference for search providers and the 12-step content extraction chain
- [extraction-rejections.md](extraction-rejections.md) — stable, privacy-safe rejection evidence returned by the extraction API
- [releasing.md](releasing.md) — release process: version sync, preflight, publish, verify
- [troubleshooting.md](troubleshooting.md) — common installation, MCP, search, and extraction issues
- [operations-status.md](operations-status.md) — liveness, startup, readiness, authenticated status, and bounded telemetry semantics
- [adr/0002-bounded-retrieval-plan-cache-identity.md](adr/0002-bounded-retrieval-plan-cache-identity.md) — deterministic internal planning, cache identity, eligibility, and invalidation
- [adr/0003-provider-aware-freshness-provenance-ranking.md](adr/0003-provider-aware-freshness-provenance-ranking.md) — typed provider evidence, freshness proof, duplicate clustering, deterministic fusion, and research diversity
- [adr/0004-no-spend-provider-readiness.md](adr/0004-no-spend-provider-readiness.md) — typed provider readiness, no-spend diagnostics, terminal exhaustion, and profile registration
- [adr/0005-structured-extraction-outcome-composition.md](adr/0005-structured-extraction-outcome-composition.md) — typed extraction finalization, terminal-cause mapping, and retrieval composition
- [adr/0006-http-mcp-compatibility-contract.md](adr/0006-http-mcp-compatibility-contract.md) — legacy HTTP preservation, evidence-rich opt-in, MCP lifecycle, and transport parity
- [prototypes/retrieval-evidence-envelope/README.md](prototypes/retrieval-evidence-envelope/README.md) — decision record for the canonical retrieval/evidence envelope; production replay covers eight hermetic scenarios and nineteen fail-closed mutations
- [superpowers/plans/2026-07-27-retrieval-evidence-mechanical-port.md](superpowers/plans/2026-07-27-retrieval-evidence-mechanical-port.md) — dependency-ordered isolated implementation and operationally gated homelab port plan
- [dashboard-design.md](dashboard-design.md) — dashboard UI design system reference
- [scorecards/stability-competitive.md](scorecards/stability-competitive.md) — hard stability gates and automated competitive-evidence verdicts
- [PUBLICITY-CHECKLIST.md](PUBLICITY-CHECKLIST.md) — project-internal launch / publicity checklist
- [roadmaps/](roadmaps/) — long-range roadmap documents
- [research/](research/) — research notes used while building features

Auto-generated and operator-local docs (e.g. `LLM-OVERVIEW.md`, session logs) live
at the repo root or are gitignored — see `.gitignore`.
