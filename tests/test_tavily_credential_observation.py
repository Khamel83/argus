from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from argus.config import load_config
from argus.broker.credential_observation import (
    observe_tavily_usage_credential,
    parse_tavily_usage_metadata,
)

FULL_SOURCE = "a" * 40
CANARY_SECRET = "fake-canary-tavily-key-do-not-persist"


def _enabled_config(tmp_path: Path):
    os.environ["ARGUS_DATA_ROOT"] = str(tmp_path)
    return load_config(
        environ={
            "ARGUS_AUTOLOAD_DOTENV": "false",
            "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
            "ARGUS_DATA_ROOT": str(tmp_path),
            "ARGUS_TAVILY_USAGE_OBSERVER_ENABLED": "true",
            "ARGUS_TAVILY_ENABLED": "true",
            "ARGUS_TAVILY_API_KEY": CANARY_SECRET,
        }
    )


def _receipt(root: Path) -> dict:
    payload = (root / "credential-health" / "tavily-usage.json").read_text(
        encoding="utf-8"
    )
    assert len(payload.encode("utf-8")) <= 4096
    assert CANARY_SECRET not in payload
    return json.loads(payload)


def _receipt_hash(receipt: dict) -> str:
    without_hash = dict(receipt)
    del without_hash["payload_sha256"]
    encoded = json.dumps(
        without_hash,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _publish_success(tmp_path: Path) -> None:
    cfg = _enabled_config(tmp_path)
    await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
            )
        ),
    )


@pytest.mark.asyncio
async def test_observer_disabled_is_complete_zero_io(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_DATA_ROOT", str(tmp_path))
    cfg = load_config(
        environ={
            "ARGUS_AUTOLOAD_DOTENV": "false",
            "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
            "ARGUS_DATA_ROOT": str(tmp_path),
            "ARGUS_TAVILY_USAGE_OBSERVER_ENABLED": "false",
            "ARGUS_TAVILY_ENABLED": "true",
            "ARGUS_TAVILY_API_KEY": CANARY_SECRET,
        }
    )

    def fail_resolve():
        raise AssertionError("disabled observer must not touch the filesystem")

    monkeypatch.setattr("argus.corpus.paths.resolve_data_root", fail_resolve)

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
    )

    assert result.status == "disabled"
    assert result.attempts == 0
    assert not (tmp_path / "credential-health").exists()


@pytest.mark.asyncio
async def test_success_publishes_sanitized_exact_latest_receipt(tmp_path):
    cfg = _enabled_config(tmp_path)
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "key": {"usage": 4, "limit": 1000},
                "account": {"name": "do-not-retain", "plan": "private"},
            },
        )

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(handler),
    )

    assert result.status == "published"
    assert result.attempts == 1
    assert [request.method for request in requests] == ["GET"]
    assert str(requests[0].url) == "https://api.tavily.com/usage"
    assert requests[0].headers["authorization"] == f"Bearer {CANARY_SECRET}"

    receipt = _receipt(tmp_path)
    assert list(receipt) == [
        "schema",
        "owner",
        "provider",
        "check_id",
        "run_id",
        "observed_at",
        "finished_at",
        "source_sha",
        "definition_sha256",
        "credential_reference",
        "credential_version",
        "attempts",
        "status",
        "reason",
        "fields",
        "payload_sha256",
    ]
    assert receipt["schema"] == "argus.credential-observation/v1"
    assert receipt["owner"] == "argus"
    assert receipt["provider"] == "tavily"
    assert receipt["check_id"] == "tavily.usage-metadata"
    assert receipt["source_sha"] == FULL_SOURCE
    assert receipt["credential_reference"] == "ARGUS_TAVILY_API_KEY"
    assert receipt["credential_version"] == "unknown"
    assert receipt["attempts"] == 1
    assert receipt["status"] == "unknown"
    assert receipt["reason"] == "partial_coverage"
    assert receipt["fields"] == {
        "presence": {"status": "verified", "reason": "injected_configuration"},
        "decryptability": {"status": "unknown", "reason": "not_established"},
        "authentication": {"status": "verified", "reason": "metadata_verified"},
        "identity": {"status": "unknown", "reason": "not_established"},
        "scope": {"status": "unknown", "reason": "not_established"},
        "capability": {"status": "unknown", "reason": "not_established"},
        "budget": {"status": "verified", "reason": "metadata_verified"},
        "availability": {"status": "unknown", "reason": "not_established"},
    }
    assert "do-not-retain" not in json.dumps(receipt)
    assert "1000" not in json.dumps(receipt)
    assert (tmp_path / "credential-health").stat().st_mode & 0o777 == 0o755
    leaf = tmp_path / "credential-health" / "tavily-usage.json"
    assert leaf.stat().st_mode & 0o777 == 0o644
    assert leaf.stat().st_nlink == 1


