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

The DuckDuckGo provider is a good reference — it's simple and doesn't need an API key.
