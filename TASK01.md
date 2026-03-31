# TASK01 — Bootstrap Argus core skeleton and config

## Goal

Create the initial Argus project skeleton and configuration foundation.

## Scope

In this task, do not implement full provider logic yet. Build the project structure, configuration, models, and operational scaffolding so later provider and broker work has a clean home.

## Deliverables

Create:

- `argus/__init__.py`
- `argus/config.py`
- `argus/models.py`
- `argus/logging.py`

Directories:
- `argus/providers/`
- `argus/broker/`
- `argus/persistence/`
- `argus/api/`
- `argus/cli/`
- `argus/mcp/`
- `tests/`
- `docs/`

Project files:
- `pyproject.toml`
- `.env.example`
- `README.md`
- `IMPLEMENTATION_CONTEXT.md`

## Requirements

### Configuration
Implement typed config loading from environment variables.

Must support:
- DB URL
- provider enable/disable flags
- provider API keys
- provider monthly budgets
- cache TTL
- provider failure thresholds
- provider cooldown minutes
- SearXNG base URL
- logging level
- optional MCP toggle

### Models
Add normalized domain models for:
- `SearchMode`
- `ProviderName`
- `ProviderStatus`
- `SearchQuery`
- `SearchResult`
- `ProviderTrace`
- `SearchResponse`

### Logging
Provide centralized logging setup.

Requirements:
- structured enough to be useful
- no noisy full payload dumps by default
- easy to reuse across API, CLI, and broker code

## Constraints

- Python only
- cleanly importable package
- suitable for VPS deployment
- maintainable by one person
- no frontend
- no auth layer in v1

## Acceptance criteria

- project installs
- config loads
- enums/models exist
- tests can import package
- `.env.example` is aligned to config
- structure is ready for providers and broker
