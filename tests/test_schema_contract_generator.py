import json

import pytest


class _FakeConnection:
    def close(self):
        return None


class _FakeEngine:
    def raw_connection(self):
        return _FakeConnection()

    def dispose(self):
        return None


def test_generator_writes_the_explicit_schema_head_path(monkeypatch, tmp_path):
    import scripts.generate_argus_schema_contract as generator

    target = tmp_path / "argus_schema_0009.json"
    legacy = tmp_path / "argus_schema_0007.json"
    payload = {
        "format_version": 1,
        "schema_head": "0009_retrieval_evidence",
        "tables": [],
        "columns": {},
        "constraints": {},
        "indexes": {},
        "contract_sha256": "a" * 64,
    }
    monkeypatch.setattr(
        generator,
        "SCHEMA_CONTRACT_PATHS",
        {
            "0007_extraction_outcomes": legacy,
            "0009_retrieval_evidence": target,
        },
    )
    monkeypatch.setattr(generator, "create_engine", lambda url: _FakeEngine())
    monkeypatch.setattr(
        generator,
        "build_argus_schema_contract",
        lambda connection: payload,
    )

    output = generator.generate_schema_contract(
        database_url="postgresql://example/argus",
        schema_head="0009_retrieval_evidence",
    )

    assert output == target
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert not legacy.exists()


def test_generator_check_mode_fails_without_rewriting(monkeypatch, tmp_path):
    import scripts.generate_argus_schema_contract as generator

    target = tmp_path / "argus_schema_0009.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    monkeypatch.setattr(
        generator,
        "SCHEMA_CONTRACT_PATHS",
        {"0009_retrieval_evidence": target},
    )
    monkeypatch.setattr(generator, "create_engine", lambda url: _FakeEngine())
    monkeypatch.setattr(
        generator,
        "build_argus_schema_contract",
        lambda connection: {
            "format_version": 1,
            "schema_head": "0009_retrieval_evidence",
        },
    )

    with pytest.raises(RuntimeError, match="out of date"):
        generator.generate_schema_contract(
            database_url="postgresql://example/argus",
            schema_head="0009_retrieval_evidence",
            check=True,
        )

    assert target.read_text(encoding="utf-8") == '{"old": true}\n'
