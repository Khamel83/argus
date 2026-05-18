# Releasing Argus

The repository version and the published package version are separate until a release is published.

As of 2026-05-18:

- Repository metadata is prepared for `1.6.1`.
- PyPI `argus-search` is still published at `1.3.3`.
- GitHub tags on remote currently stop at `v1.5.0`.
- A GitHub release or manual workflow dispatch is required to publish PyPI and MCP Registry updates.

## Version files

Keep these in sync for every release:

- `pyproject.toml` — `[project].version`
- `argus/__init__.py` — `__version__`
- `argus/api/main.py` — FastAPI app version
- `server.json` — top-level `version`
- `server.json` — `packages[0].version`
- `uv.lock` — editable `argus-search` package version
- `CHANGELOG.md` — release section

CI checks `pyproject.toml` and both `server.json` versions. The other files are checked by tests and release review.

## Preflight

```bash
git status --short --branch
pytest -q
argus --version
python3 -m build
twine check dist/*
```

For MCP-specific release work, also run:

```bash
argus mcp init --global --client all
codex mcp list
claude mcp list
opencode mcp list --print-logs
```

## Publish

The publish workflow lives at `.github/workflows/publish.yml`. It runs on:

- GitHub release creation
- manual `workflow_dispatch`

The workflow builds the package, uploads to PyPI with `PYPI_API_TOKEN`, then publishes `server.json` to the MCP Registry using GitHub OIDC.

Recommended release flow:

```bash
git tag v1.6.1
git push origin v1.6.1
gh release create v1.6.1 --title "v1.6.1" --notes-file RELEASE_NOTES.md
```

After the workflow completes, verify:

```bash
python3 -m pip index versions argus-search
pipx upgrade argus-search
argus --version
```

Check:

- PyPI: https://pypi.org/project/argus-search/
- MCP Registry: https://registry.modelcontextprotocol.io/servers/io.github.Khamel83/argus

## If PyPI is behind

Do not assume a pushed `main` branch updates PyPI. PyPI updates only after the publish workflow succeeds.

If PyPI is behind the repo:

1. Confirm the intended release version.
2. Confirm `CHANGELOG.md` describes the release.
3. Run the preflight checks.
4. Create a GitHub release or manually dispatch the publish workflow.
5. Verify PyPI and MCP Registry after the workflow finishes.
