# Contributing to Argus

Argus development is standardized on Python 3.12. The published package still supports Python 3.11+, but local repo work, CI parity, and release verification should use the commands below.

## Quick Setup

```bash
git clone https://github.com/Khamel83/argus.git && cd argus
uv sync --python 3.12 --extra dev --extra mcp
cp .env.example .env  # configure at least one provider key
uv run pytest tests/ -v --tb=short
```

## Development

- Bootstrap or refresh the dev environment: `uv sync --python 3.12 --extra dev --extra mcp`
- Run the canonical verification command: `uv run pytest tests/ -v --tb=short`
- Run a targeted test file: `uv run pytest tests/test_api.py -v --tb=short`
- All config via env vars — see `.env.example` for what's available
- Provider adapters live in `argus/providers/` and implement `BaseProvider`

If you do not already have `uv`, install it from <https://docs.astral.sh/uv/> and re-run the commands above. The repo also includes `.python-version` so `uv`, `pyenv`, and similar tools converge on Python 3.12 by default.

## Pull Requests

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes, make sure tests pass: `uv run pytest tests/ -v --tb=short`
4. Push and open a PR

One change per PR makes review easier. If it's two logically separate things, it's probably two PRs.

## Adding a Search Provider

1. Create `argus/providers/yourprovider.py` implementing `BaseProvider`
2. Add a `ProviderName` enum entry in `argus/models.py`
3. Wire it into `create_broker()` in `argus/broker/router.py`
4. Add config entries in `argus/config.py` and `.env.example`
5. Add tests in `tests/test_providers.py`
6. Add to routing policies in `argus/broker/policies.py` and budget tiers in `argus/broker/budgets.py`

The DuckDuckGo provider is a good reference — it's simple and doesn't need an API key. See [docs/providers.md](docs/providers.md) for the full provider and extractor reference.

## Testing the MCP server locally

```bash
# Stdio adapter (default — what most clients use)
ARGUS_AUTHORITY_URL=http://127.0.0.1:8000 \
ARGUS_AUTHORITY_TOKEN=test-key argus mcp serve

# Streamable HTTP (remote-style)
ARGUS_API_KEY=test-key \
ARGUS_AUTHORITY_URL=http://127.0.0.1:8000 \
ARGUS_AUTHORITY_TOKEN=test-key \
argus mcp serve --transport streamable-http --port 8001
curl -H "Authorization: Bearer test-key" http://127.0.0.1:8001/mcp

# Explicit standalone development compatibility
ARGUS_MCP_STANDALONE=true argus mcp serve
```

`argus mcp check` validates that the config Argus would write is reachable
from the client's perspective.

## Releasing

Releases are cut from `main` via the GitHub publish workflow — see
[docs/releasing.md](docs/releasing.md) for the version sync, preflight, and
publish-verify steps. Do **not** bump `pyproject.toml` or `server.json` as part
of an unrelated PR.

## Troubleshooting

If you hit something unexpected during local development, check
[docs/troubleshooting.md](docs/troubleshooting.md) before filing an issue.
