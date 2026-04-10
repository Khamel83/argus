# Contributing to Argus

Thanks for taking a look. Here's how to get started.

## Quick Setup

```bash
git clone https://github.com/Khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"
cp .env.example .env  # configure at least one provider key
pytest
```

## Development

- `pytest tests/` to run the test suite
- All config via env vars — see `.env.example` for what's available
- Provider adapters live in `argus/providers/` and implement `BaseProvider`

## Pull Requests

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes, make sure tests pass: `pytest`
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
