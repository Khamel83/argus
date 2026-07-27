"""Static security contract for release and CI workflows."""

import re
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
    assert "targets: homelab-ts" in text
    assert "DEPLOY_KNOWN_HOSTS" in text
    assert "DEPLOY_USER" not in text
    assert '"argus-deploy@homelab-ts"' in text
    assert "StrictHostKeyChecking=yes" in text
    assert "argus-deploy promote " in text
    assert ":latest" not in text
    assert "oci-" not in text
    assert "StrictHostKeyChecking=no" not in text
    assert text.count("docker/build-push-action@") == 1


def test_release_workflow_permissions_are_job_scoped_and_minimal():
    path = WORKFLOWS / "docker-publish.yml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert "permissions" not in payload
    assert payload["jobs"]["build"]["permissions"] == {
        "contents": "read",
        "packages": "write",
    }
    assert payload["jobs"]["promote"]["permissions"] == {"contents": "read"}
    assert payload["jobs"]["promote"]["if"] == (
        "github.ref == 'refs/heads/main'"
    )


def test_release_workflow_uses_the_canonical_lowercase_repository():
    payload = yaml.safe_load(
        (WORKFLOWS / "docker-publish.yml").read_text(encoding="utf-8")
    )

    assert payload["env"]["IMAGE_NAME"] == "khamel83/argus"
