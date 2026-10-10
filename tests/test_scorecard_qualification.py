"""Disposable scorecard qualification harness contracts."""

from __future__ import annotations

import json
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from argus.scorecard import qualification as qualification_module
from argus.scorecard.qualification import QualificationConfig, run_qualification


def _python(code: str, *args: str) -> tuple[str, ...]:
    return (sys.executable, "-c", code, *args)


def _pressure_command() -> tuple[str, ...]:
    return _python(
        "import json,sys; print(json.dumps({'available': True, 'docker_containers': 2, "
        "'docker_networks': 1, 'interfaces': 3, 'veth_interfaces': 1, 'phase': sys.argv[1]}))",
        "{phase}",
    )


def _scorecard_command() -> tuple[str, ...]:
    return _python(
        "import json,os,pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.mkdir(parents=True, exist_ok=True); "
        "(p/'resources.json').write_text(json.dumps({'containers': [], 'networks': [], 'leases': [], 'queue_entries': []})); "
        "(p/'env.json').write_text(json.dumps({'disposable': os.environ.get('ARGUS_QUALIFICATION_DISPOSABLE'), "
        "'authority': os.environ.get('ARGUS_AUTHORITY_URL')})); time.sleep(0.08)",
        "{output}",
    )


def _cleanup_command() -> tuple[str, ...]:
    return _python(
        "import json; print(json.dumps({'clean': True, 'containers': 0, 'networks': 0, 'leases': 0, 'queue_entries': 0}))"
    )


def _config(output: Path, evidence: dict[str, tuple[str, ...]]) -> QualificationConfig:
    command = _scorecard_command()
    return QualificationConfig(
        output=output,
        baseline_command=command,
        candidate_command=command,
        evidence_commands=evidence,
        pressure_command=_pressure_command(),
        cleanup_command=_cleanup_command(),
        scorecard_timeout=5,
        scorecard_concurrency=1,
        pressure_interval=0.25,
    )


def test_qualification_runs_real_scorecard_commands_and_records_queue_pressure(tmp_path):
    evidence = {
        name: _python("import json,time; time.sleep(0.03); print(json.dumps({'status': 'ok'}))")
        for name in ("docker", "hermes", "homelab")
    }

    report = run_qualification(_config(tmp_path / "qualification", evidence))

    assert report["status"] == "pass"
    assert report["checks"] == {
        "evidence_within_deadline": True,
        "scorecards_completed": True,
        "pressure_available": True,
        "resources_clean": True,
    }
    assert report["max_evidence_latency_seconds"] < 5
    assert report["queue"]["max_active"] == 1
    assert report["queue"]["active_after"] == 0
    assert report["queue"]["pending_after"] == 0
    assert {item["status"] for item in report["queue"]["transitions"]} >= {
        "queued",
        "admitted",
        "completed",
        "released",
    }
    assert report["pressure"]["before"]["docker_containers"] == 2
    assert report["pressure"]["after"]["docker_networks"] == 1
    assert json.loads((tmp_path / "qualification" / "baseline" / "env.json").read_text()) == {
        "disposable": "true",
        "authority": None,
    }
    assert (tmp_path / "qualification" / "qualification.json").is_file()


def test_failed_closed_evidence_fails_qualification(tmp_path):
    evidence = {
        "docker": _python("print('{\"status\": \"failed_closed\"}')"),
        "hermes": _python("print('{\"status\": \"ok\"}')"),
        "homelab": _python("print('{\"status\": \"ok\"}')"),
    }

    report = run_qualification(_config(tmp_path / "qualification", evidence))

    assert report["status"] == "fail"
    assert report["evidence"]["docker"]["ok"] is False
    assert report["evidence"]["docker"]["failure"] == "failed_closed"
    assert report["checks"]["evidence_within_deadline"] is False


def test_qualification_cli_writes_report_for_injected_local_commands(tmp_path):
    evidence = {
        name: _python("print('{\"status\": \"ok\"}')")
        for name in ("docker", "hermes", "homelab")
    }
    output = tmp_path / "cli-qualification"
    command = [
        sys.executable,
        "scripts/qualify-scorecard.py",
        "--output",
        str(output),
        "--baseline-command",
        shlex.join(_scorecard_command()),
        "--candidate-command",
        shlex.join(_scorecard_command()),
        "--pressure-command",
        shlex.join(_pressure_command()),
        "--cleanup-command",
        shlex.join(_cleanup_command()),
    ]
    for name, evidence_command in evidence.items():
        command.extend(["--evidence-command", f"{name}={shlex.join(evidence_command)}"])

    completed = subprocess.run(
        command,
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "qualification pass" in completed.stdout
    assert json.loads((output / "qualification.json").read_text())["status"] == "pass"


def test_evidence_deadline_exceeded_fails_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(qualification_module, "EVIDENCE_DEADLINE_SECONDS", 0.3)
    evidence = {
        "docker": _python("import time; time.sleep(5); print('{\"status\": \"ok\"}')"),
        "hermes": _python("print('{\"status\": \"ok\"}')"),
        "homelab": _python("print('{\"status\": \"ok\"}')"),
    }

    report = run_qualification(_config(tmp_path / "qualification", evidence))

    assert report["status"] == "fail"
    assert report["evidence"]["docker"]["ok"] is False
    assert report["evidence"]["docker"]["failure"] == "deadline_exceeded"
    assert report["evidence"]["docker"]["deadline_seconds"] == 0.3
    assert report["max_evidence_latency_seconds"] >= 0.3
    assert report["checks"]["evidence_within_deadline"] is False


def test_interrupt_runs_cleanup_and_writes_interrupted_report(tmp_path):
    output = tmp_path / "interrupted"
    baseline = _python(
        "import pathlib,sys,time; p=pathlib.Path(sys.argv[1]); p.mkdir(parents=True, exist_ok=True); "
        "(p/'started').write_text('1'); time.sleep(30)",
        "{output}",
    )
    candidate = _python("import time; time.sleep(30)", "{output}")
    cleanup = _python(
        "import json,pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ran'); "
        "print(json.dumps({'clean': True}))",
        str(output / "cleanup-marker"),
    )
    evidence = {
        name: _python("print('{\"status\": \"ok\"}')")
        for name in ("docker", "hermes", "homelab")
    }
    command = [
        sys.executable,
        "scripts/qualify-scorecard.py",
        "--output",
        str(output),
        "--baseline-command",
        shlex.join(baseline),
        "--candidate-command",
        shlex.join(candidate),
        "--cleanup-command",
        shlex.join(cleanup),
    ]
    for name, evidence_command in evidence.items():
        command.extend(["--evidence-command", f"{name}={shlex.join(evidence_command)}"])

    process = subprocess.Popen(
        command,
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 20
        while not (output / "baseline" / "started").exists():
            if process.poll() is not None or time.monotonic() > deadline:
                raise AssertionError("baseline workload never started")
            time.sleep(0.05)
        process.send_signal(signal.SIGINT)
        stdout, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()

    assert "interrupted" in stdout, stderr
    report = json.loads((output / "qualification.json").read_text())
    assert report["status"] == "interrupted"
    assert report["cleanup"]["status"] == "verified"
    assert report["checks"]["resources_clean"] is True
    assert report["queue"]["active_after"] == 0
    assert report["queue"]["pending_after"] == 0
    assert (output / "cleanup-marker").is_file()
