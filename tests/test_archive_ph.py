"""Tests for the optional archive.ph recovery fallback."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from argus.acquisition.errors import AcquisitionFailure, AcquisitionFailureCode
from argus.acquisition.guarded import GuardedAcquisitionError
from argus.acquisition.models import CredentialPolicy, OperationClass, OriginProfile


@pytest.mark.asyncio
async def test_archive_ph_uses_guarded_third_party_request():
    from argus.recovery.archive_ph import try_archive_ph

    response = SimpleNamespace(
        status_code=200,
        url="https://archive.ph/abc123/example",
        text="<article>archived</article>",
    )
    normalized = {"text": " ".join(["archived"] * 60), "title": "Archived title"}

    with (
        patch(
            "argus.recovery.archive_ph.guarded_http_request",
            new=AsyncMock(return_value=response),
        ) as guarded,
        patch("trafilatura.bare_extraction", return_value=normalized),
    ):
        result = await try_archive_ph("https://example.com/article")

    assert result is not None
    assert result["url"] == response.url
    assert result["domain"] == "archive.ph"
    kwargs = guarded.await_args.kwargs
    assert kwargs["profile"] is OriginProfile.THIRD_PARTY_FETCH
    assert kwargs["credential_policy"] is CredentialPolicy.NONE
    assert kwargs["operation_class"] is OperationClass.THIRD_PARTY
    assert kwargs["caller_principal"] == "recovery:archive-ph"
    assert kwargs["target_url"] == "https://example.com/article"


@pytest.mark.asyncio
async def test_archive_ph_guard_failure_is_an_absent_optional_fallback():
    from argus.recovery.archive_ph import try_archive_ph

    failure = AcquisitionFailure(
        code=AcquisitionFailureCode.POLICY_REJECTED,
        safe_reason="archive target was rejected by policy",
        retryable=False,
        request_id="archive-ph-recovery",
    )
    with patch(
        "argus.recovery.archive_ph.guarded_http_request",
        new=AsyncMock(side_effect=GuardedAcquisitionError(failure)),
    ):
        result = await try_archive_ph("http://127.0.0.1/private")

    assert result is None


@pytest.mark.asyncio
async def test_archive_ph_does_not_construct_an_http_client_directly():
    from argus.recovery.archive_ph import try_archive_ph

    compat_client = MagicMock(name="compat-client")
    with (
        patch(
            "argus.recovery.archive_ph.guarded_http_request",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    status_code=404,
                    url="https://archive.ph/newest/x",
                    text="",
                )
            ),
        ) as guarded,
        patch(
            "argus.recovery.archive_ph.patched_httpx_client",
            return_value=compat_client,
        ) as compat,
    ):
        assert await try_archive_ph("https://example.com/article") is None

    compat.assert_called_once_with()
    assert guarded.await_args.kwargs["compat_client_factory"] is compat_client
