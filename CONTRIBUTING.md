# Contributing to Argus

## Quick Setup

```bash
git clone https://github.com/Khamel83/argus.git && cd argus
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,mcp]"
cp .env.example .env  # configure at least one provider key
pytest
```

## Development

- Run tests: `pytest tests/`
- All config via env vars (see `.env.example`)
- Provider adapters live in `argus/providers/` and must implement `BaseProvider`

## Pull Requests

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes, ensure tests pass: `pytest`
4. Push and open a PR

Keep PRs focused. One change per PR makes review easier.

## Adding a Provider

1. Create `argus/providers/yourprovider.py` implementing `BaseProvider`
2. Register in `argus/broker/router.py` `create_broker()`
3. Add config entries in `argus/config.py` and `.env.example`
4. Add tests in `tests/test_providers.py`
5. Add to routing policies in `argus/broker/policies.py`
