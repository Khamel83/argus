# Contributing to Argus

Argus development uses Python 3.12.13 through mise 2026.10.2 and uv 0.11.26.
The published package still supports Python 3.11+; CI retains separate 3.11
and 3.13 compatibility lanes. This setup wraps the existing uv environment.

## Quick Setup

```bash
git clone https://github.com/Khamel83/argus.git && cd argus
mise trust mise.toml
mise install
mise run env-sync
mise run test
```

## Development

- Bootstrap or refresh this checkout's `.venv`: `mise run env-sync`
- Run the canonical verification command: `mise run test`
- Run a targeted test file: `mise exec -- uv run --frozen pytest tests/test_api.py -v --tb=short`
- Check dependency-lock parity: `mise run lock-check`
- List the existing verification wrappers: `mise tasks`
- All config via env vars — see `.env.example` for what's available
- Provider adapters live in `argus/providers/` and implement `BaseProvider`

Install mise using its [official instructions](https://mise.jdx.dev/getting-started.html).
Keep the existing uv installation at 0.11.26; [uv's installation guide](https://docs.astral.sh/uv/getting-started/installation/)
describes supported installers. `env-sync` refuses a different uv version.
`UV_PYTHON` comes from the selected mise tool path, including CI overrides,
and `UV_PYTHON_DOWNLOADS=never` prevents uv from selecting another runtime.
The existing `.python-version` remains the 3.12 fallback for direct uv users.
Source tests use hermetic configuration and need no provider credentials.

`mise.lock` records Python download metadata for Linux arm64/x64 and Mac arm64.
Refresh it with `mise lock --platform linux-arm64,linux-x64,macos-arm64`;
platform metadata does not prove execution. `uv.lock` remains the existing
universal dependency lock: use `uv lock --check`, not `--python-platform`.
CI overrides `MISE_PYTHON_VERSION` at job scope for the compatibility lanes,
so installation, task execution and uv all use that lane's actual interpreter.

There are no existing lint, typecheck, format or clean targets to wrap. Those
governance decisions remain open in TODO.md; this change adds no substitute
tools or successful no-op tasks. To roll back development setup, return to the
previous source and its retained environment; existing primary environments
and production runtimes are preserved.

## Pull Requests

1. Fork the repo
2. Create a feature branch: `git checkout -b feature/my-feature`
3. Make your changes, make sure tests pass: `mise run test`
4. Push and open a PR

One change per PR makes review easier. If it's two logically separate things, it's probably two PRs.

## Adding a Search Provider

1. Create `argus/providers/yourprovider.py` implementing `BaseProvider`
2. Add a `ProviderName` enum entry in `argus/models.py`
3. Add the exact module and adapter class to `CANONICAL_ADAPTERS` in
   `argus/providers/fixture_registry.py`
4. Add a hand-curated entry to
   `argus/providers/fixture_golden_contracts.py` with the exact outbound
   transport request, raw success/empty/error/malformed fixtures, normalized
   outcomes, provider contract version, and privacy expectations. This file is
   review-owned input and is never written by the attestation generator.
5. Add only the matching request, response, provider-contract, and error
   declaration metadata to
   `argus/providers/fixture_contracts.json`
6. Ensure `argus/providers/fixture_harness.py` can execute the real adapter's
   `search()` method against the curated transport for success, empty, error,
   malformed, and private-query cases.
7. Regenerate and verify the checked content-addressed artifact:

   ```bash
   PACKAGE_VERSION="$(uv run python -c 'import argus; print(argus.__version__)')"
   uv run python scripts/generate_provider_fixture_attestations.py \
     --release-revision "argus-${PACKAGE_VERSION}"
   uv run python scripts/generate_provider_fixture_attestations.py \
     --release-revision "argus-${PACKAGE_VERSION}" --check
   ```

   Commit `argus/providers/fixture_attestations.json` with the adapter and
   harness changes. Regenerate it for every package release. Runtime startup
   only loads and verifies the exact release and provider-contract artifact; it
   does not regenerate it.
8. Wire the provider into `create_broker()` in `argus/broker/router.py`
9. Add config entries in `argus/config.py` and `.env.example`
10. Add adapter and failure-shape tests in `tests/test_providers.py`, plus the
   canonical fixture-attestation cases in `tests/test_provider_readiness.py`
11. Add the provider to routing policies in `argus/broker/policies.py` and
    canonical budget tiers in `argus/provider_policy.py`

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
