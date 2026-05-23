"""Provider reachability matrix — tracks which egress can reach which provider."""

import time
from dataclasses import dataclass, field
from typing import Optional

from argus.models import ProviderName


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