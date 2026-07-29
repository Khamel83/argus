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

## Immutable homelab promotion

The complete operator procedure and recovery checkpoints live in the
[canonical production operations guide](operations.md).

The homelab runs the **container image**, not the PyPI package. A push to
`main`, a `v*` tag, or a manual dispatch builds one
`ghcr.io/khamel83/argus:sha-<commit>` image. Buildx publishes OCI provenance
and SBOM attestations. The workflow records the returned manifest digest and
passes that exact digest to the homelab promoter; the production candidate is
never rebuilt or resolved through a mutable tag.

Only a `main`-branch run may enter the protected production environment. Tag
runs build and retain immutable release evidence but do not deploy.

Configure a protected GitHub environment named `production` with required
reviewer protection and these secrets:

- `TS_OAUTH_CLIENT_ID`
- `TS_OAUTH_SECRET`
- `DEPLOY_SSH_PRIVATE_KEY`
- `DEPLOY_KNOWN_HOSTS`

The Tailscale OAuth client may create only `tag:argus-deployer` nodes. Tailnet
ACLs allow that tag to reach only TCP/22 on `homelab-ts`. The SSH public key is
installed with the forced command printed by the Homelab repository's
`sudo scripts/install-argus-promoter.sh --public-key-file <path>` command.
Actions verifies the pinned host key from `DEPLOY_KNOWN_HOSTS`; it does not use
an OCI jump host or disable host-key verification.

Trigger a build and promotion request manually:

```bash
gh workflow run docker-publish.yml --ref main
```

The request remains provisional until the isolated candidate gates, production
digest and runtime-identity checks, and a 1,800-second soak pass. A blocking
post-cutover failure restores the recorded previous compatible digest and
reruns the production proof. Requested, current, previous, known-good, and
timestamped receipts live outside Argus containers under
`/mnt/fast-storage/appdata/hestia-repo-state/status/argus-promotion`.

**Caution — `[skip ci]` skips deploys.** A commit whose message contains
`[skip ci]` skips *all* workflows, including build/publish/deploy. Reserve it for
non-deployable commits (e.g. docs-only). A stretch of deployable merges tagged
`[skip ci]` silently leaves the homelab on a stale image (this happened for ~12
commits before 2026-07-23).
