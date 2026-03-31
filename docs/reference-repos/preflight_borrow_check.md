# Argus Pre-Flight Borrow Check

## Purpose

Before building deeper into Argus, inspect existing repos and borrow what is already solved well.

This is not a repo-replacement exercise.

The goal is:
- reuse good patterns
- avoid inheriting the wrong architecture
- keep Argus as its own standalone search broker service

## What Argus is

Argus is:
- a standalone reusable search broker
- HTTP API first
- self-hosted/free-first in routing policy
- centered on provider adapters, broker logic, caching, ranking, health, and budget tracking
- optionally exposed through MCP
- not a plugin, not a UI-first product, not just a thin MCP wrapper

## Repos inspected

### 1. web-search-plus
- **Notes**: `docs/reference-repos/web-search-plus.md`
- Focus: provider abstraction, normalization, env handling, fallback structure

### 2. kindly-web-search-mcp-server
- **Notes**: `docs/reference-repos/kindly-web-search-mcp-server.md`
- Focus: MCP tool exposure patterns, coding-agent integration

### 3. Voy
- **Notes**: `docs/reference-repos/voy.md`
- Focus: HTTP API, health endpoints, deployment posture

### 4. SearXNG
- **Notes**: `docs/reference-repos/searxng.md`
- Focus: upstream local provider floor only

## Required conclusion after inspection

Argus must remain:
- a standalone broker service
- with one normalized internal result model
- with config-driven provider routing
- with persistent provider health and budget state
- with HTTP API as the main external interface
- with optional MCP wrapper on top

If any borrowed pattern conflicts with that, discard it.
