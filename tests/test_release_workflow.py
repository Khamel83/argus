"""Static security contract for release and CI workflows."""

import json
import os
import re
import shlex
import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github/workflows"
FULL_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _workflow_paths() -> list[Path]:
    return sorted((*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")))


def test_all_third_party_actions_are_pinned_to_full_commits():
    action_refs: list[tuple[Path, str]] = []
    for path in _workflow_paths():
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        for job in (payload.get("jobs") or {}).values():
            for step in job.get("steps") or []:
                if "uses" in step:
                    action_refs.append((path, step["uses"]))

    assert action_refs
    for path, action_ref in action_refs:
        assert FULL_SHA.fullmatch(action_ref), (path, action_ref)


def test_release_workflow_builds_once_and_submits_a_hardened_request():
    text = (WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8")

    assert "environment: production" in text
    assert "group: argus-production" in text
    assert "cancel-in-progress: false" in text
    assert "provenance: mode=max" in text
    assert "sbom: true" in text
    assert (
        "tags: ${{ env.REGISTRY }}/${{ env.IMAGE_NAME }}:"
        "sha-${{ steps.identity.outputs.source_revision }}"
    ) in text
    assert (
        "tailscale/github-action@"
        "780049a30b6ff5c378a9e7b389d15ece7a204888 # v4.1.3"
    ) in text
    assert "tags: tag:argus-deployer" in text
    assert "ping: homelab" in text
    assert "targets:" not in text
    assert "DEPLOY_KNOWN_HOSTS" in text
    assert "DEPLOY_USER" not in text
    assert "HostKeyAlias=homelab-ts" in text
    assert '"argus-deploy@homelab.deer-panga.ts.net"' in text
    assert "StrictHostKeyChecking=yes" in text
    assert "argus-deploy promote " in text
    assert ":latest" not in text
    assert "oci-" not in text
    assert "StrictHostKeyChecking=no" not in text
    assert text.count("docker/build-push-action@") == 1


def test_release_workflow_keeps_the_synchronous_promotion_session_alive():
    text = (WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8")

    assert "timeout-minutes: 120" in text
    assert "ServerAliveInterval=30" in text
    assert "ServerAliveCountMax=10" in text
    assert "TCPKeepAlive=yes" in text
    assert "for attempt in {1..60}" in text
    assert "transport_failures=0" in text
    assert 'if [[ "$transport_failures" -ge 5 ]]' in text
    assert 'exit "$promotion_status"' in text


def test_release_workflow_retry_loop_is_bounded_and_fail_closed(tmp_path):
    payload = yaml.safe_load(
        (WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8")
    )
    submit = next(
        step["run"]
        for step in payload["jobs"]["promote"]["steps"]
        if step.get("name") == "Submit forced-command promotion"
    )
    retry_loop = submit[submit.index("promotion_status=255") :]
    fake_ssh = r'''
read -r -a fake_statuses <<<"$FAKE_SSH_STATUSES"
fake_index=0
ssh() {
  local status="${fake_statuses[$fake_index]}"
  printf '%s\n' "$status" >>"$FAKE_SSH_CALLS"
  if [[ "$fake_index" -lt "$((${#fake_statuses[@]} - 1))" ]]; then
    fake_index=$((fake_index + 1))
  fi
  return "$status"
}
sleep() { :; }
'''

    cases = (
        ("255 75 0", 0, 3),
        ("2", 2, 1),
        ("255", 255, 5),
    )
    for index, (statuses, expected_status, expected_calls) in enumerate(cases):
        calls = tmp_path / f"calls-{index}"
        result = subprocess.run(
            ["/bin/bash", "-c", f"{fake_ssh}\n{retry_loop}"],
            env={
                **os.environ,
                "FAKE_SSH_CALLS": str(calls),
                "FAKE_SSH_STATUSES": statuses,
                "IMAGE_REF": "ghcr.io/khamel83/argus@sha256:" + "a" * 64,
                "RECEIPT_SHA256": "b" * 64,
                "RUNNER_TEMP": str(tmp_path),
                "SOURCE_REVISION": "c" * 40,
            },
            check=False,
        )

        assert result.returncode == expected_status
        assert len(calls.read_text(encoding="utf-8").splitlines()) == expected_calls


def test_release_workflow_permissions_are_job_scoped_and_minimal():
    path = WORKFLOWS / "docker-publish.yml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert "permissions" not in payload
    assert payload["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    assert payload["jobs"]["promote"]["permissions"] == {"contents": "read"}
    assert payload["jobs"]["promote"]["timeout-minutes"] == 120
    assert payload["jobs"]["promote"]["if"] == (
        "github.ref == 'refs/heads/main'"
    )


def test_release_workflow_uses_the_canonical_lowercase_repository():
    payload = yaml.safe_load(
        (WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8")
    )

    assert payload["env"]["IMAGE_NAME"] == "khamel83/argus"


def test_release_workflow_extracts_and_binds_all_immutable_release_inputs():
    text = (WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8")

    assert "docker pull \"$IMAGE_REF\"" in text
    assert "runtime-manifest.json" in text
    assert "release_descriptor.json" in text
    assert "schema-contract.json" in text
    assert "--runtime-manifest runtime-manifest.json" in text
    assert "--release-descriptor release_descriptor.json" in text
    assert "--schema-contract schema-contract.json" in text
    assert "docker cp" in text


def test_package_publication_is_fail_closed():
    text = (WORKFLOWS / "publish.yml").read_text(encoding="utf-8")

    assert "twine upload dist/*" in text
    assert "twine upload dist/* || true" not in text
    assert "Verify release contract" in text


def test_release_identity_is_1_6_5_in_user_visible_locations():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    package_text = (ROOT / "argus/__init__.py").read_text(encoding="utf-8")
    api_text = (ROOT / "argus/api/main.py").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == "1.6.5"
    assert server["version"] == "1.6.5"
    assert server["packages"][0]["version"] == "1.6.5"
    assert re.search(r'__version__\s*=\s*["\']1\.6\.5["\']', package_text)
    assert re.search(r'version=["\']1\.6\.5["\']', api_text)


def _github_path_pattern(pattern: str) -> re.Pattern[str]:
    # GitHub filter globs: ** crosses directories, * stays inside one.
    out = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**", index):
            out.append(".*")
            index += 2
        elif pattern[index] == "*":
            out.append("[^/]*")
            index += 1
        else:
            out.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(out) + r"\Z")


def _push_runs_for(patterns: list[str], changed: list[str]) -> bool:
    # A push runs when any changed path is included by the last pattern it matches.
    for path in changed:
        included = False
        for pattern in patterns:
            negated = pattern.startswith("!")
            if _github_path_pattern(pattern.lstrip("!")).match(path):
                included = not negated
        if included:
            return True
    return False


def test_release_workflow_skips_documentation_only_pushes():
    payload = yaml.safe_load((WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8"))
    push = payload[True]["push"]
    patterns = push["paths"]

    assert push["branches"] == ["main"]
    assert push["tags"] == ["v*"]
    assert "workflow_dispatch" in payload[True]

    for docs_only in (
        ["docs/research/g2k-issue-249.md"],
        ["TODO.md", "CONTEXT.md", "HANDOFF.md"],
        ["thoughts/shared/plan.md", "AGENTS.md", "CLAUDE.md"],
        ["docs/README.md"],
    ):
        assert not _push_runs_for(patterns, docs_only), docs_only

    for releasing in (
        ["README.md"],
        ["argus/api/main.py"],
        ["TODO.md", "argus/config.py"],
        ["Dockerfile"],
        ["pyproject.toml"],
        ["uv.lock"],
        [".github/workflows/docker-publish.yml"],
        ["tests/test_release_workflow.py"],
        ["deploy/argus.service"],
    ):
        assert _push_runs_for(patterns, releasing), releasing

    # Every file the image copies from the source tree still releases.
    sources = _dockerfile_context_sources(ROOT / "Dockerfile")
    assert "README.md" in sources
    assert "migrations/" in sources
    for source in sources:
        files = _context_files(source)
        assert files, source
        for path in files:
            assert _push_runs_for(patterns, [path]), (source, path)


HEREDOC_WORD = re.compile(r"\d*<<(-?)([^<]+)")
CONTINUATION = re.compile(r"\\[ \t]*$")


def _dockerfile_context_sources(dockerfile: Path) -> list[str]:
    """Build-context sources the Dockerfile reads.

    Covers COPY and ADD without --from (also under ONBUILD) and RUN bind
    mounts without from=, and skips flags such as --chown. Lines are read the
    way BuildKit reads them: a comment line (first non-blank character `#`)
    is dropped, also inside a continued instruction, and never continues; a
    backslash followed only by spaces or tabs continues the instruction.
    Heredocs are recognised only as a whole word of a COPY, ADD or RUN
    instruction, and their bodies are skipped up to the exact terminator line.
    A heredoc without a terminator fails the check rather than hiding the
    rest of the file, and so does a non-default escape directive, which
    would change the continuation character.
    """
    lines = dockerfile.read_text(encoding="utf-8").splitlines()
    for line in lines:
        directive = re.match(r"\s*#\s*escape\s*=\s*(\S*)", line, re.IGNORECASE)
        if directive:
            assert directive.group(1) == "\\", f"unsupported Dockerfile escape directive: {line.strip()}"
            continue
        if not re.match(r"\s*#\s*\w+\s*=", line):
            break

    def is_comment(line: str) -> bool:
        return line.lstrip().startswith("#")

    sources: list[str] = []
    index = 0
    while index < len(lines):
        instruction = lines[index]
        index += 1
        if is_comment(instruction) or not instruction.strip():
            continue
        while CONTINUATION.search(instruction):
            instruction = CONTINUATION.sub("", instruction)
            while index < len(lines) and (is_comment(lines[index]) or not lines[index].strip()):
                index += 1
            if index == len(lines):
                break
            instruction += lines[index]
            index += 1
        parts = instruction.split()
        if parts and parts[0].upper() in {"COPY", "ADD", "RUN"}:
            try:
                words = shlex.split(instruction)
            except ValueError:
                words = parts
            for word in words[1:]:
                heredoc = HEREDOC_WORD.fullmatch(word)
                if not heredoc:
                    continue
                chomp, name = heredoc.groups()
                while True:
                    assert index < len(lines), f"unterminated Dockerfile heredoc {name}"
                    body = lines[index]
                    index += 1
                    if (body.lstrip("\t") if chomp else body) == name:
                        break
            parts = [part for part in parts if not HEREDOC_WORD.fullmatch(part)]
        if parts and parts[0].upper() == "ONBUILD":
            parts = parts[1:]
        if parts and parts[0].upper() == "RUN":
            sources.extend(_bind_mount_sources(parts[1:]))
            continue
        if not parts or parts[0].upper() not in {"COPY", "ADD"}:
            continue
        args = parts[1:]
        if any(arg.startswith("--from") for arg in args):
            continue
        operands = [arg for arg in args if not arg.startswith("--")]
        if operands[:1] and operands[0].startswith("["):
            operands = json.loads(" ".join(operands))
        sources.extend(operands[:-1])
    return sources


def _bind_mount_sources(args: list[str]) -> list[str]:
    """Context sources of RUN --mount=type=bind flags that have no from=."""
    sources: list[str] = []
    for arg in args:
        if not arg.startswith("--mount="):
            if not arg.startswith("--"):
                break
            continue
        options = dict(
            item.split("=", 1) if "=" in item else (item, "")
            for item in arg[len("--mount="):].split(",")
        )
        if options.get("type", "bind") != "bind" or "from" in options:
            continue
        sources.append(options.get("source", options.get("src", ".")))
    return sources


def _context_files(source: str) -> list[str]:
    """Repository files a COPY/ADD source puts into the image.

    A directory or "." expands to every file under it (excluding .git), and a
    glob expands to its matches, so copying docs/ or the whole context fails
    the docs-only check instead of passing it vacuously.
    """
    files: list[str] = []
    pattern = source.strip("/")
    while pattern.startswith("./"):
        pattern = pattern[2:]
    matches = [ROOT] if pattern in {"", "."} else sorted(ROOT.glob(pattern))
    for match in matches:
        candidates = match.rglob("*") if match.is_dir() else [match]
        for path in candidates:
            relative = path.relative_to(ROOT)
            if path.is_file() and ".git" not in relative.parts:
                files.append(relative.as_posix())
    return files


def test_docs_only_guard_refuses_a_non_default_escape_directive(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("# syntax=docker/dockerfile:1\n# escape=`\nFROM python\nCOPY a `\n  docs/ ./\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="escape directive"):
        _dockerfile_context_sources(dockerfile)
    dockerfile.write_text("# escape=\\\nFROM python\nCOPY a \\\n  docs/ ./\n", encoding="utf-8")
    assert _dockerfile_context_sources(dockerfile) == ["a", "docs/"]
    dockerfile.write_text("FROM python\n# escape=`\nCOPY a b\n", encoding="utf-8")
    assert _dockerfile_context_sources(dockerfile) == ["a"]


def test_docs_only_guard_reads_lines_like_buildkit(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python\n"
        "# a comment that mentions <<EOF is not a heredoc\n"
        "COPY docs/a.md /a\n"
        "RUN echo $((x<<END)) && x=1<<2\n"
        "COPY thoughts/ /t/\n"
        "# a comment ending in a backslash does not continue \\\n"
        "COPY notes.md /n\n"
        "COPY b \\  \t\n"
        "  docs/c.md ./\n"
        "COPY d \\\n"
        "# a comment inside a continuation is dropped\n"
        "\n"
        "  docs/e.md ./\n"
        "END\n"
        "EOF\n",
        encoding="utf-8",
    )
    assert _dockerfile_context_sources(dockerfile) == [
        "docs/a.md",
        "thoughts/",
        "notes.md",
        "b",
        "docs/c.md",
        "d",
        "docs/e.md",
    ]


def test_docs_only_guard_fails_on_an_unterminated_heredoc(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM python\nCOPY <<EOF /x\nCOPY docs/ ./\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="unterminated Dockerfile heredoc EOF"):
        _dockerfile_context_sources(dockerfile)
    # A chomped heredoc ends at its terminator after leading tabs; a plain one
    # needs the exact line.
    dockerfile.write_text("FROM python\nRUN <<-EOF\n\tcat x\n\tEOF\nCOPY docs/ ./\n", encoding="utf-8")
    assert _dockerfile_context_sources(dockerfile) == ["docs/"]
    dockerfile.write_text("FROM python\nRUN <<EOF\n\tEOF\nCOPY docs/ ./\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="unterminated"):
        _dockerfile_context_sources(dockerfile)


def test_docs_only_guard_sees_every_copy_form(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM python AS builder\n"
        "COPY --chown=app:app pyproject.toml \\\n"
        "     uv.lock ./\n"
        "ADD docs/ ./docs/\n"
        'COPY ["alembic.ini", "./"]\n'
        "COPY --from=builder /app /app\n"
        "COPY . .\n"
        "ONBUILD COPY thoughts/ ./thoughts/\n"
        "RUN --mount=type=bind,source=docs/guide.md,target=/g --mount=type=cache,target=/c \\\n"
        "    --mount=type=bind,from=builder,source=/app,target=/a cat /g\n"
        "RUN --mount=target=/ctx ls /ctx\n"
        "COPY <<EOF /etc/note\n"
        "COPY docs/ignored.md /never\n"
        "EOF\n"
        "COPY <<-'ONE' <<TWO /tmp/\n"
        "a\n"
        "ONE\n"
        "b\n"
        "TWO\n",
        encoding="utf-8",
    )
    sources = _dockerfile_context_sources(dockerfile)
    assert sources == [
        "pyproject.toml",
        "uv.lock",
        "docs/",
        "alembic.ini",
        ".",
        "thoughts/",
        "docs/guide.md",
        ".",
    ]

    patterns = yaml.safe_load((WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8"))[True]["push"]["paths"]
    # Copying docs/ or the whole context would put docs in the image, so the
    # guard must report a docs path that would not release.
    for source in ("docs/", "."):
        skipped = [path for path in _context_files(source) if not _push_runs_for(patterns, [path])]
        assert any(path.startswith("docs/") for path in skipped), source
