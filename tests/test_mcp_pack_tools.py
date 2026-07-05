"""Tests for machine-readable MCP research-pack output (issue #19)."""

import json
from datetime import datetime

from argus.mcp.tools import _serialize_workflow_json
from argus.workflows.models import (
    StoredDocument,
    WorkflowArtifact,
    WorkflowKind,
    WorkflowResult,
    WorkflowStatus,
)


def _run(tmp_path):
    report = tmp_path / "SUMMARY.md"
    report.write_text("# Summary\n", encoding="utf-8")
    doc = tmp_path / "doc1.md"
    doc.write_text("content", encoding="utf-8")
    return WorkflowResult(
        run_id="run-123",
        kind=WorkflowKind.BUILD_RESEARCH_PACK,
        status=WorkflowStatus.COMPLETED,
        target="fastapi",
        created_at=datetime(2026, 7, 5),
        snapshot_dir=str(tmp_path),
        report_path=str(report),
        manifest_path=str(tmp_path / "manifest.json"),
        artifacts=[WorkflowArtifact(kind="report", path=str(report))],
        documents=[
            StoredDocument(
                id="d1", url="https://x.test", title="Doc 1", artifact_path=str(doc)
            )
        ],
    )


def test_serialize_workflow_json_shape(tmp_path):
    payload = json.loads(_serialize_workflow_json(_run(tmp_path)))
    assert payload["run_id"] == "run-123"
    assert payload["status"] == "completed"
    assert payload["report_path"].endswith("SUMMARY.md")
    paths = {f["path"] for f in payload["files"]}
    assert str(tmp_path / "SUMMARY.md") in paths
    assert str(tmp_path / "doc1.md") in paths
    for f in payload["files"]:
        assert isinstance(f["bytes"], int)


def test_serialize_workflow_json_error_run(tmp_path):
    run = _run(tmp_path)
    run.error = "boom"
    run.status = WorkflowStatus.FAILED
    payload = json.loads(_serialize_workflow_json(run))
    assert payload["error"] == "boom"
    assert payload["status"] == "failed"


import base64

from argus.mcp.tools import read_pack_file


def test_read_pack_file_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "argus.corpus.paths.resolve_data_root", lambda: tmp_path
    )
    f = tmp_path / "SUMMARY.md"
    f.write_text("hello pack", encoding="utf-8")
    payload = json.loads(read_pack_file(str(f)))
    assert payload["encoding"] == "utf-8"
    assert payload["content"] == "hello pack"
    assert payload["truncated"] is False


def test_read_pack_file_binary_falls_back_to_base64(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path)
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\xff\xfe\x00\x01")
    payload = json.loads(read_pack_file(str(f)))
    assert payload["encoding"] == "base64"
    assert base64.b64decode(payload["content"]) == b"\xff\xfe\x00\x01"


def test_read_pack_file_rejects_path_outside_data_root(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path / "root")
    (tmp_path / "root").mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")
    payload = json.loads(read_pack_file(str(outside)))
    assert "error" in payload and "content" not in payload


def test_read_pack_file_rejects_traversal(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: root)
    (tmp_path / "escape.txt").write_text("nope", encoding="utf-8")
    payload = json.loads(read_pack_file(str(root / ".." / "escape.txt")))
    assert "error" in payload


def test_read_pack_file_truncation_and_offset(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path)
    f = tmp_path / "big.md"
    f.write_text("abcdefghij", encoding="utf-8")
    first = json.loads(read_pack_file(str(f), max_bytes=4))
    assert first["content"] == "abcd"
    assert first["truncated"] is True
    rest = json.loads(read_pack_file(str(f), max_bytes=100, offset=4))
    assert rest["content"] == "efghij"
    assert rest["truncated"] is False


def test_read_pack_file_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", lambda: tmp_path)
    payload = json.loads(read_pack_file(str(tmp_path / "nope.md")))
    assert "error" in payload

