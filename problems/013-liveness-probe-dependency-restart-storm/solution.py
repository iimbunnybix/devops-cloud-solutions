"""Health probes that do not turn a slow database into a fleet-wide restart.

A liveness probe answers one question: "is this process broken in a way that
only a restart fixes?" If it also checks a shared dependency (database,
cache, another service), then a slow dependency fails the probe on every pod
at once, the kubelet restarts all of them, and the restarts add crash-loop
back-off on top. The dependency recovers; the service does not.

This module simulates a Deployment's pods, their probes and the kubelet's
restart logic second by second, with two probe designs:

* ``naive_liveness``  - ``/healthz`` runs ``SELECT 1`` with a 1 s timeout.
* ``local_liveness``  - ``/livez`` only checks the process itself (event loop
  responsive, worker threads alive). Dependency problems are handled per
  request (timeouts, circuit breakers, degraded responses) and, if needed,
  reported through readiness.

The kubelet model follows Kubernetes defaults: probes every ``period``
seconds, restart after ``failure_threshold`` consecutive failures, and
crash-loop back-off of 10 s doubling to a 300 s cap, reset after a pod has run
for 10 minutes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

BACKOFF_BASE = 10
BACKOFF_CAP = 300
BACKOFF_RESET_AFTER = 600


@dataclass
class World:
    """External conditions at time ``t``."""
    db_latency: Callable[[int], float]                 # seconds per query
    hung_pods: set[int] = field(default_factory=set)   # truly deadlocked


Probe = Callable[[int, int, World, float], bool]       # (pod, t, world, timeout)


def naive_liveness(pod: int, t: int, world: World, timeout: float) -> bool:
    if pod in world.hung_pods:
        return False
    return world.db_latency(t) <= timeout


def local_liveness(pod: int, t: int, world: World, timeout: float) -> bool:
    return pod not in world.hung_pods


@dataclass
class Pod:
    index: int
    state: str = "running"         # running | backoff | starting
    until: int = 0                 # end of current backoff/startup
    failures: int = 0
    restarts: int = 0
    running_since: int = 0


@dataclass
class SimResult:
    available: list[int]                       # running pods per second
    restarts: int
    first_full_after: int | None = None        # first t >= recovered with all pods up

    def seconds_at_zero(self) -> int:
        return sum(1 for a in self.available if a == 0)


def simulate(pods: int, duration: int, world: World, probe: Probe, *,
             period: int = 10, timeout: float = 1.0, failure_threshold: int = 3,
             startup_seconds: int = 20, recovered_at: int | None = None) -> SimResult:
    """Run the kubelet loop for ``duration`` seconds."""
    fleet = [Pod(i) for i in range(pods)]
    available: list[int] = []
    restarts = 0
    first_full = None
    for t in range(duration):
        for p in fleet:
            if p.state == "backoff" and t >= p.until:
                p.state, p.until = "starting", t + startup_seconds
            elif p.state == "starting" and t >= p.until:
                p.state, p.failures, p.running_since = "running", 0, t
            if p.state != "running":
                continue
            if t - p.running_since >= BACKOFF_RESET_AFTER:
                p.restarts = 0
            # Probes are staggered across pods, like real kubelets.
            if (t + p.index) % period:
                continue
            if probe(p.index, t, world, timeout):
                p.failures = 0
                continue
            p.failures += 1
            if p.failures >= failure_threshold:
                # Kubernetes waits 10 s before the first restart, then doubles.
                delay = min(BACKOFF_BASE * 2 ** p.restarts, BACKOFF_CAP)
                p.restarts += 1
                restarts += 1
                p.state, p.until, p.failures = "backoff", t + delay, 0
        up = sum(1 for p in fleet if p.state == "running")
        available.append(up)
        if recovered_at is not None and first_full is None and t >= recovered_at and up == pods:
            first_full = t
    return SimResult(available, restarts, first_full)


def slow_db(start: int, end: int, slow: float = 2.5, normal: float = 0.005):
    """DB latency profile: ``slow`` seconds per query during [start, end)."""
    return lambda t: slow if start <= t < end else normal


def check_probe_config(liveness_checks: list[str], readiness_checks: list[str],
                       shared_dependencies: set[str]) -> list[str]:
    """Lint a service's probe definitions; returns a list of problems."""
    problems = []
    for dep in sorted(set(liveness_checks) & shared_dependencies):
        problems.append(f"liveness checks shared dependency '{dep}': an outage there "
                        f"restarts every pod; move it to per-request handling")
    if liveness_checks and set(liveness_checks) == set(readiness_checks):
        problems.append("liveness and readiness are identical; liveness should be a "
                        "strict subset that only detects unrecoverable local failure")
    return problems