@pytest.mark.asyncio
async def test_disabled_provider_opt_in_publishes_unknown_without_http(tmp_path):
    os.environ["ARGUS_DATA_ROOT"] = str(tmp_path)
    cfg = load_config(
        environ={
            "ARGUS_AUTOLOAD_DOTENV": "false",
            "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
            "ARGUS_DATA_ROOT": str(tmp_path),
            "ARGUS_TAVILY_USAGE_OBSERVER_ENABLED": "true",
            "ARGUS_TAVILY_ENABLED": "false",
            "ARGUS_TAVILY_API_KEY": CANARY_SECRET,
        }
    )

    def fail_http(request: httpx.Request) -> httpx.Response:
        raise AssertionError("disabled Tavily provider must not be probed")

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(fail_http),
    )

    assert result.status == "published"
    assert result.attempts == 0
    receipt = _receipt(tmp_path)
    assert receipt["reason"] == "provider_disabled"
    assert receipt["fields"]["authentication"] == {
        "status": "unknown",
        "reason": "not_probed",
    }


@pytest.mark.asyncio
async def test_missing_key_opt_in_publishes_not_configured_without_http(tmp_path):
    os.environ["ARGUS_DATA_ROOT"] = str(tmp_path)
    cfg = load_config(
        environ={
            "ARGUS_AUTOLOAD_DOTENV": "false",
            "ARGUS_DISABLE_SECRET_RESOLUTION": "true",
            "ARGUS_DATA_ROOT": str(tmp_path),
            "ARGUS_TAVILY_USAGE_OBSERVER_ENABLED": "true",
            "ARGUS_TAVILY_ENABLED": "true",
            "ARGUS_TAVILY_API_KEY": "",
        }
    )

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )

    assert result.attempts == 0
    receipt = _receipt(tmp_path)
    assert receipt["reason"] == "optional_not_configured"
    assert receipt["fields"]["presence"] == {
        "status": "not_configured",
        "reason": "optional_not_configured",
    }


@pytest.mark.asyncio
async def test_missing_source_publishes_source_unavailable_without_http(tmp_path):
    cfg = _enabled_config(tmp_path)

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": "unknown"},
        http_transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    )

    assert result.attempts == 0
    receipt = _receipt(tmp_path)
    assert receipt["source_sha"] is None
    assert receipt["reason"] == "source_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "reason", "auth_field"),
    [
        (301, "redirect_refused", {"status": "unknown", "reason": "redirect_refused"}),
        (401, "unauthorized", {"status": "failed", "reason": "unauthorized"}),
        (403, "forbidden", {"status": "unknown", "reason": "forbidden"}),
        (429, "rate_limited", {"status": "unknown", "reason": "rate_limited"}),
        (500, "http_error", {"status": "unknown", "reason": "http_error"}),
    ],
)
async def test_http_failure_statuses_are_allowlisted(
    tmp_path, status_code, reason, auth_field
):
    cfg = _enabled_config(tmp_path)

    await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(status_code, json={"private": "body"})
        ),
    )

    receipt = _receipt(tmp_path)
    assert receipt["reason"] == reason
    assert receipt["fields"]["authentication"] == auth_field
    assert "private" not in json.dumps(receipt)


@pytest.mark.asyncio
async def test_response_oversized_reads_max_plus_one_and_publishes_failure(tmp_path):
    cfg = _enabled_config(tmp_path)
    oversized = b'{"key":{"usage":1,"limit":2},"account":{}}' + (b" " * 65537)

    await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=oversized)
        ),
    )

    receipt = _receipt(tmp_path)
    assert receipt["reason"] == "response_oversized"


