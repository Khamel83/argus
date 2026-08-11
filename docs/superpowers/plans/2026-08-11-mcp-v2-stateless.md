# MCP v2 Stateless Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with verification checkpoints.

**Goal:** Upgrade Argus to the official MCP Python SDK v2 so production Streamable HTTP supports MCP 2026-07-28 one-shot requests and legacy 2025 clients together.

**Architecture:** Keep `HttpMcpAdapter` as the only production execution seam. Replace the v1 `FastMCP` registration/runtime with v2 `MCPServer`, configure `stateless_http=True` for Streamable HTTP, and let the SDK route modern versus legacy protocol eras. Authentication and caller identity continue to be resolved by the existing authority-backed token verifier.

**Tech Stack:** Python 3.12, MCP Python SDK 2.0.0, Starlette/ASGI, pytest, uv lockfile.

## Global Constraints

- `mcp>=2.0.0,<3` is the only supported SDK range.
- Modern requests must validate `MCP-Protocol-Version`, `Mcp-Method`, and `Mcp-Name` against the JSON-RPC body.
- Production MCP never constructs the local broker or owns provider credentials, DB, browser, budgets, or durable state.
- Preserve the 2025-11-25 compatibility path and stdio behavior.
- No provider-spend calls in transport compatibility tests.

---

### Task 1: Upgrade and migrate the server registration seam

**Files:**
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `argus/mcp/server.py`
- Test: `tests/test_http_authority.py`

**Interfaces:**
- Consumes: existing `HttpMcpAdapter`, `StaticTokenVerifier`, and tool functions.
- Produces: `serve_mcp()` backed by v2 `MCPServer`, with `stateless_http=True` for Streamable HTTP.

- [ ] Write RED tests for v2 import/registration and modern/legacy transport behavior.
- [ ] Change dependency to `mcp>=2.0.0,<3` and regenerate the lockfile.
- [ ] Replace `FastMCP` with `MCPServer`; replace the v1 Context import with the v2 context type; keep tool/resource decorators and authority calls unchanged.
- [ ] Pass `stateless_http=True` only for Streamable HTTP; preserve stdio and legacy SSE invocation.
- [ ] Run targeted MCP/HTTP tests and fix only v2 API incompatibilities.
- [ ] Commit `feat: support MCP 2026-07-28 stateless transport`.

### Task 2: Add conformance and no-spend compatibility gates

**Files:**
- Modify: `tests/test_http_authority.py`
- Modify: `docs/mcp-clients.md`
- Modify: `docs/research/2026-08-11-mcp-stateless-production-authority.md`

**Interfaces:**
- Consumes: v2 ASGI app and existing fake HTTP authority.
- Produces: deterministic in-process evidence for modern one-shot and legacy compatibility.

- [ ] Test a direct 2026-07-28 `tools/call` without initialize or session header.
- [ ] Assert `Mcp-Method`/`Mcp-Name` mismatch returns a protocol rejection before backend invocation.
- [ ] Assert no `Mcp-Session-Id` is minted for the modern response.
- [ ] Retain a 2025 initialize/tools-list test.
- [ ] Document that the new server supports both protocol eras and update migration gates.
- [ ] Run the full hermetic suite, Ruff, and diff checks.

### Task 3: Verify promotion readiness without deploying

**Files:**
- Create: `.superpowers/sdd/task-mcp-v2-verification.md` (ignored)

- [ ] Run the targeted suite and full hermetic suite with the fast-volume `TMPDIR`.
- [ ] Run a no-spend remote canary against a non-production/staged image or local ASGI app.
- [ ] Record exact SHA, dependency lock hash, modern/legacy status, session-header behavior, and no-spend evidence.
- [ ] Stop before image promotion until the user explicitly approves deployment.
