"""Roll pods without dropping requests: pre-stop delay plus connection draining.

When Kubernetes (or any orchestrator) stops a pod it sends SIGTERM and, *at
the same time*, starts removing the pod from load-balancer endpoints. That
removal takes a few seconds to reach every kube-proxy / ingress / cloud LB, so
for a while new requests keep arriving at a pod that is shutting down.

An app that exits on SIGTERM therefore fails two kinds of requests:

* **in-flight** requests are cut off ("connection reset", 502);
* **new** requests routed during endpoint propagation are refused (502/503).

The fix is a shutdown sequence:

1. on SIGTERM, report not-ready but **keep serving** for ``pre_stop_delay``
   (longer than endpoint propagation);
2. then stop accepting new connections;
3. **drain**: wait for in-flight requests, up to ``drain_timeout``;
4. exit before the orchestrator's ``termination_grace`` SIGKILL.

``simulate_rolling_restart`` models one pod being replaced behind a
round-robin load balancer, so the policies can be compared deterministically.
``DrainController`` is the piece you embed in a real server, and
``check_timings`` validates a deployment's settings.
"""
from __future__ import annotations

import random
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Callable, Iterator, List, Optional


@dataclass(frozen=True)
class ShutdownPolicy:
    pre_stop_delay: float = 0.0     # seconds to keep serving after SIGTERM
    drain_timeout: float = 0.0      # max seconds to wait for in-flight requests
    termination_grace: float = 30.0  # orchestrator SIGKILLs after this

    @staticmethod
    def exit_immediately() -> "ShutdownPolicy":
        return ShutdownPolicy(0.0, 0.0)


def default_latency(rng: random.Random) -> float:
    """95% API calls of 50-500 ms, 5% report downloads of 2-8 s."""
    if rng.random() < 0.95:
        return rng.uniform(0.05, 0.5)
    return rng.uniform(2.0, 8.0)


@dataclass
class SimResult:
    total: int
    refused: int          # routed to the pod after it stopped accepting
    reset: int            # accepted, then killed mid-request
    exit_after: float     # seconds from SIGTERM until the process exited

    @property
    def failed(self) -> int:
        return self.refused + self.reset


def simulate_rolling_restart(policy: ShutdownPolicy, *, pods: int = 3, rps: float = 200,
                             duration: float = 60, sigterm_at: float = 30,
                             propagation_delay: float = 3.0,
                             latency: Optional[Callable[[random.Random], float]] = None,
                             seed: int = 0) -> SimResult:
    """Pod 0 receives SIGTERM at ``sigterm_at``; the LB stops routing to it
    ``propagation_delay`` seconds later. Requests arrive at a fixed rate and are
    spread round-robin over the endpoints the LB currently knows."""
    rng = random.Random(seed)
    latency = latency or default_latency
    stop_accepting = sigterm_at + policy.pre_stop_delay
    lb_removes = sigterm_at + propagation_delay
    accepted_on_victim: List[float] = []      # completion times
    refused = 0
    total = int(rps * duration)
    rr = 0
    for i in range(total):
        t = i / rps
        d = latency(rng)
        endpoints = pods if t < lb_removes else pods - 1
        pod = rr % endpoints
        rr += 1
        if t >= lb_removes:
            pod += 1                          # pod 0 is gone; others are 1..pods-1
        if pod != 0:
            continue
        if t >= stop_accepting:
            refused += 1
        else:
            accepted_on_victim.append(t + d)

    grace_kill = sigterm_at + policy.termination_grace
    drain_deadline = stop_accepting + policy.drain_timeout
    last_done = max([stop_accepting, *accepted_on_victim])
    exit_at = min(grace_kill, drain_deadline, last_done)
    reset = sum(1 for done in accepted_on_victim if done > exit_at)
    return SimResult(total, refused, reset, exit_at - sigterm_at)


def check_timings(policy: ShutdownPolicy, propagation_delay: float,
                  max_request_seconds: float) -> List[str]:
    """Return human-readable problems with a deployment's shutdown settings."""
    problems = []
    if policy.pre_stop_delay < propagation_delay:
        problems.append(f"pre_stop_delay {policy.pre_stop_delay}s is shorter than endpoint "
                        f"propagation {propagation_delay}s: new requests will be refused")
    if policy.drain_timeout < max_request_seconds:
        problems.append(f"drain_timeout {policy.drain_timeout}s is shorter than the slowest "
                        f"request {max_request_seconds}s: long requests will be cut off")
    if policy.pre_stop_delay + policy.drain_timeout >= policy.termination_grace:
        problems.append("pre_stop_delay + drain_timeout must be less than termination_grace, "
                        "or the pod is SIGKILLed mid-drain")
    return problems


@dataclass
class DrainController:
    """Track in-flight requests and run the shutdown sequence.

    Wire ``begin_request``/``end_request`` into middleware, ``ready`` into the
    readiness probe, and call ``shutdown`` from the SIGTERM handler.
    ``sleep``/``now`` are injectable for tests.
    """
    policy: ShutdownPolicy
    sleep: Callable[[float], None]
    now: Callable[[], float]
    ready: bool = True
    accepting: bool = True
    _in_flight: int = 0
    _cond: threading.Condition = field(default_factory=threading.Condition)
    log: List[str] = field(default_factory=list)

    def begin_request(self) -> bool:
        with self._cond:
            if not self.accepting:
                return False             # caller should return 503 / close
            self._in_flight += 1
            return True

    def end_request(self) -> None:
        """Mark one *accepted* request as finished.

        Raises instead of going below zero: an unmatched call (for example a
        middleware ``finally`` that also runs for refused requests) would
        otherwise make shutdown think draining is done while real requests run.
        """
        with self._cond:
            if self._in_flight <= 0:
                raise RuntimeError("end_request() without a matching accepted begin_request()")
            self._in_flight -= 1
            self._cond.notify_all()

    @contextmanager
    def track(self) -> Iterator[bool]:
        """Pair begin/end safely: yields whether the request was accepted.

        Only an accepted request is counted down at the end, so refused
        requests can never disturb the in-flight count.
        """
        accepted = self.begin_request()
        try:
            yield accepted
        finally:
            if accepted:
                self.end_request()

    @property
    def in_flight(self) -> int:
        with self._cond:
            return self._in_flight

    def shutdown(self, poll: float = 0.1) -> int:
        """Run the sequence; returns the number of requests abandoned."""
        self.ready = False
        self.log.append("not-ready")
        self.sleep(self.policy.pre_stop_delay)
        with self._cond:
            self.accepting = False
        self.log.append("stopped-accepting")
        deadline = self.now() + self.policy.drain_timeout
        while self.in_flight > 0 and self.now() < deadline:
            self.sleep(min(poll, deadline - self.now()))
        left = self.in_flight
        self.log.append(f"exit in_flight={left}")
        return left
