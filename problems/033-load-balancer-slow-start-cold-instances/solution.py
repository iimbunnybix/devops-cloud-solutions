"""Load balancer slow-start: ramp traffic to a newly started instance.

The original load balancer gave every healthy instance an equal share of
traffic the moment it passed its health check. A freshly started instance has
cold caches, an empty connection pool and un-JITed code, so it can serve only
a fraction of its normal load at first. Every scale-out or deploy therefore
overloaded the new instances: p99 latency spiked and requests timed out.

Fix: each instance's weight ramps linearly from a small floor to full over a
warm-up window after it joins. A deterministic smooth weighted round robin
turns the weights into picks, so the new instance's share grows with its
ability to serve.
"""
from __future__ import annotations

from dataclasses import dataclass

DEFAULT_WARMUP_SECONDS = 60.0   # how long a new instance takes to reach full weight
DEFAULT_WEIGHT_FLOOR = 0.1      # starting weight, as a fraction of full weight
FULL_WEIGHT = 1.0


class FakeClock:
    """Manually advanced clock so tests control time exactly."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def advance(self, seconds: float) -> None:
        self.now += seconds


def slow_start_weight(age_seconds: float, warmup_seconds: float, floor: float) -> float:
    """Weight for an instance that joined ``age_seconds`` ago.

    Linear from ``floor`` at age 0 to ``FULL_WEIGHT`` at ``warmup_seconds``.
    The floor is above zero so the instance gets *some* traffic, which is
    what warms its caches in the first place.
    """
    if warmup_seconds <= 0 or age_seconds >= warmup_seconds:
        return FULL_WEIGHT
    progress = max(0.0, age_seconds) / warmup_seconds
    return floor + (FULL_WEIGHT - floor) * progress


@dataclass
class Backend:
    name: str
    joined_at: float
    # Smooth weighted round robin state (see SmoothWeightedBalancer.pick).
    current: float = 0.0


class SmoothWeightedBalancer:
    """Deterministic weighted picker (the "smooth weighted round robin" used by nginx).

    Each pick: add every backend's weight to its ``current`` score, choose the
    highest score, then subtract the total weight from the winner. Over many
    picks each backend is chosen in proportion to its weight, interleaved
    evenly, with no randomness, so tests are exact.
    """

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.backends: dict[str, Backend] = {}

    def add(self, name: str) -> None:
        """Register (or re-register after a restart) a backend that is joining now."""
        self.backends[name] = Backend(name, joined_at=self.clock.now)

    def remove(self, name: str) -> None:
        self.backends.pop(name, None)

    def weight(self, backend: Backend) -> float:
        """Every backend has the same weight in the naive balancer."""
        return FULL_WEIGHT

    def pick(self) -> str:
        """Choose the backend for one request."""
        if not self.backends:
            raise RuntimeError("no backends registered")
        weights = {b.name: self.weight(b) for b in self.backends.values()}
        total = sum(weights.values())
        best: Backend | None = None
        for backend in self.backends.values():
            backend.current += weights[backend.name]
            # WHY: strict ">" keeps ties going to the first-registered backend,
            # which makes the order reproducible.
            if best is None or backend.current > best.current:
                best = backend
        best.current -= total
        return best.name


class NaiveBalancer(SmoothWeightedBalancer):
    """Original behaviour: a new instance gets a full share immediately."""


class SlowStartBalancer(SmoothWeightedBalancer):
    """Same picker, but new backends ramp from ``floor`` to full weight."""

    def __init__(self, clock: FakeClock, warmup_seconds: float = DEFAULT_WARMUP_SECONDS,
                 floor: float = DEFAULT_WEIGHT_FLOOR) -> None:
        if not 0.0 < floor <= FULL_WEIGHT:
            raise ValueError("floor must be in (0, 1]; 0 would starve the instance forever")
        if warmup_seconds < 0:
            raise ValueError("warmup_seconds must be >= 0")
        super().__init__(clock)
        self.warmup_seconds = warmup_seconds
        self.floor = floor

    def weight(self, backend: Backend) -> float:
        age = self.clock.now - backend.joined_at
        return slow_start_weight(age, self.warmup_seconds, self.floor)


def cold_capacity_rps(age_seconds: float, warm_capacity_rps: float,
                      warmup_seconds: float, cold_fraction: float) -> float:
    """Simulated capacity of an instance whose caches warm linearly after start."""
    return warm_capacity_rps * slow_start_weight(age_seconds, warmup_seconds, cold_fraction)


@dataclass
class SecondStats:
    second: int
    new_instance_requests: int
    new_instance_capacity: float
    timeouts: int


def simulate_scale_out(balancer: SmoothWeightedBalancer, clock: FakeClock, *,
                       warm_instances: int, requests_per_second: int, seconds: int,
                       warm_capacity_rps: float, cold_fraction: float,
                       warmup_seconds: float, new_name: str = "new-1") -> list[SecondStats]:
    """Add one cold instance to a warm fleet and send traffic second by second.

    Requests sent to the new instance beyond its current capacity are counted
    as timeouts (the cold instance queues them past the client deadline).
    """
    for i in range(warm_instances):
        balancer.add(f"warm-{i}")
    clock.advance(warmup_seconds)          # existing fleet is fully warm
    balancer.add(new_name)
    joined_at = clock.now
    stats: list[SecondStats] = []
    for second in range(seconds):
        age = clock.now - joined_at
        capacity = cold_capacity_rps(age, warm_capacity_rps, warmup_seconds, cold_fraction)
        sent = 0
        for _ in range(requests_per_second):
            if balancer.pick() == new_name:
                sent += 1
        timeouts = max(0, sent - int(capacity))
        stats.append(SecondStats(second, sent, capacity, timeouts))
        clock.advance(1.0)
    return stats
