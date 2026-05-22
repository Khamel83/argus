# Claude Code — Argus

> **What this file is for:** Claude Code's entry point into this repo. The
> canonical guide for any AI coding agent (Claude Code, Codex, Cursor, Copilot,
> OpenCode) lives in [AGENTS.md](AGENTS.md) — Claude Code reads both, but the
> content lives there. Keep this file short.

See [AGENTS.md](AGENTS.md) for project conventions, architecture, key commands,
provider tiers, configuration, and the rules contributors must follow.

## Claude-Code-specific notes

- The MCP server in this repo is `argus` — you can use it directly from Claude
  Code via `argus mcp serve` (stdio) or by configuring a remote HTTP transport.
  See the **Integration → MCP** section of [README.md](README.md).
- The repo is pinned to Python 3.12 for development. Use `uv run pytest ...`,
  not the system interpreter. See [CONTRIBUTING.md](CONTRIBUTING.md).
- Local-only operator artifacts (e.g. `1shot/`, `docs/sessions/`, `.janitor/`,
  `.claude/`) are gitignored. Don't write into them as part of a PR.
- When changing user-facing behavior, update README, `.env.example`, and
  CHANGELOG (under `[Unreleased]`). Do not bump the version in `pyproject.toml`
  or `server.json` — releases are cut separately. See
  [docs/releasing.md](docs/releasing.md).
