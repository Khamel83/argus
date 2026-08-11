# MCP v2 and 2026-07-28 Stateless Transport

## Goal

Make Argus speak the MCP 2026-07-28 stateless Streamable HTTP protocol while
retaining compatibility with existing 2025-11-25 clients during migration.

## Design

Upgrade the official Python SDK to the v2 line and migrate the registration
seam from `FastMCP` to `MCPServer`. Configure one remote Streamable HTTP app in
stateless mode. The SDK's dual-era router handles modern one-shot requests and
legacy initialize/session requests; Argus tools continue calling the
authenticated HTTP authority and do not gain local broker, database, browser,
budget, or durable-session ownership.

The current scoped bearer-token verifier remains the authentication boundary.
Modern requests must carry the protocol version and method/name headers, which
the SDK validates against the JSON-RPC body. No provider-spending operation is
retried under another protocol version.

## Rollout and safety

- Pin `mcp>=2.0.0,<3` and regenerate `uv.lock`.
- Keep stdio and legacy Streamable HTTP available for existing clients.
- Add in-process ASGI tests for a direct 2026-07-28 `tools/call`, header/body
  mismatch rejection, no `Mcp-Session-Id`, and a 2025 initialize/tools-list
  compatibility path.
- Run the full hermetic suite and a no-spend remote canary before production
  image promotion. Do not change the production container until those gates
  pass.

## Non-goals

- No provider, routing, spend, extraction, or authority API redesign.
- No custom protocol parser or duplicate JSON-RPC dispatcher.
- No removal of the 2025 compatibility path in this change.