@pytest.mark.parametrize(
    "body",
    [
        '{"key":{"usage":1,"usage":2,"limit":3},"account":{}}',
        '{"key":{"usage":NaN,"limit":3},"account":{}}',
        '{"key":{"usage":true,"limit":3},"account":{}}',
        '{"key":{"usage":1,"limit":9223372036854775808},"account":{}}',
        '{"key":{"usage":1,"limit":3}}',
    ],
)
def test_strict_parser_rejects_malformed_metadata(body):
    with pytest.raises(ValueError):
        parse_tavily_usage_metadata(body.encode("utf-8"))


def test_strict_parser_rejects_huge_int_and_deep_json_as_invalid():
    huge = (
        '{"key":{"usage":'
        + ("1" * 4000)
        + ',"limit":3},"account":{}}'
    )
    with pytest.raises(ValueError):
        parse_tavily_usage_metadata(huge.encode("utf-8"))

    deep = '{"key":{"usage":1,"limit":2},"account":' + ("[" * 1000)
    deep += "0" + ("]" * 1000) + "}"
    with pytest.raises(ValueError):
        parse_tavily_usage_metadata(deep.encode("utf-8"))


def test_strict_parser_rejects_depth_and_key_count_limits():
    deep = b'{"key":{"usage":1,"limit":2},"account":{"a":{"b":{"c":{"d":{"e":{"f":{"g":{"h":{"i":1}}}}}}}}}}'
    with pytest.raises(ValueError):
        parse_tavily_usage_metadata(deep)

    too_many = {"key": {"usage": 1, "limit": 2}, "account": {}}
    too_many.update({f"k{i}": i for i in range(257)})
    with pytest.raises(ValueError):
        parse_tavily_usage_metadata(json.dumps(too_many).encode("utf-8"))


@pytest.mark.asyncio
async def test_rate_limit_uses_current_latest_without_refreshing_timestamp(tmp_path):
    cfg = _enabled_config(tmp_path)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={"key": {"usage": 1, "limit": 2}, "account": {}},
        )

    await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(handler),
    )
    before = (tmp_path / "credential-health" / "tavily-usage.json").read_text(
        encoding="utf-8"
    )
    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(handler),
    )
    after = (tmp_path / "credential-health" / "tavily-usage.json").read_text(
        encoding="utf-8"
    )

    assert result.status == "rate_limited"
    assert calls == 1
    assert after == before


@pytest.mark.asyncio
async def test_malformed_latest_does_not_mask_new_run(tmp_path):
    cfg = _enabled_config(tmp_path)
    receipt_dir = tmp_path / "credential-health"
    receipt_dir.mkdir(mode=0o755)
    (receipt_dir / "tavily-usage.json").write_text("{not-json", encoding="utf-8")

    await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
            )
        ),
    )

    assert _receipt(tmp_path)["reason"] == "partial_coverage"


@pytest.mark.asyncio
@pytest.mark.parametrize("attempts", [0.0, 1.0])
async def test_float_attempts_latest_receipt_never_suppresses_probe(
    tmp_path, attempts
):
    await _publish_success(tmp_path)
    latest = tmp_path / "credential-health" / "tavily-usage.json"
    receipt = json.loads(latest.read_text(encoding="utf-8"))
    receipt["attempts"] = attempts
    receipt["payload_sha256"] = _receipt_hash(receipt)
    latest.write_text(json.dumps(receipt), encoding="utf-8")

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
        )

    result = await observe_tavily_usage_credential(
        _enabled_config(tmp_path),
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(handler),
    )

    assert result.status == "published"
    assert calls == 1
    assert _receipt(tmp_path)["attempts"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("leaf_kind", ["fifo", "hardlink", "group_writable"])
async def test_unsafe_existing_leaf_refuses_before_http(tmp_path, leaf_kind):
    cfg = _enabled_config(tmp_path)
    receipt_dir = tmp_path / "credential-health"
    receipt_dir.mkdir(mode=0o755)
    latest = receipt_dir / "tavily-usage.json"
    if leaf_kind == "fifo":
        os.mkfifo(latest)
    elif leaf_kind == "hardlink":
        source = tmp_path / "linked.json"
        source.write_text("preserve", encoding="utf-8")
        os.link(source, latest)
    else:
        latest.write_text("preserve", encoding="utf-8")
        os.chmod(latest, 0o664)

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
        )

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(handler),
    )

    assert result.status == "publish_failed"
    assert result.reason == "unsafe_filesystem"
    assert calls == 0


