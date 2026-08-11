"""Static security contract for release and CI workflows."""

import json
import os
import re
import subprocess
import tomllib
from pathlib import Path

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

    assert "timeout-minutes: 50" in text
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
    assert payload["jobs"]["promote"]["timeout-minutes"] == 50
    assert payload["jobs"]["promote"]["if"] == (
        "github.ref == 'refs/heads/main'"
    )


def test_release_workflow_uses_the_canonical_lowercase_repository():
    payload = yaml.safe_load(
        (WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8")
    )

    assert payload["env"]["IMAGE_NAME"] == "khamel83/argus"


def test_release_identity_is_1_6_4_in_user_visible_locations():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    package_text = (ROOT / "argus/__init__.py").read_text(encoding="utf-8")
    api_text = (ROOT / "argus/api/main.py").read_text(encoding="utf-8")

    assert pyproject["project"]["version"] == "1.6.4"
    assert server["version"] == "1.6.4"
    assert server["packages"][0]["version"] == "1.6.4"
    assert re.search(r'__version__\s*=\s*["\']1\.6\.4["\']', package_text)
    assert re.search(r'version=["\']1\.6\.4["\']', api_text)
