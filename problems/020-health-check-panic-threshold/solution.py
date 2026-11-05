"""Stop health checks from removing every backend at once during a shared blip.

The load balancer removed any backend whose health check failed. The health
check endpoint also pinged a shared session cache, so when that cache had a
40-second failover every backend failed its check together, the pool became
empty and the load balancer returned 503 for every request, even though the
backends could still serve most pages. This module adds two guards used by
real proxies such as Envoy: consecutive-result thresholds so one failed check
does not eject a backend, and a panic threshold that ignores health checks
and routes to the whole pool when too few backends look healthy.
"""
from __future__ import annotations

from dataclasses import dataclass

UNHEALTHY_AFTER_FAILURES = 3    # consecutive failed checks before ejecting
HEALTHY_AFTER_SUCCESSES = 2     # consecutive passed checks before re-adding
# Envoy's default healthy_panic_threshold: below 50% healthy, the health data
# is more likely wrong than half the fleet being truly dead.
PANIC_THRESHOLD = 0.5


@dataclass
class BackendHealth:
    """Health-check history of one backend."""
    name: str
    healthy: bool = True
    consecutive_failures: int = 0
    consecutive_successes: int = 0

    def record(self, check_passed: bool) -> None:
        """Update the state; flip only after a run of identical results."""
        if check_passed:
            self.consecutive_successes += 1
            self.consecutive_failures = 0
            if self.consecutive_successes >= HEALTHY_AFTER_SUCCESSES:
                self.healthy = True
        else:
            self.consecutive_failures += 1
            self.consecutive_successes = 0
            if self.consecutive_failures >= UNHEALTHY_AFTER_FAILURES:
                self.healthy = False


def naive_routable_backends(backends: list[BackendHealth]) -> list[str]:
    """The original rule: only backends whose last check passed (the bug).

    With no consecutive-failure threshold and no panic mode, a check failure
    shared by all backends leaves this list empty.
    """
    routable = []
    for backend in backends:
        if backend.consecutive_failures == 0:
            routable.append(backend.name)
    return routable


class LoadBalancer:
    """Round-robin load balancer with ejection thresholds and a panic mode."""

    def __init__(self, backend_names: list[str], panic_threshold: float = PANIC_THRESHOLD) -> None:
        if not backend_names:
            raise ValueError("a load balancer needs at least one backend")
        self.backends = {name: BackendHealth(name) for name in backend_names}
        self.panic_threshold = panic_threshold
        self._next_index = 0

    def record_check(self, backend_name: str, check_passed: bool) -> None:
        """Feed one health-check result for one backend."""
        self.backends[backend_name].record(check_passed)

    def healthy_names(self) -> list[str]:
        """Backends currently considered healthy."""
        return [backend.name for backend in self.backends.values() if backend.healthy]

    def in_panic_mode(self) -> bool:
        """True when the healthy share has dropped below the panic threshold."""
        healthy_share = len(self.healthy_names()) / len(self.backends)
        return healthy_share < self.panic_threshold

    def routable_backends(self) -> list[str]:
        """Backends that may receive traffic right now.

        In panic mode we route to every backend: some requests may fail, but
        that beats a guaranteed 503 for all of them, and it avoids piling the
        whole site's traffic onto the few backends that still pass.
        """
        if self.in_panic_mode():
            return list(self.backends)
        return self.healthy_names()

    def pick_backend(self) -> str:
        """Choose the next backend in round-robin order among routable ones."""
        candidates = self.routable_backends()
        chosen = candidates[self._next_index % len(candidates)]
        self._next_index += 1
        return chosen