@pytest.mark.asyncio
async def test_unsafe_filesystem_refuses_leaf_without_mutation(tmp_path):
    cfg = _enabled_config(tmp_path)
    receipt_dir = tmp_path / "credential-health"
    receipt_dir.mkdir(mode=0o755)
    target = tmp_path / "outside.json"
    target.write_text("preserve", encoding="utf-8")
    (receipt_dir / "tavily-usage.json").symlink_to(target)

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
            )
        ),
    )

    assert result.status == "publish_failed"
    assert target.read_text(encoding="utf-8") == "preserve"
    assert (receipt_dir / "tavily-usage.json").is_symlink()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutate",
    [
        lambda receipt: receipt.update({"payload_sha256": "0" * 64}),
        lambda receipt: receipt.update({"run_id": "not-a-uuid4"}),
        lambda receipt: receipt.update(
            {"finished_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat().replace("+00:00", "Z")}
        ),
        lambda receipt: receipt["fields"].update(
            {"extra": {"status": "unknown", "reason": "not_established"}}
        ),
    ],
)
async def test_malformed_latest_receipt_never_suppresses_probe(tmp_path, mutate):
    await _publish_success(tmp_path)
    latest = tmp_path / "credential-health" / "tavily-usage.json"
    receipt = json.loads(latest.read_text(encoding="utf-8"))
    mutate(receipt)
    latest.write_text(json.dumps(receipt), encoding="utf-8")

    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
        )

    result = await observe_tavily_usage_credential(
        _enabled_config(tmp_path),
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(handler),
    )

    assert result.status == "published"
    assert calls == 1
    assert _receipt(tmp_path)["reason"] == "partial_coverage"


@pytest.mark.asyncio
async def test_child_directory_substitution_under_lock_fails_boundedly(
    tmp_path, monkeypatch
):
    cfg = _enabled_config(tmp_path)

    async def fake_fetch(api_key, *, http_transport=None):
        receipt_dir = tmp_path / "credential-health"
        moved = tmp_path / "credential-health.moved"
        receipt_dir.rename(moved)
        receipt_dir.mkdir(mode=0o755)
        return "partial_coverage", type("Metadata", (), {"limit_known": True})()

    monkeypatch.setattr(
        "argus.broker.credential_observation._fetch_usage_metadata",
        fake_fetch,
    )

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
    )

    assert result.status == "publish_failed"
    assert not (tmp_path / "credential-health" / "tavily-usage.json").exists()


@pytest.mark.asyncio
async def test_failure_before_replace_preserves_existing_latest(tmp_path, monkeypatch):
    cfg = _enabled_config(tmp_path)
    receipt_dir = tmp_path / "credential-health"
    receipt_dir.mkdir(mode=0o755)
    latest = receipt_dir / "tavily-usage.json"
    latest.write_text('{"old":true}', encoding="utf-8")
    os.chmod(latest, 0o644)

    import argus.broker.credential_observation as observation

    real_replace = observation.os.replace

    def fail_replace(*args, **kwargs):
        raise OSError("replace-failed")

    monkeypatch.setattr(observation.os, "replace", fail_replace)
    try:
        result = await observe_tavily_usage_credential(
            cfg,
            build={"source_revision": FULL_SOURCE},
            http_transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
                )
            ),
        )
    finally:
        monkeypatch.setattr(observation.os, "replace", real_replace)

    assert result.status == "publish_failed"
    assert latest.read_text(encoding="utf-8") == '{"old":true}'


@pytest.mark.asyncio
async def test_zero_progress_write_preserves_existing_latest(tmp_path, monkeypatch):
    cfg = _enabled_config(tmp_path)
    receipt_dir = tmp_path / "credential-health"
    receipt_dir.mkdir(mode=0o755)
    latest = receipt_dir / "tavily-usage.json"
    latest.write_text('{"old":true}', encoding="utf-8")

    import argus.broker.credential_observation as observation

    monkeypatch.setattr(observation.os, "write", lambda fd, data: 0)

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
            )
        ),
    )

    assert result.status == "publish_failed"
    assert latest.read_text(encoding="utf-8") == '{"old":true}'


