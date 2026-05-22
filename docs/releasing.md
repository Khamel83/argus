# Releasing Argus

The repository version and the published package version are separate until a release is published.

As of 2026-05-22:

- Repository metadata is `1.6.2`.
- PyPI `argus-search` is published at `1.6.1` (latest release).
- GitHub release `v1.6.1` has been created.
- MCP Registry publication for `v1.6.1` completed through the publish workflow.

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
git tag v1.6.2
git push origin v1.6.2
gh release create v1.6.2 --title "v1.6.2" --notes-file RELEASE_NOTES.md
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

The `v1.6.1` publish run completed successfully at:

https://github.com/Khamel83/argus/actions/runs/26066266300

## If PyPI is behind

Do not assume a pushed `main` branch updates PyPI. PyPI updates only after the publish workflow succeeds.

If PyPI is behind the repo:

1. Confirm the intended release version.
2. Confirm `CHANGELOG.md` describes the release.
3. Run the preflight checks.
4. Create a GitHub release or manually dispatch the publish workflow.
5. Verify PyPI and MCP Registry after the workflow finishes.
