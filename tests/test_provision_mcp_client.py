import os
import json
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "provision-mcp-client.sh"


def test_provisioning_refuses_remote_fallback_to_localhost(tmp_path):
    env = os.environ | {
        "HOME": str(tmp_path),
        "ARGUS_API_KEY": "test-token",
        "ARGUS_LOCAL_COMMAND": str(tmp_path / "missing-argus"),
    }
    env.pop("ARGUS_REMOTE_URL", None)

    result = subprocess.run(
        ["bash", str(SCRIPT), "local"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "ARGUS_REMOTE_URL is required" in result.stderr
    assert not (tmp_path / ".claude.json").exists()


def test_provisioning_uses_the_explicit_remote_https_endpoint(tmp_path):
    endpoint = "https://homelab.deer-panga.ts.net:8443"
    env = os.environ | {
        "HOME": str(tmp_path),
        "ARGUS_API_KEY": "test-token",
        "ARGUS_REMOTE_URL": endpoint,
        "ARGUS_LOCAL_COMMAND": str(tmp_path / "missing-argus"),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), "local"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert config["mcpServers"]["argus"]["url"] == endpoint + "/mcp"


def test_explicit_remote_url_wins_over_an_executable_local_adapter(tmp_path):
    endpoint = "https://homelab.deer-panga.ts.net:8443"
    local_command = tmp_path / "argus"
    local_command.write_text("#!/usr/bin/env bash\nexit 0\n")
    local_command.chmod(0o755)
    env = os.environ | {
        "HOME": str(tmp_path),
        "ARGUS_API_KEY": "test-token",
        "ARGUS_REMOTE_URL": endpoint,
        "ARGUS_LOCAL_COMMAND": str(local_command),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), "local"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert config["mcpServers"]["argus"]["url"] == endpoint + "/mcp"


def test_provisioning_does_not_duplicate_an_existing_mcp_path(tmp_path):
    endpoint = "https://homelab.deer-panga.ts.net:8443/mcp"
    env = os.environ | {
        "HOME": str(tmp_path),
        "ARGUS_API_KEY": "test-token",
        "ARGUS_REMOTE_URL": endpoint,
        "ARGUS_LOCAL_COMMAND": str(tmp_path / "missing-argus"),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), "local"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert config["mcpServers"]["argus"]["url"] == endpoint


def test_executable_local_adapter_uses_stdio_when_no_remote_url_is_set(tmp_path):
    local_command = tmp_path / "argus"
    local_command.write_text("#!/usr/bin/env bash\nexit 0\n")
    local_command.chmod(0o755)
    env = os.environ | {
        "HOME": str(tmp_path),
        "ARGUS_LOCAL_COMMAND": str(local_command),
    }
    env.pop("ARGUS_API_KEY", None)
    env.pop("ARGUS_REMOTE_URL", None)

    result = subprocess.run(
        ["bash", str(SCRIPT), "local"],
        cwd=REPO_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    config = json.loads((tmp_path / ".claude.json").read_text())
    assert config["mcpServers"]["argus"]["command"] == str(local_command)