@pytest.mark.asyncio
async def test_partial_and_eintr_writes_complete_receipt(tmp_path, monkeypatch):
    cfg = _enabled_config(tmp_path)

    import argus.broker.credential_observation as observation

    real_write = observation.os.write
    interrupted = True

    def flaky_write(fd, data):
        nonlocal interrupted
        if interrupted:
            interrupted = False
            raise InterruptedError()
        return real_write(fd, data[: max(1, len(data) // 3)])

    monkeypatch.setattr(observation.os, "write", flaky_write)

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
            )
        ),
    )

    assert result.status == "published"
    assert _receipt(tmp_path)["reason"] == "partial_coverage"


@pytest.mark.asyncio
async def test_postrename_fsync_failure_is_durability_unknown(tmp_path, monkeypatch):
    cfg = _enabled_config(tmp_path)

    import argus.broker.credential_observation as observation

    real_replace = observation.os.replace
    real_fsync = observation.os.fsync
    renamed = False

    def mark_replace(*args, **kwargs):
        nonlocal renamed
        real_replace(*args, **kwargs)
        renamed = True

    def fail_after_rename(fd):
        if renamed:
            raise OSError("directory fsync failed after rename")
        return real_fsync(fd)

    monkeypatch.setattr(observation.os, "replace", mark_replace)
    monkeypatch.setattr(observation.os, "fsync", fail_after_rename)

    result = await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"key": {"usage": 1, "limit": 2}, "account": {}}
            )
        ),
    )

    assert result.status == "publish_failed"
    assert result.reason == "durability_unknown"


@pytest.mark.asyncio
async def test_transport_timeout_is_sanitized_and_request_policy_is_fixed(tmp_path, monkeypatch):
    cfg = _enabled_config(tmp_path)
    captured: dict[str, object] = {}

    class CapturingClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, *, headers):
            assert method == "GET"
            assert url == "https://api.tavily.com/usage"
            assert headers == {
                "Accept-Encoding": "identity",
                "Authorization": f"Bearer {CANARY_SECRET}",
            }

            class FailingStream:
                async def __aenter__(self):
                    raise httpx.ReadTimeout("fake-canary-timeout-leak")

                async def __aexit__(self, exc_type, exc, tb):
                    return False

            return FailingStream()

    monkeypatch.setattr(
        "argus.broker.credential_observation.httpx.AsyncClient",
        CapturingClient,
    )

    await observe_tavily_usage_credential(cfg, build={"source_revision": FULL_SOURCE})

    assert captured["trust_env"] is False
    assert captured["follow_redirects"] is False
    assert captured["verify"] is True
    timeout = captured["timeout"]
    assert timeout.connect == 5.0
    assert timeout.read == 10.0
    assert _receipt(tmp_path)["reason"] == "timeout"
    assert "fake-canary-timeout-leak" not in json.dumps(_receipt(tmp_path))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("headers", "expected_reason"),
    [
        ({"Content-Encoding": "gzip"}, "response_invalid"),
        ({"Content-Length": "not-an-int"}, "response_invalid"),
        ({"Content-Length": "-1"}, "response_invalid"),
        ({"Content-Length": "65537"}, "response_oversized"),
    ],
)
async def test_response_headers_are_bounded_before_decode(
    tmp_path, headers, expected_reason
):
    cfg = _enabled_config(tmp_path)

    await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                headers=headers,
                content=b'{"key":{"usage":1,"limit":2},"account":{}}',
            )
        ),
    )

    assert _receipt(tmp_path)["reason"] == expected_reason


@pytest.mark.asyncio
async def test_null_limit_keeps_budget_unknown(tmp_path):
    cfg = _enabled_config(tmp_path)

    await observe_tavily_usage_credential(
        cfg,
        build={"source_revision": FULL_SOURCE},
        http_transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"key": {"usage": 1, "limit": None}, "account": {}}
            )
        ),
    )

    assert _receipt(tmp_path)["fields"]["budget"] == {
        "status": "unknown",
        "reason": "not_established",
    }
