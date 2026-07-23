"""Static production-container contract checks that do not require local Docker."""

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).parents[1]


def test_production_image_uses_matched_digest_pinned_playwright_base():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert dockerfile.count(
        "mcr.microsoft.com/playwright/python:v1.58.0-noble"
        "@sha256:678457c4c323b981d8b4befc57b95366bb1bb6aa30057b1269f6b171e8d9975a"
    ) == 2
    assert "PLAYWRIGHT_BROWSERS_PATH=/ms-playwright" in dockerfile
    assert "USER argus" in dockerfile


def test_compose_bounds_browser_resources_and_enables_sandbox_profile():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    service = compose["services"]["argus"]

    assert service["mem_limit"] == "1g"
    assert service["memswap_limit"] == "1g"
    assert service["pids_limit"] == 256
    assert service["shm_size"] == "256m"
    assert service["init"] is True
    assert "no-new-privileges:true" in service["security_opt"]
    assert "seccomp=./docker/playwright-seccomp.json" in service["security_opt"]
    assert any(
        mount.startswith("/tmp:") and "size=256m" in mount
        for mount in service["tmpfs"]
    )


def test_seccomp_profile_only_adds_user_namespace_syscalls():
    profile = json.loads(
        (ROOT / "docker" / "playwright-seccomp.json").read_text(encoding="utf-8")
    )
    added = {
        name
        for rule in profile["syscalls"]
        if rule["action"] == "SCMP_ACT_ALLOW"
        for name in rule["names"]
    }

    assert {"clone", "setns", "unshare"} <= added
