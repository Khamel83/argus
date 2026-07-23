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
- [releasing.md](releasing.md) — release process: version sync, preflight, publish, verify
- [troubleshooting.md](troubleshooting.md) — common installation, MCP, search, and extraction issues
- [operations-status.md](operations-status.md) — liveness, startup, readiness, authenticated status, and bounded telemetry semantics
- [dashboard-design.md](dashboard-design.md) — dashboard UI design system reference
- [PUBLICITY-CHECKLIST.md](PUBLICITY-CHECKLIST.md) — project-internal launch / publicity checklist
- [roadmaps/](roadmaps/) — long-range roadmap documents
- [research/](research/) — research notes used while building features

Auto-generated and operator-local docs (e.g. `LLM-OVERVIEW.md`, session logs) live
at the repo root or are gitignored — see `.gitignore`.
