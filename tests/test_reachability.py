import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from argus.broker.reachability import ReachabilityMatrix
from argus.models import ProviderName, ProviderTrace, SearchResult


def test_best_egress_returns_local_when_reachable():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=True, latency_ms=50)
    assert m.best_egress(ProviderName.YAHOO) == "local"


def test_best_egress_returns_worker_when_local_blocked():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=120)
    assert m.best_egress(ProviderName.YAHOO) == "oci-dev"


def test_best_egress_returns_none_when_all_blocked():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=False, latency_ms=0)
    assert m.best_egress(ProviderName.YAHOO) is None


def test_best_egress_prefers_local_over_faster_worker():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.DUCKDUCKGO, reachable=True, latency_ms=200)
    m.update_probe("oci-dev", ProviderName.DUCKDUCKGO, reachable=True, latency_ms=10)
    # Local is always preferred when reachable, regardless of worker latency
    assert m.best_egress(ProviderName.DUCKDUCKGO) == "local"


def test_best_egress_default_local_when_never_probed():
    m = ReachabilityMatrix()
    # No probes recorded — assume local is reachable
    assert m.best_egress(ProviderName.SEARXNG) == "local"


def test_best_egress_picks_lower_latency_among_workers():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=200)
    m.update_probe("macmini", ProviderName.YAHOO, reachable=True, latency_ms=80)
    assert m.best_egress(ProviderName.YAHOO) == "macmini"


def test_get_all_returns_per_provider_summary():
    m = ReachabilityMatrix()
    m.update_probe("local", ProviderName.YAHOO, reachable=False, latency_ms=0)
    m.update_probe("oci-dev", ProviderName.YAHOO, reachable=True, latency_ms=100)
    summary = m.get_all()
    assert ProviderName.YAHOO in summary
    assert summary[ProviderName.YAHOO]["best"] == "oci-dev"


@pytest.mark.asyncio
async def test_probe_all_marks_reachable_on_success():
    matrix = ReachabilityMatrix()
    mock_provider = AsyncMock()
    mock_provider.is_available = MagicMock(return_value=True)
    mock_provider.search = AsyncMock(return_value=(
        [SearchResult(url="u", title="t", snippet="s", provider=ProviderName.YAHOO)],
        ProviderTrace(provider=ProviderName.YAHOO, status="success",
                      results_count=1, latency_ms=50),
    ))

    await matrix.probe_all(
        local_providers={ProviderName.YAHOO: mock_provider},
        egress_nodes=[],
    )

    assert matrix.best_egress(ProviderName.YAHOO) == "local"
    summary = matrix.get_all()
    assert summary[ProviderName.YAHOO]["probes"]["local"]["reachable"] is True
    assert mock_provider.search.await_args.args[0].user_visible is False


@pytest.mark.asyncio
async def test_probe_all_marks_blocked_on_error():
    matrix = ReachabilityMatrix()
    mock_provider = AsyncMock()
    mock_provider.is_available = MagicMock(return_value=True)
    mock_provider.search = AsyncMock(return_value=(
        [],
        ProviderTrace(provider=ProviderName.YAHOO, status="error",
                      error="500 INKApi Error", latency_ms=170),
    ))

    await matrix.probe_all(
        local_providers={ProviderName.YAHOO: mock_provider},
        egress_nodes=[],
    )

    assert matrix.best_egress(ProviderName.YAHOO) is None


@pytest.mark.asyncio
async def test_probe_all_probes_remote_nodes():
    from argus.config import EgressNode

    matrix = ReachabilityMatrix()
    node = EgressNode(name="oci-dev", url="http://worker:8273", shared_secret="s")

    # Local Yahoo blocked
    local_yahoo = AsyncMock()
    local_yahoo.is_available = MagicMock(return_value=True)
    local_yahoo.search = AsyncMock(return_value=(
        [],
        ProviderTrace(provider=ProviderName.YAHOO, status="error", latency_ms=100),
    ))

    # Remote Yahoo succeeds
    from argus.broker import remote_provider as rp_module

    class FakeRemote:
        def __init__(self, provider, n): pass
        async def search(self, q):
            return (
                [SearchResult(url="u", title="t", snippet="s", provider=ProviderName.YAHOO)],
                ProviderTrace(provider=ProviderName.YAHOO, status="success",
                              results_count=1, latency_ms=80, egress="oci-dev"),
            )

    original = rp_module.RemoteProviderClient
    rp_module.RemoteProviderClient = FakeRemote
    try:
        await matrix.probe_all(
            local_providers={ProviderName.YAHOO: local_yahoo},
            egress_nodes=[node],
        )
    finally:
        rp_module.RemoteProviderClient = original

    assert matrix.best_egress(ProviderName.YAHOO) == "oci-dev"


@pytest.mark.asyncio
async def test_probe_all_records_every_free_local_and_remote_attempt_once():
    from argus.config import EgressNode

    spend = MagicMock()
    matrix = ReachabilityMatrix(spend_repository=spend)
    local = MagicMock()
    local.is_available.return_value = True
    local.search = AsyncMock(return_value=(
        [],
        ProviderTrace(
            provider=ProviderName.WOLFRAM,
            status="error",
            latency_ms=10,
        ),
    ))
    node = EgressNode(name="homelab", url="http://worker:8273", shared_secret="s")

    with patch("argus.broker.remote_provider.RemoteProviderClient") as remote_type:
        remote = remote_type.return_value
        remote.search = AsyncMock(
            return_value=(
                [],
                ProviderTrace(
                    provider=ProviderName.WOLFRAM,
                    status="success",
                    latency_ms=20,
                ),
            )
        )
        await matrix.probe_all(
            local_providers={ProviderName.WOLFRAM: local},
            egress_nodes=[node],
        )

    assert spend.record_free_attempt.call_count == 6
    calls = spend.record_free_attempt.call_args_list
    assert {call.kwargs["caller_label"] for call in calls} == {
        "reachability:local",
        "reachability:homelab",
    }
    assert {call.kwargs["outcome"] for call in calls} == {"error", "success"}
    wolfram_calls = [
        call for call in calls if call.kwargs["provider"] == ProviderName.WOLFRAM
    ]
    assert len(wolfram_calls) == 2
    assert all(call.kwargs["usage"] == 1.0 for call in calls)
    assert len({call.kwargs["idempotency_key"] for call in calls}) == 6


@pytest.mark.asyncio
async def test_probe_accounting_failure_is_not_reclassified_or_double_recorded():
    spend = MagicMock()
    spend.record_free_attempt.side_effect = RuntimeError("ledger unavailable")
    matrix = ReachabilityMatrix(spend_repository=spend)
    local = MagicMock()
    local.is_available.return_value = True
    local.search = AsyncMock(
        return_value=(
            [],
            ProviderTrace(
                provider=ProviderName.WOLFRAM,
                status="success",
                latency_ms=10,
            ),
        )
    )

    with pytest.raises(RuntimeError, match="ledger unavailable"):
        await matrix.probe_all(
            local_providers={ProviderName.WOLFRAM: local},
            egress_nodes=[],
        )

    spend.record_free_attempt.assert_called_once()
    assert spend.record_free_attempt.call_args.kwargs["outcome"] == "success"
