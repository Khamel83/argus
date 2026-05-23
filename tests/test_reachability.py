import time
import pytest
from argus.broker.reachability import EgressProbe, ReachabilityMatrix
from argus.models import ProviderName


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