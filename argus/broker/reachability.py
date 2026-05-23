"""Provider reachability matrix — tracks which egress can reach which provider."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional
import time

from argus.models import ProviderName

if TYPE_CHECKING:
    from argus.providers.base import BaseProvider
    from argus.config import EgressNode


@dataclass
class EgressProbe:
    egress: str        # "local" | "oci-dev" | "macmini"
    reachable: bool
    latency_ms: int
    last_checked: float = field(default_factory=time.time)


class ReachabilityMatrix:
    """In-memory store of (provider, egress) reachability probes.

    Preference order: local always beats workers. Among workers, lower
    latency wins. If a provider has never been probed, local is assumed
    reachable (optimistic default).
    """

    def __init__(self) -> None:
        # provider → {egress_name → EgressProbe}
        self._probes: dict[ProviderName, dict[str, EgressProbe]] = {}

    def update_probe(
        self,
        egress: str,
        provider: ProviderName,
        reachable: bool,
        latency_ms: int,
    ) -> None:
        if provider not in self._probes:
            self._probes[provider] = {}
        self._probes[provider][egress] = EgressProbe(
            egress=egress,
            reachable=reachable,
            latency_ms=latency_ms,
        )

    def best_egress(self, provider: ProviderName) -> Optional[str]:
        """Return the name of the best reachable egress, or None if all blocked.

        'local' is always preferred when reachable. Among workers, lower
        latency wins. If no probe exists for this provider, returns 'local'.
        """
        probes = self._probes.get(provider)
        if not probes:
            return "local"

        # Local first
        local = probes.get("local")
        if local is None or local.reachable:
            return "local"

        # Workers sorted by latency ascending
        workers = sorted(
            (p for name, p in probes.items() if name != "local" and p.reachable),
            key=lambda p: p.latency_ms,
        )
        return workers[0].egress if workers else None

    def get_all(self) -> dict[ProviderName, dict]:
        """Return a summary dict for health/admin endpoints."""
        result = {}
        for provider, probes in self._probes.items():
            best = self.best_egress(provider)
            result[provider] = {
                "best": best,
                "probes": {
                    name: {
                        "reachable": p.reachable,
                        "latency_ms": p.latency_ms,
                        "last_checked": p.last_checked,
                    }
                    for name, p in probes.items()
                },
            }
        return result

    async def probe_all(
        self,
        local_providers: "dict[ProviderName, BaseProvider]",
        egress_nodes: "list[EgressNode]",
    ) -> None:
        """Probe all tier-0 providers locally and through each egress node."""
        from argus.broker.budgets import PROVIDER_TIERS
        from argus.models import SearchMode, SearchQuery
        from argus.broker.remote_provider import RemoteProviderClient

        tier_0 = [p for p, t in PROVIDER_TIERS.items() if t == 0]
        probe_query = SearchQuery(
            query="argus probe", mode=SearchMode.DISCOVERY, max_results=1
        )

        for pname in tier_0:
            # Probe local
            provider = local_providers.get(pname)
            if provider is not None and provider.is_available():
                try:
                    _, trace = await provider.search(probe_query)
                    reachable = trace.status == "success"
                    self.update_probe("local", pname, reachable=reachable,
                                      latency_ms=trace.latency_ms)
                except Exception:
                    self.update_probe("local", pname, reachable=False, latency_ms=0)

            # Probe each remote node
            for node in egress_nodes:
                try:
                    remote = RemoteProviderClient(pname, node)
                    _, trace = await remote.search(probe_query)
                    reachable = trace.status == "success"
                    self.update_probe(node.name, pname, reachable=reachable,
                                      latency_ms=trace.latency_ms)
                except Exception:
                    self.update_probe(node.name, pname, reachable=False, latency_ms=0)